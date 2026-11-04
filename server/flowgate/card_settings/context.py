"""0126 T#4: settings-only current Graph context, independent of structure editability."""
from modules.flow_gate.graph_workflow.store import definition_store, run_store
from modules.flow_gate.graph_workflow.services import engine_router
from . import legacy_adapter as legacy

KEYS = {"run_id", "graph_revision_no", "draft_version"}


def parse(value):
    if not isinstance(value, dict) or set(value) != KEYS:
        return False
    if value["run_id"] is not None and not isinstance(value["run_id"], str):
        return False
    return all(v is None or (type(v) is int and v >= 0)
               for v in (value["graph_revision_no"], value["draft_version"]))


def current(flow_id, node_key):
    run = run_store.current_run(flow_id)
    revision = run["revision_no"] if run else None
    definition = definition_store.revision(flow_id, revision) if run else definition_store.draft(flow_id)
    if definition is None and run is None:
        revision = definition_store.latest_revision_no(flow_id)
        definition = definition_store.revision(flow_id, revision) if revision else None
    node = next((n for n in (definition or {}).get("nodes", []) if n["node_key"] == node_key), None)
    return {"run_id": run["run_id"] if run else None, "graph_revision_no": revision,
            "draft_version": definition.get("draft_version") if definition and revision is None else None}, node


def require_owner(flow_id):
    legacy.require_writable(flow_id)
    engine_router.require_graph_editable(flow_id, engine_router.read_ownership(flow_id))


def require_current(flow_id, node_key, expected):
    context, node = current(flow_id, node_key)
    if node is None or (expected is not None and context != expected):
        raise legacy.GraphApiError(409, "gate_context_changed", {
            "flow_id": flow_id, "node_key": node_key, "reason": "settings_context", "context": context})
    return context, node
