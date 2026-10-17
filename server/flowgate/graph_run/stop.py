"""0061 T#7 Stop: end a run's work and leave it idle (stopped) — ``POST /runs/{run_id}/stop``.

Pause (``POST /runs/{run_id}/pause``, T#2) keeps the run's work going; Stop ends it, in this order:

(a) one Flow guard transaction, under the exclusive hold of the Flow's Stop gate (``fencing.exclusive``: every
    attempt token side effect in flight - a source write, a document registration or verdict, a chat command's
    creation or spawn - completes first, and none starts until the commit): the run becomes ``stopping``, every
    ``requested`` execution ``cancelled`` and every ``running`` one gets ``cancel_requested_at``. From this commit
    on the fence (``fencing``) refuses every attempt token write, result, finalize hand-off and review report of the
    run, and ``acquire`` refuses a new execution (``execution_store.eligible``: running / paused only). The Flow
    stays locked (``editing.LOCKED``).
(b) outside every guard: the running executions' AI writing / review / rework processes are cancelled through the
    existing kill path (``ai_invoke_service.cancel_run``: the process tree kill), and their end is awaited for the
    existing kill path's own wait (``KILL_WAIT_SEC``).
(c) only once every process is confirmed ended, in one guard: the executions are ``cancelled``, the active attempts
    ``invalidated`` (``user_stop``) and their tokens revoked through the existing reclaim path;
(d) in the same guard the open / review nodes that are not done go back to ``pending``; outside it the worktrees of
    the attempts this Stop invalidated are removed (``workspace.cleanup``: Git locks are never taken inside a Flow
    guard);
(e) then, only once every one of those worktrees is confirmed removed, in a last guard, the run becomes ``idle`` with
    the reason ``stopped`` and the Flow is unlocked. Done nodes keep their state.

A process whose end is not confirmed within the wait keeps the run ``stopping`` (a ``stop_pending`` run event names
the reason) and nothing of (c)~(e) happens; the fence goes on refusing. So does a worktree whose removal failed (Git
busy and the like): the run stays ``stopping`` and locked (``stop_pending``, ``worktree_cleanup_pending``) and its
removal is retried. T#9: the run's merge sessions (``graph_attempt_merge``) are aborted before (c) (the abort under the
group's G, ``result_merge.abort_for_stop``); while one of them is still open (G busy, the Git abort failed) nothing of
(c)~(e) happens — no attempt is invalidated, no token revoked, no worktree removed, no node reset — and the run stays
stopping (``stop_pending``, ``merge_session_abort_pending``) until a repeated Stop or the server start aborts it. An
attempt whose merge is in progress (``integrating``) is neither invalidated nor reset — it goes on to its end (done
stays done) and the run stays stopping until then; its end goes on with the Stop (``result_merge``).
The Stop goes on when that execution's own end arrives (``on_execution_finished``), on a repeated
Stop, after Git locks are released (``result_merge.on_lock_released``), and at server start (``recover``);
each of them retries the removal for the attempts already invalidated. A root-bound node (its root
document's state, no attempt, no token) is not a run's work and keeps its state. The next step / full command opens
the pending nodes with new attempts; nothing of the invalidated ones is reused (their tokens are revoked and are no
evidence for the result binder any more, ``run_store.token_ids``).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from modules.flow_gate.db import groups as db_groups, ai_invoke_runs
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from flowgate.graph_run import execution_store as ledger, workspace, fencing, result_merge

log = logging.getLogger(__name__)

STOPPING, STOPPED, IDLE = "stopping", "stopped", "idle"
CAUSE_STOP, CAUSE_PENDING = "user_stop", "stop_pending"
INVALIDATED_REASON = "user_stop"
PENDING_REASON = "process_exit_unconfirmed"
CLEANUP_PENDING_REASON = "worktree_cleanup_pending"
SESSION_PENDING_REASON = "merge_session_abort_pending"      # T#9: a graph_attempt_merge session is still open
# The existing kill path's own waits: the process tree kill (taskkill, 10 s) and the reap after it (5 s).
KILL_WAIT_SEC = 15
POLL_SEC = 0.2
RESET_STATUSES = ("open", "review")


def _context(run: dict, actor: Optional[str]):
    from modules.flow_gate.graph_workflow.services import run_apply
    return run_apply.load_context(run, (db_groups.get_by_id(run["group_id"]) or {}).get("project_id"), actor)


def stop_run(group: dict, run_id: str, reason: Optional[str], user_id: str) -> dict:
    """``run_state_ports.stop``. A running or paused run stops; a stopping one goes on with its stop (a repeated Stop);
    any other is 409 ``run_not_running``. Answers the run as it is: idle (stopped), or still stopping with the
    executions whose end was not confirmed (``unconfirmed_exec_ids``), the stopped attempts whose worktree is not
    removed yet (``cleanup_pending_attempt_ids``) or, T#9, the attempts whose merge session is not aborted yet
    (``merge_session_pending_attempt_ids``)."""
    flow_id = group["group_id"]
    launchers: list[str] = []

    def begin() -> None:
        run = run_store.run_by_id(run_id)
        if run is None or run["group_id"] != flow_id:
            raise GraphApiError(404, "run_not_found", {"run_id": run_id})
        if run["status"] == STOPPING:
            return
        if run["status"] not in ("running", "paused"):
            raise GraphApiError(409, "run_not_running", {"run_id": run_id, "status": run["status"]})
        now = run_store.utc_now_ms()
        if run_store._affected("UPDATE graph_runs SET status = 'stopping', stopped_at = ?, updated_at = ? "
                               "WHERE run_id = ? AND status IN ('running', 'paused', 'stopped')",
                               [now, now, run_id]) != 1:
            raise AssertionError("stop missed its run")
        launchers.extend(r["ai_run_id"] for r in run_store._all(
            "SELECT ai_run_id FROM graph_attempt_runs WHERE run_id = ? AND status = 'requested' "
            "AND ai_run_id IS NOT NULL", [run_id]))
        stamp = ledger.now()
        run_store._affected("UPDATE graph_attempt_runs SET status = 'cancelled', ended_at = ? "
                            "WHERE run_id = ? AND status = 'requested'", [stamp, run_id])
        run_store._affected("UPDATE graph_attempt_runs SET cancel_requested_at = ?, "
                            "lease_touch_seq = lease_touch_seq + 1 "
                            "WHERE run_id = ? AND status = 'running' AND cancel_requested_at IS NULL",
                            [stamp, run_id])
        from modules.flow_gate.graph_workflow.services import run_apply
        run_apply.emit_run_status(_context(run, user_id), run["status"], STOPPING, CAUSE_STOP, reason)

    with fencing.exclusive(flow_id):
        with_flow_guard(flow_id, begin)
    _kill(run_id, launchers)
    deadline = time.monotonic() + KILL_WAIT_SEC
    while unconfirmed(run_id) and time.monotonic() < deadline:
        time.sleep(POLL_SEC)
    pending, cleanup_pending = advance(flow_id, run_id, actor=user_id, record_pending=True)
    from modules.flow_gate.graph_workflow.services.run_service import run_payload
    return {"run": run_payload(run_id, with_nodes=False), "unconfirmed_exec_ids": pending,
            "cleanup_pending_attempt_ids": cleanup_pending,
            "merge_session_pending_attempt_ids": result_merge.session_pending(run_id)}


def _kill(run_id: str, launchers=()) -> None:
    """(b) the existing process tree kill of every running execution, and of a launcher still waiting to start."""
    from modules.flow_gate.services import ai_invoke_service
    running = [r["ai_run_id"] for r in run_store._all(
        "SELECT ai_run_id FROM graph_attempt_runs WHERE run_id = ? AND status = 'running' AND ai_run_id IS NOT NULL",
        [run_id])]
    for ai_run_id in dict.fromkeys([*running, *launchers]):
        try:
            ai_invoke_service.cancel_run(ai_run_id)
        except Exception as exc:  # noqa: BLE001 — an AI run this process no longer has (404) is no failure
            if getattr(exc, "status_code", None) != 404:
                log.warning("Graph stop could not cancel AI run %s", ai_run_id, exc_info=True)


def _process_ended(row: dict) -> bool:
    """The execution's AI process is confirmed ended: its run finished, or its own Graph end (after the provider
    stopped) was taken; with no live run in this process, its durable end record (the T#4 recovery's rule)."""
    from modules.flow_gate.services import ai_invoke_service
    ai_run_id = row.get("ai_run_id")
    if not ai_run_id:
        return False
    live = ai_invoke_service.get_run_record(ai_run_id)
    if live is None:
        return ai_invoke_runs.get(ai_run_id) is not None
    if live.get("status") == "finished":
        return True
    return live.get("graph_exec_id") == row["exec_id"] and bool(live.get("graph_execution_finished"))


def unconfirmed(run_id: str) -> list[str]:
    """The running executions of the run whose process end is not confirmed yet."""
    rows = run_store._all("SELECT exec_id, ai_run_id FROM graph_attempt_runs WHERE run_id = ? AND status = 'running' "
                          "ORDER BY created_at, exec_id", [run_id])
    return [r["exec_id"] for r in rows if not _process_ended(r)]


def _revoke(token_ids) -> None:
    from modules.flow_gate.services import ai_invoke_service
    for token_id in token_ids:
        ai_invoke_service._reclaim_orphan_lease_token({"token_id": token_id}, INVALIDATED_REASON)


def _settle_executions(run: dict, actor: Optional[str]) -> list[str]:
    """(c) and the node half of (d), inside the guard; the run stays stopping. Returns the run's invalidated
    attempts (their worktrees go next)."""
    from modules.flow_gate.graph_workflow.services import run_apply
    run_id, stamp = run["run_id"], ledger.now()
    run_store._affected("UPDATE graph_attempt_runs SET status = 'cancelled', ended_at = ? "
                        "WHERE run_id = ? AND status IN ('requested', 'running')", [stamp, run_id])
    # T#9: a merge in progress goes on to its end (``result_merge``); its attempt and node are left to it, and the
    # Stop's idle (stopped) waits for it (``finish_stop``: an active attempt).
    merging = {r["node_key"] for r in result_merge.integrating(run_id)}
    active = [r["attempt_id"] for r in run_store._all(
        "SELECT attempt_id FROM graph_node_attempts WHERE run_id = ? AND status = 'active' "
        "AND COALESCE(integration_status, 'none') <> 'integrating'", [run_id])]
    run_store._affected("UPDATE graph_node_attempts SET status = 'invalidated', active_node_key = NULL, "
                        "invalidated_reason = ?, ended_at = ? WHERE run_id = ? AND status = 'active' "
                        "AND COALESCE(integration_status, 'none') <> 'integrating'",
                        [INVALIDATED_REASON, stamp, run_id])
    for attempt_id in active:
        _revoke(r["token_id"] for r in run_store._all(
            "SELECT token_id FROM graph_run_token_map WHERE attempt_id = ?", [attempt_id]))
    ctx = _context(run, actor)
    for row in run_store.node_runs(run_id):
        if row["status"] not in RESET_STATUSES or int(row.get("root_bound") or 0) or row["node_key"] in merging:
            continue
        moved = run_store._affected(
            "UPDATE graph_node_runs SET status = 'pending', result_doc_id = NULL, opened_at = NULL, "
            "finished_at = NULL, dispatch_token_issued = NULL, dispatch_issued_to = NULL, "
            "dispatch_mention_id = NULL, dispatch_error_code = NULL, stage = NULL, "
            "instruction_doc_id = NULL, result_event_floor = NULL, updated_at = ? "
            "WHERE run_id = ? AND node_key = ? AND status = ?",
            [run_store.utc_now_ms(), run_id, row["node_key"], row["status"]])
        if moved:
            # A node opened before migration 151 has tokens of no attempt: they are revoked with it.
            _revoke(r["token_id"] for r in run_store._all(
                "SELECT token_id FROM graph_run_token_map WHERE run_id = ? AND node_key = ? AND attempt_id IS NULL",
                [run_id, row["node_key"]]))
            run_apply.emit_node_state(ctx, row["node_key"], row["status"], "pending", CAUSE_STOP, None)
    return stopped_attempts(run_id)


def stopped_attempts(run_id: str) -> list[str]:
    """The run's attempts a Stop invalidated: their worktrees must be gone before the run is idle (stopped)."""
    return [r["attempt_id"] for r in run_store._all(
        "SELECT attempt_id FROM graph_node_attempts WHERE run_id = ? AND status = 'invalidated' "
        "AND invalidated_reason = ? ORDER BY created_at, attempt_id", [run_id, INVALIDATED_REASON])]


@result_merge.defer_lock_retries()
def advance(flow_id: str, run_id: str, *, actor: Optional[str] = None,
            record_pending: bool = False) -> tuple[list[str], list[str]]:
    """(c)~(e) for a stopping run once every process is confirmed ended. Returns (the executions whose end is still
    unconfirmed, the stopped attempts whose worktree is not removed yet); with either non-empty the run stays
    stopping and locked (``record_pending`` records why). Idempotent: every step re-reads the run inside its guard,
    a run that is no longer stopping is left alone, and a repeated call retries the removal of the worktrees of the
    attempts already invalidated."""
    from modules.flow_gate.graph_workflow.services import run_apply
    held: dict = {}
    run = run_store.run_by_id(run_id)
    if run is not None and run["status"] == STOPPING:
        # T#9: the run's merge sessions, before its attempts; one still open holds the whole of (c)~(e).
        left = result_merge.abort_for_stop(flow_id, run_id)
        if left:
            log.warning("Graph stop waits for merge session aborts run=%s attempts=%s", run_id, left)
            if record_pending:
                def note_sessions() -> None:
                    run = run_store.run_by_id(run_id)
                    if run is not None and run["status"] == STOPPING:
                        run_apply.emit_run_status(_context(run, actor), STOPPING, STOPPING, CAUSE_PENDING,
                                                  SESSION_PENDING_REASON)
                with_flow_guard(flow_id, note_sessions)
            return list(unconfirmed(run_id)), []

    def settle_executions() -> None:
        run = run_store.run_by_id(run_id)
        if run is None or run["status"] != STOPPING:
            return
        if result_merge.session_pending(run_id):
            held["sessions"] = True             # T#9: a merge session opened or kept since: nothing settles yet
            return
        pending = unconfirmed(run_id)
        if pending:
            held["pending"] = pending
            if record_pending:
                run_apply.emit_run_status(_context(run, actor), STOPPING, STOPPING, CAUSE_PENDING, PENDING_REASON)
            return
        held["attempts"] = _settle_executions(run, actor)

    with_flow_guard(flow_id, settle_executions)
    if held.get("sessions"):
        log.warning("Graph stop waits for merge session aborts run=%s", run_id)
        return list(unconfirmed(run_id)), []
    if "attempts" not in held:
        if held.get("pending"):
            log.warning("Graph stop waits for unconfirmed process ends run=%s executions=%s", run_id, held["pending"])
        return list(held.get("pending") or []), []
    deferred = [a for a in held["attempts"] if not workspace.cleanup(a)]
    if deferred:
        # (d) is not complete: the run stays stopping (fenced, locked) and says why; the removal is retried by the
        # next advance (the execution's end, a repeated Stop, the start-up recovery).
        log.warning("Graph stop waits for attempt worktree removal run=%s attempts=%s", run_id, deferred)
        if record_pending:
            def note() -> None:
                run = run_store.run_by_id(run_id)
                if run is not None and run["status"] == STOPPING:
                    run_apply.emit_run_status(_context(run, actor), STOPPING, STOPPING, CAUSE_PENDING,
                                              CLEANUP_PENDING_REASON)
            with_flow_guard(flow_id, note)
        return [], deferred

    def finish_stop() -> None:
        run = run_store.run_by_id(run_id)
        if run is None or run["status"] != STOPPING:
            return
        if run_store.has_active_attempts(run_id) or ledger.working(run_id) or result_merge.session_pending(run_id):
            return                              # never idle with work; the next advance settles it first
        now = run_store.utc_now_ms()
        if run_store._affected("UPDATE graph_runs SET status = 'idle', idle_reason = ?, finished_at = ?, "
                               "updated_at = ? WHERE run_id = ? AND status = 'stopping'",
                               [STOPPED, now, now, run_id]) != 1:
            raise AssertionError("stop missed its run")
        ctx = _context(run, actor)
        ctx.run["idle_reason"] = STOPPED
        run_apply.emit_run_status(ctx, STOPPING, IDLE, CAUSE_STOP, None)

    with_flow_guard(flow_id, finish_stop)
    return [], []


def on_execution_finished(flow_id: str, run_id: str) -> None:
    """An execution's own end arrived (its provider stopped): a Stop waiting for it goes on. Never raises."""
    try:
        run = run_store.run_by_id(run_id)
        if run is not None and run["status"] == STOPPING:
            advance(flow_id, run_id)
    except Exception:  # noqa: BLE001
        log.exception("Graph stop could not go on after an execution ended run=%s", run_id)


def recover() -> None:
    """Server start, before the execution recovery: every stopping run goes on with its stop."""
    if not run_store.available():
        return
    for row in run_store._all("SELECT run_id, group_id FROM graph_runs WHERE status = 'stopping'", []):
        try:
            advance(row["group_id"], row["run_id"], record_pending=True)
        except Exception:  # noqa: BLE001
            log.exception("Graph stop recovery failed for %s", row["run_id"])
