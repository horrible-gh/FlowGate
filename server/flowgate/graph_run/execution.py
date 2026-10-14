"""Durable start intent and process fencing for Graph AI executions (0061 T#4)."""
from __future__ import annotations

import json
import logging
import threading

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.db import groups as db_groups, ai_invoke_runs
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.services import graph_execution_hooks
from flowgate.graph_run import execution_store as ledger, workspace, scheduling, fencing, stop, result_merge, hold

log = logging.getLogger(__name__)


def register():
    graph_execution_hooks.register(lambda action, *args, **kwargs: _ACTIONS[action](*args, **kwargs))
    result_merge.install()      # T#9: every session close of a group reaches its Graph integrations
    from modules.flow_gate.services import graph_source_hooks
    graph_source_hooks.register(workspace.source_root, workspace.mutation, fencing.require_token)


def request_write(job):
    # Called inside the transaction opening the node or advancing its instruction stage.
    attempt = run_store.active_attempt_id(job["run_id"], job["node_key"])
    if not attempt:
        raise ledger.LeaseLost("write has no active attempt")
    # T#7: a stopping run requests nothing (inside the guard). The engine's own opening of a node (a person's approval
    # on an idle run opens the next one) is no attempt token's write: idle opens, the acquire needs running / paused.
    fencing.check(attempt, "request_write", statuses=fencing.OPENING)
    job["graph_attempt_id"] = attempt
    payload = dict(job)
    payload.pop("graph_exec_id", None)
    # A dispatch token's raw bearer is never persisted in the execution journal.
    payload["token"] = {"token_id": (job.get("token") or {}).get("token_id")}
    old = run_store._one("SELECT * FROM graph_attempt_runs WHERE attempt_id = ? "
                         "AND status IN ('requested', 'running')", [attempt])
    if old:
        prior = json.loads(old["request_json"])
        # An already written instruction skips its process, then hands off to the result.
        if old["status"] == "requested" and prior.get("job", {}).get("stage") == "instruction" \
                and job.get("stage") == "result":
            ledger.cancel(old["exec_id"], None, "requested")
        else:
            raise ledger.LeaseLost("attempt already has an execution")
    job["graph_exec_id"] = ledger.request(attempt, job["run_id"], "write", {"type": "write", "job": payload})
    run = run_store.run_by_id(job["run_id"])
    if run and run["status"] == "idle":
        from modules.flow_gate.graph_workflow.services import run_apply
        run_apply.settle_run(run_apply.load_context(run, job.get("project_id"), None))
    return job["graph_exec_id"]


def _attempt_for_document(flow_id, doc_id):
    if not get_store().table_exists("graph_attempt_runs"):
        return None
    return run_store._one(
        "SELECT a.* FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
        "JOIN graph_node_runs n ON n.run_id = a.run_id AND n.node_key = a.node_key "
        "WHERE r.group_id = ? AND (n.result_doc_id = ? OR n.instruction_doc_id = ?) "
        "ORDER BY CASE WHEN a.status = 'active' THEN 0 ELSE 1 END, a.created_at DESC, a.attempt_id DESC LIMIT 1",
        [flow_id, doc_id, doc_id])


def _request_document(flow_id, doc_id, kind, payload, existing=None, attempt_id=None):
    attempt = _attempt_for_document(flow_id, doc_id)
    if not attempt:
        if existing:
            raise ledger.LeaseLost("recovered attempt is no longer active")
        return None
    operation = "hop_" + kind
    # T#7: a review / rework hop of an attempt that may not write any more (its run not running or paused, the attempt
    # not active) is refused (and recorded) before its guard, and judged again inside it (a refusal there is recorded
    # after the rollback, ``fencing.guarded``). Q439: the hop of a person's explicit review request is also taken
    # on an idle run (and takes it back to running below); an automatic hop is not. ``attempt_id``: the attempt the
    # run-end stage asking for the hop was made for — a node opened again since is not its to review.
    statuses = fencing.IDLE_EXCEPTED if graph_execution_hooks.explicit() and not existing else fencing.WRITABLE
    if attempt_id and attempt["attempt_id"] != attempt_id:
        raise fencing.refuse(attempt_id, operation, "attempt_superseded", active_attempt_id=attempt["attempt_id"])
    fencing.require(attempt["attempt_id"], operation, statuses=statuses)
    def body():
        attempt = _attempt_for_document(flow_id, doc_id)
        if not attempt:
            raise ledger.LeaseLost("document has no Graph attempt")
        if attempt_id and attempt["attempt_id"] != attempt_id:
            raise fencing.refuse(attempt_id, operation, "attempt_superseded", active_attempt_id=attempt["attempt_id"])
        fencing.check(attempt["attempt_id"], operation, statuses=statuses)
        if existing:
            row = ledger.get(existing)
            if not row or row["attempt_id"] != attempt["attempt_id"] or row["status"] != "requested":
                raise ledger.LeaseLost("execution is no longer requested")
            return existing
        result = ledger.request(attempt["attempt_id"], attempt["run_id"], kind, payload)
        # An explicit review may start while its card was idle awaiting a person.
        run = run_store.run_by_id(attempt["run_id"])
        if run and run["status"] == "idle":
            from modules.flow_gate.graph_workflow.services import run_apply
            ctx = run_apply.load_context(run, (db_groups.get_by_id(flow_id) or {}).get("project_id"), None)
            run_apply.settle_run(ctx)
        return result
    return fencing.guarded(flow_id, body)


