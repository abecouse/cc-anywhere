# cc-anywhere — for LLM agents

If you are an LLM (Claude Code, Codex, Gemini CLI, or any agent that can call shell commands), this is the cheatsheet for using `cc-anywhere` effectively. Read this once, then reach for it the way you reach for `git` or `grep`.

---

## What it is

A local memory of past Claude Code, Codex, and Gemini CLI conversations, captured to SQLite at `~/.cc-anywhere-sessions.db`. Every interaction the user has had with any of these tools — across machines if they sync — is queryable in two distinct modes.

**It captures conversations, not code.** It does not know what's currently in the user's filesystem, repo, or working tree. For *current* state of files, read the filesystem. For *past discussion* about anything — decisions, tradeoffs, naming, architecture, prior work — reach for `cc-anywhere`.

---

## Three read patterns — pick the right one

The agent can use `cc-anywhere --read` at any time, not just at session
start. Reach for it whenever you need to read over recent conversation
history before responding.

Bare `cc-anywhere --read` is the warm-up default: it tries the last chat in
the current project/folder first, and only falls back to broader recent
history if that project has nothing useful yet.

### Warm-up / cold start — when the user needs orientation

This is the most important behavior to get right at the start of a session.

If the user asks:

- *what have I been working on?*
- *what was I doing yesterday?*
- *what happened recently?*
- *catch me up*
- *what was the last work done here?*

then **read over past conversations first**.

Treat these as **recent-thread recall**, not keyword lookup. The job is to pull
recent conversations in the relevant time window and summarize the main threads
briefly. Do **not** search for the literal words `today`, `yesterday`, or
`recently`.

For warm-up:

1. Prefer chronological recall over semantic search
2. Prefer the current project when the user is clearly asking about "here"
3. Summarize the main threads, not every tiny session shard
4. Stay brief
5. If the user wants current file state, switch to filesystem inspection

### Topical recall — when the user mentions a subject

Use semantic search across the conversation history.

```bash
cc-anywhere --ask "what did we decide about auth"
cc-anywhere --ask "TIGIT preclinical decisions"
cc-anywhere --ask "pricing model conversation"
```

Returns: top-K matching sessions ranked by relevance, with chunk_id for drill-in.

### Temporal read — when the user asks about time-bounded activity

Time language in the query (`today`, `yesterday`, `this week`, `recently`, `just now`, `catch me up`) routes to a chronological pull. **No keywords needed.** The system slices the DB by time window and summarizes.

Important:

- `today` is **not** a search term
- `yesterday` is **not** a search term
- `this week` is **not** a search term

These are instructions to **read over past conversations in that time
window**, not to do fuzzy keyword search for those words.

```bash
cc-anywhere --read
cc-anywhere --read "today"
cc-anywhere --read "yesterday"
cc-anywhere --read "this week"
cc-anywhere --read "catch me up"
cc-anywhere --read "fri may 1"      # specific day drill-in (from a rollup)
```

Narrow window (≤48h) → per-session list with previews.
Wide window (>48h, week/month) → daily rollup with top-projects + highlights per day + walk-back hints.

### Section read — when you already have the right chunk

Use `--view` and `--source` after `--read` or `--ask` when you want to read a
specific snippet or transcript section more closely.

```bash
cc-anywhere --read "today"
cc-anywhere --view <chunk_id>
cc-anywhere --source <chunk_id>
```

### Hybrid — subject + time

When a query has both, the temporal slice runs first; the system suggests `--semantic-search "<topic>"` for the topical part. Future versions will rank within the time window automatically.

```bash
cc-anywhere --ask "what did we say about TIGIT yesterday"
```

### Drill-in

Every result row ends with a chunk_id. Read full content with:

```bash
cc-anywhere --view <chunk_id>
cc-anywhere --source <chunk_id>   # raw transcript provenance
```

---

## When to use cc-anywhere (and when not to)

### ✅ Use it when:

