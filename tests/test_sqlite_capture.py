#!/usr/bin/env python3
"""Tests for sqlite_capture module."""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from cc_anywhere.sqlite_capture import (
    _extract_assistant_text,
    _extract_user_content,
    backfill_source_provenance,
    capture_sessions,
    db_search,
    export_for_sync,
    get_capture_stats,
    get_db,
    get_session_messages,
    import_from_sync,
)


@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary database."""
    db_path = tmp_path / "test.db"
    db = get_db(db_path)
    yield db
    db.close()


@pytest.fixture
def claude_dir(tmp_path):
    """Create a fake ~/.claude/ directory with test JSONL data."""
    cdir = tmp_path / ".claude"
    proj_dir = cdir / "projects" / "-Users-test-myproject"
    proj_dir.mkdir(parents=True)

    # Write a conversation JSONL file
    conv_file = proj_dir / "abc123.jsonl"
    entries = [
        {
            "type": "user",
            "message": {"content": "How do I write a Python decorator?"},
            "timestamp": 1700000000000,
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "A decorator is a function that wraps another function."},
                    {"type": "text", "text": "Here's a simple example..."},
                ]
            },
            "timestamp": 1700000010000,
        },
        {
            "type": "user",
            "message": {"content": "Can you show me with arguments?"},
            "timestamp": 1700000020000,
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "tool1", "name": "read_file", "input": {}},
                ]
            },
            "timestamp": 1700000030000,
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool1", "content": "file contents"}
                ]
            },
            "timestamp": 1700000040000,
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Here's the decorator with arguments."},
                ]
            },
            "timestamp": 1700000050000,
        },
    ]

    with open(conv_file, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    return cdir


@pytest.fixture
def cowork_dir(tmp_path):
    """Create a fake Claude Cowork root with Claude-style JSONL data."""
    root = (
        tmp_path
        / "Library"
        / "Application Support"
        / "Claude"
        / "local-agent-mode-sessions"
        / "workspace-1"
        / "run-1"
        / "local_123"
        / ".claude"
        / "projects"
        / "-sessions-dreamy-happy-thompson"
    )
    root.mkdir(parents=True)

    conv_file = root / "cowork-abc.jsonl"
    entries = [
        {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-04-22T14:42:29.665Z",
            "sessionId": "cowork-abc",
            "content": "where is claude design",
        },
        {
            "type": "user",
            "cwd": "/Users/test/Projects/cc-anywhere",
            "sessionId": "cowork-abc",
            "message": {"role": "user", "content": "where is claude design"},
            "timestamp": "2026-04-22T14:42:29.800Z",
        },
        {
            "type": "assistant",
            "cwd": "/Users/test/Projects/cc-anywhere",
            "sessionId": "cowork-abc",
            "message": {
                "content": [
                    {"type": "text", "text": "The design docs live in the repo root."},
                ]
            },
            "timestamp": "2026-04-22T14:42:30.000Z",
        },
    ]

    with open(conv_file, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    return tmp_path / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"


# ============ Extraction helpers ============


class TestExtractUserContent:
    def test_string_content(self):
        entry = {"type": "user", "message": {"content": "Hello world"}}
        assert _extract_user_content(entry) == "Hello world"

    def test_empty_string(self):
        entry = {"type": "user", "message": {"content": ""}}
        assert _extract_user_content(entry) is None

    def test_whitespace_only(self):
        entry = {"type": "user", "message": {"content": "   "}}
        assert _extract_user_content(entry) is None

    def test_tool_result_skipped(self):
        entry = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "tool_result", "tool_use_id": "x", "content": "stuff"}
                ]
            },
        }
        assert _extract_user_content(entry) is None

    def test_text_array(self):
        entry = {
            "type": "user",
            "message": {
                "content": [
                    {"type": "text", "text": "Part one"},
                    {"type": "text", "text": "Part two"},
                ]
            },
        }
        assert _extract_user_content(entry) == "Part one Part two"

    def test_non_user_type(self):
        entry = {"type": "assistant", "message": {"content": "text"}}
        assert _extract_user_content(entry) is None

    def test_missing_message(self):
        entry = {"type": "user"}
        assert _extract_user_content(entry) is None


class TestExtractAssistantText:
    def test_text_items(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "First paragraph."},
                    {"type": "text", "text": "Second paragraph."},
                ]
            },
        }
        assert _extract_assistant_text(entry) == "First paragraph.\n\nSecond paragraph."

    def test_tool_use_skipped(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                ]
            },
        }
        assert _extract_assistant_text(entry) is None

    def test_mixed_content(self):
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {"type": "tool_use", "id": "t1", "name": "bash", "input": {}},
                    {"type": "text", "text": "Done!"},
                ]
            },
        }
        result = _extract_assistant_text(entry)
        assert "Let me check." in result
        assert "Done!" in result

    def test_non_assistant_type(self):
        entry = {"type": "user", "message": {"content": []}}
        assert _extract_assistant_text(entry) is None

    def test_empty_content(self):
        entry = {"type": "assistant", "message": {"content": []}}
        assert _extract_assistant_text(entry) is None


# ============ Database ============


class TestGetDb:
    def test_creates_tables(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = get_db(db_path)

        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in tables}

        assert "sessions" in table_names
        assert "messages" in table_names
        assert "capture_state" in table_names
        assert "messages_fts" in table_names

        db.close()

    def test_wal_mode(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = get_db(db_path)

        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

        db.close()

    def test_idempotent(self, tmp_path):
        db_path = tmp_path / "test.db"
        db1 = get_db(db_path)
        db1.close()

        db2 = get_db(db_path)
        tables = db2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        assert len(tables) >= 4
        db2.close()


# ============ Capture ============


class TestCaptureSessionsBasic:
    def test_capture_populates_db(self, tmp_db, claude_dir):
        result = capture_sessions(db=tmp_db, claude_dir=claude_dir)

        assert result["new_sessions"] >= 1
        assert result["new_messages"] >= 1
        assert result["projects_scanned"] >= 1

    def test_capture_stores_user_messages(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        rows = tmp_db.execute(
            "SELECT content FROM messages WHERE role = 'user'"
        ).fetchall()
        contents = [r["content"] for r in rows]

        assert any("decorator" in c for c in contents)
        # tool_result should NOT appear
        assert not any("tool_result" in c for c in contents)

    def test_capture_marks_compact_summary(self, tmp_db, claude_dir):
        proj_dir = claude_dir / "projects" / "-Users-test-myproject"
        conv_file = proj_dir / "summary-session.jsonl"
        summary_text = "This session is being continued from a previous conversation."
        conv_file.write_text(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": summary_text},
            "isVisibleInTranscriptOnly": True,
            "isCompactSummary": True,
            "timestamp": "2026-04-29T15:37:59.559Z",
        }) + "\n")

        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        row = tmp_db.execute(
            "SELECT role, content, message_type, is_compact_summary, "
            "is_visible_in_transcript_only FROM messages WHERE content = ?",
            (summary_text,),
        ).fetchone()

        assert row["role"] == "user"
        assert row["message_type"] == "user"
        assert row["is_compact_summary"] == 1
        assert row["is_visible_in_transcript_only"] == 1

    def test_capture_stores_assistant_text(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        rows = tmp_db.execute(
            "SELECT content FROM messages WHERE role = 'assistant'"
        ).fetchall()
        contents = [r["content"] for r in rows]

        assert any("wraps another function" in c for c in contents)

    def test_capture_stores_source_provenance(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        row = tmp_db.execute(
            "SELECT source_path, source_line, source_byte_start, source_byte_end "
            "FROM messages WHERE content LIKE '%decorator%' ORDER BY source_line LIMIT 1"
        ).fetchone()

        assert row["source_path"].endswith("abc123.jsonl")
        assert row["source_line"] == 1
        assert row["source_byte_start"] == 0
        assert row["source_byte_end"] > row["source_byte_start"]

    def test_backfill_source_provenance_for_existing_rows(self, tmp_db, claude_dir):
        tmp_db.execute(
            """
            INSERT INTO sessions (
                session_id, project_path, project_name,
                started_at, last_message_at, machine_name
            )
            VALUES ('abc123', '/Users/test/myproject', 'myproject',
                    '2023-11-14T22:13:20Z', '2023-11-14T22:13:20Z', 'test')
            """
        )
        tmp_db.execute(
            """
            INSERT INTO messages (uuid, session_id, role, content, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "legacy-message",
                "abc123",
                "user",
                "How do I write a Python decorator?",
                "2023-11-14T22:13:20.000Z",
            ),
        )
        tmp_db.commit()

        stats = backfill_source_provenance(db=tmp_db, claude_dir=claude_dir)
        assert stats["messages_updated"] == 1

        row = tmp_db.execute(
            "SELECT source_path, source_line FROM messages WHERE uuid = ?",
            ("legacy-message",),
        ).fetchone()
        assert row["source_path"].endswith("abc123.jsonl")
        assert row["source_line"] == 1

    def test_capture_skips_tool_result_user_messages(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        user_rows = tmp_db.execute(
            "SELECT content FROM messages WHERE role = 'user'"
        ).fetchall()

        for row in user_rows:
            assert "file contents" not in row["content"]

    def test_incremental_capture(self, tmp_db, claude_dir):
        # First capture
        r1 = capture_sessions(db=tmp_db, claude_dir=claude_dir)

        # Second capture with no changes
        r2 = capture_sessions(db=tmp_db, claude_dir=claude_dir)

        assert r2["new_messages"] == 0
        assert r2["new_sessions"] == 0

    def test_incremental_with_new_data(self, tmp_db, claude_dir):
        # First capture
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        # Append a new message to the JSONL file
        proj_dir = claude_dir / "projects" / "-Users-test-myproject"
        conv_file = proj_dir / "abc123.jsonl"
        new_entry = {
            "type": "user",
            "message": {"content": "What about async decorators?"},
            "timestamp": 1700000060000,
        }
        with open(conv_file, "a") as f:
            f.write(json.dumps(new_entry) + "\n")

        # Second capture picks up only the new message
        r2 = capture_sessions(db=tmp_db, claude_dir=claude_dir)
        assert r2["new_messages"] == 1

    def test_capture_creates_session(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        sessions = tmp_db.execute("SELECT * FROM sessions").fetchall()
        assert len(sessions) >= 1
        s = sessions[0]
        assert s["session_id"] == "abc123"

    def test_empty_claude_dir(self, tmp_db, tmp_path):
        empty_dir = tmp_path / "empty_claude"
        empty_dir.mkdir()
        result = capture_sessions(db=tmp_db, claude_dir=empty_dir)
        assert result["new_messages"] == 0

    def test_capture_includes_claude_cowork_sessions(self, tmp_db, tmp_path, cowork_dir):
        empty_claude = tmp_path / ".claude"
        empty_claude.mkdir()

        result = capture_sessions(
            db=tmp_db,
            claude_dir=empty_claude,
            cowork_dir=cowork_dir,
        )

        assert result["new_sessions"] == 1
        assert result["new_messages"] == 2
        assert result["projects_scanned"] == 1

        session = tmp_db.execute(
            "SELECT project_path, project_name, source FROM sessions WHERE session_id = 'cowork-abc'"
        ).fetchone()
        assert session["project_path"] == "/Users/test/Projects/cc-anywhere"
        assert session["project_name"] == "cc-anywhere"
        assert session["source"] == "claude-code"

        msg = tmp_db.execute(
            "SELECT source_path, source_line FROM messages WHERE session_id = 'cowork-abc' "
            "ORDER BY source_line LIMIT 1"
        ).fetchone()
        assert "local-agent-mode-sessions" in msg["source_path"]
        assert msg["source_line"] == 2

    def test_backfill_source_provenance_includes_claude_cowork(self, tmp_db, tmp_path, cowork_dir):
        empty_claude = tmp_path / ".claude"
        empty_claude.mkdir()
        tmp_db.execute(
            """
            INSERT INTO sessions (
                session_id, project_path, project_name,
                started_at, last_message_at, machine_name
            )
            VALUES ('cowork-abc', '/sessions/dreamy/happy/thompson', 'thompson',
                    '2026-04-22T14:42:29.800Z', '2026-04-22T14:42:30.000Z', 'test')
            """
        )
        tmp_db.execute(
            """
            INSERT INTO messages (uuid, session_id, role, content, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "cowork-user-legacy",
                "cowork-abc",
                "user",
                "where is claude design",
                "2026-04-22T14:42:29.800Z",
            ),
        )
        tmp_db.commit()

        stats = backfill_source_provenance(
            db=tmp_db,
            claude_dir=empty_claude,
            cowork_dir=cowork_dir,
        )

        assert stats["claude_files_scanned"] == 1
        assert stats["messages_updated"] == 1

        row = tmp_db.execute(
            "SELECT source_path, source_line FROM messages WHERE uuid = ?",
            ("cowork-user-legacy",),
        ).fetchone()
        assert "local-agent-mode-sessions" in row["source_path"]
        assert row["source_line"] == 2


# ============ Search ============


class TestDbSearch:
    def test_search_finds_content(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        results = db_search("decorator", db=tmp_db)
        assert len(results) > 0
        assert any("decorator" in r["content"].lower() for r in results)

    def test_search_no_results(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        results = db_search("xyznonexistent123", db=tmp_db)
        assert len(results) == 0

    def test_search_with_project_filter(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        results = db_search("decorator", project="myproject", db=tmp_db)
        assert len(results) > 0

        results = db_search("decorator", project="nonexistent", db=tmp_db)
        assert len(results) == 0

    def test_search_result_fields(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        results = db_search("decorator", db=tmp_db)
        assert len(results) > 0

        r = results[0]
        assert "uuid" in r
        assert "session_id" in r
        assert "role" in r
        assert "content" in r
        assert "timestamp" in r
        assert "project_name" in r
        assert "message_type" in r
        assert "is_compact_summary" in r
        assert "is_visible_in_transcript_only" in r

    def test_search_limit(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        results = db_search("decorator", limit=1, db=tmp_db)
        assert len(results) <= 1


# ============ Session Messages ============


class TestGetSessionMessages:
    def test_returns_ordered_messages(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        messages = get_session_messages("abc123", db=tmp_db)
        assert len(messages) > 0

        # First message should be user
        assert messages[0]["role"] == "user"

    def test_nonexistent_session(self, tmp_db):
        messages = get_session_messages("nonexistent", db=tmp_db)
        assert len(messages) == 0


# ============ Stats ============


class TestGetCaptureStats:
    def test_stats_after_capture(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        stats = get_capture_stats(db=tmp_db)
        assert stats["total_sessions"] >= 1
        assert stats["total_messages"] >= 1
        assert stats["projects"] >= 1

    def test_stats_empty_db(self, tmp_db):
        stats = get_capture_stats(db=tmp_db)
        assert stats["total_sessions"] == 0
        assert stats["total_messages"] == 0
        assert stats["projects"] == 0


# ============ Export / Import ============


class TestExportImport:
    def test_export_returns_data(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        exported = export_for_sync(db=tmp_db, days=9999)
        assert len(exported["sessions"]) >= 1
        assert len(exported["messages"]) >= 1

    def test_export_with_machine_filter(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        exported = export_for_sync(machine_name="nonexistent", db=tmp_db, days=9999)
        assert len(exported["sessions"]) == 0

    def test_import_deduplicates(self, tmp_db, claude_dir):
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        exported = export_for_sync(db=tmp_db, days=9999)

        # Import into same DB — should skip existing records
        result = import_from_sync(exported, "other-machine", db=tmp_db)
        assert result["imported_sessions"] == 0
        assert result["skipped"] == len(exported["messages"])

    def test_import_new_records(self, tmp_path):
        # Create two separate DBs
        db1_path = tmp_path / "db1.db"
        db2_path = tmp_path / "db2.db"
        db1 = get_db(db1_path)
        db2 = get_db(db2_path)

        # Insert a session+message into db1
        db1.execute(
            "INSERT INTO sessions (session_id, project_path, project_name, machine_name, last_message_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sess-remote", "/remote/path", "remote-proj", "machine-b", "2024-01-01T00:00:00")
        )
        db1.execute(
            "INSERT INTO messages (uuid, session_id, role, content, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            ("uuid-remote-1", "sess-remote", "user", "Hello from remote", "2024-01-01T00:00:00")
        )
        db1.commit()

        # Export from db1
        exported = export_for_sync(db=db1, days=9999)

        # Import into db2
        result = import_from_sync(exported, "machine-b", db=db2)
        assert result["imported_sessions"] == 1
        assert result["imported_messages"] == 1

        # Verify data is in db2
        sessions = db2.execute("SELECT * FROM sessions").fetchall()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-remote"

        db1.close()
        db2.close()

    def test_roundtrip_json_serialization(self, tmp_db, claude_dir):
        """Test that export data survives JSON serialization (as it would in sync)."""
        capture_sessions(db=tmp_db, claude_dir=claude_dir)

        exported = export_for_sync(db=tmp_db, days=9999)

        # Serialize and deserialize like sync would
        json_str = json.dumps(exported, default=str)
        reimported = json.loads(json_str)

        # Create a fresh DB and import
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            db2 = get_db(Path(td) / "db2.db")
            result = import_from_sync(reimported, "other-machine", db=db2)
            assert result["imported_sessions"] >= 1
            assert result["imported_messages"] >= 1
            db2.close()


# ============ Edge Cases ============


class TestEdgeCases:
    def test_corrupt_jsonl_line(self, tmp_db, tmp_path):
        """Capture should skip corrupt JSONL lines without crashing."""
        cdir = tmp_path / ".claude-corrupt"
        proj_dir = cdir / "projects" / "-test-project"
        proj_dir.mkdir(parents=True)

        conv_file = proj_dir / "corrupted.jsonl"
        with open(conv_file, "w") as f:
            f.write('{"type": "user", "message": {"content": "valid"}, "timestamp": 1700000000000}\n')
            f.write("THIS IS NOT JSON\n")
            f.write('{"type": "user", "message": {"content": "also valid"}, "timestamp": 1700000010000}\n')

        result = capture_sessions(db=tmp_db, claude_dir=cdir)
        assert result["new_messages"] == 2

    def test_empty_conversation_file(self, tmp_db, tmp_path):
        """Capture should handle empty JSONL files."""
        cdir = tmp_path / ".claude-empty"
        proj_dir = cdir / "projects" / "-test-empty"
        proj_dir.mkdir(parents=True)

        conv_file = proj_dir / "empty.jsonl"
        conv_file.touch()

        result = capture_sessions(db=tmp_db, claude_dir=cdir)
        assert result["new_messages"] == 0

    def test_no_projects_dir(self, tmp_db, tmp_path):
        """Capture should handle missing projects directory."""
        cdir = tmp_path / ".claude-noproj"
        cdir.mkdir()

        result = capture_sessions(db=tmp_db, claude_dir=cdir)
        assert result["projects_scanned"] == 0

    def test_timestamp_as_string(self, tmp_db, tmp_path):
        """Capture should handle string timestamps."""
        cdir = tmp_path / ".claude-strtimestamp"
        proj_dir = cdir / "projects" / "-test-strts"
        proj_dir.mkdir(parents=True)

        conv_file = proj_dir / "strts.jsonl"
        entry = {
            "type": "user",
            "message": {"content": "String timestamp message"},
            "timestamp": "2024-01-01T00:00:00",
        }
        with open(conv_file, "w") as f:
            f.write(json.dumps(entry) + "\n")

        result = capture_sessions(db=tmp_db, claude_dir=cdir)
        assert result["new_messages"] == 1

        rows = tmp_db.execute("SELECT timestamp FROM messages").fetchall()
        assert rows[0]["timestamp"] == "2024-01-01T00:00:00"