def request_hop(flow_id, bundle, gate, kind):
    clean = {k: v for k, v in bundle.items() if k != "_graph_exec_id"}
    return _request_document(flow_id, gate["slot"]["doc_id"], kind,
                             {"type": "hop", "flow_id": flow_id, "bundle": clean, "gate": gate, "kind": kind},
                             bundle.get("_graph_exec_id"))


def request_loop(project_id, flow_id, doc_id, issued_to, loop, basis, node_key, existing=None, attempt_id=None):
    from modules.flow_gate.services.ai_invoke import review
    baseline = review.compute_review_baseline(doc_id)
    kind = "rework" if baseline.get("starts_with_rework") else "review"
    return _request_document(flow_id, doc_id, kind, {
        "type": "loop", "project_id": project_id, "flow_id": flow_id, "doc_id": doc_id,
        "issued_to": issued_to, "loop": {**loop, **baseline, "current_stage": kind},
        "basis": basis, "node_key": node_key}, existing, attempt_id)


def loop_state(exec_id):
    """Internal replay data, never taken from an HTTP caller's loop settings."""
    if not exec_id:
        return None
    row = ledger.get(exec_id)
    if not row or row["status"] != "requested":
        raise ledger.LeaseLost("review loop request is no longer available")
    payload = json.loads(row["request_json"])
    if payload.get("type") != "loop":
        return None
    return {"loop": dict(payload["loop"]), "review_run_ids": list(payload.get("review_run_ids") or [])}


def validate_write(job):
    exec_id = job.get("graph_exec_id")
    if not exec_id:
        raise ledger.LeaseLost("Graph write has no durable execution")
    row = ledger.get(exec_id)
    if not row or row["run_id"] != job["run_id"]:
        raise ledger.LeaseLost("Graph write no longer belongs to the open attempt")
    # T#7: no token for a stopping run's write, nor for an execution or attempt that no longer owns the node. Inside
    # the mapping guard: the refusal is recorded once it rolled back (``start_failed``).
    if row["status"] not in ("requested", "running"):
        raise fencing.refuse(row["attempt_id"], "validate_write", "execution_" + str(row["status"]), exec_id=exec_id)
    active = run_store.active_attempt_id(job["run_id"], job["node_key"])
    if row["attempt_id"] != active:
        raise fencing.refuse(row["attempt_id"], "validate_write", "attempt_superseded", exec_id=exec_id,
                             active_attempt_id=active)
    fencing.check(row["attempt_id"], "validate_write")


def _lock(run):
    # Short lifecycle writes only; never hold this while running or killing a provider.
    return run.setdefault("_graph_execution_lock", threading.RLock())


