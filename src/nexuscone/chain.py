"""Tamper-evident append-only audit ledger.

Every write produces a SQLite row whose entry_hash is the SHA-256 of the
canonical JSON of every other field, including previous_hash. Each new row's
previous_hash equals the prior row's entry_hash, forming an unbroken chain
anchored at a genesis row whose previous_hash is sixty-four zeros.

Writes are serialised under an asyncio lock so the tip of the chain (max
entry_id, its entry_hash) is always observed consistently by the next writer.
verify_chain walks the full table and recomputes every hash from scratch, so
any edit to a stored field, including via raw SQL, causes that row's
entry_hash check to fail and cascades into the next row's previous_hash check.

Signatures are optional. When a Signer is provided to log, the entry_hash is
also signed with Ed25519 and the signature plus signing_key_id are stored on
the row. A Verifier provided to verify_chain checks every signed row.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

import aiosqlite

from nexuscone.canonical import canonical_json, sha256_hex

GENESIS_PREVIOUS_HASH = "0" * 64

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
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

_INDEX_ACTOR = "CREATE INDEX IF NOT EXISTS idx_entries_actor ON entries(actor)"
_INDEX_TIMESTAMP = "CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(timestamp)"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """A single row of the ledger, as it sits on disk after a write."""

    entry_id: int
    timestamp: str
    actor: str
    action: str
    payload: dict[str, Any]
    previous_hash: str
    entry_hash: str
    signature: str | None
    signing_key_id: str | None


class Signer(Protocol):
    """Protocol any Ed25519 signer must satisfy."""

    @property
    def key_id(self) -> str: ...

    def sign(self, message: bytes) -> bytes: ...


class Verifier(Protocol):
    """Protocol any Ed25519 verifier must satisfy."""

    def verify(self, key_id: str, message: bytes, signature: bytes) -> bool: ...


class ChainVerificationError(Exception):
    """Raised when verify_chain detects a hash mismatch, broken link, or bad signature."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _compute_entry_hash(
    *,
    entry_id: int,
    timestamp: str,
    actor: str,
    action: str,
    payload_canonical: str,
    previous_hash: str,
) -> str:
    return sha256_hex(
        canonical_json(
            {
                "entry_id": entry_id,
                "timestamp": timestamp,
                "actor": actor,
                "action": action,
                "payload": payload_canonical,
                "previous_hash": previous_hash,
            }
        )
    )


def _row_to_entry(row: aiosqlite.Row) -> LedgerEntry:
    return LedgerEntry(
        entry_id=int(row["entry_id"]),
        timestamp=row["timestamp"],
        actor=row["actor"],
        action=row["action"],
        payload=json.loads(row["payload"]),
        previous_hash=row["previous_hash"],
        entry_hash=row["entry_hash"],
        signature=row["signature"],
        signing_key_id=row["signing_key_id"],
    )


