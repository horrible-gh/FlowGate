"""Read-only reuse of Canvas/Run projections; no reconcile, identity save or job dispatch.

The user Canvas/current entrypoints perform reconciliation before reading. AI requests
use their existing pure projection functions inside a read snapshot instead.
"""
from modules.flow_gate.graph_workflow.services import canvas_service, run_service
from modules.flow_gate.graph_workflow.services.flow_guard import with_read_snapshot
from modules.flow_gate.graph_workflow.services.identity_keeper import IdentityResult, keys_from_record, sequence_tag_of
from modules.flow_gate.graph_workflow.projection.flow_snapshot import read_snapshot, protected_ids
from modules.flow_gate.graph_workflow.store import layout_store, run_store
from . import authz

NODE_RUN_FIELDS = ("node_key", "status", "opened_at", "finished_at", "root_bound", "stage",
                   "attempt_id", "hold", "question", "node_running", "node_running_cause", "wait_reason",
                   "integration_status", "integration_reason")
RUN_FIELDS = ("run_id", "flow_id", "status", "revision_no", "started_at", "stopped_at",
              "finished_at", "last_event_seq", "mode", "idle_reason")
NODE_FIELDS = ("node_key", "identity_source", "origin", "kind", "doc_type", "title", "state")
DOC_FIELDS = ("doc_id", "type_code", "seq", "title", "doc_review_status", "status", "revision_no")
LINE_FIELDS = ("slot", "doc_type", "doc_id", "revision_no", "status", "doc_review_status", "has_body", "body_state")


def scalar_fields(row, fields):
    return {k: row[k] for k in fields if k in row and (row[k] is None or isinstance(row[k], (str, int, float, bool)))}


def document(row, allowed):
    return scalar_fields(row, DOC_FIELDS) if row and row.get("doc_id") in allowed else None


def node_run(row, allowed):
    if not row:
        return None
    out = scalar_fields(row, NODE_RUN_FIELDS)
    for key in ("result_doc_id", "instruction_doc_id"):
        out[key] = row.get(key) if row.get(key) in allowed else None
    return out


def run_view(row, scope, allowed):
    if row is None:
        return None
    if scope.run_id and row["run_id"] != scope.run_id:
        authz.refuse()
    out = scalar_fields(row, RUN_FIELDS)
    for key in ("open_node_keys", "pending_node_keys"):
        out[key] = [n for n in row.get(key, []) if scope.node_key is None or n == scope.node_key]
    if "node_runs" in row:
        out["node_runs"] = [node_run(n, allowed) for n in row["node_runs"]
                            if scope.node_key is None or n["node_key"] == scope.node_key]
    return out


def _read(scope, group, resource, view):
    snap = read_snapshot(scope.flow_id)
    allowed = {d["doc_id"] for d in snap.documents
               if d.get("group_id") == scope.flow_id and d.get("project_id") == scope.project_id}
    snap.documents = [d for d in snap.documents if d.get("doc_id") in allowed]
    meta = {"flow_id": scope.flow_id, "project_id": scope.project_id,
            "scope": {"kind": "node" if scope.node_key else "group", "node_key": scope.node_key,
                      "attempt_id": scope.attempt_id},
            "capabilities": {"read": True, "write": False, "execute": False}}
    if resource == "current":
        row = run_store.current_run(scope.flow_id)
        payload = run_service.get_run(row["run_id"])["run"] if row else None
        return {**meta, "run": run_view(payload, scope, allowed)}
    protected = protected_ids(snap.rows)
    ident = IdentityResult(snap, keys_from_record(snap.rows, layout_store.identity_record(scope.flow_id), protected),
                           protected, sequence_tag_of(snap))
    # _read is the same projection used by read_canvas, without its write-capable preparation.
    raw = canvas_service._read(group, None, ident, view, None,
                               lookup=lambda ids: {k: v for k, v in snap.docs_by_id.items() if k in ids and k in allowed})
    nodes = []
    for row in raw["nodes"]:
        if scope.node_key and row["node_key"] != scope.node_key:
            continue
        out = scalar_fields(row, NODE_FIELDS)
        out["document"] = document(row.get("document"), allowed)
        out["node_run"] = node_run(row.get("node_run"), allowed)
        out["card_documents"] = [scalar_fields(line, LINE_FIELDS) for line in row.get("card_documents", [])
                                 if not line.get("doc_id") or line["doc_id"] in allowed]
        out["editable"] = {"structure": False, "position": False, "title": False}
        nodes.append(out)
    if scope.node_key and not nodes:
        authz.refuse("node_not_found", 404)
    keys = {n["node_key"] for n in nodes}
    edges = [{**scalar_fields(e, ("edge_id", "edge_class", "origin", "source", "target", "relation")), "editable": False}
             for e in raw["edges"] if e.get("source") in keys and e.get("target") in keys]
    return {**meta, "projection": raw["projection"], "view": raw["view"],
            "graph": {"latest_revision_no": raw["graph"].get("latest_revision_no")} if not scope.node_key else None,
            "nodes": nodes, "edges": edges, "external_relations": [],
            "run": run_view(raw.get("run"), scope, allowed)}


def read(token, flow_id, resource, view=None, run_hint=None):
    scope, group = authz.authorize(token, flow_id, run_hint)
    if resource not in ("canvas", "current") or view not in (None, "revision", "latest"):
        authz.refuse("invalid_request", 422)
    if scope.node_key and view not in (None, "revision"):
        authz.refuse("flow_access_denied")
    selected = view or ("revision" if scope.node_key else "latest")
    body = with_read_snapshot(lambda: _read(scope, group, resource, selected))
    # Outside the snapshot: cancellation, rotation and RBAC revocation during projection discard the result.
    authz.recheck(scope, run_hint)
    return body
