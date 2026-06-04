#!/usr/bin/env python3
"""Tests for local semantic search."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cc_anywhere.semantic import (
    ask_conversations,
    make_excerpt,
    read_conversations,
    rebuild_semantic_index,
    semantic_search,
    view_chunk,
    view_source,
)
from cc_anywhere.sqlite_capture import get_db


def seed_conversation(db):
    db.execute(
        """
        INSERT INTO sessions (
            session_id, project_path, project_name,
            started_at, last_message_at, machine_name
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "sess-1",
            "/Users/test/cc-anywhere",
            "cc-anywhere",
            "2026-04-01T10:00:00",
            "2026-04-01T10:03:00",
            "test-machine",
        ),
    )
    messages = [
        (
            "m1",
            "sess-1",
            "user",
            "Can we make it easier to continue work from another machine?",
            "2026-04-01T10:00:00",
        ),
        (
            "m2",
            "sess-1",
            "assistant",
            "Yes. We can add pickup prompts and sync recent context between devices.",
            "2026-04-01T10:01:00",
        ),
        (
            "m3",
            "sess-1",
            "user",
            "Also capture conversations into SQLite so they are searchable later.",
            "2026-04-01T10:02:00",
        ),
    ]
    for uuid, session_id, role, content, timestamp in messages:
        db.execute(
            """
            INSERT INTO messages (uuid, session_id, role, content, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uuid, session_id, role, content, timestamp),
        )
    db.commit()


def test_rebuild_semantic_index_creates_chunks(tmp_path):
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)

    stats = rebuild_semantic_index(db=db)

    assert stats["sessions"] == 1
    assert stats["messages"] == 3
    assert stats["chunks"] >= 1

    rows = db.execute("SELECT * FROM semantic_chunks").fetchall()
    assert len(rows) >= 1
    assert "continue work" in rows[0]["content"]
    db.close()


def test_semantic_search_finds_related_handoff_query(tmp_path):
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)
    rebuild_semantic_index(db=db)

    results = semantic_search("laptop handoff workflow", db=db)

    assert results
    assert results[0]["project_name"] == "cc-anywhere"
    assert "machine" in results[0]["content"].lower()
    db.close()


def test_ask_conversations_returns_summary(tmp_path):
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)
    rebuild_semantic_index(db=db)

    result = ask_conversations("sync conversations between computers", db=db)

    assert "cc-anywhere" in result["answer"]
    assert result["results"]
    db.close()


def test_read_conversations_defaults_to_recent_orientation(tmp_path):
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)
    db.execute(
        "UPDATE sessions SET project_path = ?, project_name = ? WHERE session_id = ?",
        (str(tmp_path), tmp_path.name, "sess-1"),
    )
    now = datetime.now(timezone.utc)
    started_at = (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_message_at = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        "UPDATE sessions SET started_at = ?, last_message_at = ? WHERE session_id = ?",
        (started_at, last_message_at, "sess-1"),
    )
    db.execute(
        "UPDATE messages SET timestamp = ? WHERE session_id = ?",
        (last_message_at, "sess-1"),
    )
    db.commit()
    rebuild_semantic_index(db=db)
    old_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        result = read_conversations(db=db)
    finally:
        os.chdir(old_cwd)

    assert result["intent"]["kind"] == "project_last_chat"
    assert result["intent"]["project_name"] == tmp_path.name
    assert f"Last chat in {tmp_path.name}" in result["answer"]
    assert result["results"]
    db.close()


def test_read_conversations_falls_back_to_recent_when_project_has_no_history(tmp_path):
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)
    now = datetime.now(timezone.utc)
    started_at = (now - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_message_at = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        "UPDATE sessions SET started_at = ?, last_message_at = ? WHERE session_id = ?",
        (started_at, last_message_at, "sess-1"),
    )
    db.execute(
        "UPDATE messages SET timestamp = ? WHERE session_id = ?",
        (last_message_at, "sess-1"),
    )
    db.commit()
    rebuild_semantic_index(db=db)

    other_dir = tmp_path / "other-project"
    other_dir.mkdir()
    old_cwd = Path.cwd()
    try:
        os.chdir(other_dir)
        result = read_conversations(db=db)
    finally:
        os.chdir(old_cwd)

    assert result["intent"]["kind"] == "temporal"
    assert result["intent"]["window_label"] == "recent"
    assert "Sessions from recent" in result["answer"]
    assert result["results"]
    db.close()


def test_read_conversations_rejects_topical_query(tmp_path):
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)

    try:
        read_conversations("auth decisions", db=db)
        assert False, "expected ValueError for topical query"
    except ValueError as e:
        assert "--read is for recent/time-based recall" in str(e)
    finally:
        db.close()


def test_excerpt_centers_on_dense_match_cluster():
    content = (
        "Opening context that is not relevant. " * 8
        + "The source backed recall feature stores raw transcript provenance "
        + "with line and byte ranges so agents can open the source transcript. "
        + "Trailing context that is not relevant. " * 8
    )

    excerpt = make_excerpt(content, "raw transcript provenance source", size=160)

    assert excerpt.startswith("...")
    assert "raw transcript provenance" in excerpt
    assert "source transcript" in excerpt
    assert len(excerpt) <= 166  # requested size plus ellipses


def test_semantic_search_orders_by_relevance_with_fts_and_overlap(tmp_path):
    db = get_db(tmp_path / "test.db")
    db.execute(
        """
        INSERT INTO sessions (
            session_id, project_path, project_name,
            started_at, last_message_at, machine_name
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "rank-sess",
            "/tmp/rank",
            "rank",
            "2026-04-01T10:00:00Z",
            "2026-04-01T10:03:00Z",
            "test",
        ),
    )
    for uuid, content, ts in [
        (
            "rank-weak",
            "We talked about source files in a general way.",
            "2026-04-01T10:00:00Z",
        ),
        (
            "rank-strong",
            "Source backed recall stores raw transcript provenance with line ranges.",
            "2026-04-01T10:01:00Z",
        ),
    ]:
        db.execute(
            "INSERT INTO messages (uuid, session_id, role, content, timestamp) "
            "VALUES (?, ?, 'assistant', ?, ?)",
            (uuid, "rank-sess", content, ts),
        )
    db.commit()
    rebuild_semantic_index(db=db, max_messages=1)

    results = semantic_search("raw transcript provenance", db=db)

    assert results
    assert "raw transcript provenance" in results[0]["content"]
    assert results[0]["score"] >= results[-1]["score"]
    db.close()


