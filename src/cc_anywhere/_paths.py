"""
Filesystem paths owned by cc-anywhere.

Naming rule:
  - Anything starting with ~/.claude/ (no suffix) belongs to Claude Code
    itself and must never be renamed here.
  - Anything starting with ~/.claude-<something>/ was historically our own
    branding. Those have been renamed to ~/.cc-<something>/ and are
    auto-migrated on first access via migrate_legacy_paths().
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("cc-anywhere")

HOME = Path.home()

# Real Claude Code directory — Anthropic-owned, never rename.
CLAUDE_DIR = HOME / ".claude"

# Our own paths.
DB_PATH = HOME / ".cc-anywhere-sessions.db"
SYNC_DIR = HOME / ".cc-sync"
BACKUP_DIR = HOME / ".cc-backups"
CONFIG_FILE = HOME / ".cc-anywhere.json"

_LEGACY: dict[Path, Path] = {
    DB_PATH: HOME / ".claude-anywhere-sessions.db",
    SYNC_DIR: HOME / ".claude-sync",
    BACKUP_DIR: HOME / ".claude-backups",
    CONFIG_FILE: HOME / ".claude-anywhere.json",
}

_migrated = False


def migrate_legacy_paths() -> None:
    """Rename ~/.claude-* paths owned by us to their ~/.cc-* equivalents.

    Idempotent and safe:
      - Only renames when the legacy path exists and the new one does not.
      - If both exist, leaves them alone — the new path wins by being the
        one our code reads.
    """
    global _migrated
    if _migrated:
        return
    _migrated = True

    for new_path, legacy_path in _LEGACY.items():
        if legacy_path.exists() and not new_path.exists():
            try:
                os.rename(legacy_path, new_path)
                log.info("Migrated %s -> %s", legacy_path, new_path)
            except OSError as e:
                log.warning("Could not migrate %s -> %s: %s", legacy_path, new_path, e)
