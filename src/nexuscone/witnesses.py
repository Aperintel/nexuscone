"""Witness-Inclusion Beacon dataclasses and error type.

The Witness-Inclusion Beacon (Phase 4c, v0.2.0) adds an optional
sub-anchor track that closes the OpenTimestamps confirmation gap. A
small set of registered witnesses sign the current chain head with
their own Ed25519 keys at their own cadence; the Ledger stores only
the public keys and the resulting attestations.

This module exposes the data shapes. The persistence and verification
methods live on nexuscone.chain.Ledger.

v0.2.0 ships the consortium tier only. Dynamic admission/removal
protocols (tier 3) and regulator-relationship management (tier 4) are
deferred to v0.2.1 and v0.2.2 respectively.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

WITNESS_ROLE_CONSORTIUM = "consortium"
WITNESS_ROLE_REGULATOR = "regulator"
WITNESS_ROLE_SELF = "self"

WITNESS_ROLES: frozenset[str] = frozenset(
    {
        WITNESS_ROLE_CONSORTIUM,
        WITNESS_ROLE_REGULATOR,
        WITNESS_ROLE_SELF,
    }
)


@dataclass(frozen=True, slots=True)
class Witness:
    """A registered witness identity.

    The ledger holds only the public key; the witness signs attestations
    off-machine with the matching private key.

    Fields:
        witness_id:     autoincrement primary key.
        label:          human-readable name, unique across all witnesses
                        for this ledger.
        public_key_hex: hex-encoded Ed25519 public key.
        role:           one of WITNESS_ROLES. 'consortium' is the default
                        partner-witness role. 'regulator' identifies a
                        regulator-operated witness. 'self' marks a
                        degenerate single-host witness used for
                        development.
        created_at:     when the witness was registered.
        retired_at:     when the witness was retired, or None if still
                        active. Retired witnesses cannot sign new
                        attestations but their historical attestations
                        remain verifiable.
    """

    witness_id: int
    label: str
    public_key_hex: str
    role: str
    created_at: datetime
    retired_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WitnessAttestation:
    """One witness signature over a chain head.

    Attestations from a single witness form a per-witness sub-chain via
    prev_attestation_hash. The first attestation from any witness has
    prev_attestation_hash equal to 64 zeros (the witness-genesis
    sentinel from schema.GENESIS_WITNESS_PREV_HASH).

    Fields:
        attestation_id:        autoincrement primary key.
        witness_id:            the witness that signed.
        chain_head_entry_id:   entries.entry_id of the chain head being
                               attested.
        chain_head_hash:       entries.entry_hash of the chain head.
                               Stored redundantly so tampering of the
                               entries table is detectable when the
                               attestation_hash is recomputed from this
                               row alone.
        signed_at:             when the witness signed, ISO-8601 UTC.
        prev_attestation_hash: attestation_hash of the previous
                               attestation from this same witness, or 64
                               zeros for the first attestation from this
                               witness.
        attestation_hash:      sha256 over the canonical JSON of the
                               five fields above. Signed by the
                               witness's private key.
        signature_hex:         hex-encoded Ed25519 signature over the
                               raw bytes of attestation_hash.
    """

    attestation_id: int
    witness_id: int
    chain_head_entry_id: int
    chain_head_hash: str
    signed_at: datetime
    prev_attestation_hash: str
    attestation_hash: str
    signature_hex: str


class WitnessVerificationError(Exception):
    """Raised when verify_witness_attestations detects a tampered
    attestation_hash, a broken prev_attestation_hash link, or an invalid
    Ed25519 signature."""