def test_semantic_search_modes_isolate_scoring(tmp_path):
    """Hybrid fuses cosine + bm25 + overlap; semantic uses cosine only.

    Same query against the same chunk should produce a strictly higher
    score in hybrid mode when the keyword matches, and the result's
    fts_score / overlap_score should be zero under mode='semantic'.
    """
    db = get_db(tmp_path / "test.db")
    db.execute(
        """
        INSERT INTO sessions (
            session_id, project_path, project_name,
            started_at, last_message_at, machine_name
        )
        VALUES ('mode-sess', '/tmp/mode', 'mode',
                '2026-04-01T10:00:00Z', '2026-04-01T10:01:00Z', 'test')
        """,
    )
    db.execute(
        "INSERT INTO messages (uuid, session_id, role, content, timestamp) "
        "VALUES ('mode-msg', 'mode-sess', 'assistant', "
        "'Source backed recall stores raw transcript provenance with line ranges.', "
        "'2026-04-01T10:00:00Z')",
    )
    db.commit()
    rebuild_semantic_index(db=db, max_messages=1)

    hybrid = semantic_search("raw transcript provenance", db=db, mode="hybrid")
    semantic = semantic_search("raw transcript provenance", db=db, mode="semantic")

    assert hybrid and semantic, "both modes should return results"
    h, s = hybrid[0], semantic[0]
    # Same chunk, but hybrid scores it higher because of the keyword fusion.
    assert h["chunk_id"] == s["chunk_id"]
    assert h["score"] > s["score"]
    # In semantic mode, no keyword fusion is applied.
    assert s["fts_score"] == 0.0
    assert s["overlap_score"] == 0.0
    # In hybrid mode, keyword fusion fires for an exact-phrase match.
    assert h["fts_score"] > 0 or h["overlap_score"] > 0
    # Vector score is identical between modes (same chunk, same query).
    assert abs(h["vector_score"] - s["vector_score"]) < 1e-9
    db.close()


