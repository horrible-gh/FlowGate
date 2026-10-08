"""Graph projection: draft / revision nodes on the Canvas (D0004 §3.2 Graph row, P0008 S1-3~S1-5).

- ``project_graph`` (ownership graph / conflict): the viewed definition's nodes and execution
  edges, every live document not bound to a Graph node as a free document node, and reference
  edges. The root-bound start node shows the bound root document (the live end of its supersede
  chain) as its result; that document's own node is absorbed, so reference edges touching it
  end at the Graph node (D0004 §3.6 / §3.7, P0008 S1-3). With a run (revision view) a node
  carries its ``node_run`` and run state, and its live result document is absorbed the same way.
- ``add_draft`` (unassigned): the documents projection plus the draft's nodes and edges.
Pure over its inputs; positions are placed by the canvas service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.domain.graph_model import GraphNode, node_kind

from .legacy_projector import Projection, doc_node, doc_order, document_block, reference_edges, supersedes_map

VIEW_ORIGIN = {"draft": "draft_node", "revision": "graph_node"}
EDGE_ORIGIN = {"draft": "draft", "revision": "graph"}


@dataclass
class GraphView:
    mode: str                                         # "draft" | "revision"
    nodes: list[dict] = field(default_factory=list)   # definition store rows
    edges: list[dict] = field(default_factory=list)
    bound_node_key: Optional[str] = None              # root-bound start node, if any
    bound_doc_id: Optional[str] = None                # the bound root document (live end)
    node_runs: dict = field(default_factory=dict)     # node_key -> P0008 §2.6 ``node_run`` block


def to_graph_node(row: dict) -> GraphNode:
    return GraphNode(
        key=row["node_key"], node_type=row["node_type"], doc_type=row.get("doc_type"), label=row["label"],
        palette_item_id=row.get("palette_item_id"), assignee_kind=row.get("assignee_kind"),
        assignee_user_id=row.get("assignee_user_id"), assignee_provider_id=row.get("assignee_provider_id"),
    )


def graph_node(row: dict, origin: str, ownership: str, document: Optional[dict], supersedes,
               root_bound: bool) -> dict:
    node = to_graph_node(row)
    return {
        "node_key": node.key,
        "identity_source": "graph_node",
        "origin": origin,
        "kind": node_kind(node, cm.kind_for_type),
        "doc_type": node.doc_type,
        "title": (document or {}).get("title") or node.label,
        "state": (cm.document_state(document.get("doc_review_status"), document.get("type_code"))
                  if document else "draft"),
        "editable": cm.editable_for(ownership, origin),
        "document": document_block(document, supersedes),
        "slot": None,
        "graph_node": {
            "node_type": node.node_type, "label": node.label, "palette_item_id": node.palette_item_id,
            "assignee": node.assignee(), "root_bound": root_bound,
        },
        "node_run": None,
    }


def _execution_edges(out: Projection, view: GraphView, editable: bool) -> None:
    keys = set(out.graph_nodes)
    for row in view.edges:
        out.graph_edges.append((row["source"], row["target"]))
        if row["source"] not in keys or row["target"] not in keys:
            continue                                   # a dangling draft edge is a validation issue
        out.edges.append({
            "edge_id": row["edge_id"], "edge_class": "execution", "origin": EDGE_ORIGIN[view.mode],
            "source": row["source"], "target": row["target"], "relation": None,
            "condition": row.get("condition"), "editable": editable,
        })


def _graph_nodes(out: Projection, view: GraphView, ownership: str, by_id: dict, supersedes,
                 drawn: dict, drawn_docs: list) -> None:
    origin = VIEW_ORIGIN[view.mode]
    for row in view.nodes:
        bound = row["node_key"] == view.bound_node_key
        doc = by_id.get(view.bound_doc_id) if bound and view.bound_doc_id else None
        run = view.node_runs.get(row["node_key"])
        if run is not None and not bound and run.get("result_doc_id"):
            result = by_id.get(run["result_doc_id"])
            doc = result if result is not None and cm.is_alive(result, result.get("group_id")) else None
        node = graph_node(row, origin, ownership, doc, supersedes, bound)
        if run is not None:
            node["node_run"] = run
            if not bound:
                node["state"] = cm.node_run_state(run.get("status"))
        out.nodes.append(node)
        out.graph_nodes.append(node["node_key"])
        if doc is not None:
            drawn[doc["doc_id"]] = node["node_key"]
            drawn_docs.append(doc)


def project_graph(flow_id: str, ownership: str, documents: list[dict], view: GraphView,
                  lookup: Callable[[list[str]], dict[str, dict]] = lambda _ids: {}) -> Projection:
    by_id = {d["doc_id"]: d for d in documents if d.get("doc_id")}
    supersedes = supersedes_map(documents)
    out = Projection(projection="graph")
    drawn: dict[str, str] = {}
    drawn_docs: list[dict] = []
    if view.bound_doc_id and not cm.is_alive(by_id.get(view.bound_doc_id) or {}, flow_id):
        view.bound_doc_id = None                       # L0009 E-19: deleted root -> document null
    _graph_nodes(out, view, ownership, by_id, supersedes, drawn, drawn_docs)
    _execution_edges(out, view, cm.editable_for(ownership, VIEW_ORIGIN[view.mode])["structure"])
    for doc in sorted((d for d in documents if cm.is_alive(d, flow_id)), key=doc_order):
        if doc["doc_id"] in drawn:
            continue
        node = doc_node(doc, ownership, supersedes)
        out.nodes.append(node)
        out.free_docs.append(node["node_key"])
        drawn[doc["doc_id"]] = node["node_key"]
        drawn_docs.append(doc)
    reference_edges(out, flow_id, drawn, sorted(drawn_docs, key=doc_order), by_id, lookup)
    return out


def add_draft(proj: Projection, ownership: str, documents: list[dict], view: GraphView) -> Projection:
    """Unassigned Flow: draft nodes and edges next to the document nodes (P0008 S1-4)."""
    supersedes = supersedes_map(documents)
    _graph_nodes(proj, view, ownership, {}, supersedes, {}, [])
    _execution_edges(proj, view, cm.editable_for(ownership, VIEW_ORIGIN[view.mode])["structure"])
    return proj
