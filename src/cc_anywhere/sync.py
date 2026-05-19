#!/usr/bin/env python3
"""
Sync operations for cc-anywhere.

Handles pushing/pulling state between machines via git.
"""

import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from cc_anywhere._paths import CLAUDE_DIR, SYNC_DIR, migrate_legacy_paths

log = logging.getLogger("cc-anywhere")


def get_daily_stats() -> dict:
    """Get daily prompt counts from history for permanent stats tracking.

    Returns dict like {"2025-12-01": 45, "2025-12-02": 78, ...}
    """
    history_file = CLAUDE_DIR / "history.jsonl"
    if not history_file.exists():
        return {}

    daily_counts = {}
    try:
        with open(history_file, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    ts = entry.get("timestamp")
                    if ts:
                        dt = datetime.fromtimestamp(ts / 1000)
                        date_str = dt.strftime("%Y-%m-%d")
                        daily_counts[date_str] = daily_counts.get(date_str, 0) + 1
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass  # Skip malformed JSON lines
    except OSError as e:
        log.warning("Failed to read history file: %s", e)

    return daily_counts


def get_recent_context(project_path: str, limit: int = 25) -> list:
    """Get recent conversation context (user prompts + Claude responses) for a project.

    Returns list of {"role": "user"|"assistant", "content": "..."} dicts.
    Truncates assistant responses to 1000 chars to keep sync size reasonable.
    """
    # Convert project path to Claude's folder naming convention
    # Claude uses path with slashes replaced by dashes, keeping leading dash
    folder_name = project_path.replace("/", "-")

    project_dir = CLAUDE_DIR / "projects" / folder_name
    if not project_dir.exists():
        return []

    # Find the largest (most recent/active) conversation file
    conv_files = list(project_dir.glob("*.jsonl"))
    if not conv_files:
        return []

    conv_file = max(conv_files, key=lambda f: f.stat().st_size)

    # Read entries
    entries = []
    try:
        with open(conv_file, encoding="utf-8") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # Skip malformed JSON lines
    except OSError as e:
        log.warning("Failed to read conversation file: %s", e)
        return []

    # Extract user/assistant messages
    messages = []
    for e in entries:
        if e.get("type") == "user":
            msg = e.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, str) and content:
                messages.append({"role": "user", "content": content[:500]})
        elif e.get("type") == "assistant":
            msg = e.get("message", {})
            content = msg.get("content", [])
            text = ""
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    text += c.get("text", "")
            if text:
                # Truncate long responses to keep sync size reasonable
                messages.append({"role": "assistant", "content": text[:1000]})

    # Return last N exchanges (limit * 2 messages for user+assistant pairs)
    return messages[-(limit * 2):]


