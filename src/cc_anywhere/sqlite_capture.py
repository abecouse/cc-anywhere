#!/usr/bin/env python3
"""
SQLite session capture for cc-anywhere.

Scans ~/.claude/projects/ JSONL conversation files and stores user messages
and assistant text replies in a SQLite database with full-text search.
Skips tool_use/tool_result entries to keep the DB focused on human-readable content.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _utc_iso_now() -> str:
    """Return current UTC time as an ISO-8601 string with `Z` suffix.

    Matches the timestamp shape used by Claude Code and Codex CLI JSONL
    logs (e.g. ``2026-04-28T08:47:20.435Z``), so all timestamps stored in
    the capture DB sort lexicographically and compare correctly regardless
    of which side wrote them.

    Use this anywhere we previously wrote ``datetime.now().isoformat()``,
    which produced naive *local* timestamps — those would silently break
    cross-source ordering and the semantic indexer's "skip if up to date"
    check across timezones / DST changes.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _local_display(iso_utc) -> str:
    """Convert a UTC ISO timestamp string to local time for human display.

    Storage is UTC everywhere (matches Claude Code / Codex JSONL convention).
    This helper renders a stored UTC timestamp like ``2026-04-28T08:47:20.435Z``
    into the user's local timezone with a tz abbreviation, e.g.
    ``2026-04-28 01:47 PDT``. Returns ``"?"`` for missing input and falls
    back to the raw UTC string if parsing fails.

    Use this anywhere a timestamp from the DB is shown to a human.
    """
    if not iso_utc:
        return "?"
    try:
        s = iso_utc.rstrip("Z")
        # fromisoformat (Python 3.8+) handles up to microsecond precision.
        if "." in s:
            head, _, frac = s.partition(".")
            s = f"{head}.{frac[:6]}"
        dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        rendered = local.strftime("%Y-%m-%d %H:%M %Z").rstrip()
        return rendered if rendered else local.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return iso_utc[:16]

from cc_anywhere._paths import CLAUDE_DIR, DB_PATH, migrate_legacy_paths

log = logging.getLogger("cc-anywhere")

CLAUDE_COWORK_DIR = Path.home() / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
CODEX_DIR = Path.home() / ".codex"
GEMINI_DIR = Path.home() / ".gemini"

# Codex injects synthetic user turns prefixed with these tags; skip them
# so search results show real human input, not framework scaffolding.
CODEX_SKIP_USER_PREFIXES = (
    "<environment_context>",
    "<user_instructions>",
    "<permissions instructions>",
)

# Match the UUID portion of a Codex rollout filename:
# rollout-2026-04-27T09-19-54-019dcfbd-7abb-7a30-ad18-137d1cbc7d3d.jsonl
_CODEX_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    project_path TEXT NOT NULL,
    project_name TEXT NOT NULL,
    started_at TEXT,
    last_message_at TEXT,
    machine_name TEXT,
    source TEXT NOT NULL DEFAULT 'claude-code',
    session_label TEXT,
    -- Per-client metadata captured from source-tool session_meta records.
    -- Codex populates these from rollout session_meta.payload:
    --   client_originator: e.g. "codex-tui", "codex_exec", "Codex Desktop"
    --   client_source:     e.g. "cli", "exec", "vscode"
    --   client_version:    e.g. "0.118.0"
    -- sessions.source remains the broad source ("claude-code"|"codex").
    client_originator TEXT,
    client_source TEXT,
    client_version TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    uuid TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT,
    parent_uuid TEXT,
    message_type TEXT,
    is_compact_summary INTEGER NOT NULL DEFAULT 0,
    is_visible_in_transcript_only INTEGER NOT NULL DEFAULT 0,
    source_path TEXT,
    source_line INTEGER,
    source_byte_start INTEGER,
    source_byte_end INTEGER,
    model TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content='messages',
    content_rowid='rowid'
);

CREATE TABLE IF NOT EXISTS capture_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE OF content ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role);
CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_name);
"""


def _migrate_schema(db: sqlite3.Connection) -> None:
    """Add columns introduced after initial schema. Safe to run on every open.

    Each branch is gated by a column-presence check via PRAGMA table_info,
    so the migration is idempotent and additive only — no data is rewritten,
    nothing is dropped or renamed, and ALTER TABLE ADD COLUMN with a nullable
    column is atomic in SQLite (existing rows get NULL for the new column,
    no table rewrite).

    Index on `source` is created here (not in SCHEMA_SQL) because older DBs
    don't yet have the column at the time SCHEMA_SQL runs.
    """
    cols = {row[1] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
    if "source" not in cols:
        db.execute(
            "ALTER TABLE sessions ADD COLUMN source TEXT NOT NULL DEFAULT 'claude-code'"
        )
    if "session_label" not in cols:
        db.execute("ALTER TABLE sessions ADD COLUMN session_label TEXT")
    # Per-client metadata extracted from source-tool session_meta records.
    # Nullable on purpose — older sessions captured before this columns
    # existed simply remain NULL until a re-capture re-reads the rollout.
    if "client_originator" not in cols:
        db.execute("ALTER TABLE sessions ADD COLUMN client_originator TEXT")
    if "client_source" not in cols:
        db.execute("ALTER TABLE sessions ADD COLUMN client_source TEXT")
    if "client_version" not in cols:
        db.execute("ALTER TABLE sessions ADD COLUMN client_version TEXT")
    db.execute("CREATE INDEX IF NOT EXISTS idx_sessions_source ON sessions(source)")

    msg_cols = {row[1] for row in db.execute("PRAGMA table_info(messages)").fetchall()}
    if "message_type" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN message_type TEXT")
    if "is_compact_summary" not in msg_cols:
        db.execute(
            "ALTER TABLE messages ADD COLUMN is_compact_summary INTEGER NOT NULL DEFAULT 0"
        )
    if "is_visible_in_transcript_only" not in msg_cols:
        db.execute(
            "ALTER TABLE messages ADD COLUMN is_visible_in_transcript_only INTEGER NOT NULL DEFAULT 0"
        )
    if "source_path" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN source_path TEXT")
    if "source_line" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN source_line INTEGER")
    if "source_byte_start" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN source_byte_start INTEGER")
    if "source_byte_end" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN source_byte_end INTEGER")
    # Model that produced each assistant message (claude-opus-4-7, gpt-5.5,
    # gemini-3-flash-preview, …). Nullable: user messages and pre-existing
    # rows stay NULL until --backfill-models or a re-capture fills them.
    if "model" not in msg_cols:
        db.execute("ALTER TABLE messages ADD COLUMN model TEXT")

    trigger = db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'messages_au'"
    ).fetchone()
    if trigger and "AFTER UPDATE OF content" not in (trigger[0] or ""):
        db.execute("DROP TRIGGER messages_au")
        db.execute(
            """
            CREATE TRIGGER messages_au AFTER UPDATE OF content ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
                INSERT INTO messages_fts(rowid, content)
                VALUES (new.rowid, new.content);
            END
            """
        )


def get_db(db_path: Path = None) -> sqlite3.Connection:
    """Open (or create) the capture database and initialize schema.

    Args:
        db_path: Override default DB path (useful for testing).

    Returns:
        sqlite3.Connection with WAL mode enabled.
    """
    if db_path is None:
        migrate_legacy_paths()
        path = DB_PATH
    else:
        path = db_path
    db = sqlite3.connect(str(path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA_SQL)
    _migrate_schema(db)
    db.row_factory = sqlite3.Row
    return db


def _line_number_at_offset(path: Path, offset: int) -> int:
    """Return the 1-based line number at a byte offset in a JSONL file."""
    if offset <= 0:
        return 1
    try:
        with open(path, "rb") as f:
            return f.read(offset).count(b"\n") + 1
    except OSError:
        return None


def _project_name_from_path(project_path: str) -> str:
    """Return the displayable project name for a filesystem-like path."""
    if not project_path:
        return ""
    cleaned = project_path.rstrip("/")
    if not cleaned:
        return ""
    return cleaned.split("/")[-1]


def _project_path_from_claude_folder(folder_name: str) -> str:
    """Decode Claude's dash-escaped project folder into a path-ish string."""
    return folder_name.replace("-", "/")


