"""Witness-Inclusion Beacon tests (Phase 4c).

Cover registration uniqueness and validation, retirement semantics, the
per-witness sub-chain linkage, the signer-key cross-check inside
attest_with_witness, end-to-end verification, tamper detection on the
three signed fields, and the filter behaviour of
get_witness_attestations.
"""

from __future__ import annotations

import secrets
from pathlib import Path

import aiosqlite
import pytest

from nexuscone.chain import Ledger, _compute_witness_attestation_hash
from nexuscone.schema import GENESIS_WITNESS_PREV_HASH
from nexuscone.signing import Ed25519Signer, Ed25519Verifier
from nexuscone.witnesses import (
    WITNESS_ROLE_CONSORTIUM,
    WITNESS_ROLE_REGULATOR,
    WITNESS_ROLES,
    WitnessVerificationError,
)


def make_witness_signer() -> Ed25519Signer:
    """Return an Ed25519Signer whose key_id equals its public_key_hex.

    Built by first generating a key, reading its public hex, then
    constructing a second signer with the same seed but key_id set to
    the public key. attest_with_witness cross-checks signer.key_id
    against the registered public key, so tests need this invariant.
    """
    seed = secrets.token_hex(32)
    probe = Ed25519Signer.from_seed("probe", seed)
    public_key_hex = probe.public_key_hex
    return Ed25519Signer.from_seed(public_key_hex, seed)