def setup_sync(username: str, repo: str = "cc-sync"):
    """Set up sync with a GitHub repo."""
    migrate_legacy_paths()
    SYNC_DIR.mkdir(parents=True, exist_ok=True)

    # Init git if needed
    if not (SYNC_DIR / ".git").exists():
        result = subprocess.run(["git", "init"], cwd=SYNC_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            log.warning("git init failed: %s", result.stderr)

    # Set remote
    remote_url = f"https://github.com/{username}/{repo}.git"
    subprocess.run(["git", "remote", "remove", "origin"], cwd=SYNC_DIR, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=SYNC_DIR, capture_output=True)

    return remote_url


def sync_push(machine_name: str, projects: list) -> tuple[bool, str]:
    """Push local state to remote. Pulls first to avoid conflicts."""
    migrate_legacy_paths()
    if not SYNC_DIR.exists():
        return False, "Sync not configured"

    # Pull first to get latest from other machines
    pull_result = subprocess.run(["git", "pull", "--rebase"], cwd=SYNC_DIR, capture_output=True, text=True)
    # Don't fail on pull errors (might be first push, or no remote yet)

    # Backup full history to monthly archive
    from cc_anywhere.backup import backup_history_monthly
    backup_history_monthly()

    # Build state
    state = {
        "version": 1,  # Schema version for future compatibility
        "machine": machine_name,
        "updated": datetime.now().isoformat(),
        "projects": {},
        "daily_stats": get_daily_stats()  # Permanent record of daily prompt counts
    }

    for proj in projects:
        state["projects"][proj["name"]] = {
            "path": proj["path"],
            "prompts": proj["prompts"],
            "last_active": proj["last_active"].isoformat() if proj["last_active"] else None,
            "recent_context": get_recent_context(proj["path"], limit=25)
        }

    # Save to machine folder
    machine_dir = SYNC_DIR / "machines" / machine_name
    machine_dir.mkdir(parents=True, exist_ok=True)
    (machine_dir / "state.json").write_text(
        json.dumps(state, indent=2, default=str), encoding="utf-8"
    )

    # Export captured sessions for sync (last 30 days, truncated, compressed)
    try:
        import gzip
        from cc_anywhere.sqlite_capture import export_for_sync
        captured = export_for_sync(machine_name=machine_name, days=30, content_limit=500)
        if captured["sessions"] or captured["messages"]:
            data = json.dumps(captured, default=str).encode("utf-8")
            with gzip.open(machine_dir / "captured_sessions.json.gz", "wb") as gz:
                gz.write(data)
            # Remove old uncompressed file if it exists
            old_file = machine_dir / "captured_sessions.json"
            if old_file.exists():
                old_file.unlink()
    except Exception as e:
        log.debug("Could not export captured sessions: %s", e)

    # Git add, commit, push
    add_result = subprocess.run(["git", "add", "-A"], cwd=SYNC_DIR, capture_output=True, text=True)
    if add_result.returncode != 0:
        log.warning("git add failed: %s", add_result.stderr)
    subprocess.run(["git", "commit", "-m", f"Update from {machine_name}"],
                   cwd=SYNC_DIR, capture_output=True)

    result = subprocess.run(["git", "push", "-u", "origin", "main"],
                           cwd=SYNC_DIR, capture_output=True, text=True)

    if result.returncode != 0:
        # Try push to master if main fails
        result = subprocess.run(["git", "push", "-u", "origin", "master"],
                               cwd=SYNC_DIR, capture_output=True, text=True)

    if result.returncode != 0:
        return False, result.stderr
    return True, "Pushed successfully"


def sync_pull() -> tuple[bool, str]:
    """Pull latest from remote and import any archive snapshots present."""
    migrate_legacy_paths()
    if not SYNC_DIR.exists():
        return False, "Sync not configured"

    result = subprocess.run(["git", "pull"], cwd=SYNC_DIR, capture_output=True, text=True)

    if result.returncode != 0:
        return False, result.stderr

    already_up_to_date = "Already up to date" in result.stdout

    # Import captured sessions and any archive snapshots from each machine.
    # Both file types use the same JSON shape, so import_from_sync handles
    # them identically. UUID dedup means importing both is safe — same
    # message content from rolling export and full archive lands as one row.
    imported_archives = 0
    try:
        import gzip
        from cc_anywhere.sqlite_capture import import_from_sync
        machines_dir = SYNC_DIR / "machines"
        if machines_dir.exists():
            for machine_folder in machines_dir.iterdir():
                if not machine_folder.is_dir():
                    continue
                gz_file = machine_folder / "captured_sessions.json.gz"
                plain_file = machine_folder / "captured_sessions.json"
                archive_file = machine_folder / "archive.json.gz"
                if gz_file.exists():
                    with gzip.open(gz_file, "rb") as gz:
                        records = json.loads(gz.read().decode("utf-8"))
                    import_from_sync(records, machine_folder.name)
                elif plain_file.exists():
                    records = json.loads(plain_file.read_text(encoding="utf-8"))
                    import_from_sync(records, machine_folder.name)
                if archive_file.exists():
                    with gzip.open(archive_file, "rb") as gz:
                        records = json.loads(gz.read().decode("utf-8"))
                    import_from_sync(records, machine_folder.name)
                    imported_archives += 1
    except Exception as e:
        log.debug("Could not import captured sessions: %s", e)

    if already_up_to_date and imported_archives == 0:
        return True, "Already up to date"
    if imported_archives > 0:
        return True, f"Pulled new data; imported {imported_archives} archive snapshot(s)"
    return True, "Pulled new data"


# Soft limit chosen to keep us well under GitHub's 100 MB per-file cap with
# headroom for git's own packfile overhead. Filesystem destinations have no
# cap, so the limit is only enforced when pushing to a git remote.
GITHUB_FILE_WARN_MB = 90
GITHUB_FILE_ERROR_MB = 95


def sync_push_archive(machine_name: str, dest: Path | str | None = None) -> tuple[bool, str]:
    """One-time full-history catchup.

    Exports every captured session/message from this machine and writes
    `<destination>/<machine_name>/archive.json.gz`. Other machines pulling
    the same destination will import the archive once via UUID dedup; from
    then on the rolling 30-day captured_sessions.json.gz keeps everything
    current.

    Args:
        machine_name: This machine's name; becomes the subfolder name.
        dest: Destination root. None or "github" means push to the existing
            ~/.cc-sync/ git remote. Anything else is treated as a filesystem
            path (external drive, iCloud-mounted folder, NAS, etc.).

    Returns:
        (ok, message) tuple.
    """
    migrate_legacy_paths()
    use_github = dest is None or (isinstance(dest, str) and dest.lower() == "github")

    if use_github:
        if not SYNC_DIR.exists():
            return False, "Sync not configured — run interactive setup first"
        # git pull --rebase to fold in any concurrent pushes from other machines
        subprocess.run(["git", "pull", "--rebase"], cwd=SYNC_DIR,
                       capture_output=True, text=True)
        target_root = SYNC_DIR
    else:
        target_root = Path(dest).expanduser().resolve()
        try:
            target_root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"Cannot create destination {target_root}: {e}"

    machine_dir = target_root / "machines" / machine_name
    machine_dir.mkdir(parents=True, exist_ok=True)

    try:
        import gzip
        from cc_anywhere.sqlite_capture import export_for_sync
        # No machine_name filter — archive every session in the local DB,
        # not just ones captured on this machine. The local DB is the
        # backup target; UUID dedup makes cross-machine re-import a no-op.
        captured = export_for_sync(
            since="1970-01-01T00:00:00.000Z",
            content_limit=5000,
        )
    except Exception as e:
        return False, f"Could not export archive: {e}"

    if not (captured["sessions"] or captured["messages"]):
        return False, "No captured sessions to archive — run --capture first"

    archive_path = machine_dir / "archive.json.gz"
    try:
        data = json.dumps(captured, default=str).encode("utf-8")
        with gzip.open(archive_path, "wb") as gz:
            gz.write(data)
    except OSError as e:
        return False, f"Could not write archive: {e}"

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    summary = (f"Archived {len(captured['sessions'])} sessions, "
               f"{len(captured['messages'])} messages "
               f"({size_mb:.1f} MB compressed) -> {archive_path}")

    if use_github:
        if size_mb > GITHUB_FILE_ERROR_MB:
            archive_path.unlink(missing_ok=True)
            return False, (
                f"Archive is {size_mb:.1f} MB — too large for GitHub "
                f"(100 MB/file hard limit). Push to a filesystem destination "
                f"instead: cc-anywhere --sync-archive --to <path>"
            )
        warning = ""
        if size_mb > GITHUB_FILE_WARN_MB:
            warning = (
                f"\n  Warning: archive is {size_mb:.1f} MB — within "
                f"{GITHUB_FILE_ERROR_MB - size_mb:.1f} MB of GitHub's per-file "
                f"limit. Plan to chunk by year or move to a filesystem "
                f"destination soon."
            )

        subprocess.run(["git", "add", "-A"], cwd=SYNC_DIR,
                       capture_output=True, text=True)
        subprocess.run(["git", "commit", "-m",
                        f"Archive snapshot from {machine_name}"],
                       cwd=SYNC_DIR, capture_output=True)
        result = subprocess.run(["git", "push", "-u", "origin", "main"],
                                cwd=SYNC_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            result = subprocess.run(["git", "push", "-u", "origin", "master"],
                                    cwd=SYNC_DIR, capture_output=True, text=True)
        if result.returncode != 0:
            return False, result.stderr.strip() or "git push failed"
        return True, summary + warning + "\nPushed to GitHub."

    return True, summary


def normalize_machine_name(name: str) -> str:
    """Normalize machine name for comparison."""
    for suffix in [".local", ".localdomain", ".lan"]:
        name = name.replace(suffix, "")
    return name.lower()


def get_other_machines(current_machine: str) -> list:
    """Get data from other synced machines."""
    machines = []
    machines_dir = SYNC_DIR / "machines"

    if not machines_dir.exists():
        return machines

    current_normalized = normalize_machine_name(current_machine)

    for machine_folder in machines_dir.iterdir():
        if not machine_folder.is_dir():
            continue

        state_file = machine_folder / "state.json"
        if not state_file.exists():
            continue

        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            folder_normalized = normalize_machine_name(machine_folder.name)
            machines.append({
                "name": machine_folder.name,
                "is_current": folder_normalized == current_normalized,
                "updated": state.get("updated"),
                "projects": state.get("projects", {})
            })
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read state for %s: %s", machine_folder.name, e)

    return machines