def _iter_claude_project_dirs(claude_dir: Path,
                              cowork_dir: Path | None = None) -> list[Path]:
    """Yield all Claude-style project directories, including Cowork roots."""
    project_dirs = []

    primary_projects = claude_dir / "projects"
    if primary_projects.exists():
        project_dirs.extend(
            project_dir
            for project_dir in sorted(primary_projects.iterdir())
            if project_dir.is_dir()
        )

    cowork_root = cowork_dir
    if cowork_root is None and claude_dir == CLAUDE_DIR:
        cowork_root = CLAUDE_COWORK_DIR
    if cowork_root is not None and cowork_root.exists():
        project_dirs.extend(
            project_dir
            for project_dir in sorted(
                cowork_root.glob("**/.claude/projects/*")
            )
            if project_dir.is_dir()
        )

    return project_dirs


def _maybe_update_session_project(db, session_id: str, cwd: str | None) -> None:
    """Prefer transcript cwd metadata over folder-derived Claude project names."""
    if not isinstance(cwd, str) or not cwd.strip():
        return
    project_path = cwd.strip()
    project_name = _project_name_from_path(project_path) or project_path
    db.execute(
        """
        UPDATE sessions
        SET project_path = ?,
            project_name = ?
        WHERE session_id = ?
          AND (
              project_path IS NULL
              OR project_path = ''
              OR project_path != ?
          )
        """,
        (project_path, project_name, session_id, project_path),
    )


def _extract_user_content(entry: dict) -> str | None:
    """Extract user message text from a JSONL entry.

    Returns string content or None if the entry is a tool_result or not a user message.
    """
    if entry.get("type") != "user":
        return None

    msg = entry.get("message", {})
    content = msg.get("content", "")

    # String content is a plain user message
    if isinstance(content, str) and content.strip():
        return content

    # Array content — could be tool_result blocks, skip those
    if isinstance(content, list):
        # If any item is a tool_result, skip the entire entry
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                return None
        # Otherwise concatenate text items
        texts = []
        for item in content:
            if isinstance(item, str):
                texts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        result = " ".join(texts).strip()
        return result if result else None

    return None


def _extract_assistant_text(entry: dict) -> str | None:
    """Extract assistant text from a JSONL entry, skipping tool_use blocks.

    Returns concatenated text content or None if no text found.
    """
    if entry.get("type") != "assistant":
        return None

    msg = entry.get("message", {})
    content = msg.get("content", [])

    if not isinstance(content, list):
        return None

    texts = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text", "").strip()
            if text:
                texts.append(text)

    return "\n\n".join(texts) if texts else None


def _extract_model(entry: dict) -> str | None:
    """Best-effort model id for an assistant message.

    Claude Code nests it at message.model ("claude-opus-4-7"); Gemini puts it
    at the top level ("gemini-3-flash-preview"). Codex carries the model
    per-turn in turn_context records (not on the message), so it is threaded
    separately inside capture_codex_sessions rather than here.
    """
    msg = entry.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("model"), str) and msg["model"]:
        return msg["model"]
    top = entry.get("model")
    if isinstance(top, str) and top:
        return top
    return None


