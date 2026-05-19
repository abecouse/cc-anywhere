#!/usr/bin/env python3
"""Tests for Gemini CLI session capture in sqlite_capture module."""

import json
from pathlib import Path

import pytest

from cc_anywhere.sqlite_capture import (
    _extract_gemini_user_text,
    capture_gemini_sessions,
    db_search,
    get_capture_stats,
    get_db,
)


# ============ Fixtures ============


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    db = get_db(db_path)
    yield db
    db.close()


SESSION_ID = "6a4f4e53-3fdd-454a-9ae9-56a2e3bd3a47"


def _session_header(
    session_id: str = SESSION_ID,
    start: str = "2026-04-30T16:04:42.969Z",
) -> dict:
    return {
        "sessionId": session_id,
        "projectHash": "abc123",
        "startTime": start,
        "lastUpdated": start,
        "kind": "main",
    }


def _user_record(text: str, ts: str, msg_id: str = None) -> dict:
    return {
        "id": msg_id or f"u-{ts}",
        "timestamp": ts,
        "type": "user",
        "content": [{"text": text}],
    }


def _gemini_record(text: str, ts: str, msg_id: str = None) -> dict:
    return {
        "id": msg_id or f"g-{ts}",
        "timestamp": ts,
        "type": "gemini",
        "content": text,
        "model": "gemini-3-flash-preview",
    }


def _gemini_tool_only(ts: str) -> dict:
    """Tool-call-only gemini turn — empty content + toolCalls field."""
    return {
        "id": f"t-{ts}",
        "timestamp": ts,
        "type": "gemini",
        "content": "",
        "toolCalls": [{"id": "x", "name": "search"}],
    }


def _set_marker(ts: str) -> dict:
    return {"$set": {"lastUpdated": ts}}


def _write_chat_jsonl(gemini_dir: Path, project: str, records: list) -> Path:
    chats_dir = gemini_dir / "tmp" / project / "chats"
    chats_dir.mkdir(parents=True, exist_ok=True)
    path = chats_dir / f"session-2026-04-30T16-04-{SESSION_ID[:8]}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def _write_projects_json(gemini_dir: Path, mapping: dict) -> Path:
    path = gemini_dir / "projects.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"projects": mapping}, f)
    return path


@pytest.fixture
def gemini_dir(tmp_path):
    """Build a fake ~/.gemini/ with one chat session containing user + gemini turns."""
    gdir = tmp_path / ".gemini"
    gdir.mkdir()
    _write_projects_json(gdir, {"/Users/test/Projects/myproj": "myproj"})
    records = [
        _session_header(),
        _user_record("How are you?", "2026-04-30T16:04:49.348Z"),
        _set_marker("2026-04-30T16:04:49.348Z"),
        _gemini_record("Doing well, ready to help.", "2026-04-30T16:05:05.904Z"),
        _set_marker("2026-04-30T16:05:05.905Z"),
        _user_record("Search my history.", "2026-04-30T16:05:05.919Z"),
        _gemini_tool_only("2026-04-30T16:06:31.187Z"),  # should be skipped
        _gemini_record("Found 5 relevant chunks.", "2026-04-30T16:06:45.000Z"),
    ]
    _write_chat_jsonl(gdir, "myproj", records)
    return gdir


# ============ Helper extraction ============


class TestExtractGeminiUserText:
    def test_text_block_array(self):
        assert _extract_gemini_user_text([{"text": "hi"}]) == "hi"

    def test_multiple_blocks_joined(self):
        assert _extract_gemini_user_text([{"text": "a"}, {"text": "b"}]) == "a\n\nb"

    def test_string_content(self):
        assert _extract_gemini_user_text("just a string") == "just a string"

    def test_empty_list(self):
        assert _extract_gemini_user_text([]) is None

    def test_empty_string(self):
        assert _extract_gemini_user_text("   ") is None

    def test_none(self):
        assert _extract_gemini_user_text(None) is None

    def test_block_without_text(self):
        assert _extract_gemini_user_text([{"type": "image"}]) is None


# ============ Capture ============


