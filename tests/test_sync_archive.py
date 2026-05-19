#!/usr/bin/env python3
"""Tests for --sync-archive (full-history backup) — filesystem destination only.

The GitHub-destination branch is a thin wrapper around git push and is
covered by live use rather than unit tests.
"""

import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from cc_anywhere.sqlite_capture import get_db
from cc_anywhere.sync import sync_push_archive


# ============ Fixtures ============


@pytest.fixture
def populated_db_path(tmp_path, monkeypatch):
    """A SQLite DB at a deterministic path with a few captured messages.

    Patches DB_PATH so sync_push_archive (which calls export_for_sync()
    with no `db` arg) reads from this fixture instead of the real one.
    """
    db_path = tmp_path / "cc-anywhere-sessions.db"
    db = get_db(db_path)

    db.execute(
        "INSERT INTO sessions (session_id, project_path, project_name, "
        "started_at, last_message_at, machine_name, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess-1", "/home/dev/proj", "proj",
         "2026-04-01T10:00:00.000Z", "2026-04-01T10:05:00.000Z",
         "macbook-15", "claude-code"),
    )
    db.execute(
        "INSERT INTO sessions (session_id, project_path, project_name, "
        "started_at, last_message_at, machine_name, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("sess-2", "/home/dev/proj", "proj",
         "2026-04-15T11:00:00.000Z", "2026-04-15T11:30:00.000Z",
         "imac", "codex"),
    )
    db.execute(
        "INSERT INTO messages (uuid, session_id, role, content, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        ("msg-a", "sess-1", "user", "first prompt", "2026-04-01T10:00:00.000Z"),
    )
    db.execute(
        "INSERT INTO messages (uuid, session_id, role, content, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        ("msg-b", "sess-1", "assistant", "first reply",
         "2026-04-01T10:05:00.000Z"),
    )
    db.execute(
        "INSERT INTO messages (uuid, session_id, role, content, timestamp) "
        "VALUES (?, ?, ?, ?, ?)",
        ("msg-c", "sess-2", "user", "from another machine",
         "2026-04-15T11:00:00.000Z"),
    )
    db.commit()
    db.close()

    # sync_push_archive opens a fresh DB connection via export_for_sync;
    # patch the constant so it reads from our fixture.
    monkeypatch.setattr("cc_anywhere.sqlite_capture.DB_PATH", db_path)
    return db_path


# ============ Tests ============


class TestSyncPushArchiveFilesystem:
    def test_writes_archive_file(self, populated_db_path, tmp_path):
        dest = tmp_path / "backup"
        ok, msg = sync_push_archive("macbook-15", dest=dest)
        assert ok, msg
        archive = dest / "machines" / "macbook-15" / "archive.json.gz"
        assert archive.exists()

    def test_archive_has_expected_shape(self, populated_db_path, tmp_path):
        dest = tmp_path / "backup"
        sync_push_archive("macbook-15", dest=dest)
        archive = dest / "machines" / "macbook-15" / "archive.json.gz"
        with gzip.open(archive, "rb") as gz:
            payload = json.loads(gz.read().decode("utf-8"))
        assert "sessions" in payload
        assert "messages" in payload

    def test_archive_includes_all_machines(self, populated_db_path, tmp_path):
        """Backup should include sessions from EVERY machine, not just the
        one we're running on. UUID dedup makes cross-machine merge safe.
        """
        dest = tmp_path / "backup"
        sync_push_archive("macbook-15", dest=dest)
        archive = dest / "machines" / "macbook-15" / "archive.json.gz"
        with gzip.open(archive, "rb") as gz:
            payload = json.loads(gz.read().decode("utf-8"))
        session_ids = {s["session_id"] for s in payload["sessions"]}
        # sess-1 was captured on this machine; sess-2 came from imac
        assert "sess-1" in session_ids
        assert "sess-2" in session_ids

    def test_archive_includes_all_messages(self, populated_db_path, tmp_path):
        dest = tmp_path / "backup"
        sync_push_archive("macbook-15", dest=dest)
        archive = dest / "machines" / "macbook-15" / "archive.json.gz"
        with gzip.open(archive, "rb") as gz:
            payload = json.loads(gz.read().decode("utf-8"))
        msg_uuids = {m["uuid"] for m in payload["messages"]}
        assert msg_uuids == {"msg-a", "msg-b", "msg-c"}

    def test_creates_destination_if_missing(self, populated_db_path, tmp_path):
        """--to /a/path/that/does/not/exist should mkdir it."""
        dest = tmp_path / "deep" / "nested" / "backup"
        assert not dest.exists()
        ok, msg = sync_push_archive("macbook-15", dest=dest)
        assert ok, msg
        assert (dest / "machines" / "macbook-15" / "archive.json.gz").exists()

    def test_machine_subfolder_uses_passed_name(self, populated_db_path, tmp_path):
        dest = tmp_path / "backup"
        sync_push_archive("custom-machine-name", dest=dest)
        assert (dest / "machines" / "custom-machine-name" / "archive.json.gz").exists()

    def test_returns_summary_with_counts_and_size(self, populated_db_path, tmp_path):
        dest = tmp_path / "backup"
        ok, msg = sync_push_archive("macbook-15", dest=dest)
        assert ok
        assert "2 sessions" in msg
        assert "3 messages" in msg
        assert "MB" in msg

    def test_empty_db_returns_failure(self, tmp_path, monkeypatch):
        """Archiving an empty DB should fail with a helpful message,
        not push an empty file."""
        empty_db = tmp_path / "empty.db"
        get_db(empty_db).close()
        monkeypatch.setattr("cc_anywhere.sqlite_capture.DB_PATH", empty_db)
        dest = tmp_path / "backup"
        ok, msg = sync_push_archive("any-machine", dest=dest)
        assert not ok
        assert "No captured sessions" in msg
        # Make sure no zero-byte archive was left behind.
        archive = dest / "machines" / "any-machine" / "archive.json.gz"
        assert not archive.exists()

    def test_archive_is_valid_gzip(self, populated_db_path, tmp_path):
        dest = tmp_path / "backup"
        sync_push_archive("macbook-15", dest=dest)
        archive = dest / "machines" / "macbook-15" / "archive.json.gz"
        # If gzip can't read it, this raises.
        with gzip.open(archive, "rb") as gz:
            data = gz.read()
        assert data.startswith(b"{")  # valid JSON

    def test_dest_string_path_works(self, populated_db_path, tmp_path):
        """--to accepts a str just as well as a Path."""
        dest = tmp_path / "backup"
        ok, _ = sync_push_archive("macbook-15", dest=str(dest))
        assert ok
        assert (dest / "machines" / "macbook-15" / "archive.json.gz").exists()
