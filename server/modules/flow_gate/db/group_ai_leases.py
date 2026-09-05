"""Durable, atomic group mutation leases for AI runs (flowgate.default.0378)."""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from .connection import get_store, now_iso


class RunIdCollision(Exception):
    """A caller tried to stamp a run_id onto a lease row that another row already
    carries (0401 NR0003 §4 / T0004 item 7).

    The INSERT's ON CONFLICT target is group_id only (one lease per group), but
    run_id also carries its own UNIQUE index (migration 077 idx_group_ai_leases_run)
    -- a colliding run_id would otherwise fall through as a raw, unhandled DB
    integrity error, which is exactly "the AI invoke itself dies with a server error" from the
    report. Callers (ai_invoke_service.start_run) catch this and retry with a
    freshly minted run_id instead of letting the driver exception surface as a 500.
    """


ACQUIRING_TTL_SEC = 120
ACTIVE_GRACE_SEC = 300
ACTIVE_HEARTBEAT_TTL_SEC = (4 * 60 * 60) + ACTIVE_GRACE_SEC
HANDOFF_TTL_SEC = 120

# Unit tests intentionally run without a DB connection. This mirror preserves the same
# compare-and-swap contract for direct service tests; production always uses the table.
_memory: dict[str, dict] = {}
_memory_lock = threading.RLock()
_memory_test_id: Optional[str] = None


def _using_memory() -> bool:
    # Direct service tests deliberately have no configured database. Avoid even
    # constructing the application store in that environment; production always
    # reaches the durable table.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return getattr(get_store(), "_db", None) is None


def _sync_test_scope() -> None:
    global _memory_test_id
    current = os.environ.get("PYTEST_CURRENT_TEST")
    if current and current != _memory_test_id:
        _memory.clear()
        _memory_test_id = current