def launch(run):
    exec_id = run.get("graph_exec_id")
    if not exec_id:
        return True
    with _lock(run):
        try:
            if run["cancel_event"].is_set() and not run.get("graph_lease_token"):
                try:
                    ledger.cancel(exec_id, None, "requested")
                except ledger.LeaseLost:
                    pass  # Cancellation of this launcher must not cancel an acquired winner.
            if run["cancel_event"].is_set() or run.get("graph_execution_finished"):
                raise ledger.LeaseLost("execution was cancelled or finished")
            proc = run.get("proc")
            if proc is not None and proc.poll() is None:
                raise ledger.LeaseLost("previous provider has not exited")
            workspace.verify_execution(exec_id)
            if run.get("graph_lease_token"):
                ledger.renew(exec_id, run["graph_lease_token"])
            else:
                # Capacity waits keep the durable request and hold no Flow/DB lock.
                while not run["cancel_event"].is_set():
                    token = scheduling.acquire(exec_id, run["run_id"])
                    if token:
                        run["graph_lease_token"] = token
                        break
                    run["cancel_event"].wait(0.1)
                if not run.get("graph_lease_token"):
                    raise ledger.LeaseLost("execution cancelled while waiting for capacity")
                workspace.verify_execution(exec_id)
            return True
        except (ledger.LeaseLost, workspace.port.GraphSourceError):
            run["graph_lease_lost"] = True
            run["cancel_event"].set()
            ledger.cancel_ineligible(exec_id)
            return False


def _finish_owned(run):
    token = run.get("graph_lease_token")
    # A losing launcher never touches the winner's row or card.
    if run.get("graph_lease_lost") and not token:
        return False
    from modules.flow_gate.services.ai_invoke.terminal import provider_stopped
    if not provider_stopped(run):
        raise RuntimeError("cannot finish a live Graph process")
    row = ledger.get(run["graph_exec_id"])
    if not row:
        raise ledger.LeaseLost("execution disappeared")
    if token and row["status"] in ("cancelled", "lost") and row.get("lease_token") == token:
        # T#7: a Stop (or the recovery) settled this execution after its process ended; this late end changes
        # neither a node nor the run candidates.
        run["graph_report_late"] = True
        return True
    if token:
        status = "cancelled" if run["cancel_event"].is_set() else "ended"
        ledger.finish(row["exec_id"], token, status)
    elif row["status"] == "requested":
        ledger.cancel(row["exec_id"], None, "requested")
    else:
        raise ledger.LeaseLost("unowned execution")
    return True


def finish(run):
    """The execution's ledger end (``terminal.cleanup``). T#7: only an end owned to its close — the launcher held the
    lease (or cancelled its own request), the ledger took the end, the attempt is not invalidated, the run is not
    stopping and no Stop / recovery settled the execution first — is marked ``graph_end_owned``; the finalize hands
    nothing else on and settles nothing after it (``finalize._finalize_run``). An owned end the finalize hands on to
    its run-end stage (``graph_run_end_pending``) keeps the run working until that stage is over (``handoff_end``);
    any other owned end settles here, as before."""
    if not run.get("graph_exec_id"):
        return
    with _lock(run):
        if not run.get("graph_execution_finished"):
            run["graph_execution_finished"] = _finish_owned(run)
    if not run.get("graph_execution_finished") or run.get("graph_end_judged"):
        return
    run["graph_end_judged"] = True
    row = ledger.get(run["graph_exec_id"]) or {}
    graph = run_store.run_by_id(row["run_id"]) if row.get("run_id") else None
    if graph is not None and graph["status"] == stop.STOPPING:
        run["graph_settle_skipped"] = True
        stop.on_execution_finished(run["group_id"], graph["run_id"])     # T#7: a Stop waiting for this end
        return
    attempt = fencing.attempt_row(row["attempt_id"]) if row.get("attempt_id") else None
    if run.get("graph_report_late") or (attempt and attempt["attempt_status"] == "invalidated"):
        # T#7: an invalidated attempt's or a settled execution's end settles nothing — nor does the finalize's last
        # settle after it (``graph_settle_skipped``).
        run["graph_settle_skipped"] = True
        return
    run["graph_end_owned"] = True
    if run.get("graph_run_end_pending") and graph is not None:
        ledger.begin_handoff(run["graph_exec_id"], graph["run_id"])      # T#7: settled after the run-end stage
        return
    settle(run["group_id"])


def handoff_end(run):
    """T#7, the finalize after its run-end stage (whatever happened there): the run is no longer kept working by this
    execution's hand-off. The finalize's own settle follows for an owned end."""
    if run.get("graph_exec_id"):
        ledger.end_handoff(run["graph_exec_id"])


def node_hold(run_id, node_key, attempt_id, operation):
    """``graph_execution_hooks`` ``node_hold``: ``fencing.node_hold``."""
    return fencing.node_hold(run_id, node_key, attempt_id, operation)