class Ledger:
    """Async SQLite-backed hash-chain audit ledger."""

    def __init__(self, db_path: Path | str) -> None:
        self._db_path: Path = Path(db_path)
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        self._initialised = False

    async def __aenter__(self) -> Ledger:
        await self.init()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def init(self) -> None:
        """Open the database, create the entries table and indexes if missing."""
        if self._initialised:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute(_SCHEMA)
        await self._db.execute(_INDEX_ACTOR)
        await self._db.execute(_INDEX_TIMESTAMP)
        await self._db.commit()
        self._initialised = True

    async def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
        self._initialised = False

    async def log(
        self,
        *,
        actor: str,
        action: str,
        payload: dict[str, Any] | None = None,
        signer: Signer | None = None,
    ) -> LedgerEntry:
        """Append a new entry to the chain and return the stored row."""
        db = self._require_db()
        payload_dict: dict[str, Any] = payload or {}
        async with self._lock:
            async with db.execute(
                "SELECT entry_id, entry_hash FROM entries "
                "ORDER BY entry_id DESC LIMIT 1"
            ) as cursor:
                tip = await cursor.fetchone()

            if tip is None:
                next_id = 1
                previous_hash = GENESIS_PREVIOUS_HASH
            else:
                next_id = int(tip["entry_id"]) + 1
                previous_hash = tip["entry_hash"]

            timestamp = _utc_now_iso()
            payload_canonical = canonical_json(payload_dict)
            entry_hash = _compute_entry_hash(
                entry_id=next_id,
                timestamp=timestamp,
                actor=actor,
                action=action,
                payload_canonical=payload_canonical,
                previous_hash=previous_hash,
            )

            signature_hex: str | None = None
            signing_key_id: str | None = None
            if signer is not None:
                signature_hex = signer.sign(entry_hash.encode("utf-8")).hex()
                signing_key_id = signer.key_id

            await db.execute(
                "INSERT INTO entries ("
                "entry_id, timestamp, actor, action, payload, previous_hash, "
                "entry_hash, signature, signing_key_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    next_id,
                    timestamp,
                    actor,
                    action,
                    payload_canonical,
                    previous_hash,
                    entry_hash,
                    signature_hex,
                    signing_key_id,
                ),
            )
            await db.commit()

            return LedgerEntry(
                entry_id=next_id,
                timestamp=timestamp,
                actor=actor,
                action=action,
                payload=payload_dict,
                previous_hash=previous_hash,
                entry_hash=entry_hash,
                signature=signature_hex,
                signing_key_id=signing_key_id,
            )

    async def verify_chain(self, verifier: Verifier | None = None) -> int:
        """Walk every entry, recompute hashes, raise on tamper.

        Returns the number of entries verified. When a verifier is provided,
        signed rows additionally have their Ed25519 signatures checked.
        """
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM entries ORDER BY entry_id ASC"
        ) as cursor:
            rows = list(await cursor.fetchall())

        expected_previous = GENESIS_PREVIOUS_HASH
        for row in rows:
            entry_id = int(row["entry_id"])
            stored_previous = row["previous_hash"]
            if stored_previous != expected_previous:
                raise ChainVerificationError(
                    f"entry {entry_id} previous_hash mismatch: "
                    f"expected {expected_previous}, stored {stored_previous}"
                )
            recomputed = _compute_entry_hash(
                entry_id=entry_id,
                timestamp=row["timestamp"],
                actor=row["actor"],
                action=row["action"],
                payload_canonical=row["payload"],
                previous_hash=stored_previous,
            )
            if recomputed != row["entry_hash"]:
                raise ChainVerificationError(
                    f"entry {entry_id} entry_hash mismatch: "
                    f"recomputed {recomputed}, stored {row['entry_hash']}"
                )
            if verifier is not None and row["signature"] is not None:
                if not verifier.verify(
                    row["signing_key_id"],
                    row["entry_hash"].encode("utf-8"),
                    bytes.fromhex(row["signature"]),
                ):
                    raise ChainVerificationError(
                        f"entry {entry_id} signature invalid for key "
                        f"{row['signing_key_id']}"
                    )
            expected_previous = row["entry_hash"]

        return len(rows)

    async def get_entry(self, entry_id: int) -> LedgerEntry:
        """Fetch a single entry by id; raises KeyError if not found."""
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM entries WHERE entry_id = ?", (entry_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise KeyError(f"ledger entry {entry_id} not found")
        return _row_to_entry(row)

    async def get_entries(
        self,
        *,
        actor: str | None = None,
        action: str | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
        descending: bool = True,
    ) -> list[LedgerEntry]:
        """Fetch entries with optional filters, ordered by entry_id."""
        db = self._require_db()
        clauses: list[str] = []
        params: list[Any] = []
        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(end)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "DESC" if descending else "ASC"
        query = f"SELECT * FROM entries{where} ORDER BY entry_id {order} LIMIT ?"
        params.append(int(limit))
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [_row_to_entry(r) for r in rows]

    async def count(self) -> int:
        """Return the number of entries currently in the chain."""
        db = self._require_db()
        async with db.execute("SELECT COUNT(*) FROM entries") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None or not self._initialised:
            raise RuntimeError("Ledger.init() must be awaited before use")
        return self._db
