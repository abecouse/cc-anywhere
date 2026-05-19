#!/usr/bin/env python3
"""Tests for cc-anywhere --init setup logic.

Focuses on the high-risk pieces — settings.json merge logic that must
be idempotent, never overwrite existing hooks, and produce valid JSON.
The OS-specific scheduler installers (launchd / cron / Task Scheduler)
are integration-tested live on real machines; their unit-level surface
is too thin to be worth mocking.
"""

import json
from pathlib import Path

import pytest

from cc_anywhere.init_setup import (
    _add_hook,
    _hook_command_present,
    detect_os,
    merge_claude_settings,
    run_initial_capture,
    session_start_command,
    stop_command,
    windows_periodic_instructions,
)


# ============ OS detection ============


class TestDetectOs:
    def test_returns_known_value(self):
        assert detect_os() in ("macos", "linux", "windows")


# ============ Hook helpers ============


class TestHookHelpers:
    def test_command_not_present_in_empty_settings(self):
        assert _hook_command_present({}, "SessionStart", "anything") is False

    def test_command_present_when_substring_matches(self):
        settings = {
            "hooks": {
                "SessionStart": [
                    {"hooks": [{"type": "command", "command": "cc-anywhere --ask --json-context foo"}]}
                ]
            }
        }
        assert _hook_command_present(settings, "SessionStart", "--json-context") is True

    def test_command_not_present_for_other_event(self):
        settings = {
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}]
            }
        }
        assert _hook_command_present(settings, "Stop", "x") is False

    def test_add_hook_creates_structure(self):
        settings = {}
        _add_hook(settings, "SessionStart",
                  {"type": "command", "command": "echo hi"})
        assert settings["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "echo hi"

    def test_add_hook_appends_to_existing(self):
        settings = {
            "hooks": {
                "SessionStart": [{"hooks": [
                    {"type": "command", "command": "existing"}
                ]}]
            }
        }
        _add_hook(settings, "SessionStart",
                  {"type": "command", "command": "new"})
        commands = [h["command"] for h in settings["hooks"]["SessionStart"][0]["hooks"]]
        assert commands == ["existing", "new"]


# ============ merge_claude_settings ============


class TestMergeClaudeSettings:
    def test_creates_new_file(self, tmp_path):
        settings = tmp_path / "settings.json"
        status = merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        assert status["session_start"] == "added"
        assert status["stop"] == "added"
        assert settings.exists()
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "hooks" in data

    def test_writes_session_start_command(self, tmp_path):
        settings = tmp_path / "settings.json"
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        data = json.loads(settings.read_text(encoding="utf-8"))
        cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert "--json-context" in cmd
        assert "/tmp/cc-anywhere --read" in cmd

    def test_writes_stop_command(self, tmp_path):
        settings = tmp_path / "settings.json"
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        data = json.loads(settings.read_text(encoding="utf-8"))
        cmd = data["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert cmd == stop_command("/tmp/cc-anywhere")

    def test_idempotent(self, tmp_path):
        settings = tmp_path / "settings.json"
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        status = merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        assert status["session_start"] == "present"
        assert status["stop"] == "present"

    def test_idempotent_does_not_duplicate(self, tmp_path):
        settings = tmp_path / "settings.json"
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        data = json.loads(settings.read_text(encoding="utf-8"))
        # Each event has exactly one matcher entry with exactly one hook.
        assert len(data["hooks"]["SessionStart"][0]["hooks"]) == 1
        assert len(data["hooks"]["Stop"][0]["hooks"]) == 1

    def test_preserves_existing_unrelated_settings(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps({"theme": "dark", "cleanupPeriodDays": 365}),
            encoding="utf-8",
        )
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["theme"] == "dark"
        assert data["cleanupPeriodDays"] == 365

    def test_preserves_existing_hooks_on_other_events(self, tmp_path):
        """If the user has a PreToolUse hook, we don't touch it."""
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps({"hooks": {
                "PreToolUse": [{"hooks": [
                    {"type": "command", "command": "echo pretool"}
                ]}]
            }}),
            encoding="utf-8",
        )
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        data = json.loads(settings.read_text(encoding="utf-8"))
        # Original hook still present, untouched
        assert data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == "echo pretool"
        # Our new hooks added alongside
        assert "SessionStart" in data["hooks"]
        assert "Stop" in data["hooks"]

    def test_preserves_existing_session_start_hook(self, tmp_path):
        """If the user already has a SessionStart hook for something else
        (e.g. their own sync script), we add ours alongside, not replace."""
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps({"hooks": {
                "SessionStart": [{"hooks": [
                    {"type": "command", "command": "bash my-other-script.sh", "async": True}
                ]}]
            }}),
            encoding="utf-8",
        )
        status = merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        assert status["session_start"] == "added"
        data = json.loads(settings.read_text(encoding="utf-8"))
        commands = [h["command"] for h in data["hooks"]["SessionStart"][0]["hooks"]]
        assert "bash my-other-script.sh" in commands
        assert any("--json-context" in c for c in commands)

    def test_skips_when_existing_session_start_already_uses_cc_anywhere(self, tmp_path):
        """If a hook already runs cc-anywhere with --json-context, we skip."""
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps({"hooks": {
                "SessionStart": [{"hooks": [
                    {"type": "command", "command": "cc-anywhere --read --json-context"}
                ]}]
            }}),
            encoding="utf-8",
        )
        status = merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        assert status["session_start"] == "present"
        data = json.loads(settings.read_text(encoding="utf-8"))
        # Still exactly one hook on SessionStart — we didn't add a duplicate.
        assert len(data["hooks"]["SessionStart"][0]["hooks"]) == 1

    def test_updates_existing_old_session_start_hook_to_read(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps({"hooks": {
                "SessionStart": [{"hooks": [
                    {"type": "command", "command": "cc-anywhere --ask 'foo' --json-context"}
                ]}]
            }}),
            encoding="utf-8",
        )
        status = merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        assert status["session_start"] == "updated"
        data = json.loads(settings.read_text(encoding="utf-8"))
        cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        assert "--read" in cmd
        assert "--ask" not in cmd

    def test_skips_when_existing_stop_already_runs_capture(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(
            json.dumps({"hooks": {
                "Stop": [{"hooks": [
                    {"type": "command", "command": "cc-anywhere --capture && echo done"}
                ]}]
            }}),
            encoding="utf-8",
        )
        status = merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        assert status["stop"] == "present"

    def test_handles_corrupt_settings_json(self, tmp_path):
        """If existing settings.json is invalid, we treat it as empty
        rather than crashing — but we don't overwrite the user's bad
        file's other content (we replace with a known-good minimal config)."""
        settings = tmp_path / "settings.json"
        settings.write_text("{not json at all}", encoding="utf-8")
        # Should not raise.
        status = merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        assert status["session_start"] == "added"
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "hooks" in data

    def test_creates_parent_directory(self, tmp_path):
        settings = tmp_path / "nested" / "deep" / "settings.json"
        merge_claude_settings(settings, bin_path="/tmp/cc-anywhere")
        assert settings.exists()

    def test_session_start_command_quotes_resolved_path(self):
        cmd = session_start_command("/Applications/CC Anywhere/bin/cc-anywhere")
        assert "'/Applications/CC Anywhere/bin/cc-anywhere'" in cmd
        assert "--json-context" in cmd


class TestInitialCapture:
    def test_builds_semantic_index_when_db_has_messages_but_no_chunks(self, tmp_path, monkeypatch):
        db_path = tmp_path / "test.db"
        monkeypatch.setattr("cc_anywhere.sqlite_capture.DB_PATH", db_path)
        monkeypatch.setattr(
            "cc_anywhere.sqlite_capture.capture_sessions",
            lambda db=None, claude_dir=None, cowork_dir=None: {
                "new_sessions": 0,
                "new_messages": 0,
                "projects_scanned": 0,
            },
        )
        monkeypatch.setattr(
            "cc_anywhere.sqlite_capture.capture_codex_sessions",
            lambda db=None, codex_dir=None: {
                "new_sessions": 0,
                "new_messages": 0,
                "files_scanned": 0,
            },
        )
        monkeypatch.setattr(
            "cc_anywhere.sqlite_capture.capture_gemini_sessions",
            lambda db=None, gemini_dir=None: {
                "new_sessions": 0,
                "new_messages": 0,
                "files_scanned": 0,
            },
        )

        from cc_anywhere.sqlite_capture import get_db

        db = get_db(db_path)
        db.execute(
            """
            INSERT INTO sessions (
                session_id, project_path, project_name, started_at,
                last_message_at, machine_name, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "existing-session",
                "/Users/test/project",
                "project",
                "2026-04-30T00:00:00.000Z",
                "2026-04-30T00:00:00.000Z",
                "test-machine",
                "claude-code",
            ),
        )
        db.execute(
            """
            INSERT INTO messages (
                uuid, session_id, role, content, timestamp
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "msg-1",
                "existing-session",
                "user",
                "what did we decide about auth?",
                "2026-04-30T00:00:00.000Z",
            ),
        )
        db.commit()
        db.close()

        result = run_initial_capture()
        assert result["claude_code_messages"] == 0
        assert result["codex_messages"] == 0
        assert result["gemini_messages"] == 0
        assert result["indexed_chunks"] > 0


# ============ Windows fallback ============


class TestWindowsInstructions:
    def test_includes_schtasks(self):
        msg = windows_periodic_instructions()
        assert "schtasks" in msg
        assert "cc-anywhere" in msg

    def test_mentions_wsl_alternative(self):
        msg = windows_periodic_instructions()
        assert "WSL" in msg