def test_semantic_search_prefers_last_30_days_then_falls_back(tmp_path):
    db = get_db(tmp_path / "test.db")
    now = datetime.now(timezone.utc)
    recent_ts = (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    old_ts = (now - timedelta(days=70)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    for session_id, project_name, ts, content in [
        ("recent-sess", "recent", recent_ts,
         "Recent source backed recall note about transcript provenance."),
        ("old-sess", "old", old_ts,
         "Old archive note about antique migration strategy."),
    ]:
        db.execute(
            """
            INSERT INTO sessions (
                session_id, project_path, project_name,
                started_at, last_message_at, machine_name
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (session_id, f"/tmp/{project_name}", project_name, ts, ts, "test"),
        )
        db.execute(
            "INSERT INTO messages (uuid, session_id, role, content, timestamp) "
            "VALUES (?, ?, 'assistant', ?, ?)",
            (f"{session_id}-m1", session_id, content, ts),
        )
    db.commit()
    rebuild_semantic_index(db=db, max_messages=1)

    recent_results = semantic_search("transcript provenance", db=db)
    assert recent_results
    assert recent_results[0]["project_name"] == "recent"
    assert recent_results[0]["scope"] == "last-30-days"
    assert recent_results[0]["fallback_used"] is False

    fallback_results = semantic_search("antique migration", db=db)
    assert fallback_results
    assert fallback_results[0]["project_name"] == "old"
    assert fallback_results[0]["scope"] == "all-time"
    assert fallback_results[0]["fallback_used"] is True
    db.close()


def test_incremental_index_skips_session_when_up_to_date(tmp_path):
    """A second --index-semantic run on an unchanged corpus should
    do no chunking work and should skip the session."""
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)

    first = rebuild_semantic_index(db=db)
    assert first["chunks"] >= 1
    assert first["skipped_sessions"] == 0

    chunks_before = db.execute("SELECT COUNT(*) AS n FROM semantic_chunks").fetchone()["n"]

    second = rebuild_semantic_index(db=db)
    assert second["chunks"] == 0
    assert second["messages"] == 0
    assert second["skipped_sessions"] == 1

    chunks_after = db.execute("SELECT COUNT(*) AS n FROM semantic_chunks").fetchone()["n"]
    assert chunks_after == chunks_before, "incremental run must not delete existing chunks"
    db.close()


def test_incremental_index_appends_new_messages(tmp_path):
    """When new messages arrive in an already-indexed session, the next
    --index-semantic run must append new chunks for the new content
    without touching previously-indexed chunks."""
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)
    rebuild_semantic_index(db=db)

    initial_chunks = db.execute("SELECT chunk_id FROM semantic_chunks").fetchall()
    initial_ids = {row["chunk_id"] for row in initial_chunks}
    assert len(initial_ids) >= 1

    # Append two new messages well past the previously-chunked timestamps.
    db.execute(
        """INSERT INTO messages (uuid, session_id, role, content, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        ("m4", "sess-1", "user",
         "Now we want a way to query this from any machine via MCP.",
         "2026-04-02T09:00:00"),
    )
    db.execute(
        """INSERT INTO messages (uuid, session_id, role, content, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        ("m5", "sess-1", "assistant",
         "Sure — a thin MCP wrapper around the local API works for that.",
         "2026-04-02T09:01:00"),
    )
    db.execute(
        "UPDATE sessions SET last_message_at = ? WHERE session_id = ?",
        ("2026-04-02T09:01:00", "sess-1"),
    )
    db.commit()

    second = rebuild_semantic_index(db=db)
    assert second["chunks"] >= 1, "new messages should have produced new chunks"
    assert second["messages"] >= 2

    # Original chunks must all still be present and unchanged.
    final_ids = {
        row["chunk_id"]
        for row in db.execute("SELECT chunk_id FROM semantic_chunks").fetchall()
    }
    assert initial_ids.issubset(final_ids), "original chunks must not be deleted"
    assert len(final_ids) > len(initial_ids), "new chunks must have been appended"

    # Search now returns the new content.
    results = semantic_search("MCP wrapper", db=db)
    assert any("MCP" in r["content"] for r in results)
    db.close()


def test_incremental_index_works_for_codex_sessions(tmp_path):
    """The incremental path is source-agnostic. Codex sessions captured
    via capture_codex_sessions() land in the same sessions/messages
    tables, and rebuild_semantic_index() must treat them identically:
    skip when current, append when new messages arrive."""
    db = get_db(tmp_path / "test.db")

    # Seed a Codex-tagged session.
    db.execute(
        """
        INSERT INTO sessions (
            session_id, project_path, project_name,
            started_at, last_message_at, machine_name,
            source, session_label
        )
        VALUES (?, ?, ?, ?, ?, ?, 'codex', ?)
        """,
        (
            "codex-sess-1",
            "/Users/test/Projects/myapp",
            "Review bio-target project",
            "2026-04-27T09:19:54",
            "2026-04-27T09:21:00",
            "test-machine",
            "Review bio-target project",
        ),
    )
    for uuid, role, content, ts in [
        ("c1", "user", "How do I shard the corpus across workers?",
         "2026-04-27T09:20:00"),
        ("c2", "assistant", "Hash the PMID modulo the worker count.",
         "2026-04-27T09:21:00"),
    ]:
        db.execute(
            """INSERT INTO messages (uuid, session_id, role, content, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (uuid, "codex-sess-1", role, content, ts),
        )
    db.commit()

    first = rebuild_semantic_index(db=db)
    assert first["chunks"] >= 1
    assert first["skipped_sessions"] == 0

    # Re-run on unchanged Codex corpus — must skip, must not delete.
    chunks_before = db.execute(
        "SELECT COUNT(*) AS n FROM semantic_chunks"
    ).fetchone()["n"]
    second = rebuild_semantic_index(db=db)
    assert second["chunks"] == 0
    assert second["skipped_sessions"] == 1
    chunks_after = db.execute(
        "SELECT COUNT(*) AS n FROM semantic_chunks"
    ).fetchone()["n"]
    assert chunks_after == chunks_before

    # Append a new Codex turn (simulating a fresh rollout entry).
    db.execute(
        """INSERT INTO messages (uuid, session_id, role, content, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        ("c3", "codex-sess-1", "user",
         "Now let's add a step that emits per-worker logs.",
         "2026-04-27T11:00:00"),
    )
    db.execute(
        """INSERT INTO messages (uuid, session_id, role, content, timestamp)
           VALUES (?, ?, ?, ?, ?)""",
        ("c4", "codex-sess-1", "assistant",
         "Sure — write each worker's stdout to its own logfile.",
         "2026-04-27T11:00:30"),
    )
    db.execute(
        "UPDATE sessions SET last_message_at = ? WHERE session_id = ?",
        ("2026-04-27T11:00:30", "codex-sess-1"),
    )
    db.commit()

    third = rebuild_semantic_index(db=db)
    assert third["chunks"] >= 1, "new Codex messages should produce new chunks"
    assert third["messages"] >= 2

    # The Codex session label is preserved as project_name on chunks too.
    codex_chunks = db.execute(
        """SELECT c.project_name, c.content FROM semantic_chunks c
           JOIN sessions s ON c.session_id = s.session_id
           WHERE s.source = 'codex'"""
    ).fetchall()
    assert codex_chunks
    assert any(r["project_name"] == "Review bio-target project" for r in codex_chunks)
    db.close()


class TestViewChunk:
    """The drill-down read path. Search returns previews; view_chunk returns
    the complete content + session metadata for one specific chunk."""

    def test_view_returns_full_content(self, tmp_path):
        db = get_db(tmp_path / "test.db")
        seed_conversation(db)
        rebuild_semantic_index(db=db)

        chunks = db.execute("SELECT chunk_id, content FROM semantic_chunks").fetchall()
        assert chunks, "expected at least one chunk after seeding"
        target = chunks[0]

        result = view_chunk(target["chunk_id"], db=db)
        assert result is not None
        # Full content is returned untruncated.
        assert result["content"] == target["content"]
        # Metadata fields are populated.
        assert result["session_id"] == "sess-1"
        assert result["project_name"] == "cc-anywhere"
        assert "message_count" in result
        db.close()

    def test_view_supports_prefix_match(self, tmp_path):
        db = get_db(tmp_path / "test.db")
        seed_conversation(db)
        rebuild_semantic_index(db=db)

        full_id = db.execute(
            "SELECT chunk_id FROM semantic_chunks LIMIT 1"
        ).fetchone()["chunk_id"]
        prefix = full_id[:12]

        result = view_chunk(prefix, db=db)
        assert result is not None, "prefix lookup should match"
        assert result["chunk_id"] == full_id
        db.close()

    def test_view_returns_none_when_no_match(self, tmp_path):
        db = get_db(tmp_path / "test.db")
        seed_conversation(db)
        rebuild_semantic_index(db=db)

        assert view_chunk("does-not-exist", db=db) is None
        assert view_chunk("zzz999", db=db) is None
        db.close()

    def test_view_includes_session_source_metadata(self, tmp_path):
        """When the parent session carries client metadata (originator,
        source, version), view_chunk surfaces it so the drill-down view
        can show "from Codex Desktop v0.125.0-alpha.3" rather than just
        a project name."""
        db = get_db(tmp_path / "test.db")
        # Seed a session with codex client metadata
        db.execute(
            """INSERT INTO sessions (
                session_id, project_path, project_name, started_at,
                last_message_at, machine_name, source, session_label,
                client_originator, client_source, client_version
            ) VALUES (?, ?, ?, ?, ?, ?, 'codex', ?, 'Codex Desktop', 'vscode', '0.125.0')""",
            ("sess-codex", "/tmp/test", "test-project",
             "2026-04-27T09:00:00.000Z", "2026-04-27T09:01:00.000Z",
             "test-machine", "Codex Desktop session"),
        )
        db.execute(
            """INSERT INTO messages (uuid, session_id, role, content, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            ("mc1", "sess-codex", "user",
             "Test content for the codex desktop chunk.",
             "2026-04-27T09:00:30.000Z"),
        )
        db.commit()
        rebuild_semantic_index(db=db)

        cid = db.execute(
            "SELECT chunk_id FROM semantic_chunks WHERE session_id = 'sess-codex'"
        ).fetchone()["chunk_id"]

        result = view_chunk(cid, db=db)
        assert result is not None
        assert result["source"] == "codex"
        assert result["client_originator"] == "Codex Desktop"
        assert result["client_source"] == "vscode"
        assert result["client_version"] == "0.125.0"
        assert result["session_label"] == "Codex Desktop session"
        db.close()

    def test_view_source_returns_raw_transcript_lines(self, tmp_path):
        db = get_db(tmp_path / "test.db")
        transcript = tmp_path / "session.jsonl"
        lines = [
            '{"type":"user","message":{"content":"first"}}',
            '{"type":"assistant","message":{"content":[{"type":"text","text":"second"}]}}',
            '{"type":"user","message":{"content":"source backed recall"}}',
        ]
        transcript.write_text("\n".join(lines) + "\n")
        db.execute(
            """
            INSERT INTO sessions (
                session_id, project_path, project_name,
                started_at, last_message_at, machine_name
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "source-sess",
                "/tmp/source-project",
                "source-project",
                "2026-04-01T10:00:00Z",
                "2026-04-01T10:03:00Z",
                "test-machine",
            ),
        )
        for uuid, role, content, line in [
            ("s1", "user", "first", 1),
            ("s2", "assistant", "second", 2),
            ("s3", "user", "source backed recall", 3),
        ]:
            db.execute(
                """
                INSERT INTO messages (
                    uuid, session_id, role, content, timestamp,
                    source_path, source_line, source_byte_start, source_byte_end
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid,
                    "source-sess",
                    role,
                    content,
                    f"2026-04-01T10:0{line}:00Z",
                    str(transcript),
                    line,
                    0,
                    10,
                ),
            )
        db.commit()
        rebuild_semantic_index(db=db)

        cid = db.execute(
            "SELECT chunk_id FROM semantic_chunks WHERE session_id = 'source-sess'"
        ).fetchone()["chunk_id"]
        chunk = view_chunk(cid, db=db)
        assert chunk["source_path"] == str(transcript)
        assert chunk["source_start_line"] == 1
        assert chunk["source_end_line"] == 3

        source = view_source(cid, context_lines=0, db=db)
        assert source is not None
        assert source["source_path"] == str(transcript)
        assert source["raw_lines"] == list(enumerate(lines, 1))
        db.close()


def test_ask_output_includes_drill_down_hint(tmp_path):
    """Each ask result should include a copy-pasteable
    `cc-anywhere --view <chunk_id>` line."""
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)
    rebuild_semantic_index(db=db)

    result = ask_conversations("decorator pickup sync", db=db)
    assert "--view " in result["answer"]


def test_full_rebuild_wipes_and_reindexes(tmp_path):
    """full_rebuild=True should wipe and rebuild — useful as an opt-in
    recovery path."""
    db = get_db(tmp_path / "test.db")
    seed_conversation(db)

    rebuild_semantic_index(db=db)
    initial_chunks = db.execute("SELECT chunk_id FROM semantic_chunks").fetchall()
    initial_ids = {row["chunk_id"] for row in initial_chunks}
    assert initial_ids

    stats = rebuild_semantic_index(db=db, full_rebuild=True)
    assert stats["chunks"] >= 1
    assert stats["skipped_sessions"] == 0  # never skip in full rebuild

    # All chunks should still be present (re-created with same chunk_ids
    # since chunk_id is content-deterministic).
    rebuilt_ids = {
        row["chunk_id"]
        for row in db.execute("SELECT chunk_id FROM semantic_chunks").fetchall()
    }
    assert rebuilt_ids == initial_ids
    db.close()