def capture_sessions(db: sqlite3.Connection = None,
                     claude_dir: Path = None,
                     cowork_dir: Path = None) -> dict:
    """Scan JSONL conversation files and capture user/assistant messages.

    Uses file offset tracking for incremental capture — only reads new data
    since last capture.

    Args:
        db: Database connection (opens default if None).
        claude_dir: Override Claude directory (useful for testing).
        cowork_dir: Override Claude Cowork root (useful for testing).

    Returns:
        Dict with {new_sessions, new_messages, projects_scanned}.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    cdir = claude_dir or CLAUDE_DIR
    stats = {"new_sessions": 0, "new_messages": 0, "projects_scanned": 0}
    project_dirs = _iter_claude_project_dirs(cdir, cowork_dir=cowork_dir)
    if not project_dirs:
        if own_db:
            db.close()
        return stats

    try:
        import socket
        machine_name = socket.gethostname()
        for suffix in [".local", ".localdomain", ".lan"]:
            machine_name = machine_name.replace(suffix, "")
    except OSError:
        machine_name = "unknown"

    # Scan all project directories
    for project_dir in project_dirs:
        stats["projects_scanned"] += 1

        folder_name = project_dir.name
        project_path = _project_path_from_claude_folder(folder_name)
        project_name = _project_name_from_path(project_path) or folder_name

        # Process each conversation file
        for conv_file in project_dir.glob("*.jsonl"):
            file_key = str(conv_file)

            # Get last offset for this file
            row = db.execute(
                "SELECT value FROM capture_state WHERE key = ?",
                (file_key,)
            ).fetchone()
            last_offset = int(row["value"]) if row else 0

            # Check current file size
            try:
                file_size = conv_file.stat().st_size
            except OSError:
                continue

            if file_size <= last_offset:
                continue  # No new data

            # Use conversation file name (without extension) as session_id
            session_id = conv_file.stem

            # Ensure session exists
            existing = db.execute(
                "SELECT session_id FROM sessions WHERE session_id = ?",
                (session_id,)
            ).fetchone()

            if not existing:
                db.execute(
                    "INSERT INTO sessions (session_id, project_path, project_name, started_at, machine_name) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (session_id, project_path, project_name,
                     _utc_iso_now(), machine_name)
                )
                stats["new_sessions"] += 1

            # Read new content from offset
            parent_uuid = None
            try:
                with open(conv_file, "r", encoding="utf-8") as f:
                    f.seek(last_offset)
                    line_number = _line_number_at_offset(conv_file, last_offset)
                    while True:
                        byte_start = f.tell()
                        raw_line = f.readline()
                        if not raw_line:
                            break
                        byte_end = f.tell()
                        line = raw_line.strip()
                        if not line:
                            if line_number is not None:
                                line_number += 1
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            if line_number is not None:
                                line_number += 1
                            continue

                        # Extract timestamp. Modern Claude Code / Codex JSONL
                        # use ISO strings with Z suffix (UTC) — pass through.
                        # Legacy numeric-epoch timestamps are converted to UTC
                        # explicitly so they compare correctly with the rest.
                        ts = entry.get("timestamp")
                        ts_str = None
                        if ts and isinstance(ts, (int, float)):
                            try:
                                ts_str = (
                                    datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                                    .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                                    + "Z"
                                )
                            except (ValueError, OSError):
                                pass
                        elif isinstance(ts, str):
                            ts_str = ts

                        _maybe_update_session_project(
                            db, session_id, entry.get("cwd")
                        )

                        # Try user content
                        user_text = _extract_user_content(entry)
                        if user_text:
                            msg_uuid = str(uuid.uuid4())
                            db.execute(
                                "INSERT OR IGNORE INTO messages "
                                "(uuid, session_id, role, content, timestamp, parent_uuid, "
                                "message_type, is_compact_summary, is_visible_in_transcript_only, "
                                "source_path, source_line, source_byte_start, source_byte_end) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (msg_uuid, session_id, "user", user_text,
                                 ts_str, parent_uuid, entry.get("type"),
                                 1 if entry.get("isCompactSummary") else 0,
                                 1 if entry.get("isVisibleInTranscriptOnly") else 0,
                                 file_key, line_number, byte_start, byte_end)
                            )
                            parent_uuid = msg_uuid
                            stats["new_messages"] += 1

                            # Update session last_message_at
                            if ts_str:
                                db.execute(
                                    "UPDATE sessions SET last_message_at = ? "
                                    "WHERE session_id = ? AND (last_message_at IS NULL OR last_message_at < ?)",
                                    (ts_str, session_id, ts_str)
                                )
                            if line_number is not None:
                                line_number += 1
                            continue

                        # Try assistant text
                        assistant_text = _extract_assistant_text(entry)
                        if assistant_text:
                            msg_uuid = str(uuid.uuid4())
                            db.execute(
                                "INSERT OR IGNORE INTO messages "
                                "(uuid, session_id, role, content, timestamp, parent_uuid, "
                                "message_type, is_compact_summary, is_visible_in_transcript_only, "
                                "source_path, source_line, source_byte_start, source_byte_end, model) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (msg_uuid, session_id, "assistant", assistant_text,
                                 ts_str, parent_uuid, entry.get("type"),
                                 1 if entry.get("isCompactSummary") else 0,
                                 1 if entry.get("isVisibleInTranscriptOnly") else 0,
                                 file_key, line_number, byte_start, byte_end,
                                 _extract_model(entry))
                            )
                            parent_uuid = msg_uuid
                            stats["new_messages"] += 1

                            if ts_str:
                                db.execute(
                                    "UPDATE sessions SET last_message_at = ? "
                                    "WHERE session_id = ? AND (last_message_at IS NULL OR last_message_at < ?)",
                                    (ts_str, session_id, ts_str)
                                )

                        if line_number is not None:
                            line_number += 1

                    # Update offset to current position
                    new_offset = f.tell()

            except OSError as e:
                log.warning("Failed to read %s: %s", conv_file, e)
                continue

            # Save new offset
            db.execute(
                "INSERT OR REPLACE INTO capture_state (key, value) VALUES (?, ?)",
                (file_key, str(new_offset))
            )

        db.commit()

    if own_db:
        db.close()

    return stats


def _extract_codex_text(content) -> str:
    """Extract concatenated text from Codex content blocks."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in (
            "input_text", "output_text", "text"
        ):
            t = block.get("text", "")
            if t:
                parts.append(t)
    return "\n\n".join(parts).strip()


def _load_codex_session_index(codex_dir: Path) -> dict:
    """Load session_index.jsonl into {session_id: thread_name}."""
    index_path = codex_dir / "session_index.jsonl"
    out = {}
    if not index_path.exists():
        return out
    try:
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = rec.get("id")
                if sid:
                    out[sid] = rec.get("thread_name", "") or ""
    except OSError:
        pass
    return out


def _codex_session_id_from_filename(path: Path) -> str:
    """Extract UUID from a Codex rollout filename, fallback to stem."""
    match = _CODEX_UUID_RE.search(path.stem)
    return match.group(1) if match else path.stem


def _maybe_backfill_metadata(db, session_id, originator, source, version):
    """COALESCE-update Codex client metadata on an existing session row.

    Used both during the bootstrap phase (existing session, fresh rollout
    read) and during the message loop (session_meta record encountered
    mid-stream). COALESCE ensures we never replace a previously-stored
    non-null value with NULL — we only fill in missing fields.
    """
    if originator or source or version:
        db.execute(
            "UPDATE sessions SET "
            "client_originator = COALESCE(client_originator, ?), "
            "client_source     = COALESCE(client_source,     ?), "
            "client_version    = COALESCE(client_version,    ?) "
            "WHERE session_id = ?",
            (originator, source, version, session_id),
        )


