"""Data model for OpenTimestamps and RFC 3161 anchor records.

An AnchorRecord represents one submission of a chain head to one or
both timestamping tracks: OpenTimestamps (Bitcoin-anchored, ~1 hour
to confirm) and an RFC 3161 Time-Stamp Authority (TSA-signed,
confirmed immediately). The record is created when Ledger.anchor
posts the chain head and is updated later (via a separate row write,
not in-place mutation) once Bitcoin confirms the OpenTimestamps
proof.

Either track may be absent on a given anchor:
  - When OpenTimestamps fails completely but at least one TSA
    succeeds, ots_proof_blob is None and calendar_servers is empty.
  - When the TSA fails but at least one OpenTimestamps calendar
    succeeds, tst_blob and tsa_url are None.
At least one track must succeed for an anchor row to exist;
Ledger.anchor raises RuntimeError when both tracks fail entirely.

The record is intentionally frozen. The background confirmation poller
writes a new row state to the database directly rather than mutating an
in-memory dataclass, which keeps the model safe to pass across the async
event loop without coordination.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AnchorRecord:
    """One anchor submitted for a given chain head.

    Required fields:
        anchor_id:            autoincrement primary key in the anchors table.
        chain_head_hash:      the entry_hash being anchored, hex.
        chain_head_entry_id:  the entry_id of that chain head.
        submitted_at:         when the proof(s) were posted.
        calendar_servers:     URLs of the OpenTimestamps calendar servers
                              that accepted the submission. Empty when no
                              calendar succeeded.

    Optional, OpenTimestamps track:
        ots_proof_blob:       serialised OpenTimestamps proof. None when no
                              OpenTimestamps calendar accepted the
                              submission for this anchor.
        confirmed_at:         when Bitcoin confirmed the OpenTimestamps
                              proof. None until the background poller
                              upgrades the proof.
        bitcoin_block_height: Bitcoin block containing the proof, once
                              confirmed. None before confirmation or when
                              the OpenTimestamps track is absent.
        bitcoin_block_hash:   Bitcoin block hash containing the proof,
                              once confirmed. None before confirmation
                              or when the OpenTimestamps track is absent.

    Optional, RFC 3161 track:
        tst_blob:             the full TimeStampResp DER returned by the
                              TSA. None when the TSA submission failed.
        tsa_url:              the TSA URL that returned the TST. None
                              when the TSA submission failed.
        tsa_gen_time:         the genTime field from inside the TST,
                              parsed as UTC. None when the TSA
                              submission failed.
    """

    anchor_id: int
    chain_head_hash: str
    chain_head_entry_id: int
    submitted_at: datetime
    calendar_servers: list[str]
    ots_proof_blob: bytes | None = None
    confirmed_at: datetime | None = None
    bitcoin_block_height: int | None = None
    bitcoin_block_hash: str | None = None
    tst_blob: bytes | None = None
    tsa_url: str | None = None
    tsa_gen_time: datetime | None = None
