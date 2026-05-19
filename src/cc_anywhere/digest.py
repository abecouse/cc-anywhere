#!/usr/bin/env python3
"""
Daily/weekly/monthly digest generation for cc-anywhere.

Generates fun activity reports with headlines, stats, and git correlation.
"""

import logging
import random
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger("cc-anywhere")

from cc_anywhere.core import load_all_history

# Try rich for nice output
try:
    from rich.console import Console
    from rich.markdown import Markdown
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None

# Project groupings (customize as needed)
PROJECT_GROUPS = {
    "Threaded Ecosystem": ["threaded", "frontend", "threaded_backend", "threaded-browser-extension"],
}

# Fun messages based on activity patterns
ACTIVITY_HEADLINES = {
    "supernova": [
        "The week you went supernova",
        "Someone discovered unlimited coffee",
        "Sleep is for the weak (apparently)",
        "You and Claude are basically roommates now",
    ],
    "intense": [
        "A week of serious building",
        "The code was strong with this one",
        "Flow state: achieved",
        "You came, you saw, you coded",
    ],
    "steady": [
        "Consistent progress wins races",
        "The steady builder's week",
        "Brick by brick",
        "Marathon, not a sprint",
    ],
    "light": [
        "A lighter week (and that's okay)",
        "Quality over quantity",
        "Strategic coding sessions",
        "Focused and intentional",
    ],
}

STREAK_MESSAGES = {
    7: "One week strong!",
    14: "Two weeks of dedication!",
    21: "Three weeks - you're unstoppable!",
    30: "A FULL MONTH. Legend status.",
    60: "Two months?! Are you okay?",
    90: "90 days. You're not a developer, you're a force of nature.",
}

RATIO_INSIGHTS = {
    "shipper": "You ship fast. Conversations lead directly to commits.",
    "explorer": "You explore deeply before committing. Feature-branch mentality.",
    "architect": "Heavy planning, strategic commits. You're building something big.",
    "balanced": "Nice balance of exploration and shipping.",
}


def get_activity_headline(total_convos: int, peak_day_convos: int) -> str:
    """Generate a fun headline based on activity level."""
    if peak_day_convos > 500 or total_convos > 1500:
        return random.choice(ACTIVITY_HEADLINES["supernova"])
    elif peak_day_convos > 200 or total_convos > 800:
        return random.choice(ACTIVITY_HEADLINES["intense"])
    elif total_convos > 300:
        return random.choice(ACTIVITY_HEADLINES["steady"])
    else:
        return random.choice(ACTIVITY_HEADLINES["light"])


def get_streak_message(streak: int) -> str:
    """Get a message for coding streaks."""
    for threshold in sorted(STREAK_MESSAGES.keys(), reverse=True):
        if streak >= threshold:
            return STREAK_MESSAGES[threshold]
    return f"{streak} day streak!" if streak > 1 else ""


def get_ratio_insight(ratio: float) -> str:
    """Get insight based on conversation/commit ratio."""
    if ratio < 10:
        return RATIO_INSIGHTS["shipper"]
    elif ratio > 50:
        return RATIO_INSIGHTS["architect"]
    elif ratio > 20:
        return RATIO_INSIGHTS["explorer"]
    else:
        return RATIO_INSIGHTS["balanced"]


