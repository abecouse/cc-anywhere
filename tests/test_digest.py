#!/usr/bin/env python3
"""Tests for digest generation."""

from datetime import datetime, timedelta

from cc_anywhere.digest import show_digest


def _ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_daily_digest_includes_last_48_hours(monkeypatch):
    now = datetime.now()
    history = [
        {
            "timestamp": _ts_ms(now - timedelta(hours=6)),
            "project": "/Users/test/Projects/alpha",
            "_source": "local",
        },
        {
            "timestamp": _ts_ms(now - timedelta(hours=30)),
            "project": "/Users/test/Projects/beta",
            "_source": "local",
        },
        {
            "timestamp": _ts_ms(now - timedelta(days=5)),
            "project": "/Users/test/Projects/old",
            "_source": "local",
        },
    ]
    monkeypatch.setattr("cc_anywhere.digest.load_all_history", lambda include_synced=False: history)

    digest = show_digest("daily", output_file="/tmp/cc-anywhere-test-daily.md")

    assert "Last 48 Hours" in digest
    assert "| Total Conversations | 2 |" in digest
    assert "| Projects Touched | 2 |" in digest
    assert "## Hourly Activity" in digest
    assert "alpha" in digest
    assert "beta" in digest
    assert "old" not in digest


def test_daily_digest_empty_window(monkeypatch):
    now = datetime.now()
    history = [
        {
            "timestamp": _ts_ms(now - timedelta(days=4)),
            "project": "/Users/test/Projects/old",
            "_source": "local",
        },
    ]
    monkeypatch.setattr("cc_anywhere.digest.load_all_history", lambda include_synced=False: history)

    digest = show_digest("daily", output_file="/tmp/cc-anywhere-test-daily-empty.md")

    assert digest == ""

