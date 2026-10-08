"""Git/source concurrency storage (flowgate.default.0669 unit 1a; 0666 0010-DB, migrations 130~133).

server_instance / resource_lock CRUD and CAS writes. Every query
is the one 0010-DB §4 fixes, letter for letter where it has one — (a)~(f), (m)~(p).
operation_job / resource_reservation exist in the schema (resource_lock.job_id FK) but
0669 does not run jobs, so nothing here reads or writes them.

Contract shared by every CAS write below (DB §2.0, invariant 5): the WHERE carries the
previous value (epoch / status / holder) and success is the affected-row count from
``_execute_affected`` — never a re-read. A write whose SET could equal the stored value
on a match bumps a dedicated ``touch_seq`` so MySQL/MariaDB (which report *changed*
rows) agrees with SQLite/PostgreSQL (which report *matched* rows).

Time columns hold ``now_iso()`` strings; callers bind every threshold already computed
in that format. No DB clock function appears in a condition (DB §2.0, invariant 7).
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso

DOMAINS = ("P", "G", "W", "B", "R", "M")
HOLD_CLASSES = ("short", "long")
INSTANCE_STATUSES = ("alive", "stopped", "dead")


# ── current process instance (L 2.8 INSTANCE_ID) ─────────────────────────────
# Held here, not in the service layer, so the DB layer can read it without importing
# services.

_current_instance_id: Optional[str] = None


def current_instance_id() -> Optional[str]:
    """This process's registered instance id, or None before/without registration."""
    return _current_instance_id


def set_current_instance_id(instance_id: Optional[str]) -> None:
    global _current_instance_id
    _current_instance_id = instance_id


# ── server_instance (DB §2.4) ────────────────────────────────────────────────

def insert_instance(instance_id: str, node_key: str, pid: int,
                    process_started_at: Optional[str], now: Optional[str] = None) -> None:
    now = now or now_iso()
    get_store()._execute(
        "INSERT INTO server_instance "
        "(instance_id, node_key, pid, process_started_at, started_at, heartbeat_at, status, touch_seq) "
        "VALUES (?, ?, ?, ?, ?, ?, 'alive', 0)",
        [instance_id, node_key, int(pid), process_started_at, now, now],
    )


def get_instance(instance_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM server_instance WHERE instance_id = ?", [instance_id]
    )


def heartbeat_instance(instance_id: str, now: str) -> int:
    """(m) Instance heartbeat. 0 = this instance was declared dead/stopped (L 2.8).

    touch_seq makes a same-second repeat still count as one affected row on MySQL —
    without it a live instance would read its own heartbeat as a death sentence.
    """
    return get_store()._execute_affected(
        "UPDATE server_instance SET heartbeat_at = ?, touch_seq = touch_seq + 1 "
        "WHERE instance_id = ? AND status = 'alive'",
        [now, instance_id],
    )


def mark_instance_stopped(instance_id: str, now: str) -> int:
    """Graceful shutdown. alive -> stopped only (monotonic, DB invariant 4)."""
    return get_store()._execute_affected(
        "UPDATE server_instance SET status = 'stopped', stopped_at = ? "
        "WHERE instance_id = ? AND status = 'alive'",
        [now, instance_id],
    )


def mark_instance_dead(instance_id: str) -> int:
    """alive -> dead for one instance (same-node process gone, L 2.26 step 2)."""
    return get_store()._execute_affected(
        "UPDATE server_instance SET status = 'dead' WHERE instance_id = ? AND status = 'alive'",
        [instance_id],
    )


def mark_dead_other_nodes(my_node_key: str, threshold: str) -> int:
    """(n) Batch alive -> dead for OTHER nodes whose heartbeat is older than threshold.

    threshold = now - INSTANCE_DEAD_AFTER_SEC - CLOCK_SKEW_MARGIN_SEC. Same-node
    instances are judged by os_process_alive instead, never by this query.
    """
    return get_store()._execute_affected(
        "UPDATE server_instance SET status = 'dead' "
        "WHERE status = 'alive' AND node_key <> ? AND heartbeat_at < ?",
        [my_node_key, threshold],
    )


def list_alive_instances(node_key: Optional[str] = None) -> list[dict]:
    if node_key is None:
        return get_store()._fetch_all(
            "SELECT * FROM server_instance WHERE status = 'alive'", []
        )
    return get_store()._fetch_all(
        "SELECT * FROM server_instance WHERE status = 'alive' AND node_key = ?", [node_key]
    )


# ── legacy_admission_gate: unused since 0669 unit 9c (L 2.6 step 9 removed the gate).
# The table is left in the schema.


# ── resource_lock (DB §2.1, L 2.5) ───────────────────────────────────────────

def insert_lock(*, lock_key: str, domain: str, project_id: str, group_id: Optional[str],
                target_key: Optional[str], holder_ctx_id: str, holder_kind: str,
                job_id: Optional[str], instance_id: str, lock_epoch: str, hold_class: str,
                acquired_at: str, heartbeat_until: Optional[str]) -> None:
    """(a) Acquire = insert. Raises on a lock_key collision; the caller classifies it
    with get_resource_lock (L 2.5.3) and never retries the insert blindly.

    Under PostgreSQL an expected unique violation aborts its transaction, so the caller
    runs this in a transaction of its own (D 8.4).
    """
    get_store()._execute(
        "INSERT INTO resource_lock "
        "(lock_key, domain, project_id, group_id, target_key, holder_ctx_id, holder_kind, "
        "job_id, instance_id, lock_epoch, hold_class, acquired_at, heartbeat_until, protected) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
        [lock_key, domain, project_id, group_id, target_key, holder_ctx_id, holder_kind,
         job_id, instance_id, lock_epoch, hold_class, acquired_at, heartbeat_until],
    )