def transition(run, loop):
    if not run.get("graph_exec_id"):
        return
    with _lock(run):
        if run.get("graph_lease_lost") or run["cancel_event"].is_set():
            raise ledger.LeaseLost("cancelled execution cannot advance a review loop")
        current = ledger.get(run["graph_exec_id"])
        if current:
            # T#7: a review verdict reaches its next hop only while the run and attempt may take it (recorded here).
            fencing.require(current["attempt_id"], "review_transition", statuses=fencing.REPORTING)
        def body():
            row = ledger.get(run["graph_exec_id"])
            if not row:
                raise ledger.LeaseLost("review execution disappeared")
            # T#7: judged again inside the guard; a refusal is recorded once the guard rolled back (``guarded``).
            if row["status"] != "running":
                raise fencing.refuse(row["attempt_id"], "review_transition", "execution_" + str(row["status"]),
                                     exec_id=row["exec_id"])
            fencing.check(row["attempt_id"], "review_transition", statuses=fencing.REPORTING)
            if row.get("cancel_requested_at"):
                raise fencing.refuse(row["attempt_id"], "review_transition", "execution_cancel_requested",
                                     exec_id=row["exec_id"])
            if row["lease_token"] != run.get("graph_lease_token"):
                raise fencing.refuse(row["attempt_id"], "review_transition", "lease_token_stale",
                                     exec_id=row["exec_id"])
            if row["kind"] == loop["current_stage"] and loop.get("last_hop_outcome") == "failed":
                return None  # A no-output/provider retry renews the same execution.
            payload = json.loads(row["request_json"])
            _finish_owned(run)
            payload["loop"] = dict(loop)
            payload["review_run_ids"] = sorted(set(payload.get("review_run_ids") or []) | {run["run_id"]})
            successor = ledger.request(row["attempt_id"], row["run_id"], loop["current_stage"], payload)
            run_store._affected("UPDATE graph_attempt_runs SET ai_run_id = ? WHERE exec_id = ?",
                                [run["run_id"], successor])
            return successor
        successor = fencing.guarded(run["group_id"], body)
        # Publish the successor only after both ledger writes have committed.
        if successor:
            run["graph_exec_id"] = successor
            run.pop("graph_lease_token", None)
            run.pop("graph_execution_finished", None)
    if successor:
        settle(run["group_id"])


def heartbeat(ai_run_id):
    from modules.flow_gate.services import ai_invoke_service
    run = ai_invoke_service.get_run_record(ai_run_id)
    if not run or not run.get("graph_exec_id"):
        return
    try:
        with _lock(run):
            if not run.get("graph_lease_token") or run.get("graph_execution_finished"):
                return
            ledger.renew(run["graph_exec_id"], run["graph_lease_token"])
    except ledger.LeaseLost:
        run["graph_lease_lost"] = True
        ai_invoke_service.cancel_run(ai_run_id)
        raise


def start_failed(exec_id, error=None):
    if error is not None:
        fencing.recorded(error)          # T#7: a refusal judged inside the start's guard, recorded after its rollback
    if not exec_id:
        return False
    if isinstance(error, graph_execution_hooks.ExecutionLeaseLost):   # T#7: a fence refusal too
        ledger.cancel_ineligible(exec_id)
        return True
    detail = getattr(error, "detail", None)
    if isinstance(detail, dict) and detail.get("reason") == "worktree_failed":
        return True  # Workspace preparation already invalidated/reset the node.
    if isinstance(detail, dict) and detail.get("code") == "run_in_progress":
        return True  # Admission is busy; keep the durable request, including duplicate launches.
    row = ledger.get(exec_id)
    if not row or row["status"] != "requested":
        return True  # A duplicate admission is not a node failure.
    try:
        ledger.cancel(exec_id, None, "requested")
    except ledger.LeaseLost:
        return True  # Another launcher acquired between the read and the cancellation CAS.
    return False


