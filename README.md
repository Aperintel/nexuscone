# Nexuscone

Tamper-evident append-only audit ledger with a SHA-256 hash chain and optional Ed25519 signing, designed for AI systems, agent platforms, and regulated software where every action needs to be provably unmodified after the fact.

[![PyPI](https://img.shields.io/pypi/v/nexuscone.svg)](https://pypi.org/project/nexuscone/)
[![Python](https://img.shields.io/pypi/pyversions/nexuscone.svg)](https://pypi.org/project/nexuscone/)
[![Downloads](https://img.shields.io/pypi/dm/nexuscone.svg)](https://pypi.org/project/nexuscone/)
[![CI](https://github.com/nexuscone/nexuscone/actions/workflows/ci.yml/badge.svg)](https://github.com/nexuscone/nexuscone/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-green.svg)](LICENSE)
[![Typed](https://img.shields.io/badge/typed-mypy%20strict-blue)](https://mypy-lang.org/)

## What this is

Nexuscone is a small, dependency-light Python library that writes every audit event to a SQLite database in an append-only, hash-chained, optionally Ed25519-signed format, so any later edit to a stored row, including via raw SQL, breaks verification. The chain anchors at a genesis row whose previous_hash is sixty-four zeros, every subsequent row's previous_hash equals the prior row's entry_hash, and entry_hash is the SHA-256 of the canonical JSON of every other field in the row. Walking the chain end-to-end recomputes every hash from scratch, so tamper detection is mechanical rather than trust-based.

This is the package extracted from the audit ledger that ships inside Metacarpal (a personal autonomous-agent operating system with twenty-one specialist agents and 150 passing tests) and the Aperintel AI Gateway (a multi-model AI router with a cryptographic audit chain on every request). It is the same chain logic, generalised into a standalone library so anyone building governance-first AI infrastructure can drop it into their stack without rebuilding the cryptographic primitives.

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

## Standalone verification

The package ships a CLI verifier you can run against any Nexuscone database file:

```bash
nexuscone-verify data/audit.db
```

Exits 0 on a clean chain, exits 1 with the failing row on tamper. Equivalent to `python -m nexuscone.verifier data/audit.db`.

## Design

The chain is anchored at a genesis row whose previous_hash is sixty-four zero characters. Each subsequent row's previous_hash equals the previous row's entry_hash. Each row's entry_hash is the SHA-256 hex digest of the canonical JSON serialisation of the six payload fields (entry_id, timestamp, actor, action, payload, previous_hash), so any post-write edit to any of those fields produces a hash mismatch on verification.

Writes are serialised under an asyncio lock so the tip of the chain (the max entry_id and its entry_hash) is always observed consistently by the next writer, which is the property that makes concurrent writes safe under contention.

When a signer is provided to log, the entry_hash is also signed with Ed25519 and the signature plus signing_key_id are stored on the row. Verification with a matching Verifier checks every signed row's signature against the recorded key id, and rows without signatures are skipped on the signature path while still being hash-verified.

The package is intentionally small, with no orchestration, no agent system, no AI specifics, no opinions about what fields go in payload, and no opinions about deployment. It is a primitive you compose into a larger governance stack.

## When to use this

This library fits applications where you need to prove after the fact that a stored event has not been modified since it was written. Concrete cases are AI agent action logs (so an evaluator can verify the agent did exactly what the chain records), regulated software audit trails (so a compliance team can verify the audit file is the same one written at the time of the action), and append-only operational ledgers (so an oncall engineer can verify the log has not been retroactively edited during an incident).

If you do not need tamper-evidence and a regular log file is fine for your case, you do not need this. If you need a full enterprise governance platform with dashboards, alerting, regional hosting, and customer onboarding, Nexuscone is the primitive you build that platform on top of rather than the platform itself.

## Production usage

Nexuscone is the extracted core of the audit chain that runs inside Metacarpal (the personal autonomous-agent operating system this library was extracted from) and the Aperintel AI Gateway (the Aperintel multi-model router). The Aperintel governance product Nexus, which is being built on top of the same primitive, treats Nexuscone as its open-core dependency and adds the dashboards, the BYOK onboarding, the per-customer regional hosting, the FCA and HIPAA evidence packs, and the commercial subscription on top.

## Local development

```bash
git clone https://github.com/nexuscone/nexuscone.git
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

Built by Julius (Osi) Abu, founder of Aperintel, as the open-core audit primitive underneath the Aperintel governance product family. Aperintel is a self-employed AI studio building governance-first AI infrastructure. Portfolio at [osiabu.vercel.app](https://osiabu.vercel.app), LinkedIn at [linkedin.com/in/osiabu](https://linkedin.com/in/osiabu).
