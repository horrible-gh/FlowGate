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
from flowgate.graph_run import execution_store as ledger

log = logging.getLogger(__name__)


def register():
    graph_execution_hooks.register(lambda action, *args, **kwargs: _ACTIONS[action](*args, **kwargs))


def request_write(job):
    # Called inside the transaction opening the node or advancing its instruction stage.
    attempt = run_store.active_attempt_id(job["run_id"], job["node_key"])
    if not attempt:
        raise ledger.LeaseLost("write has no active attempt")
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


def _request_document(flow_id, doc_id, kind, payload, existing=None):
    attempt = _attempt_for_document(flow_id, doc_id)
    if not attempt:
        if existing:
            raise ledger.LeaseLost("recovered attempt is no longer active")
        return None
    def body():
        attempt = _attempt_for_document(flow_id, doc_id)
        if not attempt or attempt["status"] != "active":
            raise ledger.LeaseLost("document has no active Graph attempt")
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
    return with_flow_guard(flow_id, body)


def request_hop(flow_id, bundle, gate, kind):
    clean = {k: v for k, v in bundle.items() if k != "_graph_exec_id"}
    return _request_document(flow_id, gate["slot"]["doc_id"], kind,
                             {"type": "hop", "flow_id": flow_id, "bundle": clean, "gate": gate, "kind": kind},
                             bundle.get("_graph_exec_id"))


def request_loop(project_id, flow_id, doc_id, issued_to, loop, basis, node_key, existing=None):
    from modules.flow_gate.services.ai_invoke import review
    baseline = review.compute_review_baseline(doc_id)
    kind = "rework" if baseline.get("starts_with_rework") else "review"
    return _request_document(flow_id, doc_id, kind, {
        "type": "loop", "project_id": project_id, "flow_id": flow_id, "doc_id": doc_id,
        "issued_to": issued_to, "loop": {**loop, **baseline, "current_stage": kind},
        "basis": basis, "node_key": node_key}, existing)


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
    if not row or row["run_id"] != job["run_id"] or row["status"] not in ("requested", "running") \
            or row["attempt_id"] != run_store.active_attempt_id(job["run_id"], job["node_key"]):
        raise ledger.LeaseLost("Graph write no longer belongs to the open attempt")


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
            if run.get("graph_lease_token"):
                ledger.renew(exec_id, run["graph_lease_token"])
            else:
                run["graph_lease_token"] = ledger.acquire(exec_id, run["run_id"])
            return True
        except ledger.LeaseLost:
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
    if token:
        status = "cancelled" if run["cancel_event"].is_set() else "ended"
        ledger.finish(row["exec_id"], token, status)
    elif row["status"] == "requested":
        ledger.cancel(row["exec_id"], None, "requested")
    else:
        raise ledger.LeaseLost("unowned execution")
    return True


def finish(run):
    if not run.get("graph_exec_id"):
        return
    with _lock(run):
        if not run.get("graph_execution_finished"):
            run["graph_execution_finished"] = _finish_owned(run)


def transition(run, loop):
    if not run.get("graph_exec_id"):
        return
    with _lock(run):
        if run.get("graph_lease_lost") or run["cancel_event"].is_set():
            raise ledger.LeaseLost("cancelled execution cannot advance a review loop")
        def body():
            row = ledger.get(run["graph_exec_id"])
            if not row or row["status"] != "running":
                raise ledger.LeaseLost("review execution is no longer running")
            if row["kind"] == loop["current_stage"] and loop.get("last_hop_outcome") == "failed":
                return None  # A no-output/provider retry renews the same execution.
            payload = json.loads(row["request_json"])
            _finish_owned(run)
            payload["loop"] = dict(loop)
            payload["review_run_ids"] = sorted(set(payload.get("review_run_ids") or []) | {run["run_id"]})
            return ledger.request(row["attempt_id"], row["run_id"], loop["current_stage"], payload)
        successor = with_flow_guard(run["group_id"], body)
        # Publish the successor only after both ledger writes have committed.
        if successor:
            run["graph_exec_id"] = successor
            run.pop("graph_lease_token", None)
            run.pop("graph_execution_finished", None)


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
    if not exec_id:
        return False
    if isinstance(error, ledger.LeaseLost):
        ledger.cancel_ineligible(exec_id)
        return True
    detail = getattr(error, "detail", None)
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
    rows = run_store._all("SELECT e.*, r.group_id FROM graph_attempt_runs e JOIN graph_runs r "
                         "ON r.run_id = e.run_id WHERE e.status IN ('requested', 'running') "
                         "ORDER BY e.created_at, e.exec_id", [])
    for snapshot in rows:
        jobs = []
        def body():
            row = ledger.get(snapshot["exec_id"])
            if not row or row["status"] not in ("requested", "running"):
                return
            if row["status"] == "requested":
                if ledger.eligible(row):
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
            attempt = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id = ? AND status = 'active'",
                                     [row["attempt_id"]])
            if not attempt or not current or current["status"] not in ("requested", "running"):
                raise ledger.LeaseLost("attempt invalidated before token mapping")
            payload = json.loads(current["request_json"])
            doc_id = (payload.get("gate", {}).get("slot", {}).get("doc_id") or payload.get("doc_id"))
            node = next(n for n in run_store.node_runs(row["run_id"]) if n["node_key"] == attempt["node_key"])
            stage = "instruction" if doc_id and doc_id == node.get("instruction_doc_id") else "result"
            if not run_store.node_run_by_token(token_id):
                run_store.map_token(token_id, row["run_id"], attempt["node_key"], stage)
        with_flow_guard(run["group_id"], body)
    except Exception:
        token_service.revoke(token_id, reason="graph_attempt_not_active")
        raise


def settle(flow_id):
    from flowgate.card_settings.legacy_adapter import settle_graph_run
    settle_graph_run(flow_id)


_ACTIONS = {
    "map_token": map_token, "validate_write": validate_write, "loop_state": loop_state,
    "settle": settle, "request_write": request_write, "request_hop": request_hop, "request_loop": request_loop,
    "launch": launch, "finish": finish, "transition": transition, "heartbeat": heartbeat,
    "working": ledger.working, "start_failed": start_failed,
}
