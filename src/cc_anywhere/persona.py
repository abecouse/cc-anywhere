"""
Persona / "about me" analysis from cc-anywhere transcripts.

Produces a structured evidence dossier from user-typed messages:
active projects, vocabulary signatures, directive style, push-back
patterns, recent shifts. Each statistical claim links back to chunk_ids
so an LLM (or the user) can drill into the raw evidence via
`cc-anywhere --view <chunk_id>`.

Designed as the *evidence* layer. Synthesis into a narrative persona
is left to an LLM that reads the dossier output.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cc_anywhere._paths import DB_PATH


WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")

PUSHBACK_PATTERNS = (
    r"\bno\b", r"\bnope\b", r"\bnot really\b", r"\bwait\b",
    r"\bactually\b", r"\bstop\b", r"\binstead\b", r"\brather\b",
    r"\bdon'?t\b", r"\bdo not\b", r"\bnever\b", r"\bskip\b",
    r"\bbut\b", r"\bwrong\b",
)
QUESTION_OPENERS = (
    r"^(what|why|how|when|where|who|which|can|should|does|is|are|do|will|would|could)\b",
)
DIRECTIVE_OPENERS = (
    r"^(do|make|build|run|add|remove|delete|fix|use|let'?s|try|keep|stop|skip|show|write|create|give|tell|find|check|sketch|draft)\b",
)

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "when", "while",
    "is", "are", "was", "were", "be", "been", "being",
    "do", "does", "did", "doing", "done", "have", "has", "had",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "their", "our", "its",
    "this", "that", "these", "those",
    "to", "of", "in", "on", "at", "for", "with", "by", "from", "into", "about",
    "as", "so", "not", "no", "yes",
    "what", "why", "how", "when", "where", "who", "which",
    "can", "could", "should", "would", "will", "may", "might", "must",
    "just", "really", "very", "much", "more", "most", "less", "few",
    "only", "even", "also", "than", "then", "there", "here",
    "yeah", "ok", "okay", "sure", "right", "now", "still", "yet",
    "any", "all", "some", "one", "two", "three",
    "out", "up", "down", "over", "under",
    "good", "bad", "new", "old",
    "thing", "things", "stuff", "way", "ways",
}


def _connect():
    return sqlite3.connect(str(DB_PATH))


def _user_messages(conn, days: int):
    """User-typed messages within the window. Filters tool-results, hooks, and pasted system reminders."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        """
        SELECT m.uuid, m.session_id, m.content, m.timestamp,
               s.project_name, s.source
          FROM messages m
          JOIN sessions s ON s.session_id = m.session_id
         WHERE m.role = 'user'
           AND m.timestamp >= ?
           AND COALESCE(m.is_compact_summary, 0) = 0
           AND COALESCE(m.is_visible_in_transcript_only, 0) = 0
        """,
        (since,),
    )
    rows = []
    for uuid, sid, content, ts, project, source in cur:
        content = (content or "").strip()
        if not content:
            continue
        # Skip tool-result / hook / system-reminder pastes — these aren't user voice.
        if content.startswith("<task-notification>"):
            continue
        if content.startswith("<system-reminder>"):
            continue
        if content.startswith("<tool_use_id>") or content.startswith("<tool_result>"):
            continue
        if content.startswith("[{") or content.startswith("{\""):
            continue
        if content.startswith("<command-name>") or content.startswith("<local-command-stdout>"):
            continue
        if content.startswith("<local-command-caveat>") or content.startswith("<local-command-stderr>"):
            continue
        if content.startswith("<bash-input>") or content.startswith("<bash-output>"):
            continue
        # Drop synthetic Claude Code envelope markers that get attributed to "user".
        lower = content.lower()
        if lower.startswith("[request interrupted") or "request interrupted by user" in lower[:80]:
            continue
        if lower.startswith("session continued") or lower.startswith("the previous conversation"):
            continue
        if "previous conversation ran out of context" in lower[:200]:
            continue
        if lower.startswith("this session is being continued"):
            continue
        if lower.startswith("caveat:") and "the messages below were generated" in lower:
            continue
        if content.startswith("Result of") or content.startswith("<result>"):
            continue
        # Strip absolute paths so n-gram noise doesn't drown out vocabulary.
        content = re.sub(r"/(?:Users|Volumes|tmp|opt|home)/[^\s)]+", "", content)
        content = content.strip()
        if not content:
            continue
        rows.append({
            "uuid": uuid, "session_id": sid, "content": content,
            "timestamp": ts, "project_name": project or "(unknown)",
            "source": source,
        })
    return rows


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in WORD_RE.findall(text)]


def _content_words(text: str) -> list[str]:
    return [w for w in _tokens(text) if w not in STOPWORDS and len(w) > 2]


def _ngrams(words: list[str], n: int) -> list[str]:
    return [" ".join(words[i:i + n]) for i in range(len(words) - n + 1)]


def _looks_pasted(content: str) -> bool:
    """Heuristic: is this message likely pasted-in content rather than Abe typing?

    Pasted writings (articles, prompts, persona definitions, code blocks)
    contaminate vocabulary analysis if treated as user voice. We want to
    keep them counted for *activity* but exclude from *style*.
    """
    # Code fence anywhere — pasted code or formatted prose, not typed voice.
    if "```" in content:
        return True
    # Markdown headers — rare in typed messages.
    if re.search(r"(^|\n)#{1,6}\s+\S", content):
        return True
    # Bullet lists — pasted structured content.
    if len(re.findall(r"(^|\n)\s*[-*]\s+\S", content)) >= 3:
        return True
    # Bulk URLs.
    if len(re.findall(r"https?://", content)) >= 3:
        return True
    # Third-person self-reference — almost always a pasted persona/bio.
    head = content[:200].lower()
    if re.search(r"\babe\s+(is|will|likes|prefers|wants|builds|works)\b", head):
        return True
    if re.search(r"\babe'?s\s+\w+", head) and " i " not in head[:60]:
        return True
    # Frontmatter / structured pastes.
    if (content.startswith("---\n") or content.startswith("{")
            or content.startswith("[") or content.startswith("<?xml")):
        return True
    # Length alone is not enough — typed long-form voice exists on Claude.ai.
    # Only catch truly massive pastes that slipped past structural signals.
    if len(content) > 4000:
        return True
    return False


def _classify_turn(content: str) -> str:
    """Coarse: question | directive | statement."""
    text = content.strip()
    if "?" in text:
        return "question"
    lower = text.lower()
    if any(re.search(p, lower) for p in QUESTION_OPENERS):
        return "question"
    if any(re.search(p, lower) for p in DIRECTIVE_OPENERS):
        return "directive"
    return "statement"


def _find_chunk_id(conn, session_id: str, ts: str | None) -> str | None:
    if not ts:
        return None
    row = conn.execute(
        """SELECT chunk_id FROM semantic_chunks
            WHERE session_id = ? AND started_at <= ? AND ended_at >= ?
            LIMIT 1""",
        (session_id, ts, ts),
    ).fetchone()
    return row[0] if row else None


def _attach_chunk_ids(conn, samples: list[dict]) -> list[dict]:
    for s in samples:
        s["chunk_id"] = _find_chunk_id(conn, s["session_id"], s["timestamp"])
    return samples


def build_dossier(days: int = 30, top_projects: int = 10) -> dict:
    """Compute the evidence dossier. Pure heuristics — no LLM call."""
    conn = _connect()
    try:
        all_msgs = _user_messages(conn, days)
        if not all_msgs:
            return {"empty": True, "days": days}

        # Split: typed = Abe's voice; pasted = data Abe presented to the AI.
        # Both count for project activity. Only typed feeds the style stats.
        msgs = [m for m in all_msgs if not _looks_pasted(m["content"])]
        pasted = [m for m in all_msgs if _looks_pasted(m["content"])]

        project_counts = Counter(m["project_name"] for m in all_msgs)
        pasted_per_project = Counter(m["project_name"] for m in pasted)

        # Quick characterization of presented data
        url_re = re.compile(r"https?://([^/\s)]+)")
        code_fence_re = re.compile(r"```(\w+)")
        domain_counts = Counter()
        code_lang_counts = Counter()
        for m in pasted:
            for d in url_re.findall(m["content"]):
                domain_counts[d.lower().lstrip("www.")] += 1
            for lang in code_fence_re.findall(m["content"]):
                code_lang_counts[lang.lower()] += 1

        turn_types = Counter()
        lengths = []
        pushback_hits = []
        bigram_counts = Counter()
        trigram_counts = Counter()
        bigram_projects = defaultdict(set)
        trigram_projects = defaultdict(set)
        closing_words = Counter()
        opening_words = Counter()

        pushback_re = re.compile("|".join(PUSHBACK_PATTERNS), re.IGNORECASE)

        for m in msgs:
            content = m["content"]
            lengths.append(len(content))
            turn_types[_classify_turn(content)] += 1

            if pushback_re.search(content) and 5 < len(content) < 400:
                pushback_hits.append(m)

            words = _content_words(content)
            for bg in _ngrams(words, 2):
                bigram_counts[bg] += 1
                bigram_projects[bg].add(m["project_name"])
            for tg in _ngrams(words, 3):
                trigram_counts[tg] += 1
                trigram_projects[tg].add(m["project_name"])

            tokens = _tokens(content)
            if tokens:
                opening_words[tokens[0]] += 1
                closing_words[tokens[-1]] += 1

        # Vocabulary signatures: cross-project recurrence, not just frequency.
        sig_bigrams = sorted(
            (
                (bg, c, len(bigram_projects[bg]))
                for bg, c in bigram_counts.items()
                if c >= 5 and len(bigram_projects[bg]) >= 2
            ),
            key=lambda x: (-x[2], -x[1]),
        )[:20]
        sig_trigrams = sorted(
            (
                (tg, c, len(trigram_projects[tg]))
                for tg, c in trigram_counts.items()
                if c >= 3 and len(trigram_projects[tg]) >= 2
            ),
            key=lambda x: (-x[2], -x[1]),
        )[:15]

        # Receipts: one sample per pushback kind, with chunk_id where possible.
        pushback_samples = _attach_chunk_ids(conn, pushback_hits[:6])

        # Recent-window shift: phrases new this week vs >7 days ago.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        recent_terms = Counter()
        older_terms = Counter()
        for m in msgs:
            words = _content_words(m["content"])
            bucket = recent_terms if (m["timestamp"] or "") >= cutoff else older_terms
            for w in set(words):
                bucket[w] += 1
        rising = []
        for w, c in recent_terms.most_common(150):
            if c >= 3 and older_terms.get(w, 0) <= 1:
                rising.append((w, c, older_terms.get(w, 0)))
            if len(rising) >= 12:
                break

        lengths.sort()
        n = len(lengths)
        median = lengths[n // 2]
        avg = sum(lengths) / n
        p90 = lengths[min(int(n * 0.9), n - 1)]

        return {
            "empty": False,
            "days": days,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M %Z").strip(),
            "total_typed_messages": n,
            "total_pasted_messages": len(pasted),
            "total_projects": len(project_counts),
            "top_projects": project_counts.most_common(top_projects),
            "pasted_per_project": pasted_per_project.most_common(top_projects),
            "turn_types": dict(turn_types),
            "avg_length": int(avg),
            "median_length": median,
            "p90_length": p90,
            "vocabulary_bigrams": sig_bigrams,
            "vocabulary_trigrams": sig_trigrams,
            "top_openers": opening_words.most_common(10),
            "top_closers": closing_words.most_common(10),
            "pushback_count": len(pushback_hits),
            "pushback_samples": pushback_samples,
            "rising_terms": rising,
            "presented_domains": domain_counts.most_common(10),
            "presented_code_langs": code_lang_counts.most_common(10),
        }
    finally:
        conn.close()


def render_markdown(d: dict) -> str:
    if d.get("empty"):
        return f"# About Me\n\n_No user messages found in the last {d['days']} days._\n"

    out = []
    out.append("# About Me — evidence dossier")
    out.append("")
    out.append(f"_Generated {d['generated_at']} from cc-anywhere transcripts, "
               f"last {d['days']} days._  ")
    out.append(f"_{d['total_typed_messages']} typed (voice) + "
               f"{d['total_pasted_messages']} pasted (data presented) "
               f"across {d['total_projects']} projects._")
    out.append("")
    out.append("_Style analysis below uses **typed** messages only. "
               "Pasted content is reported separately as 'presented data' "
               "since it reflects what Abe brings to a conversation, not how he speaks._")
    out.append("")

    out.append("## Active projects")
    out.append("")
    for proj, count in d["top_projects"]:
        out.append(f"- **{proj}** — {count} user messages")
    out.append("")

    out.append("## Directive style")
    out.append("")
    tt = d["turn_types"]
    total = sum(tt.values()) or 1
    for k in ("question", "directive", "statement"):
        v = tt.get(k, 0)
        out.append(f"- {k}: {v} ({100 * v / total:.0f}%)")
    out.append(f"- median turn length: {d['median_length']} chars")
    out.append(f"- avg turn length: {d['avg_length']} chars")
    out.append(f"- p90 turn length: {d['p90_length']} chars")
    out.append("")

    out.append("## Top openers (first word of turns)")
    out.append("")
    for word, c in d["top_openers"]:
        out.append(f"- `{word}` — {c}x")
    out.append("")

    out.append("## Top closers (last word of turns)")
    out.append("")
    for word, c in d["top_closers"]:
        out.append(f"- `{word}` — {c}x")
    out.append("")

    if d["vocabulary_bigrams"]:
        out.append("## Vocabulary signatures (2-grams, >=5 uses across >=2 projects)")
        out.append("")
        for phrase, count, projcount in d["vocabulary_bigrams"]:
            out.append(f"- `{phrase}` — {count}x in {projcount} projects")
        out.append("")

    if d["vocabulary_trigrams"]:
        out.append("## Vocabulary signatures (3-grams, >=3 uses across >=2 projects)")
        out.append("")
        for phrase, count, projcount in d["vocabulary_trigrams"]:
            out.append(f"- `{phrase}` — {count}x in {projcount} projects")
        out.append("")

    if d["rising_terms"]:
        out.append("## Rising terms (new in last 7 days, rare or absent before)")
        out.append("")
        for w, recent, older in d["rising_terms"]:
            out.append(f"- `{w}` — {recent}x recent, {older}x in prior {d['days']-7} days")
        out.append("")

    out.append(f"## Push-back patterns — {d['pushback_count']} messages contain pushback markers")
    out.append("")
    out.append("Sample receipts:")
    for s in d["pushback_samples"]:
        snippet = s["content"][:140].replace("\n", " ")
        line = f"- [{s['project_name']}] _\"{snippet}\"_"
        if s.get("chunk_id"):
            line += f"  → `cc-anywhere --view {s['chunk_id']}`"
        out.append(line)
    out.append("")

    if d.get("pasted_per_project") or d.get("presented_domains") or d.get("presented_code_langs"):
        out.append("## Presented data (pasted content — not voice)")
        out.append("")
        out.append("_What Abe brings into conversations as context: articles, code, persona files, prompts._")
        out.append("")
        if d.get("pasted_per_project"):
            out.append("**Pastes per project:**")
            for proj, c in d["pasted_per_project"]:
                out.append(f"- {proj} — {c} pasted messages")
            out.append("")
        if d.get("presented_domains"):
            out.append("**Top domains in pasted URLs:**")
            for dom, c in d["presented_domains"]:
                out.append(f"- {dom} — {c}x")
            out.append("")
        if d.get("presented_code_langs"):
            out.append("**Code languages pasted:**")
            for lang, c in d["presented_code_langs"]:
                out.append(f"- {lang} — {c} blocks")
            out.append("")

    out.append("---")
    out.append("")
    out.append("_This is an evidence dossier, not a synthesized persona._  ")
    out.append("_Feed it to an LLM with a synthesis prompt to produce the narrative version._")
    return "\n".join(out)


def about_me(days: int = 30, output_path: str | None = None) -> str:
    d = build_dossier(days=days)
    md = render_markdown(d)
    if output_path:
        Path(output_path).write_text(md, encoding="utf-8")
    return md
