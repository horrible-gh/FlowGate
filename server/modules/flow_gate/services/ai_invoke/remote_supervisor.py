"""Remote AI Job supervisor (flowgate.v02.0129 T#2, D0005 §3-4 / §3-6).

The executor (``provider_remote.execute``) watches its own Job while it runs. This module
covers everything that must keep happening without it:

* **Settlement** ("종결"): a remote Job is settled when it is terminal and, if the server
  expired it without an Agent report, the run token it carried is revoked (``fenced_at``).
  Until then the run that created it counts as alive: Graph launch, Graph recovery, Stop
  and terminal cleanup all ask :func:`run_has_unsettled_job`.
* **Periodic tick** (:func:`tick`, a daemon thread started at bootstrap): applies the
  stage limits, hands over and scrubs Jobs whose owner run vanished or whose retrieval
  deadline passed, retries token revocation of unconfirmed ends, finishes in-process runs
  whose watch was lost once their Job is settled, and keeps restart holds alive.
  It also sweeps unowned review loops and aged unadmitted Graph requests, even without remote Jobs.
* **Restart hold**: at startup, a run of the previous process whose Job is not settled is
  recorded in ``ai_remote_recovery_holds`` (durable) and kept out of the lease reclaim and
  the Graph lease expiry; its token revocation is attempted at once. The tick heartbeats
  its leases and, once the Job is settled, resumes exactly the restart recovery that was
  skipped for it (end record, group lease release + orphan token revoke, Graph lease
  expiry and the Graph execution recovery / Stop continuation).

The existing tick also resumes stranded Graph executions through their own recovery and refill boundaries.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from .runtime import _svc, logger, REVIEW_LOOP_ORPHAN_TTL_SEC, REVIEW_LOOP_SWEEP_EVERY_TICKS

SUPERVISOR_INTERVAL_SEC = 15.0
FENCE_REASON = "remote_termination_unconfirmed"
HOLD_TABLE = "ai_remote_recovery_holds"

_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()
_stop = threading.Event()
_tick_lock = threading.Lock()


def _store():
    from modules.flow_gate.db.connection import get_store
    return get_store()


def _jobs():
    from modules.flow_gate.services.agent_jobs import AgentJobService
    return AgentJobService()


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def available(store=None) -> bool:
    """Is the remote Job schema present? A DB-less unit test or a pre-172 schema has none."""
    if store is None:
        try:
            store = _store()
        except Exception:
            return False
    if getattr(store, "_db", "present") is None:
        return False
    checker = getattr(store, "column_exists", None)
    if checker is None:
        # A store without schema introspection (some test doubles): probe the column.
        try:
            store._fetch_all("SELECT fenced_at FROM agent_jobs WHERE 1 = 0", [])
            return True
        except Exception:
            return False
    try:
        return bool(checker("agent_jobs", "fenced_at"))
    except Exception:
        return True


# ── settlement ───────────────────────────────────────────────────────────────

def run_has_unsettled_job(ai_run_id: Optional[str]) -> bool:
    """Fail closed: an unreadable queue means "maybe still running"."""
    if not ai_run_id or not available():
        return False
    try:
        return bool(_jobs().unsettled_ai_jobs(ai_run_id=str(ai_run_id)))
    except Exception:
        logger.warning("ai-invoke %s: remote Job settlement unreadable; treated as alive", ai_run_id, exc_info=True)
        return True


def executions_unsettled(exec_ids: Iterable[str]) -> bool:
    exec_ids = [e for e in exec_ids if e]
    if not exec_ids or not available():
        return False
    try:
        return bool(_jobs().unsettled_ai_jobs(graph_exec_ids=exec_ids))
    except Exception:
        logger.warning("Graph executions %s: remote Job settlement unreadable; treated as alive",
                       exec_ids, exc_info=True)
        return True


def is_held(ai_run_id: Optional[str]) -> bool:
    """A restart hold that has not been resumed yet (fail closed on a read error)."""
    if not ai_run_id or not available():
        return False
    try:
        return _store()._fetch_one(
            f"SELECT 1 AS present FROM {HOLD_TABLE} WHERE run_id = ? AND resumed_at IS NULL",
            [str(ai_run_id)]) is not None
    except Exception:
        logger.warning("ai-invoke %s: restart hold unreadable; treated as held", ai_run_id, exc_info=True)
        return True


def remote_alive(ai_run_id: Optional[str]) -> bool:
    """The run's remote work is not settled, or its restart recovery is still held."""
    return run_has_unsettled_job(ai_run_id) or is_held(ai_run_id)