class TestCaptureGeminiSessions:
    def test_missing_gemini_dir(self, tmp_db, tmp_path):
        """No ~/.gemini/ — capture is a no-op."""
        stats = capture_gemini_sessions(db=tmp_db, gemini_dir=tmp_path / "nope")
        assert stats == {"new_sessions": 0, "new_messages": 0, "files_scanned": 0}

    def test_missing_tmp_dir(self, tmp_db, tmp_path):
        """~/.gemini/ exists but no tmp/ — no-op."""
        gdir = tmp_path / ".gemini"
        gdir.mkdir()
        stats = capture_gemini_sessions(db=tmp_db, gemini_dir=gdir)
        assert stats["new_sessions"] == 0
        assert stats["new_messages"] == 0

    def test_basic_capture(self, tmp_db, gemini_dir):
        stats = capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        assert stats["new_sessions"] == 1
        # 2 user + 2 gemini text turns; 1 tool-only gemini turn skipped
        assert stats["new_messages"] == 4
        assert stats["files_scanned"] == 1

    def test_session_tagged_gemini(self, tmp_db, gemini_dir):
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        row = tmp_db.execute(
            "SELECT source, project_name, project_path FROM sessions "
            "WHERE session_id = ?", (SESSION_ID,)
        ).fetchone()
        assert row["source"] == "gemini"
        assert row["project_name"] == "myproj"
        assert row["project_path"] == "/Users/test/Projects/myproj"

    def test_role_mapping(self, tmp_db, gemini_dir):
        """type='gemini' becomes role='assistant'; type='user' stays 'user'."""
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        roles = [r["role"] for r in tmp_db.execute(
            "SELECT role FROM messages WHERE session_id = ? ORDER BY timestamp",
            (SESSION_ID,)
        ).fetchall()]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_tool_only_gemini_turns_skipped(self, tmp_db, gemini_dir):
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        # tool-only turn was at 2026-04-30T16:06:31.187Z; should not appear
        row = tmp_db.execute(
            "SELECT 1 FROM messages WHERE timestamp = '2026-04-30T16:06:31.187Z'"
        ).fetchone()
        assert row is None

    def test_set_markers_skipped(self, tmp_db, gemini_dir):
        """{"$set": ...} update markers don't become messages."""
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        count = tmp_db.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE content LIKE '%$set%'"
        ).fetchone()["n"]
        assert count == 0

    def test_idempotent_recapture(self, tmp_db, gemini_dir):
        """Re-running capture is a no-op via UUID dedup."""
        first = capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        second = capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        assert first["new_messages"] == 4
        # Second run finds no new content because file offset is at EOF
        assert second["new_messages"] == 0

    def test_message_uuids_use_record_id(self, tmp_db, gemini_dir):
        """We use the JSONL record's stable id as the message UUID."""
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        uuids = {r["uuid"] for r in tmp_db.execute(
            "SELECT uuid FROM messages WHERE session_id = ?", (SESSION_ID,)
        ).fetchall()}
        # Records used u-<ts>/g-<ts> ids; all four should be present
        assert "u-2026-04-30T16:04:49.348Z" in uuids
        assert "g-2026-04-30T16:05:05.904Z" in uuids

    def test_project_path_falls_back_to_dir_when_no_mapping(self, tmp_db, tmp_path):
        """If projects.json doesn't list the dir, project_path = the dir itself."""
        gdir = tmp_path / ".gemini"
        gdir.mkdir()
        # Note: no projects.json this time
        records = [_session_header(),
                   _user_record("hi", "2026-04-30T16:00:00.000Z")]
        _write_chat_jsonl(gdir, "unmapped-project", records)
        capture_gemini_sessions(db=tmp_db, gemini_dir=gdir)
        row = tmp_db.execute(
            "SELECT project_path FROM sessions WHERE session_id = ?",
            (SESSION_ID,)
        ).fetchone()
        assert "unmapped-project" in row["project_path"]

    def test_search_finds_gemini_content(self, tmp_db, gemini_dir):
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        results = db_search("Search my history", db=tmp_db)
        assert any("Search my history" in r["content"] for r in results)

    def test_credentials_files_never_touched(self, tmp_db, tmp_path):
        """oauth_creds.json + google_accounts.json must not be read or indexed."""
        gdir = tmp_path / ".gemini"
        gdir.mkdir()
        # Drop in fake credential files; capture must ignore them.
        (gdir / "oauth_creds.json").write_text(
            '{"access_token": "secret-token-do-not-read"}', encoding="utf-8"
        )
        (gdir / "google_accounts.json").write_text(
            '{"email": "private@example.com"}', encoding="utf-8"
        )
        records = [_session_header(),
                   _user_record("hi", "2026-04-30T16:00:00.000Z")]
        _write_chat_jsonl(gdir, "myproj", records)
        capture_gemini_sessions(db=tmp_db, gemini_dir=gdir)
        # Search the FTS for any trace of the credential strings.
        results = db_search("secret-token-do-not-read", db=tmp_db)
        assert results == []
        results = db_search("private@example.com", db=tmp_db)
        assert results == []

    def test_session_header_sets_started_at(self, tmp_db, gemini_dir):
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        row = tmp_db.execute(
            "SELECT started_at FROM sessions WHERE session_id = ?",
            (SESSION_ID,)
        ).fetchone()
        assert row["started_at"] == "2026-04-30T16:04:42.969Z"

    def test_last_message_at_advances(self, tmp_db, gemini_dir):
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        row = tmp_db.execute(
            "SELECT last_message_at FROM sessions WHERE session_id = ?",
            (SESSION_ID,)
        ).fetchone()
        # Last text gemini turn was at 16:06:45
        assert row["last_message_at"] == "2026-04-30T16:06:45.000Z"

    def test_appended_messages_picked_up(self, tmp_db, gemini_dir):
        """File grows; subsequent capture picks up only the new tail."""
        capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        chat_file = next((gemini_dir / "tmp" / "myproj" / "chats").glob("*.jsonl"))
        with open(chat_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(_user_record(
                "follow-up question", "2026-04-30T17:00:00.000Z",
                msg_id="u-followup"
            )) + "\n")
        stats = capture_gemini_sessions(db=tmp_db, gemini_dir=gemini_dir)
        assert stats["new_messages"] == 1