def capture_codex_sessions(db: sqlite3.Connection = None,
                           codex_dir: Path = None) -> dict:
    """Scan ~/.codex/sessions/ rollout JSONL files and capture user/assistant
    messages into the same SQLite DB used for Claude Code, tagged with
    source='codex'.

    Skips developer-role messages and Codex-injected synthetic user turns
    (environment_context, user_instructions, permissions instructions).
    Tool calls are not captured — text only, mirroring the Claude Code path.

    Uses file offset tracking for incremental capture so re-running is cheap.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    cdir = codex_dir or CODEX_DIR
    sessions_dir = cdir / "sessions"

    stats = {"new_sessions": 0, "new_messages": 0, "files_scanned": 0}

    if not sessions_dir.exists():
        if own_db:
            db.close()
        return stats

    try:
        import socket
        machine_name = socket.gethostname()
        for suffix in [".local", ".localdomain", ".lan"]:
            machine_name = machine_name.replace(suffix, "")
    except OSError:
        machine_name = "unknown"

    label_map = _load_codex_session_index(cdir)

    for conv_file in sorted(sessions_dir.rglob("rollout-*.jsonl")):
        stats["files_scanned"] += 1
        file_key = str(conv_file)

        row = db.execute(
            "SELECT value FROM capture_state WHERE key = ?",
            (file_key,)
        ).fetchone()
        last_offset = int(row["value"]) if row else 0

        try:
            file_size = conv_file.stat().st_size
        except OSError:
            continue

        session_id = _codex_session_id_from_filename(conv_file)
        thread_name = label_map.get(session_id, "")

        existing_row = db.execute(
            "SELECT session_id, project_path FROM sessions WHERE session_id = ?",
            (session_id,)
        ).fetchone()
        existing = existing_row is not None
        cwd = existing_row["project_path"] if existing_row else None
        # Per-client metadata extracted from session_meta payload(s).
        # Captured the first time we see a session_meta record with a value;
        # later session_meta records (e.g., turn_context, fork) don't override.
        client_originator = None
        client_source = None
        client_version = None
        parent_uuid = None
        new_offset = last_offset

        # Metadata bootstrap: always read the first record(s) of the rollout
        # file looking for session_meta, regardless of last_offset. This
        # runs unconditionally — including for files with no new bytes since
        # last capture — so existing sessions whose message-level offset is
        # already past session_meta still get their client metadata
        # backfilled when this code rolls out. Cheap — at most ~8 short
        # JSONL lines read per file per capture run.
        try:
            with open(conv_file, "r", encoding="utf-8") as bootstrap:
                for _ in range(8):
                    line = bootstrap.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("type") == "session_meta":
                        meta = rec.get("payload", {}) or {}
                        if not cwd:
                            cwd = meta.get("cwd") or cwd
                        if not client_originator:
                            client_originator = meta.get("originator") or None
                        if not client_source:
                            client_source = meta.get("source") or None
                        if not client_version:
                            client_version = meta.get("cli_version") or None
                        break
        except OSError:
            pass

        # Backfill metadata onto existing session rows. No-op if nothing
        # was discovered or all fields are already populated. Runs even
        # when there's no new message content to process.
        if existing:
            _maybe_backfill_metadata(db, session_id,
                                     client_originator, client_source,
                                     client_version)

        # Skip the message-loop work when there's no new content to process,
        # but only AFTER the bootstrap backfill above has run.
        if file_size <= last_offset:
            continue

        # Codex records the model per-turn in turn_context records, not on the
        # message. Seed from this session's most recently captured model so a
        # resumed capture (new byte range starting mid-turn) still stamps the
        # right one; turn_context records below update it as they arrive.
        mrow = db.execute(
            "SELECT model FROM messages WHERE session_id = ? AND model IS NOT NULL "
            "ORDER BY source_byte_start DESC LIMIT 1",
            (session_id,)
        ).fetchone()
        current_model = mrow[0] if mrow and mrow[0] else None

        def _ensure_session(ts):
            nonlocal existing
            if existing:
                return
            project_path = cwd or "unknown"
            project_name = (
                thread_name
                or (cwd.rstrip("/").split("/")[-1] if cwd else "codex")
            )
            db.execute(
                "INSERT OR IGNORE INTO sessions "
                "(session_id, project_path, project_name, started_at, "
                " machine_name, source, session_label, "
                " client_originator, client_source, client_version) "
                "VALUES (?, ?, ?, ?, ?, 'codex', ?, ?, ?, ?)",
                (session_id, project_path, project_name,
                 ts or _utc_iso_now(), machine_name,
                 thread_name or None,
                 client_originator, client_source, client_version)
            )
            stats["new_sessions"] += 1
            existing = True

        def _backfill_client_metadata():
            """Backfill client metadata onto an already-inserted session
            (called from inside the message loop when a session_meta record
            arrives after a message in the same file)."""
            _maybe_backfill_metadata(db, session_id,
                                     client_originator, client_source,
                                     client_version)

        try:
            with open(conv_file, "r", encoding="utf-8") as f:
                f.seek(last_offset)
                line_number = _line_number_at_offset(conv_file, last_offset)
                while True:
                    byte_start = f.tell()
                    raw_line = f.readline()
                    if not raw_line:
                        break
                    byte_end = f.tell()
                    line = raw_line.strip()
                    if not line:
                        if line_number is not None:
                            line_number += 1
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        if line_number is not None:
                            line_number += 1
                        continue

                    rtype = rec.get("type")
                    ts = rec.get("timestamp")
                    payload = rec.get("payload", {}) or {}

                    if rtype == "session_meta":
                        cwd = payload.get("cwd", cwd)
                        # Capture client metadata on the first non-null
                        # observation; later session_meta records don't
                        # override (they're typically turn_context/fork).
                        if not client_originator:
                            client_originator = payload.get("originator") or None
                        if not client_source:
                            client_source = payload.get("source") or None
                        if not client_version:
                            client_version = payload.get("cli_version") or None
                        _ensure_session(ts)
                        # If session already existed (this run or a prior run),
                        # backfill metadata fields that are still NULL.
                        _backfill_client_metadata()
                        if line_number is not None:
                            line_number += 1
                        continue

                    if rtype == "turn_context":
                        m = payload.get("model")
                        if m:
                            current_model = m
                        if line_number is not None:
                            line_number += 1
                        continue

                    if rtype != "response_item":
                        if line_number is not None:
                            line_number += 1
                        continue
                    if payload.get("type") != "message":
                        if line_number is not None:
                            line_number += 1
                        continue

                    role = payload.get("role")
                    if role == "developer":
                        if line_number is not None:
                            line_number += 1
                        continue
                    if role not in ("user", "assistant"):
                        if line_number is not None:
                            line_number += 1
                        continue

                    text = _extract_codex_text(payload.get("content", []))
                    if not text:
                        if line_number is not None:
                            line_number += 1
                        continue
                    if role == "user" and text.startswith(CODEX_SKIP_USER_PREFIXES):
                        if line_number is not None:
                            line_number += 1
                        continue

                    _ensure_session(ts)

                    msg_uuid = str(uuid.uuid4())
                    db.execute(
                        "INSERT OR IGNORE INTO messages "
                        "(uuid, session_id, role, content, timestamp, parent_uuid, "
                        "source_path, source_line, source_byte_start, source_byte_end, model) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (msg_uuid, session_id, role, text, ts, parent_uuid,
                         file_key, line_number, byte_start, byte_end,
                         current_model if role == "assistant" else None)
                    )
                    parent_uuid = msg_uuid
                    stats["new_messages"] += 1

                    if ts:
                        db.execute(
                            "UPDATE sessions SET last_message_at = ? "
                            "WHERE session_id = ? AND "
                            "(last_message_at IS NULL OR last_message_at < ?)",
                            (ts, session_id, ts)
                        )

                    if line_number is not None:
                        line_number += 1

                new_offset = f.tell()
        except OSError as e:
            log.warning("Failed to read %s: %s", conv_file, e)
            continue

        db.execute(
            "INSERT OR REPLACE INTO capture_state (key, value) VALUES (?, ?)",
            (file_key, str(new_offset))
        )
        db.commit()

    if own_db:
        db.close()

    return stats


def _extract_gemini_user_text(content) -> str | None:
    """Pull plain text from a Gemini user record's content array."""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if text:
                    parts.append(text)
        return "\n\n".join(parts) if parts else None
    if isinstance(content, str) and content.strip():
        return content
    return None