def prior_remote_unsettled(run: dict) -> bool:
    """D0005 §3-3 ① defensive check for a run that replaces another one.

    Same run (an earlier attempt), the same Graph execution (a replay re-uses it), or --
    outside Graph -- the same group (a group runs one AI run at a time, so any other
    run's live Job there belongs to the run this one follows).
    """
    if not available():
        return False
    exec_id = run.get("graph_exec_id")
    try:
        rows = _jobs().unsettled_ai_jobs(
            ai_run_id=run.get("run_id"),
            graph_exec_ids=[exec_id] if exec_id else None,
            group_id=None if exec_id else run.get("group_id"),
        )
    except Exception:
        logger.warning("ai-invoke %s: previous remote Job state unreadable", run.get("run_id"), exc_info=True)
        return True
    return bool(rows)


# ── fencing (token revocation of an unconfirmed end) ─────────────────────────

def revoke_token(token_id: Optional[str]) -> bool:
    """Revoke a run token through the audited path. A token that does not exist is fenced."""
    if not token_id:
        return True
    try:
        from modules.flow_gate.services import token_service
        token_service.revoke(token_id, reason=FENCE_REASON)
        return True
    except Exception as exc:
        if getattr(exc, "status_code", None) == 404:
            return True
        logger.warning("ai-invoke: token %s revocation failed", token_id, exc_info=True)
        return False


def fence_job(svc, row: dict) -> bool:
    """Revoke the token one Job carried and record it on that Job (and its siblings)."""
    if not revoke_token(row.get("ai_token_id")):
        return False
    try:
        if row.get("ai_token_id"):
            svc.mark_fenced(token_id=row["ai_token_id"])
        svc.mark_fenced(job_id=row["job_id"], attempt_id=row["attempt_id"])
        return True
    except Exception:
        logger.warning("ai-invoke: fence record failed for job %s", row.get("job_id"), exc_info=True)
        return False


def _fence_run(svc, run_id: str) -> bool:
    """Revoke every token the run's Jobs carried (restart: §3-4 step 1)."""
    rows = _store()._fetch_all(
        "SELECT DISTINCT ai_token_id FROM agent_jobs WHERE ai_run_id = ? AND ai_token_id IS NOT NULL",
        [run_id])
    ok = all([revoke_token(r["ai_token_id"]) for r in rows])
    if ok:
        svc.mark_fenced(ai_run_id=run_id)
    return ok


# ── restart hold ─────────────────────────────────────────────────────────────

def _hold_rows(active_only: bool = True) -> list[dict]:
    where = " WHERE resumed_at IS NULL" if active_only else ""
    return _store()._fetch_all(f"SELECT * FROM {HOLD_TABLE}{where} ORDER BY held_at, run_id", [])


def held_run_ids() -> set[str]:
    """Runs whose restart recovery is on hold. Raises when it cannot be read."""
    if not available():
        return set()
    return {row["run_id"] for row in _hold_rows()}


