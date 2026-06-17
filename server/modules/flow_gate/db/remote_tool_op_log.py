"""remote_tool_op_log CRUD (DB0007 §6).

Operation history — records every operation attempt, one row each, once the subject (token) has been identified (L0006 §5.1).
The service (remote_tool_service) decides *when* to record and computes result/error_code;
this module only holds the storage responsibility of INSERTing that result as a single row.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso

# Result-value enum (DB0007 §7.3 / L0006 §8).
VALID_RESULTS = (
    "success", "denied", "not_found", "conflict", "too_large", "error", "unavailable",
)


def insert(
    *,
    grant_id: str,
    op: str,
    result: str,
    error_code: Optional[str] = None,
    target_path: Optional[str] = None,
    target_pattern: Optional[str] = None,
    bytes_processed: Optional[int] = None,
    occurred_at: Optional[str] = None,
) -> None:
    """Append one operation-history row (1 request = 1 row).

    occurred_at defaults to now (the completion time just before the response, L0006 §5.1). The DB enforces
    the result⇔error_code consistency via two portable CHECK constraints (the
    success⇔NULL bijection and the non-success 1:1 value mapping, DB0007 §7.5;
    migration 041), so callers must pass a matched pair.
    """
    now = now_iso()
    get_store()._execute(
        "INSERT INTO remote_tool_op_log "
        "(grant_id, op, target_path, target_pattern, result, error_code, "
        "bytes_processed, occurred_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            grant_id, op, target_path, target_pattern, result, error_code,
            bytes_processed, occurred_at or now, now,
        ],
    )
