"""Append-only group AI lease/admission forensic history (flowgate.default.0502 T0004).

`group_ai_leases` is a current-state table: acquire/activate/release/reclaim all
overwrite or delete the one row a group may hold, so the exact identity of a lease
that blocked a since-resolved 409 is gone the moment the row moves on. This module is
the durable history `group_ai_leases` was never meant to keep — one row per lifecycle
transition, never updated or deleted by normal operation (T0004 §7).

Every append is best-effort from the caller's point of view: a forensic write failure
must never change an admission or release decision (T0004 §10), so callers in
`group_ai_leases.py` and `ai_invoke/admission.py` wrap `append()` in their own
try/except and only log a warning on failure. This module itself does not swallow
errors -- that choice belongs to the caller, exactly like `_record_orphaned_lease_run`
already does one layer up.
"""
from __future__ import annotations

import json
import os
import threading
import uuid as _uuid
from typing import Optional

from .connection import get_store, now_iso

# Mirrors group_ai_leases.py's own test-only fallback: direct service tests run with no
# configured database (PYTEST_CURRENT_TEST), so this store must not require one either.
_memory: list[dict] = []
_memory_lock = threading.RLock()
_memory_test_id: Optional[str] = None


def _using_memory() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return getattr(get_store(), "_db", None) is None


def _sync_test_scope() -> None:
    global _memory_test_id
    current = os.environ.get("PYTEST_CURRENT_TEST")
    if current and current != _memory_test_id:
        _memory.clear()
        _memory_test_id = current


def _dump(value: Optional[dict]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value: Optional[str]) -> Optional[dict]:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _row_out(row: dict) -> dict:
    """Decode the JSON snapshot columns back into dicts for a reader."""
    out = dict(row)
    out["requested"] = _load(out.pop("requested_snapshot", None))
    out["blocking"] = _load(out.pop("blocking_snapshot", None))
    out["detail"] = _load(out.pop("detail", None)) if isinstance(out.get("detail"), str) else out.get("detail")
    return out


def append(
    *,
    event_type: str,
    group_id: str,
    project_id: Optional[str] = None,
    run_id: Optional[str] = None,
    token_id: Optional[str] = None,
    chain_id: Optional[str] = None,
    action_scope: Optional[str] = None,
    lease_generation: Optional[int] = None,
    reason: Optional[str] = None,
    requested: Optional[dict] = None,
    blocking: Optional[dict] = None,
    detail: Optional[dict] = None,
) -> dict:
    """Append one immutable lifecycle event. Never call UPDATE/DELETE against this
    table from anywhere else -- that would defeat the entire point (T0004 §7)."""
    event_id = f"gale_{_uuid.uuid4().hex}"
    stamp = now_iso()
    row = {
        "event_id": event_id,
        "event_type": event_type,
        "group_id": group_id,
        "project_id": project_id,
        "run_id": run_id,
        "token_id": token_id,
        "chain_id": chain_id,
        "action_scope": action_scope,
        "lease_generation": lease_generation,
        "reason": reason,
        "requested_snapshot": _dump(requested),
        "blocking_snapshot": _dump(blocking),
        "detail": _dump(detail),
        "created_at": stamp,
    }
    if _using_memory():
        with _memory_lock:
            _sync_test_scope()
            stored = dict(row)
            stored["id"] = len(_memory) + 1
            _memory.append(stored)
            return _row_out(stored)
    get_store()._execute(
        "INSERT INTO group_ai_lease_events "
        "(event_id, event_type, group_id, project_id, run_id, token_id, chain_id, "
        "action_scope, lease_generation, reason, requested_snapshot, blocking_snapshot, "
        "detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            row["event_id"], row["event_type"], row["group_id"], row["project_id"],
            row["run_id"], row["token_id"], row["chain_id"], row["action_scope"],
            row["lease_generation"], row["reason"], row["requested_snapshot"],
            row["blocking_snapshot"], row["detail"], row["created_at"],
        ],
    )
    stored = get_store()._fetch_one(
        "SELECT * FROM group_ai_lease_events WHERE event_id = ?", [row["event_id"]]
    )
    return _row_out(stored or row)


def list_for_group(
    group_id: str,
    *,
    run_id: Optional[str] = None,
    token_id: Optional[str] = None,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 200,
) -> list[dict]:
    """Time-ordered (oldest first) history for one group, optionally narrowed --
    the read path behind GET /api/v1/ai-invoke/lease-events (T0004 §14).

    ``since``/``until`` are inclusive ISO-8601 bounds on ``created_at`` -- the
    time-range filter T0004 §14 lists as part of the minimum filter set, so an
    incident window can be isolated instead of always pulling the whole group
    history. ``created_at`` is always stamped via ``now_iso()`` (a fixed-format
    ISO-8601 string), so a plain lexical compare against the caller-supplied
    bound is safe -- no parsing/timezone normalization is required here.
    """
    limit = max(1, min(int(limit or 200), 1000))
    if _using_memory():
        with _memory_lock:
            _sync_test_scope()
            rows = [dict(r) for r in _memory if r.get("group_id") == group_id]
    else:
        query = "SELECT * FROM group_ai_lease_events WHERE group_id = ?"
        params: list = [group_id]
        if since:
            query += " AND created_at >= ?"
            params.append(since)
        if until:
            query += " AND created_at <= ?"
            params.append(until)
        query += " ORDER BY id ASC"
        rows = get_store()._fetch_all(query, params)
        rows = [dict(r) for r in rows]
    if run_id:
        rows = [r for r in rows if r.get("run_id") == run_id]
    if token_id:
        rows = [r for r in rows if r.get("token_id") == token_id]
    if event_type:
        rows = [r for r in rows if r.get("event_type") == event_type]
    if since:
        rows = [r for r in rows if str(r.get("created_at") or "") >= since]
    if until:
        rows = [r for r in rows if str(r.get("created_at") or "") <= until]
    return [_row_out(r) for r in rows[-limit:]]
