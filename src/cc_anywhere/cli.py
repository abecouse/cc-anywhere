#!/usr/bin/env python3
"""
cc-anywhere CLI - Simple interface to core functionality.
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

log = logging.getLogger("cc-anywhere")

from cc_anywhere import __version__
from cc_anywhere.core import (
    get_projects,
    generate_pickup_prompt,
    generate_pickup_prompt_from_machine,
    search_history,
    get_machine_name,
    set_machine_name,
    load_all_history,
    get_cross_machine_stats,
)
from cc_anywhere.stats import show_global_stats
from cc_anywhere.sync import (
    setup_sync,
    sync_push,
    sync_pull,
    get_other_machines,
)

# Try rich for nice output, fall back to plain
try:
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    from rich import box
    console = Console()
    RICH = True
except ImportError:
    console = None
    RICH = False


HEADER = """
 ██████╗ ██████╗     █████╗ ███╗   ██╗██╗   ██╗██╗    ██╗██╗  ██╗███████╗██████╗ ███████╗
██╔════╝██╔════╝    ██╔══██╗████╗  ██║╚██╗ ██╔╝██║    ██║██║  ██║██╔════╝██╔══██╗██╔════╝
██║     ██║         ███████║██╔██╗ ██║ ╚████╔╝ ██║ █╗ ██║███████║█████╗  ██████╔╝█████╗
██║     ██║         ██╔══██║██║╚██╗██║  ╚██╔╝  ██║███╗██║██╔══██║██╔══╝  ██╔══██╗██╔══╝
╚██████╗╚██████╗    ██║  ██║██║ ╚████║   ██║   ╚███╔███╔╝██║  ██║███████╗██║  ██║███████╗
 ╚═════╝ ╚═════╝    ╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚══╝╚══╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚══════╝
