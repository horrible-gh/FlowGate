"""Graph definition service (D0004 §2.2 그래프 정의 서비스, P0008 §5 S3, L0009 §2.1.5 / §2.5).

Draft save and validation run inside the Flow guard: the ownership judgement, the
``draft_version`` comparison and the write are one unit. A draft save replaces the Flow's draft
as a whole (P0008 S3-1); concurrent saves are refused by version (Q-13 provisional rule, S3-2).
Structure is not checked on save, only on validation / confirmation. The same unit brings the
cards' documents in line with the draft (flowgate.v02.0053 T#1, ``card_document_ports.draft_saved``;
the feature lives in ``flowgate.card_documents``).

flowgate.v02.0061 T#11: a Graph read tells whether each node's card is held (``hold`` on every node of the draft and the
revisions, ``graph_execution_hooks`` ``held_nodes``; false for all when no handler is registered).

0064 T-B4b (P0006 §4.2.1): a Graph read carries each node's ``title_state`` (the card's current state, also on a
past revision; null on a v0.1 Flow). A draft save neither reads nor writes title states nor answers them. A Graph read
and a revision read take their definition and title states in one read unit (``flow_guard.with_read_snapshot``,
DB0007 Q1): never a draft of one moment with the title states of another.
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
from modules.flow_gate.graph_workflow.projection.graph_projector import title_state_block, to_graph_node
from modules.flow_gate.graph_workflow.store import assignment_store, definition_store, layout_store, title_store

from .canvas_events import publish_canvas_changed
from .canvas_service import follow_supersede
from . import card_document_ports, graph_validation_ports, run_state_ports
from .engine_router import Ownership, block, confirm_block_reason, read_ownership, require_graph_editable
from .errors import GraphApiError
from .flow_guard import held_guard, with_flow_guard, with_read_snapshot

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


def _with_hold(flow_id: str, *wires: Optional[dict]) -> None:
    """0061 T#11: ``hold`` on each node of the wires (the card's Hold, kept by node key whatever the revision)."""
    from modules.flow_gate.services import graph_execution_hooks
    held = frozenset(graph_execution_hooks.call("held_nodes", flow_id) or ())
    for wire in wires:
        for node in (wire or {}).get("nodes", []):
            node["hold"] = node["node_key"] in held


def _with_title_states(flow_id: str, carried: bool, *wires: Optional[dict]) -> None:
    """0064 T-B4b: ``title_state`` on each node of the wires (the Flow's states in one statement, in the caller's read
    unit with the wires' definition), else null."""
    states = title_store.states(flow_id) if carried else {}
    for wire in wires:
        for node in (wire or {}).get("nodes", []):
            node["title_state"] = title_state_block(states.get(node["node_key"])) if carried else None


def graph_payload(flow_id: str) -> dict:
    """P0008 §5.2 ``GET /flows/{flow_id}/graph``."""
    return with_read_snapshot(lambda: _graph_read(flow_id))


def _graph_read(flow_id: str) -> dict:
    own = read_ownership(flow_id)
    latest_no = definition_store.latest_revision_no(flow_id)
    draft = draft_wire(definition_store.draft(flow_id), own.state)
    latest = revision_wire(definition_store.revision(flow_id, latest_no) if latest_no else None)
    _with_hold(flow_id, draft, latest)
    _with_title_states(flow_id, own.state != "v01", draft, latest)
    return {
        "flow_id": flow_id,
        "ownership": block(flow_id, own),
        "draft": draft,
        "latest_revision": latest,
        "policies": policies.policy_states(),
    }


def revision_payload(flow_id: str, revision_no: int) -> dict:
    """P0008 §5.2 ``GET /flows/{flow_id}/graph/revisions/{n}``."""
    return with_read_snapshot(lambda: _revision_read(flow_id, revision_no))


def _revision_read(flow_id: str, revision_no: int) -> dict:
    rev = definition_store.revision(flow_id, revision_no)
    if rev is None:
        raise GraphApiError(404, "revision_not_found", {"flow_id": flow_id, "revision_no": revision_no})
    out = revision_wire(rev)
    _with_hold(flow_id, out)
    _with_title_states(flow_id, True, out)
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


def save_draft(group: dict, user_id: str, req: dr.DraftRequest, client_id: Optional[str],
               locale: Optional[str] = None) -> dict:
    """P0008 S3-1~S3-4 ``PUT /flows/{flow_id}/graph/draft``; ``locale`` is the request's (flowgate.v02.0088)."""
    flow_id = group["group_id"]
    require_writable(flow_id)
    registered = registered_doc_types(group.get("project_id"))
    return with_flow_guard(flow_id, lambda: write_draft(group, user_id, req, client_id, registered, locale))


def write_draft(group: dict, user_id: Optional[str], req: dr.DraftRequest, client_id: Optional[str],
                registered: set[str], locale: Optional[str] = None) -> dict:
    """The draft save inside the caller's Flow guard of ``group`` (flowgate.v02.0074 T#4, D0006 §2 초안 저장 내부 함수).

    ``save_draft`` runs it in a guard of its own; the work plan deploy runs it in the guard that also writes its
    cards' settings and instructions, so the draft, the card lines and those writes commit together or not at all.
    Every check and write of a draft save is here (ownership, run lock, version CAS, protected nodes, keys, card
    documents); the Canvas event is queued after the commit, so a rolled-back caller announces nothing.
    ``locale`` (the saving request's) goes to the card documents port: the documents the save makes are titled in it.
    """
    flow_id, project_id = group["group_id"], group.get("project_id")
    assert held_guard() == ("flow", flow_id), "write_draft runs inside the Flow guard of its Flow"

    def body() -> dict:
        own = read_ownership(flow_id)
        require_graph_editable(flow_id, own)
        run_state_ports.require_editable(flow_id)
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
        run_state_ports.protect_definition(flow_id, nodes, edges)
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
        try:
            card_documents = card_document_ports.draft_saved(group, user_id, nodes, removed_nodes, registered,
                                                             ((current or latest) or {}).get("nodes", []),
                                                             locale=locale)
        except GraphApiError as exc:
            if exc.code == "document_storage_unwritable":
                reverse_keys = {value: key for key, value in key_map.items()}
                key = exc.params.get("node_key")
                exc.params["node_key"] = reverse_keys.get(key, key)
            raise
        version = req.draft_version + 1
        publish_canvas_changed(project_id=project_id, flow_id=flow_id, reason="draft", actor_user_id=user_id,
                               origin_client_id=client_id, draft_version=version)
        return {"flow_id": flow_id, "draft_version": version, "key_map": key_map,
                "removed_node_keys": removed_nodes, "removed_edge_ids": removed_edges,
                "card_documents": card_documents}

    return body()


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
