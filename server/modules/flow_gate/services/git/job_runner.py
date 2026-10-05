"""Job Runner, workers and reconciler (flowgate.default.0669 unit 5b; 0666 0009-L §1.4,
§2.17.1, §2.17.3, §2.18, §4.4 framework, §4.5).

* Executors — one per job kind, registered by the units that own the kind (6~8). A
  kind without an executor is never claimed for execution (the candidate reads filter
  on registered kinds). Unit 6b registers final_approval_publish
  (``approval_publish.install``); the other kinds still have none.
* Workers — ``JOB_WORKERS_PER_INSTANCE`` daemon threads taking from the Notifier queue:
  a job the Sweeper already claimed, or a wake-up signal they turn into claims.
* Reconciler — the L 4.4 decision: ``decide(job)`` looks up a decider registered per
  (kind, phase). Fixed rows (freeze phases in a publish state, ``done``) are here; the
  kind tables arrive with their executors. A missing row is UNDECIDABLE, which parks the
  job in recovery_required — the safe side of "never re-run a side effect blindly".

Execution (L 4.5) always runs inside ``job_store.lease_scope`` with the job's lock
context bound, and ``finally`` unbinds it (leaked locks are released there). Every
thread here is a daemon stopped by an Event and joined with a bound, so no worker or
renewer outlives ``shutdown`` (0669 seq 40 lesson).

Not here: fairness reservations (L 2.12) — DOMAIN_RELEASED wakes the oldest blocked
jobs only. The freeze reconcile / freeze_release bodies live in ``approval_freeze``
(unit 6a) and are installed through ``register_freeze_hooks`` at sweep start; until
then a freezing job falls back to recovery_required.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from modules.flow_gate.db import operation_job as db
from modules.flow_gate.db.connection import now_iso

from . import instance_registry as registry
from . import job_notifier as notifier
from . import job_store as store
from . import lock_manager as locks
from .lock_manager import _env_num

_log = logging.getLogger(__name__)


# ── parameters (L 1.4; FLOWGATE_ env override, clamped) ──────────────────────

def job_workers_per_instance() -> int:
    return int(_env_num("FLOWGATE_JOB_WORKERS_PER_INSTANCE", 2, 1, 8))


def wakeup_domain_fanout() -> int:
    return int(_env_num("FLOWGATE_WAKEUP_DOMAIN_FANOUT", 4, 1, 16))


def sweep_tick_sec() -> float:
    return _env_num("FLOWGATE_SWEEP_TICK_SEC", 30, 5, 300)


def reconcile_auto_window_sec() -> int:
    return int(_env_num("FLOWGATE_RECONCILE_AUTO_WINDOW_SEC", 86400, 3600, 604800))


RECONCILE_PENDING = "reconcile_pending"
TERMINAL = frozenset(db.TERMINAL_STATUSES)


# ── registries (filled by units 6~8) ─────────────────────────────────────────

# run(ctx, entry) -> RunResult | None. entry is store.PUBLISH / FREEZE / RELEASE.
# None means the executor already recorded the outcome itself (job_write / finish_run).
Executor = Callable[[store.JobContext, str], Optional[store.RunResult]]

_executors: dict[str, Executor] = {}
_deciders: dict[tuple[str, str], Callable[[dict], "Decision"]] = {}
_freeze_reconciler: Optional[Callable[[store.JobContext], None]] = None
_freeze_releaser: Optional[Callable[[store.JobContext, str, str], None]] = None
_reg_guard = threading.Lock()


def register_executor(kind: str, run: Executor) -> None:
    if kind not in store.KINDS:
        raise store.JobProgramError("unknown_job_kind", kind=kind)
    with _reg_guard:
        _executors[kind] = run


def register_decider(kind: str, phase: str, decide_fn: Callable[[dict], "Decision"]) -> None:
    """One row of L table 4.4-1: (kind, recorded phase P) -> evidence check of step S."""
    with _reg_guard:
        _deciders[(kind, phase)] = decide_fn


def register_freeze_hooks(reconciler: Callable[[store.JobContext], None],
                          releaser: Callable[[store.JobContext, str, str], None]) -> None:
    """Unit 6: reconcile_freeze_expired (L 2.22.4) and freeze_release (L 2.19.3)."""
    global _freeze_reconciler, _freeze_releaser
    with _reg_guard:
        _freeze_reconciler, _freeze_releaser = reconciler, releaser


def executable_kinds() -> list[str]:
    with _reg_guard:
        return sorted(_executors)


# ── reconciler (L 4.4) ───────────────────────────────────────────────────────

APPLIED_NEXT = "applied_next"
NOT_APPLIED = "not_applied"
APPLIED_TERMINAL = "applied_terminal"
UNDECIDABLE = "undecidable"
TRANSIENT_CHECK_FAILURE = "transient_check_failure"

_FREEZE_PHASES = frozenset(("freeze_pending", "F1", "F3"))


@dataclass
class Decision:
    verdict: str
    next_phase: Optional[str] = None        # APPLIED_NEXT
    resume_phase: Optional[str] = None      # NOT_APPLIED (None = the recorded phase)
    evidence: Optional[dict] = None
    result: object = None                   # APPLIED_TERMINAL
    code: Optional[str] = None


def decide(job: dict) -> Decision:
    phase = job.get("phase")
    if phase in _FREEZE_PHASES:
        # Freeze phases live in freezing/freeze_wait only (L 2.22.4); seen here = mismatch.
        return Decision(UNDECIDABLE, code="freeze_phase_in_publish_state")
    if phase == "done":
        return Decision(APPLIED_TERMINAL)
    with _reg_guard:
        decide_fn = _deciders.get((job.get("kind"), phase))
    if decide_fn is None:
        return Decision(UNDECIDABLE, code="unknown_phase")
    try:
        return decide_fn(job)
    except Exception:
        # An evidence read that blew up proves nothing either way: never re-run.
        _log.warning("reconcile decide failed: %s", job.get("job_id"), exc_info=True)
        return Decision(TRANSIENT_CHECK_FAILURE, code="reconcile_check_error")


def _merged_evidence(job: dict, extra: Optional[dict]) -> Optional[str]:
    if not extra:
        return job.get("evidence")
    try:
        current = json.loads(job.get("evidence") or "{}")
        if not isinstance(current, dict):
            current = {"previous": current}
    except ValueError:
        current = {"previous": job.get("evidence")}
    current.update(extra)
    return store._canonical_json(current)


_LEASE_CLEARED = {"lease_owner": None, "lease_token": None, "lease_until": None}


def apply_decision(ctx: store.JobContext, d: Decision, *, in_recovery: bool = False) -> None:
    """L 2.17.3 mapping (and L 2.18 when the job sits in recovery_required).

    The job is left un-leased in every branch; JOB_PUBLISHABLE wakes a worker for the
    pending ones. Raises LeaseLost when the reconcile lease was taken from us.
    """
    job, now = ctx.job, now_iso()
    attempts = int(job.get("attempt_count") or 0)
    if d.verdict == APPLIED_TERMINAL:
        store.job_write(ctx, status="succeeded", result=d.result, finished_at=now,
                        phase_note=None, **_LEASE_CLEARED)
        notifier.job_terminal(job)
    elif d.verdict in (APPLIED_NEXT, NOT_APPLIED):
        phase = d.next_phase if d.verdict == APPLIED_NEXT else (d.resume_phase or job.get("phase"))
        store.job_write(ctx, status="pending", phase=phase, evidence=_merged_evidence(job, d.evidence),
                        phase_note=None, available_at=now, **_LEASE_CLEARED)
        notifier.job_publishable(job)
    elif d.verdict == TRANSIENT_CHECK_FAILURE and attempts + 1 <= store.retry_max_attempts():
        a = attempts + 1
        store.job_write(ctx, status="retry_wait", attempt_count=a, phase_note=RECONCILE_PENDING,
                        available_at=registry.add_seconds(now, store.next_delay(a)),
                        last_error_code=d.code or "reconcile_check_failed", retryable=1,
                        **_LEASE_CLEARED)
    else:
        # UNDECIDABLE, or a check that kept failing: an operator decides, nothing re-runs.
        changes = dict(available_at=registry.add_seconds(now, store.reconcile_retry_interval_sec()),
                       last_error_code=d.code or "reconcile_undecidable", **_LEASE_CLEARED)
        if not in_recovery:
            changes.update(status="recovery_required", recovery_since=now)
        store.job_write(ctx, **changes)


# ── execution (L 4.5) ────────────────────────────────────────────────────────

def _hand_back(ctx: store.JobContext, code: str) -> None:
    """Claimed but cannot run here (executor vanished): back to where it was claimed from."""
    back = ctx.job.get("claimed_from") or "pending"
    store.job_write(ctx, status=back, last_error_code=code,
                    available_at=registry.add_seconds(now_iso(), store.reconcile_retry_interval_sec()),
                    **_LEASE_CLEARED)


def _reconcile_at_entry(ctx: store.JobContext) -> bool:
    """retry_wait(reconcile_pending) claimed for publish: reconcile before any re-run
    (D 3.6.4). True = continue with the executor from the decided phase."""
    job = ctx.job
    d = decide(job)
    if d.verdict in (APPLIED_NEXT, NOT_APPLIED):
        phase = d.next_phase if d.verdict == APPLIED_NEXT else (d.resume_phase or job.get("phase"))
        store.job_write(ctx, phase=phase, evidence=_merged_evidence(job, d.evidence), phase_note=None)
        return True
    apply_decision(ctx, d)
    return False


def execute(cr: store.ClaimResult) -> None:
    """Run one WON claim to the end of this attempt. Never raises."""
    ctx, entry = cr.ctx, cr.phase_entry
    job = ctx.job
    locks.bind(ctx.lock_ctx)
    try:
        with store.lease_scope(ctx):
            with _reg_guard:
                run = _executors.get(job.get("kind"))
            if run is None:
                _hand_back(ctx, "no_executor")
                return
            if entry == store.PUBLISH and job.get("phase_note") == RECONCILE_PENDING:
                if not _reconcile_at_entry(ctx):
                    return
            result = run(ctx, entry)
            if result is not None:
                store.finish_run(ctx, result)
    except store.LeaseLost:
        _log.warning("job lease lost during run: %s", ctx.job_id)
    except Exception as exc:
        # The side effects of this attempt are unknown: record that and let the lease run
        # out, so the Sweeper's reconcile decides (I3). Nothing is retried blindly.
        _log.error("job executor failed: %s (%s)", ctx.job_id, type(exc).__name__, exc_info=True)
        if not ctx.lease_lost:
            try:
                store.finish_run(ctx, store.RunResult(store.UNKNOWN, code="executor_exception"))
            except Exception:
                _log.warning("job unknown-result record failed: %s", ctx.job_id, exc_info=True)
    finally:
        try:
            locks.unbind(ctx.lock_ctx)        # releases anything the executor leaked
        except Exception:
            _log.warning("job context unbind failed: %s", ctx.job_id, exc_info=True)
        if ctx.job.get("status") in TERMINAL:
            notifier.job_terminal(ctx.job)


def reconcile_expired(ctx: store.JobContext) -> None:
    """L 2.17.2 after a won reconcile claim: running -> 4.4, freezing -> unit 6 hook."""
    locks.bind(ctx.lock_ctx)
    try:
        with store.lease_scope(ctx):
            if ctx.job.get("status") == "freezing":
                if _freeze_reconciler is not None:
                    _freeze_reconciler(ctx)
                else:
                    apply_decision(ctx, Decision(UNDECIDABLE, code="freeze_reconcile_unavailable"))
                return
            apply_decision(ctx, decide(ctx.job))
    except store.LeaseLost:
        _log.warning("reconcile lease lost: %s", ctx.job_id)
    except Exception:
        _log.error("reconcile failed: %s", ctx.job_id, exc_info=True)
    finally:
        try:
            locks.unbind(ctx.lock_ctx)
        except Exception:
            _log.warning("reconcile context unbind failed: %s", ctx.job_id, exc_info=True)


def retry_recovery(ctx: store.JobContext) -> None:
    """L 2.18 body for one recovery_required job whose retry lease we won."""
    locks.bind(ctx.lock_ctx)
    try:
        with store.lease_scope(ctx):
            intent = ctx.job.get("release_intent")
            if intent:
                if _freeze_releaser is None:
                    apply_decision(ctx, Decision(UNDECIDABLE, code="freeze_release_unavailable"),
                                   in_recovery=True)
                    return
                try:
                    parsed = json.loads(intent)
                except ValueError:
                    parsed = {}
                _freeze_releaser(ctx, parsed.get("final_status") or "cancelled",
                                 parsed.get("code") or "cancelled_by_user")
                return
            apply_decision(ctx, decide(ctx.job), in_recovery=True)
    except store.LeaseLost:
        _log.warning("recovery retry lease lost: %s", ctx.job_id)
    except Exception:
        _log.error("recovery retry failed: %s", ctx.job_id, exc_info=True)
    finally:
        try:
            locks.unbind(ctx.lock_ctx)
        except Exception:
            _log.warning("recovery context unbind failed: %s", ctx.job_id, exc_info=True)


# ── claim helpers ────────────────────────────────────────────────────────────

def try_claim(job_id: str) -> Optional[store.ClaimResult]:
    """Re-read, pick the mode from the row, one CAS (I1). None unless WON."""
    job = store.get_job(job_id)
    if job is None:
        return None
    with _reg_guard:
        if job.get("kind") not in _executors:
            return None
    mode = store.mode_for(job)
    if mode is None:
        return None
    cr = store.claim(job_id, mode)
    return cr if cr.outcome == store.WON else None


def targets_of(sig: notifier.Signal) -> list[str]:
    if sig.kind in (notifier.JOB_CREATED, notifier.JOB_PUBLISHABLE):
        return [sig.job_id] if sig.job_id else []
    if sig.kind == notifier.DOMAIN_RELEASED:
        if sig.domain == "*":
            return db.blocked_job_ids_of_project(sig.project_id, wakeup_domain_fanout())
        return db.blocked_job_ids(sig.lock_key, wakeup_domain_fanout()) if sig.lock_key else []
    return []                                   # JOB_TERMINAL: for other consumers (SSE)


# ── workers ──────────────────────────────────────────────────────────────────

_stop = threading.Event()
_threads: list[threading.Thread] = []
_busy = 0
_busy_guard = threading.Lock()
_release_hook_installed = False


def _run_counted(cr: store.ClaimResult) -> None:
    global _busy
    with _busy_guard:
        _busy += 1
    try:
        execute(cr)
    finally:
        with _busy_guard:
            _busy -= 1


def free_slots() -> int:
    """Worker saturation (L 2.17.2): claim only what a worker can start right away."""
    if not _threads:
        return 0
    with _busy_guard:
        busy = _busy
    return job_workers_per_instance() - busy - notifier.queue().pending_hand_offs()


def hand_to_worker(cr: store.ClaimResult) -> None:
    notifier.queue().hand_off(cr)


def _worker_loop() -> None:
    q = notifier.queue()
    while not _stop.is_set():
        try:
            item = q.take(timeout=sweep_tick_sec())
            if item is None or _stop.is_set():
                continue
            if isinstance(item, store.ClaimResult):
                _run_counted(item)
                continue
            for job_id in targets_of(item):
                if _stop.is_set():
                    break
                cr = try_claim(job_id)
                if cr is not None:
                    _run_counted(cr)
        except Exception:
            _log.warning("job worker loop error", exc_info=True)


def notify_released(lock_key: str, domain: str, project_id: str,
                    group_id: Optional[str] = None, target_key: Optional[str] = None) -> None:
    """DOMAIN_RELEASED after a committed release or reclaim."""
    with _reg_guard:
        if not _executors:
            return          # nothing can be waiting on a lock yet: skip the wake-up read
    notifier.domain_released(lock_key, domain, project_id, group_id, target_key)


def _on_lock_released(h: locks.Held) -> None:
    notify_released(h.lock_key, h.domain, h.project_id, h.group_id, h.target_key)


def start() -> None:
    """Start the workers (L 2.26 step 11). Idempotent."""
    global _release_hook_installed
    with _busy_guard:
        if _threads and any(t.is_alive() for t in _threads):
            return
        _stop.clear()
        _threads.clear()
        if not _release_hook_installed:
            locks.on_released(_on_lock_released)
            _release_hook_installed = True
        for i in range(job_workers_per_instance()):
            t = threading.Thread(target=_worker_loop, name=f"job-worker-{i}", daemon=True)
            _threads.append(t)
            t.start()


def shutdown(timeout: float = 5.0) -> None:
    """Stop taking work and join the workers (bounded). A job mid-run keeps its lease
    until it finishes or the lease lapses; the reconcile path covers the rest."""
    _stop.set()
    notifier.queue().wake_all()
    with _busy_guard:
        threads = list(_threads)
        _threads.clear()
    for t in threads:
        if t is not threading.current_thread():
            t.join(timeout)