def startup_hold_remote_runs() -> set[str]:
    """Before any restart lease recovery: settle what can be settled, hold the rest.

    1. The previous process's Jobs: NEW -> cancelled, claimed -> asked to stop with
       retrieval handed over, terminal -> handed over; settled bodies scrubbed.
    2. Every run that still has an unsettled Job is recorded as held (durable, so a
       second restart keeps it) and its token revocation is attempted immediately.
    Returns the held run ids (including holds left by an earlier restart).
    """
    if not available():
        return set()
    svc = _jobs()
    try:
        settled = svc.sweep_orphaned_ai_jobs()
        if settled:
            logger.info("[ai_invoke] startup settled %d orphaned remote AI Job(s)", settled)
    except Exception:
        logger.warning("[ai_invoke] startup remote AI Job sweep failed", exc_info=True)
    store = _store()
    for row in svc.unsettled_ai_run_ids():
        run_id = row["ai_run_id"]
        if _svc().get_run_record(run_id) is not None:
            continue                    # admitted by this process: its executor owns it
        ts = _stamp()
        existing = store._fetch_one(f"SELECT run_id, resumed_at FROM {HOLD_TABLE} WHERE run_id = ?", [run_id])
        if existing is None:
            project_id = None
            if row.get("ai_group_id"):
                try:
                    from modules.flow_gate.db import groups as db_groups
                    project_id = (db_groups.get_by_id(row["ai_group_id"]) or {}).get("project_id")
                except Exception:
                    project_id = None
            store._execute(
                f"INSERT INTO {HOLD_TABLE}(run_id, group_id, project_id, graph_exec_id, token_id, held_at, "
                "updated_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
                [run_id, row.get("ai_group_id"), project_id, row.get("graph_exec_id"), row.get("ai_token_id"), ts, ts])
        elif existing.get("resumed_at"):
            store._execute(f"UPDATE {HOLD_TABLE} SET resumed_at = NULL, held_at = ?, updated_at = ? WHERE run_id = ?",
                           [ts, ts, run_id])
        _try_fence_hold(svc, run_id)
    held = held_run_ids()
    if held:
        logger.warning("[ai_invoke] startup holds recovery of %d run(s) with a live remote Job", len(held))
    return held


def _try_fence_hold(svc, run_id: str) -> bool:
    store = _store()
    try:
        ok = _fence_run(svc, run_id)
        error = None if ok else "token_revoke_failed"
    except Exception as exc:
        ok, error = False, type(exc).__name__
    ts = _stamp()
    if ok:
        store._execute(f"UPDATE {HOLD_TABLE} SET token_revoked_at = COALESCE(token_revoked_at, ?), "
                       "last_error = NULL, updated_at = ? WHERE run_id = ?", [ts, ts, run_id])
    else:
        store._execute(f"UPDATE {HOLD_TABLE} SET last_error = ?, updated_at = ? WHERE run_id = ?",
                       [error, ts, run_id])
    return ok


def _graph_lease_deadline() -> str:
    from modules.flow_gate.db.group_ai_leases import ACTIVE_HEARTBEAT_TTL_SEC
    return (datetime.now(timezone.utc) + timedelta(seconds=ACTIVE_HEARTBEAT_TTL_SEC)).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _heartbeat_hold(hold: dict) -> None:
    """Keep a held run's leases from the expiry-based reclaim (§3-4 step 3)."""
    from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
    run_id = hold["run_id"]
    lease = db_group_ai_leases.get_by_run_id(run_id)
    if lease:
        db_group_ai_leases.heartbeat(lease["group_id"], run_id)
    store = _store()
    if getattr(store, "table_exists", None) is None or store.table_exists("graph_attempt_runs"):
        store._execute(
            "UPDATE graph_attempt_runs SET lease_expires_at = ? WHERE ai_run_id = ? AND status = 'running'",
            [_graph_lease_deadline(), run_id])


def _resume_hold(svc, hold: dict) -> bool:
    """One held run: heartbeat, retry revocation, and resume its recovery once settled."""
    run_id = hold["run_id"]
    try:
        _heartbeat_hold(hold)
    except Exception:
        logger.warning("ai-invoke %s: held lease heartbeat failed", run_id, exc_info=True)
    if not hold.get("token_revoked_at"):
        _try_fence_hold(svc, run_id)
    if svc.unsettled_ai_jobs(ai_run_id=run_id):
        return False
    from . import admission
    exec_rows = admission.resume_remote_held_recovery(run_id)
    ts = _stamp()
    _store()._execute(f"UPDATE {HOLD_TABLE} SET resumed_at = ?, updated_at = ? WHERE run_id = ? "
                      "AND resumed_at IS NULL", [ts, ts, run_id])
    _continue_graph(exec_rows)
    logger.info("ai-invoke %s: remote Job settled; held restart recovery resumed", run_id)
    return True


