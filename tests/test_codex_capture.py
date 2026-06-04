#!/usr/bin/env python3
"""Tests for Codex session capture in sqlite_capture module."""

import json
from pathlib import Path

import pytest

from cc_anywhere.sqlite_capture import (
    _codex_session_id_from_filename,
    _extract_codex_text,
    _load_codex_session_index,
    capture_codex_sessions,
    capture_sessions,
    db_search,
    get_capture_stats,
    get_db,
)


# ============ Fixtures ============


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database."""
    db_path = tmp_path / "test.db"
    db = get_db(db_path)
    yield db
    db.close()


SESSION_UUID = "019dcfbd-7abb-7a30-ad18-137d1cbc7d3d"
ROLLOUT_NAME = f"rollout-2026-04-27T09-19-54-{SESSION_UUID}.jsonl"


def _write_codex_rollout(codex_dir: Path, entries: list, filename: str = ROLLOUT_NAME) -> Path:
    """Write a Codex rollout JSONL under sessions/YYYY/MM/DD/."""
    sub = codex_dir / "sessions" / "2026" / "04" / "27"
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / filename
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return path


def _session_meta(
    cwd: str = "/Users/test/Projects/myproj",
    originator: str = None,
    source: str = None,
    cli_version: str = None,
) -> dict:
    payload = {"cwd": cwd}
    if originator is not None:
        payload["originator"] = originator
    if source is not None:
        payload["source"] = source
    if cli_version is not None:
        payload["cli_version"] = cli_version
    return {
        "type": "session_meta",
        "timestamp": "2026-04-27T09:19:54.000Z",
        "payload": payload,
    }


def _user_msg(text: str, ts: str = "2026-04-27T09:20:00.000Z") -> dict:
    return {
        "type": "response_item",
        "timestamp": ts,
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def _assistant_msg(text: str, ts: str = "2026-04-27T09:20:30.000Z") -> dict:
    return {
        "type": "response_item",
        "timestamp": ts,
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
    }


@pytest.fixture
def codex_dir(tmp_path):
    """Create a fake ~/.codex/ directory with one rollout and a session_index."""
    cdir = tmp_path / ".codex"
    cdir.mkdir()

    # session_index entry with thread_name
    with open(cdir / "session_index.jsonl", "w") as f:
        f.write(json.dumps({
            "id": SESSION_UUID,
            "thread_name": "Review bio-target project",
            "updated_at": "2026-04-27T09:20:30.000Z",
        }) + "\n")

    entries = [
        _session_meta("/Users/test/Projects/myapp"),
        _user_msg("How do I shard the corpus?"),
        _assistant_msg("Use a hash on the PMID modulo the worker count."),
        _user_msg("Can you show me with code?"),
        _assistant_msg("Here's the implementation."),
    ]
    _write_codex_rollout(cdir, entries)
    return cdir


# ============ Helper functions ============


class TestExtractCodexText:
    def test_string(self):
        assert _extract_codex_text("hello") == "hello"

    def test_input_text_block(self):
        assert _extract_codex_text(
            [{"type": "input_text", "text": "hello"}]
        ) == "hello"

    def test_output_text_block(self):
        assert _extract_codex_text(
            [{"type": "output_text", "text": "world"}]
        ) == "world"

    def test_text_block(self):
        assert _extract_codex_text(
            [{"type": "text", "text": "plain"}]
        ) == "plain"

    def test_multiple_blocks_joined(self):
        result = _extract_codex_text([
            {"type": "input_text", "text": "first"},
            {"type": "input_text", "text": "second"},
        ])
        assert result == "first\n\nsecond"

    def test_unknown_block_type_skipped(self):
        result = _extract_codex_text([
            {"type": "input_text", "text": "keep"},
            {"type": "unknown", "text": "drop"},
        ])
        assert result == "keep"

    def test_empty_list(self):
        assert _extract_codex_text([]) == ""

    def test_non_list_non_string(self):
        assert _extract_codex_text({"x": "y"}) == ""


class TestCodexSessionIdFromFilename:
    def test_extracts_uuid(self):
        path = Path(f"rollout-2026-04-27T09-19-54-{SESSION_UUID}.jsonl")
        assert _codex_session_id_from_filename(path) == SESSION_UUID

    def test_fallback_to_stem(self):
        path = Path("not-a-rollout-file.jsonl")
        assert _codex_session_id_from_filename(path) == "not-a-rollout-file"


class TestLoadCodexSessionIndex:
    def test_loads_thread_names(self, tmp_path):
        cdir = tmp_path / ".codex"
        cdir.mkdir()
        with open(cdir / "session_index.jsonl", "w") as f:
            f.write(json.dumps({"id": "abc", "thread_name": "Hello"}) + "\n")
            f.write(json.dumps({"id": "def", "thread_name": "World"}) + "\n")

        result = _load_codex_session_index(cdir)
        assert result == {"abc": "Hello", "def": "World"}

    def test_missing_index(self, tmp_path):
        cdir = tmp_path / ".codex-empty"
        cdir.mkdir()
        assert _load_codex_session_index(cdir) == {}

    def test_skips_malformed_lines(self, tmp_path):
        cdir = tmp_path / ".codex"
        cdir.mkdir()
        with open(cdir / "session_index.jsonl", "w") as f:
            f.write(json.dumps({"id": "abc", "thread_name": "Good"}) + "\n")
            f.write("NOT JSON\n")
            f.write(json.dumps({"id": "def", "thread_name": "AlsoGood"}) + "\n")

        result = _load_codex_session_index(cdir)
        assert result == {"abc": "Good", "def": "AlsoGood"}


# ============ Capture ============


class TestCaptureCodexSessions:
    def test_basic_capture(self, tmp_db, codex_dir):
        result = capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        assert result["new_sessions"] == 1
        assert result["new_messages"] == 4  # 2 user + 2 assistant
        assert result["files_scanned"] == 1

    def test_session_has_codex_source(self, tmp_db, codex_dir):
        capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        rows = tmp_db.execute("SELECT source FROM sessions").fetchall()
        assert all(r["source"] == "codex" for r in rows)

    def test_session_label_from_index(self, tmp_db, codex_dir):
        capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        row = tmp_db.execute("SELECT session_label FROM sessions").fetchone()
        assert row["session_label"] == "Review bio-target project"

    def test_project_name_uses_label_when_present(self, tmp_db, codex_dir):
        capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        row = tmp_db.execute("SELECT project_name FROM sessions").fetchone()
        # When label is present, it becomes project_name
        assert row["project_name"] == "Review bio-target project"

    def test_project_path_from_session_meta(self, tmp_db, codex_dir):
        capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        row = tmp_db.execute("SELECT project_path FROM sessions").fetchone()
        assert row["project_path"] == "/Users/test/Projects/myapp"

    def test_messages_store_source_provenance(self, tmp_db, codex_dir):
        capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)

        row = tmp_db.execute(
            "SELECT source_path, source_line, source_byte_start, source_byte_end "
            "FROM messages WHERE role = 'user' ORDER BY source_line LIMIT 1"
        ).fetchone()

        assert row["source_path"].endswith(ROLLOUT_NAME)
        assert row["source_line"] == 2
        assert row["source_byte_end"] > row["source_byte_start"]

    def test_project_name_falls_back_to_cwd(self, tmp_db, tmp_path):
        cdir = tmp_path / ".codex-no-label"
        cdir.mkdir()
        # No session_index.jsonl — falls back to cwd basename
        entries = [
            _session_meta("/Users/test/Projects/falconwing"),
            _user_msg("Hello"),
            _assistant_msg("Hi back"),
        ]
        _write_codex_rollout(cdir, entries)

        capture_codex_sessions(db=tmp_db, codex_dir=cdir)
        row = tmp_db.execute("SELECT project_name FROM sessions").fetchone()
        assert row["project_name"] == "falconwing"

    def test_skips_developer_role(self, tmp_db, tmp_path):
        cdir = tmp_path / ".codex-dev"
        cdir.mkdir()
        entries = [
            _session_meta(),
            {
                "type": "response_item",
                "timestamp": "2026-04-27T09:20:00.000Z",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "system instruction"}],
                },
            },
            _user_msg("real user message"),
            _assistant_msg("real assistant reply"),
        ]
        _write_codex_rollout(cdir, entries)

        result = capture_codex_sessions(db=tmp_db, codex_dir=cdir)
        assert result["new_messages"] == 2  # only the real user + assistant pair

        rows = tmp_db.execute("SELECT content FROM messages").fetchall()
        contents = [r["content"] for r in rows]
        assert not any("system instruction" in c for c in contents)

    def test_skips_synthetic_user_prefixes(self, tmp_db, tmp_path):
        cdir = tmp_path / ".codex-synthetic"
        cdir.mkdir()
        entries = [
            _session_meta(),
            _user_msg("<environment_context>cwd: /tmp"),
            _user_msg("<user_instructions>be brief"),
            _user_msg("<permissions instructions>read only"),
            _user_msg("genuine user message"),
            _assistant_msg("reply"),
        ]
        _write_codex_rollout(cdir, entries)

        result = capture_codex_sessions(db=tmp_db, codex_dir=cdir)
        # Only 1 user (genuine) + 1 assistant
        assert result["new_messages"] == 2

        user_rows = tmp_db.execute(
            "SELECT content FROM messages WHERE role='user'"
        ).fetchall()
        assert len(user_rows) == 1
        assert user_rows[0]["content"] == "genuine user message"

    def test_skips_function_calls(self, tmp_db, tmp_path):
        cdir = tmp_path / ".codex-tools"
        cdir.mkdir()
        entries = [
            _session_meta(),
            _user_msg("run a command"),
            {
                "type": "response_item",
                "timestamp": "2026-04-27T09:20:30.000Z",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "arguments": '{"command": "ls"}',
                },
            },
            _assistant_msg("done"),
        ]
        _write_codex_rollout(cdir, entries)

        result = capture_codex_sessions(db=tmp_db, codex_dir=cdir)
        assert result["new_messages"] == 2

    def test_incremental_capture(self, tmp_db, codex_dir):
        r1 = capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        assert r1["new_messages"] == 4

        r2 = capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        assert r2["new_messages"] == 0
        assert r2["new_sessions"] == 0

    def test_incremental_picks_up_appended_messages(self, tmp_db, codex_dir):
        capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)

        # Append a new user/assistant pair to the same rollout
        sub = codex_dir / "sessions" / "2026" / "04" / "27"
        rollout = sub / ROLLOUT_NAME
        with open(rollout, "a") as f:
            f.write(json.dumps(_user_msg("follow up", "2026-04-27T10:00:00Z")) + "\n")
            f.write(json.dumps(_assistant_msg("reply two", "2026-04-27T10:00:30Z")) + "\n")

        r2 = capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        assert r2["new_messages"] == 2
        assert r2["new_sessions"] == 0

    def test_missing_codex_dir(self, tmp_db, tmp_path):
        nope = tmp_path / "nonexistent-codex"
        result = capture_codex_sessions(db=tmp_db, codex_dir=nope)
        assert result == {"new_sessions": 0, "new_messages": 0, "files_scanned": 0}

    def test_search_finds_codex_content(self, tmp_db, codex_dir):
        capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)
        results = db_search("shard the corpus", db=tmp_db)
        assert len(results) > 0
        assert any("shard" in r["content"] for r in results)


# ============ Mixed-source integration ============


class TestClientMetadata:
    """Codex session_meta exposes originator / source / cli_version. Capture
    persists those into sessions.client_originator / client_source /
    client_version so we can distinguish Codex Desktop, codex-tui, and
    codex_exec sessions without losing the broad source='codex' tag."""

    def _setup(self, tmp_db, tmp_path, originator, source, cli_version,
               *, file_suffix="0"):
        cdir = tmp_path / f".codex-{originator.replace(' ', '-')}-{file_suffix}"
        cdir.mkdir()
        entries = [
            _session_meta(
                cwd=f"/Users/test/Projects/{originator.replace(' ', '-').lower()}",
                originator=originator,
                source=source,
                cli_version=cli_version,
            ),
            _user_msg("hello"),
            _assistant_msg("hi back"),
        ]
        # Use a unique rollout filename per scenario so different originators
        # don't collide on the same session_id.
        unique_uuid = SESSION_UUID[:-len(file_suffix)] + file_suffix
        _write_codex_rollout(
            cdir, entries,
            filename=f"rollout-2026-04-27T09-19-54-{unique_uuid}.jsonl",
        )
        capture_codex_sessions(db=tmp_db, codex_dir=cdir)
        return unique_uuid

    def test_codex_desktop_metadata(self, tmp_db, tmp_path):
        """Codex Desktop sessions carry originator='Codex Desktop' source='vscode'."""
        sid = self._setup(tmp_db, tmp_path, "Codex Desktop", "vscode", "26.417.40842",
                          file_suffix="1")
        row = tmp_db.execute(
            "SELECT source, client_originator, client_source, client_version "
            "FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()
        assert row["source"] == "codex"
        assert row["client_originator"] == "Codex Desktop"
        assert row["client_source"] == "vscode"
        assert row["client_version"] == "26.417.40842"

    def test_codex_tui_metadata(self, tmp_db, tmp_path):
        """Codex CLI (TUI) sessions carry originator='codex-tui' source='cli'."""
        sid = self._setup(tmp_db, tmp_path, "codex-tui", "cli", "0.118.0",
                          file_suffix="2")
        row = tmp_db.execute(
            "SELECT source, client_originator, client_source, client_version "
            "FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()
        assert row["source"] == "codex"
        assert row["client_originator"] == "codex-tui"
        assert row["client_source"] == "cli"
        assert row["client_version"] == "0.118.0"

    def test_codex_exec_metadata(self, tmp_db, tmp_path):
        """Codex non-interactive (codex exec) carries originator='codex_exec' source='exec'."""
        sid = self._setup(tmp_db, tmp_path, "codex_exec", "exec", "0.118.0",
                          file_suffix="3")
        row = tmp_db.execute(
            "SELECT source, client_originator, client_source, client_version "
            "FROM sessions WHERE session_id = ?",
            (sid,),
        ).fetchone()
        assert row["source"] == "codex"
        assert row["client_originator"] == "codex_exec"
        assert row["client_source"] == "exec"
        assert row["client_version"] == "0.118.0"

    def test_missing_metadata_fields_stay_null(self, tmp_db, tmp_path):
        """A rollout whose session_meta omits the metadata keys (older Codex
        format, or a partial payload) leaves the columns NULL — never crashes
        and never invents data."""
        cdir = tmp_path / ".codex-no-meta"
        cdir.mkdir()
        entries = [
            _session_meta(),  # cwd only, no originator/source/cli_version
            _user_msg("hello"),
            _assistant_msg("hi back"),
        ]
        _write_codex_rollout(cdir, entries)
        capture_codex_sessions(db=tmp_db, codex_dir=cdir)
        row = tmp_db.execute(
            "SELECT client_originator, client_source, client_version "
            "FROM sessions WHERE source = 'codex'"
        ).fetchone()
        assert row["client_originator"] is None
        assert row["client_source"] is None
        assert row["client_version"] is None

    def test_backfill_when_session_predates_metadata_capture(self, tmp_db, tmp_path):
        """If a session was captured before this feature existed (or with a
        rollout that lacked metadata), and we later re-encounter the same
        rollout with metadata available, the columns get backfilled."""
        cdir = tmp_path / ".codex-backfill"
        cdir.mkdir()

        # Step 1: pre-existing session row with NULL metadata, simulating an
        # earlier capture that didn't have these fields populated.
        tmp_db.execute(
            """INSERT INTO sessions (
                session_id, project_path, project_name, started_at,
                last_message_at, machine_name, source, session_label,
                client_originator, client_source, client_version
            ) VALUES (?, ?, ?, ?, ?, ?, 'codex', NULL, NULL, NULL, NULL)""",
            (
                SESSION_UUID,
                "/Users/test/Projects/myapp",
                "myapp",
                "2026-04-27T09:19:54.000Z",
                "2026-04-27T09:20:30.000Z",
                "test-machine",
            ),
        )
        tmp_db.commit()

        # Step 2: write a rollout file matching that session_id with metadata.
        entries = [
            _session_meta(
                cwd="/Users/test/Projects/myapp",
                originator="codex-tui",
                source="cli",
                cli_version="0.118.0",
            ),
            _user_msg("first turn"),
            _assistant_msg("first reply"),
        ]
        _write_codex_rollout(cdir, entries)

        # Step 3: re-capture. Since the existing session row is unchanged,
        # capture_codex_sessions sees existing=True at the start, but the
        # session_meta record triggers _backfill_client_metadata which
        # COALESCEs metadata onto the row.
        capture_codex_sessions(db=tmp_db, codex_dir=cdir)

        row = tmp_db.execute(
            "SELECT client_originator, client_source, client_version "
            "FROM sessions WHERE session_id = ?",
            (SESSION_UUID,),
        ).fetchone()
        assert row["client_originator"] == "codex-tui"
        assert row["client_source"] == "cli"
        assert row["client_version"] == "0.118.0"


class TestMixedCapture:
    def test_claude_and_codex_coexist(self, tmp_db, tmp_path):
        # Build a Claude Code project with one session
        claude_dir = tmp_path / ".claude"
        proj = claude_dir / "projects" / "-Users-test-myproject"
        proj.mkdir(parents=True)
        with open(proj / "claude123.jsonl", "w") as f:
            f.write(json.dumps({
                "type": "user",
                "message": {"content": "Claude Code question"},
                "timestamp": 1700000000000,
            }) + "\n")
            f.write(json.dumps({
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Claude Code answer"}]
                },
                "timestamp": 1700000010000,
            }) + "\n")

        # Build a Codex rollout
        codex_dir = tmp_path / ".codex"
        codex_dir.mkdir()
        _write_codex_rollout(codex_dir, [
            _session_meta(),
            _user_msg("Codex question"),
            _assistant_msg("Codex answer"),
        ])

        # Capture both
        capture_sessions(db=tmp_db, claude_dir=claude_dir)
        capture_codex_sessions(db=tmp_db, codex_dir=codex_dir)

        # Both sources represented
        sources = {r["source"] for r in tmp_db.execute(
            "SELECT DISTINCT source FROM sessions"
        ).fetchall()}
        assert sources == {"claude-code", "codex"}

        # Search returns hits from both
        results = db_search("question", db=tmp_db)
        assert any("Claude Code" in r["content"] for r in results)
        assert any("Codex" in r["content"] for r in results)

        # Stats reflect combined
        stats = get_capture_stats(db=tmp_db)
        assert stats["total_sessions"] == 2
