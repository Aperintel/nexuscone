"""Integration tests for Ledger.anchor().

Calendar servers are mocked by monkeypatching the RemoteCalendar symbol
imported by nexuscone.chain. No real network traffic fires in this
module. Real-network smoke tests against alice.btc.calendar.opentimestamps.org
belong in a separate suite gated behind a pytest marker; that suite is
deliberately not part of the default test run.

The patch_calendars fixture also disables the RFC 3161 TSA track at
the schedule level (tsa_urls=[]) so the Phase 4 OTS-only behaviours
under test never make a real network call to a TSA. Tests that
exercise the TSA track explicitly construct their own schedule and
monkeypatch submit_to_tsa; see tests/test_rfc3161.py and the
combined-track tests below.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from opentimestamps.core.notary import PendingAttestation
from opentimestamps.core.serialize import BytesDeserializationContext
from opentimestamps.core.timestamp import Timestamp

from nexuscone.anchor_schedule import AnchorSchedule
from nexuscone.chain import Ledger


class _FakeCalendar:
    """Drop-in replacement for opentimestamps.calendar.RemoteCalendar.

    Behaviour is controlled by a per-URL configuration map set on the
    class before each test. Mirrors the real class's constructor signature
    and submit method so monkeypatching is transparent to the calling
    code.
    """

    behaviours: dict[str, str] = {}

    def __init__(self, url: str, user_agent: str = "python-opentimestamps") -> None:
        self.url = url
        self.user_agent = user_agent

    def submit(self, digest: bytes, timeout: int | None = None) -> Timestamp:
        behaviour = self.behaviours.get(self.url, "succeed")
        if behaviour == "fail":
            raise RuntimeError(f"fake-failure for {self.url}")
        if behaviour == "timeout":
            raise TimeoutError(f"fake-timeout for {self.url}")
        ts = Timestamp(digest)
        ts.attestations.add(PendingAttestation(self.url))
        return ts


@pytest.fixture
def patch_calendars(monkeypatch: pytest.MonkeyPatch) -> type[_FakeCalendar]:
    """Replace RemoteCalendar in nexuscone.chain with the fake.

    Resets the per-URL behaviour map between tests by handing the test a
    fresh class-level dict each time.
    """
    _FakeCalendar.behaviours = {}
    monkeypatch.setattr("nexuscone.chain.RemoteCalendar", _FakeCalendar)
    return _FakeCalendar


@pytest.mark.asyncio
async def test_anchor_succeeds_with_all_calendars(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=[
            "https://alice.example.invalid",
            "https://bob.example.invalid",
        ],
        tsa_urls=[],
    )
    async with Ledger(tmp_path / "ledger.db", anchor_schedule=schedule) as ledger:
        entry = await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()

    assert record is not None
    assert record.anchor_id > 0
    assert record.chain_head_hash == entry.entry_hash
    assert record.chain_head_entry_id == entry.entry_id
    assert record.calendar_servers == [
        "https://alice.example.invalid",
        "https://bob.example.invalid",
    ]
    assert record.confirmed_at is None
    assert record.bitcoin_block_height is None
    assert record.bitcoin_block_hash is None
    assert len(record.ots_proof_blob) > 0


@pytest.mark.asyncio
async def test_anchor_persists_partial_when_some_calendars_fail(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    """Two calendars in the schedule, one fails. The anchor still persists
    and cites only the successful one. The proof blob still verifies."""
    patch_calendars.behaviours = {
        "https://bob.example.invalid": "fail",
    }
    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=[
            "https://alice.example.invalid",
            "https://bob.example.invalid",
        ],
        tsa_urls=[],
    )
    async with Ledger(tmp_path / "ledger.db", anchor_schedule=schedule) as ledger:
        entry = await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()

    assert record is not None
    assert record.calendar_servers == ["https://alice.example.invalid"]

    digest = bytes.fromhex(entry.entry_hash)
    ts = Timestamp.deserialize(
        BytesDeserializationContext(record.ots_proof_blob), digest
    )
    pending_uris = {a.uri for a in ts.attestations if isinstance(a, PendingAttestation)}
    assert pending_uris == {"https://alice.example.invalid"}


@pytest.mark.asyncio
async def test_anchor_raises_when_all_calendars_fail(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    patch_calendars.behaviours = {
        "https://alice.example.invalid": "fail",
        "https://bob.example.invalid": "timeout",
    }
    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=[
            "https://alice.example.invalid",
            "https://bob.example.invalid",
        ],
        tsa_urls=[],
    )
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path, anchor_schedule=schedule) as ledger:
        await ledger.log(actor="agent", action="tick")
        with pytest.raises(RuntimeError, match="Both anchor tracks failed"):
            await ledger.anchor()

    # No anchor row written when every calendar failed.
    async with aiosqlite.connect(db_path) as raw:
        async with raw.execute("SELECT COUNT(*) FROM anchors") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 0


@pytest.mark.asyncio
async def test_anchor_returns_none_on_empty_ledger(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=[],
    )
    async with Ledger(tmp_path / "ledger.db", anchor_schedule=schedule) as ledger:
        record = await ledger.anchor()
    assert record is None


@pytest.mark.asyncio
async def test_anchor_raises_when_no_schedule_configured(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="agent", action="tick")
        with pytest.raises(RuntimeError, match="no AnchorSchedule configured"):
            await ledger.anchor()


@pytest.mark.asyncio
async def test_anchor_raises_when_schedule_disabled(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    schedule = AnchorSchedule(
        enabled=False,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=[],
    )
    async with Ledger(tmp_path / "ledger.db", anchor_schedule=schedule) as ledger:
        await ledger.log(actor="agent", action="tick")
        with pytest.raises(RuntimeError, match="schedule is disabled"):
            await ledger.anchor()


@pytest.mark.asyncio
async def test_anchor_uses_schedule_arg_over_constructor_default(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    """A schedule passed to anchor() wins over the one set on the Ledger."""
    constructor_schedule = AnchorSchedule(enabled=False)
    arg_schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=[],
    )
    async with Ledger(
        tmp_path / "ledger.db", anchor_schedule=constructor_schedule
    ) as ledger:
        await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor(schedule=arg_schedule)
    assert record is not None
    assert record.calendar_servers == ["https://alice.example.invalid"]


@pytest.mark.asyncio
async def test_anchor_persisted_proof_round_trips_through_deserialise(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    """End-to-end: the proof_blob stored in the database deserialises into
    a Timestamp whose attestations match the calendars that signed it."""
    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=[
            "https://alice.example.invalid",
            "https://bob.example.invalid",
            "https://carol.example.invalid",
        ],
        tsa_urls=[],
    )
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path, anchor_schedule=schedule) as ledger:
        entry = await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()
    assert record is not None

    async with aiosqlite.connect(db_path) as raw:
        raw.row_factory = aiosqlite.Row
        async with raw.execute(
            "SELECT ots_proof_blob FROM anchors WHERE anchor_id = ?",
            (record.anchor_id,),
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    stored_proof: bytes = row["ots_proof_blob"]

    digest = bytes.fromhex(entry.entry_hash)
    ts = Timestamp.deserialize(BytesDeserializationContext(stored_proof), digest)
    pending_uris = {a.uri for a in ts.attestations if isinstance(a, PendingAttestation)}
    assert pending_uris == {
        "https://alice.example.invalid",
        "https://bob.example.invalid",
        "https://carol.example.invalid",
    }


@pytest.mark.asyncio
async def test_anchor_persists_calendars_as_json_round_trip(
    tmp_path: Path, patch_calendars: type[_FakeCalendar]
) -> None:
    """The calendar_servers column stores JSON. Verifying the row
    round-trips back into the same Python list defends against an
    accidental switch to a lossy delimiter such as comma-separation
    (real OTS URLs never contain commas, but a future schema change
    that swapped to CSV would silently lose data on URLs that do).
    """
    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=[
            "https://alice.btc.example.invalid",
            "https://bob.btc.example.invalid",
            "https://finney.example.invalid",
        ],
        tsa_urls=[],
    )
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path, anchor_schedule=schedule) as ledger:
        await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()
    assert record is not None

    import json as _json

    async with aiosqlite.connect(db_path) as raw:
        raw.row_factory = aiosqlite.Row
        async with raw.execute(
            "SELECT calendar_servers FROM anchors WHERE anchor_id = ?",
            (record.anchor_id,),
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    raw_value: str = row["calendar_servers"]
    # Stored value must be valid JSON (not a CSV-like string).
    assert raw_value.startswith("[") and raw_value.endswith("]")
    decoded: Any = _json.loads(raw_value)
    assert decoded == [
        "https://alice.btc.example.invalid",
        "https://bob.btc.example.invalid",
        "https://finney.example.invalid",
    ]


# ---------------------------------------------------------------------------
# Phase 4b: parallel OpenTimestamps + RFC 3161 TSA anchor tests
# ---------------------------------------------------------------------------
#
# These tests cover the four track-combination cases (both succeed, OTS
# only, TSA only, neither). The TSA submission goes through nexuscone.
# chain.submit_to_tsa (imported from nexuscone.rfc3161), so the
# monkeypatch target is "nexuscone.chain.submit_to_tsa".


from datetime import datetime, timezone  # noqa: E402

from nexuscone.rfc3161 import TSAError, TSAResponse  # noqa: E402


def _make_tsa_response(tsa_url: str) -> TSAResponse:
    return TSAResponse(
        tst_blob=b"FAKE-TST-DER-FOR-" + tsa_url.encode(),
        tsa_url=tsa_url,
        gen_time=datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc),
        hash_algorithm="2.16.840.1.101.3.4.2.1",
        serial_number="424242",
    )


@pytest.mark.asyncio
async def test_anchor_persists_both_ots_and_tsa_when_both_succeed(
    tmp_path: Path,
    patch_calendars: type[_FakeCalendar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_tsa(digest: bytes, tsa_url: str, timeout_seconds: int = 30) -> TSAResponse:
        return _make_tsa_response(tsa_url)

    monkeypatch.setattr("nexuscone.chain.submit_to_tsa", fake_tsa)

    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=["https://freetsa.test.invalid/tsr"],
    )
    async with Ledger(tmp_path / "ledger.db", anchor_schedule=schedule) as ledger:
        entry = await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()

    assert record is not None
    assert record.chain_head_hash == entry.entry_hash
    # OTS track populated.
    assert record.ots_proof_blob is not None
    assert record.calendar_servers == ["https://alice.example.invalid"]
    # TSA track populated.
    assert record.tst_blob == b"FAKE-TST-DER-FOR-https://freetsa.test.invalid/tsr"
    assert record.tsa_url == "https://freetsa.test.invalid/tsr"
    assert record.tsa_gen_time == datetime(2026, 5, 25, 19, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_anchor_persists_ots_only_when_tsa_fails(
    tmp_path: Path,
    patch_calendars: type[_FakeCalendar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_tsa(digest: bytes, tsa_url: str, timeout_seconds: int = 30) -> TSAResponse:
        raise TSAError(f"fake-TSA-failure for {tsa_url}")

    monkeypatch.setattr("nexuscone.chain.submit_to_tsa", fake_tsa)

    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=["https://broken.test.invalid/tsr"],
    )
    async with Ledger(tmp_path / "ledger.db", anchor_schedule=schedule) as ledger:
        await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()

    assert record is not None
    assert record.ots_proof_blob is not None
    assert record.calendar_servers == ["https://alice.example.invalid"]
    assert record.tst_blob is None
    assert record.tsa_url is None
    assert record.tsa_gen_time is None


@pytest.mark.asyncio
async def test_anchor_persists_tsa_only_when_ots_fails(
    tmp_path: Path,
    patch_calendars: type[_FakeCalendar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_calendars.behaviours = {
        "https://alice.example.invalid": "fail",
    }

    def fake_tsa(digest: bytes, tsa_url: str, timeout_seconds: int = 30) -> TSAResponse:
        return _make_tsa_response(tsa_url)

    monkeypatch.setattr("nexuscone.chain.submit_to_tsa", fake_tsa)

    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=["https://freetsa.test.invalid/tsr"],
    )
    async with Ledger(tmp_path / "ledger.db", anchor_schedule=schedule) as ledger:
        await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()

    assert record is not None
    assert record.ots_proof_blob is None
    assert record.calendar_servers == []
    assert record.tst_blob == b"FAKE-TST-DER-FOR-https://freetsa.test.invalid/tsr"
    assert record.tsa_url == "https://freetsa.test.invalid/tsr"


@pytest.mark.asyncio
async def test_anchor_raises_when_both_ots_and_tsa_fail(
    tmp_path: Path,
    patch_calendars: type[_FakeCalendar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_calendars.behaviours = {
        "https://alice.example.invalid": "fail",
    }

    def fake_tsa(digest: bytes, tsa_url: str, timeout_seconds: int = 30) -> TSAResponse:
        raise TSAError(f"fake-TSA-failure for {tsa_url}")

    monkeypatch.setattr("nexuscone.chain.submit_to_tsa", fake_tsa)

    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=["https://broken.test.invalid/tsr"],
    )
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path, anchor_schedule=schedule) as ledger:
        await ledger.log(actor="agent", action="tick")
        with pytest.raises(RuntimeError, match="Both anchor tracks failed"):
            await ledger.anchor()

    # No row written when both tracks failed.
    async with aiosqlite.connect(db_path) as raw:
        async with raw.execute("SELECT COUNT(*) FROM anchors") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == 0


@pytest.mark.asyncio
async def test_anchor_falls_through_tsa_url_list_to_find_first_success(
    tmp_path: Path,
    patch_calendars: type[_FakeCalendar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first TSA URL fails, the second succeeds. The anchor cites
    the second URL on the record."""
    call_order: list[str] = []

    def fake_tsa(digest: bytes, tsa_url: str, timeout_seconds: int = 30) -> TSAResponse:
        call_order.append(tsa_url)
        if tsa_url == "https://broken.test.invalid/tsr":
            raise TSAError("primary down")
        return _make_tsa_response(tsa_url)

    monkeypatch.setattr("nexuscone.chain.submit_to_tsa", fake_tsa)

    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=[
            "https://broken.test.invalid/tsr",
            "https://backup.test.invalid/tsr",
        ],
    )
    async with Ledger(tmp_path / "ledger.db", anchor_schedule=schedule) as ledger:
        await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()

    assert call_order == [
        "https://broken.test.invalid/tsr",
        "https://backup.test.invalid/tsr",
    ]
    assert record is not None
    assert record.tsa_url == "https://backup.test.invalid/tsr"


