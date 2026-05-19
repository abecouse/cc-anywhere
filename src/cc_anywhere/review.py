"""
Review tools — surface what's in the corpus that's worth a second look.

Two functions:
- list_projects: groups project names by canonical normalization, surfaces
  total activity, date range; flags name fragmentation across efforts.
- list_deep_sessions: ranks sessions by a depth score (duration + volume +
  density) so prolonged substantive conversations surface for re-reading.

Both pure SQL/heuristic — no LLM calls.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from cc_anywhere._paths import DB_PATH


SIDECAR_FILENAME = ".cc-anywhere.json"


def _read_sidecar(project_path: str | None) -> dict | None:
    """Read the optional .cc-anywhere.json sidecar from a project folder.

    Schema (all fields optional):
      {
        "name": "persona-lab",
        "aliases": ["writer", "360intel"],
        "description": "B2B persona/outreach platform",
        "related": ["contact-discovery-360", "sales-funnel"]
      }
    """
    if not project_path:
        return None
    try:
        p = Path(project_path)
        if not p.is_dir():
            return None
        sidecar = p / SIDECAR_FILENAME
        if not sidecar.exists():
            return None
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def init_sidecar(project_path: str, name: str | None = None) -> str:
    """Write a starter sidecar to a project folder. Idempotent: refuses to overwrite."""
    p = Path(project_path).expanduser().resolve()
    if not p.is_dir():
        return f"Error: not a directory: {p}"
    sidecar = p / SIDECAR_FILENAME
    if sidecar.exists():
        return f"Sidecar already exists: {sidecar}"
    payload = {
        "name": name or p.name,
        "aliases": [],
        "description": "",
        "related": [],
    }
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return f"Created {sidecar}"


def _connect():
    return sqlite3.connect(str(DB_PATH))


def _canonical_name(name: str) -> str:
    """Lowercase + alphanumeric only — collapses BioXCell/Bioxcell/biotarget variants."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower()) or "(unknown)"


def _parse_iso(ts: str | None):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None


def _format_date(ts: str | None) -> str:
    return (ts or "")[:10] or "-"


def _format_duration(start: str | None, end: str | None) -> str:
    s = _parse_iso(start)
    e = _parse_iso(end)
    if not s or not e:
        return "-"
    total_min = max(0, int((e - s).total_seconds() / 60))
    if total_min < 60:
        return f"{total_min}m"
    h, m = divmod(total_min, 60)
    if h < 24:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


