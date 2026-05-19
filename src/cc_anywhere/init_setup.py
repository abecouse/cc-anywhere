"""
One-command install for cc-anywhere.

Usage:
    cc-anywhere --init

What this does, idempotently and safely:

  1. Run an initial `cc-anywhere --capture` so the local DB has data on
     first launch.
  2. Merge two hooks into ~/.claude/settings.json (creating the file if
     missing, never replacing existing hooks):
       - SessionStart: cc-anywhere --read --json-context
         → injects past-conversation recall + a "use this tool" instruction
           into Claude Code's context at every session start.
       - Stop: cc-anywhere --capture
         → captures the just-ended session into the SQLite DB.
  3. Set up periodic capture as an hourly safety net:
       - macOS: a LaunchAgent plist at
         ~/Library/LaunchAgents/com.cc-anywhere.periodic.plist
         (loaded with launchctl).
       - Linux: a crontab line `0 * * * * cc-anywhere --capture`.
       - Windows: prints the schtasks command to run manually
         (most Windows users running Claude Code do so via WSL, where
         the Linux flow above already works).

Design notes:
  - Strict idempotency. Every step checks for prior presence and skips
    cleanly. Re-running is safe.
  - Never breaks workflow. We never overwrite an existing SessionStart
    or Stop hook; we add alongside, leaving any existing entries untouched.
  - Manual CLI commands keep working unchanged. cc-anywhere --ask,
    --semantic-search, --capture, --view, --source remain the user-facing
    surface; --init is just the wiring to make them auto-fire too.
"""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path

CC_ANYWHERE_BIN_FALLBACK = "cc-anywhere"

LAUNCHD_LABEL = "com.cc-anywhere.periodic"
CRON_MARKER = "# cc-anywhere periodic capture"


# ============ OS detection ============


def detect_os() -> str:
    s = platform.system().lower()
    if s == "darwin":
        return "macos"
    if s == "linux":
        return "linux"
    if s == "windows":
        return "windows"
    return s


def cc_anywhere_path() -> str:
    """Return an absolute path to cc-anywhere, falling back to the bare name."""
    found = shutil.which("cc-anywhere")
    return found if found else CC_ANYWHERE_BIN_FALLBACK


# ============ ~/.claude/settings.json hook merging ============


def session_start_command(bin_path: str) -> str:
    """Return the SessionStart hook command using a stable binary path."""
    quoted = shlex.quote(bin_path)
    return f"{quoted} --read --json-context 2>/dev/null"


def stop_command(bin_path: str) -> str:
    """Return the Stop hook command using a stable binary path."""
    return f"{shlex.quote(bin_path)} --capture"


def _hook_command_present(settings: dict, event: str, *needles: str) -> bool:
    """True iff any hook on this event runs a command containing all needles."""
    for matcher in settings.get("hooks", {}).get(event, []):
        for hook in matcher.get("hooks", []):
            command = hook.get("command", "")
            if (
                hook.get("type") == "command"
                and all(needle in command for needle in needles)
            ):
                return True
    return False


def _add_hook(settings: dict, event: str, hook: dict) -> None:
    """Append a hook entry to the given event, creating structure as needed."""
    settings.setdefault("hooks", {})
    settings["hooks"].setdefault(event, [])
    if not settings["hooks"][event]:
        settings["hooks"][event].append({"hooks": []})
    # Use the first matcher entry — Claude Code allows multiple commands
    # under a single matcher block.
    settings["hooks"][event][0].setdefault("hooks", [])
    settings["hooks"][event][0]["hooks"].append(hook)