def capture_gemini_sessions(db: sqlite3.Connection = None,
                            gemini_dir: Path = None) -> dict:
    """Scan ~/.gemini/tmp/<project>/chats/*.jsonl files and capture
    user/model messages into the same SQLite DB used for Claude Code and
    Codex, tagged with source='gemini'.

    Gemini's chat JSONL format:
      Line 1: {"sessionId": "...", "projectHash": "...", "startTime": "...",
               "lastUpdated": "...", "kind": "main"}  (session header)
      Subsequent: {"id": "<uuid>", "timestamp": "...", "type": "user"|"gemini",
                   "content": [...] | "...", ...}
      Plus {"$set": {...}} update markers — skipped.
      Tool-call-only gemini records (empty content + toolCalls field) — skipped.

    Project name -> absolute path mapping comes from ~/.gemini/projects.json.

    Skips ~/.gemini/oauth_creds.json and ~/.gemini/google_accounts.json
    (auth credentials, never indexed).

    Uses file offset tracking via the capture_state table for incremental
    capture so re-running on an unchanged file finishes in milliseconds.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    gdir = gemini_dir or GEMINI_DIR
    tmp_dir = gdir / "tmp"

    stats = {"new_sessions": 0, "new_messages": 0, "files_scanned": 0}

    if not tmp_dir.exists():
        if own_db:
            db.close()
        return stats

    try:
        import socket
        machine_name = socket.gethostname()
        for suffix in [".local", ".localdomain", ".lan"]:
            machine_name = machine_name.replace(suffix, "")
    except OSError:
        machine_name = "unknown"

    # ~/.gemini/projects.json maps absolute path -> project name. Invert
    # so we can look up the absolute path when we visit tmp/<name>/.
    name_to_path: dict[str, str] = {}
    projects_file = gdir / "projects.json"
    if projects_file.exists():
        try:
            data = json.loads(projects_file.read_text(encoding="utf-8"))
            for path, name in (data.get("projects") or {}).items():
                if name and path:
                    name_to_path[name] = path
        except (json.JSONDecodeError, OSError):
            pass

    for project_dir in sorted(tmp_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            continue

        project_name = project_dir.name
        project_path = name_to_path.get(project_name, str(project_dir))

        for conv_file in sorted(chats_dir.glob("session-*.jsonl")):
            stats["files_scanned"] += 1
            file_key = str(conv_file)
            file_str = file_key

            row = db.execute(
                "SELECT value FROM capture_state WHERE key = ?",
                (file_key,)
            ).fetchone()
            last_offset = int(row["value"]) if row else 0

            try:
                file_size = conv_file.stat().st_size
            except OSError:
                continue

            if file_size <= last_offset:
                # Still need to read the header on first encounter to ensure
                # the session row exists; otherwise nothing new to do.
                continue

            session_id: str | None = None
            session_started: str | None = None

            # Bootstrap session_id from the file's first line — the header
            # is the only record that carries `sessionId`. On subsequent
            # captures we've seeked past it, so without this we'd lose
            # the session linkage for newly-appended messages.
            try:
                with open(conv_file, "r", encoding="utf-8") as bootstrap:
                    first = bootstrap.readline().strip()
                    if first:
                        try:
                            head = json.loads(first)
                            if isinstance(head, dict) and head.get("sessionId"):
                                session_id = head.get("sessionId")
                                session_started = head.get("startTime") or _utc_iso_now()
                        except json.JSONDecodeError:
                            pass
            except OSError:
                pass

            try:
                with open(conv_file, "r", encoding="utf-8") as f:
                    f.seek(last_offset)
                    line_number = _line_number_at_offset(conv_file, last_offset)
                    while True:
                        byte_start = f.tell()
                        raw_line = f.readline()
                        if not raw_line:
                            break
                        byte_end = f.tell()
                        if line_number is not None:
                            line_number += 1
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Header line: capture session_id + start time, ensure row.
                        if isinstance(rec, dict) and "sessionId" in rec and rec.get("kind"):
                            session_id = rec.get("sessionId")
                            session_started = rec.get("startTime") or _utc_iso_now()
                            if session_id and not db.execute(
                                "SELECT 1 FROM sessions WHERE session_id = ?",
                                (session_id,)
                            ).fetchone():
                                db.execute(
                                    "INSERT OR IGNORE INTO sessions "
                                    "(session_id, project_path, project_name, "
                                    " started_at, last_message_at, "
                                    " machine_name, source) "
                                    "VALUES (?, ?, ?, ?, ?, ?, 'gemini')",
                                    (session_id, project_path, project_name,
                                     session_started, session_started,
                                     machine_name)
                                )
                                stats["new_sessions"] += 1
                            continue

                        # $set update markers: skip.
                        if isinstance(rec, dict) and "$set" in rec:
                            continue

                        rec_type = rec.get("type")
                        if rec_type not in ("user", "gemini"):
                            continue

                        # Backfill session_id from the record if header was
                        # missed (mid-file resume).
                        if not session_id:
                            session_id = rec.get("sessionId")
                            if session_id and not db.execute(
                                "SELECT 1 FROM sessions WHERE session_id = ?",
                                (session_id,)
                            ).fetchone():
                                ts0 = rec.get("timestamp") or _utc_iso_now()
                                db.execute(
                                    "INSERT OR IGNORE INTO sessions "
                                    "(session_id, project_path, project_name, "
                                    " started_at, last_message_at, "
                                    " machine_name, source) "
                                    "VALUES (?, ?, ?, ?, ?, ?, 'gemini')",
                                    (session_id, project_path, project_name,
                                     ts0, ts0, machine_name)
                                )
                                stats["new_sessions"] += 1
                        if not session_id:
                            continue

                        if rec_type == "user":
                            content = _extract_gemini_user_text(rec.get("content"))
                            role = "user"
                        else:  # gemini
                            content = rec.get("content")
                            role = "assistant"
                            if not isinstance(content, str) or not content.strip():
                                # Tool-call-only or empty model turn — skip.
                                continue

                        if not content:
                            continue

                        msg_uuid = rec.get("id") or str(uuid.uuid5(
                            uuid.NAMESPACE_URL,
                            f"gemini:{session_id}:{byte_start}"
                        ))

                        if db.execute(
                            "SELECT 1 FROM messages WHERE uuid = ?",
                            (msg_uuid,)
                        ).fetchone():
                            continue

                        ts = rec.get("timestamp") or _utc_iso_now()
                        db.execute(
                            "INSERT INTO messages "
                            "(uuid, session_id, role, content, timestamp, "
                            " source_path, source_line, "
                            " source_byte_start, source_byte_end, model) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (msg_uuid, session_id, role, content, ts,
                             file_str, line_number,
                             byte_start, byte_end,
                             rec.get("model") if role == "assistant" else None)
                        )
                        stats["new_messages"] += 1

                        db.execute(
                            "UPDATE sessions SET last_message_at = ? "
                            "WHERE session_id = ? "
                            "AND (last_message_at IS NULL OR last_message_at < ?)",
                            (ts, session_id, ts)
                        )

                    new_offset = f.tell()
            except OSError:
                continue

            db.execute(
                "INSERT OR REPLACE INTO capture_state (key, value) VALUES (?, ?)",
                (file_key, str(new_offset))
            )

    db.commit()
    if own_db:
        db.close()
    return stats


def backfill_models(db: sqlite3.Connection = None) -> dict:
    """One-time pass: populate messages.model for already-captured rows.

    Claude Code and Gemini record the model on the assistant message line, so
    we re-read each message's exact bytes via its stored provenance
    (source_byte_start/end) and pull the model out. Codex records the model
    per-turn in turn_context records (not on the message), so its files are
    walked start-to-finish tracking the current model and matching messages
    by (source_path, source_byte_start).

    Idempotent and additive: only touches rows where role='assistant' and
    model IS NULL, so re-running is safe and skips already-filled rows. New
    captures fill model inline, so this is only needed once after upgrading.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    stats = {"updated": 0, "files": 0, "skipped_missing_file": 0}

    # ── Claude Code + Gemini: model is on the message's own line ──
    rows = db.execute(
        "SELECT m.uuid, m.source_path, m.source_byte_start, m.source_byte_end "
        "FROM messages m JOIN sessions s ON m.session_id = s.session_id "
        "WHERE m.role = 'assistant' AND m.model IS NULL AND s.source != 'codex' "
        "AND m.source_path IS NOT NULL AND m.source_byte_start IS NOT NULL "
        "AND m.source_byte_end IS NOT NULL"
    ).fetchall()

    by_path = defaultdict(list)
    for r in rows:
        by_path[r["source_path"]].append(r)

    for path, items in by_path.items():
        try:
            fh = open(path, "rb")
        except OSError:
            stats["skipped_missing_file"] += len(items)
            continue
        with fh:
            for r in items:
                try:
                    fh.seek(r["source_byte_start"])
                    raw = fh.read(r["source_byte_end"] - r["source_byte_start"])
                    entry = json.loads(raw)
                except (OSError, ValueError):
                    continue
                model = _extract_model(entry)
                if model:
                    db.execute(
                        "UPDATE messages SET model = ? WHERE uuid = ?",
                        (model, r["uuid"])
                    )
                    stats["updated"] += 1
        stats["files"] += 1
    db.commit()

    # ── Codex: walk each file tracking the per-turn turn_context model ──
    codex_paths = db.execute(
        "SELECT DISTINCT m.source_path FROM messages m "
        "JOIN sessions s ON m.session_id = s.session_id "
        "WHERE s.source = 'codex' AND m.role = 'assistant' AND m.model IS NULL "
        "AND m.source_path IS NOT NULL"
    ).fetchall()

    for prow in codex_paths:
        path = prow["source_path"]
        want = {
            r["source_byte_start"]: r["uuid"]
            for r in db.execute(
                "SELECT m.source_byte_start, m.uuid FROM messages m "
                "JOIN sessions s ON m.session_id = s.session_id "
                "WHERE m.source_path = ? AND s.source = 'codex' "
                "AND m.role = 'assistant' AND m.model IS NULL "
                "AND m.source_byte_start IS NOT NULL",
                (path,),
            ).fetchall()
        }
        if not want:
            continue
        try:
            fh = open(path, "r", encoding="utf-8")
        except OSError:
            stats["skipped_missing_file"] += len(want)
            continue
        current_model = None
        with fh:
            while True:
                byte_start = fh.tell()
                raw_line = fh.readline()
                if not raw_line:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "turn_context":
                    m = (rec.get("payload") or {}).get("model")
                    if m:
                        current_model = m
                    continue
                if byte_start in want and current_model:
                    db.execute(
                        "UPDATE messages SET model = ? WHERE uuid = ?",
                        (current_model, want[byte_start])
                    )
                    stats["updated"] += 1
        stats["files"] += 1
    db.commit()

    if own_db:
        db.close()
    return stats


