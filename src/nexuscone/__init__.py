"""Nexuscone: tamper-evident append-only audit ledger.

Public API:
    Ledger: async SQLite-backed hash-chain ledger
    LedgerEntry: typed entry returned by the chain
    ChainVerificationError: raised when verify_chain detects tamper
    GENESIS_PREVIOUS_HASH: sixty-four zero characters; the chain anchor
    canonical_json: canonical JSON serialisation utility
    sha256_hex: SHA-256 hex digest utility

Optional API (requires ``pip install "nexuscone[signing]"``):
    nexuscone.signing.Ed25519Signer
    nexuscone.signing.Ed25519Verifier
"""

from nexuscone.canonical import canonical_json, sha256_hex
from nexuscone.chain import (
    GENESIS_PREVIOUS_HASH,
    ChainVerificationError,
    Ledger,
    LedgerEntry,
)

__version__ = "0.1.0"

__all__ = [
    "GENESIS_PREVIOUS_HASH",
    "ChainVerificationError",
    "Ledger",
    "LedgerEntry",
    "canonical_json",
    "sha256_hex",
    "__version__",
]
