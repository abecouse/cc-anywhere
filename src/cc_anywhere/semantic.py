#!/usr/bin/env python3
"""
Local natural-language search over captured Claude Code sessions.

This module builds a lightweight semantic index inside the existing SQLite
capture database. It intentionally has no third-party dependencies: chunks are
represented by sparse lexical vectors with a small coding-oriented synonym map.
That gives cc-anywhere a useful offline baseline while leaving room for a
future embedding provider or sqlite-vec backend.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cc_anywhere.sqlite_capture import _local_display, _utc_iso_now, get_db


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS semantic_chunks (
    chunk_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    project_path TEXT,
    started_at TEXT,
    ended_at TEXT,
    content TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    first_message_uuid TEXT,
    last_message_uuid TEXT,
    source_path TEXT,
    source_start_line INTEGER,
    source_end_line INTEGER,
    source_byte_start INTEGER,
    source_byte_end INTEGER
);

CREATE VIRTUAL TABLE IF NOT EXISTS semantic_chunks_fts USING fts5(
    content,
    project_name,
    content='semantic_chunks',
    content_rowid='rowid'
);
"""


STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did",
    "do", "does", "for", "from", "had", "has", "have", "how", "i", "if",
    "in", "is", "it", "me", "my", "of", "on", "or", "our", "that", "the",
    "then", "there", "this", "to", "was", "we", "were", "what", "when",
    "where", "which", "with", "you",
}


SYNONYMS = {
    "handoff": ["continue", "resume", "pickup", "sync", "machine"],
    "laptop": ["machine", "device", "macbook", "computer"],
    "computer": ["machine", "device", "laptop"],
    "sync": ["synchronize", "push", "pull", "handoff", "remote"],
    "search": ["find", "lookup", "query", "retrieve"],
    "semantic": ["natural", "language", "meaning", "vector"],
    "natural": ["semantic", "language", "meaning"],
    "memory": ["history", "context", "conversation", "session"],
    "conversation": ["session", "message", "history", "chat"],
    "bug": ["error", "issue", "failure", "crash"],
    "fix": ["repair", "resolve", "patch", "debug"],
    "auth": ["authentication", "login", "user"],
    "database": ["db", "sqlite", "storage"],
}


TOKEN_RE = re.compile(r"[a-zA-Z0-9_./:-]+")


def ensure_semantic_schema(db):
    """Create semantic search tables if needed."""
    db.executescript(SCHEMA_SQL)
    cols = {
        row[1]
        for row in db.execute("PRAGMA table_info(semantic_chunks)").fetchall()
    }
    for name, decl in [
        ("first_message_uuid", "TEXT"),
        ("last_message_uuid", "TEXT"),
        ("source_path", "TEXT"),
        ("source_start_line", "INTEGER"),
        ("source_end_line", "INTEGER"),
        ("source_byte_start", "INTEGER"),
        ("source_byte_end", "INTEGER"),
    ]:
        if name not in cols:
            db.execute(f"ALTER TABLE semantic_chunks ADD COLUMN {name} {decl}")


def normalize_token(token):
    """Normalize a token with a tiny stemmer for local lexical matching."""
    token = token.lower().strip("._-:/")
    if len(token) <= 2 or token in STOPWORDS:
        return ""
    for suffix in ("ization", "ations", "ation", "ingly", "edly", "ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 3:
            return token[: -len(suffix)]
    return token


def tokenize(text, expand=False):
    """Tokenize text into normalized terms, optionally adding query synonyms."""
    tokens = []
    raw_tokens = TOKEN_RE.findall(text.lower())
    for raw in raw_tokens:
        token = normalize_token(raw)
        if not token:
            continue
        tokens.append(token)
        if expand:
            for synonym in SYNONYMS.get(token, []):
                normalized = normalize_token(synonym)
                if normalized:
                    tokens.append(normalized)

    for left, right in zip(tokens, tokens[1:]):
        tokens.append(f"{left}_{right}")

    return tokens


def make_vector(text, expand=False):
    """Create a normalized sparse vector represented as a dict."""
    counts = Counter(tokenize(text, expand=expand))
    if not counts:
        return {}

    weighted = {term: 1.0 + math.log(count) for term, count in counts.items()}
    norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
    return {term: value / norm for term, value in weighted.items()}


def cosine(left, right):
    """Cosine similarity between sparse vectors."""
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(term, 0.0) for term, value in left.items())


def _chunk_id(session_id, index, content):
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
    return f"{session_id}:{index}:{digest}"


def _format_message(row):
    role = "Summary" if row["is_compact_summary"] else (
        "You" if row["role"] == "user" else "Claude"
    )
    return f"{role}: {row['content']}"