def replay(row):
    payload = json.loads(row["request_json"])
    if payload["type"] == "write":
        from modules.flow_gate.graph_workflow.services.node_dispatch import start_ai
        start_ai({**payload["job"], "graph_exec_id": row["exec_id"]})
    elif payload["type"] == "hop":
        from modules.flow_gate.services.ai_invoke import review
        spawn = review._spawn_review_hop if row["kind"] == "review" else review._spawn_rework_hop
        spawn(payload["flow_id"], {**payload["bundle"], "_graph_exec_id": row["exec_id"]}, payload["gate"])
    elif payload["type"] == "loop":
        from flowgate.card_settings.legacy_adapter import start_review_loop
        start_review_loop(payload["project_id"], payload["flow_id"], payload["doc_id"], payload["issued_to"],
                          payload["loop"], lambda _reason: payload["basis"], payload["node_key"],
                          graph_exec_id=row["exec_id"])
    else:
        raise ValueError("unknown execution request type")


def recover():
    """Once after orphan AI cleanup and Graph port registration. Never infer death from TTL alone."""
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.graph_workflow.services import run_apply
    if not get_store().table_exists("graph_attempt_runs"):
        return
    result_merge.recover()  # T#9: an integrating merge is finished or pending again, before a Stop goes on
    stop.recover()          # T#7: a stopping run goes on with its stop, never with a replay or a lease loss
    rows = run_store._all("SELECT e.*, r.group_id FROM graph_attempt_runs e JOIN graph_runs r "
                         "ON r.run_id = e.run_id WHERE e.status IN ('requested', 'running') "
                         "AND r.status <> 'stopping' ORDER BY e.created_at, e.exec_id", [])
    for snapshot in rows:
        jobs = []
        def body():
            row = ledger.get(snapshot["exec_id"])
            if not row or row["status"] not in ("requested", "running"):
                return
            if row["status"] == "requested":
                if ledger.eligible(row):
                    live = ai_invoke_service.get_run_record(row.get("ai_run_id")) if row.get("ai_run_id") else None
                    if live and live.get("status") != "finished":
                        return
                    run_store._affected("UPDATE graph_attempt_runs SET ai_run_id = NULL WHERE exec_id = ? "
                                        "AND status = 'requested'", [row["exec_id"]])
                    jobs.append(lambda: replay(row))
                else:
                    ledger.cancel(row["exec_id"], None, "requested")
                return
            if not row.get("lease_expires_at") or row["lease_expires_at"] > ledger.now():
                return
            live = ai_invoke_service.get_run_record(row.get("ai_run_id"))
            ended = ai_invoke_runs.get(row.get("ai_run_id")) if row.get("ai_run_id") else None
            from modules.flow_gate.services.ai_invoke.terminal import provider_stopped
            if (live and (live.get("status") != "finished" or not provider_stopped(live))) or not ended:
                log.warning("Graph recovery has no confirmed AI termination: %s", row["exec_id"])
                return
            ledger.mark_lost(row["exec_id"], row["lease_token"])
            attempt = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id = ?", [row["attempt_id"]])
            changed = run_store._affected(
                "UPDATE graph_node_attempts SET status = 'invalidated', active_node_key = NULL, "
                "invalidated_reason = 'lease_expired', ended_at = ? WHERE attempt_id = ? AND status = 'active'",
                [ledger.now(), row["attempt_id"]])
            if not changed:
                return
            for token in run_store._all("SELECT token_id FROM graph_run_token_map WHERE attempt_id = ?", [row["attempt_id"]]):
                ai_invoke_service._reclaim_orphan_lease_token(token, "lease_expired")
            run = run_store.run_by_id(row["run_id"])
            node = next((n for n in run_store.node_runs(row["run_id"]) if n["node_key"] == attempt["node_key"]), None)
            if not node or node["status"] == "done":
                return
            run_store._affected(
                "UPDATE graph_node_runs SET status = 'pending', result_doc_id = NULL, opened_at = NULL, "
                "finished_at = NULL, dispatch_token_issued = NULL, dispatch_issued_to = NULL, "
                "dispatch_mention_id = NULL, dispatch_error_code = NULL, stage = NULL, "
                "instruction_doc_id = NULL, result_event_floor = NULL, updated_at = ? "
                "WHERE run_id = ? AND node_key = ? AND status <> 'done'",
                [ledger.now(), row["run_id"], attempt["node_key"]])
            ctx = run_apply.load_context(run, (db_groups.get_by_id(snapshot["group_id"]) or {}).get("project_id"), None, jobs)
            run_apply.emit_node_state(ctx, attempt["node_key"], node["status"], "pending", "lease_expired", None)
            run_apply.settle_run(ctx)  # T#2 command candidates preserve the step target set.
        try:
            # T#7: a lease-expired attempt is invalidated under the Stop gate's exclusive hold, as a Stop and a failed
            # worktree are (no side effect of its token runs across the commit).
            with fencing.exclusive(snapshot["group_id"], own_ok=True):
                with_flow_guard(snapshot["group_id"], body)
        except Exception:
            log.exception("Graph execution recovery failed for %s", snapshot["exec_id"])
            continue  # A failed transaction never dispatches its jobs.
        for job in jobs:
            try:
                job()
            except Exception as exc:
                log.exception("Graph execution replay failed for %s", snapshot["exec_id"])
                try:
                    start_failed(snapshot["exec_id"], exc)
                except Exception:
                    log.exception("Graph replay failure could not be recorded for %s", snapshot["exec_id"])

    # Outside all Flow guards and DB transactions. T#10: invalidated and integrated (ended) attempts' trees.
    workspace.cleanup_finished()


