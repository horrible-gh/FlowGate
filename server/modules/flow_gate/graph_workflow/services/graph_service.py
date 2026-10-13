"""Graph definition service (D0004 §2.2 그래프 정의 서비스, P0008 §5 S3, L0009 §2.1.5 / §2.5).

Draft save and validation run inside the Flow guard: the ownership judgement, the
``draft_version`` comparison and the write are one unit. A draft save replaces the Flow's draft
as a whole (P0008 S3-1); concurrent saves are refused by version (Q-13 provisional rule, S3-2).
Structure is not checked on save, only on validation / confirmation. The same unit brings the
cards' documents in line with the draft (flowgate.v02.0053 T#1, ``card_document_ports.draft_saved``;
the feature lives in ``flowgate.card_documents``).
"""
from __future__ import annotations

import secrets
import string
from typing import Optional

from modules.flow_gate.graph_workflow.domain import draft_request as dr
from modules.flow_gate.graph_workflow.domain import policies
from modules.flow_gate.graph_workflow.domain.graph_model import (
    EDGE_ID_PREFIX,
    NODE_KEY_PREFIX,
    Graph,
    GraphEdge,
)
from modules.flow_gate.graph_workflow.domain.root_binding import BindingCheck, check_root_binding
from modules.flow_gate.graph_workflow.projection.flow_snapshot import (
    alive_documents,
    read_snapshot,
    registered_doc_types,
)
from modules.flow_gate.graph_workflow.projection.graph_projector import to_graph_node
from modules.flow_gate.graph_workflow.store import assignment_store, definition_store, layout_store

from .canvas_events import publish_canvas_changed
from .canvas_service import follow_supersede
from . import card_document_ports, graph_validation_ports
from .engine_router import Ownership, block, confirm_block_reason, read_ownership, require_graph_editable
from .errors import GraphApiError
from .flow_guard import with_flow_guard

_KEY_ALPHABET = string.digits + string.ascii_lowercase
_KEY_LENGTH = 12


def require_writable(flow_id: str) -> None:
    from modules.flow_gate.process_service import is_group_disposed   # v0.1 single source of truth
    if is_group_disposed(flow_id):
        raise GraphApiError(409, "flow_disposed", {"flow_id": flow_id})


def new_key(prefix: str, taken: set[str]) -> str:
    while True:
        key = prefix + "".join(secrets.choice(_KEY_ALPHABET) for _ in range(_KEY_LENGTH))
        if key not in taken:
            taken.add(key)
            return key


def as_graph(record: Optional[dict]) -> Graph:
    nodes, edges = definition_store.as_graph_parts(record)
    return Graph(
        nodes=tuple(to_graph_node(n) for n in nodes),
        edges=tuple(GraphEdge(e["edge_id"], e["source"], e["target"], e.get("condition")) for e in edges),
    )


def _wire(record: dict) -> tuple[list[dict], list[dict]]:
    g = as_graph(record)
    return [n.as_wire() for n in g.nodes], [e.as_wire() for e in g.edges]


def draft_wire(draft: Optional[dict], state: str) -> Optional[dict]:
    if draft is None:
        return None
    nodes, edges = _wire(draft)
    reason = confirm_block_reason(state)
    return {
        "draft_version": draft["draft_version"], "base_revision_no": draft.get("base_revision_no"),
        "updated_at": draft.get("updated_at"), "updated_by": draft.get("updated_by"),
        "confirmable": reason is None, "blocked_reason": reason, "nodes": nodes, "edges": edges,
    }


def revision_wire(rev: Optional[dict]) -> Optional[dict]:
    if rev is None:
        return None
    nodes, edges = _wire(rev)
    return {"revision_no": rev["revision_no"], "confirmed_at": rev.get("confirmed_at"),
            "confirmed_by": rev.get("confirmed_by"), "nodes": nodes, "edges": edges}


def graph_payload(flow_id: str) -> dict:
    """P0008 §5.2 ``GET /flows/{flow_id}/graph``."""
    own = read_ownership(flow_id)
    latest_no = definition_store.latest_revision_no(flow_id)
    return {
        "flow_id": flow_id,
        "ownership": block(flow_id, own),
        "draft": draft_wire(definition_store.draft(flow_id), own.state),
        "latest_revision": revision_wire(definition_store.revision(flow_id, latest_no) if latest_no else None),
        "policies": policies.policy_states(),
    }


def revision_payload(flow_id: str, revision_no: int) -> dict:
    """P0008 §5.2 ``GET /flows/{flow_id}/graph/revisions/{n}``."""
    rev = definition_store.revision(flow_id, revision_no)
    if rev is None:
        raise GraphApiError(404, "revision_not_found", {"flow_id": flow_id, "revision_no": revision_no})
    out = revision_wire(rev)
    binding = assignment_store.root_binding(flow_id)
    out["root_binding"] = None
    if binding and binding["bound_at_revision_no"] <= revision_no and \
            binding["node_key"] in {n["node_key"] for n in rev["nodes"]}:
        docs = read_snapshot(flow_id).docs_by_id
        out["root_binding"] = {"node_key": binding["node_key"],
                               "doc_id": follow_supersede(flow_id, docs, binding["doc_id"]),
                               "bound_at_revision_no": binding["bound_at_revision_no"]}
    return {"flow_id": flow_id, "revision": out}


def version_conflict(flow_id: str, expected: int, current: Optional[dict]) -> GraphApiError:
    return GraphApiError(409, "draft_version_conflict", {
        "flow_id": flow_id, "expected_draft_version": expected,
        "current_draft_version": current["draft_version"] if current else 0,
        "updated_by": (current or {}).get("updated_by"), "updated_at": (current or {}).get("updated_at"),
    })