"""


def _message_role_tag(record: dict) -> str:
    """Human label for captured message records."""
    if record.get("is_compact_summary"):
        return "Summary"
    return "You" if record["role"] == "user" else "Claude"


_VALID_SEARCH_MODES = ("keyword", "semantic", "hybrid")


def _parse_search_args(rest):
    """Pull --mode and --limit out of `rest`; return (query, mode, limit, error).

    `rest` is the args list after `--search` (or after the alias). Anything
    that isn't a recognized flag becomes part of the query string.
    """
    mode = "hybrid"
    limit = 10
    query_parts = []
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--mode" and i + 1 < len(rest):
            mode = rest[i + 1]
            i += 2
            continue
        if a == "--limit" and i + 1 < len(rest):
            try:
                limit = int(rest[i + 1])
            except ValueError:
                return None, None, None, f"--limit expects an integer, got: {rest[i + 1]}"
            i += 2
            continue
        query_parts.append(a)
        i += 1
    if mode not in _VALID_SEARCH_MODES:
        return None, None, None, (
            f"Unknown --mode: {mode!r}. Use one of: "
            + ", ".join(_VALID_SEARCH_MODES)
        )
    query = " ".join(query_parts).strip()
    if not query:
        return None, None, None, (
            "Usage: cc-anywhere --search <query> "
            "[--mode keyword|semantic|hybrid] [--limit N]"
        )
    return query, mode, limit, None


def _print_search_results_keyword(results, query, limit):
    """Print FTS5 keyword results. Same shape as the old --db-search output."""
    from cc_anywhere.sqlite_capture import _local_display
    if not results:
        print(f"No keyword matches for {query!r}.")
        print("Try `--mode hybrid` (default) for fuzzy matches.")
        return
    print(f"Mode: keyword (FTS5) · {len(results[:limit])} of {len(results)} shown\n")
    for r in results[:limit]:
        role_tag = _message_role_tag(r)
        text = r["content"][:120].replace("\n", " ")
        ts = _local_display(r.get("timestamp"))
        print(f"[{r['project_name']}] ({role_tag}, {ts}) {text}...")


def _print_search_results_chunks(results, query, limit, mode):
    """Print chunk-based hybrid/semantic results."""
    from cc_anywhere.sqlite_capture import _local_display
    if not results:
        print(f"No {mode} matches for {query!r}.")
        print("Try `cc-anywhere --index-semantic` if the index is empty,")
        print("or `--mode keyword` for an exact-match fallback.")
        return
    header = f"Mode: {mode}"
    if results[0].get("fallback_used"):
        days = results[0].get("searched_recent_days")
        header += f" · no matches in last {days} days, expanded to all time"
    elif results[0].get("scope"):
        header += f" · scope: {results[0]['scope'].replace('-', ' ')}"
    print(header + "\n")
    for r in results[:limit]:
        ts = _local_display(r.get("started_at"))
        print(f"[{r['project_name']}] ({ts}, {r['match_type']}, {r['score']:.2f})")
        print(f"  {r['excerpt']}")
        print(f"  → cc-anywhere --view {r['chunk_id']}")
        print()


def _run_unified_search(rest):
    """Dispatch `--search <q> [--mode ...] [--limit N]` to the right backend."""
    query, mode, limit, err = _parse_search_args(rest)
    if err:
        print(err, file=sys.stderr)
        return
    if mode == "keyword":
        from cc_anywhere.sqlite_capture import db_search
        results = db_search(query, limit=max(limit, 25))
        _print_search_results_keyword(results, query, limit)
    else:
        from cc_anywhere.semantic import semantic_search
        results = semantic_search(query, limit=max(limit, 10), mode=mode)
        _print_search_results_chunks(results, query, limit, mode)


def _memory_instruction() -> str:
    return (
        "## Memory tool: cc-anywhere\n\n"
        "Local memory of past Claude Code, Codex, and Gemini CLI "
        "sessions. Two query modes:\n\n"
        "**Warm-up / cold start** — when the user asks what they were "
        "working on, what happened recently, what they did yesterday, "
        "or to catch them up, read over recent conversations first. "
        "Do not treat `today`, `yesterday`, `this week`, or "
        "`recently` as keyword queries. These are instructions to "
        "read past conversations in that time window and summarize the "
        "main threads briefly. Use `cc-anywhere --read` for this. "
        "The agent can do this at any time, not just at session start.\n\n"
        "**Topical recall** — when the user names a subject:\n"
        "    cc-anywhere --ask \"<topic>\"\n"
        "Examples: `--ask \"auth decisions\"`, `--ask \"why we chose pg over mysql\"`.\n\n"
        "**Temporal recall** — when the user asks about recent activity:\n"
        "    cc-anywhere --read\n"
        "    cc-anywhere --read \"today\" / \"yesterday\" / \"this week\" / \"catch me up\"\n"
        "No keywords needed; time language routes to a chronological pull.\n"
        "Wide windows return a daily rollup with drill-in hints.\n\n"
        "**Section read / drill in** to a result with:\n"
        "    cc-anywhere --view <chunk_id>\n"
        "    cc-anywhere --source <chunk_id>\n\n"
        "**When NOT to use it:** for the *current* state of files, repos, builds, "
        "or tests, read the filesystem (git log, ls, the file). cc-anywhere "
        "indexes captured conversations, not current code.\n\n"
        "**Discipline:** if a past decision is found, quote it back with the "
        "chunk_id. If your current suggestion would contradict a past decision, "
        "flag it before proceeding. The user built this memory layer specifically "
        "so you'd stop asking them what was decided.\n\n"
        "Full LLM cheatsheet (run from any cwd):\n"
        "    cc-anywhere --llm-guide\n\n"
        "## Recent activity in this project\n\n"
    )


def _print_json_context(answer: str) -> None:
    import json as _json
    print(_json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _memory_instruction() + answer,
        }
    }))


def display_header():
    """Display ASCII header."""
    if RICH:
        console.print(Text(HEADER, style="bold red"))
        console.print("[bold]memory built for AI coding.[/bold]")
        console.print(f"[dim]v{__version__}[/dim]\n")
    else:
        print(HEADER)
        print("memory built for AI coding.")
        print(f"v{__version__}\n")


def get_project_machines(project_name: str) -> list:
    """Get list of OTHER machines that have this project (excludes current)."""
    current = get_machine_name()
    machines = get_other_machines(current)
    result = []
    for m in machines:
        # Skip current machine - we show that as "Local" already
        if m.get("is_current") or m["name"] == current:
            continue
        if project_name in m.get("projects", {}):
            result.append(m["name"])
    return result


def print_projects(projects: list):
    """Display projects list."""
    if not projects:
        print("No projects found.")
        return

    # Get synced machine data
    current_machine = get_machine_name()
    other_machines = get_other_machines(current_machine)

    if RICH:
        table = Table(title="Code Projects", box=box.ROUNDED)
        table.add_column("#", width=3)
        table.add_column("Project", style="cyan")
        table.add_column("Prompts", justify="right")
        table.add_column("Last Active", justify="right")
        if other_machines:
            table.add_column("Machines", style="dim")

        for i, p in enumerate(projects[:15], 1):
            last = p["last_active"].strftime("%b %d") if p["last_active"] else "-"
            if other_machines:
                machines = get_project_machines(p["name"])
                count = len(machines)
                if count == 0:
                    machines_str = "local"
                elif count == 1:
                    machines_str = "1 machine"
                else:
                    machines_str = f"{count} machines"
                table.add_row(str(i), p["name"], str(p["prompts"]), last, machines_str)
            else:
                table.add_row(str(i), p["name"], str(p["prompts"]), last)

        console.print(table)
    else:
        print("\nCode Projects:")
        print("-" * 50)
        for i, p in enumerate(projects[:15], 1):
            last = p["last_active"].strftime("%b %d") if p["last_active"] else "-"
            print(f"{i:2}. {p['name']:<30} {p['prompts']:>4} prompts  {last}")


def interactive():
    """Main interactive mode."""
    projects = get_projects()
    needs_redraw = True

    while True:
        if needs_redraw:
            if RICH:
                console.clear()
                display_header()
            else:
                print("\n")
                display_header()

            print_projects(projects)

            print("\n[Commands]")
            shown = min(len(projects), 15)
            print(f"  1-{shown}   Select project (copy pickup prompt)")
            print("  s     Setup sync    u  Upload    d  Download")
            print("  /     Search        t  Stats     w  Weekly digest")
            print("  m     Monthly       c  Capture   f  DB search")
            print("  r     Refresh       q  Quit")

        needs_redraw = True  # Default to redraw after actions

        try:
            choice = input("\n> ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            break

        if not choice:
            # Empty input - don't redraw
            needs_redraw = False
            continue
        elif choice == 'q':
            break
        elif choice == 'r':
            projects = get_projects()
        elif choice == 's':
            handle_setup()
        elif choice == 'u':
            handle_upload()
        elif choice == 'd':
            handle_download()
        elif choice == '/':
            handle_search()
        elif choice == 't':
            handle_stats()
        elif choice == 'w':
            handle_digest("weekly")
        elif choice == 'm':
            handle_digest("monthly")
        elif choice == 'c':
            handle_capture()
        elif choice == 'f':
            handle_db_search()
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(projects):
                handle_select(projects[idx])


def handle_setup():
    """Setup sync wizard."""
    print("\n[Setup Sync]")
    print("This syncs your Claude context between machines via GitHub.\n")

    name = input(f"Machine name [{get_machine_name()}]: ").strip()
    if name:
        set_machine_name(name)
        print(f"Set machine name: {name}")

    print("\nCreate a PRIVATE repo on GitHub called 'cc-sync'")
    username = input("GitHub username: ").strip()
    if not username:
        print("Cancelled.")
        return

    repo = input("Repo name [cc-sync]: ").strip() or "cc-sync"

    url = setup_sync(username, repo)
    print(f"\nConnected to {url}")
    print("Use 'u' to upload, 'd' to download.")
    input("\nPress Enter...")


def handle_upload():
    """Upload to remote."""
    print("\nUploading...")
    ok, msg = sync_push(get_machine_name(), get_projects())
    print(f"{'Done!' if ok else 'Failed:'} {msg}")
    input("\nPress Enter...")


def handle_download():
    """Download from remote."""
    print("\nDownloading...")
    ok, msg = sync_pull()
    print(f"{'Done!' if ok else 'Failed:'} {msg}")

    # Show other machines
    others = [m for m in get_other_machines(get_machine_name()) if not m["is_current"]]
    if others:
        print(f"\nFound data from: {', '.join(m['name'] for m in others)}")

    input("\nPress Enter...")


def handle_stats():
    """Show usage statistics with heatmap."""
    show_global_stats(load_all_history, get_cross_machine_stats, display_header)
    input("\nPress Enter...")


def handle_digest(period: str = "weekly"):
    """Show daily, weekly, or monthly digest."""
    from cc_anywhere.digest import show_digest
    if RICH:
        console.clear()
        display_header()
    show_digest(period)
    input("\nPress Enter...")


def handle_search():
    """Search conversations."""
    query = input("\nSearch: ").strip()
    if not query:
        return

    results = search_history(query)
    if not results:
        print("No results.")
        input("\nPress Enter...")
        return

    print(f"\nFound {len(results)} results:\n")
    for i, r in enumerate(results[:10], 1):
        text = r["text"][:60].replace("\n", " ")
        print(f"{i}. [{r['project']}] {text}...")

    input("\nPress Enter...")


def copy_to_clipboard(text: str) -> bool:
    """Copy text to clipboard (cross-platform). Returns True if successful."""
    import platform
    import subprocess
    system = platform.system()

    try:
        if system == "Darwin":  # macOS
            process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            process.communicate(text.encode("utf-8"))
            return process.returncode == 0
        elif system == "Windows":
            # Use clip.exe with Popen - handles spaces correctly
            process = subprocess.Popen(
                ["clip.exe"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            process.communicate(text.encode("utf-8"))
            return process.returncode == 0
        else:  # Linux
            process = subprocess.Popen(
                ["xclip", "-selection", "clipboard"],
                stdin=subprocess.PIPE
            )
            process.communicate(text.encode("utf-8"))
            return process.returncode == 0
    except FileNotFoundError:
        return False  # Clipboard tool not installed
    except OSError as e:
        log.debug("Clipboard copy failed: %s", e)
        return False


def get_machine_project_data(project_name: str, machine_name: str) -> dict:
    """Get project data from a specific machine's sync."""
    import json
    sync_dir = Path.home() / ".claude-sync"
    state_file = sync_dir / "machines" / machine_name / "state.json"

    if not state_file.exists():
        return {}

    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        return state.get("projects", {}).get(project_name, {})
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read machine project data: %s", e)
        return {}