def _expiry(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat(timespec="seconds")


def _expired(row: Optional[dict], at: Optional[str] = None) -> bool:
    if not row or not row.get("expires_at"):
        return True
    try:
        expires = datetime.fromisoformat(str(row["expires_at"]))
        now = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return expires <= now
    except (TypeError, ValueError):
        return True


def get(group_id: str) -> Optional[dict]:
    if _using_memory():
        with _memory_lock:
            _sync_test_scope()
            row = _memory.get(group_id)
            return dict(row) if row else None
    return get_store()._fetch_one("SELECT * FROM group_ai_leases WHERE group_id = ?", [group_id])


def recover_expired(group_id: Optional[str] = None) -> int:
    """Reclaim leases whose heartbeat deadline passed (restart/crash recovery rule)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if _using_memory():
        with _memory_lock:
            _sync_test_scope()
            victims = [gid for gid, row in _memory.items() if (not group_id or gid == group_id) and _expired(row, now)]
            for gid in victims:
                _memory.pop(gid, None)
            return len(victims)
    store = get_store()
    if group_id:
        row = get(group_id)
        if row and _expired(row, now):
            store._execute("DELETE FROM group_ai_leases WHERE group_id = ? AND expires_at = ?", [group_id, row["expires_at"]])
            return 1
        return 0
    rows = store._fetch_all("SELECT group_id FROM group_ai_leases WHERE expires_at <= ?", [now])
    store._execute("DELETE FROM group_ai_leases WHERE expires_at <= ?", [now])
    return len(rows)


def reclaim_orphaned(before: str) -> list[dict]:
    """Reclaim every lease acquired before *before* -- the startup dead-lease sweep
    (flowgate.default.0401 NR0003 SS6-1 / T0004 item 1).

    Unlike :func:`recover_expired`, this ignores ``expires_at``: a lease can sit
    orphaned for its whole 4h05m TTL before that check would ever touch it. It is
    safe to be this aggressive only because the caller passes the CALLING process's
    own start time -- the in-memory run registry that could still legitimately own a
    lease starts empty every process, so anything acquired earlier belongs to a
    process that is gone. A lease acquired at or after *before* is left alone (another
    process may be mid-admission for it). Returns the reclaimed rows (group/run/
    acquired_at and the rest) so the caller can explain each one's disappearance.
    """
    if _using_memory():
        with _memory_lock:
            _sync_test_scope()
            victims = [dict(row) for row in _memory.values() if str(row.get("acquired_at") or "") < before]
            for row in victims:
                _memory.pop(row["group_id"], None)
            return victims
    store = get_store()
    rows = store._fetch_all("SELECT * FROM group_ai_leases WHERE acquired_at < ?", [before])
    if rows:
        store._execute("DELETE FROM group_ai_leases WHERE acquired_at < ?", [before])
    return rows


def get_active(group_id: str) -> Optional[dict]:
    recover_expired(group_id)
    row = get(group_id)
    if _using_memory() and row and row.get("state") != "acquiring":
        # Direct service tests often mark their fake run finished instead of calling the
        # real finalizer. Reconcile that test-only shape so one test/hop cannot poison the
        # next admission; production ownership is always the DB row and never uses this.
        try:
            from modules.flow_gate.services import ai_invoke_service
            run = ai_invoke_service.get_run_record(str(row.get("run_id")))
        except Exception:
            run = None
        if run is None or run.get("status") == "finished":
            release(group_id, str(row.get("run_id")))
            return None
    return None if _expired(row) else row


def acquire(*, group_id: str, project_id: str, run_id: str, chain_id: Optional[str],
            action_scope: str, worker_identity: Optional[str]) -> Optional[dict]:
    """Atomically acquire, or transfer a releasing lease to the next hop."""
    stamp = now_iso()
    expires = _expiry(ACQUIRING_TTL_SEC)
    if _using_memory():
        with _memory_lock:
            _sync_test_scope()
            current = _memory.get(group_id)
            if current and _expired(current):
                _memory.pop(group_id, None)
                current = None
            if any(row.get("run_id") == run_id for gid, row in _memory.items() if gid != group_id):
                # 0401 NR0003 §4 / T0004 item 7: mirrors the DB-path pre-check below so the
                # in-memory contract direct service tests run against (PYTEST_CURRENT_TEST
                # forces _using_memory() True) matches production's.
                raise RunIdCollision(run_id)
            if current:
                if not (current.get("state") == "releasing" and chain_id and current.get("chain_id") == chain_id):
                    return None
                generation = int(current.get("generation") or 1) + 1
                acquired_at = current.get("acquired_at") or stamp
            else:
                generation, acquired_at = 1, stamp
            _memory[group_id] = {
                "group_id": group_id, "project_id": project_id, "run_id": run_id,
                "chain_id": chain_id, "token_id": None, "action_scope": action_scope,
                "worker_identity": worker_identity, "state": "acquiring",
                "generation": generation, "acquired_at": acquired_at,
                "heartbeat_at": stamp, "expires_at": expires, "updated_at": stamp,
            }
            return dict(_memory[group_id])
    store = get_store()
    with store.transaction():
        current = get(group_id)
        if current and _expired(current):
            store._execute("DELETE FROM group_ai_leases WHERE group_id = ? AND run_id = ?", [group_id, current["run_id"]])
            current = None
        # 0401 NR0003 §4 / T0004 item 7: neither write below is protected against the
        # run_id UNIQUE index (migration 077) -- only group_id has an ON CONFLICT target.
        # Check first so a colliding run_id (two runs minted the same today-serial) comes
        # back as RunIdCollision for the caller to retry, instead of an unhandled DB
        # integrity error surfacing as a raw 500 out of the AI-invoke start path.
        if store._fetch_one("SELECT 1 AS x FROM group_ai_leases WHERE run_id = ?", [run_id]) is not None:
            raise RunIdCollision(run_id)
        if current:
            if not (current.get("state") == "releasing" and chain_id and current.get("chain_id") == chain_id):
                return None
            store._execute(
                "UPDATE group_ai_leases SET run_id = ?, token_id = NULL, action_scope = ?, worker_identity = ?, "
                "state = 'acquiring', generation = generation + 1, heartbeat_at = ?, expires_at = ?, updated_at = ? "
                "WHERE group_id = ? AND run_id = ? AND state = 'releasing'",
                [run_id, action_scope, worker_identity, stamp, expires, stamp, group_id, current["run_id"]],
            )
        else:
            store._execute(
                "INSERT INTO group_ai_leases (group_id, project_id, run_id, chain_id, token_id, action_scope, "
                "worker_identity, state, generation, acquired_at, heartbeat_at, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?, NULL, ?, ?, 'acquiring', 1, ?, ?, ?, ?) ON CONFLICT(group_id) DO NOTHING",
                [group_id, project_id, run_id, chain_id, action_scope, worker_identity, stamp, stamp, expires, stamp],
            )
        owned = get(group_id)
        return owned if owned and owned.get("run_id") == run_id else None


def activate(group_id: str, run_id: str, token_id: Optional[str], action_scope: str,
             worker_identity: Optional[str], ttl_seconds: int) -> Optional[dict]:
    stamp, expires = now_iso(), _expiry(ttl_seconds + ACTIVE_GRACE_SEC)
    if _using_memory():
        with _memory_lock:
            row = _memory.get(group_id)
            if not row or row.get("run_id") != run_id or row.get("state") != "acquiring":
                return None
            row.update(token_id=token_id, action_scope=action_scope, worker_identity=worker_identity,
                       state="active", heartbeat_at=stamp, expires_at=expires, updated_at=stamp)
            return dict(row)
    get_store()._execute(
        "UPDATE group_ai_leases SET token_id = ?, action_scope = ?, worker_identity = ?, state = 'active', "
        "heartbeat_at = ?, expires_at = ?, updated_at = ? WHERE group_id = ? AND run_id = ? AND state = 'acquiring'",
        [token_id, action_scope, worker_identity, stamp, expires, stamp, group_id, run_id],
    )
    row = get(group_id)
    return row if row and row.get("run_id") == run_id and row.get("state") == "active" else None


def heartbeat(group_id: str, run_id: str, ttl_seconds: int = ACTIVE_HEARTBEAT_TTL_SEC) -> bool:
    stamp, expires = now_iso(), _expiry(ttl_seconds)
    if _using_memory():
        with _memory_lock:
            row = _memory.get(group_id)
            if not row or row.get("run_id") != run_id:
                return False
            row.update(heartbeat_at=stamp, expires_at=expires, updated_at=stamp)
            return True
    get_store()._execute(
        "UPDATE group_ai_leases SET heartbeat_at = ?, expires_at = ?, updated_at = ? "
        "WHERE group_id = ? AND run_id = ? AND state IN ('active', 'releasing')",
        [stamp, expires, stamp, group_id, run_id],
    )
    row = get(group_id)
    return bool(row and row.get("run_id") == run_id)


def begin_handoff(group_id: str, run_id: str) -> bool:
    stamp, expires = now_iso(), _expiry(HANDOFF_TTL_SEC)
    if _using_memory():
        with _memory_lock:
            row = _memory.get(group_id)
            if not row or row.get("run_id") != run_id or row.get("state") != "active":
                return False
            row.update(state="releasing", heartbeat_at=stamp, expires_at=expires, updated_at=stamp)
            return True
    get_store()._execute(
        "UPDATE group_ai_leases SET state = 'releasing', heartbeat_at = ?, expires_at = ?, updated_at = ? "
        "WHERE group_id = ? AND run_id = ? AND state = 'active'",
        [stamp, expires, stamp, group_id, run_id],
    )
    row = get(group_id)
    return bool(row and row.get("run_id") == run_id and row.get("state") == "releasing")


def update_token(group_id: str, run_id: str, token_id: Optional[str],
                  action_scope: Optional[str] = None) -> None:
    """Re-point the lease at a reissued token (0359 L0007 §2.9).

    0417 T0013: a document_review_loop hop reissues a token with a DIFFERENT action_scope
    than the one activate() first recorded (review <-> edit as the loop alternates stages) —
    without also refreshing action_scope here, mutation_policy's owner-match check compares
    the new token's real scope against this lease's stale one and 403s every rework hop.
    action_scope is optional so a plain (single-scope) run's retry, which passes None, keeps
    leaving it untouched exactly as before.
    """
    stamp = now_iso()
    if _using_memory():
        with _memory_lock:
            row = _memory.get(group_id)
            if row and row.get("run_id") == run_id:
                updates = {"token_id": token_id, "heartbeat_at": stamp, "updated_at": stamp}
                if action_scope is not None:
                    updates["action_scope"] = action_scope
                row.update(**updates)
        return
    if action_scope is not None:
        get_store()._execute(
            "UPDATE group_ai_leases SET token_id = ?, action_scope = ?, heartbeat_at = ?, "
            "updated_at = ? WHERE group_id = ? AND run_id = ?",
            [token_id, action_scope, stamp, stamp, group_id, run_id],
        )
    else:
        get_store()._execute(
            "UPDATE group_ai_leases SET token_id = ?, heartbeat_at = ?, updated_at = ? WHERE group_id = ? AND run_id = ?",
            [token_id, stamp, stamp, group_id, run_id],
        )


def release(group_id: str, run_id: str) -> bool:
    if _using_memory():
        with _memory_lock:
            row = _memory.get(group_id)
            if not row or row.get("run_id") != run_id:
                return False
            _memory.pop(group_id, None)
            return True
    row = get(group_id)
    if not row or row.get("run_id") != run_id:
        return False
    get_store()._execute("DELETE FROM group_ai_leases WHERE group_id = ? AND run_id = ?", [group_id, run_id])
    return get(group_id) is None


def max_serial_for_date(date_str: str) -> int:
    """Highest NNNNNN serial already claimed by an OPEN lease whose run_id starts
    with ``aiv_<date_str>_`` (0401 NR0003 §4 / T0004 item 7).

    A run still admitting or running has no ai_invoke_runs row yet (that table is
    written once, at finalize), but its lease already reserves the serial -- so the
    in-memory run-id counter must be floored against this too, not just the
    finished-run table, or a fresh process could still mint an id that collides
    with a run that is mid-flight in another process.
    """
    prefix = f"aiv_{date_str}_"
    if _using_memory():
        with _memory_lock:
            _sync_test_scope()
            highest = 0
            for row in _memory.values():
                run_id = str(row.get("run_id") or "")
                if run_id.startswith(prefix):
                    try:
                        highest = max(highest, int(run_id.rsplit("_", 1)[-1]))
                    except ValueError:
                        continue
            return highest
    rows = get_store()._fetch_all(
        "SELECT run_id FROM group_ai_leases WHERE run_id LIKE ?", [f"{prefix}%"]
    )
    highest = 0
    for row in rows:
        try:
            highest = max(highest, int(str(row["run_id"]).rsplit("_", 1)[-1]))
        except (TypeError, ValueError):
            continue
    return highest


def list_active_by_project(project_id: str) -> list[dict]:
    recover_expired()
    if _using_memory():
        with _memory_lock:
            return [dict(row) for row in _memory.values() if row.get("project_id") == project_id]
    return get_store()._fetch_all("SELECT * FROM group_ai_leases WHERE project_id = ? ORDER BY acquired_at", [project_id])