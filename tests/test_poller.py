"""Background poller tests (Phase 5).

The RemoteCalendar import seen by both Ledger.anchor and the poller is
monkeypatched so no real network traffic fires. The mempool.space
block-hash lookup inside the poller is monkeypatched at the function
level. The fake calendars produce real opentimestamps Timestamp
objects so the merge and traversal code under test runs unchanged.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from opentimestamps.calendar import CommitmentNotFoundError
from opentimestamps.core.notary import (
    BitcoinBlockHeaderAttestation,
    PendingAttestation,
)
from opentimestamps.core.timestamp import Timestamp

from nexuscone import poller as poller_module
from nexuscone.anchor_schedule import AnchorSchedule
from nexuscone.chain import Ledger
from nexuscone.poller import upgrade_pending_anchors

CALENDAR_URL = "https://alice.example.invalid"


class _FakeSubmitCalendar:
    """RemoteCalendar stand-in used by Ledger.anchor in tests.

    On submit, returns a Timestamp rooted at the digest with a single
    PendingAttestation pointing at the calendar's URL. This mirrors the
    real OpenTimestamps Phase-4 baseline behaviour without touching the
    network.
    """

    def __init__(self, url: str, user_agent: str = "test") -> None:
        self.url = url
        self.user_agent = user_agent

    def submit(self, digest: bytes, timeout: int | None = None) -> Timestamp:
        ts = Timestamp(digest)
        ts.attestations.add(PendingAttestation(self.url))
        return ts


class _FakeUpgradeCalendar:
    """RemoteCalendar stand-in used by the poller in tests.

    Per-URL behaviour is set on the class before each test. Supports
    three modes:
        'bitcoin'      - get_timestamp returns a Timestamp with a
                         BitcoinBlockHeaderAttestation at height
                         _height_for(uri).
        'not_found'    - get_timestamp raises CommitmentNotFoundError.
        'no_bitcoin'   - get_timestamp returns an empty Timestamp at
                         the commitment (calendar has aggregated but
                         Bitcoin has not yet mined).
        'error'        - get_timestamp raises a generic RuntimeError.
    """

    behaviours: dict[str, str] = {}
    heights: dict[str, int] = {}

    def __init__(self, uri: str, user_agent: str = "test") -> None:
        self.uri = uri
        self.user_agent = user_agent

    def get_timestamp(self, commitment: bytes, timeout: int | None = None) -> Timestamp:
        behaviour = self.behaviours.get(self.uri, "bitcoin")
        if behaviour == "not_found":
            raise CommitmentNotFoundError("fake not-yet-aggregated")
        if behaviour == "error":
            raise RuntimeError("fake calendar outage")
        ts = Timestamp(commitment)
        if behaviour == "bitcoin":
            height = self.heights.get(self.uri, 850_000)
            ts.attestations.add(BitcoinBlockHeaderAttestation(height))
        return ts


@pytest.fixture
def patch_submit_calendar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nexuscone.chain.RemoteCalendar", _FakeSubmitCalendar)


@pytest.fixture
def patch_upgrade_calendar(monkeypatch: pytest.MonkeyPatch) -> type[_FakeUpgradeCalendar]:
    _FakeUpgradeCalendar.behaviours = {}
    _FakeUpgradeCalendar.heights = {}
    monkeypatch.setattr("nexuscone.poller.RemoteCalendar", _FakeUpgradeCalendar)
    return _FakeUpgradeCalendar


@pytest.fixture
def patch_mempool_lookup(monkeypatch: pytest.MonkeyPatch) -> dict[int, str | None]:
    """Replace the block-hash lookup with a deterministic table.

    Tests populate the returned dict with {height: hash_or_None}. The
    fake reads from that dict directly so a single test can set up
    different responses for different heights.
    """
    table: dict[int, str | None] = {}

    def fake_lookup(height: int, timeout: int) -> str | None:
        return table.get(height)

    monkeypatch.setattr(
        "nexuscone.poller._fetch_bitcoin_block_hash_at_height", fake_lookup
    )
    return table


async def _seed_one_pending_anchor(
    ledger: Ledger, *, calendar_url: str = CALENDAR_URL
) -> int:
    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=[calendar_url],
        tsa_urls=[],
    )
    await ledger.log(actor="test", action="seed")
    record = await ledger.anchor(schedule=schedule)
    assert record is not None
    return record.anchor_id


@pytest.mark.asyncio
async def test_upgrades_pending_anchor_with_bitcoin_attestation(
    tmp_path: Path,
    patch_submit_calendar: None,
    patch_upgrade_calendar: type[_FakeUpgradeCalendar],
    patch_mempool_lookup: dict[int, str | None],
) -> None:
    patch_upgrade_calendar.behaviours[CALENDAR_URL] = "bitcoin"
    patch_upgrade_calendar.heights[CALENDAR_URL] = 851_234
    patch_mempool_lookup[851_234] = "00" * 32

    async with Ledger(tmp_path / "ledger.db") as ledger:
        anchor_id = await _seed_one_pending_anchor(ledger)
        counters = await upgrade_pending_anchors(ledger)

        assert counters == {
            "attempted": 1,
            "upgraded": 1,
            "still_pending": 0,
            "failed": 0,
        }
        pending_after = await ledger.list_unconfirmed_anchors()
        assert pending_after == []

        db = ledger._require_db()
        async with db.execute(
            "SELECT confirmed_at, bitcoin_block_height, bitcoin_block_hash, "
            "ots_proof_blob FROM anchors WHERE anchor_id = ?",
            (anchor_id,),
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    assert row["confirmed_at"] is not None
    assert row["bitcoin_block_height"] == 851_234
    assert row["bitcoin_block_hash"] == "00" * 32
    assert row["ots_proof_blob"] is not None


@pytest.mark.asyncio
async def test_pending_anchor_stays_pending_when_calendar_not_yet_aggregated(
    tmp_path: Path,
    patch_submit_calendar: None,
    patch_upgrade_calendar: type[_FakeUpgradeCalendar],
    patch_mempool_lookup: dict[int, str | None],
) -> None:
    patch_upgrade_calendar.behaviours[CALENDAR_URL] = "not_found"

    async with Ledger(tmp_path / "ledger.db") as ledger:
        await _seed_one_pending_anchor(ledger)
        counters = await upgrade_pending_anchors(ledger)

        assert counters["still_pending"] == 1
        assert counters["upgraded"] == 0
        pending_after = await ledger.list_unconfirmed_anchors()
        assert len(pending_after) == 1


@pytest.mark.asyncio
async def test_pending_anchor_stays_pending_when_calendar_has_no_bitcoin(
    tmp_path: Path,
    patch_submit_calendar: None,
    patch_upgrade_calendar: type[_FakeUpgradeCalendar],
    patch_mempool_lookup: dict[int, str | None],
) -> None:
    patch_upgrade_calendar.behaviours[CALENDAR_URL] = "no_bitcoin"

    async with Ledger(tmp_path / "ledger.db") as ledger:
        await _seed_one_pending_anchor(ledger)
        counters = await upgrade_pending_anchors(ledger)

        assert counters["still_pending"] == 1
        assert counters["upgraded"] == 0
        pending_after = await ledger.list_unconfirmed_anchors()
        assert len(pending_after) == 1


@pytest.mark.asyncio
async def test_unexpected_calendar_error_counts_as_still_pending(
    tmp_path: Path,
    patch_submit_calendar: None,
    patch_upgrade_calendar: type[_FakeUpgradeCalendar],
    patch_mempool_lookup: dict[int, str | None],
) -> None:
    """A single-calendar anchor whose calendar raises a generic error
    has no Bitcoin attestation in its merged tree, so the anchor ends
    the run as still_pending. Other anchors in the same batch must
    still be processed; that is covered separately by the multi-anchor
    test below.
    """
    patch_upgrade_calendar.behaviours[CALENDAR_URL] = "error"

    async with Ledger(tmp_path / "ledger.db") as ledger:
        await _seed_one_pending_anchor(ledger)
        counters = await upgrade_pending_anchors(ledger)

        assert counters["attempted"] == 1
        assert counters["still_pending"] == 1
        assert counters["upgraded"] == 0


@pytest.mark.asyncio
async def test_two_pending_anchors_both_upgrade(
    tmp_path: Path,
    patch_submit_calendar: None,
    patch_upgrade_calendar: type[_FakeUpgradeCalendar],
    patch_mempool_lookup: dict[int, str | None],
) -> None:
    patch_upgrade_calendar.behaviours[CALENDAR_URL] = "bitcoin"
    patch_upgrade_calendar.heights[CALENDAR_URL] = 851_000
    patch_mempool_lookup[851_000] = "11" * 32

    async with Ledger(tmp_path / "ledger.db") as ledger:
        await _seed_one_pending_anchor(ledger)
        await _seed_one_pending_anchor(ledger)
        counters = await upgrade_pending_anchors(ledger)

        assert counters["attempted"] == 2
        assert counters["upgraded"] == 2
        assert counters["still_pending"] == 0


@pytest.mark.asyncio
async def test_confirmed_anchor_is_not_reprocessed(
    tmp_path: Path,
    patch_submit_calendar: None,
    patch_upgrade_calendar: type[_FakeUpgradeCalendar],
    patch_mempool_lookup: dict[int, str | None],
) -> None:
    patch_upgrade_calendar.behaviours[CALENDAR_URL] = "bitcoin"
    patch_upgrade_calendar.heights[CALENDAR_URL] = 852_000
    patch_mempool_lookup[852_000] = "22" * 32

    async with Ledger(tmp_path / "ledger.db") as ledger:
        await _seed_one_pending_anchor(ledger)
        first_run = await upgrade_pending_anchors(ledger)
        assert first_run["upgraded"] == 1

        second_run = await upgrade_pending_anchors(ledger)
        assert second_run == {
            "attempted": 0,
            "upgraded": 0,
            "still_pending": 0,
            "failed": 0,
        }


@pytest.mark.asyncio
async def test_max_age_minutes_excludes_old_anchor(
    tmp_path: Path,
    patch_submit_calendar: None,
    patch_upgrade_calendar: type[_FakeUpgradeCalendar],
    patch_mempool_lookup: dict[int, str | None],
) -> None:
    patch_upgrade_calendar.behaviours[CALENDAR_URL] = "bitcoin"
    patch_upgrade_calendar.heights[CALENDAR_URL] = 853_000
    patch_mempool_lookup[853_000] = "33" * 32

    async with Ledger(tmp_path / "ledger.db") as ledger:
        anchor_id = await _seed_one_pending_anchor(ledger)

        old_submitted_at = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        db = ledger._require_db()
        await db.execute(
            "UPDATE anchors SET submitted_at = ? WHERE anchor_id = ?",
            (old_submitted_at, anchor_id),
        )
        await db.commit()

        counters = await upgrade_pending_anchors(ledger, max_age_minutes=60)
        assert counters == {
            "attempted": 0,
            "upgraded": 0,
            "still_pending": 0,
            "failed": 0,
        }


@pytest.mark.asyncio
async def test_mempool_lookup_failure_still_persists_height(
    tmp_path: Path,
    patch_submit_calendar: None,
    patch_upgrade_calendar: type[_FakeUpgradeCalendar],
    patch_mempool_lookup: dict[int, str | None],
) -> None:
    patch_upgrade_calendar.behaviours[CALENDAR_URL] = "bitcoin"
    patch_upgrade_calendar.heights[CALENDAR_URL] = 854_000
    # mempool table left empty, so the lookup returns None.

    async with Ledger(tmp_path / "ledger.db") as ledger:
        await _seed_one_pending_anchor(ledger)
        counters = await upgrade_pending_anchors(ledger)
        assert counters["upgraded"] == 1

        # The lookup-not-found branch returns None, so bitcoin_block_hash
        # stays NULL while bitcoin_block_height is populated.
        db = ledger._require_db()
        async with db.execute(
            "SELECT bitcoin_block_height, bitcoin_block_hash FROM anchors"
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row["bitcoin_block_height"] == 854_000
        assert row["bitcoin_block_hash"] is None


def test_block_hash_lookup_returns_none_on_urlopen_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real helper swallows network errors and returns None."""

    def boom(*_args: object, **_kwargs: object) -> object:
        raise OSError("boom")

    monkeypatch.setattr(poller_module.urllib.request, "urlopen", boom)
    assert poller_module._fetch_bitcoin_block_hash_at_height(123, 5) is None