def _continue_graph(exec_rows: list[dict]) -> None:
    """The Graph half of the restart recovery for executions that were held."""
    if not exec_rows:
        return
    try:
        from flowgate.graph_run import execution, stop
    except Exception:
        logger.warning("Graph recovery modules unavailable for held executions", exc_info=True)
        return
    for row in exec_rows:
        try:
            execution.recover_execution(row["exec_id"])
        except Exception:
            logger.warning("Graph execution recovery failed for held %s", row.get("exec_id"), exc_info=True)
        try:
            stop.on_execution_finished(row.get("group_id"), row.get("run_id"))
        except Exception:
            logger.warning("Graph stop continuation failed for held %s", row.get("exec_id"), exc_info=True)


# ── in-process runs whose watch ended before their Job settled ────────────────

def _live_owner(owner) -> bool:
    from modules.flow_gate.services.agent_jobs import PROCESS_OWNER_PREFIX
    owner = str(owner or "")
    prefix = PROCESS_OWNER_PREFIX + ":"
    if not owner.startswith(prefix):
        return False
    run = _svc().get_run_record(owner[len(prefix):])
    return run is not None and run.get("status") != "finished"


def _settle_in_process(svc) -> None:
    from . import terminal
    with _svc()._runs_lock:
        runs = list(_svc()._runs.values())
    for run in runs:
        if not run.get("remote_job_active") or run.get("_remote_watch_live"):
            continue
        run_id = run.get("run_id")
        try:
            rows = svc.unsettled_ai_jobs(ai_run_id=run_id)
        except Exception:
            logger.warning("ai-invoke %s: remote Job state unreadable; run kept alive", run_id, exc_info=True)
            continue
        if rows:
            for row in rows:
                if row["status"] in ("NEW", "CLAIMED", "STARTED"):
                    # The executor gave this Job up; it must still stop (idempotent).
                    try:
                        svc.cancel_ai_job(row["job_id"], row["attempt_id"])
                    except Exception:
                        logger.warning("ai-invoke %s: remote Job cancel retry failed", run_id, exc_info=True)
                else:
                    fence_job(svc, row)
            _keep_leases(run)
            continue
        run["remote_job_active"] = False
        if run.get("lease_cleanup_pending") and run.get("status") != "finished":
            # The worker already reached its terminal boundary and parked the leases.
            terminal.cleanup(run, reason="remote_job_settled")


def _keep_leases(run: dict) -> None:
    from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
    from modules.flow_gate.services import graph_execution_hooks
    try:
        if run.get("group_id") and not run.get("graph_exec_id"):
            db_group_ai_leases.heartbeat(run["group_id"], run["run_id"])
        if run.get("graph_exec_id"):
            graph_execution_hooks.call("heartbeat", str(run["run_id"]))
    except Exception:
        logger.warning("ai-invoke %s: lease heartbeat of a waiting remote run failed", run.get("run_id"),
                       exc_info=True)


# ── the periodic tick ────────────────────────────────────────────────────────

# The previous pass's eligible, unclaimed rows. No time/death inference is made here.
_graph_refill_candidates: dict[str, tuple] = {}


def _recover_graph() -> None:
    """Single-worker deployment (FLOWGATE_WORKERS=1): reuse Graph recovery/refill.

    Expiry only selects candidates; _recover_snapshot alone confirms termination
    and fences loss. Stopping rows continue exclusively through Stop. An unclaimed
    request must have identical state on two consecutive ticks before refill.
    """
    global _graph_refill_candidates
    from flowgate.graph_run import execution, execution_store as ledger, result_merge, scheduling
    store = _store()
    if not store.table_exists("graph_attempt_runs"):
        _graph_refill_candidates = {}
        return
    rows = store._fetch_all(
        "SELECT e.*, r.group_id, r.status AS run_status, r.last_event_seq "
        "FROM graph_attempt_runs e JOIN graph_runs r ON r.run_id = e.run_id "
        "WHERE (e.status = 'running' AND e.lease_expires_at <= ?) "
        "OR (e.status = 'requested' AND r.status IN ('running', 'paused')) "
        "ORDER BY e.created_at, e.exec_id", [ledger.now()])
    previous, _graph_refill_candidates = _graph_refill_candidates, {}
    refilled: set[str] = set()
    for row in rows:
        try:
            if is_held(row.get("ai_run_id")):
                continue
            if row["status"] == "running":
                _continue_graph([row])
            elif row.get("ai_run_id"):
                if scheduling.starting(row["exec_id"], row["ai_run_id"]):
                    continue    # flowgate.v02gui.0006 CH: admitted, its worktree still being prepared; not ended
                live = _svc().get_run_record(row["ai_run_id"])
                if not live or live.get("status") == "finished":
                    execution.recover_execution(row["exec_id"])
            elif row["run_status"] == "running" and not result_merge.holding(row["run_id"]):
                slots = scheduling.available(row["run_id"], row["group_id"])
                if slots > 0:
                    state = (dict(row), slots)
                    _graph_refill_candidates[row["exec_id"]] = state
                    if previous.get(row["exec_id"]) == state and row["group_id"] not in refilled:
                        refilled.add(row["group_id"])
                        execution.settle(row["group_id"])
        except Exception:
            logger.warning("Graph runtime continuation failed for %s", row.get("exec_id"), exc_info=True)


