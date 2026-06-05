#!/usr/bin/env python3
"""
Statistics/analytics display for cc-anywhere.
"""

import calendar
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("cc-anywhere")

# Try to import rich for nice output
try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


def show_global_stats(load_all_history, get_cross_machine_stats, display_header):
    """Show global usage statistics across all projects.

    Args:
        load_all_history: Function to load all history entries
        get_cross_machine_stats: Function to get cross-machine stats
        display_header: Function to display the header
    """
    history = load_all_history()
    if not history:
        print("No history found.")
        return

    # Parse all timestamps and organize by date/project
    days_active = set()
    project_counts = defaultdict(int)
    hourly_counts = defaultdict(int)
    daily_counts = defaultdict(int)  # day of week
    dates_list = []

    for entry in history:
        ts = entry.get("timestamp")
        project = entry.get("project", "unknown")
        if project:
            project_name = Path(project).name
            project_counts[project_name] += 1

        if ts:
            try:
                dt = datetime.fromtimestamp(ts / 1000)
                days_active.add(dt.strftime("%Y-%m-%d"))
                dates_list.append(dt)
                hourly_counts[dt.hour] += 1
                daily_counts[dt.weekday()] += 1
            except (TypeError, ValueError, OSError):
                pass

    if not dates_list:
        print("No timestamps found in history.")
        return

    # Calculate stats
    dates_list.sort()
    first_date = dates_list[0]
    last_date = dates_list[-1]
    total_days = (last_date - first_date).days + 1
    active_days = len(days_active)

    # Last 30 days
    today = datetime.now()
    last_30_active = len([d for d in days_active
                         if (today - datetime.strptime(d, "%Y-%m-%d")).days <= 30])

    # Current streak
    current_streak = 0
    check_date = today.date()
    while check_date.strftime("%Y-%m-%d") in days_active:
        current_streak += 1
        check_date = check_date - timedelta(days=1)

    # Longest streak
    sorted_days = sorted(days_active)
    longest_streak = 0
    current_run = 1
    for i in range(1, len(sorted_days)):
        prev = datetime.strptime(sorted_days[i-1], "%Y-%m-%d")
        curr = datetime.strptime(sorted_days[i], "%Y-%m-%d")
        if (curr - prev).days == 1:
            current_run += 1
        else:
            longest_streak = max(longest_streak, current_run)
            current_run = 1
    longest_streak = max(longest_streak, current_run)

    # Peak hour
    peak_hour = max(hourly_counts, key=hourly_counts.get) if hourly_counts else 0

    # Top projects
    top_projects = sorted(project_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    # Build heatmap for last 3 months
    def get_heatmap_char(count):
        if count == 0: return "·"
        elif count <= 5: return "░"
        elif count <= 15: return "▒"
        elif count <= 30: return "▓"
        else: return "█"

    # Get machine name from config or use placeholder
    from cc_anywhere._paths import CONFIG_FILE, migrate_legacy_paths
    migrate_legacy_paths()
    config_file = CONFIG_FILE
    machine_name = None
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text(encoding="utf-8"))
            machine_name = config.get("machine_name")
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load config: %s", e)
    if not machine_name:
        machine_name = "this machine"

    # Display
    if RICH_AVAILABLE:
        display_header()
        console.print(f"[bold]Local Statistics ({machine_name})[/bold]\n")

        # Heatmap - last 13 weeks
        console.print("[dim]Activity (last 13 weeks)[/dim]")

        # Build daily activity counts
        daily_activity = defaultdict(int)
        for entry in history:
            ts = entry.get("timestamp")
            if ts:
                try:
                    dt = datetime.fromtimestamp(ts / 1000)
                    if (today - dt).days <= 91:  # ~13 weeks
                        daily_activity[dt.strftime("%Y-%m-%d")] += 1
                except (TypeError, ValueError, OSError):
                    pass

        # Print month labels
        months_row = "      "
        current_month = None
        for week in range(13):
            week_start = today - timedelta(days=today.weekday() + (12-week)*7)
            if week_start.month != current_month:
                current_month = week_start.month
                months_row += calendar.month_abbr[current_month][:3] + " "
            else:
                months_row += "    "
        console.print(f"[dim]{months_row}[/dim]")

        # Print heatmap rows (Mon, Wed, Fri or all days)
        day_names = ["Mon", "", "Wed", "", "Fri", "", ""]
        for dow in range(7):
            row = f"[dim]{day_names[dow]:>3}[/dim] "
            for week in range(13):
                # Calculate the date for this cell
                week_start = today - timedelta(days=today.weekday() + (12-week)*7)
                cell_date = week_start + timedelta(days=dow)
                cell_date_obj = cell_date.date() if isinstance(cell_date, datetime) else cell_date
                if cell_date_obj <= today.date():
                    date_str = cell_date_obj.strftime("%Y-%m-%d")
                    count = daily_activity.get(date_str, 0)
                    char = get_heatmap_char(count)
                    if char == "█":
                        row += f"[green]{char}[/green]"
                    elif char == "▓":
                        row += f"[bright_green]{char}[/bright_green]"
                    elif char == "▒":
                        row += f"[yellow]{char}[/yellow]"
                    elif char == "░":
                        row += f"[dim yellow]{char}[/dim yellow]"
                    else:
                        row += f"[dim]{char}[/dim]"
                else:
                    row += " "
            console.print(row)

        console.print(f"\n[dim]      Less [/dim][dim]·[/dim] [dim yellow]░[/dim yellow] [yellow]▒[/yellow] [bright_green]▓[/bright_green] [green]█[/green] [dim]More[/dim]\n")

        # Stats summary
        stats_table = Table(box=None, show_header=False, padding=(0, 2))
        stats_table.add_column("Label", style="dim")
        stats_table.add_column("Value", style="bold")
        stats_table.add_column("Label2", style="dim")
        stats_table.add_column("Value2", style="bold")

        stats_table.add_row(
            "Total prompts:", f"{len(history):,}",
            "Projects:", f"{len(project_counts)}"
        )
        stats_table.add_row(
            "Active days:", f"{active_days}/{total_days}",
            "Last 30 days:", f"{last_30_active}/30"
        )
        stats_table.add_row(
            "Current streak:", f"{current_streak} days",
            "Longest streak:", f"{longest_streak} days"
        )
        stats_table.add_row(
            "Peak hour:", f"{peak_hour}:00-{peak_hour+1}:00",
            "Since:", first_date.strftime("%b %d, %Y")
        )
        console.print(stats_table)

        # Top projects table
        console.print(f"\n[bold]Top Projects (this machine)[/bold]")
        proj_table = Table(box=box.SIMPLE, show_header=True)
        proj_table.add_column("#", style="dim", width=3)
        proj_table.add_column("Project", style="cyan")
        proj_table.add_column("Prompts", justify="right")
        proj_table.add_column("Share", justify="right", style="dim")

        total_prompts = sum(project_counts.values())
        for i, (proj, count) in enumerate(top_projects, 1):
            pct = (count / total_prompts * 100) if total_prompts > 0 else 0
            proj_table.add_row(str(i), proj, f"{count:,}", f"{pct:.1f}%")

        console.print(proj_table)

        # Cross-machine stats section
        cross_stats = get_cross_machine_stats()
        if cross_stats["machines"] and len(cross_stats["machines"]) > 0:
            console.print("\n[bold]Global Stats (Synced Machines)[/bold]")

            # Combined totals
            console.print(f"[dim]Combined across {len(cross_stats['machines'])} machines:[/dim]")
            console.print(f"  Total prompts:  [cyan]{cross_stats['total_prompts']:,}[/cyan]")
            console.print(f"  Total projects: [cyan]{cross_stats['total_projects']:,}[/cyan]")
            console.print(f"  Total sessions: [cyan]{cross_stats['total_sessions']:,}[/cyan]")

            # Per-machine breakdown
            console.print("\n[dim]By machine:[/dim]")
            machine_table = Table(box=box.SIMPLE, show_header=True)
            machine_table.add_column("Machine", style="cyan")
            machine_table.add_column("Prompts", justify="right")
            machine_table.add_column("Projects", justify="right")
            machine_table.add_column("Sessions", justify="right")
            machine_table.add_column("Last Sync", style="dim")

            for m in sorted(cross_stats["machines"], key=lambda x: x["prompts"], reverse=True):
                name = m["name"]
                if m["is_current"]:
                    name = f"{name} (this)"
                last_sync = ""
                if m.get("last_updated"):
                    try:
                        dt = datetime.fromisoformat(m["last_updated"])
                        last_sync = dt.strftime("%b %d %H:%M")
                    except (TypeError, ValueError):
                        pass
                machine_table.add_row(
                    name,
                    f"{m['prompts']:,}",
                    str(m["projects"]),
                    str(m["sessions"]),
                    last_sync
                )

            console.print(machine_table)

            # Top projects across all machines
            if cross_stats["all_projects"]:
                console.print("\n[dim]Top projects (synced machines):[/dim]")
                cross_proj_table = Table(box=box.SIMPLE, show_header=True)
                cross_proj_table.add_column("Project", style="cyan")
                cross_proj_table.add_column("Total", justify="right")
                cross_proj_table.add_column("%", justify="right", style="dim")
                cross_proj_table.add_column("Breakdown", style="dim")

                sorted_projects = sorted(
                    cross_stats["all_projects"].items(),
                    key=lambda x: x[1]["total"],
                    reverse=True
                )[:10]

                grand_total = cross_stats["total_prompts"] or 1

                for proj_name, proj_data in sorted_projects:
                    pct = (proj_data['total'] / grand_total) * 100
                    breakdown = ", ".join(
                        f"{m.replace('.local', '').replace('.localdomain', '')}: {c}"
                        for m, c in proj_data["machines"].items()
                    )
                    cross_proj_table.add_row(
                        proj_name[:30],
                        f"{proj_data['total']:,}",
                        f"{pct:.1f}%",
                        breakdown[:50]
                    )

                console.print(cross_proj_table)

    else:
        # Plain text fallback
        print("\n=== Global Usage Statistics ===\n")
        print(f"Total prompts:    {len(history):,}")
        print(f"Projects:         {len(project_counts)}")
        print(f"Active days:      {active_days}/{total_days}")
        print(f"Last 30 days:     {last_30_active}/30")
        print(f"Current streak:   {current_streak} days")
        print(f"Longest streak:   {longest_streak} days")
        print(f"Peak hour:        {peak_hour}:00-{peak_hour+1}:00")
        print(f"Since:            {first_date.strftime('%b %d, %Y')}")
        print("\nTop Projects:")
        for i, (proj, count) in enumerate(top_projects[:5], 1):
            print(f"  {i}. {proj}: {count:,}")

        # Cross-machine stats (plain text)
        cross_stats = get_cross_machine_stats()
        if cross_stats["machines"]:
            print(f"\n=== Global Stats ({len(cross_stats['machines'])} Synced Machines) ===")
            print(f"Combined prompts:  {cross_stats['total_prompts']:,}")
            print(f"Combined projects: {cross_stats['total_projects']:,}")
            print("\nBy machine:")
            for m in cross_stats["machines"]:
                marker = " (this)" if m["is_current"] else ""
                print(f"  {m['name']}{marker}: {m['prompts']:,} prompts, {m['projects']} projects")


