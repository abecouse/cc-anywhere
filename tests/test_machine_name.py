#!/usr/bin/env python3
"""Tests for machine attribution and session start times.

Regression cover for three defects that corrupted session provenance:

1. ``get_machine_name`` stripped ".local" as a *substring*, so the host
   "Mac.localdomain" was recorded as the machine "Macdomain".
2. The capture paths called ``socket.gethostname()`` directly and ignored the
   configured ``machine_name`` in ~/.cc-anywhere.json entirely.
3. The Claude Code capture path stamped ``started_at`` with the capture time,
   so every session looked like it began the moment it was indexed.
"""

import json
import socket

import pytest

from cc_anywhere import core
from cc_anywhere.sqlite_capture import capture_sessions, get_db


class TestGetMachineName:
    @pytest.fixture(autouse=True)
    def _no_config(self, monkeypatch):
        """Force the hostname fallback path."""
        monkeypatch.setattr(core, "load_config", lambda: {})

    @pytest.mark.parametrize("hostname,expected", [
        # The regression: ".localdomain" must be matched before ".local".
        ("Mac.localdomain", "Mac"),
        ("Abes-Mac-mini.local", "Abes-Mac-mini"),
        ("MacBookAir.lan", "MacBookAir"),
        ("plainhost", "plainhost"),
        # Only a trailing suffix is stripped, never a substring.
        ("my.local.box", "my.local.box"),
        ("localdomain-server", "localdomain-server"),
    ])
    def test_suffix_stripping(self, monkeypatch, hostname, expected):
        monkeypatch.setattr(socket, "gethostname", lambda: hostname)
        assert core.get_machine_name() == expected

    def test_unresolvable_hostname_is_not_fatal(self, monkeypatch):
        def boom():
            raise OSError("no hostname")
        monkeypatch.setattr(socket, "gethostname", boom)
        assert core.get_machine_name() == "unknown"


class TestConfiguredMachineNameWins:
    def test_config_overrides_hostname(self, monkeypatch):
        monkeypatch.setattr(core, "load_config", lambda: {"machine_name": "Abes-Mac-mini"})
        monkeypatch.setattr(socket, "gethostname", lambda: "Mac.localdomain")
        assert core.get_machine_name() == "Abes-Mac-mini"

    def test_capture_uses_configured_name_not_hostname(self, tmp_path, monkeypatch):
        """Renaming the Mac must not split history into a second machine."""
        config = tmp_path / ".cc-anywhere.json"
        config.write_text(json.dumps({"machine_name": "Abes-Mac-mini"}))
        monkeypatch.setattr(core, "CONFIG_FILE", config)
        monkeypatch.setattr(socket, "gethostname", lambda: "some-other-host.local")

        claude_dir = tmp_path / ".claude"
        proj = claude_dir / "projects" / "-Users-test-proj"
        proj.mkdir(parents=True)
        (proj / "s1.jsonl").write_text(json.dumps({
            "type": "user",
            "message": {"content": "hello"},
            "timestamp": "2025-11-15T15:32:32.234Z",
        }) + "\n")

        db = get_db(tmp_path / "test.db")
        capture_sessions(db=db, claude_dir=claude_dir)

        machines = [r[0] for r in db.execute(
            "SELECT DISTINCT machine_name FROM sessions").fetchall()]
        assert machines == ["Abes-Mac-mini"]
        db.close()


class TestSessionStartedAt:
    def test_started_at_is_first_message_not_capture_time(self, tmp_path):
        """started_at must reflect the conversation, not when we indexed it."""
        claude_dir = tmp_path / ".claude"
        proj = claude_dir / "projects" / "-Users-test-proj"
        proj.mkdir(parents=True)
        first = "2025-11-15T15:32:32.234Z"
        last = "2025-11-26T17:07:20.872Z"
        (proj / "s1.jsonl").write_text(
            json.dumps({"type": "user", "message": {"content": "first"},
                        "timestamp": first}) + "\n" +
            json.dumps({"type": "assistant",
                        "message": {"content": [{"type": "text", "text": "later"}]},
                        "timestamp": last}) + "\n"
        )

        db = get_db(tmp_path / "test.db")
        capture_sessions(db=db, claude_dir=claude_dir)

        row = db.execute(
            "SELECT started_at, last_message_at FROM sessions").fetchone()
        assert row["started_at"] == first
        assert row["last_message_at"] == last
        db.close()

    def test_incremental_capture_does_not_push_started_at_forward(self, tmp_path):
        """A second capture appending newer messages must not move started_at."""
        claude_dir = tmp_path / ".claude"
        proj = claude_dir / "projects" / "-Users-test-proj"
        proj.mkdir(parents=True)
        conv = proj / "s1.jsonl"
        first = "2025-11-15T15:32:32.234Z"
        conv.write_text(json.dumps({
            "type": "user", "message": {"content": "first"},
            "timestamp": first}) + "\n")

        db = get_db(tmp_path / "test.db")
        capture_sessions(db=db, claude_dir=claude_dir)

        with conv.open("a") as f:
            f.write(json.dumps({
                "type": "user", "message": {"content": "much later"},
                "timestamp": "2026-09-02T23:05:36.240Z"}) + "\n")
        capture_sessions(db=db, claude_dir=claude_dir)

        row = db.execute("SELECT started_at, last_message_at FROM sessions").fetchone()
        assert row["started_at"] == first
        assert row["last_message_at"] == "2026-09-02T23:05:36.240Z"
        db.close()
