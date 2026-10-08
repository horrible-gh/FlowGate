"""Canvas read (P0008 §3 ``GET /flows/{flow_id}/canvas``, S2 column of the stage table).

ownership (engine router) -> node identity (keeper, may record a rematch) -> legacy projection
-> saved / auto positions -> collapse -> response. Graph draft / revision / run blocks stay
null until S3~S5 add their stores.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.domain.auto_layout import layout
from modules.flow_gate.graph_workflow.projection.flow_snapshot import (
    lookup_documents,
    protected_ids,
    read_live_flow,
    read_snapshot,
)
from modules.flow_gate.graph_workflow.projection.legacy_projector import Projection, project
from modules.flow_gate.graph_workflow.store import layout_store

from .engine_router import ownership_block
from .errors import FlowNotFound, GraphApiError
from .identity_keeper import ensure_identity, keys_from_record

VIEW_MODES = ("revision", "draft")


def load_flow(flow_id: str) -> dict:
    group = read_live_flow(flow_id)
    if group is None:
        raise FlowNotFound(flow_id)
    return group


def read_canvas(group: dict, user_id: Optional[str], *, view: Optional[str], collapse: Optional[str],
                client_id: Optional[str]) -> dict:
    flow_id = group["group_id"]
    project_id = group.get("project_id")
    ident = ensure_identity(flow_id, project_id, user_id, client_id)
    snap = ident.snapshot
    ownership = ownership_block(flow_id, snap.sequence)
    if view == "draft" and ownership["state"] in ("graph", "conflict"):
        # The draft store arrives with S3; a Graph Flow has no draft to show before it.
        raise GraphApiError(404, "draft_not_found", {"flow_id": flow_id})
    proj = project(flow_id, ownership["state"], snap.documents, snap.sequence, snap.rows,
                   ident.row_keys, ident.protected, lambda ids: lookup_documents(ids))
    _place(proj, layout_store.positions(flow_id))
    applied, ignored = cm.parse_collapse(collapse)
    hidden_counts = _collapse(proj, applied)
    return {
        "flow_id": flow_id,
        "project_id": project_id,
        "ownership": ownership,
        "projection": proj.projection,
        "view": None,
        "graph": {"draft": None, "latest_revision_no": None, "root_binding": None},
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
    """Keys a Canvas read would return now, collapse not applied (L0009 §2.3.8).

    Reads the recorded mapping only (never rematches), so it is safe inside a Flow guard.
    """
    snap = read_snapshot(flow_id)
    protected = protected_ids(snap.rows)
    row_keys = keys_from_record(snap.rows, layout_store.identity_record(flow_id), protected)
    ownership = ownership_block(flow_id, snap.sequence)
    proj = project(flow_id, ownership["state"], snap.documents, snap.sequence, snap.rows, row_keys, protected)
    return {node["node_key"] for node in proj.nodes}


def _place(proj: Projection, saved: dict) -> None:
    placed = layout(proj.projection, chain=proj.chain, free_docs=proj.free_docs, saved=saved)
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
