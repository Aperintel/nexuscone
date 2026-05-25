"""Background poller for OpenTimestamps proof upgrade.

When Ledger.anchor first submits a chain head to one or more
OpenTimestamps calendar servers the resulting proof carries
PendingAttestations: promises that the calendar will roll the
commitment into a Bitcoin block within the next confirmation cycle.
The chain head is not yet Bitcoin-anchored. This module picks up those
pending anchors, asks each calendar for its upgraded proof, merges the
upgrade back into the stored tree, and once a
BitcoinBlockHeaderAttestation lands in the tree, persists the result
via Ledger.confirm_anchor.

The poller is best-effort by design: per-anchor failures are caught
and counted but do not halt the run; the Bitcoin block-hash lookup is
optional and may leave bitcoin_block_hash NULL on success so the
verifier CLI can retry.

This module replaces the original Phase 5 design that targeted the
opentimestamps-client StamperUtility surface (which is not importable
on Windows + Python 3.14 because its python-bitcoinlib dependency
fails at import time). The lower-level opentimestamps.calendar and
opentimestamps.core APIs used here have no broken dependency chain.
"""

from __future__ import annotations

import asyncio
import urllib.request
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from opentimestamps.calendar import CommitmentNotFoundError, RemoteCalendar
from opentimestamps.core.notary import (
    BitcoinBlockHeaderAttestation,
    PendingAttestation,
)
from opentimestamps.core.serialize import (
    BytesDeserializationContext,
    BytesSerializationContext,
)
from opentimestamps.core.timestamp import Timestamp

if TYPE_CHECKING:
    from nexuscone.anchors import AnchorRecord
    from nexuscone.chain import Ledger


MEMPOOL_SPACE_BLOCK_BY_HEIGHT_URL = (
    "https://mempool.space/api/block-height/{height}"
)


async def upgrade_pending_anchors(
    ledger: Ledger,
    *,
    max_age_minutes: int | None = 24 * 60,
    timeout_seconds: int = 30,
) -> dict[str, int]:
    """Walk all unconfirmed anchors and try to upgrade each one.

    For every pending anchor the poller deserialises the stored proof,
    walks its attestation tree, queries each calendar named by a
    PendingAttestation, merges any returned upgrade back in, and if the
    result now contains a BitcoinBlockHeaderAttestation, calls
    ledger.confirm_anchor with the upgraded serialisation, the block
    height, and a best-effort block-hash lookup.

    Returns a counter dict:
        attempted:     anchors processed in this run
        upgraded:      gained a Bitcoin attestation in this run
        still_pending: had no upgrade or no Bitcoin attestation yet
        failed:        raised during processing (counted, not surfaced)

    One anchor failing never halts the run; the counter increments and
    the loop moves on.
    """
    pending = await ledger.list_unconfirmed_anchors(
        max_age_minutes=max_age_minutes
    )
    counters: dict[str, int] = {
        "attempted": len(pending),
        "upgraded": 0,
        "still_pending": 0,
        "failed": 0,
    }
    loop = asyncio.get_running_loop()

    for anchor in pending:
        try:
            outcome = await _upgrade_one(
                ledger, anchor, loop, timeout_seconds
            )
            counters[outcome] += 1
        except Exception:  # noqa: BLE001
            counters["failed"] += 1
    return counters


async def _upgrade_one(
    ledger: Ledger,
    anchor: AnchorRecord,
    loop: asyncio.AbstractEventLoop,
    timeout_seconds: int,
) -> str:
    """Upgrade one anchor; returns 'upgraded' or 'still_pending'.

    Unexpected exceptions propagate to the caller, which counts them as
    'failed'. Expected calendar errors (CommitmentNotFoundError, other
    per-calendar exceptions) are absorbed inside this function so a
    single broken calendar does not abandon the remaining pending
    attestations on the same anchor.
    """
    assert anchor.ots_proof_blob is not None
    ts = Timestamp.deserialize(
        BytesDeserializationContext(anchor.ots_proof_blob),
        bytes.fromhex(anchor.chain_head_hash),
    )

    pending_pairs = _collect_pending(ts)
    if not pending_pairs:
        return "still_pending"

    for commitment, uri in pending_pairs:
        try:
            upgraded_partial = await loop.run_in_executor(
                None,
                _calendar_get_timestamp,
                uri,
                commitment,
                timeout_seconds,
            )
        except CommitmentNotFoundError:
            continue
        except Exception:  # noqa: BLE001
            continue
        _merge_at_commitment(ts, commitment, upgraded_partial)

    btc_att = _first_bitcoin_attestation(ts)
    if btc_att is None:
        return "still_pending"

    proof_ctx = BytesSerializationContext()
    ts.serialize(proof_ctx)
    upgraded_blob = proof_ctx.getbytes()

    block_hash = await loop.run_in_executor(
        None,
        _fetch_bitcoin_block_hash_at_height,
        int(btc_att.height),
        timeout_seconds,
    )

    await ledger.confirm_anchor(
        anchor_id=anchor.anchor_id,
        upgraded_proof_blob=upgraded_blob,
        bitcoin_block_height=int(btc_att.height),
        bitcoin_block_hash=block_hash,
        confirmed_at=datetime.now(timezone.utc),
    )
    return "upgraded"


def _calendar_get_timestamp(
    uri: str, commitment: bytes, timeout: int
) -> Timestamp:
    return RemoteCalendar(uri).get_timestamp(commitment, timeout=timeout)


def _collect_pending(ts: Timestamp) -> list[tuple[bytes, str]]:
    out: list[tuple[bytes, str]] = []

    def walk(node: Timestamp) -> None:
        for att in node.attestations:
            if isinstance(att, PendingAttestation):
                out.append((node.msg, att.uri))
        for child in node.ops.values():
            walk(child)

    walk(ts)
    return out


def _merge_at_commitment(
    ts: Timestamp, commitment: bytes, upgraded_partial: Timestamp
) -> bool:
    if ts.msg == commitment:
        ts.merge(upgraded_partial)
        return True
    for child in ts.ops.values():
        if _merge_at_commitment(child, commitment, upgraded_partial):
            return True
    return False


def _first_bitcoin_attestation(
    ts: Timestamp,
) -> BitcoinBlockHeaderAttestation | None:
    for _msg, att in ts.all_attestations():
        if isinstance(att, BitcoinBlockHeaderAttestation):
            return att
    return None


def _fetch_bitcoin_block_hash_at_height(
    height: int, timeout: int
) -> str | None:
    """Best-effort lookup of the block hash for a given height.

    The OpenTimestamps BitcoinBlockHeaderAttestation carries only the
    height; the block hash is fetched from mempool.space. Any failure
    returns None and the caller persists the height with
    bitcoin_block_hash NULL.
    """
    url = MEMPOOL_SPACE_BLOCK_BY_HEIGHT_URL.format(height=height)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if resp.status != 200:
                return None
            body = resp.read().decode("ascii").strip()
            return body or None
    except Exception:  # noqa: BLE001
        return None
