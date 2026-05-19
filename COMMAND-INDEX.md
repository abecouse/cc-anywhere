# Command Index

*The practical command sheet for `cc-anywhere`. When in doubt, play the opener.*

Current product order:

1. Nail the CLI
2. Nail agent integration
3. Add extra surfaces later

---

## The Opener

```bash
pip install cc-anywhere
cc-anywhere --capture
cc-anywhere --ask "what did we decide about auth?"
```

What it does:

1. Install the local memory CLI
2. Capture Claude Code + Claude Cowork + Codex CLI sessions into SQLite
3. Ask your AI coding memory a natural-language question

The same flow works through a coding agent. The user can ask the agent for memory, and the agent can call `cc-anywhere` directly.

Once installed, any coding agent with local CLI access can theoretically call the same commands. Claude Code and Codex are the first real examples; other agents can plug into the same retrieval path if they can execute shell commands and read results.

---

## Save The Memory

Run these manually when you want the latest sessions searchable now:

```bash
cc-anywhere --capture
cc-anywhere --index-semantic
```

Signal chain:

```text
Claude Code / Claude Cowork / Codex logs
        │
        ▼
cc-anywhere --capture
        │
        ▼
SQLite memory DB + FTS5
        │
        ▼
cc-anywhere --index-semantic
        │
        ▼
Searchable recall
```

Future alias idea:

```bash
cc-anywhere save
```

---

## Search And Recall

Keyword search:

```bash
cc-anywhere --db-search "verification customer"
```

Natural-language search:

```bash
cc-anywhere --semantic-search "when did we discuss the MCP wrapper?"
```

Answer-shaped recall:

```bash
cc-anywhere --ask "what did we decide about natural language search?"
```

Read the full indexed chunk behind a search result:

```bash
cc-anywhere --view <chunk_id>
```

Jump from an indexed chunk back to the raw JSONL transcript source:

```bash
cc-anywhere --source <chunk_id>
```

Agent retrieval pattern:

```bash
cc-anywhere --ask "what did we decide about auth?"
cc-anywhere --view <chunk_id>
cc-anywhere --source <chunk_id>
```

That gives an agent search, full chunk drill-down, and raw transcript provenance without direct DB access.

Backfill source pointers for memories captured before transcript provenance existed:

```bash
cc-anywhere --backfill-sources
cc-anywhere --index-semantic --rebuild
```

Full semantic rebuild, only when needed:

```bash
cc-anywhere --index-semantic --rebuild
```

---

## Dashboard And Project Views

Interactive dashboard:

```bash
cc-anywhere
```

Project list:

```bash
cc-anywhere --list
```

In the dashboard:

```text
s  setup sync
u  upload / push metadata
d  download / pull metadata
/  keyword search
t  stats
w  weekly digest
m  monthly digest
c  capture
f  DB search
r  refresh
q  quit
```

---

## Stats And Digests

Database stats:

```bash
cc-anywhere --db-stats
```

Weekly digest:

```bash
cc-anywhere --weekly
```

Monthly digest:

```bash
cc-anywhere --monthly
```

Git correlation report:

```bash
cc-anywhere --git-analysis 30
```

---

## Sync And Backup

Push local metadata to the configured sync repo:

```bash
cc-anywhere --sync
```

Pull synced metadata:

```bash
cc-anywhere --pull
```

Create a local history backup:

```bash
cc-anywhere --backup
```

Sync setup is currently done from the interactive dashboard with `s`.

---

## Automation Hook

Recommended Claude Code Stop hook snippet:

```bash
if command -v cc-anywhere >/dev/null 2>&1; then
  cc-anywhere --capture        >> "$HOME/.claude-memory-archive/.last-capture.log"        2>&1 || true
  cc-anywhere --index-semantic >> "$HOME/.claude-memory-archive/.last-index-semantic.log" 2>&1 || true
fi
```

This keeps keyword search and natural-language recall current after every session.

---

## Meta Commands

Help:

```bash
cc-anywhere --help
```

Version:

```bash
cc-anywhere --version
```

Full guide:

```bash
cc-anywhere --help-guide
```

---

## Setlist Rule

No new instruments until the opener lands:

```bash
cc-anywhere --capture
cc-anywhere --ask "what did we decide about auth?"
```
