#!/usr/bin/env python3
"""
Help page for cc-anywhere.
"""

HELP_GUIDE = """
╔══════════════════════════════════════════════════════════════════╗
║                        cc-anywhere                               ║
║         Access your Claude Code sessions from anywhere           ║
╚══════════════════════════════════════════════════════════════════╝

WHY THIS EXISTS
───────────────
  Claude Code stores your conversation history and todos in ~/.claude/
  This data is NOT part of your project repos - it stays on one machine.

  When you switch machines, even if you git pull your code, Claude Code
  doesn't know what you were working on. You'd have to re-explain everything.

  cc-anywhere syncs this Claude-specific data between machines:
    • Your prompts (what you asked Claude in each project)
    • Your todos (tasks Claude is tracking)
    • A dashboard of ALL your Claude Code projects

  So when you sit down at another machine, you can pick up where you left off
  without re-explaining your tasks or duplicating work Claude already knows about.

HOW TO USE IT
─────────────
  1. Create a private repo on GitHub called 'cc-sync'
  2. Run this tool and press 's' to set up sync
  3. Enter your GitHub username (repo defaults to 'cc-sync')
  4. Press 'u' to upload your projects
  5. On another machine, run cc-anywhere and press 'd' to download

  That's it. Your Claude context syncs across multiple machines.
  Git will prompt you to log in if not already authenticated.

DASHBOARD COMMANDS
──────────────────
  1-15    Select a project (view details, copy pickup prompt)
  s       Setup sync (set machine name, configure GitHub repo)
  u       Upload - push local state to remote
  d       Download - pull latest from remote
  /       Search conversation history
  t       Stats - show usage heatmap and statistics
  c       Capture sessions into SQLite database
  f       Full-text search across captured sessions
  r       Refresh project list
  q       Quit

WHAT GETS SYNCED
────────────────
  ✓ Your prompts (what you typed to Claude)
  ✓ Your todos (open, in progress, completed)
  ✓ Project metadata (name, path, last activity)

  ✗ Claude's responses (not stored by Claude Code)
  ✗ Your actual code (use git for that)

SESSION CAPTURE (SQLite)
───────────────────────
  Claude Code conversations get truncated as context windows compress.
  Session capture stores your messages and Claude's text replies in a
  local SQLite database, creating a searchable audit trail that survives
  context compression. Tool calls/results are skipped to keep it readable.

  Interactive commands:
    c       Run capture (scans ~/.claude/projects/ for new messages)
    f       Full-text search across all captured sessions

  CLI flags:
    --capture            Run session capture
    --db-search "query"  Full-text search captured sessions
    --db-stats           Show capture database statistics

  The database is stored at ~/.cc-anywhere-sessions.db
  Captured sessions are included in sync (upload/download).

OPTIONAL: SESSION LOGGING
─────────────────────────
  Want to capture Claude's responses too? Enable logging:

  --logging on         Turn on logging
  --start              Start Claude Code (captures full conversation)
  --logs "query"       Search through logged sessions
  --logging off        Turn off logging
  --cleanup 30         Delete logs older than 30 days

  Logs are stored locally at ~/.claude-logs/

COMMAND LINE OPTIONS
────────────────────
  --search "query"     Search all conversations from any terminal
  --list               Simple list view (no interactive mode)
  --capture            Capture sessions to SQLite database
  --db-search "query"  Full-text search captured sessions
  --db-stats           Show capture database statistics
  --json               Output as JSON (for scripting)
  --set-name "Name"    Name this machine for cc-anywhere (e.g. "Work Laptop")
  --help-guide         Show this help

GOOD TO KNOW
────────────
  Projects are named after the folder where you started Claude Code.
  If you run 'claude' from ~/Projects/my-app, all prompts in that
  session are logged under "my-app" - even if you talk about other projects.

  When you select a project, you can see all your prompts from that session.

SYNCING TIPS
────────────
  • Use the SAME FOLDER NAME on all machines for the same project.
    If it's "my-app" on one machine, use "my-app" on others too.

  • Syncing is MANUAL - it won't auto-sync in the background.
    Press 'u' to upload before switching machines.
    Press 'd' to download when you arrive on a new machine.

HOW IT WORKS
────────────
  ┌─────────────────────────────────────────────────────────────────┐
  │                        MACHINE A (leaving)                      │
  │                                                                 │
  │   Working in Claude Code...                                     │
  │            │                                                    │
  │            ▼                                                    │
  │   ┌─────────────────┐                                           │
  │   │   cc-anywhere   │ ──► Press 'u' to upload                   │
  │   └─────────────────┘                                           │
  │            │                                                    │
  │            ▼                                                    │
  │   ┌─────────────────┐                                           │
  │   │   GitHub Repo   │  (your private cc-sync repo)          │
  │   │  cc-sync       │                                           │
  │   └─────────────────┘                                           │
  └─────────────────────────────────────────────────────────────────┘
                         │
                         │  git push / pull
                         ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                       MACHINE B (arriving)                      │
  │                                                                 │
  │   ┌─────────────────┐                                           │
  │   │   cc-anywhere   │ ──► Press 'd' to download                 │
  │   └─────────────────┘                                           │
  │            │                                                    │
  │            ▼                                                    │
  │   Select project ──► Press 'c' to copy pickup prompt            │
  │            │                                                    │
  │            ▼                                                    │
  │   Open Claude Code in project folder                            │
  │            │                                                    │
  │            ▼                                                    │
  │   Paste prompt ──► Claude has full context!                     │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘

SETUP (one time per machine)
────────────────────────────
  1. Create a PRIVATE repo on GitHub called 'cc-sync'
  2. Run: pip install cc-anywhere
  3. Run: cc-anywhere
  4. Press 's' to set up sync
  5. Enter your GitHub username
  6. Press Enter for default repo name (cc-sync)
  7. Done! Now use 'u' to upload, 'd' to download

ONE-TIME FULL-HISTORY CATCHUP
─────────────────────────────
  Regular sync only ships the last 30 days. To share full history once
  with another machine (or to take a complete off-disk backup):

  cc-anywhere --sync-archive                    # push to your cc-sync repo
  cc-anywhere --sync-archive --to <path>        # write to filesystem

  --to accepts any directory: external SSD (/Volumes/Backup),
  iCloud Drive, Dropbox, NAS share, etc. Layout is
  <path>/machines/<machine>/archive.json.gz, identical to GitHub.

  On other machines, cc-anywhere --pull picks up archive.json.gz
  automatically (UUID dedup makes re-running harmless).

EVEN ON A SINGLE MACHINE
────────────────────────
  Even if you only use one computer, cc-anywhere gives you:

  • Dashboard of all your Claude Code projects
  • Search across ALL your prompts and Claude's responses
  • View conversation summaries (Claude's recaps)
  • See your todos across projects
  • Backup your Claude Code history to GitHub

ACROSS MULTIPLE MACHINES
────────────────────────
  Sync your Claude Code sessions between machines via GitHub:

  • Full conversation history (your prompts + Claude's responses)
  • Summaries - Claude's recaps when context runs out
  • Todos - pick up where you left off
  • Pickup prompt - paste into Claude Code for instant context

DATA & PRIVACY
──────────────
  All data stays on YOUR machines and YOUR private GitHub repo.
  Nothing is sent to us. No analytics. No telemetry.

  ~/.claude/                      Claude Code's data (read only)
  ~/.cc-sync/                 Your sync repo (pushed to YOUR GitHub)
  ~/.claude-logs/                 Session logs (local only, optional)
  ~/.cc-anywhere.json         Config file
  ~/.cc-anywhere-sessions.db  SQLite capture database

───────────────────────────────────────────────────────────────────
           github.com/abecouse/cc-anywhere
"""


def show_help_guide():
    """Show detailed help guide."""
    print(HELP_GUIDE)
