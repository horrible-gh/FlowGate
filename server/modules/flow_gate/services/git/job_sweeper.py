"""Unified sweep tick (flowgate.default.0669 unit 5b; 0666 0009-L §1.4, §2.17.2, §2.18, §2.26 steps 6~11).

One daemon thread replaces the old 30-minute merge-sweep loop. Every
``SWEEP_TICK_SEC`` it runs, each step guarded so one failure never skips the rest:

1. step_instance_liveness — other nodes past INSTANCE_DEAD_AFTER_SEC, and this node's
   instances whose process is gone, become ``dead`` (L 2.8).
2. step_stale_locks — long locks whose heartbeat lapsed and rows of non-alive owners,
   judged by ``is_stale`` and reclaimed by epoch CAS (L 2.9).
3. step_expired_leases — running/freezing jobs whose lease lapsed or whose owner is
   not alive: reconcile claim (DB §4(k)), then the Runner's reconcile (L 2.17.3).
4. step_claim_candidates — round-robin over projects, oldest first inside one, only as
   many as free workers (L 2.17.2 fairness / saturation).
5. step_recovery_required_retry — recovery_required jobs due, inside
   RECONCILE_AUTO_WINDOW_SEC (L 2.18).
6. merge_session_sweep every MERGE_SWEEP_EVERY_TICKS (the old 30-minute period).
7. step_orphan_pins every ORPHAN_PIN_SWEEP_EVERY_TICKS (L 2.25, unit 6a): approval pins
   of failed/cancelled jobs deleted only when they still point at the recorded SHA.

Tick 0 runs at startup with steps 2 and 3 unbounded (L 2.26 steps 6~7); the workers
start just before it so the first claims have somewhere to go, and the
final_approval_publish executor with its freeze hooks (``approval_publish.install``,
unit 6b), the worktree_cleanup executor (``worktree_cleanup.install``, unit 7a) and the
worktree_provision executor (``worktree_provision.install``, unit 7b), the
base_publish executor (``base_publish.install``, unit 7c), the branch_merge_publish
executor (``branch_merge_publish.install``, unit 8a) and the archive_* executors
(``archive_publish.install``, unit 8b) are in place before either.

Not here: step_legacy_locks (L 2.6.5 — startup still releases every old row; while the
server lives an old row's owner is this process), step_reservations (L 2.12),
step_pack_refs (L 2.20.5, with the M wrapper).
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Optional

from modules.flow_gate.db import git_concurrency as db_locks
from modules.flow_gate.db import operation_job as db_jobs
from modules.flow_gate.db.connection import now_iso

from . import approval_freeze
from . import approval_publish
from . import archive_publish
from . import base_publish
from . import branch_merge_publish
from . import worktree_cleanup
from . import worktree_provision
from . import instance_registry as registry
from . import job_runner as runner
from . import job_store as store
from . import lock_manager as locks
from .lock_manager import _env_num

_log = logging.getLogger(__name__)


# ── parameters (L 1.4; FLOWGATE_ env override, clamped) ──────────────────────

def merge_sweep_every_ticks() -> int:
    return max(1, math.ceil(1800 / runner.sweep_tick_sec()))


def sweep_batch_size() -> int:
    return int(_env_num("FLOWGATE_SWEEP_BATCH_SIZE", 16, 1, 64))


def sweep_per_project_cap() -> int:
    return int(_env_num("FLOWGATE_SWEEP_PER_PROJECT_CAP", 2, 1, sweep_batch_size()))


def sweep_stale_lock_batch() -> int:
    return int(_env_num("FLOWGATE_SWEEP_STALE_LOCK_BATCH", 50, 1, 500))


def sweep_expired_lease_batch() -> int:
    return int(_env_num("FLOWGATE_SWEEP_EXPIRED_LEASE_BATCH", 16, 1, 64))


_FULL = 100000          # startup pass: "no batch limit" (L 2.26 steps 6~7)


# ── steps ────────────────────────────────────────────────────────────────────

def step_instance_liveness() -> int:
    now = now_iso()
    threshold = registry.add_seconds(now, -(registry.dead_after_sec() + registry.clock_skew_margin_sec()))
    marked = db_locks.mark_dead_other_nodes(registry.node_key(), threshold)
    marked += registry.mark_prior_same_node_dead()
    if marked:
        _log.warning("instance_marked_dead: %s instance(s)", marked)
    return marked


def step_stale_locks(full: bool = False) -> int:
    limit = _FULL if full else sweep_stale_lock_batch()
    threshold = registry.add_seconds(now_iso(), -registry.clock_skew_margin_sec())
    rows: dict[str, dict] = {}
    for row in db_locks.list_stale_long_locks(threshold, limit) + db_locks.list_unprotected_locks_of_non_alive(limit):
        rows.setdefault(row["lock_key"], row)
    reclaimed = 0
    for row in rows.values():
        if locks.is_stale(row) != locks.STALE_RECLAIMABLE:
            continue
        if locks.reclaim_stale_row(row):
            reclaimed += 1
            runner.notify_released(row["lock_key"], row.get("domain"), row.get("project_id"),
                                   row.get("group_id"), row.get("target_key"))
    return reclaimed


def step_expired_leases(full: bool = False) -> int:
    instance_id = store._instance_id()
    if instance_id is None:
        return 0
    now = now_iso()
    threshold = registry.add_seconds(now, -registry.clock_skew_margin_sec())
    rows = db_jobs.list_expired_leases(threshold, _FULL if full else sweep_expired_lease_batch())
    handled = 0
    for row in rows:
        lease_until = row.get("lease_until")
        reason = "lease_expired" if lease_until and lease_until < threshold else "owner_dead"
        token = store.new_lease_token()
        claimed_at = now_iso()
        until = registry.add_seconds(claimed_at, store.job_lease_sec())
        n = db_jobs.reconcile_claim(row["job_id"], row["status"], row.get("lease_token"),
                                    row.get("lease_owner"), threshold, instance_id, token, until,
                                    reason, claimed_at)
        if n != 1:
            continue                        # another Sweeper took it, or it moved on
        job = store.get_job(row["job_id"])
        if job is None:
            continue
        ctx = store._job_context(job, token, until)
        ctx.expected_statuses = frozenset((row["status"],))
        _log.warning("job_reconcile_claimed: %s (%s, %s)", row["job_id"], row["status"], reason)
        runner.reconcile_expired(ctx)
        handled += 1
    return handled


_rr_cursor: Optional[str] = None


def step_claim_candidates() -> int:
    global _rr_cursor
    slots = min(runner.free_slots(), sweep_batch_size())
    if slots <= 0:
        return 0
    kinds = runner.executable_kinds()
    if not kinds:
        return 0
    ids, _rr_cursor = store.claimable_job_ids(sweep_per_project_cap(), slots,
                                              kinds=kinds, cursor=_rr_cursor)
    handed = 0
    for job_id in ids:
        if handed >= slots:
            break
        cr = runner.try_claim(job_id)
        if cr is not None:
            runner.hand_to_worker(cr)
            handed += 1
    return handed


def step_recovery_required_retry() -> int:
    instance_id = store._instance_id()
    if instance_id is None:
        return 0
    now = now_iso()
    since_after = registry.add_seconds(now, -runner.reconcile_auto_window_sec())
    threshold = registry.add_seconds(now, -registry.clock_skew_margin_sec())
    handled = 0
    for row in db_jobs.recovery_retry_candidates(now, since_after, sweep_expired_lease_batch()):
        token = store.new_lease_token()
        claimed_at = now_iso()
        until = registry.add_seconds(claimed_at, store.job_lease_sec())
        if db_jobs.claim_recovery(row["job_id"], instance_id, token, until, claimed_at,
                                  since_after, threshold) != 1:
            continue
        job = store.get_job(row["job_id"])
        if job is None:
            continue
        ctx = store._job_context(job, token, until)
        ctx.expected_statuses = frozenset(("recovery_required",))
        runner.retry_recovery(ctx)
        handled += 1
    return handled


def _merge_session_sweep() -> None:
    from modules.flow_gate.services import git_service as _gs
    _gs.merge_session_sweep()


# ── tick and daemon ──────────────────────────────────────────────────────────

def _safe(name: str, fn, *args) -> None:
    try:
        fn(*args)
    except Exception:
        _log.warning("sweep step %s failed", name, exc_info=True)


def run_tick(tick_no: int, *, full: bool = False) -> None:
    _safe("instance_liveness", step_instance_liveness)
    _safe("stale_locks", step_stale_locks, full)
    _safe("expired_leases", step_expired_leases, full)
    _safe("claim_candidates", step_claim_candidates)
    _safe("recovery_required_retry", step_recovery_required_retry)
    if tick_no > 0 and tick_no % merge_sweep_every_ticks() == 0:
        _safe("merge_session_sweep", _merge_session_sweep)
    if tick_no > 0 and tick_no % approval_freeze.orphan_pin_sweep_every_ticks(runner.sweep_tick_sec()) == 0:
        _safe("orphan_pins", approval_freeze.step_orphan_pins)


_stop = threading.Event()


def run_forever() -> None:
    """Body of the sweep daemon thread: workers, tick 0 (full), then every tick."""
    _stop.clear()
    _safe("approval_executor", approval_publish.install)
    _safe("cleanup_executor", worktree_cleanup.install)
    _safe("provision_executor", worktree_provision.install)
    _safe("base_publish_executor", base_publish.install)
    _safe("branch_merge_executor", branch_merge_publish.install)
    _safe("archive_executor", archive_publish.install)
    _safe("job_workers_start", runner.start)
    _safe("tick_0", lambda: run_tick(0, full=True))
    tick_no = 0
    while not _stop.wait(runner.sweep_tick_sec()):
        tick_no += 1
        try:
            run_tick(tick_no)
        except Exception:
            _log.warning("sweep tick %s failed", tick_no, exc_info=True)


def shutdown(timeout: float = 5.0) -> None:
    """Stop the tick loop and the workers (graceful shutdown, before instance stop)."""
    _stop.set()
    runner.shutdown(timeout)