def handle_select(project: dict):
    """Handle project selection - show project details with machine data."""
    machines = get_project_machines(project["name"])
    current = get_machine_name()

    # Build detail view
    if RICH:
        console.clear()
        console.print(f"\n[bold cyan]═══ {project['name']} ═══[/bold cyan]\n")

        # Create comparison table
        table = Table(box=box.ROUNDED, show_header=True, title="Machine Comparison")
        table.add_column("Machine", style="cyan")
        table.add_column("Prompts", justify="right")
        table.add_column("Last Active", justify="right")
        table.add_column("Status", style="dim")

        # Add local row as [1]
        local_last = project['last_active'].strftime('%b %d %H:%M') if project['last_active'] else "-"
        table.add_row(
            f"[1] {current} (local)",
            str(project['prompts']),
            local_last,
            "[green]● current[/green]"
        )

        # Add each synced machine starting at [2]
        for i, m in enumerate(machines, 2):
            data = get_machine_project_data(project["name"], m)
            if data:
                prompts = str(data.get('prompts', '?'))
                last_active = data.get('last_active', '-')
                if last_active and last_active != '-':
                    try:
                        dt = datetime.fromisoformat(last_active.replace('Z', '+00:00'))
                        last_active = dt.strftime('%b %d %H:%M')
                    except (TypeError, ValueError):
                        pass
                # Clean up machine name and mark as remote
                display_name = m.replace('.local', '').replace('.localdomain', '')
                table.add_row(f"[{i}] {display_name} (remote)", prompts, last_active, "[dim]synced[/dim]")

        console.print(table)

        # Show path
        console.print(f"\n[dim]Path: {project['path']}[/dim]")

        console.print("\n[bold]Commands:[/bold]")
        total_machines = 1 + len(machines)
        console.print(f"  1-{total_machines}     Copy pickup prompt from that machine")
        console.print("  Enter   Go back")
    else:
        print(f"\n=== {project['name']} ===\n")
        print(f"{'Machine':<30} {'Prompts':>8} {'Last Active':>15}")
        print("-" * 55)

        local_last = project['last_active'].strftime('%b %d %H:%M') if project['last_active'] else "-"
        print(f"{current + ' (local)':<30} {project['prompts']:>8} {local_last:>15}")

        for i, m in enumerate(machines, 1):
            data = get_machine_project_data(project["name"], m)
            if data:
                prompts = data.get('prompts', '?')
                last_active = data.get('last_active', '-')[:16] if data.get('last_active') else '-'
                print(f"[{i}] {m:<27} {prompts:>8} {last_active:>15}")

        print(f"\nPath: {project['path']}")
        print("\nCommands:")
        print("  c       Copy pickup prompt (local)")
        if machines:
            print(f"  1-{len(machines)}     Copy pickup from that machine")
        print("  Enter   Go back")

    choice = input("\n> ").strip().lower()

    if choice == '1':
        # Local machine
        prompt = generate_pickup_prompt(project)
        if copy_to_clipboard(prompt):
            print(f"\nCopied pickup prompt from {current} (local) to clipboard!")
        else:
            print("\nPickup prompt:")
            print(prompt)
        input("\nPress Enter...")
    elif choice.isdigit() and int(choice) >= 2 and int(choice) <= len(machines) + 1:
        # Remote machine (index 2+ maps to machines[0+])
        selected_machine = machines[int(choice) - 2]
        display_name = selected_machine.replace('.local', '').replace('.localdomain', '')
        prompt = generate_pickup_prompt_from_machine(project["name"], selected_machine)
        if copy_to_clipboard(prompt):
            print(f"\nCopied pickup prompt from {display_name} (remote) to clipboard!")
        else:
            print("\nPickup prompt:")
            print(prompt)
        input("\nPress Enter...")


