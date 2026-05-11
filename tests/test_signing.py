"""Ed25519 signing layer tests."""

from __future__ import annotations

import secrets
from pathlib import Path

import pytest

from nexuscone.chain import Ledger
from nexuscone.signing import Ed25519Signer, Ed25519Verifier


@pytest.fixture()
def signer() -> Ed25519Signer:
    return Ed25519Signer.from_seed("aks_test_2026q2", secrets.token_hex(32))


def test_sign_and_verify_roundtrip(signer: Ed25519Signer) -> None:
    message = b"nexuscone test message"
    signature = signer.sign(message)
    verifier = Ed25519Verifier({signer.key_id: signer.public_key_hex})
    assert verifier.verify(signer.key_id, message, signature)


def test_verifier_rejects_wrong_message(signer: Ed25519Signer) -> None:
    signature = signer.sign(b"original")
    verifier = Ed25519Verifier({signer.key_id: signer.public_key_hex})
    assert not verifier.verify(signer.key_id, b"different", signature)


def test_verifier_rejects_unknown_key_id(signer: Ed25519Signer) -> None:
    signature = signer.sign(b"test")
    verifier = Ed25519Verifier({"other_key": signer.public_key_hex})
    assert not verifier.verify(signer.key_id, b"test", signature)


def test_seed_must_be_thirty_two_bytes() -> None:
    with pytest.raises(ValueError):
        Ed25519Signer.from_seed("aks_test", "deadbeef")


def test_generate_produces_unique_keys() -> None:
    a = Ed25519Signer.generate("aks_a")
    b = Ed25519Signer.generate("aks_b")
    assert a.public_key_hex != b.public_key_hex


@pytest.mark.asyncio
async def test_chain_stores_signatures(tmp_path: Path, signer: Ed25519Signer) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        entry = await ledger.log(actor="test", action="signed", signer=signer)
        assert entry.signature is not None
        assert entry.signing_key_id == signer.key_id


@pytest.mark.asyncio
async def test_chain_verify_passes_with_valid_signatures(
    tmp_path: Path, signer: Ed25519Signer
) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        for i in range(3):
            await ledger.log(
                actor="test", action="tick", payload={"i": i}, signer=signer
            )
        verifier = Ed25519Verifier({signer.key_id: signer.public_key_hex})
        count = await ledger.verify_chain(verifier=verifier)
    assert count == 3
