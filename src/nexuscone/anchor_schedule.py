"""When to anchor: every N entries OR every M minutes.

AnchorSchedule is the pure-data configuration object that controls when
Ledger.anchor submits the current chain head to OpenTimestamps. The
should_anchor function is deliberately a free function rather than a
method so it can be tested without touching SQLite, threading, or the
calendar client.

The default schedule is disabled. Anchoring is opt-in: the v0.2.0 release
introduces the capability but does not change behaviour for callers that
do not configure a schedule. Callers enable anchoring by constructing
AnchorSchedule(enabled=True) and passing it to the Ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

DEFAULT_CALENDAR_SERVERS: list[str] = [
    "https://alice.btc.calendar.opentimestamps.org",
    "https://bob.btc.calendar.opentimestamps.org",
    "https://finney.calendar.eternitywall.com",
]


@dataclass
class AnchorSchedule:
    """Controls when Nexuscone submits a chain head to OpenTimestamps.

    Anchoring fires when EITHER condition is met:
      - new_entries_since_last_anchor >= every_n_entries
      - time_since_last_anchor >= every_m_minutes

    Defaults are conservative: 1000 entries or 60 minutes, whichever first.
    """

    every_n_entries: int = 1000
    every_m_minutes: int = 60
    calendar_servers: list[str] = field(
        default_factory=lambda: DEFAULT_CALENDAR_SERVERS.copy()
    )
    enabled: bool = False


def should_anchor(
    schedule: AnchorSchedule,
    entries_since_last: int,
    last_anchor_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Pure function. Decides whether to anchor right now.

    Returns False whenever the schedule is disabled, regardless of the
    other inputs. Returns True when the entries-since-last threshold is
    met, or when the time-since-last threshold is met. When no anchor
    has ever been recorded (last_anchor_at is None) and at least one
    new entry exists, the first anchor fires immediately so the first
    chain head is bound to Bitcoin without waiting a full hour.
    """
    if not schedule.enabled:
        return False
    if entries_since_last >= schedule.every_n_entries:
        return True
    if entries_since_last <= 0:
        return False
    if last_anchor_at is None:
        return True
    now = now or datetime.now(timezone.utc)
    elapsed_minutes = (now - last_anchor_at).total_seconds() / 60.0
    return elapsed_minutes >= schedule.every_m_minutes