def handle_capture():
    """Run session capture from JSONL files into SQLite (Claude Code + Codex + Gemini)."""
    from cc_anywhere.sqlite_capture import (
        capture_sessions, capture_codex_sessions,
        capture_gemini_sessions, get_capture_stats,
    )
    print("\nCapturing sessions...")
    cc = capture_sessions()
    print(f"Claude Code: {cc['new_sessions']} new sessions, "
          f"{cc['new_messages']} new messages "
          f"({cc['projects_scanned']} projects scanned)")
    cx = capture_codex_sessions()
    print(f"Codex:       {cx['new_sessions']} new sessions, "
          f"{cx['new_messages']} new messages "
          f"({cx['files_scanned']} files scanned)")
    gm = capture_gemini_sessions()
    print(f"Gemini:      {gm['new_sessions']} new sessions, "
          f"{gm['new_messages']} new messages "
          f"({gm['files_scanned']} files scanned)")

    stats = get_capture_stats()
    print(f"\nDB totals: {stats['total_sessions']} sessions, "
          f"{stats['total_messages']} messages, "
          f"{stats['projects']} projects "
          f"({stats['db_size_bytes'] / 1024:.1f} KB)")
    input("\nPress Enter...")


def handle_db_search():
    """Full-text search across captured sessions."""
    from cc_anywhere.sqlite_capture import db_search, _local_display
    query = input("\nDB Search: ").strip()
    if not query:
        return

    results = db_search(query)
    if not results:
        print("No results found.")
        input("\nPress Enter...")
        return

    print(f"\nFound {len(results)} results:\n")
    for i, r in enumerate(results[:15], 1):
        role_tag = _message_role_tag(r)
        text = r["content"][:80].replace("\n", " ")
        ts = _local_display(r.get("timestamp"))
        print(f"{i:2}. [{r['project_name']}] ({role_tag}, {ts})")
        print(f"    {text}...")

    input("\nPress Enter...")


