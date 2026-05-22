# cc-anywhere

**Memory built for AI coding.** &nbsp;·&nbsp; Pick work back up with context, not guesswork.

> I wanted to remember what we discussed about my projects, and couldn't find past discussions, decisions, or feedback. The AI agents also seemed to forget.
>
> So I built cc-anywhere — for me and the AI coding agents. One memory layer; both audiences read it the same way.

Every Claude Code, Claude Cowork, Codex CLI / Desktop, and Gemini CLI session you run gets captured into a fast local SQLite database with full-text and natural-language search. Then you can ask any future session — Claude, Codex, Gemini, anything that can shell out to a CLI — *"what did we decide about auth last week?"* and get the actual past conversation back.

---

## Features

- **Automatic capture** — every Claude Code, Codex, Cowork & Gemini CLI session is saved to a local SQLite database on session-end and hourly. Nothing to remember.
- **Search your history** — `--ask` (by topic *or* time), `--search` (keyword + related-term matching), `--view` (open the full transcript), `--source` (jump to the raw line on disk).
- **Cross-tool memory** — one shared database across all your coding agents, so Claude Code can pick up what Codex did, and vice versa.
- **Digests** — `--daily` / `--weekly` / `--monthly` activity reports with per-project breakdowns and daily-activity charts: *what did I actually work on this week?*
- **Local-first & private** — no cloud, no API key, no model inside. Your conversations stay in a SQLite file you own.
- **Cross-machine sync** *(opt-in)* — bring-your-own-storage: a private GitHub repo, an external SSD, or an iCloud / Dropbox / NAS folder.
- **Agent-callable** — the integration contract is plain stdout. Any agent with command-line access can use it — no MCP, no SDK, no plugin.

---

## The 30-second demo

```bash
pip install cc-anywhere
cc-anywhere --capture
cc-anywhere --ask "what did we decide about auth?"
```

That's the whole thing. The third command searches every Claude Code, Claude Cowork, and Codex session you've ever had on this machine and returns the conversations that match — ranked, with project, speaker, and timestamp.

The first time you run it, search will surface conversations from months ago that you'd half-forgotten. The moment people realize their entire AI coding history just became queryable is the moment this project earns its keep.

### Two query modes — `--ask` routes automatically

```bash
# Topical recall — name a subject, get the relevant past conversations
cc-anywhere --ask "what did we decide about pricing"

# Temporal recall — name a time window, get a chronological pull (no keywords needed)
cc-anywhere --ask "what was I working on today"
cc-anywhere --ask "this week"
cc-anywhere --ask "catch me up"
```

Wide windows (this week / month) return a daily rollup with drill-in hints so you can walk back into a specific day. Narrow windows return a per-session list with previews.

### Usage overview

```bash
cc-anywhere --usage
```

Modeled on Claude Code's `/usage` but with longer time horizons (not capped at 30 days), per-project breakdown, per-machine breakdown when sync data is present, and a marathon-sessions view.

### For LLM agents

```bash
cc-anywhere --llm-guide
```

Prints the full LLM-facing usage reference (query patterns, drill-in, when *not* to use, code-vs-conversation interplay). Reachable from any cwd, so an agent in any session can fetch it. `LLM-CHEATSHEET.md` at the repo root mirrors the same content for human reading.

---

## Why it exists

AI coding assistants are great at remembering inside a session and unaware of everything before it. ChatGPT memory is locked to ChatGPT. Claude memory is locked to Claude. Codex has no cross-tool memory at all. Vendors won't fix this — siloed memory is a feature of their lock-in, not a bug.

`cc-anywhere` is the local layer that gives that history back to you. User-owned, vendor-neutral, fully on your machine, free.

It also exposes that memory to coding agents. A human can use the CLI directly, or an agent can call `--ask`, `--view`, and `--source` on the user's behalf to recover project context before acting.

Once installed on a machine, any coding agent that can run local shell commands can theoretically use it as a memory layer. Today that means Claude Code and Codex are the concrete first-class examples; other CLI-capable agents can use the same interface as long as they can execute local commands and read the output.

---

## Install

One command on macOS and Linux (including WSL):

```bash
pip install cc-anywhere
cc-anywhere --init
```

`--init` is idempotent and does three things:

1. Captures every existing Claude Code / Codex / Gemini session into the local SQLite DB and builds the search index.
2. Wires SessionStart + Stop hooks into `~/.claude/settings.json` so future sessions auto-load past context and capture on exit. Existing hooks are preserved, never replaced.
3. Sets up an hourly capture safety net (launchd on macOS, cron on Linux).

Open a fresh Claude Code session — recall fires automatically.

### Platform support

