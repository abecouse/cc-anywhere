#!/usr/bin/env python3
"""
Backup operations for cc-anywhere.

Organized archives of Claude history by year/month/project.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from cc_anywhere._paths import BACKUP_DIR, CLAUDE_DIR, migrate_legacy_paths

log = logging.getLogger("cc-anywhere")


def backup_history() -> int:
    """Backup Claude history organized by year/month/project.

    Structure:
        ~/.cc-backups/
          2025/
            12/
              threaded.jsonl
              biosearch.jsonl
              ...
            11/
              ...

    Returns number of new entries backed up.
    """
    migrate_legacy_paths()
    history_file = CLAUDE_DIR / "history.jsonl"
    if not history_file.exists():
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Load ALL existing backup entries (to avoid duplicates)
    existing_timestamps = set()
    for year_dir in BACKUP_DIR.glob("*"):
        if not year_dir.is_dir():
            continue
        for month_dir in year_dir.glob("*"):
            if not month_dir.is_dir():
                continue
            for backup_file in month_dir.glob("*.jsonl"):
                try:
                    with open(backup_file, encoding="utf-8") as f:
                        for line in f:
                            try:
                                entry = json.loads(line)
                                ts = entry.get("timestamp")
                                if ts:
                                    existing_timestamps.add(ts)
                            except json.JSONDecodeError:
                                pass  # Skip malformed JSON lines
                except OSError as e:
                    log.warning("Failed to read backup file %s: %s", backup_file, e)

    # Also check old flat structure for migration
    for backup_file in BACKUP_DIR.glob("*.jsonl"):
        try:
            with open(backup_file, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        ts = entry.get("timestamp")
                        if ts:
                            existing_timestamps.add(ts)
                    except json.JSONDecodeError:
                        pass
        except OSError as e:
            log.warning("Failed to read legacy backup %s: %s", backup_file, e)

    # Group new entries by year/month/project
    entries_by_path = {}  # {(year, month, project): [lines]}
    try:
        with open(history_file, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp")
                    project_path = entry.get("project", "")

                    if ts and isinstance(ts, (int, float)) and ts not in existing_timestamps:
                        dt = datetime.fromtimestamp(ts / 1000)
                        year = dt.strftime("%Y")
                        month = dt.strftime("%m")
                        project = Path(project_path).name if project_path else "unknown"

                        key = (year, month, project)
                        if key not in entries_by_path:
                            entries_by_path[key] = []
                        entries_by_path[key].append(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass  # Skip malformed JSON lines
    except OSError as e:
        log.warning("Failed to read history file: %s", e)

    # Write entries to their respective files
    new_entries = 0
    for (year, month, project), lines in entries_by_path.items():
        project_dir = BACKUP_DIR / year / month
        project_dir.mkdir(parents=True, exist_ok=True)

        backup_file = project_dir / f"{project}.jsonl"
        with open(backup_file, "a", encoding="utf-8") as out:
            for line in lines:
                out.write(line)
                new_entries += 1

    return new_entries


# Keep old function name for backwards compatibility
def backup_history_monthly() -> int:
    """Backwards compatible wrapper for backup_history()."""
    return backup_history()


def get_backup_stats() -> dict:
    """Get statistics about local backups.

    Returns dict with backup file info organized by year/month.
    """
    migrate_legacy_paths()
    if not BACKUP_DIR.exists():
        return {"files": [], "total_size": 0, "total_entries": 0, "projects": set()}

    files = []
    total_size = 0
    total_entries = 0
    all_projects = set()

    # Check new nested structure
    for year_dir in sorted(BACKUP_DIR.glob("*")):
        if not year_dir.is_dir():
            continue
        for month_dir in sorted(year_dir.glob("*")):
            if not month_dir.is_dir():
                continue
            for backup_file in sorted(month_dir.glob("*.jsonl")):
                size = backup_file.stat().st_size
                with open(backup_file, encoding="utf-8") as f:
                    entries = sum(1 for _ in f)
                project = backup_file.stem
                all_projects.add(project)
                files.append({
                    "year": year_dir.name,
                    "month": month_dir.name,
                    "project": project,
                    "size": size,
                    "entries": entries
                })
                total_size += size
                total_entries += entries

    # Also check old flat structure
    for backup_file in sorted(BACKUP_DIR.glob("*.jsonl")):
        size = backup_file.stat().st_size
        with open(backup_file, encoding="utf-8") as f:
            entries = sum(1 for _ in f)
        files.append({
            "month": backup_file.stem,
            "size": size,
            "entries": entries,
            "legacy": True
        })
        total_size += size
        total_entries += entries

    return {
        "files": files,
        "total_size": total_size,
        "total_entries": total_entries,
        "projects": list(all_projects)
    }