def capture_claude_ai_export(
    export_path,
    db: sqlite3.Connection = None,
    project_name: str = "claude-ai",
) -> dict:
    """Ingest a Claude.ai conversations.json export into the messages DB.

    The export is a list of conversation objects, each with a
    `chat_messages` array. Senders are 'human' / 'assistant'; we map them
    to role='user' / 'assistant' to match the rest of cc-anywhere.

    Tagged with source='claude-ai' so it sits alongside claude-code,
    codex, and gemini in the same tables. Idempotent: re-ingesting the
    same export is safe (uuids are unique-keyed).
    """
    own_db = db is None
    if own_db:
        db = get_db()

    stats = {"new_sessions": 0, "new_messages": 0,
             "conversations_scanned": 0, "skipped_empty": 0}

    path = Path(export_path)
    if not path.exists():
        if own_db:
            db.close()
        return stats

    try:
        with open(path, "r", encoding="utf-8") as f:
            conversations = json.load(f)
    except (OSError, json.JSONDecodeError):
        if own_db:
            db.close()
        return stats

    try:
        import socket
        machine_name = socket.gethostname()
        for suffix in [".local", ".localdomain", ".lan"]:
            machine_name = machine_name.replace(suffix, "")
    except OSError:
        machine_name = "unknown"

    project_path = str(path.parent)

    for conv in conversations:
        stats["conversations_scanned"] += 1
        session_id = conv.get("uuid")
        if not session_id:
            continue

        started_at = conv.get("created_at") or _utc_iso_now()
        last_at = conv.get("updated_at") or started_at
        label = conv.get("name") or ""

        existing = db.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()

        if not existing:
            db.execute(
                "INSERT INTO sessions "
                "(session_id, project_path, project_name, started_at, "
                " last_message_at, machine_name, source, session_label) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session_id, project_path, project_name, started_at,
                 last_at, machine_name, "claude-ai", label),
            )
            stats["new_sessions"] += 1

        for msg in conv.get("chat_messages", []) or []:
            msg_uuid = msg.get("uuid")
            if not msg_uuid:
                continue

            if db.execute(
                "SELECT 1 FROM messages WHERE uuid = ?",
                (msg_uuid,),
            ).fetchone():
                continue

            text = msg.get("text")
            if not text:
                # Fall back to assembling from typed content blocks.
                blocks = msg.get("content") or []
                parts = []
                for b in blocks:
                    if isinstance(b, dict) and b.get("type") == "text":
                        t = b.get("text") or ""
                        if t:
                            parts.append(t)
                text = "\n\n".join(parts)
            text = (text or "").strip()
            if not text:
                stats["skipped_empty"] += 1
                continue

            sender = msg.get("sender")
            role = "user" if sender == "human" else "assistant"
            ts = msg.get("created_at") or started_at

            db.execute(
                "INSERT INTO messages "
                "(uuid, session_id, role, content, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (msg_uuid, session_id, role, text, ts),
            )
            stats["new_messages"] += 1

            db.execute(
                "UPDATE sessions SET last_message_at = ? "
                "WHERE session_id = ? "
                "AND (last_message_at IS NULL OR last_message_at < ?)",
                (ts, session_id, ts),
            )

    db.commit()
    if own_db:
        db.close()
    return stats


