"""Explicit, idempotent recovery shared by chat and other authorized controllers.

Transports supply a recheck of their caller; it runs again inside each mutation's
Flow guard. No scheduler, approval override, history deletion or whole-run retry.
"""
import json

from modules.flow_gate.graph_workflow.store import definition_store, run_store
from modules.flow_gate.graph_workflow.services import run_apply, run_service, node_dispatch, card_settings_ports
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.services.graph_service import require_writable
from . import fencing, stop, workspace, recovery_basis as basis, recovery_store as store


def refuse(reason, **params):
    raise GraphApiError(409, "recovery_refused", {"reason": reason, **params})


def parse(body):
    if not isinstance(body, dict) or set(body) - {"request_id", "expected_context"}:
        raise GraphApiError(422, "invalid_request", {"reason": "recovery_fields"})
    key = body.get("request_id")
    if not isinstance(key, str) or not key or len(key) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise GraphApiError(422, "invalid_request", {"field": "request_id"})
    if not isinstance(body.get("expected_context"), dict):
        raise GraphApiError(422, "invalid_request", {"field": "expected_context"})
    return key, body["expected_context"]


def prior(flow_id, request_id, fingerprint):
    row = store.request(flow_id, request_id)
    if row and row["fingerprint"] != fingerprint:
        raise GraphApiError(409, "recovery_request_conflict", {"request_id": request_id})
    return json.loads(row["response_json"]) if row else None


def fresh_result(group, node, user_id):
    """Allocate a new report slot, leaving the old document/revisions/reviews intact.

    Rebinding is projection-only. The old attempt and the reset event retain the
    old document ID; no supersede, approval or deletion is performed.
    """
    from flowgate.card_documents import repository, legacy_adapter, slots
    if node.get("node_type") != "document" or node.get("doc_type") not in ("T", "TS"):
        return
    for slot in slots.card_slots(node.get("node_type"), node.get("doc_type")):
        if slot.slot != slots.REPORT:
            continue
        rows = [r for r in repository.rows(group["group_id"], [node["node_key"]])
                if r["slot"] == slot.slot and r["doc_type"] == slot.doc_type]
        if len(rows) != 1:
            refuse("result_slot_missing", node_key=node["node_key"])
        doc_id = legacy_adapter.create_document(group, node, slot, user_id)
        if repository.retarget(group["group_id"], node["node_key"], slot.slot, slot.doc_type,
                               slot.doc_type, doc_id) != 1:
            raise RuntimeError("recovery result slot changed")
        run_store._affected("UPDATE graph_node_documents SET owned = 1 WHERE group_id = ? "
                            "AND node_key = ? AND slot = ? AND doc_type = ?",
                            [group["group_id"], node["node_key"], slot.slot, slot.doc_type])


def _run(group, expected_id):
    run = run_store.current_run(group["group_id"])
    if not run or run["run_id"] != expected_id:
        refuse("run_changed", expected_run_id=expected_id)
    return run


