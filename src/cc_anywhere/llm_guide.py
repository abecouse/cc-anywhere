"""LLM-facing usage guide for cc-anywhere.

Surfaced via `cc-anywhere --llm-guide` so LLM agents in any cwd (not just
the cc-anywhere repo) can fetch the reference at any time.

The canonical source is `LLM-CHEATSHEET.md` at the repo root. This module
mirrors that content for runtime distribution. If the .md changes, sync
this string. (Drift detection: a one-line shasum check could be added
to CI later if drift becomes a problem.)
"""


LLM_GUIDE = """\
# cc-anywhere — for LLM agents

Local memory of past Claude Code, Codex, and Gemini CLI conversations,
captured to ~/.cc-anywhere-sessions.db.

It captures CONVERSATIONS, not code. For *current* state of files, read
the filesystem. For *past discussion* — decisions, tradeoffs, naming,
prior work — use `cc-anywhere`.

## Search modes — pick the right one

`cc-anywhere --search <q>` is the unified entry point. It accepts
`--mode keyword|semantic|hybrid`. Default is hybrid.

```
cc-anywhere --search "BioTarget Score" --mode keyword
cc-anywhere --search "what scoring approach we settled on" --mode semantic
cc-anywhere --search "auth decisions"            # hybrid (default)
```

Mode-picking heuristic for an agent:

- **keyword** — looking for a specific identifier, function name, file
  path, error message, or any literal string you expect to appear
  verbatim in the conversation. Uses FTS5 over messages.
- **semantic** — looking for a concept, "what did we decide about X",
  "why did we pick Y", or any fuzzy intent where the literal phrase
  may not appear. Cosine similarity, no keyword fusion.
- **hybrid** (default) — don't know / mixed / general purpose. Fuses
  cosine + bm25 + term overlap; usually the right answer.
- **--ask <q>** — different layer entirely. Search + LLM synthesis with
  quoted excerpts. Use when you want a written-up answer, not a list of
  ranked chunks.

Examples:

```
# Find the function/file: keyword
cc-anywhere --search "_search_chunks" --mode keyword

# Find the rationale: semantic
cc-anywhere --search "why we forked into bioxcell-app" --mode semantic

# General topic: hybrid (default)
cc-anywhere --search "auth decisions"

# Synthesized answer for the user: --ask
cc-anywhere --ask "what did we decide about auth"
```

## Three read patterns — pick the right one

The agent can use `cc-anywhere --read` at any time, not just at session
start. Reach for it whenever you need to read over recent conversation
history before responding.

Bare `cc-anywhere --read` is the warm-up default: it tries the last chat in
the current project/folder first, and only falls back to broader recent
history if that project has nothing useful yet.

### Warm-up / cold start — when the user needs orientation

If the user asks:
- what have I been working on?
- what was I doing yesterday?
- what happened recently?
- catch me up
- what was the last work done here?

then READ OVER PAST CONVERSATIONS FIRST.

Treat these as recent-thread recall, not keyword lookup. The job is to
pull recent conversations in the relevant time window and summarize the
main threads briefly. Do NOT search for the literal words `today`,
`yesterday`, or `recently`.

For warm-up:
- prefer chronological recall over semantic search
- prefer the current project when the user is clearly asking about "here"
- summarize the main threads, not every tiny session shard
- stay brief
- if the user wants current file state, switch to filesystem inspection

### Topical recall — when the user names a subject

```
cc-anywhere --ask "what did we decide about auth"
cc-anywhere --ask "TIGIT preclinical decisions"
cc-anywhere --ask "pricing model conversation"
```

Returns top-K matching sessions ranked by relevance, with chunk_id
for drill-in.

### Temporal read — when the user asks about time-bounded activity

Time language in the query (today, yesterday, this week, recently,
just now, catch me up) routes to a chronological pull. NO KEYWORDS
NEEDED.

Important:
- `today` is NOT a search term
- `yesterday` is NOT a search term
- `this week` is NOT a search term

These are instructions to READ OVER PAST CONVERSATIONS in that time
window, not to do fuzzy keyword search for those words.

```
cc-anywhere --read
cc-anywhere --read "today"
cc-anywhere --read "yesterday"
cc-anywhere --read "this week"
cc-anywhere --read "catch me up"
cc-anywhere --read "fri may 1"        # specific day drill-in
```

Narrow window (<=48h) -> per-session list with previews.
Wide window (>48h)    -> daily rollup with top-projects + highlights
                          per day + walk-back hints.

### Section read — when you already have the right chunk

Use `--view` and `--source` after `--read` or `--ask` when you want to
read a specific snippet or transcript section more closely.

```
cc-anywhere --read "today"
cc-anywhere --view <chunk_id>
cc-anywhere --source <chunk_id>
```

### Hybrid — subject + time

When a query has both, the temporal slice runs first; the system
suggests `--semantic-search "<topic>"` for the topical part.

```
cc-anywhere --ask "what did we say about TIGIT yesterday"
```

### Drill-in

Every result row ends with a chunk_id. Read full content with:

```
cc-anywhere --view <chunk_id>
cc-anywhere --source <chunk_id>     # raw transcript provenance
```

## When to use cc-anywhere (and when NOT to)

USE IT WHEN:
- The user mentions a topic that might have prior context (architecture
  choices, naming, libraries, auth, pricing, prior tradeoffs)
- The user asks about recent activity without naming a topic
  ("what was I just doing", "today", "this week")
- The user is starting cold and needs orientation before acting
- You're about to make a non-trivial decision and want to check whether
  you've already decided this before
- You start a session cold and need to know what was happening recently
- The user references a past decision but you don't have the context

DO NOT USE IT FOR:
- Current state of project files -> use the filesystem (git log, ls,
  read). cc-anywhere indexes captured CONVERSATIONS, not current CODE.
- "What's in this directory" / "what was just committed" -> git/ls.
- "Did the test pass / does the build work" -> run the test/build.
- Any question whose answer is computed from current files — the
  conversation index is stale by definition.

## Code + conversation = full picture

The two layers complement each other. Don't pick one — use both.

- "What does this code do?"             -> code (read files)
- "Why did we write it this way?"       -> conversation (--ask)
- "What's in the current state?"        -> code (git, ls, read)
- "What were we discussing here?"       -> conversation (--read / --ask)
- "What's the current decision?"        -> both (code + --ask)

PATTERN: when reading code with non-obvious design choices, check
cc-anywhere for the conversation that produced those choices.
*Code tells you what; cc-anywhere tells you why.*

## Why this saves time (the intent)

1. AVOID REDOING DECISIONS. If you've already debated naming /
   architecture / library choice three weeks ago and concluded, that
   conversation is queryable. Don't make the user repeat themselves.
2. AVOID CONTRADICTING PRIOR WORK. Before suggesting a refactor, check
   if the current shape was a deliberate tradeoff. If your suggestion
   contradicts a past decision, FLAG IT BEFORE PROCEEDING.
3. COLD-START WARM. When picking up a project after time away,
   `cc-anywhere --read "this week"`
   gets you to context faster than re-reading transcripts.
4. CATCH UP AFTER A BREAK. `cc-anywhere --read "catch me up"` returns
   the recent chronological view — covers what happened while offline.
5. SURFACE FORGOTTEN CONTEXT. The user might not remember they
   discussed a topic six weeks ago. Retrieval makes their own past
   work findable.

The user expects you to use it. They built a memory layer specifically
so you'd stop re-asking them what was decided. WHEN IN DOUBT: SEARCH
BEFORE GUESSING.

## How to interpret results

Every output includes:
  project_name      which workspace the conversation was in
  timestamp         local time of the session
  source            claude-code, codex, or gemini
  message_count     rough size of the session
  chunk_id          drill-in handle for --view
  preview           first ~240 chars of the matched chunk

When you find a relevant past decision: QUOTE IT BACK to the user
with the chunk_id. If your current suggestion would contradict that
decision, surface the contradiction before proceeding. Don't silently
override prior work.

When the result set is thin or stale: check whether
`cc-anywhere --capture` has been run recently. The capture is
incremental — it picks up only new sessions since the last capture.
If the user just had a session you're trying to find, run --capture
first.

## Important: capture before search

```
cc-anywhere --capture
```

Indexes any new sessions since the last capture. RUN THIS IF YOU
SUSPECT THE ANSWER IS IN A RECENT CONVERSATION THAT MAY NOT YET BE
INDEXED. Idempotent and fast — 0 messages added if everything is
current.

## Learnings (from real usage)

- MARKETING != ARCHITECTURE. When using cc-anywhere to evaluate a
  competitor's product, the answer to "is this real?" is in the code,
  not the landing page. cc-anywhere can recall whether you previously
  read the code, but it can't substitute for reading it.
- CROSS-VENDOR. Claude Code, Codex, and Gemini CLI sessions are all
  in the same DB. A decision made in a Codex session is recallable
  from a Claude Code session, and vice versa.
- COMPACTION DOESN'T DESTROY MEMORY. When the source CLI compacts a
  session, the original JSONL on disk is append-only —
  `cc-anywhere --capture` reads the raw transcripts, not the compacted
  summaries. Full history persists.
- TEMPORAL QUERIES SURFACE CROSS-WINDOW PATTERNS. A daily/weekly
  rollup often reveals work happening in parallel across multiple
  sessions on the same theme. Semantic search would have spread those
  hits across keyword-matched results; chronological pull groups them
  visibly.
- LOW SCORE = FILESYSTEM. When --ask returns scores <=0.25 on a
  topical query, the answer probably isn't in conversation history —
  it's in current files. Switch to filesystem inspection.

## Common patterns

START OF A SESSION — get oriented:
```
cc-anywhere --capture
cc-anywhere --read
```

For a cold start:
- read recent conversations first
- summarize the main threads briefly
- avoid flooding the user with every tiny session
- use filesystem inspection only for current-state questions

BEFORE MAKING A DECISION — check prior work:
```
cc-anywhere --ask "<topic>"
```

READING UNFAMILIAR CODE — get the rationale:
```
cc-anywhere --ask "why did we choose <library/pattern>"
```

CATCHING UP AFTER A BREAK:
```
cc-anywhere --read "this week"
# then drill into busy days
cc-anywhere --read "wed apr 29"
```

CROSS-CHECKING A CURRENT SUGGESTION AGAINST PAST DECISIONS:
```
cc-anywhere --ask "what did we decide about <topic>"
# if past decision contradicts current suggestion, flag to user
```

## What this tool is NOT

- Not a search engine for the live web. Local conversations only.
- Not a substitute for reading code. Code in filesystem; cc-anywhere
  remembers what you said about it.
- Not a knowledge base. Doesn't store facts about the world — only
  your past conversations.
- Not perfect recall. Captures are incremental, semantic search has
  scope (defaults to last 30 days unless fallback expands), and
  chunking can split a conversation across multiple results. Use
  --view to read full content when ranking is unclear.

## TL;DR for an LLM that just landed

1. Topic-shaped question?     -> cc-anywhere --ask "<topic>"
                                 or --search "<topic>" for raw chunks
2. Looking for an identifier? -> --search "<id>" --mode keyword
3. Time-shaped question?      -> cc-anywhere --read "today" / "this week"
4. Found something useful?    -> quote it back with the chunk_id
5. Filesystem question?       -> read the files, don't search history
6. About to contradict prior? -> surface it before proceeding
"""


def show_llm_guide():
    """Print the LLM-facing usage guide to stdout."""
    print(LLM_GUIDE)