def merge_claude_settings(
    settings_path: Path | None = None,
    bin_path: str | None = None,
) -> dict[str, str]:
    """Add cc-anywhere hooks to ~/.claude/settings.json.

    Idempotent: a hook is only added if no existing hook on the same event
    already references the cc-anywhere SessionStart / Stop behavior.

    Returns a status dict: {"session_start": "added"|"present"|"skipped",
                            "stop":         "added"|"present"|"skipped",
                            "settings_path": "<path>"}.
    """
    settings_path = settings_path or (Path.home() / ".claude" / "settings.json")
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                settings = {}
        except (json.JSONDecodeError, OSError):
            settings = {}
    else:
        settings = {}

    bin_path = bin_path or cc_anywhere_path()
    start_cmd = session_start_command(bin_path)
    stop_cmd = stop_command(bin_path)
    status = {"settings_path": str(settings_path)}

    # SessionStart — inject memory recall as Claude Code context
    for matcher in settings.get("hooks", {}).get("SessionStart", []):
        for hook in matcher.get("hooks", []):
            command = hook.get("command", "")
            if (
                hook.get("type") == "command"
                and "--json-context" in command
                and "anywhere" in command
            ):
                if "--read" in command:
                    status["session_start"] = "present"
                else:
                    hook["command"] = start_cmd
                    status["session_start"] = "updated"
                break
        if "session_start" in status:
            break
    if "session_start" not in status:
        _add_hook(
            settings,
            "SessionStart",
            {"type": "command", "command": start_cmd, "timeout": 15},
        )
        status["session_start"] = "added"

    # Stop — capture the ending session
    if _hook_command_present(settings, "Stop", "--capture", "anywhere"):
        status["stop"] = "present"
    else:
        _add_hook(
            settings,
            "Stop",
            {
                "type": "command",
                "command": stop_cmd,
                "timeout": 30,
                "async": True,
            },
        )
        status["stop"] = "added"

    settings_path.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )
    return status


# ============ Periodic capture: macOS launchd ============


def _launchd_plist(cc_anywhere_bin: str, log_dir: Path) -> str:
    log_path = log_dir / ".periodic.launchd.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{cc_anywhere_bin}</string>
    <string>--capture</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <false/>
  <key>StandardOutPath</key>
  <string>{log_path}</string>
  <key>StandardErrorPath</key>
  <string>{log_path}</string>
