# Changelog

All notable changes to `cc-anywhere` are documented here.

---

## [1.3.2] — 2026-09-02

### Fix: sessions attributed to a machine that never existed

Three defects corrupted session provenance. All are fixed, and existing rows
can be repaired in place — no re-capture needed.

- **`Mac.localdomain` was recorded as the machine `Macdomain`.** `get_machine_name()`
  stripped domain suffixes with `str.replace`, testing `".local"` before
  `".localdomain"` — so the shorter suffix matched *inside* the longer one and
  left the tail behind. Suffixes are now matched longest-first and anchored with
  `endswith`, so only a genuine trailing suffix is removed. A host like
  `my.local.box` is no longer mangled either. An unresolvable hostname now
  yields `"unknown"` instead of raising.
- **The capture paths ignored your configured machine name.** All four call
  sites in `sqlite_capture.py` read `socket.gethostname()` directly, so
  `machine_name` in `~/.cc-anywhere.json` had no effect on captured rows — it
  only ever applied to sync. Renaming your Mac silently split its history into a
  second machine. All four now route through `get_machine_name()`, making the
  config file the single source of truth.
- **`started_at` recorded when a session was indexed, not when it happened.**
  The Claude Code capture path inserts the session row before reading any
  messages and stamped `started_at` with the current time. Every session
  imported in one batch therefore shared a single timestamp seconds apart, and
  time-windowed queries (`--read "this week"`, `--usage`, the digests) could not
  see any of them. `started_at` is now clamped back to the earliest message
  timestamp observed, mirroring how `last_message_at` is advanced. Incremental
  captures never push it forward.

Repairing an existing database:

```sql
-- collapse a mangled machine name into the real one
UPDATE sessions SET machine_name = 'Your-Mac' WHERE machine_name = 'Macdomain';

-- recover true session start times from the messages themselves
UPDATE sessions SET started_at = (
    SELECT MIN(m.timestamp) FROM messages m
    WHERE m.session_id = sessions.session_id AND m.timestamp IS NOT NULL
)
WHERE EXISTS (
    SELECT 1 FROM messages m
    WHERE m.session_id = sessions.session_id AND m.timestamp IS NOT NULL
);
```

Adds `tests/test_machine_name.py` (11 tests) covering suffix stripping, config
precedence over hostname, and `started_at` across initial and incremental capture.

---

## [1.3.1] — 2026-06-12

### Fix: runs on Python 3.8 / 3.9 again

`cc-anywhere` declares support for Python 3.8+, but three modules used the
`X | Y` union type syntax (PEP 604) without the `from __future__ import
annotations` guard — so on Python 3.8 and 3.9 the package crashed on import
(`TypeError: unsupported operand type(s) for |`) the moment you ran any command.
This affected anyone on the default macOS Python (3.9).

- Added `from __future__ import annotations` to `sync.py`, `semantic.py`, and
  `sqlite_capture.py`, bringing them in line with the modules that already had
  it. All `|` usages are annotation-only, so this fully resolves the crash with
  no behavior change.
- Verified on a real Python 3.9.6 install.

If you previously hit this, just upgrade: `pip install --upgrade cc-anywhere`.

---

## [1.3.0] — 2026-06-05

### See which AI models you actually use

cc-anywhere now remembers the model behind every reply it captures — across Claude Code, Codex, and Gemini CLI. That unlocks questions you couldn't answer before: Which models do you lean on? How has your mix shifted month to month? Which projects pull in which models?

- **Model history across providers.** Every session you capture is tagged with its model, so your usage is searchable across time and across projects — Claude, GPT, and Gemini side by side.
- **A redesigned `cc-anywhere --usage` dashboard.** An activity snapshot (streaks, peak hour, active days), a 13-week contribution heatmap, your most-used model, and a full per-model breakdown — right in the terminal.
- **One command for your back catalog.** `cc-anywhere --backfill-models` tags everything you captured before upgrading. Run it once; it's safe to re-run.

Upgrading is automatic — your local database migrates on first run, and new sessions are tagged going forward.

---

## [1.2.1] — 2026-06-03

- Polished the docs and onboarding, and refreshed the look of the header banner.

---

## [1.2.0] — 2026-05-01

**Memory built for AI coding.** One-command setup, capture across every major coding-AI tool, and a real cross-machine backup story.

- **`cc-anywhere --init` — one-command setup** on macOS, Linux, and WSL. Installs the recall hooks and an hourly capture safety net, and indexes your existing history. Re-running is always safe — it never duplicates anything.
- **Gemini CLI capture.** `cc-anywhere --capture` now picks up Gemini CLI sessions alongside Claude Code and Codex, all in one searchable database. Credentials are never indexed.
- **Full-history backup** to your own GitHub repo or any local, external, or cloud folder — for off-disk safety and for onboarding a fresh machine to your *entire* history, not just the recent window.
- **Simpler install.** `pip install cc-anywhere` is now the only step.

---

## [1.1.0] — 2026-04-28

- **Drill into any result.** `cc-anywhere --view <id>` opens the full conversation behind a search hit; `cc-anywhere --source <id>` points to the exact transcript line it came from.
- **Natural-language search.** Ask in plain language and get ranked, relevant results — fully local, no API key, no model required.
- **Codex CLI capture.** Codex sessions are captured alongside Claude Code in the same searchable database.

Existing databases upgrade automatically — no data loss.

---

**Source:** [github.com/abecouse/cc-anywhere](https://github.com/abecouse/cc-anywhere) · **License:** Apache-2.0 · **Author:** Abe Couse
