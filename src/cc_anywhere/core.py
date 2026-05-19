#!/usr/bin/env python3
"""
cc-anywhere core - Projects, prompts, search.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from collections import defaultdict

from cc_anywhere._paths import CLAUDE_DIR, CONFIG_FILE, SYNC_DIR, migrate_legacy_paths

log = logging.getLogger("cc-anywhere")


# ============ CONFIG ============

def load_config() -> dict:
    """Load user configuration."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load config: %s", e)
    return {}


def save_config(config: dict):
    """Save user configuration."""
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get_machine_name() -> str:
    """Get this machine's name."""
    config = load_config()
    if config.get("machine_name"):
        return config["machine_name"]
    import socket
    hostname = socket.gethostname()
    # Strip common suffixes
    for suffix in [".local", ".localdomain", ".lan"]:
        hostname = hostname.replace(suffix, "")
    return hostname


def set_machine_name(name: str):
    """Set this machine's name."""
    config = load_config()
    config["machine_name"] = name
    save_config(config)


# ============ HISTORY ============

def load_all_history(include_synced: bool = False) -> list:
    """Load all history entries.

    Args:
        include_synced: If True, also load history from synced machines

    Returns:
        List of history entries
    """
    entries = []

    # Load local history
    history_file = CLAUDE_DIR / "history.jsonl"
    if history_file.exists():
        with open(history_file, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    entry["_source"] = "local"
                    entries.append(entry)
                except json.JSONDecodeError:
                    pass  # Skip malformed JSON lines

    # Load synced machine history if requested
    if include_synced:
        migrate_legacy_paths()
        sync_dir = SYNC_DIR / "machines"
        if sync_dir.exists():
            for machine_dir in sync_dir.iterdir():
                if not machine_dir.is_dir():
                    continue
                machine_name = machine_dir.name

                # Check for full claude backup first
                full_history = machine_dir / "claude-full" / "history.jsonl"
                if not full_history.exists():
                    # Fall back to direct history.jsonl
                    full_history = machine_dir / "history.jsonl"

                if full_history.exists():
                    with open(full_history, encoding="utf-8") as f:
                        for line in f:
                            try:
                                entry = json.loads(line.strip())
                                entry["_source"] = machine_name
                                entries.append(entry)
                            except json.JSONDecodeError:
                                pass

    return entries


# ============ PROJECT DISCOVERY ============

def get_projects() -> list:
    """Get all Claude Code projects with their metadata."""
    projects = []

    # Read from Claude's history
    history_file = CLAUDE_DIR / "history.jsonl"
    if not history_file.exists():
        return projects

    # Track projects by path
    project_data = defaultdict(lambda: {
        "prompts": 0,
        "last_active": None
    })

    with open(history_file, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                project_path = entry.get("project", "")
                if not project_path:
                    continue

                project_data[project_path]["prompts"] += 1

                ts = entry.get("timestamp")
                if ts:
                    dt = datetime.fromtimestamp(ts / 1000)
                    current = project_data[project_path]["last_active"]
                    if not current or dt > current:
                        project_data[project_path]["last_active"] = dt
            except (json.JSONDecodeError, TypeError, ValueError):
                pass  # Skip malformed JSON lines

    # Convert to list
    for path, data in project_data.items():
        projects.append({
            "name": Path(path).name,
            "path": path,
            "prompts": data["prompts"],
            "last_active": data["last_active"]
        })

    # Sort by last active
    projects.sort(key=lambda x: x["last_active"] or datetime.min, reverse=True)
    return projects


def get_recent_prompts(project_path: str, limit: int = 10) -> list:
    """Get recent prompts for a project."""
    prompts = []
    history_file = CLAUDE_DIR / "history.jsonl"

    if not history_file.exists():
        return prompts

    with open(history_file, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                if entry.get("project") == project_path:
                    prompts.append({
                        "text": entry.get("display", "")[:200],
                        "timestamp": entry.get("timestamp")
                    })
            except json.JSONDecodeError:
                pass  # Skip malformed JSON lines

    # Return most recent
    prompts.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return prompts[:limit]


# ============ PICKUP PROMPT ============

def generate_pickup_prompt(project: dict) -> str:
    """Generate a pickup prompt for continuing work on another machine."""
    lines = ["I'm continuing work on this project from another machine.", ""]

    # Recent prompts
    recent = get_recent_prompts(project["path"], limit=5)
    if recent:
        lines.append("Recent context (what I was working on):")
        for p in recent:
            text = p["text"][:100].replace("\n", " ")
            lines.append(f"- {text}")
        lines.append("")

    lines.append("Please help me continue where I left off.")
    return "\n".join(lines)


def generate_pickup_prompt_from_machine(project_name: str, machine_name: str) -> str:
    """Generate a pickup prompt using context from a specific synced machine.

    Includes recent conversation history (user prompts + Claude responses) if available.
    """
    import json
    migrate_legacy_paths()
    state_file = SYNC_DIR / "machines" / machine_name / "state.json"

    if not state_file.exists():
        return f"I'm continuing work on '{project_name}' from {machine_name}.\n\nPlease help me continue where I left off."

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        project_data = state.get("projects", {}).get(project_name, {})

        lines = [f"I'm continuing work on this project from another machine ({machine_name}).", ""]

        # Include recent conversation context if available
        recent_context = project_data.get("recent_context", [])
        if recent_context:
            lines.append("Here's the recent conversation context:")
            lines.append("")
            for msg in recent_context[-20:]:  # Last 10 exchanges
                role = "Me" if msg["role"] == "user" else "Claude"
                content = msg["content"][:300]  # Truncate for prompt
                if len(msg["content"]) > 300:
                    content += "..."
                lines.append(f"**{role}:** {content}")
                lines.append("")
        else:
            # Fallback to basic info
            if project_data.get("last_active"):
                lines.append(f"Last active: {project_data['last_active']}")
            if project_data.get("prompts"):
                lines.append(f"Total prompts on that machine: {project_data['prompts']}")
            lines.append("")

        lines.append("Please help me continue where I left off.")
        return "\n".join(lines)
    except Exception:
        return f"I'm continuing work on '{project_name}' from {machine_name}.\n\nPlease help me continue where I left off."


# ============ SEARCH ============

def search_history(query: str) -> list:
    """Search across all conversation history."""
    results = []
    history_file = CLAUDE_DIR / "history.jsonl"

    if not history_file.exists():
        return results

    query_lower = query.lower()

    with open(history_file, encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line.strip())
                display = entry.get("display", "")
                if query_lower in display.lower():
                    results.append({
                        "text": display[:200],
                        "project": Path(entry.get("project", "")).name,
                        "timestamp": entry.get("timestamp")
                    })
            except json.JSONDecodeError:
                pass  # Skip malformed JSON lines

    # Also search Claude's responses in project conversations
    for conv_file in (CLAUDE_DIR / "projects").glob("*/*.jsonl"):
        try:
            for line in conv_file.open(encoding="utf-8"):
                entry = json.loads(line)
                if entry.get("type") == "assistant":
                    for item in entry.get("message", {}).get("content", []):
                        if isinstance(item, dict) and query_lower in item.get("text", "").lower():
                            results.append({"text": item["text"][:200], "project": conv_file.parent.name.split("-")[-1], "timestamp": entry.get("timestamp"), "type": "claude"})
                            break
        except (json.JSONDecodeError, OSError) as e:
            log.debug("Error searching %s: %s", conv_file, e)

    # Sort by timestamp, most recent first (handle mixed int/str timestamps)
    def get_sort_key(x):
        ts = x.get("timestamp", 0)
        if isinstance(ts, str):
            try:
                return int(ts)
            except ValueError:
                return 0
        return ts or 0
    results.sort(key=get_sort_key, reverse=True)
    return results[:50]


# ============ CROSS-MACHINE STATS ============

def get_cross_machine_stats() -> dict:
    """Get combined statistics across all synced machines."""
    migrate_legacy_paths()
    sync_dir = SYNC_DIR / "machines"

    stats = {
        "machines": [],
        "total_prompts": 0,
        "total_projects": 0,
        "total_sessions": 0,
        "all_projects": {}
    }

    if not sync_dir.exists():
        return stats

    current_machine = get_machine_name()
    all_project_names = set()

    for machine_folder in sync_dir.iterdir():
        if not machine_folder.is_dir():
            continue

        state_file = machine_folder / "state.json"
        if not state_file.exists():
            continue

        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
            projects = state.get("projects", {})

            machine_prompts = sum(p.get("prompts", 0) for p in projects.values())

            # Normalize for comparison
            folder_normalized = machine_folder.name.replace(".local", "").replace(".localdomain", "").lower()
            current_normalized = current_machine.replace(".local", "").replace(".localdomain", "").lower()

            stats["machines"].append({
                "name": machine_folder.name,
                "is_current": folder_normalized == current_normalized,
                "prompts": machine_prompts,
                "projects": len(projects),
                "sessions": len(projects),  # Approximate
                "last_updated": state.get("updated")
            })

            stats["total_prompts"] += machine_prompts

            # Track projects across machines
            for proj_name, proj_data in projects.items():
                all_project_names.add(proj_name)
                if proj_name not in stats["all_projects"]:
                    stats["all_projects"][proj_name] = {"total": 0, "machines": {}}
                stats["all_projects"][proj_name]["total"] += proj_data.get("prompts", 0)
                stats["all_projects"][proj_name]["machines"][machine_folder.name] = proj_data.get("prompts", 0)

        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to read state for %s: %s", machine_folder.name, e)

    stats["total_projects"] = len(all_project_names)
    stats["total_sessions"] = sum(m["sessions"] for m in stats["machines"])

    return stats
