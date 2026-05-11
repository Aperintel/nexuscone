"""Chain integrity tests for Nexuscone."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from nexuscone.chain import (
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