_unowned_sweep_ticks = 0
_unowned_request_candidates: dict[str, tuple] = {}


def _sweep_unowned() -> None:
    global _unowned_sweep_ticks, _unowned_request_candidates
    _unowned_sweep_ticks += 1
    if _unowned_sweep_ticks % REVIEW_LOOP_SWEEP_EVERY_TICKS:
        return
    previous, _unowned_request_candidates = _unowned_request_candidates, {}
    try:
        store = _store()
        if store is None:
            return
        loops_available = store.table_exists("ai_invoke_document_review_loops")
        graph_available = store.table_exists("graph_attempt_runs")
    except Exception:
        return  # DB-less processes have no recovery work.
    try:
        if loops_available:
            _svc().sweep_unowned_review_loops()
    except Exception:
        logger.warning("unowned review-loop supervisor sweep failed", exc_info=True)
    try:
        if graph_available:
            from flowgate.graph_run import execution
            def confirm(row):
                state = tuple(row.get(key) for key in
                              ("status", "ai_run_id", "lease_token", "created_at", "attempt_id"))
                _unowned_request_candidates[row["exec_id"]] = state
                return previous.get(row["exec_id"]) == state
            execution._close_unowned_requests(
                started_before=datetime.now(timezone.utc) - timedelta(seconds=REVIEW_LOOP_ORPHAN_TTL_SEC),
                run_statuses=("running",), reason="stalled_unowned_request", confirm=confirm)
    except Exception:
        logger.warning("unowned Graph request supervisor sweep failed", exc_info=True)


def tick() -> None:
    """One supervisor pass. Each step is independent: one failing never skips the others."""
    with _tick_lock:
        steps = []
        if available():
            svc = _jobs()
            steps.extend((
                ("advance", lambda: svc.advance_ai_jobs()),
                ("sweep", lambda: svc.sweep_orphaned_ai_jobs(_live_owner)),
                ("fence", lambda: [fence_job(svc, row) for row in svc.unfenced_expired_ai_jobs()]),
                ("in_process", lambda: _settle_in_process(svc)),
                ("holds", lambda: [_resume_hold_safely(svc, hold) for hold in _hold_rows()]),
                ("graph", _recover_graph),
            ))
        steps.append(("unowned_sweep", _sweep_unowned))
        for name, step in steps:
            try:
                step()
            except Exception:
                logger.warning("ai-invoke remote supervisor step %s failed", name, exc_info=True)


def _resume_hold_safely(svc, hold: dict) -> None:
    try:
        _resume_hold(svc, hold)
    except Exception:
        logger.warning("ai-invoke %s: held restart recovery could not resume yet", hold.get("run_id"),
                       exc_info=True)


def _loop() -> None:
    while not _stop.wait(SUPERVISOR_INTERVAL_SEC):
        try:
            tick()
        except Exception:
            logger.warning("ai-invoke remote supervisor tick failed", exc_info=True)


def start() -> bool:
    """Start the daemon supervisor once per process (bootstrap)."""
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="ai-remote-job-supervisor", daemon=True)
        _thread.start()
        return True


def stop_supervisor(timeout: float = 5.0) -> None:
    """Stop the daemon supervisor (application shutdown)."""
    global _thread
    _stop.set()
    with _thread_lock:
        thread, _thread = _thread, None
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)
