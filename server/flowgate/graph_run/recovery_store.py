"""Recovery records written in the same Flow transaction as the IDs they describe."""
import json
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services.errors import GraphApiError


def available():
    return run_store.get_store().table_exists("graph_recovery_requests")


def require_available():
    if not available():
        raise GraphApiError(409, "recovery_migration_required")


def request(flow_id, request_id):
    require_available()
    return run_store._one("SELECT * FROM graph_recovery_requests WHERE group_id = ? AND request_id = ?",
                          [flow_id, request_id])


def save(flow_id, request_id, fingerprint, operation, previous_run_id, response):
    run_store.get_store()._execute(
        "INSERT INTO graph_recovery_requests (group_id, request_id, fingerprint, operation, previous_run_id, "
        "response_json) VALUES (?, ?, ?, ?, ?, ?)",
        [flow_id, request_id, fingerprint, operation, previous_run_id, json.dumps(response)])


def finish(flow_id, request_id, response):
    run_store._affected("UPDATE graph_recovery_requests SET response_json = ? WHERE group_id = ? AND request_id = ?",
                        [json.dumps(response), flow_id, request_id])


def refuse_pending(flow_id, except_request=None):
    if not available():
        return
    for row in run_store._all("SELECT request_id, response_json FROM graph_recovery_requests "
                              "WHERE group_id = ? AND operation = 'restart'", [flow_id]):
        if row["request_id"] != except_request and json.loads(row["response_json"]).get("pending"):
            raise GraphApiError(409, "recovery_in_progress", {"request_id": row["request_id"]})


def pin(owner_id, group_id, value):
    run_store.get_store()._execute("INSERT INTO graph_recovery_inputs (owner_id, group_id, input_json) VALUES (?, ?, ?)",
                                 [owner_id, group_id, json.dumps(value)])


def input_of(owner_id):
    if not run_store.get_store().table_exists("graph_recovery_inputs"):
        return None
    row = run_store._one("SELECT input_json FROM graph_recovery_inputs WHERE owner_id = ?", [owner_id])
    return json.loads(row["input_json"]) if row else None


def workspace_input(row, root, proposed):
    """Inside workspace's Git locks, before creating a ref, marker or tree.

    Explicit retry pins its inspected input even when the old window used another
    base. Restart pins its new run's initial base. Neither path enters _join.
    """
    from . import workspace
    from modules.flow_gate.services import graph_source_hooks as port
    pinned = input_of(row["attempt_id"])
    run_pin = input_of(row["run_id"])
    if run_pin:
        window = workspace._base_window(row["run_id"])[0]
        event = run_store._one("SELECT cause FROM graph_run_events WHERE run_id = ? AND event_seq = ?",
                              [row["run_id"], window])
        if event and event["cause"] != "user_start":
            run_pin = None  # later explicit commands keep their ordinary new-window semantics
    if not pinned and not run_pin:
        return proposed
    if proposed.get("join_inputs"):
        raise port.GraphSourceError("graph_worktree_unavailable", reason="recovery_join_requires_merge")
    value = pinned or run_pin
    if workspace.git._rev_parse(root, value["commit"] + "^{commit}") != value["commit"]:
        raise port.GraphSourceError("graph_worktree_unavailable", reason="recovery_input_missing")
    if pinned:
        inputs = [list(i) for i in workspace._inputs(row)]
        if inputs != value["predecessors"]:
            raise port.GraphSourceError("graph_worktree_unavailable", reason="recovery_context_changed")
    if pinned or proposed.get("input_source") == "run_base":
        return {"input_commit": value["commit"], "input_source": "predecessors" if value.get("predecessors") else "run_base",
                "input_attempt_ids": value.get("input_attempt_ids", []),
                "base_since": proposed.get("base_since", 0)}
    return proposed
