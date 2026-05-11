"""Optional Ed25519 signing layer for the audit ledger.

Requires ``pip install "nexuscone[signing]"`` to pull in the cryptography
dependency. The Signer and Verifier classes implement the matching
nexuscone.chain.Signer and nexuscone.chain.Verifier protocols.
"""

from __future__ import annotations

from collections.abc import Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


class Ed25519Signer:
    """Ed25519 signer for ledger entries."""

    def __init__(self, key_id: str, private_key: Ed25519PrivateKey) -> None:
        self._key_id = key_id
        self._private_key = private_key

    @classmethod
    def generate(cls, key_id: str) -> Ed25519Signer:
        """Construct a signer from a fresh randomly generated key."""
        return cls(key_id, Ed25519PrivateKey.generate())

    @classmethod
    def from_seed(cls, key_id: str, seed_hex: str) -> Ed25519Signer:
        """Construct from a 32-byte hex seed (64 hex characters)."""
        seed = bytes.fromhex(seed_hex)
        if len(seed) != 32:
            raise ValueError("seed must be exactly 32 bytes (64 hex characters)")
        private_key = Ed25519PrivateKey.from_private_bytes(seed)
        return cls(key_id, private_key)

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def public_key_hex(self) -> str:
        """Hex-encoded raw public key bytes for distribution to verifiers."""
        return self._private_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw
        ).hex()

    def sign(self, message: bytes) -> bytes:
        return self._private_key.sign(message)


class Ed25519Verifier:
    """Ed25519 verifier holding the known public keys, keyed by key_id."""

    def __init__(self, public_keys: Mapping[str, str]) -> None:
        """Build a verifier from a mapping of key_id to hex-encoded public key."""
        self._public_keys: dict[str, Ed25519PublicKey] = {
            key_id: Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_key))
            for key_id, hex_key in public_keys.items()
        }

    def verify(self, key_id: str, message: bytes, signature: bytes) -> bool:
        pub = self._public_keys.get(key_id)
        if pub is None:
            return False
        try:
            pub.verify(signature, message)
        except InvalidSignature:
            return False
        return True
