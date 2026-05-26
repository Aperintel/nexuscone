"""Chain integrity tests for Nexuscone."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from nexuscone.chain import (
    CURRENT_FORMAT_VERSION,
    EVENT_TYPE_COST_ANOMALY,
    EVENT_TYPE_REQUEST,
    EVENT_TYPE_SCOPE_VIOLATION,
    GENESIS_PREVIOUS_HASH,
    ChainVerificationError,
    Ledger,
)


@pytest.mark.asyncio
async def test_first_entry_uses_genesis_previous_hash(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        entry = await ledger.log(
            actor="test",
            action="bootstrap",
            payload={"event": "first"},
        )
    assert entry.entry_id == 1
    assert entry.previous_hash == GENESIS_PREVIOUS_HASH


@pytest.mark.asyncio
async def test_chain_links_correctly(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        first = await ledger.log(actor="a", action="x")
        second = await ledger.log(actor="b", action="y")
        third = await ledger.log(actor="c", action="z")

    assert second.previous_hash == first.entry_hash
    assert third.previous_hash == second.entry_hash


@pytest.mark.asyncio
async def test_verify_chain_passes_on_clean_chain(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        for i in range(10):
            await ledger.log(actor="agent", action="tick", payload={"i": i})
        count = await ledger.verify_chain()
    assert count == 10


@pytest.mark.asyncio
async def test_verify_chain_detects_tampered_payload(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path) as ledger:
        await ledger.log(actor="agent", action="approve")
        await ledger.log(actor="agent", action="execute")

    async with aiosqlite.connect(db_path) as raw:
        await raw.execute(
            "UPDATE entries SET payload = ? WHERE entry_id = ?",
            ('{"tampered":true}', 1),
        )
        await raw.commit()

    async with Ledger(db_path) as ledger:
        with pytest.raises(ChainVerificationError):
            await ledger.verify_chain()


@pytest.mark.asyncio
async def test_verify_chain_detects_broken_link(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path) as ledger:
        await ledger.log(actor="agent", action="a")
        await ledger.log(actor="agent", action="b")

    async with aiosqlite.connect(db_path) as raw:
        await raw.execute(
            "UPDATE entries SET previous_hash = ? WHERE entry_id = ?",
            ("ff" * 32, 2),
        )
        await raw.commit()

    async with Ledger(db_path) as ledger:
        with pytest.raises(ChainVerificationError):
            await ledger.verify_chain()


@pytest.mark.asyncio
async def test_concurrent_writes_keep_chain_intact(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:

        async def write(i: int) -> None:
            await ledger.log(actor=f"w{i}", action="tick", payload={"i": i})

        await asyncio.gather(*(write(i) for i in range(50)))
        count = await ledger.verify_chain()
    assert count == 50


@pytest.mark.asyncio
async def test_get_entries_filters_by_actor(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="alpha", action="x")
        await ledger.log(actor="beta", action="y")
        await ledger.log(actor="alpha", action="z")
        rows = await ledger.get_entries(actor="alpha")
    assert len(rows) == 2
    assert all(r.actor == "alpha" for r in rows)


@pytest.mark.asyncio
async def test_get_entries_filters_by_action(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="agent", action="login")
        await ledger.log(actor="agent", action="logout")
        await ledger.log(actor="agent", action="login")
        rows = await ledger.get_entries(action="login")
    assert len(rows) == 2
    assert all(r.action == "login" for r in rows)


@pytest.mark.asyncio
async def test_count_returns_chain_length(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        assert await ledger.count() == 0
        for _ in range(5):
            await ledger.log(actor="agent", action="tick")
        assert await ledger.count() == 5


@pytest.mark.asyncio
async def test_get_entry_raises_on_missing_id(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="agent", action="tick")
        with pytest.raises(KeyError):
            await ledger.get_entry(999)


# ---------------------------------------------------------------------------
# v0.2.0 event_type tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_log_default_event_type_is_request(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        entry = await ledger.log(actor="agent", action="tick")
    assert entry.event_type == EVENT_TYPE_REQUEST
    assert entry.format_version == CURRENT_FORMAT_VERSION


@pytest.mark.asyncio
async def test_log_records_non_default_event_type(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        anomaly = await ledger.log(
            actor="drift-detector",
            action="cost_anomaly",
            event_type=EVENT_TYPE_COST_ANOMALY,
            payload={"baseline_ms": 412, "observed_ms": 4200, "z_score": 5.8},
        )
    assert anomaly.event_type == EVENT_TYPE_COST_ANOMALY


@pytest.mark.asyncio
async def test_verify_chain_passes_with_mixed_event_types(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="a", action="generate")
        await ledger.log(
            actor="drift",
            action="anomaly",
            event_type=EVENT_TYPE_COST_ANOMALY,
            payload={"z_score": 4.1},
        )
        await ledger.log(actor="b", action="generate")
        await ledger.log(
            actor="agent",
            action="mcp_call",
            event_type=EVENT_TYPE_SCOPE_VIOLATION,
            payload={"tool": "delete_file", "allowed": ["read_file"]},
        )
        count = await ledger.verify_chain()
    assert count == 4


@pytest.mark.asyncio
async def test_verify_chain_detects_tampered_event_type(tmp_path: Path) -> None:
    """An admin who flips a request row to cost_anomaly without changing the
    hash should be caught: format_version=2 includes event_type in the hash."""
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path) as ledger:
        await ledger.log(actor="agent", action="generate")
        await ledger.log(actor="agent", action="generate")

    async with aiosqlite.connect(db_path) as raw:
        await raw.execute(
            "UPDATE entries SET event_type = ? WHERE entry_id = ?",
            (EVENT_TYPE_COST_ANOMALY, 1),
        )
        await raw.commit()

    async with Ledger(db_path) as ledger:
        with pytest.raises(ChainVerificationError):
            await ledger.verify_chain()


@pytest.mark.asyncio
async def test_get_entries_filters_by_event_type(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="a", action="generate")
        await ledger.log(
            actor="drift",
            action="anomaly",
            event_type=EVENT_TYPE_COST_ANOMALY,
        )
        await ledger.log(actor="b", action="generate")
        await ledger.log(
            actor="drift",
            action="anomaly",
            event_type=EVENT_TYPE_COST_ANOMALY,
        )
        anomalies = await ledger.get_entries(event_type=EVENT_TYPE_COST_ANOMALY)
    assert len(anomalies) == 2
    assert all(r.event_type == EVENT_TYPE_COST_ANOMALY for r in anomalies)


@pytest.mark.asyncio
async def test_legacy_v0_1_0_database_migrates_and_verifies(tmp_path: Path) -> None:
    """A v0.1.0-shaped database (no event_type, no format_version columns)
    must continue to verify after upgrading to v0.2.0. Legacy rows are
    assigned format_version=1 so the v0.1.0 hash formula still applies."""
    db_path = tmp_path / "legacy.db"

    # Hand-craft a v0.1.0-style entries table without the new columns.
    from nexuscone.canonical import canonical_json, sha256_hex

    async with aiosqlite.connect(db_path) as raw:
        raw.row_factory = aiosqlite.Row
        await raw.execute(
            """
            CREATE TABLE entries (
                entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                actor           TEXT    NOT NULL,
                action          TEXT    NOT NULL,
                payload         TEXT    NOT NULL,
                previous_hash   TEXT    NOT NULL,
                entry_hash      TEXT    NOT NULL,
                signature       TEXT,
                signing_key_id  TEXT
            )
            """
        )

        # Insert one legacy row computed with the v0.1.0 hash formula
        # (event_type NOT included).
        payload_canonical = canonical_json({"i": 1})
        timestamp = "2026-05-19T18:00:00.000000Z"
        legacy_hash = sha256_hex(
            canonical_json(
                {
                    "entry_id": 1,
                    "timestamp": timestamp,
                    "actor": "legacy",
                    "action": "tick",
                    "payload": payload_canonical,
                    "previous_hash": GENESIS_PREVIOUS_HASH,
                }
            )
        )
        await raw.execute(
            "INSERT INTO entries (entry_id, timestamp, actor, action, payload, "
            "previous_hash, entry_hash, signature, signing_key_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (1, timestamp, "legacy", "tick", payload_canonical,
             GENESIS_PREVIOUS_HASH, legacy_hash),
        )
        await raw.commit()

    # Open with the v0.2.0 Ledger: migration adds columns and verify_chain
    # treats the legacy row as format_version=1.
    async with Ledger(db_path) as ledger:
        count = await ledger.verify_chain()
        assert count == 1
        legacy_entry = await ledger.get_entry(1)
        assert legacy_entry.format_version == 1
        assert legacy_entry.event_type == EVENT_TYPE_REQUEST

        # Append a v0.2.0 entry on top and re-verify; chain holds across the
        # format boundary because previous_hash links by entry_hash regardless
        # of which formula produced it.
        await ledger.log(actor="agent", action="post-upgrade")
        count = await ledger.verify_chain()
        assert count == 2
