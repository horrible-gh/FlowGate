"""0083 WP-2: recover one idle, unfinished card; never broaden Stop to idle.

The exclusive token fence precedes the Flow transaction, as in execution.recover.
Only this card is reset. Its documents, executions and worktree remain history;
the next explicit command allocates a fresh attempt through normal dispatch.
"""
from __future__ import annotations

from flowgate.graph_run import fencing, stop, settle
from flowgate.graph_time_machine import operation


def _live_job(run_id: str, node_key: str) -> bool:
    # Also check runtime records when a finished ledger row or an expired legacy lease
    # would otherwise conceal a process that has not stopped yet.
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.services.ai_invoke.terminal import provider_stopped
    rows = run_store._all(
        "SELECT t.ai_run_id FROM graph_run_token_map m JOIN tokens t ON t.token_id = m.token_id "
        "WHERE m.run_id = ? AND m.node_key = ? AND t.ai_run_id IS NOT NULL "
        "UNION SELECT e.ai_run_id FROM graph_attempt_runs e JOIN graph_node_attempts a "
        "ON a.attempt_id = e.attempt_id WHERE e.run_id = ? AND a.node_key = ? AND e.ai_run_id IS NOT NULL",
        [run_id, node_key, run_id, node_key])
    for row in rows:
        live = ai_invoke_service.get_run_record(row["ai_run_id"])
        if live and (live.get("status") != "finished" or not provider_stopped(live)):
            return True
    return False


def recover_card(group: dict, run_id: str, node_key: str, reason, user_id: str) -> dict:
    # Function-level imports keep modules.flow_gate out of this module's top level (dependency direction).
    from modules.flow_gate.graph_workflow.services import card_settings_ports, result_binder, run_apply
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store
    flow_id = group["group_id"]
    answer = {}

    def refuse(why):
        raise GraphApiError(409, "card_recovery_refused", {"run_id": run_id, "node_key": node_key, "reason": why})

    def body():
        run = run_store.run_by_id(run_id)
        if not run or run["group_id"] != flow_id:
            raise GraphApiError(404, "run_not_found", {"run_id": run_id})
        from modules.flow_gate.graph_workflow.services.graph_service import require_writable
        require_writable(flow_id)
        operation.require_no_operation(flow_id)
        if run["status"] != "idle" or run.get("idle_reason") == "stopped":
            refuse("run_not_idle" if run["status"] != "idle" else "run_stopped")
        current = run_store.active_run(flow_id)
        if not current or current["run_id"] != run_id:
            refuse("run_not_active")
        node = next((n for n in run_store.node_runs(run_id) if n["node_key"] == node_key), None)
        if not node:
            raise GraphApiError(404, "node_not_found", {"node_key": node_key})
        if node["status"] != "open" or node.get("root_bound"):
            refuse("node_not_open" if node["status"] != "open" else "root_bound")
        attempt_id = run_store.active_attempt_id(run_id, node_key)
        if not attempt_id:
            refuse("attempt_not_active")
        attempt = run_store._one("SELECT * FROM graph_node_attempts WHERE attempt_id = ?", [attempt_id])
        if node.get("result_doc_id") or attempt.get("result_doc_id") or attempt.get("result_commit"):
            refuse("result_exists")
        if attempt.get("integration_status") not in (None, "none"):
            refuse("integration_pending")
        if run_store._one("SELECT 1 AS present FROM graph_attempt_runs WHERE attempt_id = ? "
                          "AND status IN ('requested', 'running') LIMIT 1", [attempt_id]):
            refuse("execution_active")
        if card_settings_ports.ai_working(flow_id, node_key, None) or _live_job(run_id, node_key):
            refuse("ai_running")
        ctx = run_apply.load_context(run, group.get("project_id"), user_id)
        # A stored registration with a lost contact must bind normally, not become history.
        if result_binder.next_missing_event(ctx, run_apply.read_run_state(ctx), node_key) is not None:
            refuse("result_exists")
        if not stop.invalidate_attempt(attempt_id, "card_recovery"):
            refuse("attempt_not_active")
        if not stop.reset_node(ctx, node, "card_recovery"):
            raise AssertionError("card recovery missed its node")
        settle.settle_run(ctx)  # idle stays idle: recovery never dispatches work
        run_apply.emit_run_status(ctx, "idle", "idle", "card_recovery", reason)
        answer.update(node_key=node_key, attempt_id=attempt_id)

    with fencing.exclusive(flow_id):
        with_flow_guard(flow_id, body)
    from modules.flow_gate.graph_workflow.services.run_service import run_payload
    return {"run": run_payload(run_id, with_nodes=True), "recovery": answer}
