"""Canonical JSON serialisation and SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """Return JSON with keys sorted and no whitespace between separators."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    """Return the SHA-256 hex digest of the UTF-8 encoding of text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
