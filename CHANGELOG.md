# Changelog

All notable changes to `cc-anywhere` are documented here.

---

## [1.2.0] — 2026-05-01

**Memory built for AI coding.** This release consolidates the project around a single category claim, broadens capture across every major coding-AI surface, adds a one-command install, and fills in the cross-machine backup story.

### Added

#### `cc-anywhere --init` — one-command setup on macOS, Linux, and WSL

```bash
pip install cc-anywhere
cc-anywhere --init
```

That's the install. `--init` does three things, idempotently:

1. Captures every existing Claude Code, Codex, Cowork, and Gemini transcript into the local SQLite database and builds the semantic search index.
2. Merges `SessionStart` and `Stop` hooks into `~/.claude/settings.json` (creating the file if missing, preserving existing hooks). The `SessionStart` hook auto-loads relevant past-session recall as context for every new Claude Code session.
3. Installs an hourly capture safety net — `launchd` on macOS, `crontab` on Linux. Native Windows users get a one-line `schtasks` instruction printed for manual setup.

Re-running on an already-configured machine reports *"already present"* for each step instead of duplicating hooks.

#### Gemini CLI session capture

`cc-anywhere --capture` now scans `~/.gemini/tmp/<project>/chats/session-*.jsonl` alongside Claude Code, Cowork, and Codex. User and Gemini turns are captured with stable UUIDs from the source records; tool-call-only turns and credential files (`oauth_creds.json`, `google_accounts.json`) are skipped. Tagged with `source='gemini'`. Tests: `tests/test_gemini_capture.py` (22 tests).

#### `cc-anywhere --sync-archive` — full-history backup

```bash
cc-anywhere --sync-archive                    # to your GitHub cc-sync repo
cc-anywhere --sync-archive --to <path>        # to any filesystem path
```

Writes every session in the local DB to `<destination>/machines/<machine>/archive.json.gz`. Solves two problems the rolling 30-day sync didn't: (1) onboarding a fresh machine to the *full* history, not just the last 30 days; (2) off-disk backup of the entire local DB. The `--to` flag accepts external SSDs, mounted iCloud / Dropbox / NAS folders, or any local path. UUID dedup makes re-imports a no-op. GitHub destinations carry a 90 MB warning / 95 MB error guardrail to keep archives under the per-file PyPI hard limit.

#### `cc-anywhere --ask --json-context`

New flag wraps the answer in the JSON envelope Claude Code's `SessionStart` hook expects:

```json
{"hookSpecificOutput": {
  "hookEventName": "SessionStart",
  "additionalContext": "<recall + behavioral instruction>"
}}
```

Drops the `jq` dependency the hook setup previously needed; `pip install cc-anywhere` is now genuinely the only install required.

#### Auto-index after `--capture`

Manual `cc-anywhere --capture` now also runs `--index-semantic` when new messages were captured. Before this, you had to remember to run both for `--ask` to see new content. Pass `--no-index` to opt out.

#### `cc-anywhere --daily` — last 48 hours digest

New short-window digest with an hourly activity heatmap, complementing the existing `--weekly` and `--monthly` options.

### Changed

#### Renamed: `claude-anywhere` → `cc-anywhere`

The product is now `cc-anywhere`. The `cc` is deliberately unexplained — read it however you like. CLI command, PyPI package, Python module, hooks, settings file paths, and on-disk directory names all flipped:

| Old | New |
|---|---|
| `pip install claude-anywhere` | `pip install cc-anywhere` |
| Python package `claude_anywhere` | `cc_anywhere` (auto-import compat retained where possible) |
| `~/.claude-anywhere-sessions.db` | `~/.cc-anywhere-sessions.db` |
| `~/.claude-sync/` | `~/.cc-sync/` |
| `~/.claude-backups/` | `~/.cc-backups/` |
| `~/.claude-anywhere.json` | `~/.cc-anywhere.json` |