def _update_message_source(db, session_id, role, content, timestamp,
                           source_path, source_line, byte_start, byte_end) -> bool:
    """Attach raw transcript provenance to one already-captured message."""
    row = db.execute(
        """
        SELECT rowid
        FROM messages
        WHERE session_id = ?
          AND role = ?
          AND content = ?
          AND (timestamp IS ? OR timestamp = ?)
          AND source_path IS NULL
        ORDER BY rowid
        LIMIT 1
        """,
        (session_id, role, content, timestamp, timestamp),
    ).fetchone()
    if row is None:
        return False

    db.execute(
        """
        UPDATE messages
        SET source_path = ?,
            source_line = ?,
            source_byte_start = ?,
            source_byte_end = ?
        WHERE rowid = ?
        """,
        (source_path, source_line, byte_start, byte_end, row["rowid"]),
    )
    return True


def _backfill_claude_file(db, conv_file: Path) -> int:
    session_id = conv_file.stem
    updated = 0
    source_path = str(conv_file)
    try:
        with open(conv_file, "r", encoding="utf-8") as f:
            line_number = 1
            while True:
                byte_start = f.tell()
                raw_line = f.readline()
                if not raw_line:
                    break
                byte_end = f.tell()
                line = raw_line.strip()
                if not line:
                    line_number += 1
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    line_number += 1
                    continue

                ts = entry.get("timestamp")
                ts_str = None
                if ts and isinstance(ts, (int, float)):
                    try:
                        ts_str = (
                            datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                            + "Z"
                        )
                    except (ValueError, OSError):
                        pass
                elif isinstance(ts, str):
                    ts_str = ts

                user_text = _extract_user_content(entry)
                if user_text and _update_message_source(
                    db, session_id, "user", user_text, ts_str,
                    source_path, line_number, byte_start, byte_end
                ):
                    updated += 1
                    line_number += 1
                    continue

                assistant_text = _extract_assistant_text(entry)
                if assistant_text and _update_message_source(
                    db, session_id, "assistant", assistant_text, ts_str,
                    source_path, line_number, byte_start, byte_end
                ):
                    updated += 1
                line_number += 1
    except OSError as e:
        log.warning("Failed to read %s for source backfill: %s", conv_file, e)
    return updated


def _backfill_codex_file(db, conv_file: Path) -> int:
    session_id = _codex_session_id_from_filename(conv_file)
    updated = 0
    source_path = str(conv_file)
    try:
        with open(conv_file, "r", encoding="utf-8") as f:
            line_number = 1
            while True:
                byte_start = f.tell()
                raw_line = f.readline()
                if not raw_line:
                    break
                byte_end = f.tell()
                line = raw_line.strip()
                if not line:
                    line_number += 1
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    line_number += 1
                    continue

                if rec.get("type") != "response_item":
                    line_number += 1
                    continue
                payload = rec.get("payload", {}) or {}
                if payload.get("type") != "message":
                    line_number += 1
                    continue
                role = payload.get("role")
                if role == "developer" or role not in ("user", "assistant"):
                    line_number += 1
                    continue
                text = _extract_codex_text(payload.get("content", []))
                if not text:
                    line_number += 1
                    continue
                if role == "user" and text.startswith(CODEX_SKIP_USER_PREFIXES):
                    line_number += 1
                    continue

                if _update_message_source(
                    db, session_id, role, text, rec.get("timestamp"),
                    source_path, line_number, byte_start, byte_end
                ):
                    updated += 1
                line_number += 1
    except OSError as e:
        log.warning("Failed to read %s for source backfill: %s", conv_file, e)
    return updated


def backfill_source_provenance(db: sqlite3.Connection = None,
                               claude_dir: Path = None,
                               cowork_dir: Path = None,
                               codex_dir: Path = None) -> dict:
    """Attach raw transcript file/line/byte provenance to existing messages.

    Older DB rows predate the source_* columns. This scans local raw
    transcripts, matches existing message rows by session/role/timestamp/content,
    and fills only NULL source fields. It is additive and safe to re-run.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    stats = {
        "claude_files_scanned": 0,
        "codex_files_scanned": 0,
        "messages_updated": 0,
    }

    cdir = claude_dir or CLAUDE_DIR
    for project_dir in _iter_claude_project_dirs(cdir, cowork_dir=cowork_dir):
        for conv_file in project_dir.glob("*.jsonl"):
            stats["claude_files_scanned"] += 1
            stats["messages_updated"] += _backfill_claude_file(db, conv_file)

    xdir = codex_dir or CODEX_DIR
    sessions_dir = xdir / "sessions"
    if sessions_dir.exists():
        for conv_file in sessions_dir.rglob("rollout-*.jsonl"):
            stats["codex_files_scanned"] += 1
            stats["messages_updated"] += _backfill_codex_file(db, conv_file)

    db.commit()
    if own_db:
        db.close()
    return stats


def db_search(query: str, project: str = None, limit: int = 25,
              db: sqlite3.Connection = None) -> list:
    """Full-text search across captured messages.

    Uses FTS5 MATCH first, falls back to LIKE if MATCH fails.

    Args:
        query: Search string.
        project: Optional project name filter.
        limit: Max results to return.
        db: Database connection (opens default if None).

    Returns:
        List of dicts with session_id, project_name, role, content (truncated), timestamp.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    results = []

    # Try FTS5 MATCH first
    try:
        if project:
            rows = db.execute(
                """
                SELECT m.uuid, m.session_id, m.role, m.content, m.timestamp,
                       m.message_type, m.is_compact_summary,
                       m.is_visible_in_transcript_only,
                       s.project_name, s.project_path
                FROM messages m
                JOIN messages_fts fts ON m.rowid = fts.rowid
                JOIN sessions s ON m.session_id = s.session_id
                WHERE messages_fts MATCH ? AND s.project_name = ?
                ORDER BY m.timestamp DESC
                LIMIT ?
                """,
                (query, project, limit)
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT m.uuid, m.session_id, m.role, m.content, m.timestamp,
                       m.message_type, m.is_compact_summary,
                       m.is_visible_in_transcript_only,
                       s.project_name, s.project_path
                FROM messages m
                JOIN messages_fts fts ON m.rowid = fts.rowid
                JOIN sessions s ON m.session_id = s.session_id
                WHERE messages_fts MATCH ?
                ORDER BY m.timestamp DESC
                LIMIT ?
                """,
                (query, limit)
            ).fetchall()
    except sqlite3.OperationalError:
        # FTS MATCH can fail on certain query syntax; fall back to LIKE
        like_pattern = f"%{query}%"
        if project:
            rows = db.execute(
                """
                SELECT m.uuid, m.session_id, m.role, m.content, m.timestamp,
                       m.message_type, m.is_compact_summary,
                       m.is_visible_in_transcript_only,
                       s.project_name, s.project_path
                FROM messages m
                JOIN sessions s ON m.session_id = s.session_id
                WHERE m.content LIKE ? AND s.project_name = ?
                ORDER BY m.timestamp DESC
                LIMIT ?
                """,
                (like_pattern, project, limit)
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT m.uuid, m.session_id, m.role, m.content, m.timestamp,
                       m.message_type, m.is_compact_summary,
                       m.is_visible_in_transcript_only,
                       s.project_name, s.project_path
                FROM messages m
                JOIN sessions s ON m.session_id = s.session_id
                WHERE m.content LIKE ?
                ORDER BY m.timestamp DESC
                LIMIT ?
                """,
                (like_pattern, limit)
            ).fetchall()

    for row in rows:
        content = row["content"]
        results.append({
            "uuid": row["uuid"],
            "session_id": row["session_id"],
            "role": row["role"],
            "content": content[:200],
            "full_content": content,
            "timestamp": row["timestamp"],
            "message_type": row["message_type"],
            "is_compact_summary": bool(row["is_compact_summary"]),
            "is_visible_in_transcript_only": bool(row["is_visible_in_transcript_only"]),
            "project_name": row["project_name"],
            "project_path": row["project_path"],
        })

    if own_db:
        db.close()

    return results