def map_token(exec_id, token_id):
    if not exec_id:
        return
    from modules.flow_gate.services import token_service
    try:
        row = ledger.get(exec_id)
        if not row:
            raise ledger.LeaseLost("execution disappeared before token mapping")
        run = run_store.run_by_id(row["run_id"])
        def body():
            current = ledger.get(exec_id)
            if not current or current["status"] not in ("requested", "running"):
                raise fencing.refuse(row["attempt_id"], "map_token",
                                     "execution_" + str((current or {}).get("status") or "missing"), exec_id=exec_id)
            # T#7: a stopping run or an invalidated attempt maps no new token (recorded after the rollback).
            fencing.check(row["attempt_id"], "map_token")
            attempt = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id = ? AND status = 'active'",
                                     [row["attempt_id"]])
            payload = json.loads(current["request_json"])
            doc_id = (payload.get("gate", {}).get("slot", {}).get("doc_id") or payload.get("doc_id"))
            node = next(n for n in run_store.node_runs(row["run_id"]) if n["node_key"] == attempt["node_key"])
            stage = "instruction" if doc_id and doc_id == node.get("instruction_doc_id") else "result"
            if not run_store.node_run_by_token(token_id):
                run_store.map_token(token_id, row["run_id"], attempt["node_key"], stage)
        fencing.guarded(run["group_id"], body)
    except Exception:
        token_service.revoke(token_id, reason="graph_attempt_not_active")
        raise


def settle(flow_id):
    from flowgate.card_settings.legacy_adapter import settle_graph_run
    settle_graph_run(flow_id)


def result_refused(run, kind, payload):
    """T#7, inside the result binder's guard: a stopping run binds nothing, nor does a registration made with the
    token of an attempt that may not write any more. Recorded."""
    if fencing.run_refused(run, "result_" + kind, doc_id=payload.get("doc_id")):
        return True
    return kind == "inbox_registered" and fencing.token_refused(payload.get("token_id"), "result_" + kind)


_ACTIONS = {
    "admission": scheduling.admission, "admit": scheduling.admit, "legacy_acquire": scheduling.legacy_acquire,
    "execution_wire": scheduling.wire, "opening": scheduling.opening, "can_open": scheduling.can_open,
    "prepare_workspace": workspace.prepare,
    "map_token": map_token, "validate_write": validate_write, "loop_state": loop_state,
    "settle": settle, "request_write": request_write, "request_hop": request_hop, "request_loop": request_loop,
    "launch": launch, "finish": finish, "transition": transition, "heartbeat": heartbeat,
    "working": ledger.working, "start_failed": start_failed,
    "report_refused": fencing.report_refused, "node_refused": fencing.node_refused, "result_refused": result_refused,
    "execution_attempt": fencing.execution_attempt, "handoff_end": handoff_end, "node_hold": node_hold,
    # T#9: an approved code node is done once its result is merged into the Flow's branch.
    "defer_done": result_merge.defer_done, "integration_waiting": result_merge.integration_waiting,
    "integration_wire": result_merge.integration_wire, "retry_integrations": result_merge.retry_integrations,
    "hold_command": result_merge.hold_command, "propagation_held": result_merge.propagation_held,
    # T#11: the Flow's held node keys, never opened (``run_apply.read_run_state`` -> ``RunState.held``).
    "held_nodes": hold.held,
}