# ─── Usage view ───────────────────────────────────────────────────────────
# Modeled on Claude Code's /usage but going further on three axes Claude's
# /usage doesn't cover: longer time horizons (not capped at 30 days),
# per-project breakdown, per-machine breakdown when sync data exists.

def show_usage():
    """Print a /usage-style overview of captured-session activity.

    Uses the SQLite capture DB directly. Sections:
      - Capture status (total counts, DB size, last capture time)
      - Activity windows (today / this week / this month / last 30d / all time)
      - By source (claude-code / codex / gemini)
      - By machine (only shown when meaningful — multiple machines or named)
      - Top projects (this month + all time)
      - Recent marathon sessions (highest message-count in last 30 days)
    """
    from datetime import datetime, timedelta, timezone
    from cc_anywhere.sqlite_capture import get_db, _local_display

    db = get_db()
    now = datetime.now(timezone.utc)

    def _iso_cutoff(hours):
        return (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    windows = [
        ("Today",        _iso_cutoff(24)),
        ("This week",    _iso_cutoff(168)),
        ("This month",   _iso_cutoff(720)),
    ]

    # ── Helper: merge project rows by lowercase name ──────────
    # The capture sources sometimes record different casings of the same
    # project (e.g. "Webapp" and "WebApp"). For usage display we want
    # them merged. Pick the variant with the highest session count as
    # the display label, sum the counts.
    def _merge_by_lower(rows):
        merged = {}
        for name, count in rows:
            if name is None:
                continue
            key = name.lower()
            if key not in merged:
                merged[key] = (name, count)
            else:
                existing_name, existing_count = merged[key]
                display = name if count > existing_count else existing_name
                merged[key] = (display, existing_count + count)
        return sorted(merged.values(), key=lambda x: x[1], reverse=True)

    # ── Capture status ─────────────────────────────────────────
    total_sessions = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    total_messages = db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    projects = db.execute(
        "SELECT COUNT(DISTINCT LOWER(project_name)) FROM sessions"
    ).fetchone()[0]
    earliest_row = db.execute(
        "SELECT MIN(timestamp) FROM messages WHERE timestamp IS NOT NULL"
    ).fetchone()
    latest_row = db.execute(
        "SELECT MAX(timestamp) FROM messages WHERE timestamp IS NOT NULL"
    ).fetchone()
    earliest = earliest_row[0] if earliest_row else None
    latest = latest_row[0] if latest_row else None

    # DB size (file)
    try:
        from cc_anywhere._paths import DB_PATH
        db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
        db_size_mb = db_size_bytes / (1024 * 1024)
    except Exception:
        db_size_mb = 0

    # ── Snapshot stats (streaks, peak hour, heatmap, token estimate) ──
    # Per-day / per-hour counts in LOCAL time: SQLite converts the ISO-8601
    # 'Z' timestamps with 'localtime' so day boundaries, streaks and the peak
    # hour line up with the wall clock instead of UTC.
    day_rows = db.execute(
        "SELECT strftime('%Y-%m-%d', timestamp, 'localtime') AS day, COUNT(*) "
        "FROM messages WHERE timestamp IS NOT NULL GROUP BY day"
    ).fetchall()
    daily_activity = {day: cnt for day, cnt in day_rows if day}
    days_active = set(daily_activity)
    active_days = len(days_active)

    # Current streak (consecutive local days ending today)
    current_streak = 0
    check = datetime.now().date()
    while check.strftime("%Y-%m-%d") in days_active:
        current_streak += 1
        check = check - timedelta(days=1)

    # Longest streak ever
    sorted_days = sorted(days_active)
    longest_streak = 0
    run = 1
    for i in range(1, len(sorted_days)):
        prev = datetime.strptime(sorted_days[i - 1], "%Y-%m-%d")
        curr = datetime.strptime(sorted_days[i], "%Y-%m-%d")
        if (curr - prev).days == 1:
            run += 1
        else:
            longest_streak = max(longest_streak, run)
            run = 1
    longest_streak = max(longest_streak, run) if sorted_days else 0

    # Peak hour (local)
    peak_row = db.execute(
        "SELECT strftime('%H', timestamp, 'localtime') AS hr, COUNT(*) c "
        "FROM messages WHERE timestamp IS NOT NULL "
        "GROUP BY hr ORDER BY c DESC LIMIT 1"
    ).fetchone()
    peak_hour = int(peak_row[0]) if peak_row and peak_row[0] is not None else None

    # Token estimate — captured *text* only (~chars/4). cc-anywhere stores
    # message text, not tool calls/outputs, so this is the conversation we
    # hold, not total API spend.
    char_row = db.execute("SELECT SUM(LENGTH(content)) FROM messages").fetchone()
    est_tokens = (char_row[0] or 0) / 4

    # Tools used (capture source → friendly provider name)
    _TOOL_NAMES = {
        "claude-code": "Claude Code",
        "codex": "Codex",
        "gemini": "Gemini CLI",
        "gemini-cli": "Gemini CLI",
        "claude-ai": "Claude.ai",
    }
    src_rows = db.execute(
        "SELECT source, COUNT(*) c FROM sessions "
        "GROUP BY source ORDER BY c DESC"
    ).fetchall()
    tool_parts = []
    for src, c in src_rows:
        if not src:
            continue
        pct = (c / total_sessions * 100) if total_sessions else 0
        tool_parts.append(f"{_TOOL_NAMES.get(src, src)} {pct:.0f}%")
    tools_line = " · ".join(tool_parts) if tool_parts else "—"

    # Models used (across OpenAI / Anthropic / Google). Pretty-print the raw
    # ids and fall back to the id itself for anything unrecognised.
    def _pretty_model(m):
        if not m or m == "<synthetic>":
            return None
        mm = re.match(r"claude-(opus|sonnet|haiku)-(\d+)(?:-(\d+))?(?:-\d{6,})?$", m)
        if mm:
            fam, major, minor = mm.group(1), mm.group(2), mm.group(3)
            if minor and len(minor) >= 6:  # a date stamp, not a minor version
                minor = None
            ver = f"{major}.{minor}" if minor else major
            return f"Claude {fam.title()} {ver}"
        if m.startswith("gpt-"):
            return "GPT-" + m[4:]
        if m.startswith("gemini-"):
            tail = [p.title() for p in m.split("-")[1:] if p != "preview"]
            return "Gemini " + " ".join(tail)
        return m

    model_rows = db.execute(
        "SELECT model, COUNT(*) c FROM messages "
        "WHERE model IS NOT NULL AND model != '<synthetic>' "
        "GROUP BY model ORDER BY c DESC"
    ).fetchall()
    model_total = sum(c for _, c in model_rows) or 0
    top_model = _pretty_model(model_rows[0][0]) if model_rows else None

    def _fmt_tokens(t):
        if t >= 1e6:
            return f"~{t / 1e6:.1f}M"
        if t >= 1e3:
            return f"~{t / 1e3:.0f}k"
        return f"~{t:.0f}"

    def _fmt_hour(h):
        if h is None:
            return "—"
        return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"

    def _streak(n):
        return f"{n} day" + ("" if n == 1 else "s")

    print("cc-anywhere — usage")
    print("=" * 60)
    print()
    print(f"  {total_sessions:,} sessions · {total_messages:,} messages · "
          f"{_fmt_tokens(est_tokens)} tokens of captured text · {projects:,} projects")
    print()
    print(f"  {'Active days':<16}{str(active_days):<12}"
          f"{'Peak hour':<16}{_fmt_hour(peak_hour)}")
    print(f"  {'Current streak':<16}{_streak(current_streak):<12}"
          f"{'Longest streak':<16}{_streak(longest_streak)}")
    print(f"  {'Tools':<16}{tools_line}")
    if top_model:
        print(f"  {'Favorite model':<16}{top_model}")
    print()

    # ── Heatmap (last 13 weeks, GitHub-contribution style) ──
    today_d = datetime.now()
    start_cell = (today_d - timedelta(days=today_d.weekday() + 12 * 7)).date()

    # Relative shading: bucket each day by its share of the busiest day in
    # the window, so the gradient adapts to the user's volume (a few msgs/day
    # or hundreds) instead of using fixed thresholds.
    window_max = max(
        (cnt for day, cnt in daily_activity.items()
         if datetime.strptime(day, "%Y-%m-%d").date() >= start_cell),
        default=0,
    )

    def _cell(count):
        if count == 0:
            return "·"
        frac = count / window_max if window_max else 0
        if frac <= 0.25:
            return "░"
        if frac <= 0.50:
            return "▒"
        if frac <= 0.75:
            return "▓"
        return "█"

    print("  Activity (last 13 weeks)")
    day_names = ["Mon", "", "Wed", "", "Fri", "", ""]
    for dow in range(7):
        row = f"  {day_names[dow]:>3} "
        for week in range(13):
            ws = today_d - timedelta(days=today_d.weekday() + (12 - week) * 7)
            cell = (ws + timedelta(days=dow)).date()
            if cell <= today_d.date():
                row += _cell(daily_activity.get(cell.strftime("%Y-%m-%d"), 0))
            else:
                row += " "
        print(row)
    print(f"      {start_cell.strftime('%b %-d')} → "
          f"{today_d.strftime('%b %-d')}    Less ·░▒▓█ More")
    print()

    # ── A little delight (matches Claude Code Desktop's flourish) ──
    # Harry Potter and the Philosopher's Stone ≈ 100k tokens.
    books = est_tokens / 100_000
    if books >= 1:
        print(f"  You've captured ~{books:.0f}× the text of "
              f"Harry Potter and the Philosopher's Stone.")
        print()

    print("CAPTURE STATUS")
    print(f"  Total sessions:   {total_sessions:>8,}")
    print(f"  Total messages:   {total_messages:>8,}")
    print(f"  Active projects:  {projects:>8,}")
    print(f"  DB size:          {db_size_mb:>7.1f} MB")
    if earliest:
        print(f"  Earliest:         {_local_display(earliest)}")
    if latest:
        print(f"  Last capture:     {_local_display(latest)}")
    print()

    # ── Activity windows ──────────────────────────────────────
    print("ACTIVITY")
    print(f"  {'':<14}{'Sessions':>10}{'Messages':>12}{'Projects':>11}")
    for label, cutoff in windows:
        s = db.execute(
            "SELECT COUNT(*) FROM sessions "
            "WHERE COALESCE(last_message_at, started_at) >= ?",
            (cutoff,),
        ).fetchone()[0]
        m = db.execute(
            "SELECT COUNT(*) FROM messages WHERE timestamp >= ?",
            (cutoff,),
        ).fetchone()[0]
        p = db.execute(
            "SELECT COUNT(DISTINCT LOWER(project_name)) FROM sessions "
            "WHERE COALESCE(last_message_at, started_at) >= ?",
            (cutoff,),
        ).fetchone()[0]
        print(f"  {label:<14}{s:>10,}{m:>12,}{p:>11,}")
    print(f"  {'All time':<14}{total_sessions:>10,}{total_messages:>12,}{projects:>11,}")
    print()

    # ── By source ─────────────────────────────────────────────
    by_source = db.execute(
        "SELECT source, COUNT(*) FROM sessions "
        "GROUP BY source ORDER BY COUNT(*) DESC"
    ).fetchall()
    if by_source:
        print("BY SOURCE")
        for source, count in by_source:
            pct = (count / total_sessions * 100) if total_sessions else 0
            print(f"  {source or 'unknown':<16}{count:>6,}  ({pct:.0f}%)")
        print()

    # ── By model (across OpenAI / Anthropic / Google) ─────────
    if model_rows:
        print("BY MODEL")
        for model, count in model_rows[:8]:
            label = _pretty_model(model) or model
            pct = (count / model_total * 100) if model_total else 0
            print(f"  {label:<26}{count:>8,}  ({pct:.0f}%)")
        print()

    # ── By machine ────────────────────────────────────────────
    by_machine = db.execute(
        "SELECT COALESCE(machine_name, '(unset)') AS m, COUNT(*) "
        "FROM sessions GROUP BY m ORDER BY COUNT(*) DESC"
    ).fetchall()
    has_real_machines = any(m[0] != "(unset)" for m in by_machine)
    if has_real_machines or len(by_machine) > 1:
        print("BY MACHINE")
        for machine, count in by_machine:
            pct = (count / total_sessions * 100) if total_sessions else 0
            print(f"  {machine:<24}{count:>6,}  ({pct:.0f}%)")
        print()

    # ── Top projects (this month + all time) ──────────────────
    # Merge case-variants ("Webapp" + "WebApp") via _merge_by_lower
    # before slicing to top 5.
    month_cutoff = _iso_cutoff(720)
    top_month_raw = db.execute(
        """
        SELECT project_name, COUNT(*) FROM sessions
        WHERE COALESCE(last_message_at, started_at) >= ?
        GROUP BY project_name
        """,
        (month_cutoff,),
    ).fetchall()
    top_month = _merge_by_lower(top_month_raw)[:5]
    if top_month:
        print("TOP PROJECTS (this month)")
        for i, (proj, count) in enumerate(top_month, 1):
            print(f"  {i}. {proj:<32} {count:>4} sessions")
        print()

    top_alltime_raw = db.execute(
        """
        SELECT project_name, COUNT(*) FROM sessions
        GROUP BY project_name
        """
    ).fetchall()
    top_alltime = _merge_by_lower(top_alltime_raw)[:5]
    if top_alltime:
        print("TOP PROJECTS (all time)")
        for i, (proj, count) in enumerate(top_alltime, 1):
            print(f"  {i}. {proj:<32} {count:>4} sessions")
        print()

    # ── Marathon sessions (highest message count, last 30 days) ──
    marathon = db.execute(
        """
        SELECT s.project_name, s.started_at, s.last_message_at, s.source,
               COUNT(m.uuid) AS msg_count
        FROM sessions s
        LEFT JOIN messages m ON m.session_id = s.session_id
        WHERE COALESCE(s.last_message_at, s.started_at) >= ?
        GROUP BY s.session_id
        ORDER BY msg_count DESC
        LIMIT 5
        """,
        (month_cutoff,),
    ).fetchall()
    if marathon:
        print("MARATHON SESSIONS (last 30 days)")
        for proj, started, last, source, msgs in marathon:
            ts = _local_display(last or started)
            print(f"  {proj:<28}{ts} · {source or 'unknown':<11} · {msgs:>5,} msgs")
        print()

    print("TIPS")
    print('  --ask "today" / "this week" / "catch me up"   chronological recall')
    print('  --ask "<topic>"                                semantic search')
    print('  --capture                                      index new sessions')
    print('  --llm-guide                                    agent-facing reference')

    db.close()
