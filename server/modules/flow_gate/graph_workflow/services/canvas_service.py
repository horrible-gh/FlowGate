"""Canvas read (P0008 §3 ``GET /flows/{flow_id}/canvas``, S2~S4 columns).

ownership (engine router) -> node identity (keeper, may record a rematch) -> projection by
ownership (v01: sequence, unassigned: documents + draft, graph / conflict: Graph definition) ->
saved / auto positions -> collapse -> response. Run blocks stay null until S5 adds runs.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.domain.auto_layout import layout
from modules.flow_gate.graph_workflow.domain.supersede import live_end
from modules.flow_gate.graph_workflow.projection.flow_snapshot import (
    FlowSnapshot,
    lookup_documents,
    protected_ids,
    read_live_flow,
    read_snapshot,
)
from modules.flow_gate.graph_workflow.projection.graph_projector import GraphView, add_draft, project_graph
from modules.flow_gate.graph_workflow.projection.legacy_projector import Projection, project
from modules.flow_gate.graph_workflow.store import assignment_store, definition_store, layout_store

from .engine_router import block, confirm_block_reason, judge
from .errors import FlowNotFound, GraphApiError
from .identity_keeper import ensure_identity, keys_from_record

VIEW_MODES = ("revision", "draft")


def load_flow(flow_id: str) -> dict:
    group = read_live_flow(flow_id)
    if group is None:
        raise FlowNotFound(flow_id)
    return group


def follow_supersede(flow_id: str, docs: dict, doc_id: Optional[str]) -> Optional[str]:
    """L0009 §2.4.2 ``follow_supersede``: the live end of the chain, else the recorded id."""
    if not doc_id:
        return None
    end, _hops = live_end(flow_id, docs.get(doc_id), docs)
    return end["doc_id"] if end else doc_id


class _Definition:
    """The Flow's Graph definition state read once per request."""

    def __init__(self, flow_id: str, docs: dict):
        self.draft = definition_store.draft(flow_id)
        self.latest_no = definition_store.latest_revision_no(flow_id)
        self.binding = assignment_store.root_binding(flow_id)
        self.bound_doc_id = follow_supersede(flow_id, docs, (self.binding or {}).get("doc_id"))
        self._flow_id = flow_id
        self._revision: Optional[dict] = None

    def revision(self) -> Optional[dict]:
        if self._revision is None and self.latest_no is not None:
            self._revision = definition_store.revision(self._flow_id, self.latest_no)
        return self._revision

    def view(self, mode: str) -> GraphView:
        record = self.draft if mode == "draft" else self.revision()
        nodes = list((record or {}).get("nodes") or [])
        bound_key = (self.binding or {}).get("node_key")
        if bound_key not in {n["node_key"] for n in nodes}:
            bound_key = None
        return GraphView(mode=mode, nodes=nodes, edges=list((record or {}).get("edges") or []),
                         bound_node_key=bound_key, bound_doc_id=self.bound_doc_id if bound_key else None)

    def graph_block(self, state: str) -> dict:
        draft = None
        if self.draft is not None:
            reason = confirm_block_reason(state)
            draft = {
                "draft_version": self.draft["draft_version"],
                "base_revision_no": self.draft.get("base_revision_no"),
                "updated_at": self.draft.get("updated_at"),
                "updated_by": self.draft.get("updated_by"),
                "confirmable": reason is None,
                "blocked_reason": reason,
            }
        binding = None
        if self.binding is not None:
            binding = {"node_key": self.binding["node_key"], "doc_id": self.bound_doc_id,
                       "bound_at_revision_no": self.binding["bound_at_revision_no"]}
        return {"draft": draft, "latest_revision_no": self.latest_no, "root_binding": binding}


def _pick_mode(flow_id: str, state: str, view: Optional[str], d: _Definition) -> Optional[str]:
    """Which definition the Canvas draws (P0008 §3 ``view``)."""
    if state == "unassigned":
        return "draft" if d.draft is not None else None          # ``view`` is ignored
    if state not in ("graph", "conflict"):
        return None                                                # v0.1: ignored
    if view == "draft":
        if d.draft is None:
            raise GraphApiError(404, "draft_not_found", {"flow_id": flow_id})
        return "draft"
    if view == "revision":
        if d.latest_no is None:
            raise GraphApiError(404, "revision_not_found", {"flow_id": flow_id, "revision_no": None})
        return "revision"
    return "revision" if d.latest_no is not None else ("draft" if d.draft is not None else None)