Auto-migration runs on first invocation: legacy paths are renamed in place atomically. Existing v1.1 installs upgrade transparently. Anything starting with `~/.claude/` (Claude Code's own directory) is left untouched — that's not ours to rename.

The `claude-anywhere` and `claude-sync` script entry points are kept as compatibility aliases.

#### Hardened `--init` hook commands

Hook commands now use the absolute path to `cc-anywhere` (resolved via `shutil.which`) and `shlex.quote` the binary path, so hooks run correctly under `launchd` / `cron` minimal-PATH environments and tolerate install paths with spaces or special characters. Idempotency check tightened to multi-needle substring match (`--ask` AND `--json-context`) to avoid false-positive "already present" detection.

#### License metadata modernized

`pyproject.toml` updated to PEP 639 form (`license = "Apache-2.0"`, `license-files = ["LICENSE"]`). Removed the now-deprecated trove classifier. Required for clean PyPI publish under modern build tooling.

### Fixed

- **Cross-platform encoding.** All text-mode `open()`, `Path.read_text()`, and `Path.write_text()` calls now pass `encoding="utf-8"` explicitly. Without this, Windows users (where Python's text-mode default is `cp1252`) would silently mangle non-ASCII characters in transcript content. ~36 call sites updated.
- **Gemini append-resume bug.** `capture_gemini_sessions` re-running on a chat file that grew since last capture would silently drop newly-appended messages. The session header (containing `sessionId`) is only on the first line; on a second capture we'd seek past it. Now the first line is read unconditionally to bootstrap the session linkage before the offset-based message loop. Surfaced by `test_appended_messages_picked_up`.
- **`--init` semantic bootstrap.** Initial capture now also runs `rebuild_semantic_index` when new messages arrive, so a fresh `--init` produces a queryable database immediately.

### Migration notes

`pip install --upgrade cc-anywhere` then `cc-anywhere --init` is sufficient. Auto-migration will rename your `~/.claude-anywhere-sessions.db` to `~/.cc-anywhere-sessions.db` on first invocation; the same applies to the sync, backup, and config directories. No manual migration. No data loss.

If you previously had hooks pointing at `claude-anywhere --capture` or `claude-anywhere --json-context`, they continue to work via the legacy script alias. New installs use `cc-anywhere` directly.

### Test coverage

- 22 new tests covering Gemini CLI capture (`tests/test_gemini_capture.py`)
- 10 new tests covering `--sync-archive` (`tests/test_sync_archive.py`)
- 10 new tests covering legacy-path migration (`tests/test_paths_migration.py`)
- 20 new tests covering `--init` setup logic (`tests/test_init_setup.py`)
- 59 new tests covering daily digest formatting (`tests/test_digest.py`)
- **Total: 161 passing (up from 95).**

---

## [1.1.0] — 2026-04-28

### Added

#### Drill-down: read the full chunk behind any search hit

`cc-anywhere --view <chunk_id>` returns the full content of a captured chunk plus its session metadata (project, source, originator, version, time range, message count). Search outputs (`--ask`, `--semantic-search`) now include a copy-pasteable drill-down hint after each result line.

`cc-anywhere --source <chunk_id>` goes one level deeper: prints the raw transcript path, line range, byte range, and nearby JSONL lines for the source transcript behind an indexed chunk. `cc-anywhere --backfill-sources` attaches source pointers to older DB rows captured before transcript provenance existed.

```bash
$ cc-anywhere --semantic-search "findings cards"
[Bio-Agent] (2026-04-22 04:57 PDT, hybrid, 0.26)
  ...everything downstream (findings cards, substantiation, timeline...
  → cc-anywhere --view eae07e00-...:273:a24c1d9b380a

$ cc-anywhere --view eae07e00-...:273:a24c1d9b380a
[Bio-Agent]  claude-code
  2026-04-22 04:57 PDT  →  2026-04-22 04:58 PDT  ·  7 messages  ·  1842 chars

[full content]

$ cc-anywhere --source eae07e00-...:273:a24c1d9b380a
[Bio-Agent] raw transcript source
  source: ~/.claude/projects/.../session.jsonl:1042-1078
```

Prefix match: pass any prefix of a chunk_id (`--view a0f0df06-2be`) and the most recently indexed match is returned.

#### Local semantic search

First-pass natural-language search on top of the existing keyword index. Fully local, dependency-free, hybrid-scored — local sparse vectors over chunked messages plus FTS keyword boost.

```bash
cc-anywhere --index-semantic               # build / refresh the semantic chunk index
cc-anywhere --semantic-search "<query>"    # ranked semantic hits
cc-anywhere --ask "<query>"                # semantic search + summary view
```

Lightweight local baseline — *"semantic-ish"* rather than true neural embeddings — but the storage shape is designed so the vector backend can be swapped without changing the capture database or CLI surface.

#### Codex CLI session capture

`cc-anywhere --capture` ingests Codex CLI sessions alongside Claude Code, into the same SQLite database with the same FTS5 index. Walks `~/.codex/sessions/**/rollout-*.jsonl`. Maps thread names from `~/.codex/session_index.jsonl` to a `session_label` column. Skips developer-role messages, Codex's synthetic user turns, and tool calls. Tagged with `source='codex'`.

`--db-search "<query>"` returns ranked hits from both sources.

### Schema migration (v1.0 → v1.1)

Existing v1.0 databases auto-upgrade. Two new columns:
- `sessions.source` — `'claude-code'` or `'codex'`, defaults to `'claude-code'` for existing rows
- `sessions.session_label` — optional human-readable session name (Codex thread names)

No data loss. Old captures continue to work.

### Test coverage

- 22 new tests in `tests/test_codex_capture.py`
- 3 new tests in `tests/test_semantic.py`
- **Total: 71 passing.**

---

**Source:** [github.com/abecouse/cc-anywhere](https://github.com/abecouse/cc-anywhere) · **License:** Apache-2.0 · **Author:** Abe Couse
