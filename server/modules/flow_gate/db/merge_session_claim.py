"""Merge-session claim rows (flowgate.v02.0153 T#4, T0011 §4.2~§4.3, migration 181).

One ``git_merge_session_claim`` row per merge session, created empty on first use. Every
write follows the ``git_concurrency`` contract: the WHERE carries the previously observed
``claim_token`` and success is the affected-row count from ``_execute_affected`` — never a
re-read. Time columns hold ``now_iso()`` strings computed by the caller; no DB clock
function appears in a condition.

Note on MySQL/MariaDB (they report *changed*, not *matched*, rows): ``take`` and
``release`` always change ``claim_token``, so their count is exact on every dialect.
``renew`` can report 0 on MySQL when the new ``lease_until`` equals the stored one (a
same-second renewal); the service therefore never trusts ``renew`` alone and decides
with ``fence``, which reads the row (see ``services/git/session_claim.py``).
"""
from __future__ import annotations

from typing import Optional

from . import request_cache as _request_cache
from .connection import get_store, is_unique_violation

TABLE = "git_merge_session_claim"
COLUMNS = (
    "merge_id", "claim_token", "owner_instance_id", "purpose", "claimed_at", "lease_until",
    "last_owner_instance_id", "last_purpose", "last_outcome", "last_released_at",
)


def get(merge_id: int) -> Optional[dict]:
    """The current row (never a request-cached copy: another instance may have written)."""
    _request_cache.invalidate()
    return get_store()._fetch_one(
        "SELECT * FROM git_merge_session_claim WHERE merge_id = ?", [int(merge_id)]
    )


def ensure_row(merge_id: int) -> None:
    """Insert the empty row once; a concurrent insert of the same row is ignored."""
    if get(merge_id) is not None:
        return
    try:
        get_store()._execute(
            "INSERT INTO git_merge_session_claim (merge_id) VALUES (?)", [int(merge_id)]
        )
    except Exception as exc:
        if not is_unique_violation(exc):
            raise


def take(merge_id: int, observed_token: Optional[str], *, token: str, owner: str,
         purpose: str, claimed_at: str, lease_until: str,
         reclaimed: Optional[dict] = None) -> int:
    """CAS acquisition. 1 = this caller holds the claim, 0 = it lost the race.

    ``observed_token`` None means "the row was free" (``claim_token IS NULL``). A stale
    reclaim passes ``reclaimed`` = {owner, purpose, outcome, at}: the forced end of the
    previous holder is written to ``last_*`` in the same statement."""
    sets = ["claim_token = ?", "owner_instance_id = ?", "purpose = ?", "claimed_at = ?",
            "lease_until = ?"]
    params: list = [token, owner, purpose, claimed_at, lease_until]
    if reclaimed is not None:
        sets += ["last_owner_instance_id = ?", "last_purpose = ?", "last_outcome = ?",
                 "last_released_at = ?"]
        params += [reclaimed.get("owner"), reclaimed.get("purpose"),
                   reclaimed.get("outcome"), reclaimed.get("at")]
    if observed_token is None:
        where = "merge_id = ? AND claim_token IS NULL"
        params.append(int(merge_id))
    else:
        where = "merge_id = ? AND claim_token = ?"
        params += [int(merge_id), observed_token]
    return get_store()._execute_affected(
        f"UPDATE git_merge_session_claim SET {', '.join(sets)} WHERE {where}", params
    )


def renew(merge_id: int, token: str, lease_until: str) -> int:
    """Token CAS lease extension (see the module note on MySQL same-second renewals)."""
    return get_store()._execute_affected(
        "UPDATE git_merge_session_claim SET lease_until = ? WHERE merge_id = ? AND claim_token = ?",
        [lease_until, int(merge_id), token],
    )


def release(merge_id: int, token: str, *, owner: Optional[str], purpose: Optional[str],
            outcome: Optional[str], released_at: str) -> int:
    """Token CAS release: the claim columns go NULL and ``last_*`` record this holder."""
    return get_store()._execute_affected(
        "UPDATE git_merge_session_claim SET claim_token = NULL, owner_instance_id = NULL, "
        "purpose = NULL, claimed_at = NULL, lease_until = NULL, "
        "last_owner_instance_id = ?, last_purpose = ?, last_outcome = ?, last_released_at = ? "
        "WHERE merge_id = ? AND claim_token = ?",
        [owner, purpose, (outcome or "")[:128] or None, released_at, int(merge_id), token],
    )
