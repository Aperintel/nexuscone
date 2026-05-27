# Nexuscone

Tamper-evident append-only audit ledger with a SHA-256 hash chain and optional Ed25519 signing, designed for AI systems, agent platforms, and regulated software where every action needs to be provably unmodified after the fact.

[![PyPI](https://img.shields.io/pypi/v/nexuscone.svg)](https://pypi.org/project/nexuscone/)
[![Python](https://img.shields.io/pypi/pyversions/nexuscone.svg)](https://pypi.org/project/nexuscone/)
[![Downloads](https://img.shields.io/pypi/dm/nexuscone.svg)](https://pypi.org/project/nexuscone/)
[![CI](https://github.com/aperintel/nexuscone/actions/workflows/ci.yml/badge.svg)](https://github.com/aperintel/nexuscone/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue)](https://mypy-lang.org/)

## What this is

Nexuscone is a small, dependency-light Python library that writes every audit event to a SQLite database in an append-only, hash-chained, optionally Ed25519-signed format, so any later edit to a stored row, including via raw SQL, breaks verification. The chain anchors at a genesis row whose previous_hash is sixty-four zeros, every subsequent row's previous_hash equals the prior row's entry_hash, and entry_hash is the SHA-256 of the canonical JSON of the row's identifying fields. Walking the chain end-to-end recomputes every hash from scratch, so tamper detection is mechanical rather than trust-based.

From v0.2.0, the chain head can be anchored periodically to Bitcoin's blockchain through OpenTimestamps, and in parallel to a regulator-friendly RFC 3161 Time-Stamp Authority, so the integrity proof no longer rests on trusting Aperintel or any single party but on Bitcoin's proof-of-work and a signed TSA token. A small set of optional witnesses can also sign the chain head between Bitcoin confirmations to close the confirmation gap.

This is the package extracted from the audit ledger that ships inside Metacarpal (an accountability-first autonomous-agent operating system designed to be auditable to a regulator) and the Aperintel AI Gateway (a multi-model AI router with a cryptographic audit chain on every request). It is the same chain logic, generalised into a standalone library so anyone building governance-first AI infrastructure can drop it into their stack without rebuilding the cryptographic primitives.

## Install

```bash
pip install nexuscone
```

For the optional Ed25519 signing layer:

```bash
pip install "nexuscone[signing]"
```

## Quick start

```python
import asyncio
from nexuscone import Ledger


async def main() -> None:
    async with Ledger("data/audit.db") as ledger:
        await ledger.log(
            actor="user_service",
            action="user_login",
            payload={"user_id": "u-001", "ip": "203.0.113.7"},
        )
        await ledger.log(
            actor="payment_service",
            action="charge",
            payload={"user_id": "u-001", "amount_pence": 1299},
        )
        count = await ledger.verify_chain()
        print(f"chain valid, {count} entries")


asyncio.run(main())
```

## With Ed25519 signing

```python
import asyncio
import secrets

from nexuscone import Ledger
from nexuscone.signing import Ed25519Signer, Ed25519Verifier


async def main() -> None:
    signer = Ed25519Signer.from_seed("aks_2026q2_main", secrets.token_hex(32))
    verifier = Ed25519Verifier({signer.key_id: signer.public_key_hex})

    async with Ledger("data/audit.db") as ledger:
        await ledger.log(
            actor="judge",
            action="approve",
            payload={"request_id": "r-123", "verdict": "allow"},
            signer=signer,
        )
        count = await ledger.verify_chain(verifier=verifier)
        print(f"chain valid with signatures, {count} entries")


asyncio.run(main())
```

## With Bitcoin-anchored timestamps

```python
import asyncio

from nexuscone import Ledger
from nexuscone.anchor_schedule import AnchorSchedule


async def main() -> None:
    schedule = AnchorSchedule(
        every_n_entries=1000,
        every_m_minutes=60,
        enabled=True,
    )

    async with Ledger("data/audit.db", anchor_schedule=schedule) as ledger:
        await ledger.log(actor="api", action="request", payload={"id": "r-001"})
        await ledger.log(actor="api", action="request", payload={"id": "r-002"})

        anchor = await ledger.anchor()
        if anchor is not None:
            print(f"anchored {anchor.chain_head_hash[:16]} at {anchor.submitted_at}")


asyncio.run(main())
```

Anchoring runs two proof tracks in parallel for every submission. OpenTimestamps submits the chain head to free public calendar servers which roll it into the next Bitcoin block, giving a trust-minimised proof that confirms in roughly an hour. An RFC 3161 Time-Stamp Authority signs the same head immediately, giving a regulator-friendly token that is verifiable the moment it arrives. Either track can fail without blocking the other.

Once Bitcoin mines the relevant block, a background task asks each calendar to upgrade the OpenTimestamps proof in place and writes the upgraded blob plus the confirmed block height back to the anchor row:

```python
from nexuscone.poller import upgrade_pending_anchors

counters = await upgrade_pending_anchors(ledger)
print(f"{counters['upgraded']} of {counters['attempted']} anchors confirmed")
```

## Standalone verification

The package ships a CLI verifier you can run against any Nexuscone database file:

```bash
nexuscone-verify data/audit.db
```

This walks the chain end-to-end, recomputes every hash, and exits 0 on a clean chain or 1 with the failing row on tamper. Equivalent to `python -m nexuscone.verifier data/audit.db`.

From v0.2.0 the same CLI exposes the anchor and TimeStampToken verification surfaces:

```bash
nexuscone-verify data/audit.db --check-anchors      # verify OpenTimestamps proofs against the stored heights
nexuscone-verify data/audit.db --check-tst          # verify RFC 3161 tokens against their chain heads
nexuscone-verify data/audit.db --upgrade-pending    # ask calendars to upgrade incomplete proofs
nexuscone-verify data/audit.db --print-anchor 1     # pretty-print one anchor row
```

## Trust model

Nexuscone gives you three levels of integrity guarantee that stack on top of each other.

The first is chain integrity. Walking the chain recomputes every hash, so any post-write edit to any field on any row produces a hash mismatch on the next verification pass. This level requires only the SQLite database and is always on.

The second is Ed25519 signatures. Every signed row carries a signature that proves the row could not have been produced by anyone without the matching private signing key. This level requires the database and the public key, and is opt-in by passing a signer to log().

The third is anchored timestamps, available from v0.2.0. Every anchor row binds a specific chain head either to a Bitcoin block through OpenTimestamps (around an hour to confirm, no wallet or node required), or to a regulator-friendly RFC 3161 token signed immediately by a Time-Stamp Authority, or to both in parallel. This level requires the database, the stored proof blobs, and access to either Bitcoin block headers or the TSA's certificate depending on which proof is being verified. It is opt-in by passing an enabled AnchorSchedule to the Ledger.

Choose the level that matches your regulatory or operational setting. The lower levels are always available; the higher levels add stronger guarantees at the cost of a few more dependencies on external infrastructure.

## Design

The chain is anchored at a genesis row whose previous_hash is sixty-four zero characters. Each subsequent row's previous_hash equals the previous row's entry_hash. Each row's entry_hash is the SHA-256 hex digest of the canonical JSON serialisation of the row's identifying fields. From v0.2.0 the hash inputs are entry_id, timestamp, actor, action, event_type, payload, and previous_hash, while v0.1.0 chains continue to verify under their original six-field formula because the verifier dispatches by format_version. Any post-write edit to any of those fields produces a hash mismatch on the next verification pass.

Writes are serialised under an asyncio lock so the tip of the chain (the max entry_id and its entry_hash) is always observed consistently by the next writer, which is the property that makes concurrent writes safe under contention.

When a signer is provided to log, the entry_hash is also signed with Ed25519 and the signature plus signing_key_id are stored on the row. Verification with a matching Verifier checks every signed row's signature against the recorded key id, and rows without signatures are skipped on the signature path while still being hash-verified.

The package is intentionally small, with no orchestration, no agent system, no AI specifics, no opinions about what fields go in payload, and no opinions about deployment. It is a primitive you compose into a larger governance stack.

## When to use this

This library fits applications where you need to prove after the fact that a stored event has not been modified since it was written. Concrete cases are AI agent action logs (so an evaluator can verify the agent did exactly what the chain records), regulated software audit trails (so a compliance team can verify the audit file is the same one written at the time of the action), and append-only operational ledgers (so an oncall engineer can verify the log has not been retroactively edited during an incident).

If you do not need tamper-evidence and a regular log file is fine for your case, you do not need this. If you need a full enterprise governance platform with dashboards, alerting, regional hosting, and customer onboarding, Nexuscone is the primitive you build that platform on top of rather than the platform itself.

## Production usage

Nexuscone is the extracted core of the audit chain that runs inside Metacarpal (the accountability-first autonomous-agent operating system this library was extracted from, serving both personal and enterprise use on a single auditable spine) and the Aperintel AI Gateway (the Aperintel multi-model router with a 4-provider fallback chain and per-request audit logging). The Aperintel governance product Hyperaxis is built on the same primitive. Hyperaxis treats Nexuscone as its open-core dependency and adds three product surfaces for regulated AI deployments: Discover (AI inventory and risk mapping), Govern (policy enforcement and human-in-the-loop approvals), and Sign (cryptographic accountability and regulator-facing evidence packs for the FCA, EU AI Act Article 12, and NHS DSPT).

## Roadmap

v0.3.x targets AWS-native deployment as a first-class path: a public ECR container image, an AWS Lambda Layer, CloudFormation and Terraform reference modules, optional DynamoDB and Aurora PostgreSQL backends for the ledger, an AWS KMS adapter for signing keys, and a CloudTrail anchor adapter that pulls CloudTrail events into the Nexuscone tamper-evident chain. v0.4.x targets Merkle inclusion proofs and selective disclosure with redaction proofs, so a regulator can verify that a specific event was logged without the auditor seeing the surrounding chain.

## Local development

```bash
git clone https://github.com/aperintel/nexuscone.git
cd nexuscone
pip install -e ".[dev]"
pytest tests/ -v
```

Lint and type checks:

```bash
ruff check src tests
mypy src
```

CI runs pytest on Python 3.10, 3.11, 3.12, and 3.13 against every push to main and every pull request.

## License

Apache 2.0. See [LICENSE](LICENSE).

## About

Built by [Aperintel](https://aperintel.com). Nexuscone is the open-core audit primitive underneath the Aperintel governance product family. More at [aperintel.com](https://aperintel.com).