| Platform | Install path | Notes |
|---|---|---|
| **macOS** | `pip install cc-anywhere && cc-anywhere --init` | Fully automatic. Uses `launchd` for hourly capture. |
| **Linux** | `pip install cc-anywhere && cc-anywhere --init` | Fully automatic. Uses `crontab` for hourly capture. |
| **Windows (WSL)** | `pip install cc-anywhere && cc-anywhere --init` (inside WSL) | Identical to Linux. Recommended Windows path. |
| **Windows (native)** | `pip install cc-anywhere && cc-anywhere --init`, then optional `schtasks` step below | Capture and search work natively. Hourly scheduler needs one manual step. |

### Native Windows: hourly capture step

`--init` configures the hooks and runs the initial capture. For the hourly safety net, run this once in PowerShell (no admin needed):

```powershell
schtasks /create /sc hourly /tn "cc-anywhere capture" /tr "cc-anywhere --capture" /f
```

You can skip this entirely — the Stop hook still captures after every Claude Code session ends, which covers most cases. The hourly job is a safety net for long-running sessions where Stop never fires.

### Manual commands work the same on every platform

Every command below is platform-independent:

```bash
cc-anywhere --ask "what did we decide about auth?"
cc-anywhere --search "topic"
cc-anywhere --capture
cc-anywhere --sync-archive       # full-history backup
cc-anywhere --view <chunk_id>
cc-anywhere --source <chunk_id>
```

### Requirements

Python 3.8+. Claude Code, Codex CLI, or Gemini CLI installed locally (whichever you use). `git` is required for cross-machine sync but not for local-only use.

`cc-anywhere` is the public-facing command name. `claude-anywhere` continues to work as a compatibility alias for users upgrading from earlier versions.

---

## Tips

Concrete things you might want to do once it's installed.

### Cross-machine sync (via your own private GitHub repo)

Create a private repo on GitHub named `cc-sync` (one-time, free). Then on each machine:

```bash
cc-anywhere
# press 's' for setup, enter your GitHub username, accept default repo name
```

After that:

```bash
cc-anywhere --sync   # push from this machine
cc-anywhere --pull   # receive from others
```

Sync is **manual** — you control when. A 30-day rolling slice goes to the repo per machine.

### Onboarding a fresh machine to your full history

On the *origin* machine (one-time):

```bash
cc-anywhere --sync-archive
```

That pushes your **entire** local history (not just 30 days) to the `cc-sync` repo. On the *new* machine:

```bash
pip install cc-anywhere
cc-anywhere --init
cc-anywhere --pull
```

The archive imports automatically. UUID dedup means you can re-run `--pull` any time without producing duplicates.

### Backing up to an external SSD

```bash
cc-anywhere --sync-archive --to /Volumes/Backup-SSD/cc-anywhere/
```

Idempotent — re-running just rewrites the archive with whatever's current. The same command works for any mounted destination.

### Backing up to iCloud / Dropbox / NAS, or on a schedule

The `--to` path can be any mounted folder — iCloud Drive, Dropbox, a NAS share. Automate it with a cron entry (e.g. weekly: `0 18 * * 5 cc-anywhere --sync-archive --to /Volumes/Backup-SSD/cc-anywhere/`).

### Checking what's actually captured

```bash
cc-anywhere --db-stats
```

Shows session count, message count, project count, DB size, earliest and latest captures.

### Asking your AI to use cc-anywhere mid-session

Just nudge it mid-conversation — *"check our past decisions on auth"* or *"what did we discuss about X?"* — and the AI runs `cc-anywhere --ask` for you (the `--init` instruction tells it to). The `SessionStart` hook also injects recent-project context at the start of each Claude Code session.

### What's local-only vs. what gets synced

| Stays local | Synced to your `cc-sync` repo |
|---|---|
| `~/.cc-anywhere-sessions.db` (full SQLite DB) | A 30-day rolling slice per machine via `--sync` |
| Semantic search index | The full archive snapshot via `--sync-archive` |
| Raw JSONL transcripts on disk | — |

`--sync` is for cross-machine continuity; `--sync-archive` is for off-disk backup and onboarding new machines. Both are manual; nothing leaves your machine without a command you ran.

### Build a permanent history — before it's gone

Without cc-anywhere, your AI coding history is at the mercy of each tool's retention policy. Claude Code's `cleanupPeriodDays` defaults to 30 days; Codex and Gemini have their own retention behaviors. **Anything pruned before cc-anywhere captures it is gone forever.** No recovery.

With cc-anywhere installed, every captured conversation is persisted permanently in your local SQLite database, independent of any tool's retention. You're actively building a long-term archive of your project work — preserved across sessions, machines, and assistants. The earlier you install, the more history you keep.

If you want maximum coverage of your existing data before cc-anywhere's first run, bump `cleanupPeriodDays` in `~/.claude/settings.json` to a larger value (e.g. `365`) first.

### What's not yet supported

Direct integrations with cloud-storage APIs (Cloudflare R2, S3, Backblaze B2) are planned but not yet implemented. Until then, mount the cloud storage as a folder on your machine and use the filesystem path with `--sync-archive --to <path>`.

---

## Presently supported

Today there are two different support questions:

