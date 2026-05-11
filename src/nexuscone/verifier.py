"""Standalone chain verification utility, callable as a CLI script."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from nexuscone.chain import ChainVerificationError, Ledger


async def _verify(db_path: Path) -> tuple[bool, str, int]:
    async with Ledger(db_path) as ledger:
        try:
            count = await ledger.verify_chain()
        except ChainVerificationError as exc:
            return False, str(exc), 0
    return True, "chain valid", count


def main(argv: list[str] | None = None) -> int:
    """Verify the integrity of a Nexuscone ledger database. Exit 0 on pass, 1 on fail."""
    parser = argparse.ArgumentParser(
        description="Verify the integrity of a Nexuscone ledger database."
    )
    parser.add_argument(
        "db_path",
        type=Path,
        help="Path to the SQLite database file containing the ledger.",
    )
    args = parser.parse_args(argv)

    ok, message, count = asyncio.run(_verify(args.db_path))
    if ok:
        print(f"OK · {message} ({count} entries)")
        return 0
    print(f"FAIL · {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
