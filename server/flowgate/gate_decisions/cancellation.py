"""Cancel an undecided gate and stop its run without publishing approval work.

The Stop fence and Flow transaction atomically record the cancellation and enter
stopping. Approval uses the same Flow guard, so only one can win. Stop owns process
termination, attempt revocation, pending reset and unlock; completed results survive.
The stored kind remains confirm (the existing schema's gate intent kind), but the
record is born canceled, never accepted/succeeded and never emits gate_confirmed.
"""
from flowgate.gate_decisions import history, intake, legacy_adapter as legacy, preflight, repository as repo
from flowgate.graph_run import stop


class _Replay(Exception):
    def __init__(self, row):
        self.row = row


def cancel(group: dict, run_id: str, node_key: str, user_id: str, body: dict) -> dict:
    fp = intake.fingerprint("cancel_before_decision", None, body)
    stored = None

    def recheck():
        nonlocal stored
        found = repo.by_request(run_id, node_key, user_id, body["request_id"])
        if found is not None:
            if found["request_fingerprint"] != fp:
                raise legacy.GraphApiError(409, intake.REQUEST_CONFLICT, {"decision_id": found["decision_id"]})
            raise _Replay(found)
        run = legacy.run_store.run_by_id(run_id)
        if run is None or run["group_id"] != group["group_id"]:
            raise legacy.GraphApiError(404, "run_not_found", {"run_id": run_id})
        if run["status"] not in ("running", "idle", "paused"):
            raise legacy.GraphApiError(409, "run_not_running", {"run_id": run_id})
        own = legacy.read_ownership(group["group_id"])
        if own.state != "graph":
            raise legacy.ownership_error(group["group_id"], own)
        ctx = legacy.load_context(run, group.get("project_id"), user_id, [])
        row = ctx.node_rows.get(node_key)
        if row is None or row.get("node_type") != "gate":
            raise legacy.GraphApiError(409, "node_not_gate", {"node_key": node_key})
        node = legacy.read_run_state(ctx).node(node_key)
        if node is None or node.status != "open":
            raise legacy.GraphApiError(409, "node_not_open", {"node_key": node_key})
        attempt = intake._attempt_of(run_id, node_key)
        held = repo.reservation(run_id, node_key) or repo.valid_for_attempt(attempt)
        if held:
            raise legacy.GraphApiError(409, intake.IN_PROGRESS, {"decision_id": held["decision_id"]})
        intake._require_unblocked(group["group_id"], node_key)
        shown, version = preflight.settings_of(group["group_id"], node_key)
        if body["expected_attempt_id"] != attempt or body["expected_settings_version"] != version:
            raise legacy.GraphApiError(409, intake.CONTEXT_CHANGED, {"node_key": node_key})
        stored = repo.insert_decision({
            "group_id": group["group_id"], "run_id": run_id, "node_key": node_key,
            "attempt_id": attempt, "revision_no": int(run["revision_no"]),
            "actor_user_id": user_id, "request_id": body["request_id"], "request_fingerprint": fp,
            "decision": repo.CONFIRM, "status": repo.CANCELED, "settings_version": version,
            "settings": {"shown": shown, "canceled_before_decision": True},
        })
        from modules.flow_gate.graph_workflow.services.run_apply import emit_node_state
        emit_node_state(ctx, node_key, node.status, node.status, "gate_decision_canceled", None,
                        note={"actor_user_id": user_id, "payload": history.decision_link(stored["decision_id"])})

    try:
        stop.stop_run(group, run_id, "gate_decision_canceled", user_id, allow_idle=True, recheck=recheck)
    except _Replay as replay:
        stored = replay.row
        # A lost response may have left process/workspace cleanup pending. This never
        # stops a later command: advance acts only on a run still in stopping.
        stop.advance(group["group_id"], run_id, actor=user_id)
    return intake._answer(run_id, stored)