def _project(flow_id: str, state: str, snap: FlowSnapshot, row_keys, protected, d: _Definition,
             mode: Optional[str], lookup=lambda _ids: {}) -> Projection:
    if state in ("graph", "conflict") and mode is not None:
        return project_graph(flow_id, state, snap.documents, d.view(mode), lookup)
    proj = project(flow_id, state, snap.documents, snap.sequence, snap.rows, row_keys, protected, lookup)
    if state == "unassigned" and mode == "draft":
        add_draft(proj, state, snap.documents, d.view("draft"))
    return proj


def read_canvas(group: dict, user_id: Optional[str], *, view: Optional[str], collapse: Optional[str],
                client_id: Optional[str]) -> dict:
    flow_id = group["group_id"]
    project_id = group.get("project_id")
    ident = ensure_identity(flow_id, project_id, user_id, client_id)
    snap = ident.snapshot
    own = judge(flow_id, snap.sequence)
    d = _Definition(flow_id, snap.docs_by_id)
    mode = _pick_mode(flow_id, own.state, view, d)
    proj = _project(flow_id, own.state, snap, ident.row_keys, ident.protected, d, mode,
                    lambda ids: lookup_documents(ids))
    _place(proj, layout_store.positions(flow_id))
    applied, ignored = cm.parse_collapse(collapse)
    hidden_counts = _collapse(proj, applied)
    view_block = None
    if mode == "revision":
        view_block = {"mode": "revision", "revision_no": d.latest_no, "draft_version": None}
    elif mode == "draft":
        view_block = {"mode": "draft", "revision_no": None, "draft_version": d.draft["draft_version"]}
    return {
        "flow_id": flow_id,
        "project_id": project_id,
        "ownership": block(flow_id, own),
        "projection": proj.projection,
        "view": view_block,
        "graph": d.graph_block(own.state),
        "nodes": proj.nodes,
        "edges": proj.edges,
        "external_relations": proj.external_relations,
        "viewport": layout_store.viewport(flow_id, user_id) if user_id else None,
        "run": None,
        "identity": {
            "sequence_tag": ident.sequence_tag,
            "rematched": ident.rematched,
            "retired_node_keys": list(ident.retired),
        },
        "collapsed": {"applied": applied, "ignored_keys": ignored, "hidden_counts": hidden_counts},
    }


def current_node_keys(flow_id: str) -> set[str]:
    """Keys a Canvas read can return now, collapse not applied (L0009 §2.3.8).

    Every view counts (a Graph Flow's draft and revision views), so a node moved in either view
    is stored. Reads the recorded mapping only (never rematches): safe inside a Flow guard.
    """
    snap = read_snapshot(flow_id)
    protected = protected_ids(snap.rows)
    row_keys = keys_from_record(snap.rows, layout_store.identity_record(flow_id), protected)
    own = judge(flow_id, snap.sequence)
    d = _Definition(flow_id, snap.docs_by_id)
    modes: list[Optional[str]] = [None]
    if own.state == "unassigned" and d.draft is not None:
        modes = ["draft"]
    elif own.state in ("graph", "conflict"):
        modes = [m for m, present in (("revision", d.latest_no is not None), ("draft", d.draft is not None))
                 if present] or [None]
    keys: set[str] = set()
    for mode in modes:
        proj = _project(flow_id, own.state, snap, row_keys, protected, d, mode)
        keys |= {node["node_key"] for node in proj.nodes}
    return keys


def _place(proj: Projection, saved: dict) -> None:
    placed = layout(proj.projection, chain=proj.chain, free_docs=proj.free_docs, graph_nodes=proj.graph_nodes,
                    graph_edges=proj.graph_edges, saved=saved)
    for node in proj.nodes:
        pos = placed.get(node["node_key"]) or {"x": 0, "y": 0, "source": "auto"}
        node["position"] = {"x": pos["x"], "y": pos["y"], "source": pos["source"]}


def _collapse(proj: Projection, applied: list[str]) -> dict:
    if not applied:
        return {}
    counts: dict[str, int] = {}
    hidden: set[str] = set()
    for node in proj.nodes:
        group = cm.collapse_group_of(node.get("doc_type"))
        if node["origin"] == "document" and group in applied and node["node_key"] in proj.free_docs:
            hidden.add(node["node_key"])
            counts[group] = counts.get(group, 0) + 1
    proj.nodes = [n for n in proj.nodes if n["node_key"] not in hidden]
    proj.edges = [e for e in proj.edges if e["source"] not in hidden and e["target"] not in hidden]
    proj.external_relations = [r for r in proj.external_relations if r["node_key"] not in hidden]
    return counts