</dict>
</plist>
"""


def setup_macos_periodic() -> str:
    """Install the LaunchAgent for hourly capture. Returns status string."""
    plist_dir = Path.home() / "Library" / "LaunchAgents"
    plist_dir.mkdir(parents=True, exist_ok=True)
    plist_path = plist_dir / f"{LAUNCHD_LABEL}.plist"

    log_dir = Path.home() / ".cc-memory-archive"
    log_dir.mkdir(parents=True, exist_ok=True)

    bin_path = cc_anywhere_path()
    contents = _launchd_plist(bin_path, log_dir)

    if plist_path.exists() and plist_path.read_text(encoding="utf-8") == contents:
        return "present"

    plist_path.write_text(contents, encoding="utf-8")

    # Reload the agent so the new schedule takes effect immediately.
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        capture_output=True, text=True,
    )
    result = subprocess.run(
        ["launchctl", "load", str(plist_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return f"written-but-load-failed: {result.stderr.strip() or 'unknown'}"
    return "added"


# ============ Periodic capture: Linux crontab ============


def setup_linux_periodic() -> str:
    """Append an hourly cron line if not already present. Returns status."""
    if shutil.which("crontab") is None:
        return "skipped: crontab not available"

    bin_path = cc_anywhere_path()
    cron_line = f"0 * * * * {bin_path} --capture >/dev/null 2>&1  {CRON_MARKER}"

    existing = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    current = existing.stdout if existing.returncode == 0 else ""

    if CRON_MARKER in current:
        return "present"

    new_crontab = (current + ("\n" if current and not current.endswith("\n") else "")
                   + cron_line + "\n")
    install = subprocess.run(
        ["crontab", "-"], input=new_crontab, capture_output=True, text=True
    )
    if install.returncode != 0:
        return f"failed: {install.stderr.strip() or 'crontab install error'}"
    return "added"


# ============ Periodic capture: Windows ============


def windows_periodic_instructions() -> str:
    """Return the schtasks command Windows users run manually."""
    bin_path = cc_anywhere_path()
    return (
        "Windows native: Task Scheduler is not auto-configured. To set up "
        "hourly capture, run the following in an Administrator PowerShell "
        "or cmd window:\n\n"
        f'  schtasks /create /sc hourly /tn "cc-anywhere capture" '
        f'/tr "{bin_path} --capture" /f\n\n'
        "Or use cc-anywhere on WSL, where --init configures cron automatically."
    )


# ============ Initial capture ============


def run_initial_capture() -> dict[str, int]:
    """Populate the DB on first install AND build the semantic search index.

    Both pieces are required for `cc-anywhere --ask` to return useful
    results. Capture loads raw messages; the semantic index is what
    makes recall work. Safe to re-run; both are incremental.
    """
    from cc_anywhere.sqlite_capture import (
        capture_sessions,
        capture_codex_sessions,
        capture_gemini_sessions,
    )
    from cc_anywhere.semantic import ensure_semantic_schema, rebuild_semantic_index
    from cc_anywhere.sqlite_capture import get_db
    cc = capture_sessions()
    cx = capture_codex_sessions()
    gm = capture_gemini_sessions()
    new_messages = (cc.get("new_messages", 0)
                    + cx.get("new_messages", 0)
                    + gm.get("new_messages", 0))

    indexed_chunks = 0
    indexed_messages = 0
    semantic_chunks = 0
    with get_db() as db:
        ensure_semantic_schema(db)
        row = db.execute("SELECT COUNT(*) AS n FROM semantic_chunks").fetchone()
        semantic_chunks = row["n"] if row else 0

    if new_messages > 0 or semantic_chunks == 0:
        ix = rebuild_semantic_index(full_rebuild=False)
        indexed_chunks = ix.get("chunks", 0)
        indexed_messages = ix.get("messages", 0)

    return {
        "claude_code_messages": cc.get("new_messages", 0),
        "codex_messages": cx.get("new_messages", 0),
        "gemini_messages": gm.get("new_messages", 0),
        "indexed_chunks": indexed_chunks,
        "indexed_messages": indexed_messages,
    }


# ============ Orchestrator ============


def run_init() -> int:
    """End-to-end install. Prints progress, returns process exit code."""
    print("cc-anywhere init — setting up your local memory layer\n")

    osname = detect_os()
    print(f"  OS: {osname}")
    bin_path = cc_anywhere_path()
    print(f"  cc-anywhere binary: {bin_path}")
    print()

    # 1. Initial capture + semantic index so the DB has content AND
    #    `cc-anywhere --ask` returns useful results immediately.
    print("Step 1/3: Capturing sessions and building semantic index...")
    try:
        cap = run_initial_capture()
        total_msgs = (cap["claude_code_messages"]
                      + cap["codex_messages"]
                      + cap["gemini_messages"])
        print(f"  captured {total_msgs} new messages "
              f"(claude={cap['claude_code_messages']}, "
              f"codex={cap['codex_messages']}, "
              f"gemini={cap['gemini_messages']})")
        if cap["indexed_chunks"] > 0:
            print(f"  indexed {cap['indexed_chunks']} new chunks "
                  f"from {cap['indexed_messages']} messages")
        elif total_msgs == 0:
            print("  (DB already current — no new content to index)")
    except Exception as e:
        print(f"  warning: initial capture failed: {e}")
    print()

    # 2. Merge hooks into ~/.claude/settings.json.
    print("Step 2/3: Wiring hooks into ~/.claude/settings.json...")
    try:
        s = merge_claude_settings(bin_path=bin_path)
        print(f"  SessionStart hook: {s['session_start']}")
        print(f"  Stop hook:         {s['stop']}")
        print(f"  settings file:     {s['settings_path']}")
    except Exception as e:
        print(f"  warning: settings merge failed: {e}")
    print()

    # 3. Periodic capture safety net.
    print("Step 3/3: Setting up hourly capture safety net...")
    if osname == "macos":
        result = setup_macos_periodic()
        print(f"  launchd: {result}")
    elif osname == "linux":
        result = setup_linux_periodic()
        print(f"  cron:    {result}")
    elif osname == "windows":
        print()
        print(windows_periodic_instructions())
    else:
        print(f"  skipped: no scheduler integration for {osname}")
    print()

    print("Done. Open a fresh Claude Code session — recall fires automatically.")
    print()
    print("Manual commands stay available:")
    print("  cc-anywhere --ask 'what did we decide about X?'")
    print("  cc-anywhere --semantic-search 'topic'")
    print("  cc-anywhere --capture")
    print("  cc-anywhere --sync-archive       # full-history backup")
    return 0
