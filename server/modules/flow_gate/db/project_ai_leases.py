"""Project-scoped admission lease for group-less AI runs."""
from __future__ import annotations
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional
from .connection import get_store, now_iso

ACQUIRING_TTL_SEC = 120
ACTIVE_TTL_SEC = 4 * 60 * 60 + 300
_memory: dict[str, dict] = {}
_lock = threading.RLock()

def _expiry(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds")

def _utc_now_iso() -> str:
    # `expires_at` is always written by `_expiry()` in UTC. The SQL WHERE clauses below compare
    # against `expires_at` with a plain string `<=`, so the comparator MUST be UTC too — mixing
    # in `connection.now_iso()` (JST) breaks that comparison silently: a JST-formatted "now" sorts
    # lexicographically later than a same-instant UTC `expires_at` for most of the day, so the
    # acquiring row this function is supposed to protect gets deleted as "expired" immediately,
    # defeating the single-run-per-project guarantee this lease exists to enforce. See
    # `group_ai_leases.recover_expired`, which already keeps this UTC comparator separate from the
    # JST `stamp` it uses for record fields — this mirrors that convention.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _memory_mode() -> bool:
    return getattr(get_store(), "_db", None) is None

def _expired(row: Optional[dict]) -> bool:
    if not row: return True
    try: return datetime.fromisoformat(str(row["expires_at"])) <= datetime.now(timezone.utc)
    except Exception: return True

def get_active(project_id: str) -> Optional[dict]:
    if _memory_mode():
        with _lock:
            row = _memory.get(project_id)
            if row and _expired(row): _memory.pop(project_id, None); return None
            return dict(row) if row else None
    store = get_store()
    store._execute("DELETE FROM project_ai_leases WHERE project_id = ? AND expires_at <= ?", [project_id, _utc_now_iso()])
    return store._fetch_one("SELECT * FROM project_ai_leases WHERE project_id = ?", [project_id])

def acquire(project_id: str, owner_id: str) -> Optional[dict]:
    stamp, expires = now_iso(), _expiry(ACQUIRING_TTL_SEC)
    if _memory_mode():
        with _lock:
            if get_active(project_id): return None
            _memory[project_id] = {"project_id": project_id, "run_id": owner_id, "state": "acquiring", "acquired_at": stamp, "expires_at": expires}
            return dict(_memory[project_id])
    store = get_store()
    with store.transaction():
        store._execute("DELETE FROM project_ai_leases WHERE project_id = ? AND expires_at <= ?", [project_id, _utc_now_iso()])
        store._execute("INSERT INTO project_ai_leases(project_id,run_id,state,acquired_at,heartbeat_at,expires_at) VALUES(?,?,'acquiring',?,?,?) ON CONFLICT(project_id) DO NOTHING", [project_id, owner_id, stamp, stamp, expires])
        row = store._fetch_one("SELECT * FROM project_ai_leases WHERE project_id = ?", [project_id])
        return row if row and row.get("run_id") == owner_id else None

def activate(project_id: str, owner_id: str, run_id: str) -> Optional[dict]:
    expires = _expiry(ACTIVE_TTL_SEC)
    if _memory_mode():
        with _lock:
            row = _memory.get(project_id)
            if not row or row.get("run_id") != owner_id: return None
            row.update(run_id=run_id, state="active", expires_at=expires)
            return dict(row)
    # This is a compare-and-swap: a later lookup alone must not decide success.
    # The acquiring row may have expired and been replaced by another owner between
    # acquire() and here; seeing that owner's lease would otherwise admit this run.
    store = get_store()
    with store.transaction():
        affected = store._execute_affected(
            "UPDATE project_ai_leases SET run_id=?,state='active',heartbeat_at=?,expires_at=? WHERE project_id=? AND run_id=? AND state='acquiring'",
            [run_id, now_iso(), expires, project_id, owner_id],
        )
        if affected != 1:
            return None
        return store._fetch_one(
            "SELECT * FROM project_ai_leases WHERE project_id=? AND run_id=? AND state='active'",
            [project_id, run_id],
        )

def release(project_id: str, run_id: str) -> bool:
    if _memory_mode():
        with _lock:
            row = _memory.get(project_id)
            if not row or row.get("run_id") != run_id: return False
            _memory.pop(project_id, None); return True
    get_store()._execute("DELETE FROM project_ai_leases WHERE project_id=? AND run_id=?", [project_id, run_id])
    return get_active(project_id) is None