def check_draft(flow_id: str, own: Ownership, draft: dict) -> tuple[list[dict], BindingCheck]:
    """Structure issues + root binding (L0009 §2.4.2 / §2.5): binding issues come last. The structure is
    validated through ``graph_validation_ports`` (the flowgate.v02.0061 pre-run rules when registered)."""
    graph = as_graph(draft)
    issues = graph_validation_ports.validate(graph, policies.current())
    snap = read_snapshot(flow_id)
    binding = check_root_binding(
        graph, own.state, alive_documents(snap), assignment_store.root_binding(flow_id),
        lambda doc_id: follow_supersede(flow_id, snap.docs_by_id, doc_id),
    )
    return issues + binding.issues, binding


def _resolve(req: dr.DraftRequest, taken: set[str]) -> tuple[list[dict], list[dict], dict]:
    key_map: dict[str, str] = {}
    for n in req.nodes:
        if n.client_key:
            key_map[n.client_key] = new_key(NODE_KEY_PREFIX, taken)
    for e in req.edges:
        if e.client_key:
            key_map[e.client_key] = new_key(EDGE_ID_PREFIX, taken)
    nodes = [{
        "node_key": n.node_key or key_map[n.client_key], "node_type": n.node_type, "doc_type": n.doc_type,
        "palette_item_id": n.palette_item_id, "label": n.label, "assignee_kind": n.assignee_kind,
        "assignee_user_id": n.assignee_user_id, "assignee_provider_id": n.assignee_provider_id,
    } for n in req.nodes]
    edges = [{
        "edge_id": e.edge_id or key_map[e.client_key], "source": key_map.get(e.source, e.source),
        "target": key_map.get(e.target, e.target), "condition": e.condition,
    } for e in req.edges]
    return nodes, edges, key_map


def save_draft(group: dict, user_id: str, req: dr.DraftRequest, client_id: Optional[str]) -> dict:
    """P0008 S3-1~S3-4 ``PUT /flows/{flow_id}/graph/draft``."""
    flow_id, project_id = group["group_id"], group.get("project_id")
    require_writable(flow_id)
    registered = registered_doc_types(project_id)

    def body() -> dict:
        own = read_ownership(flow_id)
        require_graph_editable(flow_id, own)
        current = definition_store.draft(flow_id)
        if (current["draft_version"] if current else 0) != req.draft_version:
            raise version_conflict(flow_id, req.draft_version, current)
        latest_no = definition_store.latest_revision_no(flow_id)
        latest = definition_store.revision(flow_id, latest_no) if latest_no else None
        known_nodes = {n["node_key"] for rec in (current, latest) if rec for n in rec["nodes"]}
        known_edges = {e["edge_id"] for rec in (current, latest) if rec for e in rec["edges"]}
        errors = req.errors + dr.unknown_keys(req, known_nodes, known_edges) + dr.unknown_doc_types(req, registered)
        if errors:
            raise GraphApiError(422, "invalid_request", {"errors": dr.sort_errors(errors)})
        taken = known_nodes | known_edges | layout_store.retired_keys(flow_id) | set(layout_store.positions(flow_id))
        nodes, edges, key_map = _resolve(req, taken)
        if req.draft_version == 0:
            base = latest_no if own.state == "graph" else None
            written = definition_store.create_draft(flow_id, base, user_id)
        else:
            written = definition_store.bump_draft(flow_id, req.draft_version, user_id)
        if written != 1:
            raise version_conflict(flow_id, req.draft_version, current)
        definition_store.replace_draft_content(flow_id, nodes, edges)
        new_nodes, new_edges = {n["node_key"] for n in nodes}, {e["edge_id"] for e in edges}
        removed_nodes = [n["node_key"] for n in (current or {}).get("nodes", []) if n["node_key"] not in new_nodes]
        removed_edges = [e["edge_id"] for e in (current or {}).get("edges", []) if e["edge_id"] not in new_edges]
        for key in removed_nodes:
            layout_store.retire(flow_id, key, "draft_removed")
        card_documents = card_document_ports.draft_saved(group, user_id, nodes, removed_nodes, registered,
                                                         ((current or latest) or {}).get("nodes", []))
        version = req.draft_version + 1
        publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason="draft", actor_user_id=user_id,
                               origin_client_id=client_id, draft_version=version)
        return {"flow_id": flow_id, "draft_version": version, "key_map": key_map,
                "removed_node_keys": removed_nodes, "removed_edge_ids": removed_edges,
                "card_documents": card_documents}

    return with_flow_guard(flow_id, body)


def validate_draft(group: dict, user_id: str, draft_version: int) -> dict:
    """P0008 S3-5 / S3-6 ``POST /flows/{flow_id}/graph/draft/validate`` — never writes."""
    flow_id = group["group_id"]

    def body() -> dict:
        own = read_ownership(flow_id)
        require_graph_editable(flow_id, own)
        draft = definition_store.draft(flow_id)
        if draft is None:
            raise GraphApiError(404, "draft_not_found", {"flow_id": flow_id})
        if draft["draft_version"] != draft_version:
            raise version_conflict(flow_id, draft_version, draft)
        issues, binding = check_draft(flow_id, own, draft)
        return {"flow_id": flow_id, "draft_version": draft_version, "confirmable": not issues,
                "issues": issues, "root_binding_preview": binding.preview()}

    return with_flow_guard(flow_id, body)
