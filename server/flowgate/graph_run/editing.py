"""0061 T#3: guarded editing and immutable executed graph regions.

T#14 (WP answer 2): while the Flow's Graph run is running, paused or stopping, a chat of the Flow (the group
conversation path, not an attempt's token) may not take an edit / edit-once source access either
(``chat_source_refusal``, 409 ``graph_run_locked``). Reading and sending messages stay open; the Canvas / Inspector
edit lock above is a separate rule."""
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.graph_workflow.store import definition_store, layout_store, run_store

LOCKED = frozenset({"running", "paused", "stopping"})
PROTECTED = frozenset({"done", "open", "review"})


def require_editable(flow_id: str, node_key=None) -> None:
    """Inside the Flow guard; node_key adds the always-protected card check. T#13: an unfinished Graph Time Machine
    operation refuses every edit (409 ``graph_time_machine_in_progress``)."""
    from flowgate.graph_time_machine.operation import require_no_operation
    require_no_operation(flow_id)
    run = run_store.current_run(flow_id)
    if not run:
        return
    params = {"flow_id": flow_id, "run_id": run["run_id"], "status": run["status"]}
    if run["status"] in LOCKED:
        raise GraphApiError(409, "graph_run_locked", params)
    if node_key is not None and any(r["node_key"] == node_key and r["status"] in PROTECTED
                                    for r in run_store.node_runs(run["run_id"])):
        raise GraphApiError(409, "graph_node_protected", {**params, "node_key": node_key})


def chat_source_refusal(group_id, locale=None) -> None:
    """T#14: a chat token of the group asks for edit / edit-once source access (``token_service.issue``, before the
    token is written or an edit-once claim is taken). Refused with 409 ``graph_run_locked`` (the ai-invoke / token
    route ``detail`` shape: ``code``, ``message``, ``flow_id``, ``run_id``, ``status``) while the Flow's run is locked."""
    run = run_store.active_run(group_id) if group_id else None
    if not run or run["status"] not in LOCKED:
        return
    from fastapi import HTTPException
    from modules.flow_gate.graph_workflow.api.graph_messages import CHAT_SOURCE_LOCKED
    message = CHAT_SOURCE_LOCKED.get(locale or "ko") or CHAT_SOURCE_LOCKED["ko"]
    raise HTTPException(status_code=409, detail={"code": "graph_run_locked", "message": message, "flow_id": group_id,
                                                 "run_id": run["run_id"], "status": run["status"]})


def settings_locked(flow_id: str, node_key: str) -> bool:
    try:
        require_editable(flow_id, node_key)
    except GraphApiError:
        return True
    return False


def protect_definition(flow_id: str, nodes: list, edges: list, run=None) -> None:
    """Compare against the execution revision, never the mutable draft/latest revision.

    Called before draft side effects as well as confirmation and revision adoption.
    Retired identities cannot reappear; retained pending nodes may freely change.
    """
    run = run if run is not None else run_store.current_run(flow_id)
    if not run:
        return
    before = definition_store.revision(flow_id, int(run["revision_no"]))
    if before is None:
        raise GraphApiError(409, "revision_not_found", {"flow_id": flow_id})
    old = {n["node_key"]: n for n in before["nodes"]}
    new = {n["node_key"]: n for n in nodes}
    protected = {r["node_key"] for r in run_store.node_runs(run["run_id"])
                 if r["status"] in PROTECTED}
    changed = {k for k in protected if old.get(k) != new.get(k) or k not in new}
    def incoming(items, key):
        return {e["edge_id"]: e for e in items if e["target"] == key}
    changed.update(k for k in protected if incoming(before["edges"], k) != incoming(edges, k))
    if changed:
        raise GraphApiError(409, "graph_node_protected", {"flow_id": flow_id,
                            "run_id": run["run_id"], "node_keys": sorted(changed)})
    reused = set(new) & layout_store.retired_keys(flow_id)
    if reused:
        raise GraphApiError(409, "graph_node_key_reused", {"flow_id": flow_id, "node_keys": sorted(reused)})
