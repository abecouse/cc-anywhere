#!/usr/bin/env python3
"""Tests for cc_anywhere._paths.migrate_legacy_paths.

Verifies the one-time auto-rename of `~/.claude-*` paths owned by us
to their `~/.cc-*` equivalents, used during the v1.1 -> v1.2 brand
rename so existing installs upgrade in place.
"""

import importlib

import pytest


@pytest.fixture
def fresh_paths(tmp_path, monkeypatch):
    """Reload cc_anywhere._paths against a faked $HOME and reset its
    one-shot guard so migrate_legacy_paths runs again from scratch."""
    monkeypatch.setenv("HOME", str(tmp_path))
    import cc_anywhere._paths as paths_mod
    importlib.reload(paths_mod)
    paths_mod._migrated = False  # reset the one-shot guard
    return paths_mod, tmp_path


# ============ Tests ============


class TestMigrateLegacyPaths:
    def test_no_legacy_no_op(self, fresh_paths):
        """Fresh install with no legacy paths — no-op, no errors."""
        paths_mod, home = fresh_paths
        paths_mod.migrate_legacy_paths()
        # Nothing should have been created.
        assert not paths_mod.DB_PATH.exists()
        assert not paths_mod.SYNC_DIR.exists()

    def test_renames_db_file(self, fresh_paths):
        paths_mod, home = fresh_paths
        legacy_db = home / ".claude-anywhere-sessions.db"
        legacy_db.write_text("fake db content", encoding="utf-8")
        paths_mod.migrate_legacy_paths()
        assert not legacy_db.exists()
        assert paths_mod.DB_PATH.exists()
        assert paths_mod.DB_PATH.read_text(encoding="utf-8") == "fake db content"

    def test_renames_sync_dir(self, fresh_paths):
        paths_mod, home = fresh_paths
        legacy_sync = home / ".claude-sync"
        legacy_sync.mkdir()
        (legacy_sync / "marker.txt").write_text("from old name", encoding="utf-8")
        paths_mod.migrate_legacy_paths()
        assert not legacy_sync.exists()
        assert paths_mod.SYNC_DIR.exists()
        assert (paths_mod.SYNC_DIR / "marker.txt").read_text(encoding="utf-8") == "from old name"

    def test_renames_backup_dir(self, fresh_paths):
        paths_mod, home = fresh_paths
        legacy_backup = home / ".claude-backups"
        legacy_backup.mkdir()
        (legacy_backup / "2026" / "04").mkdir(parents=True)
        (legacy_backup / "2026" / "04" / "proj.jsonl").write_text(
            "monthly archive", encoding="utf-8"
        )
        paths_mod.migrate_legacy_paths()
        assert not legacy_backup.exists()
        assert (paths_mod.BACKUP_DIR / "2026" / "04" / "proj.jsonl").exists()

    def test_renames_config_file(self, fresh_paths):
        paths_mod, home = fresh_paths
        legacy_cfg = home / ".claude-anywhere.json"
        legacy_cfg.write_text('{"machine_name": "test"}', encoding="utf-8")
        paths_mod.migrate_legacy_paths()
        assert not legacy_cfg.exists()
        assert paths_mod.CONFIG_FILE.exists()

    def test_idempotent(self, fresh_paths):
        """Running migration twice is a no-op the second time."""
        paths_mod, home = fresh_paths
        legacy_db = home / ".claude-anywhere-sessions.db"
        legacy_db.write_text("v1", encoding="utf-8")
        paths_mod.migrate_legacy_paths()  # moves
        # Reset guard so we'd actually retry.
        paths_mod._migrated = False
        paths_mod.migrate_legacy_paths()  # no-op now
        assert paths_mod.DB_PATH.read_text(encoding="utf-8") == "v1"

    def test_both_paths_exist_keeps_new(self, fresh_paths):
        """If both legacy AND new paths exist, the new one wins.

        Refuses to silently overwrite either side — leaves both alone
        so the user can resolve manually.
        """
        paths_mod, home = fresh_paths
        legacy_db = home / ".claude-anywhere-sessions.db"
        legacy_db.write_text("legacy content", encoding="utf-8")
        paths_mod.DB_PATH.write_text("new content", encoding="utf-8")
        paths_mod.migrate_legacy_paths()
        # Both should still exist.
        assert legacy_db.exists()
        assert paths_mod.DB_PATH.exists()
        # The new one is what code reads — must be untouched.
        assert paths_mod.DB_PATH.read_text(encoding="utf-8") == "new content"

    def test_does_not_touch_real_claude_dir(self, fresh_paths):
        """~/.claude/ belongs to Claude Code itself — never rename."""
        paths_mod, home = fresh_paths
        claude_dir = home / ".claude"
        claude_dir.mkdir()
        (claude_dir / "projects").mkdir()
        paths_mod.migrate_legacy_paths()
        # Path should be unchanged.
        assert (home / ".claude" / "projects").exists()
        assert paths_mod.CLAUDE_DIR == home / ".claude"

    def test_one_shot_guard_prevents_repeat(self, fresh_paths):
        """The module-level _migrated flag short-circuits repeat calls."""
        paths_mod, home = fresh_paths
        # First call sets the guard.
        paths_mod.migrate_legacy_paths()
        assert paths_mod._migrated is True
        # Subsequent calls return immediately without scanning.
        legacy_late = home / ".claude-anywhere.json"
        legacy_late.write_text("created after first call", encoding="utf-8")
        paths_mod.migrate_legacy_paths()
        # Without resetting the flag, the late-arriving legacy file is
        # NOT migrated. (Process-lifetime caching is intentional.)
        assert legacy_late.exists()
        assert not paths_mod.CONFIG_FILE.exists()

    def test_migrates_multiple_paths_in_one_call(self, fresh_paths):
        paths_mod, home = fresh_paths
        (home / ".claude-anywhere-sessions.db").write_text("a", encoding="utf-8")
        (home / ".claude-sync").mkdir()
        (home / ".claude-anywhere.json").write_text("b", encoding="utf-8")
        paths_mod.migrate_legacy_paths()
        assert paths_mod.DB_PATH.exists()
        assert paths_mod.SYNC_DIR.exists()
        assert paths_mod.CONFIG_FILE.exists()
