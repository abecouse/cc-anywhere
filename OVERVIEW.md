# Claude Anywhere - Overview

## What It Does

**cc-anywhere** gives you visibility into your Claude Code projects:

1. **Dashboard** - See all your projects, prompt counts, todos, last activity
2. **Search** - Find anything across all conversations (your prompts + Claude's responses)
3. **Cross-machine sync** - Share context between machines via GitHub
4. **Analytics** - Usage stats per machine and combined across all machines

## How It Relates to Claude Code on the Web

Claude Code on the web (claude.ai/code) is **repo-centric** - it clones GitHub repos and runs in cloud VMs. Great for kicking off tasks remotely.

**cc-anywhere** is **conversation-centric** - it syncs the *discussion* about the code:
- "We tried approach X but it failed because Y"
- "The user prefers Z pattern"
- "These are the open todos"

This context lives in conversation history, not in the repo itself. When you switch machines, you want Claude to understand the full context of your work, not just the current code state.

| Feature | Claude Code Web | cc-anywhere |
|---------|----------------|-----------------|
| Cross-machine work | Same repo via GitHub | Any project, full context |
| Conversation history | Not synced | Synced (recent + metadata) |
| Search past sessions | No | Yes (local) |
| Usage analytics | No | Yes (per-machine + combined) |
| Offline access | No | Yes (local backups) |

## The Key Insight

Claude Code stores data in two places:

| Location | Contents | Size |
|----------|----------|------|
| `~/.claude/history.jsonl` | Your prompts only | Small |
| `~/.claude/projects/*.jsonl` | Full conversations (prompts + Claude's responses) | Large |

When you ask "how did we fix X?", the answer is usually in what Claude said, not what you asked. cc-anywhere searches both.

## How Sync Works (v1)

### What syncs to GitHub (lightweight)

```
~/.cc-sync/
├── machines/
│   ├── MacBook/
│   │   └── state.json       # Project metadata for this machine
│   ├── Mac-mini/
│   │   └── state.json
│   └── Work-Desktop/
│       └── state.json
└── projects/
    └── my-project/
        ├── MacBook/
        │   ├── session_state.json  # Recent history, todos
        │   ├── CONTEXT.md          # Human-readable summary
        │   └── PICKUP.md           # Prompt to continue session
        └── Mac-mini/
            └── ...
```

**state.json contains:**
- Project list with prompt counts, session counts
- Last active timestamps
- Recent context (last ~20 messages per project)
- Active todos

**Total sync size:** ~KB per machine (not MB)

### What stays local (large files)

```
~/.claude/projects/           # Full conversation JSONL files
~/.cc-anywhere/backups/   # Monthly full backups
```

Full conversations are too large for GitHub (can be 25MB+ per session). They stay local but are backed up monthly.

### Why this approach?

| Concern | Solution |
|---------|----------|
| GitHub 50MB file limit | Only sync small metadata files |
| GitHub ~1GB repo limit | Lightweight sync = unlimited machines |
| Need full search | Search works locally on full data |
| Data loss protection | Monthly local backups |
| Cross-machine context | Recent messages + todos sync |

## Machine Naming

Each machine gets a friendly name for identification:

```bash
# Set during first sync setup
cc-anywhere --setup

# Or change anytime
cc-anywhere --set-name "Work-Laptop"
```

Names appear in:
- Analytics ("MacBook: 1,500 prompts")
- Sync status ("Last sync from Mac-mini: 5 min ago")
- Project views ("Also on: Work-Desktop")

## Commands

```bash
cc-anywhere              # Interactive dashboard
cc-anywhere --search X   # Search all conversations
cc-anywhere --machines   # View projects by machine
cc-anywhere --project X  # View specific project
cc-anywhere --stats      # Usage analytics (local + all machines)
cc-anywhere --setup      # Initial setup (machine name + GitHub)
cc-anywhere --set-name X # Change machine name
cc-anywhere --backup     # Create manual backup
cc-anywhere --list       # Simple list view
```

## Dashboard Keys

| Key | Action |
|-----|--------|
| `u` | Upload - sync this machine to GitHub |
| `d` | Download - pull other machines' data |
| `s` | Setup sync with GitHub |
| `c` | Copy context for current project |
| `/` | Search conversations |
| `a` | Analytics |
| `q` | Quit |

## Architecture

```
~/.claude/                    # Claude Code's data (read only)
├── history.jsonl            # Your prompts
├── projects/                # Full conversations
│   └── -path-to-project/    # JSONL files per session
└── CLAUDE.md                # Global instructions

~/.cc-anywhere.json       # Config (machine name, settings)

~/.cc-anywhere/          # Our data
├── state.json              # Local project state cache
└── backups/                # Monthly backups
    └── 2025-12.json        # Full history + conversations

~/.cc-sync/              # Git sync repo (GitHub)
├── machines/               # State from each machine
│   └── MacBook/
│       └── state.json
└── projects/               # Per-project context
    └── my-project/
        └── MacBook/
            ├── session_state.json
            ├── CONTEXT.md
            └── PICKUP.md
```

## Workflow Example

```
Mac mini (home):
  Working on "api-server" project
  500 prompts, 3 open todos
  ↓
  Press 'u' to upload
  ↓
  Syncs to GitHub: state.json + CONTEXT.md

MacBook (coffee shop):
  Press 'd' to download
  ↓
  Dashboard shows: "api-server (Mac-mini): 500 prompts, 3 todos"
  ↓
  Select project, press 'c' to copy context
  ↓
  Paste into Claude: "Continuing from Mac-mini..."
  ↓
  Claude understands: recent work, open todos, decisions made
```

## Backups

Automatic monthly backups of full history:

```
~/.cc-anywhere/backups/
├── 2025-11.json    # November 2025
├── 2025-12.json    # December 2025
└── ...
```

Each backup contains:
- All prompts from history.jsonl
- Full conversations from projects/
- All todos
- Project metadata

This protects against Claude Code's history getting truncated (which can happen).

## Summary

| Feature | What it does |
|---------|--------------|
| Dashboard | See all projects at a glance |
| Search | Find anything in your history (prompts + responses) |
| Cross-machine | Sync metadata + context via GitHub |
| Analytics | Per-machine and combined usage stats |
| Backups | Monthly local backups of full history |
| Machine naming | Friendly names for each device |
