"""Rejection-item identity helpers (P0005 / T0006).

P0005 fixes the rejection_history item identifier contract: a time-sortable,
stable id assigned by the server on the reject-creation path. Pointing at an
item by array index or timestamp alone is forbidden — concurrent rejections or
history edits would mis-link the AI response to the wrong item.

We have no ULID dependency available, so this module emits an equivalent
time-sortable id: ``rej_`` + Crockford base32 of the 48-bit millisecond epoch
(monotonic prefix) + 6 random base32 chars (per-item entropy). Lexical order of
two ids generated at different millis matches chronological order.
"""
from __future__ import annotations

import os
import time

# Crockford base32 alphabet (ULID-compatible: no I, L, O, U).
_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32_encode(value: int, length: int) -> str:
    """Encode a non-negative int as a fixed-length Crockford base32 string."""
    out = []
    for _ in range(length):
        out.append(_B32[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def new_rejection_id() -> str:
    """Return a fresh time-sortable rejection id (``rej_<stable>``).

    The 10-char base32 timestamp covers a 50-bit ms range (well beyond year
    9999), so the encoding never overflows in practice. The 6-char random tail
    disambiguates ids minted within the same millisecond.
    """
    ts_ms = int(time.time() * 1000)
    rand = int.from_bytes(os.urandom(4), "big")
    return f"rej_{_b32_encode(ts_ms, 10)}{_b32_encode(rand, 6)}"


def legacy_rejection_id(rejected_at: str, index: int) -> str:
    """Deterministic backfill id for a pre-existing item lacking an id (T0006).

    Mirrors the SQL backfill rule exactly so Python and the migration agree:
    ``rej_legacy_<YYYYMMDDHHMMSS>_<index>`` where the timestamp is the item's
    ``rejected_at`` stripped of any non-digit characters and right-padded to 14
    digits. Read-time lazy assignment is forbidden (concurrency risk), but this
    same rule is also used as a defensive fallback for any item that somehow
    reaches the response API without an id.
    """
    digits = "".join(ch for ch in (rejected_at or "") if ch.isdigit())
    compact = (digits + "0" * 14)[:14]
    return f"rej_legacy_{compact}_{index}"
