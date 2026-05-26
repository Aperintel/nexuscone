"""SQL schema fragments for nexuscone tables created after the v0.1.0 baseline.

The original entries-table schema lives in nexuscone.chain alongside the
Ledger class that owns it. From v0.2.0 onwards new tables and their
migration helpers live here so the chain module stays focused on the
hash-chain primitive.

The ANCHORS_TABLE_SQL constant defines the table used to store
OpenTimestamps proofs that anchor chain heads to Bitcoin. The two indexes
support the two query patterns used by Ledger.anchor (lookup by chain
head) and the background confirmation poller (find unconfirmed anchors).

Phase 4c adds two further tables for the Witness-Inclusion Beacon: a
witnesses identity table holding only public keys and a
witness_attestations table holding the per-witness sub-chain of signed
chain-head attestations.
"""

from __future__ import annotations

ANCHORS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS anchors (
    anchor_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_head_hash      TEXT    NOT NULL,
    chain_head_entry_id  INTEGER NOT NULL,
    ots_proof_blob       BLOB,
    submitted_at         TEXT    NOT NULL,
    calendar_servers     TEXT    NOT NULL,
    confirmed_at         TEXT,
    bitcoin_block_height INTEGER,
    bitcoin_block_hash   TEXT,
    tst_blob             BLOB,
    tsa_url              TEXT,
    tsa_gen_time         TEXT,
    FOREIGN KEY (chain_head_entry_id) REFERENCES entries(entry_id)
)
"""

ANCHORS_INDEX_CHAIN_HEAD = (
    "CREATE INDEX IF NOT EXISTS idx_anchors_chain_head "
    "ON anchors(chain_head_hash)"
)

ANCHORS_INDEX_UNCONFIRMED = (
    "CREATE INDEX IF NOT EXISTS idx_anchors_unconfirmed "
    "ON anchors(confirmed_at) WHERE confirmed_at IS NULL"
)

WITNESSES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS witnesses (
    witness_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    label          TEXT    NOT NULL UNIQUE,
    public_key_hex TEXT    NOT NULL UNIQUE,
    role           TEXT    NOT NULL DEFAULT 'consortium',
    created_at     TEXT    NOT NULL,
    retired_at     TEXT
)
"""

WITNESSES_INDEX_ACTIVE = (
    "CREATE INDEX IF NOT EXISTS idx_witnesses_active "
    "ON witnesses(retired_at) WHERE retired_at IS NULL"
)

WITNESS_ATTESTATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS witness_attestations (
    attestation_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    witness_id              INTEGER NOT NULL,
    chain_head_entry_id     INTEGER NOT NULL,
    chain_head_hash         TEXT    NOT NULL,
    signed_at               TEXT    NOT NULL,
    prev_attestation_hash   TEXT    NOT NULL,
    attestation_hash        TEXT    NOT NULL,
    signature_hex           TEXT    NOT NULL,
    FOREIGN KEY (witness_id) REFERENCES witnesses(witness_id),
    FOREIGN KEY (chain_head_entry_id) REFERENCES entries(entry_id)
)
"""

WITNESS_ATTESTATIONS_INDEX_WITNESS = (
    "CREATE INDEX IF NOT EXISTS idx_witness_attestations_witness "
    "ON witness_attestations(witness_id, signed_at)"
)

WITNESS_ATTESTATIONS_INDEX_HEAD = (
    "CREATE INDEX IF NOT EXISTS idx_witness_attestations_chain_head "
    "ON witness_attestations(chain_head_entry_id)"
)

GENESIS_WITNESS_PREV_HASH = "0" * 64