def list_projects(min_messages: int = 5) -> str:
    """Render a project listing with normalized groupings.

    Projects whose names normalize to the same canonical key (lowercased,
    non-alphanumeric stripped) are grouped together. Surfaces fragmentation:
    Bioxcell + BioXCell + biotarget collapse, but biotarget vs Bio-Agent
    stay separate (different roots) — flagged for the user to consider.
    """
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT
                s.project_name,
                s.project_path,
                s.source,
                COUNT(DISTINCT s.session_id) AS session_count,
                COUNT(m.uuid) AS msg_count,
                SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) AS user_msg_count,
                MIN(s.started_at) AS first_at,
                MAX(s.last_message_at) AS last_at
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.session_id
            GROUP BY s.project_name, s.project_path, s.source
            HAVING msg_count >= ?
            """,
            (min_messages,),
        ).fetchall()

        # Cache sidecar reads per path
        sidecar_cache: dict[str, dict | None] = {}

        def _get_sidecar(path):
            if path not in sidecar_cache:
                sidecar_cache[path] = _read_sidecar(path)
            return sidecar_cache[path]

        groups: dict[str, list[dict]] = defaultdict(list)
        for project_name, project_path, source, sessions, msgs, user_msgs, first_at, last_at in rows:
            sidecar = _get_sidecar(project_path) if project_path else None
            # Sidecar.name overrides canonical key — that's how a renamed project
            # collapses with its old folder name without filesystem renames.
            if sidecar and sidecar.get("name"):
                key = _canonical_name(sidecar["name"])
            else:
                key = _canonical_name(project_name)
            groups[key].append({
                "project_name": project_name,
                "project_path": project_path,
                "source": source,
                "sessions": sessions or 0,
                "msgs": msgs or 0,
                "user_msgs": user_msgs or 0,
                "first_at": first_at,
                "last_at": last_at,
                "sidecar": sidecar,
            })

        sorted_groups = sorted(
            groups.items(),
            key=lambda kv: -sum(v["user_msgs"] for v in kv[1]),
        )

        out = []
        out.append("# Project groups")
        out.append("")
        out.append(f"_{len(rows)} project labels collapse to "
                   f"{len(groups)} canonical efforts._")
        out.append("")

        # Suggest cross-group merges where the canonical key prefixes match
        # and there are multiple roots (e.g., 'bio*' covering biotarget,
        # bioxcell, bioagent). Heuristic, not authoritative.
        prefix_buckets: dict[str, list[str]] = defaultdict(list)
        for key in groups.keys():
            if len(key) >= 3:
                prefix_buckets[key[:3]].append(key)
        related_hint: dict[str, list[str]] = {}
        for prefix, keys in prefix_buckets.items():
            if len(keys) >= 2:
                for k in keys:
                    related_hint[k] = [x for x in keys if x != k]

        for key, projects in sorted_groups:
            total_user = sum(p["user_msgs"] for p in projects)
            total_sessions = sum(p["sessions"] for p in projects)
            firsts = [p["first_at"] for p in projects if p["first_at"]]
            lasts = [p["last_at"] for p in projects if p["last_at"]]
            first_at = min(firsts) if firsts else None
            last_at = max(lasts) if lasts else None

            # Sidecar wins for the display heading. Pick the most informative
            # sidecar in the group (one with description preferred).
            sidecars = [p["sidecar"] for p in projects if p["sidecar"]]
            sidecar = next(
                (s for s in sidecars if s.get("description")),
                sidecars[0] if sidecars else None,
            )

            if sidecar and sidecar.get("name"):
                heading = sidecar["name"]
                folder_names = sorted({p["project_name"] for p in projects})
                folder_str = ", ".join(f"`{n}`" for n in folder_names)
                out.append(f"## {heading}")
                out.append(f"_declared via sidecar · folder: {folder_str}_")
            else:
                out.append(f"## {key}")
                if len(projects) == 1:
                    p = projects[0]
                    out.append(f"_inferred from folder name: `{p['project_name']}` "
                               f"({p['source']})_")

            out.append(f"_{total_user} user msgs · {total_sessions} sessions · "
                       f"{_format_date(first_at)} → {_format_date(last_at)}_")

            if sidecar:
                if sidecar.get("description"):
                    out.append("")
                    out.append(f"> {sidecar['description']}")
                if sidecar.get("aliases"):
                    aliases = ", ".join(f"`{a}`" for a in sidecar["aliases"])
                    out.append(f"_aliases: {aliases}_")
                if sidecar.get("related"):
                    related = ", ".join(f"`{r}`" for r in sidecar["related"])
                    out.append(f"_related: {related}_")

            if len(projects) > 1:
                out.append("")
                out.append(f"**Combines {len(projects)} project labels:**")
                for p in sorted(projects, key=lambda x: -x["user_msgs"]):
                    out.append(f"- `{p['project_name']}` ({p['source']}) "
                               f"— {p['user_msgs']} user msgs, "
                               f"{_format_date(p['first_at'])} → "
                               f"{_format_date(p['last_at'])}")

            if related_hint.get(key) and not sidecar:
                rels = ", ".join(f"`{r}`" for r in related_hint[key][:5])
                out.append("")
                out.append(f"_possibly related (shared prefix): {rels} — "
                           f"verify before merging, or declare via sidecar_")
            out.append("")

        return "\n".join(out)
    finally:
        conn.close()


def reconstruct_timeline(project_query: str, include_assistant: bool = False,
                         max_chars_per_msg: int = 400) -> str:
    """Merge all sessions matching a project query into one chronological timeline.

    Useful for projects whose work was scattered across multiple sessions or
    multiple project labels (e.g., a writing project that lived in three
    Claude Code sessions and four Claude.ai conversations).

    Matching is case-insensitive against project_name AND session_label, so
    `--timeline writer` catches the `writer` project, `Creative writer`,
    and any Claude.ai conversation with "writer" in its label.
    """
    conn = _connect()
    try:
        q = f"%{project_query.lower()}%"
        sessions = conn.execute(
            """
            SELECT session_id, project_name, source, session_label,
                   started_at, last_message_at
              FROM sessions
             WHERE LOWER(project_name) LIKE ?
                OR LOWER(COALESCE(session_label, '')) LIKE ?
             ORDER BY started_at
            """,
            (q, q),
        ).fetchall()

        if not sessions:
            return f"No sessions matching `{project_query}`."

        out = []
        out.append(f"# Timeline: `{project_query}`")
        out.append("")
        out.append(f"_{len(sessions)} sessions, "
                   f"{_format_date(sessions[0][4])} → "
                   f"{_format_date(sessions[-1][5])}_")
        out.append("")

        roles_filter = ("user", "assistant") if include_assistant else ("user",)
        placeholders = ",".join("?" * len(roles_filter))

        for sid, project, source, label, start, end in sessions:
            label_str = (label or "(unlabeled)")[:80]
            out.append(f"## {_format_date(start)} — [{project}] {label_str}")
            out.append(f"_{source} · session {sid[:8]} · "
                       f"{_format_duration(start, end)}_")
            out.append("")

            msgs = conn.execute(
                f"""
                SELECT role, timestamp, content
                  FROM messages
                 WHERE session_id = ?
                   AND role IN ({placeholders})
                   AND COALESCE(is_compact_summary, 0) = 0
                   AND COALESCE(is_visible_in_transcript_only, 0) = 0
                 ORDER BY timestamp
                """,
                (sid, *roles_filter),
            ).fetchall()

            for role, ts, content in msgs:
                content = (content or "").strip()
                if not content:
                    continue
                # Skip envelope/system noise
                if content.startswith(("<system-reminder>", "<task-notification>",
                                       "<local-command-caveat>", "<command-name>",
                                       "<local-command-stdout>", "<bash-input>",
                                       "<bash-output>")):
                    continue
                if content.lower().startswith(("session continued",
                                                "the previous conversation")):
                    continue

                snippet = content[:max_chars_per_msg].replace("\n", " ")
                if len(content) > max_chars_per_msg:
                    snippet += "…"
                tag = "**you:**" if role == "user" else "_claude:_"
                time_str = (ts or "")[11:16] if ts else ""
                out.append(f"- `{time_str}` {tag} {snippet}")
            out.append("")

        return "\n".join(out)
    finally:
        conn.close()


def list_deep_sessions(top: int = 20, min_user_messages: int = 30) -> str:
    """Surface sessions ranked by depth (volume × duration × density)."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT
                s.session_id,
                s.project_name,
                s.source,
                s.session_label,
                s.started_at,
                s.last_message_at,
                COUNT(m.uuid) AS total_msgs,
                SUM(CASE WHEN m.role='user' THEN 1 ELSE 0 END) AS user_msgs,
                AVG(CASE WHEN m.role='user' THEN LENGTH(m.content) END) AS avg_user_len
            FROM sessions s
            JOIN messages m ON m.session_id = s.session_id
            GROUP BY s.session_id
            HAVING user_msgs >= ?
            """,
            (min_user_messages,),
        ).fetchall()

        scored = []
        for sid, project, source, label, start, end, total, user, avg_len in rows:
            avg_len = avg_len or 0
            s_dt = _parse_iso(start)
            e_dt = _parse_iso(end)
            duration_min = 1
            if s_dt and e_dt:
                duration_min = max(1, int((e_dt - s_dt).total_seconds() / 60))

            volume_score = math.log10(max(1, user)) * 2.0
            duration_score = math.log10(max(1, duration_min)) * 1.5
            density_score = math.log10(max(1, avg_len / 10)) * 1.0
            depth = volume_score + duration_score + density_score

            scored.append({
                "session_id": sid, "project": project, "source": source,
                "label": label, "start": start, "end": end,
                "total": total, "user": user, "avg_len": avg_len,
                "duration_min": duration_min, "depth": depth,
            })

        scored.sort(key=lambda x: -x["depth"])

        out = []
        out.append("# Deep sessions")
        out.append("")
        out.append(f"_Top {min(top, len(scored))} of {len(scored)} qualifying sessions "
                   f"(>={min_user_messages} user messages)._")
        out.append(f"_Score = log(user_msgs)·2 + log(duration_min)·1.5 + "
                   f"log(avg_user_len/10)·1._")
        out.append("")

        for i, s in enumerate(scored[:top], 1):
            label = (s["label"] or "(unlabeled)")[:80]
            duration_str = _format_duration(s["start"], s["end"])

            out.append(f"## {i}. [{s['project']}] {label}")
            out.append(f"_{s['source']} · {_format_date(s['start'])} · "
                       f"{duration_str} · {s['user']} user msgs · "
                       f"avg {int(s['avg_len'])} chars · score {s['depth']:.1f}_")

            chunk = conn.execute(
                """
                SELECT chunk_id, content
                  FROM semantic_chunks
                 WHERE session_id = ?
                 ORDER BY started_at
                 LIMIT 1
                """,
                (s["session_id"],),
            ).fetchone()

            if chunk:
                preview = chunk[1][:240].replace("\n", " ").strip()
                out.append("")
                out.append(f"> _{preview}..._")
                out.append("")
                out.append(f"`cc-anywhere --view {chunk[0]}`")
            out.append("")

        return "\n".join(out)
    finally:
        conn.close()