- The user mentions a topic that *might* have prior context (architecture choices, naming, libraries, auth, pricing, prior tradeoffs, anything they've discussed before with any AI tool)
- The user asks about *recent activity* without naming a topic ("what was I just doing", "today", "this week")
- The user is starting cold and needs orientation before acting
- You're about to make a non-trivial decision and want to check whether you've already decided this before
- You start a session cold and need to know what was happening recently
- The user references a past decision but you don't have the context yourself

### ❌ Don't use it for:

- **Current state of project files** — use the filesystem (git log, ls, read).
  cc-anywhere indexes captured *conversations*, not current *code*. If the user asks "what's the state of biotarget today," read biotarget's git log + project docs, not cc-anywhere.
- **What's in this directory / what was just committed** — use git/ls.
- **Did the test pass / does the build work** — run the test/build, don't search history.
- **Any question whose answer is computed from current files** — the conversation index is stale by definition.

---

## Code + conversation = full picture

The two layers complement each other. Don't pick one — use both.

| Question shape | Layer | Tool |
|---|---|---|
| "What does this code do?" | code | read files |
| "Why did we write it this way?" | conversation | `cc-anywhere --ask` |
| "What's in the current state?" | code | git, ls, read |
| "What were we discussing here last week?" | conversation | `cc-anywhere --read "this week"` |
| "What's the current decision?" | both | check git log + cc-anywhere for rationale |

**Pattern:** when reading code that has non-obvious design choices, check cc-anywhere for the conversation that produced those choices. *Code tells you what; cc-anywhere tells you why.*

---

## How it saves time (the intent)

The reason this tool exists:

1. **Avoid redoing decisions.** If you've already debated naming / architecture / library choice three weeks ago and concluded, that conversation is queryable. Don't make the user repeat themselves.
2. **Avoid contradicting prior work.** Before suggesting a refactor, check if the current shape was a deliberate tradeoff. If your suggestion contradicts a past decision, **flag it before proceeding**.
3. **Cold-start a session warm.** When picking up a project after time away, `cc-anywhere --read "this week"` gets you to context faster than re-reading transcripts.
4. **Catch up after a break.** `cc-anywhere --read "catch me up"` returns the recent chronological view — covers what happened while you were offline.
5. **Surface forgotten context.** The user might not remember they discussed a topic six weeks ago. The retrieval makes their own past work findable.

The user expects you to use it. **They built a memory layer specifically so you'd stop re-asking them what was decided.** When in doubt: search before guessing.

---

## How to interpret results

Every output includes:

- **project_name** — which workspace the conversation was in
- **timestamp** — local time of the session
- **source** — `claude-code`, `codex`, or `gemini`
- **message_count** — rough size of the session
- **chunk_id** — drill-in handle for `--view`
- **preview** — first ~240 chars of the matched chunk

**When you find a relevant past decision:** quote it back to the user with the chunk_id. If your current suggestion would contradict that decision, surface the contradiction before proceeding. Don't silently override prior work.

**When the result set is thin or stale:** check whether `cc-anywhere --capture` has been run recently. The capture is incremental — it picks up only new sessions since the last capture. If the user just had a session you're trying to find, run `--capture` first.

---

## Important: capture before search

```bash
cc-anywhere --capture
```

Indexes any new sessions since the last capture. **Run this if you suspect the answer is in a recent conversation that may not yet be indexed.** It's idempotent and fast — 0 messages added if everything is current.

---

## Learnings (from real usage)

- **Marketing ≠ architecture.** When using cc-anywhere to evaluate a competitor's product (open-source or closed), the answer to *"is this real?"* is in the code, not the landing page. cc-anywhere can recall *whether* you previously read the code, but it can't substitute for reading it.
- **The conversation context survives across model providers.** Claude Code, Codex, and Gemini CLI sessions are all in the same DB. A decision made in a Codex session is recallable from a Claude Code session, and vice versa.
- **Compaction doesn't destroy memory.** When Claude Code or Codex compacts a session, the original JSONL on disk is append-only — `cc-anywhere --capture` reads the raw transcripts, not the compacted summaries. The full conversation history persists.
- **Temporal queries surface cross-window patterns.** A daily/weekly rollup often reveals work happening in parallel across multiple sessions on the same theme. Semantic search would have spread those hits across keyword-matched results; chronological pull groups them visibly.
- **When `--ask` returns low scores (≤0.25) on a topical query**, the answer probably isn't in conversation history — it's likely in current files. Switch to filesystem inspection.

---

## Common patterns

### Start of a session — get oriented
```bash
cc-anywhere --capture
cc-anywhere --read
```

For a cold start, prefer this behavior:

- read recent conversations first
- summarize the main threads briefly
- avoid flooding the user with every tiny session
- use filesystem inspection only for current-state questions

### Before making a decision — check prior work
```bash
cc-anywhere --ask "<topic>"
```

### Reading unfamiliar code — get the rationale
```bash
cc-anywhere --ask "why did we choose <library/pattern>"
```

### Catching up after a break
```bash
cc-anywhere --read "this week"
# then drill into busy days
cc-anywhere --read "wed apr 29"
```

### Cross-checking a current suggestion against past decisions
```bash
cc-anywhere --ask "what did we decide about <topic>"
# if past decision contradicts current suggestion, flag to user
```

---

## What this tool is NOT

- **Not a search engine for the live web.** It indexes your local conversation history only.
- **Not a substitute for reading code.** Code lives in the filesystem; cc-anywhere remembers what you said about it.
- **Not a knowledge base.** It doesn't store facts about the world — only your past conversations.
- **Not perfect recall.** Captures are incremental, semantic search has scope (defaults to last 30 days unless fallback expands), and chunking can split a conversation across multiple results. Use `--view` to read full content when ranking is unclear.

---

## TL;DR for an LLM that just landed

1. Topic-shaped question? → `cc-anywhere --ask "<topic>"`
2. Time-shaped / orientation question? → `cc-anywhere --read "today" / "this week" / etc.`
3. Need to read one exact snippet? → `cc-anywhere --view <chunk_id>`
4. Filesystem question pretending to be a conversation question? → read the files instead
5. About to contradict a past decision? → surface it before proceeding