def get_resource_lock(lock_key: str) -> Optional[dict]:
    """(b) Re-read after an insert failure."""
    return get_store()._fetch_one(
        "SELECT * FROM resource_lock WHERE lock_key = ?", [lock_key]
    )


def delete_lock(lock_key: str, holder_ctx_id: str, lock_epoch: str) -> int:
    """(c) Release. A protected row is never deleted by its holder (L 2.5.4)."""
    return get_store()._execute_affected(
        "DELETE FROM resource_lock "
        "WHERE lock_key = ? AND holder_ctx_id = ? AND lock_epoch = ? AND protected = 0",
        [lock_key, holder_ctx_id, lock_epoch],
    )


def reclaim_lock(lock_key: str, lock_epoch: str, exec_seq: int) -> int:
    """(d) Stale reclaim. exec_seq guards against a holder that started a process
    between the stale judgement and this delete (L 2.9.2)."""
    return get_store()._execute_affected(
        "DELETE FROM resource_lock "
        "WHERE lock_key = ? AND lock_epoch = ? AND protected = 0 AND exec_seq = ?",
        [lock_key, lock_epoch, int(exec_seq)],
    )


def recovery_release_lock(lock_key: str, holder_ctx_id: str) -> int:
    """Self-check restart recovery (L 2.24.3 unprotect_and_release): the run's owner is
    proved gone, so its row goes whatever its epoch or protection mark."""
    return get_store()._execute_affected(
        "DELETE FROM resource_lock WHERE lock_key = ? AND holder_ctx_id = ?",
        [lock_key, holder_ctx_id],
    )


def list_stale_long_locks(threshold: str, limit: int) -> list[dict]:
    """(e) Long locks whose heartbeat lapsed. threshold = now - CLOCK_SKEW_MARGIN_SEC."""
    return get_store()._fetch_all(
        "SELECT * FROM resource_lock "
        "WHERE hold_class = 'long' AND protected = 0 AND heartbeat_until < ? "
        "ORDER BY heartbeat_until LIMIT ?",
        [threshold, int(limit)],
    )


def protect_lock(lock_key: str, lock_epoch: str, reason: str, now: str) -> int:
    """(f) Protection mark (L 2.5.5). 0 = row gone or epoch changed.

    MySQL caveat (DB §2.0): re-protecting an already-protected row with the same reason
    in the same second changes nothing and reports 0 there — re-read before treating
    a repeat call's 0 as loss."""
    return get_store()._execute_affected(
        "UPDATE resource_lock "
        "SET protected = 1, protect_reason = ?, protected_at = ?, heartbeat_until = NULL "
        "WHERE lock_key = ? AND lock_epoch = ?",
        [reason, now, lock_key, lock_epoch],
    )


def heartbeat_long_lock(lock_key: str, lock_epoch: str, holder_ctx_id: str, until: str) -> int:
    """Long-lock liveness renewal (L 2.7). 0 = reclaimed or protected -> mark_lost.

    `until` is now + LOCK_HEARTBEAT_TTL_SEC and renewals are LOCK_HEARTBEAT_INTERVAL_SEC
    (>= 5s) apart, so the SET value always differs from the stored one.
    """
    return get_store()._execute_affected(
        "UPDATE resource_lock SET heartbeat_until = ? "
        "WHERE lock_key = ? AND lock_epoch = ? AND holder_ctx_id = ? AND protected = 0",
        [until, lock_key, lock_epoch, holder_ctx_id],
    )


def list_locks_of_instance(instance_id: str) -> list[dict]:
    """Rows owned by one instance (idx_resource_lock_instance) — DEAD sweep (L 2.9)."""
    return get_store()._fetch_all(
        "SELECT * FROM resource_lock WHERE instance_id = ?", [instance_id]
    )


def list_unprotected_locks_of_non_alive(limit: int) -> list[dict]:
    """Sweep candidates whose owner is not an alive instance (L 2.17.2 step_stale_locks).

    Only a candidate list: the caller still judges each row with is_stale (unknown or
    NULL owner = DEAD, L 2.8) and reclaims it by epoch CAS (L 2.9.2).
    """
    return get_store()._fetch_all(
        "SELECT * FROM resource_lock rl WHERE rl.protected = 0 AND ("
        "rl.instance_id IS NULL OR NOT EXISTS (SELECT 1 FROM server_instance si "
        "WHERE si.instance_id = rl.instance_id AND si.status = 'alive')) "
        "ORDER BY rl.acquired_at LIMIT ?",
        [int(limit)],
    )


def list_locks_in_scope(project_id: str, group_id: Optional[str] = None) -> list[dict]:
    """Blocker diagnosis by scope (idx_resource_lock_scope, D 3.13)."""
    if group_id is None:
        return get_store()._fetch_all(
            "SELECT * FROM resource_lock WHERE project_id = ? ORDER BY acquired_at", [project_id]
        )
    return get_store()._fetch_all(
        "SELECT * FROM resource_lock WHERE project_id = ? AND group_id = ? ORDER BY acquired_at",
        [project_id, group_id],
    )
