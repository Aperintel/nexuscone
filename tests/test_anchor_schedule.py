"""Tests for the AnchorSchedule pure-function decision logic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from nexuscone.anchor_schedule import (
    DEFAULT_CALENDAR_SERVERS,
    AnchorSchedule,
    should_anchor,
)


def test_default_schedule_is_disabled() -> None:
    schedule = AnchorSchedule()
    assert schedule.enabled is False
    assert schedule.every_n_entries == 1000
    assert schedule.every_m_minutes == 60
    assert schedule.calendar_servers == DEFAULT_CALENDAR_SERVERS


def test_disabled_schedule_never_triggers() -> None:
    schedule = AnchorSchedule(enabled=False)
    now = datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc)
    long_ago = now - timedelta(days=7)
    # Huge entry count and a stale last_anchor still returns False when
    # the schedule is disabled.
    assert should_anchor(schedule, 1_000_000, long_ago, now) is False
    assert should_anchor(schedule, 0, None, now) is False


def test_triggers_exactly_at_entry_threshold() -> None:
    schedule = AnchorSchedule(enabled=True, every_n_entries=1000)
    now = datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc)
    just_now = now - timedelta(seconds=1)
    assert should_anchor(schedule, 999, just_now, now) is False
    assert should_anchor(schedule, 1000, just_now, now) is True
    assert should_anchor(schedule, 1001, just_now, now) is True


def test_triggers_when_time_threshold_elapsed_regardless_of_entry_count() -> None:
    schedule = AnchorSchedule(enabled=True, every_m_minutes=60)
    now = datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc)
    one_hour_ago = now - timedelta(minutes=60)
    just_over = now - timedelta(minutes=61)
    just_under = now - timedelta(minutes=59)
    assert should_anchor(schedule, 1, one_hour_ago, now) is True
    assert should_anchor(schedule, 1, just_over, now) is True
    assert should_anchor(schedule, 1, just_under, now) is False


def test_enabled_with_no_entries_does_not_trigger() -> None:
    schedule = AnchorSchedule(enabled=True)
    now = datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc)
    long_ago = now - timedelta(days=30)
    # Zero entries since last anchor: nothing to anchor, even if a very
    # long time has elapsed.
    assert should_anchor(schedule, 0, long_ago, now) is False
    assert should_anchor(schedule, 0, None, now) is False


def test_first_anchor_fires_on_first_entry_above_zero() -> None:
    """No anchor has ever been recorded. The first write should fire the
    first anchor immediately so the chain head is bound to Bitcoin
    without waiting a full hour."""
    schedule = AnchorSchedule(enabled=True)
    now = datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc)
    assert should_anchor(schedule, 1, None, now) is True
    assert should_anchor(schedule, 50, None, now) is True


def test_should_anchor_uses_utc_now_when_now_arg_omitted() -> None:
    """Sanity check: the function does not require an explicit now and
    falls back to datetime.now(timezone.utc). A 30-day-stale last_anchor
    must trigger without an explicit now argument."""
    schedule = AnchorSchedule(enabled=True, every_m_minutes=60)
    stale = datetime.now(timezone.utc) - timedelta(days=30)
    assert should_anchor(schedule, 1, stale) is True