def retry(group, user_id, node_key, body, recheck):
    request_id, expected = parse(body)
    flow_id = group["group_id"]
    fingerprint = basis.digest(["retry", user_id, node_key, body])
    jobs = []
    def change():
        recheck()
        require_writable(flow_id)
        replay = prior(flow_id, request_id, fingerprint)
        if replay:
            return {**replay, "replayed": True}
        store.refuse_pending(flow_id)
        checked = basis.require_context(group, expected, node_key)
        run = _run(group, expected.get("run_id"))
        run_service._refuse_time_machine(flow_id)
        if run["status"] not in ("running", "paused", "idle"):
            refuse("run_" + run["status"])
        ctx = run_apply.load_context(run, group.get("project_id"), user_id, jobs)
        node = ctx.node_rows.get(node_key) or {}
        row = next((r for r in run_store.node_runs(run["run_id"]) if r["node_key"] == node_key), None)
        if node.get("node_type") != "document" or node.get("doc_type") not in ("T", "TS"):
            refuse("not_a_work_card", node_key=node_key)
        if not row or row["status"] not in ("open", "review") or row.get("root_bound"):
            refuse("card_not_retryable", node_key=node_key, status=(row or {}).get("status"))
        from . import hold
        if node_key in (hold.held(flow_id) or ()):
            refuse("card_held", node_key=node_key)
        aid = run_store.active_attempt_id(run["run_id"], node_key)
        if not aid:
            refuse("attempt_missing", node_key=node_key)
        if run_store._one("SELECT 1 AS present FROM graph_attempt_runs WHERE attempt_id = ? "
                          "AND status IN ('requested', 'running') LIMIT 1", [aid]) or \
                card_settings_ports.ai_working(flow_id, node_key, None):
            refuse("card_running", node_key=node_key, attempt_id=aid)
        attempt = workspace.attempt(aid) or {}
        if attempt.get("integration_status") in ("integrating", "pending"):
            refuse("integration_pending", attempt_id=aid)
        from . import result_merge
        if aid in result_merge.session_pending(run["run_id"]):
            refuse("merge_session_pending", attempt_id=aid)
        # The reset event retains the previous report ID alongside the previous attempt.
        if not stop.invalidate_attempt(aid, "explicit_retry") or not stop.reset_node(
                ctx, row, "explicit_retry", {"payload": {"previous_attempt_id": aid, "previous_result_doc_id": row.get("result_doc_id")}}):
            refuse("attempt_changed")
        fresh_result(group, node, user_id)
        new_id = run_store.open_attempt(run["run_id"], node_key, int(run["revision_no"]))
        store.pin(new_id, flow_id, checked["new_attempt"])
        if run["status"] != "running":
            run_store.set_run_status(run["run_id"], run["status"], "running")
            run_store.set_run_command(run["run_id"], "step", [node_key])
            run_apply.emit_run_status(ctx, run["status"], "running", "explicit_retry")
            ctx.run.update(status="running", run_mode="step", step_node_keys=[node_key])
        dispatch = node_dispatch.dispatch(ctx, node_key)
        if run_store.open_node_run(run["run_id"], node_key, dispatch) != 1:
            raise RuntimeError("recovery opening missed its node")
        run_apply.emit_node_state(ctx, node_key, "pending", "open", "opened", None, dispatch)
        result = {"flow_id": flow_id, "node_key": node_key, "request_id": request_id,
                  "run_id": run["run_id"], "created": True, "replayed": False,
                  "previous_attempt_id": aid, "new_attempt_id": new_id,
                  "status": "open", "selected_basis": checked["new_attempt"]}
        store.save(flow_id, request_id, fingerprint, "retry", run["run_id"], result)
        jobs.append(lambda: workspace.cleanup(aid))
        return result
    with fencing.exclusive(flow_id):
        result = with_flow_guard(flow_id, change)
    run_service.run_jobs(jobs)
    actual = workspace.attempt(result["new_attempt_id"]) or {}
    result["actual_basis"] = ({"available": True, "commit": actual["input_commit"]}
                              if actual.get("input_commit") else basis.unknown("not_prepared"))
    rows = run_store.node_runs(result["run_id"])
    row = next((r for r in rows if r["node_key"] == node_key), {})
    result["status"] = row.get("status", result["status"])
    result["error_code"] = row.get("dispatch_error_code")
    return result


def restart_answer(saved):
    if not saved.get("new_run_id"):
        return saved
    rows = run_store._all("SELECT attempt_id, node_key, input_commit, input_source FROM graph_node_attempts "
                          "WHERE run_id = ? ORDER BY node_key, created_at, attempt_id", [saved["new_run_id"]])
    inputs = [{"attempt_id": r["attempt_id"], "node_key": r["node_key"],
               **({"available": True, "commit": r["input_commit"], "source": r["input_source"]}
                  if r.get("input_commit") else basis.unknown("not_prepared"))} for r in rows]
    return {**saved, "run": run_service.run_payload(saved["new_run_id"], with_nodes=True),
            "attempt_bases": inputs, "actual_basis": basis.unknown("see_attempt_bases")}