def get_session_messages(session_id: str, db: sqlite3.Connection = None) -> list:
    """Get all messages for a session in chronological order.

    Args:
        session_id: The session to retrieve.
        db: Database connection (opens default if None).

    Returns:
        List of dicts with uuid, role, content, timestamp.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    rows = db.execute(
        """
        SELECT uuid, role, content, timestamp, parent_uuid, message_type,
               is_compact_summary, is_visible_in_transcript_only
        FROM messages
        WHERE session_id = ?
        ORDER BY rowid ASC
        """,
        (session_id,)
    ).fetchall()

    messages = [dict(row) for row in rows]

    if own_db:
        db.close()

    return messages


def get_capture_stats(db: sqlite3.Connection = None) -> dict:
    """Get statistics about the capture database.

    Returns:
        Dict with total_sessions, total_messages, projects, db_size_bytes,
        earliest_message, latest_message.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    total_sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_messages = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    projects = db.execute("SELECT COUNT(DISTINCT project_name) FROM sessions").fetchone()[0]

    earliest = db.execute(
        "SELECT MIN(timestamp) FROM messages WHERE timestamp IS NOT NULL"
    ).fetchone()[0]
    latest = db.execute(
        "SELECT MAX(timestamp) FROM messages WHERE timestamp IS NOT NULL"
    ).fetchone()[0]

    db_path = DB_PATH
    try:
        db_size = db_path.stat().st_size
    except OSError:
        db_size = 0

    if own_db:
        db.close()

    return {
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "projects": projects,
        "db_size_bytes": db_size,
        "earliest_message": earliest,
        "latest_message": latest,
    }


def export_for_sync(since: str = None, machine_name: str = None,
                    days: int = 30, content_limit: int = 500,
                    db: sqlite3.Connection = None) -> dict:
    """Export recent captured records as JSON for cross-machine sync.

    Full content stays in the local SQLite DB. Exports are truncated and
    time-limited to keep the GitHub sync repo small.

    Args:
        since: ISO timestamp — only export records after this time.
            If None, defaults to `days` ago.
        machine_name: Filter to records from this machine.
        days: How many days back to export (default 30). Ignored if `since` is set.
        content_limit: Max chars of assistant content to include (default 500).
            User messages are sent in full (they're short).
        db: Database connection (opens default if None).

    Returns:
        Dict with 'sessions' and 'messages' lists ready for JSON serialization.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    # Default to last N days if no explicit since. Use UTC to match the
    # timestamp shape stored in messages.timestamp (UTC ISO with Z suffix).
    if not since:
        cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(days=days)
        since = cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Export sessions
    if machine_name:
        sessions = db.execute(
            "SELECT * FROM sessions WHERE last_message_at > ? AND machine_name = ?",
            (since, machine_name)
        ).fetchall()
    else:
        sessions = db.execute(
            "SELECT * FROM sessions WHERE last_message_at > ?",
            (since,)
        ).fetchall()

    session_ids = [s["session_id"] for s in sessions]

    # Export messages for those sessions, truncating assistant content
    messages = []
    for sid in session_ids:
        rows = db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY rowid",
            (sid,)
        ).fetchall()
        for row in rows:
            msg = dict(row)
            if msg["role"] == "assistant" and len(msg.get("content", "")) > content_limit:
                msg["content"] = msg["content"][:content_limit] + "..."
            messages.append(msg)

    if own_db:
        db.close()

    return {
        "sessions": [dict(s) for s in sessions],
        "messages": messages,
    }


def import_from_sync(records: dict, source_machine: str,
                     db: sqlite3.Connection = None) -> dict:
    """Import captured records from another machine, deduplicating by uuid.

    Args:
        records: Dict with 'sessions' and 'messages' lists.
        source_machine: Name of the source machine (for logging).
        db: Database connection (opens default if None).

    Returns:
        Dict with {imported_sessions, imported_messages, skipped}.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    stats = {"imported_sessions": 0, "imported_messages": 0, "skipped": 0}

    for session in records.get("sessions", []):
        existing = db.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?",
            (session["session_id"],)
        ).fetchone()

        if not existing:
            db.execute(
                "INSERT INTO sessions (session_id, project_path, project_name, "
                "started_at, last_message_at, machine_name, source, session_label, "
                "client_originator, client_source, client_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (session["session_id"], session["project_path"],
                 session["project_name"], session.get("started_at"),
                 session.get("last_message_at"),
                 session.get("machine_name", source_machine),
                 session.get("source", "claude-code"),
                 session.get("session_label"),
                 session.get("client_originator"),
                 session.get("client_source"),
                 session.get("client_version"))
            )
            stats["imported_sessions"] += 1
        else:
            # Update last_message_at if newer
            new_ts = session.get("last_message_at")
            if new_ts:
                db.execute(
                    "UPDATE sessions SET last_message_at = ? "
                    "WHERE session_id = ? AND (last_message_at IS NULL OR last_message_at < ?)",
                    (new_ts, session["session_id"], new_ts)
                )

    for message in records.get("messages", []):
        existing = db.execute(
            "SELECT uuid FROM messages WHERE uuid = ?",
            (message["uuid"],)
        ).fetchone()

        if not existing:
            db.execute(
                "INSERT INTO messages (uuid, session_id, role, content, timestamp, parent_uuid, "
                "message_type, is_compact_summary, is_visible_in_transcript_only, "
                "source_path, source_line, source_byte_start, source_byte_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (message["uuid"], message["session_id"], message["role"],
                 message["content"], message.get("timestamp"),
                 message.get("parent_uuid"), message.get("message_type"),
                 1 if message.get("is_compact_summary") else 0,
                 1 if message.get("is_visible_in_transcript_only") else 0,
                 message.get("source_path"), message.get("source_line"),
                 message.get("source_byte_start"), message.get("source_byte_end"))
            )
            stats["imported_messages"] += 1
        else:
            stats["skipped"] += 1

    db.commit()

    if own_db:
        db.close()

    return stats