1. **Which tools can `cc-anywhere` capture memory from?**
2. **Which tools can call `cc-anywhere` after it is installed?**

### Capture sources supported today

These transcript sources are captured and indexed now:

| Source | Status | Notes |
|---|---|---|
| Claude Code | ✅ | Main session transcripts under `~/.claude/projects/*.jsonl` |
| Claude Cowork | ✅ | Local-agent transcripts under `~/Library/Application Support/Claude/local-agent-mode-sessions/**/.claude/projects/*/*.jsonl` |
| Codex CLI / Codex Desktop | ✅ | Rollout logs under `~/.codex/sessions/.../*.jsonl` |

### Search assistants supported today

These tools can use `cc-anywhere` as a search assistant and memory layer today, as long as they can run local shell commands and read the output:

| Tool | Status | Notes |
|---|---|---|
| Claude Code | ✅ | Can call `--ask`, `--db-search`, `--search`, `--view`, and `--source` directly or through slash-command / memory workflows |
| Codex CLI | ✅ | Can call the same commands directly; the `threaded` skill makes this feel automatic |
| Codex Desktop | ✅ | Same local CLI contract as Codex CLI |
| Gemini CLI and other CLI-capable coding agents | In theory ✅ | They can use `cc-anywhere` as a search assistant once installed; capture support depends on their own local transcript format |

### Not captured yet

These are intentionally not first-class capture sources yet:

| Source | Status |
|---|---|
| Claude Code subagents (`*/subagents/*.jsonl`) | ⏳ planned |
| Codex child/agent traces outside rollout logs | ⏳ investigate |
| Cursor | ⏳ planned |
| Continue.dev | ⏳ planned |
| GitHub Copilot Chat | ⏳ planned |

The important distinction is that **calling** `cc-anywhere` and **being captured by** `cc-anywhere` are different things. A tool might be able to use it as a search assistant today even if we do not yet ingest that tool's own transcript history.

Capture is incremental and resilient to compaction (the JSONL on disk is append-only, so `/compact` never destroys history), and stores **text only** — tool calls and their outputs are skipped, keeping the index focused on what you and the assistant actually said.

---

## First five minutes

```bash
# Snapshot every session into the local DB (auto-builds the search index)
cc-anywhere --capture

# Search by keyword
cc-anywhere --db-search "verification customer"

# Or in natural language
cc-anywhere --ask "when did we discuss the MCP wrapper?"

# Drill into a result, then jump back to the raw transcript source
cc-anywhere --view <chunk_id>
cc-anywhere --source <chunk_id>

# See your project list
cc-anywhere --list

# Or open the interactive dashboard
cc-anywhere
```

That's the whole CLI surface that matters for daily use. The same commands also form a retrieval interface for coding agents.

---

## Make it automatic

You don't have to do anything — **`cc-anywhere --init` already wires this up:** a Stop hook captures on session end, and an hourly `launchd` / `cron` job is the safety net for long-running sessions where Stop never fires. Capture is incremental (only new messages are processed) and auto-builds the search index, so the cost stays small as your corpus grows.

If you'd rather script it yourself instead of using `--init`, just call `cc-anywhere --capture` from your own Stop hook — it's safe to run repeatedly.

---

## Pair with Claude Code and Codex

After `--init`, the Stop hook captures on exit and a `SessionStart` hook injects recent context plus an instruction to reach for cc-anywhere — so you can nudge mid-session (*"what did we decide last week?"*) and the agent runs `cc-anywhere --ask` for you.

Both Claude Code and Codex call the same local database, so memory written from one assistant is visible to the other.

The important architectural point is that agents do not need to inspect SQLite directly. They can use:

```bash
cc-anywhere --ask "what did we decide about auth?"
cc-anywhere --view <chunk_id>
cc-anywhere --source <chunk_id>
```

That makes `cc-anywhere` both a human CLI and a stable memory interface for coding agents.

In practical terms, any coding agent with local CLI access can call that interface after install. The retrieval contract is vendor-neutral even though the current capture sources started with Claude Code and Codex.

---

## What it does not do

- **It does not replace the AI's own memory.** It is the layer underneath that — the one that survives context windows, vendor changes, and machine reinstalls.
- **It does not send your data anywhere by default.** Everything is local. If you opt into sync, only lightweight metadata travels (full conversations stay on your machine).
- **It does not require any AI provider account.** It reads what's already on disk.

---

## Beyond the basics

A few more commands once the daily loop is working:

- **`cc-anywhere --weekly`** / **`--monthly`** — activity reports with project breakdowns and charts
- **`cc-anywhere --setup`** — wires up cross-machine sync via a private GitHub repo
- **`cc-anywhere`** (no args) — interactive dashboard

---

## Status

- **Version:** 1.2.0
- **License:** Apache-2.0
- **Source:** [github.com/abecouse/cc-anywhere](https://github.com/abecouse/cc-anywhere)
- **Website:** [cc-anywhere.com](https://cc-anywhere.com)
- **Author:** Abe Couse