class Restart:
    """Internal hooks of the existing start service, executed inside its transaction."""
    def __init__(self, group, user_id, request_id, expected, fingerprint, recheck):
        self.group, self.user_id, self.request_id = group, user_id, request_id
        self.expected, self.fingerprint, self.recheck = expected, fingerprint, recheck

    def before(self):
        self.recheck()
        flow_id = self.group["group_id"]
        saved = prior(flow_id, self.request_id, self.fingerprint)
        if saved and saved.get("new_run_id"):
            return {"run": run_service.run_payload(saved["new_run_id"], with_nodes=True)}
        old = _run(self.group, self.expected["run_id"])
        if old["status"] != "idle" or old.get("idle_reason") != "stopped":
            refuse("stop_not_complete", run_id=old["run_id"])
        from . import execution_store, result_merge
        from flowgate.gate_decisions import repository as gates
        if execution_store.working(old["run_id"]) or result_merge.session_pending(old["run_id"]) or \
                gates.reservations_of_run(old["run_id"]) or run_store._one(
                    "SELECT 1 AS present FROM graph_node_attempts WHERE run_id = ? AND status = 'active' LIMIT 1",
                    [old["run_id"]]):
            refuse("recovery_pending", run_id=old["run_id"])
        now = basis.view(self.group)["context"]
        for key in ("group_head", "latest_revision_no", "draft_version", "settings_versions"):
            if now[key] != self.expected[key]:
                raise GraphApiError(409, "recovery_context_changed", {"field": key, "current": now})
        # Failure later in start_run rolls this release back with the new run.
        if run_store._affected("UPDATE graph_runs SET active_group_id = NULL WHERE run_id = ? "
                               "AND active_group_id = ? AND status = 'idle' AND idle_reason = 'stopped'",
                               [old["run_id"], flow_id]) != 1:
            refuse("run_changed")
        return None

    def created(self, ctx):
        selected = self.expected["new_attempt"]
        store.pin(ctx.run_id, self.group["group_id"], selected)
        for node in ctx.node_rows.values():
            fresh_result(self.group, node, self.user_id)
        store.finish(self.group["group_id"], self.request_id,
                     {"flow_id": self.group["group_id"], "request_id": self.request_id,
                      "previous_run_id": self.expected["run_id"], "new_run_id": ctx.run_id,
                      "created": True, "pending": False, "stop_confirmed": True,
                      "selected_basis": selected})


def restart(group, user_id, body, recheck):
    request_id, expected = parse(body)
    flow_id = group["group_id"]
    fingerprint = basis.digest(["restart", user_id, body])
    def accept():
        recheck()
        require_writable(flow_id)
        saved = prior(flow_id, request_id, fingerprint)
        if saved:
            return {**saved, "replayed": True}
        store.refuse_pending(flow_id)
        basis.require_context(group, expected)
        run = _run(group, expected.get("run_id"))
        run_service._refuse_time_machine(flow_id)
        if definition_store.draft(flow_id) is not None:
            refuse("draft_not_confirmed")
        record = definition_store.revision(flow_id, definition_store.latest_revision_no(flow_id))
        # Every card is fresh in the new run; do not skip old protected projections.
        from flowgate.card_documents import lines, slots
        missing = [n["node_key"] for n in (record or {}).get("nodes", [])
                   if n.get("node_type") == "document" and n.get("doc_type") == "T"
                   and not lines.has_body((lines.line_document(flow_id, n["node_key"], slots.INSTRUCTION,
                       n.get("node_type"), n.get("doc_type")) or {}).get("document"))]
        if missing:
            raise GraphApiError(409, "chat_run_instruction_missing", {"node_keys": missing})
        result = {"flow_id": flow_id, "request_id": request_id, "previous_run_id": run["run_id"],
                  "new_run_id": None, "created": False, "pending": True, "stop_confirmed": False}
        store.save(flow_id, request_id, fingerprint, "restart", run["run_id"], result)
        return result
    with fencing.exclusive(flow_id):
        accepted = with_flow_guard(flow_id, accept)
    if accepted.get("new_run_id") or accepted.get("blocked"):
        return restart_answer(accepted)
    def proceed():
        old = _run(group, accepted["previous_run_id"])
        if not (old["status"] == "idle" and old.get("idle_reason") == "stopped"):
            def stop_check():
                recheck()
                _run(group, accepted["previous_run_id"])
            stopped = stop.stop_run(group, old["run_id"], "explicit_restart", user_id,
                                    allow_idle=True, recheck=stop_check)
            current = stopped.get("run") or {}
            if current.get("status") != "idle" or current.get("idle_reason") != "stopped":
                return {**accepted, "stop_status": current.get("status"),
                        **{k: v for k, v in stopped.items() if k != "run"}}
        hooks = Restart(group, user_id, request_id, expected, fingerprint, recheck)
        result = run_service.start_run(group, user_id, None, "full", None, recovery=hooks)
        saved = prior(flow_id, request_id, fingerprint)
        return restart_answer(saved)

    try:
        return proceed()
    except GraphApiError as exc:
        # A concurrent retransmission may have committed while this one stopped.
        def blocked():
            saved = prior(flow_id, request_id, fingerprint)
            if saved and saved.get("new_run_id"):
                return {**saved, "replayed": True}
            old = run_store.run_by_id(accepted["previous_run_id"])
            confirmed = bool(old and old["status"] == "idle" and old.get("idle_reason") == "stopped")
            if saved:
                store.finish(flow_id, request_id, {**saved, "pending": False, "blocked": True,
                             "stop_confirmed": confirmed, "error_code": exc.code, "error_params": exc.params})
            return None
        replay = with_flow_guard(flow_id, blocked)
        if replay:
            return restart_answer(replay)
        raise