def _iter_session_chunks(rows, max_messages=8, max_chars=2600):
    chunk = []
    char_count = 0
    chunk_index = 0

    for row in rows:
        formatted = _format_message(row)
        too_many_messages = len(chunk) >= max_messages
        too_many_chars = chunk and char_count + len(formatted) > max_chars
        if too_many_messages or too_many_chars:
            yield chunk_index, chunk
            chunk_index += 1
            chunk = []
            char_count = 0
        chunk.append(row)
        char_count += len(formatted)

    if chunk:
        yield chunk_index, chunk


def rebuild_semantic_index(db=None, max_messages=8, max_chars=2600,
                            *, full_rebuild=False):
    """Build or update the semantic chunk index.

    By default this is **incremental** — only sessions with new messages
    since their last-indexed chunk are processed, and new messages are
    chunked and appended as new chunks. Existing chunks are never touched.

    Pass ``full_rebuild=True`` to wipe the chunk + FTS tables and re-index
    from scratch (use after a corpus migration or to recover from a
    suspected corrupted index).

    Args:
        db: Optional SQLite connection. Defaults to the capture database.
        max_messages: Maximum messages per chunk.
        max_chars: Approximate maximum chunk size.
        full_rebuild: If True, wipe and re-index from scratch.

    Returns:
        Dict with sessions/chunks/messages counts (counts are NEW work
        in the incremental path) plus skipped_sessions.
    """
    own_db = db is None
    if own_db:
        db = get_db()
    ensure_semantic_schema(db)

    if full_rebuild:
        db.execute("DELETE FROM semantic_chunks_fts")
        db.execute("DELETE FROM semantic_chunks")

    sessions = db.execute(
        """
        SELECT session_id, project_name, project_path, last_message_at
        FROM sessions
        ORDER BY COALESCE(last_message_at, started_at, session_id)
        """
    ).fetchall()

    indexed_chunks = 0
    indexed_messages = 0
    skipped_sessions = 0
    now = _utc_iso_now()

    for session in sessions:
        # Find where this session left off in the chunk index, if at all.
        last_indexed = db.execute(
            """
            SELECT MAX(ended_at) AS ended_at
            FROM semantic_chunks
            WHERE session_id = ?
            """,
            (session["session_id"],),
        ).fetchone()
        last_ended = last_indexed["ended_at"] if last_indexed else None

        # Skip sessions whose chunks are already current.
        if (not full_rebuild
                and last_ended
                and session["last_message_at"]
                and session["last_message_at"] <= last_ended):
            skipped_sessions += 1
            continue

        # Fetch only new messages in the incremental path; everything in
        # the full-rebuild path.
        if not full_rebuild and last_ended:
            rows = db.execute(
                """
                SELECT uuid, role, content, timestamp, is_compact_summary,
                       source_path, source_line, source_byte_start, source_byte_end
                FROM messages
                WHERE session_id = ?
                  AND (timestamp IS NULL OR timestamp > ?)
                ORDER BY rowid ASC
                """,
                (session["session_id"], last_ended),
            ).fetchall()
        else:
            rows = db.execute(
                """
                SELECT uuid, role, content, timestamp, is_compact_summary,
                       source_path, source_line, source_byte_start, source_byte_end
                FROM messages
                WHERE session_id = ?
                ORDER BY rowid ASC
                """,
                (session["session_id"],),
            ).fetchall()

        if not rows:
            # No new messages even though session.last_message_at suggested
            # there might be. Treat this as a skip so the caller can see
            # that no work was needed.
            if not full_rebuild and last_ended:
                skipped_sessions += 1
            continue

        # Continue chunk numbering from where we left off so chunk_ids
        # don't collide with previously-stored chunks for this session.
        last_index_row = db.execute(
            """
            SELECT chunk_id FROM semantic_chunks
            WHERE session_id = ?
            ORDER BY rowid DESC LIMIT 1
            """,
            (session["session_id"],),
        ).fetchone()
        base_index = 0
        if last_index_row and last_index_row["chunk_id"]:
            parts = last_index_row["chunk_id"].split(":")
            if len(parts) >= 2 and parts[-2].isdigit():
                base_index = int(parts[-2]) + 1

        for chunk_offset, chunk_rows in _iter_session_chunks(rows, max_messages, max_chars):
            content = "\n\n".join(_format_message(row) for row in chunk_rows)
            vector = make_vector(content)
            if not vector:
                continue

            timestamps = [row["timestamp"] for row in chunk_rows if row["timestamp"]]
            source_rows = [row for row in chunk_rows if row["source_path"]]
            chunk_id = _chunk_id(session["session_id"], base_index + chunk_offset, content)

            cur = db.execute(
                """
                INSERT OR IGNORE INTO semantic_chunks (
                    chunk_id, session_id, project_name, project_path,
                    started_at, ended_at, content, message_count,
                    vector_json, updated_at, first_message_uuid,
                    last_message_uuid, source_path, source_start_line,
                    source_end_line, source_byte_start, source_byte_end
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    session["session_id"],
                    session["project_name"],
                    session["project_path"],
                    min(timestamps) if timestamps else None,
                    max(timestamps) if timestamps else None,
                    content,
                    len(chunk_rows),
                    json.dumps(vector, sort_keys=True),
                    now,
                    chunk_rows[0]["uuid"],
                    chunk_rows[-1]["uuid"],
                    source_rows[0]["source_path"] if source_rows else None,
                    min(
                        row["source_line"]
                        for row in source_rows
                        if row["source_line"] is not None
                    ) if any(row["source_line"] is not None for row in source_rows) else None,
                    max(
                        row["source_line"]
                        for row in source_rows
                        if row["source_line"] is not None
                    ) if any(row["source_line"] is not None for row in source_rows) else None,
                    min(
                        row["source_byte_start"]
                        for row in source_rows
                        if row["source_byte_start"] is not None
                    ) if any(row["source_byte_start"] is not None for row in source_rows) else None,
                    max(
                        row["source_byte_end"]
                        for row in source_rows
                        if row["source_byte_end"] is not None
                    ) if any(row["source_byte_end"] is not None for row in source_rows) else None,
                ),
            )
            if cur.rowcount > 0:
                # Insert succeeded — sync FTS for this chunk only.
                rowid_row = db.execute(
                    "SELECT rowid FROM semantic_chunks WHERE chunk_id = ?",
                    (chunk_id,),
                ).fetchone()
                if rowid_row:
                    db.execute(
                        """
                        INSERT INTO semantic_chunks_fts(rowid, content, project_name)
                        VALUES (?, ?, ?)
                        """,
                        (rowid_row["rowid"], content, session["project_name"]),
                    )
                indexed_chunks += 1
                indexed_messages += len(chunk_rows)

    db.commit()

    if own_db:
        db.close()

    return {
        "sessions": len(sessions),
        "skipped_sessions": skipped_sessions,
        "chunks": indexed_chunks,
        "messages": indexed_messages,
    }


def _fts_query(query):
    """Build a conservative FTS query from normalized query tokens."""
    terms = []
    seen = set()
    for term in tokenize(query, expand=False):
        if term in seen:
            continue
        seen.add(term)
        # Keep terms simple for FTS MATCH. TOKEN_RE already restricts the
        # alphabet, but bigram tokens with underscores add little value here.
        if "_" in term:
            continue
        terms.append(term)
    return " OR ".join(terms)


def _fts_candidates(db, query, limit):
    fts_query = _fts_query(query)
    if not fts_query:
        return {}
    try:
        rows = db.execute(
            """
            SELECT c.chunk_id, bm25(semantic_chunks_fts) AS rank
            FROM semantic_chunks c
            JOIN semantic_chunks_fts fts ON c.rowid = fts.rowid
            WHERE semantic_chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        like = f"%{query}%"
        rows = db.execute(
            "SELECT chunk_id, 0.0 AS rank FROM semantic_chunks WHERE content LIKE ? LIMIT ?",
            (like, limit),
        ).fetchall()
    if not rows:
        return {}
    ranks = [abs(row["rank"]) for row in rows]
    max_rank = max(ranks) or 1.0
    return {
        row["chunk_id"]: 1.0 - min(abs(row["rank"]) / max_rank, 1.0)
        for row in rows
    }


def _query_terms(query):
    return {
        term
        for term in tokenize(query, expand=True)
        if len(term) > 2 and "_" not in term
    }


def _term_overlap_score(query_terms, content):
    if not query_terms:
        return 0.0
    content_terms = set(tokenize(content, expand=False))
    hits = len(query_terms & content_terms)
    return hits / len(query_terms)


def _cutoff_iso(days):
    if days is None:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _search_chunks(query, limit=10, db=None, since=None, mode="hybrid"):
    """Search indexed chunks.

    mode='hybrid' (default): cosine similarity fused with bm25 + term overlap.
    mode='semantic':         cosine similarity only — no keyword fusion.
    """
    own_db = db is None
    if own_db:
        db = get_db()
    ensure_semantic_schema(db)

    query_vector = make_vector(query, expand=True)
    use_keyword = mode == "hybrid"
    query_terms = _query_terms(query) if use_keyword else set()
    fts_hits = _fts_candidates(db, query, max(limit * 6, 25)) if use_keyword else {}

    rows = db.execute(
        """
        SELECT chunk_id, session_id, project_name, project_path,
               started_at, ended_at, content, message_count, vector_json,
               source_path, source_start_line, source_end_line,
               source_byte_start, source_byte_end
        FROM semantic_chunks
        WHERE (? IS NULL OR COALESCE(ended_at, started_at, updated_at) >= ?)
        """,
        (since, since),
    ).fetchall()

    scored = []
    for row in rows:
        vector = json.loads(row["vector_json"])
        vector_score = cosine(query_vector, vector)
        fts_score = fts_hits.get(row["chunk_id"], 0.0) if use_keyword else 0.0
        overlap_score = _term_overlap_score(query_terms, row["content"]) if use_keyword else 0.0
        if use_keyword:
            score = vector_score + (0.18 * fts_score) + (0.12 * overlap_score)
        else:
            score = vector_score
        if score <= 0:
            continue
        item = dict(row)
        item.pop("vector_json", None)
        item["score"] = score
        item["match_type"] = "hybrid" if fts_score else "semantic"
        item["vector_score"] = vector_score
        item["fts_score"] = fts_score
        item["overlap_score"] = overlap_score
        item["excerpt"] = make_excerpt(item["content"], query)
        scored.append(item)

    scored.sort(key=lambda item: item["score"], reverse=True)

    if own_db:
        db.close()

    return scored[:limit]


def semantic_search(query, limit=10, db=None, recent_days=30,
                    include_all_time_fallback=True, mode="hybrid"):
    """Search indexed chunks, preferring recent results before all-time.

    By default recall searches the last 30 days first. If nothing matches,
    it falls back to all-time and marks each result with scope metadata so
    callers can explain what happened.

    mode is passed through to `_search_chunks` — 'hybrid' (default) fuses
    cosine + bm25, 'semantic' uses cosine only.
    """
    own_db = db is None
    if own_db:
        db = get_db()

    scope = "all-time"
    searched_recent_days = None
    results = []
    if recent_days is not None:
        searched_recent_days = recent_days
        since = _cutoff_iso(recent_days)
        results = _search_chunks(query, limit=limit, db=db, since=since, mode=mode)
        scope = f"last-{recent_days}-days"

    if not results and include_all_time_fallback:
        results = _search_chunks(query, limit=limit, db=db, since=None, mode=mode)
        scope = "all-time"

    for result in results:
        result["scope"] = scope
        result["searched_recent_days"] = searched_recent_days
        result["fallback_used"] = scope == "all-time" and searched_recent_days is not None

    if own_db:
        db.close()

    return results


def make_excerpt(content, query, size=220):
    """Return an excerpt centered around the densest query-term match."""
    normalized = content.replace("\n", " ")
    lowered = normalized.lower()
    tokens = [token for token in _query_terms(query) if len(token) > 2]
    positions = []
    for token in tokens:
        start = 0
        while True:
            idx = lowered.find(token, start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + len(token)

    if positions:
        positions.sort()
        half = max(size // 2, 1)
        best_center = positions[0]
        best_count = -1
        best_distance = None
        for pos in positions:
            left = pos - half
            right = pos + half
            in_window = [p for p in positions if left <= p <= right]
            count = len(in_window)
            distance = sum(abs(p - pos) for p in in_window)
            if count > best_count or (count == best_count and (
                best_distance is None or distance < best_distance
            )):
                best_center = pos
                best_count = count
                best_distance = distance
        window_positions = [
            p for p in positions if best_center - half <= p <= best_center + half
        ]
        if window_positions:
            cluster_start = min(window_positions)
            cluster_end = max(window_positions)
            start = max(0, ((cluster_start + cluster_end) // 2) - half)
        else:
            start = max(0, best_center - half)
    else:
        start = 0

    excerpt = normalized[start : start + size]
    if start > 0:
        excerpt = "..." + excerpt
    if start + size < len(content):
        excerpt += "..."
    return excerpt


# ─── Temporal recall ──────────────────────────────────────────────────────
# When a user asks "what did I work on today" or "what was I just doing,"
# semantic search is the wrong tool — there's no keyword to match against.
# The right answer is a chronological pull of recent sessions in a time
# window. This block detects the intent and returns that pull instead.
#
# See SPEC-temporal-recall-routing.md at the repo root for full design.

# Time-window phrases mapped to (start_offset, end_offset) in hours.
# Order matters — longer phrases match first to avoid e.g. "this week"
# being captured by "this".
_TEMPORAL_PATTERNS = [
    # phrase regex                              window_label    hours_back
    (r"\bthis morning\b",                       "this morning",   24),
    (r"\bthis afternoon\b",                     "this afternoon", 24),
    (r"\bthis evening\b",                       "this evening",   24),
    (r"\bthis week\b",                          "this week",     168),
    (r"\bthis month\b",                         "this month",    720),
    (r"\blast hour\b",                          "last hour",       1),
    (r"\blast (\d+)\s*hours?\b",                "last N hours",   -1),  # parsed
    (r"\blast (\d+)\s*days?\b",                 "last N days",    -1),  # parsed
    (r"\blast day\b",                           "last day",       24),
    (r"\blast week\b",                          "last week",     168),
    (r"\blast month\b",                         "last month",    720),
    (r"\bthe other day\b",                      "the other day",  72),
    (r"\b(today|just today)\b",                 "today",          24),
    (r"\byesterday\b",                          "yesterday",      48),
    (r"\b(just|recently|lately)\b",             "recent",          4),
    (r"\bmost recent\b",                        "most recent",    24),
    (r"\bcatch me up\b",                        "catch-up",       48),
    (r"\b(read|show me) (today|recent|todays)\b","read today",    24),
    (r"\bwhat was i (just|recently) (working|talking|doing|building)\b",
                                                "just-working",    4),
    (r"\bwhat (have|did) (i|we) (work|been working|do|been doing)\b",
                                                "what-i-did",     24),
]

# Topical-anchor signals — proper-noun-ish tokens, common bio/tech terms,
# project names. If the query has BOTH a temporal phrase AND a clear
# topical anchor, route to "hybrid" rather than pure temporal.
_TOPICAL_ANCHOR_RE = re.compile(
    r"\b("
    r"[A-Z][A-Z0-9]{1,7}|"               # gene-symbol-shape: TIGIT, KRAS, PD-1
    r"[A-Z][a-zA-Z]+(?:[A-Z][a-z]+)+|"   # CamelCase: BioAgent, OpenTargets
    r"about\s+\w+|"                       # "about <thing>"
    r"on\s+\w+\s+\w+"                     # "on the <thing>"
    r")\b"
)


def detect_temporal_intent(query):
    """Parse a query for time-window intent.

    Returns a dict with:
        kind: "temporal" | "hybrid" | None
        window_label: human label (e.g. "today")
        hours_back: int — how far back to slice
        topical: str | None — the topical anchor when intent is hybrid

    Returns None when no temporal phrase matches.
    """
    q = query.lower()

    matched = None
    for pattern, label, default_hours in _TEMPORAL_PATTERNS:
        m = re.search(pattern, q)
        if not m:
            continue
        # Parse explicit "last N hours" / "last N days" if applicable
        if default_hours == -1:
            try:
                n = int(m.group(1))
                hours = n if "hour" in label else n * 24
            except (ValueError, IndexError):
                continue
        else:
            hours = default_hours
        matched = {"window_label": label, "hours_back": hours}
        break

    if matched is None:
        return None

    # Hybrid: temporal phrase + topical anchor → return both
    topical = None
    anchor = _TOPICAL_ANCHOR_RE.search(query)  # case-sensitive on original
    if anchor:
        # Trim "about" / "on" prefix to keep just the subject
        topical = re.sub(r"^(about|on)\s+", "", anchor.group(0).strip(), flags=re.I)

    return {
        "kind": "hybrid" if topical else "temporal",
        "window_label": matched["window_label"],
        "hours_back": matched["hours_back"],
        "topical": topical,
    }


def recent_sessions(hours_back=24, limit=20, db=None):
    """Return sessions with last-message activity in the trailing window.

    Output is chronological (most-recent-first). Each row carries enough
    metadata for a one-line summary: project, source, started_at,
    last_message_at, message_count, and one preview chunk.
    """
    own_db = db is None
    if own_db:
        db = get_db()
    ensure_semantic_schema(db)

    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=hours_back)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Pull sessions with last_message_at >= cutoff. Some legacy sessions
    # may have NULL last_message_at — fall back to started_at in that case.
    rows = db.execute(
        """
        SELECT s.session_id, s.project_name, s.project_path,
               s.started_at, s.last_message_at,
               s.source, s.session_label,
               s.client_originator, s.client_source, s.client_version,
               COUNT(m.uuid) AS message_count
        FROM sessions s
        LEFT JOIN messages m ON m.session_id = s.session_id
        WHERE COALESCE(s.last_message_at, s.started_at) >= ?
        GROUP BY s.session_id
        ORDER BY COALESCE(s.last_message_at, s.started_at) DESC
        LIMIT ?
        """,
        (cutoff, limit),
    ).fetchall()

    sessions = []
    for row in rows:
        d = dict(row)
        # Pull the most recent semantic chunk for a preview (if indexed)
        preview_row = db.execute(
            """
            SELECT chunk_id, content
            FROM semantic_chunks
            WHERE session_id = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (d["session_id"],),
        ).fetchone()
        if preview_row:
            d["chunk_id"] = preview_row["chunk_id"]
            d["preview"] = make_excerpt(preview_row["content"], "", size=240)
        else:
            d["chunk_id"] = None
            d["preview"] = ""
        sessions.append(d)

    if own_db:
        db.close()
    return sessions


def latest_project_sessions(project_path=None, project_name=None, limit=1, db=None):
    """Return the most recent sessions for the current project, if any."""
    own_db = db is None
    if own_db:
        db = get_db()
    ensure_semantic_schema(db)

    rows = []
    if project_path:
        rows = db.execute(
            """
            SELECT s.session_id, s.project_name, s.project_path,
                   s.started_at, s.last_message_at,
                   s.source, s.session_label,
                   s.client_originator, s.client_source, s.client_version,
                   COUNT(m.uuid) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.session_id
            WHERE s.project_path = ?
            GROUP BY s.session_id
            ORDER BY COALESCE(s.last_message_at, s.started_at) DESC
            LIMIT ?
            """,
            (project_path, limit),
        ).fetchall()

    if not rows and project_name:
        rows = db.execute(
            """
            SELECT s.session_id, s.project_name, s.project_path,
                   s.started_at, s.last_message_at,
                   s.source, s.session_label,
                   s.client_originator, s.client_source, s.client_version,
                   COUNT(m.uuid) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.session_id
            WHERE LOWER(s.project_name) = LOWER(?)
            GROUP BY s.session_id
            ORDER BY COALESCE(s.last_message_at, s.started_at) DESC
            LIMIT ?
            """,
            (project_name, limit),
        ).fetchall()

    sessions = []
    for row in rows:
        d = dict(row)
        preview_row = db.execute(
            """
            SELECT chunk_id, content
            FROM semantic_chunks
            WHERE session_id = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (d["session_id"],),
        ).fetchone()
        if preview_row:
            d["chunk_id"] = preview_row["chunk_id"]
            d["preview"] = make_excerpt(preview_row["content"], "", size=240)
        else:
            d["chunk_id"] = None
            d["preview"] = ""
        sessions.append(d)

    if own_db:
        db.close()
    return sessions


def _parse_iso_local_date(ts):
    """Best-effort parse of an ISO-ish timestamp into a (date_key, label).

    Returns (None, None) if unparseable. date_key is YYYY-MM-DD; label is
    a short human form like 'Fri May 1' for grouping.
    """
    if not ts:
        return None, None
    try:
        # Sessions are stored UTC-ish; for grouping we just want the date
        # portion in local time. A faithful parse is in _local_display.
        local = _local_display(ts)
        # _local_display returns e.g. "2026-05-01 13:54 PDT"
        date_part = local.split(" ")[0] if local else None
        if not date_part:
            return None, None
        dt = datetime.strptime(date_part, "%Y-%m-%d")
        return date_part, dt.strftime("%a %b %-d")
    except (ValueError, TypeError):
        return None, None


def _summarize_session_brief(sessions, max_per_day=3):
    """One-line-per-session helper used by the day-detail branch."""
    out = []
    for s in sessions[:max_per_day]:
        ts = _local_display(s.get("last_message_at") or s.get("started_at"))
        time_part = ts.split(" ", 1)[1] if ts and " " in ts else ts
        source = s.get("source") or "claude-code"
        msgs = s.get("message_count") or 0
        line = f"  {time_part}  {s['project_name']} · {source} · {msgs} msgs"
        out.append(line)
        if s.get("preview"):
            preview = s["preview"].replace("\n", " ")[:160]
            out.append(f"     {preview}")
    if len(sessions) > max_per_day:
        out.append(f"  + {len(sessions) - max_per_day} more session(s) this day")
    return out


def _summarize_temporal(intent, sessions):
    """Format a chronological-pull result as the user-facing answer.

    Two output modes depending on window width:
      - hours_back <= 48: per-session list (today/yesterday/last hour)
      - hours_back > 48:  daily rollup with top projects + 1-3 highlight
        sessions per day (this week / this month / catch-up)

    The wider-window mode addresses the 'limit=20 truncates a week' bug
    where 20 sessions all from the last 36h hide the rest of the week.
    """
    label = intent["window_label"]
    if not sessions:
        return (
            f"No captured sessions in the {label} window. "
            "If you expected activity here, run `cc-anywhere --capture` first "
            "to ingest the latest transcripts."
        )

    wide = intent["hours_back"] > 48
    if wide:
        return _summarize_temporal_wide(intent, sessions)

    # Narrow window: per-session list (existing behavior).
    lines = [
        f"Sessions from {label} ({len(sessions)} session{'s' if len(sessions) != 1 else ''}):",
        "",
    ]
    for idx, s in enumerate(sessions, 1):
        ts = _local_display(s.get("last_message_at") or s.get("started_at"))
        source = s.get("source") or "claude-code"
        msgs = s.get("message_count") or 0
        line = f"{idx}. {s['project_name']} ({ts}) · {source} · {msgs} msgs"
        lines.append(line)
        if s.get("preview"):
            lines.append(f"   {s['preview']}")
        if s.get("chunk_id"):
            lines.append(f"   → cc-anywhere --view {s['chunk_id']}")
        lines.append("")

    if intent.get("topical"):
        lines.insert(
            2,
            f"(Hybrid query — also filter by '{intent['topical']}' "
            f"using `cc-anywhere --semantic-search '{intent['topical']}'`.)",
        )
        lines.insert(3, "")

    return "\n".join(lines).rstrip()


def _summarize_project_last_chat(project_name, sessions):
    """Format the most recent project-local chat for warm-up."""
    if not sessions:
        return ""

    s = sessions[0]
    ts = _local_display(s.get("last_message_at") or s.get("started_at"))
    source = s.get("source") or "claude-code"
    msgs = s.get("message_count") or 0
    lines = [
        f"Last chat in {project_name} ({ts}) · {source} · {msgs} msgs",
    ]
    if s.get("preview"):
        lines.append("")
        lines.append(s["preview"])
    if s.get("chunk_id"):
        lines.append("")
        lines.append(f"→ cc-anywhere --view {s['chunk_id']}")
    return "\n".join(lines).rstrip()


def _summarize_temporal_wide(intent, sessions):
    """Daily rollup format for wider windows (this week, this month, etc.).

    Groups sessions by day, surfaces top-3 projects per day plus 2-3
    highlight sessions, and includes drill-in hints so the user can walk
    back into a specific day with a follow-up `--ask` query.
    """
    from collections import Counter, defaultdict

    label = intent["window_label"]
    by_day = defaultdict(list)
    day_label = {}
    for s in sessions:
        ts = s.get("last_message_at") or s.get("started_at")
        date_key, pretty = _parse_iso_local_date(ts)
        if date_key is None:
            continue
        by_day[date_key].append(s)
        day_label[date_key] = pretty

    if not by_day:
        # Shouldn't happen with valid timestamps, but guard.
        return _summarize_temporal({**intent, "hours_back": 24}, sessions)

    days_sorted = sorted(by_day.keys(), reverse=True)
    total_sessions = sum(len(v) for v in by_day.values())

    lines = [
        f"{label.capitalize()} — {len(days_sorted)} active days, "
        f"{total_sessions} sessions total",
        "",
    ]
    if intent.get("topical"):
        lines.append(
            f"(Hybrid query — also filter by '{intent['topical']}' "
            f"using `cc-anywhere --semantic-search '{intent['topical']}'`.)"
        )
        lines.append("")

    for date_key in days_sorted:
        day_sessions = by_day[date_key]
        # Top projects by session count
        project_counts = Counter(s["project_name"] for s in day_sessions)
        top_projects = [p for p, _ in project_counts.most_common(3)]
        top_str = " · ".join(top_projects) if top_projects else "—"

        lines.append(
            f"**{day_label[date_key]}** ({date_key}) — "
            f"{len(day_sessions)} session{'s' if len(day_sessions) != 1 else ''}"
        )
        lines.append(f"  top: {top_str}")
        lines.extend(_summarize_session_brief(day_sessions, max_per_day=3))
        lines.append(f"  → cc-anywhere --ask \"{day_label[date_key].lower()}\"")
        lines.append("")

    lines.append("Walk-back hints:")
    lines.append(
        '  cc-anywhere --ask "today" / "yesterday" / "last 3 days" / '
        '"this week" / "this month"'
    )

    return "\n".join(lines).rstrip()


def _temporal_db_limit(hours_back):
    """Return a fetch limit sized to the requested time window."""
    if hours_back <= 48:
        return 20      # today / yesterday / last few hours
    if hours_back <= 168:
        return 150     # this week / last week
    return 500         # this month and longer


def read_conversations(query=None, db=None):
    """Read recent conversation history for warm-up / orientation.

    This is the "read, don't search" path: chronological recall over a
    recent time window. It is meant for warm-up, catch-up, and general
    orientation, not topical lookup.

    Args:
        query: Optional temporal phrase like "today", "yesterday",
            "this week", or "catch me up". If omitted, defaults to a
            recent 4-hour window.
        db: Optional SQLite connection for testing.

    Returns:
        Dict with `answer`, `results`, and `intent`, matching the
        temporal branch of ask_conversations().
    """
    query = (query or "").strip()
    if not query:
        cwd = os.getcwd()
        project_name = os.path.basename(cwd.rstrip(os.sep)) or cwd
        sessions = latest_project_sessions(
            project_path=cwd,
            project_name=project_name,
            limit=1,
            db=db,
        )
        if sessions:
            return {
                "answer": _summarize_project_last_chat(project_name, sessions),
                "results": sessions,
                "intent": {
                    "kind": "project_last_chat",
                    "project_name": project_name,
                    "project_path": cwd,
                },
            }
        query = "recently"

    intent = detect_temporal_intent(query)
    if intent is None:
        raise ValueError(
            "--read is for recent/time-based recall. "
            "Use --ask for topic lookup."
        )

    hb = intent["hours_back"]
    sessions = recent_sessions(hours_back=hb, limit=_temporal_db_limit(hb), db=db)
    return {
        "answer": _summarize_temporal(intent, sessions),
        "results": sessions,
        "intent": intent,
    }


def ask_conversations(query, limit=5, db=None):
    """Route an `--ask` query to the right retrieval strategy.

    Two intents:
      - TEMPORAL: question is about RECENT activity in a time window
        ("today", "yesterday", "this week", "what was I just working on").
        Skip semantic search — slice the DB by time and summarize
        chronologically.
      - TOPICAL: question names a subject without a time qualifier.
        Run semantic search as before.
      - HYBRID (temporal + topical): for now we route to TEMPORAL with a
        suggestion to also try `--semantic-search` for the topical part.
        A future revision can rank within the time-windowed slice.

    Each line in the output ends with the chunk_id so the user (or another
    agent) can drill into the full content via `view_chunk(chunk_id)` or
    the CLI's `--view <chunk_id>` command.
    """
    intent = detect_temporal_intent(query)
    if intent is not None:
        return read_conversations(query, db=db)

    # TOPICAL — existing semantic-search path.
    results = semantic_search(query, limit=limit, db=db)
    if not results:
        return {"answer": "No matching conversations found.", "results": []}

    fallback_used = results[0].get("fallback_used")
    scope = results[0].get("scope")
    lines = [
        (
            "I found these likely relevant coding conversations "
            f"({scope.replace('-', ' ') if scope else 'all time'}):"
        ),
        "",
    ]
    if fallback_used:
        days = results[0].get("searched_recent_days")
        lines.extend([
            f"No matches in the last {days} days; expanded to all time.",
            "",
        ])
    for idx, result in enumerate(results, 1):
        ts = _local_display(result.get("started_at"))
        lines.append(
            f"{idx}. {result['project_name']} ({ts}, score {result['score']:.2f})"
        )
        lines.append(f"   {result['excerpt']}")
        lines.append(f"   → cc-anywhere --view {result['chunk_id']}")
        lines.append("")

    return {"answer": "\n".join(lines).rstrip(), "results": results}


def view_chunk(chunk_id, db=None):
    """Return the full content + metadata of a captured chunk.

    Looks up by exact chunk_id, with prefix-match fallback so users can
    paste a partial id (e.g. the first 12 chars of the session UUID).
    Returns None if no match. If a prefix matches multiple chunks, the
    one most recently indexed (highest rowid) is returned.

    The returned dict includes the *full* content (no truncation) plus
    project, source, timestamps, message_count, and the underlying
    session row's metadata so the caller has everything needed to
    present a clean drill-down view.
    """
    own_db = db is None
    if own_db:
        db = get_db()
    ensure_semantic_schema(db)

    # Try exact match first.
    row = db.execute(
        """
        SELECT c.chunk_id, c.session_id, c.project_name, c.project_path,
               c.started_at, c.ended_at, c.content, c.message_count,
               c.first_message_uuid, c.last_message_uuid,
               c.source_path, c.source_start_line, c.source_end_line,
               c.source_byte_start, c.source_byte_end,
               s.source, s.session_label,
               s.client_originator, s.client_source, s.client_version
        FROM semantic_chunks c
        LEFT JOIN sessions s ON c.session_id = s.session_id
        WHERE c.chunk_id = ?
        """,
        (chunk_id,),
    ).fetchone()

    # Fall back to prefix match.
    if row is None:
        row = db.execute(
            """
            SELECT c.chunk_id, c.session_id, c.project_name, c.project_path,
                   c.started_at, c.ended_at, c.content, c.message_count,
                   c.first_message_uuid, c.last_message_uuid,
                   c.source_path, c.source_start_line, c.source_end_line,
                   c.source_byte_start, c.source_byte_end,
                   s.source, s.session_label,
                   s.client_originator, s.client_source, s.client_version
            FROM semantic_chunks c
            LEFT JOIN sessions s ON c.session_id = s.session_id
            WHERE c.chunk_id LIKE ?
            ORDER BY c.rowid DESC
            LIMIT 1
            """,
            (chunk_id + "%",),
        ).fetchone()

    if own_db:
        db.close()

    if row is None:
        return None
    return dict(row)


def view_source(chunk_id, context_lines=4, db=None):
    """Return raw transcript provenance and nearby JSONL lines for a chunk.

    This is the source-backed recall path: agents can use it to move from a
    search hit to the raw transcript file without opening SQLite directly.
    If the chunk was indexed before source provenance existed, this returns
    metadata with a helpful `error` instead of raising.
    """
    chunk = view_chunk(chunk_id, db=db)
    if chunk is None:
        return None

    source_path = chunk.get("source_path")
    start_line = chunk.get("source_start_line")
    end_line = chunk.get("source_end_line")
    result = dict(chunk)
    result["raw_lines"] = []

    if not source_path:
        result["error"] = "No source transcript path is stored for this chunk."
        return result

    path = Path(source_path).expanduser()
    if not path.exists():
        result["error"] = f"Source transcript is not present on this machine: {source_path}"
        return result

    if not start_line or not end_line:
        result["error"] = "No source line range is stored for this chunk."
        return result

    first = max(1, int(start_line) - context_lines)
    last = int(end_line) + context_lines
    try:
        with open(path, "r", encoding="utf-8") as f:
            for number, line in enumerate(f, 1):
                if number < first:
                    continue
                if number > last:
                    break
                result["raw_lines"].append((number, line.rstrip("\n")))
    except OSError as exc:
        result["error"] = f"Failed to read source transcript: {exc}"

    return result
