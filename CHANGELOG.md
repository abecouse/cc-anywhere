# Changelog

All notable changes to `cc-anywhere` are documented here.

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