@pytest.mark.asyncio
async def test_anchor_persists_tsa_fields_through_database(
    tmp_path: Path,
    patch_calendars: type[_FakeCalendar],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TSA fields written to the anchors row match what is on the
    returned record. Confirms the new tst_blob, tsa_url, and
    tsa_gen_time columns round-trip cleanly."""
    def fake_tsa(digest: bytes, tsa_url: str, timeout_seconds: int = 30) -> TSAResponse:
        return _make_tsa_response(tsa_url)

    monkeypatch.setattr("nexuscone.chain.submit_to_tsa", fake_tsa)

    schedule = AnchorSchedule(
        enabled=True,
        calendar_servers=["https://alice.example.invalid"],
        tsa_urls=["https://freetsa.test.invalid/tsr"],
    )
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path, anchor_schedule=schedule) as ledger:
        await ledger.log(actor="agent", action="tick")
        record = await ledger.anchor()
    assert record is not None

    async with aiosqlite.connect(db_path) as raw:
        raw.row_factory = aiosqlite.Row
        async with raw.execute(
            "SELECT tst_blob, tsa_url, tsa_gen_time FROM anchors "
            "WHERE anchor_id = ?",
            (record.anchor_id,),
        ) as cursor:
            row = await cursor.fetchone()
    assert row is not None
    assert row["tst_blob"] == b"FAKE-TST-DER-FOR-https://freetsa.test.invalid/tsr"
    assert row["tsa_url"] == "https://freetsa.test.invalid/tsr"
    # The ISO-8601 string with trailing Z.
    assert row["tsa_gen_time"].startswith("2026-05-25T19:00:00")
    assert row["tsa_gen_time"].endswith("Z")