def search_main():
    """Search entry point."""
    args = sys.argv[1:]
    if not args:
        print("Usage: claude-search <query>")
        return
    for r in search_history(" ".join(args))[:10]:
        print(f"[{r['project']}] {r['text'][:80]}...")


def sync_main():
    """Sync entry point."""
    args = sys.argv[1:]
    if not args or args[0] == "push":
        ok, msg = sync_push(get_machine_name(), get_projects())
        print(msg)
    elif args[0] == "pull":
        ok, msg = sync_pull()
        print(msg)
    else:
        print("Usage: claude-sync [push|pull]")


def main():
    """Entry point."""
    args = sys.argv[1:]

    if not args:
        interactive()
    elif args[0] == "--list":
        print_projects(get_projects())
    elif args[0] == "--search":
        _run_unified_search(args[1:])
    elif args[0] == "--grep-history" and len(args) > 1:
        # Legacy substring grep over the raw history.jsonl (the original
        # behavior of --search). Kept for any user with this baked into
        # a script. Most users want --search instead.
        for r in search_history(" ".join(args[1:]))[:10]:
            print(f"[{r['project']}] {r['text'][:80]}...")
    elif args[0] == "--sync":
        ok, msg = sync_push(get_machine_name(), get_projects())
        print(msg)
    elif args[0] == "--pull":
        ok, msg = sync_pull()
        print(msg)
    elif args[0] == "--backup":
        from cc_anywhere.backup import backup_history_monthly, get_backup_stats
        new = backup_history_monthly()
        stats = get_backup_stats()
        print(f"Backed up {new} new entries")
        print(f"Total: {stats['total_entries']} entries across {len(stats['files'])} months ({stats['total_size']/1024:.1f} KB)")
        for f in stats['files']:
            print(f"  {f['month']}: {f['entries']} entries")
    elif args[0] == "--daily":
        from cc_anywhere.digest import show_digest
        show_digest("daily")
    elif args[0] == "--weekly":
        from cc_anywhere.digest import show_digest
        show_digest("weekly")
    elif args[0] == "--monthly":
        from cc_anywhere.digest import show_digest
        show_digest("monthly")
    elif args[0] == "--init":
        from cc_anywhere.init_setup import run_init
        return run_init()
    elif args[0] == "--capture":
        from cc_anywhere.sqlite_capture import (
            capture_sessions, capture_codex_sessions,
            capture_gemini_sessions, get_capture_stats,
        )
        cc = capture_sessions()
        print(f"Claude Code: {cc['new_sessions']} new sessions, "
              f"{cc['new_messages']} new messages "
              f"({cc['projects_scanned']} projects scanned)")
        cx = capture_codex_sessions()
        print(f"Codex:       {cx['new_sessions']} new sessions, "
              f"{cx['new_messages']} new messages "
              f"({cx['files_scanned']} files scanned)")
        gm = capture_gemini_sessions()
        print(f"Gemini:      {gm['new_sessions']} new sessions, "
              f"{gm['new_messages']} new messages "
              f"({gm['files_scanned']} files scanned)")
        stats = get_capture_stats()
        print(f"DB totals: {stats['total_sessions']} sessions, "
              f"{stats['total_messages']} messages, "
              f"{stats['projects']} projects "
              f"({stats['db_size_bytes'] / 1024:.1f} KB)")

        # Auto-index any new messages so a single --capture call leaves the
        # semantic search layer current. Skip cleanly if nothing new arrived.
        new_messages = (cc.get("new_messages", 0)
                        + cx.get("new_messages", 0)
                        + gm.get("new_messages", 0))
        if "--no-index" not in args and new_messages > 0:
            from cc_anywhere.semantic import rebuild_semantic_index
            ix = rebuild_semantic_index(full_rebuild=False)
            if ix.get("chunks", 0) > 0:
                print(f"Indexed:     {ix['chunks']} new chunks "
                      f"from {ix['messages']} new messages")
    elif args[0] == "--sync-archive":
        from cc_anywhere.sync import sync_push_archive
        # Optional --to <path> selects a non-GitHub destination.
        dest = None
        if "--to" in args:
            i = args.index("--to")
            if i + 1 < len(args):
                dest = args[i + 1]
            else:
                print("Usage: cc-anywhere --sync-archive [--to <path>]")
                return
        print("Building full-history archive...")
        ok, msg = sync_push_archive(get_machine_name(), dest=dest)
        print(("Done. " if ok else "Failed: ") + msg)
        if ok and dest is None:
            print("\nOther machines: run `cc-anywhere --pull` to import.")
        elif ok:
            print(f"\nOther machines: copy or mount {dest} and run "
                  f"`cc-anywhere --pull` (with --from <path> when "
                  f"that flag ships) to import.")
    elif args[0] == "--backfill-sources":
        from cc_anywhere.sqlite_capture import backfill_source_provenance
        stats = backfill_source_provenance()
        print(
            "Backfilled source provenance for "
            f"{stats['messages_updated']} messages "
            f"({stats['claude_files_scanned']} Claude files, "
            f"{stats['codex_files_scanned']} Codex files scanned)"
        )
    elif args[0] == "--db-search" and len(args) > 1:
        # Deprecated alias — translates to `--search <q> --mode keyword`.
        print("note: --db-search is deprecated; use `cc-anywhere --search <q> --mode keyword`",
              file=sys.stderr)
        _run_unified_search(args[1:] + ["--mode", "keyword"])
    elif args[0] == "--index-semantic":
        from cc_anywhere.semantic import rebuild_semantic_index
        full_rebuild = "--rebuild" in args
        stats = rebuild_semantic_index(full_rebuild=full_rebuild)
        new_chunks = stats["chunks"]
        new_msgs = stats["messages"]
        total_sessions = stats["sessions"]
        skipped = stats.get("skipped_sessions", 0)
        if full_rebuild:
            print(f"Full rebuild: indexed {new_chunks} chunks from "
                  f"{new_msgs} messages across {total_sessions} sessions")
        elif new_chunks == 0:
            print(f"Up to date: {skipped} of {total_sessions} sessions already indexed, "
                  f"no new chunks needed")
        else:
            print(f"Appended {new_chunks} new chunks from {new_msgs} new messages "
                  f"({skipped} of {total_sessions} sessions already current)")
    elif args[0] == "--semantic-search" and len(args) > 1:
        # Deprecated alias — translates to `--search <q> --mode hybrid`,
        # which matches what --semantic-search already does today (the old
        # name was misleading: scoring fused cosine + bm25 + overlap).
        print("note: --semantic-search is deprecated; use `cc-anywhere --search <q>` (hybrid is default)",
              file=sys.stderr)
        _run_unified_search(args[1:] + ["--mode", "hybrid"])
    elif args[0] == "--read":
        from cc_anywhere.semantic import read_conversations
        json_context = "--json-context" in args
        query_args = [a for a in args[1:] if a != "--json-context"]
        query = " ".join(query_args).strip() if query_args else None
        try:
            result = read_conversations(query)
        except ValueError as e:
            print(str(e))
            print("Examples: `cc-anywhere --read`, `--read today`, `--read this week`")
            return
        if json_context:
            _print_json_context(result["answer"])
        else:
            print(result["answer"])
    elif args[0] == "--ask" and len(args) > 1:
        from cc_anywhere.semantic import ask_conversations
        # --json-context wraps the answer in a Claude Code SessionStart-hook
        # envelope so the SessionStart hook doesn't need jq to format it:
        #   { "hookSpecificOutput": { "hookEventName": "SessionStart",
        #                              "additionalContext": "<answer>" } }
        json_context = "--json-context" in args
        query_args = [a for a in args[1:] if a != "--json-context"]
        if not query_args:
            print("Usage: cc-anywhere --ask <query> [--json-context]")
            return
        query = " ".join(query_args)
        result = ask_conversations(query)
        answer = result["answer"]
        if json_context:
            _print_json_context(answer)
        else:
            print(answer)
    elif args[0] == "--view" and len(args) > 1:
        from cc_anywhere.semantic import view_chunk
        from cc_anywhere.sqlite_capture import _local_display
        chunk_id = args[1]
        chunk = view_chunk(chunk_id)
        if chunk is None:
            print(f"No chunk found matching: {chunk_id}")
        else:
            # Header: project, source, time range, length
            project = chunk.get("project_name") or "(unknown project)"
            source = chunk.get("source") or "?"
            originator = chunk.get("client_originator")
            version = chunk.get("client_version")
            label = chunk.get("session_label")
            started = _local_display(chunk.get("started_at"))
            ended = _local_display(chunk.get("ended_at"))
            msgs = chunk.get("message_count") or "?"
            chars = len(chunk.get("content") or "")

            print(f"[{project}]  {source}", end="")
            if originator:
                tag = f"  ({originator}"
                if version:
                    tag += f" {version}"
                tag += ")"
                print(tag, end="")
            print()
            if label and label != project:
                print(f"  session: {label}")
            print(f"  {started}  →  {ended}  ·  {msgs} messages  ·  {chars} chars")
            print(f"  chunk_id: {chunk['chunk_id']}")
            if chunk.get("source_path"):
                src = chunk["source_path"]
                start_line = chunk.get("source_start_line")
                end_line = chunk.get("source_end_line")
                if start_line and end_line:
                    src += f":{start_line}-{end_line}"
                print(f"  source: {src}")
                print(f"  → cc-anywhere --source {chunk['chunk_id']}")
            print()
            print(chunk.get("content") or "(empty)")
    elif args[0] == "--source" and len(args) > 1:
        from cc_anywhere.semantic import view_source
        from cc_anywhere.sqlite_capture import _local_display
        chunk_id = args[1]
        source = view_source(chunk_id)
        if source is None:
            print(f"No chunk found matching: {chunk_id}")
        else:
            project = source.get("project_name") or "(unknown project)"
            started = _local_display(source.get("started_at"))
            ended = _local_display(source.get("ended_at"))
            print(f"[{project}] raw transcript source")
            print(f"  chunk_id: {source['chunk_id']}")
            print(f"  session_id: {source['session_id']}")
            print(f"  {started}  →  {ended}")
            if source.get("source_path"):
                src = source["source_path"]
                start_line = source.get("source_start_line")
                end_line = source.get("source_end_line")
                if start_line and end_line:
                    src += f":{start_line}-{end_line}"
                print(f"  source: {src}")
            if source.get("source_byte_start") is not None:
                print(
                    "  bytes: "
                    f"{source.get('source_byte_start')}-{source.get('source_byte_end')}"
                )
            if source.get("error"):
                print(f"\n{source['error']}")
            elif source.get("raw_lines"):
                print()
                for number, line in source["raw_lines"]:
                    print(f"{number:>6}: {line}")
            else:
                print("\nNo raw lines available.")
    elif args[0] == "--db-stats":
        from cc_anywhere.sqlite_capture import get_capture_stats, _local_display
        stats = get_capture_stats()
        print(f"Sessions:  {stats['total_sessions']}")
        print(f"Messages:  {stats['total_messages']}")
        print(f"Projects:  {stats['projects']}")
        print(f"DB size:   {stats['db_size_bytes'] / 1024:.1f} KB")
        if stats['earliest_message']:
            print(f"Earliest:  {_local_display(stats['earliest_message'])}")
        if stats['latest_message']:
            print(f"Latest:    {_local_display(stats['latest_message'])}")
    elif args[0] == "--projects":
        from cc_anywhere.review import list_projects
        print(list_projects())
    elif args[0] == "--init-sidecar" and len(args) > 1:
        from cc_anywhere.review import init_sidecar
        path = args[1]
        name = None
        if "--name" in args:
            i = args.index("--name")
            if i + 1 < len(args):
                name = args[i + 1]
        print(init_sidecar(path, name=name))
    elif args[0] == "--deep-sessions":
        from cc_anywhere.review import list_deep_sessions
        top = 20
        min_msgs = 30
        if "--top" in args:
            i = args.index("--top")
            if i + 1 < len(args):
                try: top = int(args[i + 1])
                except ValueError: pass
        if "--min" in args:
            i = args.index("--min")
            if i + 1 < len(args):
                try: min_msgs = int(args[i + 1])
                except ValueError: pass
        print(list_deep_sessions(top=top, min_user_messages=min_msgs))
    elif args[0] == "--timeline" and len(args) > 1:
        from cc_anywhere.review import reconstruct_timeline
        include_asst = "--with-assistant" in args
        query_args = [a for a in args[1:] if a not in ("--with-assistant",)]
        query = " ".join(query_args)
        print(reconstruct_timeline(query, include_assistant=include_asst))
    elif args[0] == "--evaluate-session" and len(args) > 1:
        from cc_anywhere.evaluate import evaluate_session
        target = args[1]
        model = "claude-sonnet-4-6"
        if "--model" in args:
            i = args.index("--model")
            if i + 1 < len(args):
                model = args[i + 1]
        evaluate_session(target, model=model)
    elif args[0] == "--capture-claude-ai" and len(args) > 1:
        from cc_anywhere.sqlite_capture import capture_claude_ai_export
        path = args[1]
        s = capture_claude_ai_export(path)
        print(f"Claude.ai: scanned {s['conversations_scanned']} conversations, "
              f"{s['new_sessions']} new sessions, "
              f"{s['new_messages']} new messages, "
              f"{s['skipped_empty']} empty skipped")
    elif args[0] == "--about-me":
        from cc_anywhere.persona import about_me
        days = 30
        output = None
        if "--days" in args:
            i = args.index("--days")
            if i + 1 < len(args):
                try:
                    days = int(args[i + 1])
                except ValueError:
                    print(f"Invalid --days value: {args[i + 1]}")
                    return
        if "--output" in args:
            i = args.index("--output")
            if i + 1 < len(args):
                output = args[i + 1]
        md = about_me(days=days, output_path=output)
        print(md)
        if output:
            print(f"\nSaved to {output}")
    elif args[0] == "--git-analysis":
        from cc_anywhere.digest import show_git_analysis
        days = int(args[1]) if len(args) > 1 else 30
        show_git_analysis(days)
    elif args[0] in ("--version", "-v"):
        print(f"cc-anywhere v{__version__}")
    elif args[0] == "--help-guide":
        from cc_anywhere.help import show_help_guide
        show_help_guide()
    elif args[0] == "--llm-guide":
        # LLM-facing usage reference. Surfaced as a CLI command so agents
        # in any cwd (not just the cc-anywhere repo) can fetch it. Mirrors
        # LLM-CHEATSHEET.md at the repo root.
        from cc_anywhere.llm_guide import show_llm_guide
        show_llm_guide()
    elif args[0] == "--usage":
        # /usage-style overview — modeled on Claude Code's /usage but with
        # longer time horizons (not capped at 30d), per-project breakdown,
        # and per-machine breakdown when sync data is present.
        from cc_anywhere.stats import show_usage
        show_usage()
    elif args[0] == "--help":
        print(f"cc-anywhere v{__version__} - See, search and sync your Claude Code, Codex, and Gemini CLI sessions across machines")
        print("\nUsage:")
        print("  cc-anywhere              Interactive mode")
        print("  cc-anywhere --init       Set up hooks + periodic capture (one-time)")
        print("  cc-anywhere --list       List projects")
        print("\nSearch:")
        print("  cc-anywhere --search <q>                       Search captured sessions (default: hybrid)")
        print("    --mode keyword                                 Exact-match (FTS5)")
        print("    --mode semantic                                Conceptual (cosine similarity)")
        print("    --mode hybrid                                  Both, ranked together (default)")
        print("    --limit N                                      Cap results (default: 10)")
        print("  cc-anywhere --read [window]                    Read recent conversations chronologically")
        print("  cc-anywhere --ask <q>                          Search + LLM-synthesized answer with quotes")
        print("  cc-anywhere --view <chunk_id>                  Read the full content of a chunk")
        print("  cc-anywhere --source <chunk_id>                Show raw transcript provenance")
        print("\nCapture & sync:")
        print("  cc-anywhere --capture    Capture sessions to SQLite DB")
        print("  cc-anywhere --index-semantic Build natural-language search index")
        print("  cc-anywhere --backfill-sources  Link old DB rows to raw transcripts")
        print("  cc-anywhere --sync       Push to remote")
        print("  cc-anywhere --pull       Pull from remote")
        print("  cc-anywhere --backup     Backup history to monthly archive")
        print("\nDigests & stats:")
        print("  cc-anywhere --usage      Usage overview: activity windows, projects, machines, sources")
        print("  cc-anywhere --daily      Daily activity digest (last 48 hours)")
        print("  cc-anywhere --weekly     Weekly activity digest")
        print("  cc-anywhere --monthly    Monthly activity digest")
        print("  cc-anywhere --db-stats   Capture database statistics (basic counts)")
        print("  cc-anywhere --git-analysis [days]  Git correlation report")
        print("\nReference:")
        print("  cc-anywhere --help-guide Full documentation")
        print("  cc-anywhere --llm-guide  LLM-facing usage reference (run this if you are an AI agent)")
        print("  cc-anywhere --version")
        print("\nDeprecated (use --search):")
        print("  cc-anywhere --db-search <q>        → --search <q> --mode keyword")
        print("  cc-anywhere --semantic-search <q>  → --search <q>  (hybrid is default)")
        print("  cc-anywhere --grep-history <q>     legacy substring grep over raw history")
    else:
        print(f"Unknown option: {args[0]}")
        print("Use --help for usage.")


if __name__ == "__main__":
    main()