@pytest.mark.asyncio
async def test_register_witness_returns_populated_record(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        signer = make_witness_signer()
        witness = await ledger.register_witness(
            label="acme-witness-1",
            public_key_hex=signer.public_key_hex,
        )
    assert witness.witness_id > 0
    assert witness.label == "acme-witness-1"
    assert witness.public_key_hex == signer.public_key_hex
    assert witness.role == WITNESS_ROLE_CONSORTIUM
    assert witness.retired_at is None


@pytest.mark.asyncio
async def test_register_witness_accepts_regulator_role(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        signer = make_witness_signer()
        witness = await ledger.register_witness(
            label="fca-witness",
            public_key_hex=signer.public_key_hex,
            role=WITNESS_ROLE_REGULATOR,
        )
    assert witness.role == WITNESS_ROLE_REGULATOR


@pytest.mark.asyncio
async def test_register_witness_rejects_unknown_role(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        signer = make_witness_signer()
        with pytest.raises(ValueError) as excinfo:
            await ledger.register_witness(
                label="bogus",
                public_key_hex=signer.public_key_hex,
                role="bogus",
            )
    message = str(excinfo.value)
    for role in WITNESS_ROLES:
        assert role in message


@pytest.mark.asyncio
async def test_register_witness_duplicate_label_raises(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        first = make_witness_signer()
        second = make_witness_signer()
        await ledger.register_witness(
            label="dup-label",
            public_key_hex=first.public_key_hex,
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await ledger.register_witness(
                label="dup-label",
                public_key_hex=second.public_key_hex,
            )


@pytest.mark.asyncio
async def test_register_witness_duplicate_public_key_raises(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        signer = make_witness_signer()
        await ledger.register_witness(
            label="first",
            public_key_hex=signer.public_key_hex,
        )
        with pytest.raises(aiosqlite.IntegrityError):
            await ledger.register_witness(
                label="second",
                public_key_hex=signer.public_key_hex,
            )


@pytest.mark.asyncio
async def test_list_witnesses_filters_retired_by_default(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        active_signer = make_witness_signer()
        retired_signer = make_witness_signer()
        await ledger.register_witness(
            label="active", public_key_hex=active_signer.public_key_hex
        )
        retired = await ledger.register_witness(
            label="retired", public_key_hex=retired_signer.public_key_hex
        )
        await ledger.retire_witness(retired.witness_id)

        default_listing = await ledger.list_witnesses()
        full_listing = await ledger.list_witnesses(include_retired=True)

    default_labels = [w.label for w in default_listing]
    full_labels = [w.label for w in full_listing]
    assert "active" in default_labels
    assert "retired" not in default_labels
    assert "retired" in full_labels


@pytest.mark.asyncio
async def test_retire_witness_is_idempotent(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        signer = make_witness_signer()
        registered = await ledger.register_witness(
            label="to-retire", public_key_hex=signer.public_key_hex
        )
        first_retire = await ledger.retire_witness(registered.witness_id)
        assert first_retire.retired_at is not None
        second_retire = await ledger.retire_witness(registered.witness_id)
        assert second_retire.retired_at == first_retire.retired_at


@pytest.mark.asyncio
async def test_attest_with_witness_returns_none_on_empty_ledger(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        signer = make_witness_signer()
        registered = await ledger.register_witness(
            label="lonely", public_key_hex=signer.public_key_hex
        )
        result = await ledger.attest_with_witness(
            witness_id=registered.witness_id, signer=signer
        )
    assert result is None


@pytest.mark.asyncio
async def test_attest_with_witness_persists_first_attestation(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="user", action="ping")
        signer = make_witness_signer()
        registered = await ledger.register_witness(
            label="w1", public_key_hex=signer.public_key_hex
        )
        attestation = await ledger.attest_with_witness(
            witness_id=registered.witness_id, signer=signer
        )

    assert attestation is not None
    assert attestation.prev_attestation_hash == GENESIS_WITNESS_PREV_HASH
    expected_hash = _compute_witness_attestation_hash(
        witness_id=attestation.witness_id,
        chain_head_entry_id=attestation.chain_head_entry_id,
        chain_head_hash=attestation.chain_head_hash,
        signed_at=attestation.signed_at.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        prev_attestation_hash=attestation.prev_attestation_hash,
    )
    assert attestation.attestation_hash == expected_hash


@pytest.mark.asyncio
async def test_attest_with_witness_links_sub_chain(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="user", action="one")
        signer = make_witness_signer()
        registered = await ledger.register_witness(
            label="link", public_key_hex=signer.public_key_hex
        )
        first = await ledger.attest_with_witness(
            witness_id=registered.witness_id, signer=signer
        )
        await ledger.log(actor="user", action="two")
        second = await ledger.attest_with_witness(
            witness_id=registered.witness_id, signer=signer
        )

    assert first is not None
    assert second is not None
    assert second.prev_attestation_hash == first.attestation_hash


@pytest.mark.asyncio
async def test_attest_with_witness_rejects_retired_witness(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="user", action="anything")
        signer = make_witness_signer()
        registered = await ledger.register_witness(
            label="retired-attempt", public_key_hex=signer.public_key_hex
        )
        await ledger.retire_witness(registered.witness_id)
        with pytest.raises(RuntimeError) as excinfo:
            await ledger.attest_with_witness(
                witness_id=registered.witness_id, signer=signer
            )
    assert "retired" in str(excinfo.value)


@pytest.mark.asyncio
async def test_attest_with_witness_rejects_mismatched_signer(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="user", action="anything")
        registered_signer = make_witness_signer()
        wrong_signer = make_witness_signer()
        registered = await ledger.register_witness(
            label="strict", public_key_hex=registered_signer.public_key_hex
        )
        with pytest.raises(RuntimeError) as excinfo:
            await ledger.attest_with_witness(
                witness_id=registered.witness_id, signer=wrong_signer
            )
    assert "does not match" in str(excinfo.value)


@pytest.mark.asyncio
async def test_verify_witness_attestations_returns_count(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="user", action="one")
        signer_a = make_witness_signer()
        signer_b = make_witness_signer()
        witness_a = await ledger.register_witness(
            label="witness-a", public_key_hex=signer_a.public_key_hex
        )
        witness_b = await ledger.register_witness(
            label="witness-b", public_key_hex=signer_b.public_key_hex
        )
        for _ in range(3):
            await ledger.attest_with_witness(
                witness_id=witness_a.witness_id, signer=signer_a
            )
            await ledger.attest_with_witness(
                witness_id=witness_b.witness_id, signer=signer_b
            )
            await ledger.log(actor="user", action="step")
        verifier = Ed25519Verifier(
            {
                signer_a.public_key_hex: signer_a.public_key_hex,
                signer_b.public_key_hex: signer_b.public_key_hex,
            }
        )
        count = await ledger.verify_witness_attestations(verifier)
    assert count == 6


@pytest.mark.asyncio
async def test_verify_detects_tampered_attestation_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path) as ledger:
        await ledger.log(actor="user", action="t1")
        signer = make_witness_signer()
        witness = await ledger.register_witness(
            label="tamper-1", public_key_hex=signer.public_key_hex
        )
        await ledger.attest_with_witness(
            witness_id=witness.witness_id, signer=signer
        )

    async with aiosqlite.connect(db_path) as raw:
        await raw.execute(
            "UPDATE witness_attestations SET attestation_hash = ? "
            "WHERE attestation_id = 1",
            ("0" * 64,),
        )
        await raw.commit()

    async with Ledger(db_path) as ledger:
        verifier = Ed25519Verifier({signer.public_key_hex: signer.public_key_hex})
        with pytest.raises(WitnessVerificationError) as excinfo:
            await ledger.verify_witness_attestations(verifier)
    assert "attestation_hash mismatch" in str(excinfo.value)


@pytest.mark.asyncio
async def test_verify_detects_tampered_signature(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path) as ledger:
        await ledger.log(actor="user", action="t2")
        signer = make_witness_signer()
        witness = await ledger.register_witness(
            label="tamper-2", public_key_hex=signer.public_key_hex
        )
        await ledger.attest_with_witness(
            witness_id=witness.witness_id, signer=signer
        )

    async with aiosqlite.connect(db_path) as raw:
        # Flip every byte of the signature to a deterministic dud.
        await raw.execute(
            "UPDATE witness_attestations SET signature_hex = ? "
            "WHERE attestation_id = 1",
            ("aa" * 64,),
        )
        await raw.commit()

    async with Ledger(db_path) as ledger:
        verifier = Ed25519Verifier({signer.public_key_hex: signer.public_key_hex})
        with pytest.raises(WitnessVerificationError) as excinfo:
            await ledger.verify_witness_attestations(verifier)
    assert "signature verification" in str(excinfo.value)


@pytest.mark.asyncio
async def test_verify_detects_tampered_prev_attestation_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    async with Ledger(db_path) as ledger:
        await ledger.log(actor="user", action="t3")
        signer = make_witness_signer()
        witness = await ledger.register_witness(
            label="tamper-3", public_key_hex=signer.public_key_hex
        )
        await ledger.attest_with_witness(
            witness_id=witness.witness_id, signer=signer
        )

    async with aiosqlite.connect(db_path) as raw:
        await raw.execute(
            "UPDATE witness_attestations SET prev_attestation_hash = ? "
            "WHERE attestation_id = 1",
            ("ff" * 32,),
        )
        await raw.commit()

    async with Ledger(db_path) as ledger:
        verifier = Ed25519Verifier({signer.public_key_hex: signer.public_key_hex})
        with pytest.raises(WitnessVerificationError) as excinfo:
            await ledger.verify_witness_attestations(verifier)
    assert "prev_attestation_hash mismatch" in str(excinfo.value)


@pytest.mark.asyncio
async def test_get_witness_attestations_filters_by_witness_id(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        await ledger.log(actor="user", action="f1")
        signer_a = make_witness_signer()
        signer_b = make_witness_signer()
        witness_a = await ledger.register_witness(
            label="f-a", public_key_hex=signer_a.public_key_hex
        )
        witness_b = await ledger.register_witness(
            label="f-b", public_key_hex=signer_b.public_key_hex
        )
        await ledger.attest_with_witness(
            witness_id=witness_a.witness_id, signer=signer_a
        )
        await ledger.attest_with_witness(
            witness_id=witness_b.witness_id, signer=signer_b
        )

        a_only = await ledger.get_witness_attestations(witness_id=witness_a.witness_id)
        b_only = await ledger.get_witness_attestations(witness_id=witness_b.witness_id)
    assert [att.witness_id for att in a_only] == [witness_a.witness_id]
    assert [att.witness_id for att in b_only] == [witness_b.witness_id]


@pytest.mark.asyncio
async def test_get_witness_attestations_filters_by_chain_head(tmp_path: Path) -> None:
    async with Ledger(tmp_path / "ledger.db") as ledger:
        first_entry = await ledger.log(actor="user", action="head-1")
        signer = make_witness_signer()
        witness = await ledger.register_witness(
            label="head-filter", public_key_hex=signer.public_key_hex
        )
        first = await ledger.attest_with_witness(
            witness_id=witness.witness_id, signer=signer
        )
        await ledger.log(actor="user", action="head-2")
        await ledger.attest_with_witness(
            witness_id=witness.witness_id, signer=signer
        )

        head_one = await ledger.get_witness_attestations(
            chain_head_entry_id=first_entry.entry_id
        )

    assert first is not None
    assert len(head_one) == 1
    assert head_one[0].attestation_id == first.attestation_id