def show_digest(period: str = "weekly", output_file: str = None, scope: str = "local") -> str:
    """Generate a daily, weekly, or monthly digest of coding activity.

    Args:
        period: "daily", "weekly", or "monthly"
        output_file: Optional path to save the digest
        scope: "local" for this machine only, "all" for all synced machines

    Returns:
        The digest as a markdown string
    """
    include_synced = scope == "all"
    history = load_all_history(include_synced=include_synced)
    if not history:
        print("No history found.")
        return ""

    # Calculate date range
    today = datetime.now()
    scope_label = "All Machines" if include_synced else "This Machine"
    if period == "daily":
        start_date = today - timedelta(hours=48)
        end_date = today
        period_label = f"Last 48 Hours ({scope_label})"
    elif period == "weekly":
        start_of_week = today - timedelta(days=today.weekday())
        start_date = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = today
        period_label = f"Week of {start_date.strftime('%B %d, %Y')} ({scope_label})"
    else:  # monthly - last 30 days
        start_date = today - timedelta(days=30)
        end_date = today
        period_label = f"{start_date.strftime('%B %d')} - {end_date.strftime('%B %d, %Y')} ({scope_label})"

    # Filter entries to period
    period_entries = []
    for entry in history:
        ts = entry.get("timestamp")
        if ts:
            try:
                dt = datetime.fromtimestamp(ts / 1000)
                if start_date <= dt <= end_date:
                    entry["_datetime"] = dt
                    period_entries.append(entry)
            except (TypeError, ValueError, OSError):
                pass  # Skip entries with invalid timestamps

    if not period_entries:
        print(f"No activity found for {period_label}")
        return ""

    # Analyze by project and machine
    project_counts = defaultdict(int)
    daily_counts = defaultdict(int)
    hourly_counts = defaultdict(int)
    machine_counts = defaultdict(int)
    days_active = set()

    for entry in period_entries:
        project = entry.get("project", "unknown")
        if project:
            project_name = Path(project).name
            project_counts[project_name] += 1

        # Track by machine source
        source = entry.get("_source", "local")
        machine_counts[source] += 1

        dt = entry.get("_datetime")
        if dt:
            date_key = dt.strftime("%Y-%m-%d")
            days_active.add(date_key)
            daily_counts[date_key] += 1
            hourly_counts[dt.hour] += 1

    # Calculate stats
    total_convos = len(period_entries)
    total_projects = len(project_counts)
    busiest_day = max(daily_counts.items(), key=lambda x: x[1]) if daily_counts else ("N/A", 0)
    peak_hour = max(hourly_counts.items(), key=lambda x: x[1]) if hourly_counts else (0, 0)

    # Format peak hour nicely
    peak_hour_str = datetime.strptime(str(peak_hour[0]), "%H").strftime("%I %p").lstrip("0")

    # Sort projects by count
    top_projects = sorted(project_counts.items(), key=lambda x: x[1], reverse=True)

    # Calculate grouped projects
    grouped_totals = {}
    for group_name, group_projects in PROJECT_GROUPS.items():
        group_total = sum(project_counts.get(p, 0) for p in group_projects)
        if group_total > 0:
            grouped_totals[group_name] = group_total

    # Calculate streak
    current_streak = 0
    check_date = today.date()
    sorted_active = sorted(days_active, reverse=True)
    for day_str in sorted_active:
        if day_str == check_date.strftime("%Y-%m-%d"):
            current_streak += 1
            check_date = check_date - timedelta(days=1)
        else:
            break

    # Build markdown digest
    lines = []
    headline = get_activity_headline(total_convos, busiest_day[1])
    lines.append(f"# {headline}")
    lines.append(f"**{period_label}**")
    lines.append("")

    # Fun opening based on activity
    if total_convos > 1000:
        lines.append(f"> {total_convos:,} conversations. You're not using Claude - you're *becoming* Claude.")
    elif total_convos > 500:
        lines.append(f"> {total_convos:,} conversations. Claude knows you better than your therapist.")
    elif total_convos > 200:
        lines.append(f"> {total_convos:,} conversations. Solid week of building.")
    lines.append("")

    # Stats table
    lines.append("## The Numbers")
    lines.append("")
    lines.append("| Stat | Value |")
    lines.append("|------|-------|")
    lines.append(f"| Total Conversations | {total_convos:,} |")
    lines.append(f"| Projects Touched | {total_projects} |")
    lines.append(f"| Days Active | {len(days_active)} |")
    lines.append(f"| Average Daily | {total_convos // max(len(days_active), 1):,} |")
    lines.append(f"| Busiest Day | {busiest_day[0]} ({busiest_day[1]:,} conversations) |")
    lines.append(f"| Peak Hour | {peak_hour_str} ({peak_hour[1]:,} conversations) |")
    lines.append("")

    # Machine breakdown (only for "all" scope)
    if include_synced and len(machine_counts) > 1:
        lines.append("## Machine Breakdown")
        lines.append("")
        lines.append("| Machine | Conversations | Share |")
        lines.append("|---------|--------------|-------|")
        for machine, count in sorted(machine_counts.items(), key=lambda x: x[1], reverse=True):
            share = (count / total_convos) * 100
            lines.append(f"| {machine} | {count:,} | {share:.0f}% |")
        lines.append("")

    # Project breakdown
    lines.append("## Project Breakdown")
    lines.append("")
    lines.append("| Project | Conversations | Share |")
    lines.append("|---------|--------------|-------|")
    for project, count in top_projects[:15]:
        share = (count / total_convos) * 100
        lines.append(f"| {project} | {count:,} | {share:.0f}% |")
    lines.append("")

    # Grouped projects
    if grouped_totals:
        lines.append("## Project Groups")
        lines.append("")
        for group_name, total in grouped_totals.items():
            share = (total / total_convos) * 100
            lines.append(f"**{group_name}**: {total:,} conversations ({share:.0f}% of total)")
        lines.append("")

    # Activity timeline
    if period == "daily":
        lines.append("## Hourly Activity")
        lines.append("")
        lines.append("```")
        active_hours = sorted(hourly_counts.items())
        max_count = max(hourly_counts.values()) if hourly_counts else 1
        for hour, count in active_hours:
            hour_str = datetime.strptime(str(hour), "%H").strftime("%I %p").lstrip("0")
            bar_len = int((count / max_count) * 24)
            bar = "█" * bar_len
            lines.append(f"{hour_str:>4} {bar} {count:,}")
        lines.append("```")
        lines.append("")
    elif period == "weekly":
        lines.append("## Daily Activity")
        lines.append("")
        lines.append("```")
        sorted_days = sorted(daily_counts.items())
        max_count = max(daily_counts.values()) if daily_counts else 1
        for date_str, count in sorted_days:
            day_name = datetime.strptime(date_str, "%Y-%m-%d").strftime("%a")
            bar_len = int((count / max_count) * 30)
            bar = "█" * bar_len
            lines.append(f"{day_name} {bar} {count:,}")
        lines.append("```")
        lines.append("")

    # Achievements & Streak
    achievements = []
    if current_streak >= 7:
        streak_msg = get_streak_message(current_streak)
        achievements.append(f"**{current_streak}-day streak!** {streak_msg}")
    if busiest_day[1] > 500:
        achievements.append(f"**Supernova day:** {busiest_day[0]} with {busiest_day[1]:,} conversations")
    if total_projects >= 5:
        achievements.append(f"**Multi-project maestro:** {total_projects} projects touched")
    if peak_hour[0] >= 22 or peak_hour[0] <= 5:
        achievements.append("**Night owl certified:** Peak coding after dark")

    if achievements:
        lines.append("## Achievements Unlocked")
        lines.append("")
        for achievement in achievements:
            lines.append(f"- {achievement}")
        lines.append("")

    # Fun closing thought
    lines.append("## Closing Thought")
    lines.append("")
    closings = [
        "The code will wait. Your energy needs replenishing. Take care of yourself.",
        "Every conversation is a step toward something great. Keep building.",
        "You're not just coding - you're creating. That's special.",
        "Remember: the best code is written by well-rested developers. Probably.",
        "Whatever you're building, the data shows someone who cares deeply about it.",
        "Ship it. Learn. Iterate. You're doing great.",
    ]
    if total_convos > 1000:
        closings.extend([
            "Maybe go outside? Just a thought. The sun exists.",
            "At this point, Claude should be paying YOU rent.",
            "Your dedication is inspiring (and slightly concerning). Keep going.",
        ])
    lines.append(f"*{random.choice(closings)}*")
    lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by cc-anywhere*")

    digest = "\n".join(lines)

    # Output handling
    if output_file:
        output_path = Path(output_file).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(digest)
        print(f"Digest saved to: {output_path}")
    else:
        if RICH_AVAILABLE:
            console.print(Markdown(digest))
        else:
            print(digest)

    return digest


def show_git_analysis(days: int = 30, output_file: str = None) -> str:
    """Analyze git commits and correlate with Claude Code activity.

    Args:
        days: Number of days to analyze (default 30)
        output_file: Optional path to save the analysis

    Returns:
        The analysis as a markdown string
    """
    history = load_all_history()
    if not history:
        print("No history found.")
        return ""

    # Get date range
    today = datetime.now()
    start_date = today - timedelta(days=days)

    # Filter Claude Code entries to period
    period_entries = []
    project_convos = defaultdict(int)
    daily_convos = defaultdict(int)

    for entry in history:
        ts = entry.get("timestamp")
        if ts:
            try:
                dt = datetime.fromtimestamp(ts / 1000)
                if start_date <= dt <= today:
                    entry["_datetime"] = dt
                    period_entries.append(entry)

                    project = Path(entry.get("project", "")).name
                    if project:
                        project_convos[project] += 1

                    date_key = dt.strftime("%Y-%m-%d")
                    daily_convos[date_key] += 1
            except (TypeError, ValueError, OSError):
                pass  # Skip entries with invalid timestamps

    # Find git repos in projects
    git_projects = {}
    daily_commits = defaultdict(int)
    project_commits = defaultdict(int)
    total_commits = 0

    for entry in history:
        project_path = entry.get("project", "")
        if project_path and project_path not in git_projects:
            try:
                result = subprocess.run(
                    ["git", "-C", project_path, "rev-parse", "--git-dir"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    git_projects[project_path] = Path(project_path).name
            except (subprocess.SubprocessError, OSError):
                pass  # Skip non-git directories

    # Get commits from each git repo
    for repo_path, repo_name in git_projects.items():
        try:
            since_date = start_date.strftime("%Y-%m-%d")
            result = subprocess.run(
                ["git", "-C", repo_path, "log", f"--since={since_date}",
                 "--format=%ad", "--date=short"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    if line:
                        daily_commits[line] += 1
                        project_commits[repo_name] += 1
                        total_commits += 1
        except (subprocess.SubprocessError, OSError) as e:
            log.debug("Git error for %s: %s", repo_path, e)

    # Calculate correlation
    total_convos = len(period_entries)
    ratio = f"{total_convos / max(total_commits, 1):.1f}:1" if total_commits > 0 else "N/A"
    ratio_num = total_convos / max(total_commits, 1) if total_commits > 0 else 0

    # Fun headline based on ratio
    if ratio_num > 50:
        headline = "The Architect's Analysis"
        subtitle = "You plan thoroughly before shipping"
    elif ratio_num > 20:
        headline = "The Explorer's Analysis"
        subtitle = "Deep dives lead to confident commits"
    elif ratio_num > 5:
        headline = "The Builder's Analysis"
        subtitle = "Balanced exploration and shipping"
    else:
        headline = "The Shipper's Analysis"
        subtitle = "Fast feedback loops, rapid iteration"

    # Build markdown
    lines = []
    lines.append(f"# {headline}")
    lines.append(f"*{subtitle}*")
    lines.append("")
    lines.append(f"**Period: {start_date.strftime('%B %d')} - {today.strftime('%B %d, %Y')}**")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Claude Code Conversations | {total_convos:,} |")
    lines.append(f"| Git Commits | {total_commits:,} |")
    lines.append(f"| Conversation/Commit Ratio | {ratio} |")
    lines.append(f"| Projects (Claude Code) | {len(project_convos)} |")
    lines.append(f"| Projects (Git) | {len(project_commits)} |")
    lines.append("")

    if total_commits > 0:
        insight = get_ratio_insight(ratio_num)
        lines.append(f"**What this means:** {insight}")
        lines.append("")
        if ratio_num > 30:
            quote = f"> {int(ratio_num)} conversations for every commit. You're building something big - take your time."
        elif ratio_num > 15:
            quote = f"> {int(ratio_num)} conversations for every commit. Feature-branch mentality. Ship when it's ready."
        else:
            quote = f"> {int(ratio_num)} conversations for every commit. Rapid iteration. Fast feedback loops."
        lines.append(quote)
        lines.append("")

    # Daily correlation
    lines.append("## Daily Correlation")
    lines.append("")
    lines.append("| Date | Claude Convos | Git Commits | Ratio | Notes |")
    lines.append("|------|--------------|-------------|-------|-------|")

    all_dates = sorted(set(list(daily_convos.keys()) + list(daily_commits.keys())), reverse=True)
    for date in all_dates[:14]:
        convos = daily_convos.get(date, 0)
        commits = daily_commits.get(date, 0)

        if commits > 0:
            day_ratio = f"{convos // commits}:1"
        else:
            day_ratio = "N/A" if convos == 0 else "exploration"

        notes = []
        if convos > 100 and commits > 5:
            notes.append("shipping")
        elif convos > 200:
            notes.append("intense")
        elif convos > 0 and commits == 0:
            notes.append("exploration")

        notes_str = ", ".join(notes) if notes else ""
        lines.append(f"| {date} | {convos:,} | {commits} | {day_ratio} | {notes_str} |")

    lines.append("")

    # Project correlation
    lines.append("## Project Correlation")
    lines.append("")
    lines.append("| Project | Claude Convos | Git Commits | Ratio | Pattern |")
    lines.append("|---------|--------------|-------------|-------|---------|")

    all_projects = set(list(project_convos.keys()) + list(project_commits.keys()))
    project_data = []
    for project in all_projects:
        convos = project_convos.get(project, 0)
        commits = project_commits.get(project, 0)
        project_data.append((project, convos, commits))

    project_data.sort(key=lambda x: x[1], reverse=True)

    for project, convos, commits in project_data[:15]:
        if commits > 0:
            proj_ratio = f"{convos // commits}:1" if convos >= commits else f"1:{commits // max(convos, 1)}"
        else:
            proj_ratio = "exploration" if convos > 0 else "N/A"

        if commits > 0 and convos / commits < 10:
            pattern = "Rapid shipping"
        elif commits > 0 and convos / commits > 50:
            pattern = "High iteration"
        elif commits == 0 and convos > 50:
            pattern = "Exploration only"
        else:
            pattern = ""

        lines.append(f"| {project} | {convos:,} | {commits} | {proj_ratio} | {pattern} |")

    lines.append("")

    # Grouped analysis
    if PROJECT_GROUPS:
        lines.append("## Project Groups")
        lines.append("")
        for group_name, group_projects in PROJECT_GROUPS.items():
            group_convos = sum(project_convos.get(p, 0) for p in group_projects)
            group_commits = sum(project_commits.get(p, 0) for p in group_projects)
            if group_convos > 0 or group_commits > 0:
                group_ratio = f"{group_convos // max(group_commits, 1)}:1" if group_commits > 0 else "N/A"
                lines.append(f"**{group_name}**: {group_convos:,} conversations, {group_commits} commits (ratio: {group_ratio})")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by cc-anywhere*")

    analysis = "\n".join(lines)

    # Output handling
    if output_file:
        output_path = Path(output_file).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(analysis)
        print(f"Git analysis saved to: {output_path}")
    else:
        if RICH_AVAILABLE:
            console.print(Markdown(analysis))
        else:
            print(analysis)

    return analysis
