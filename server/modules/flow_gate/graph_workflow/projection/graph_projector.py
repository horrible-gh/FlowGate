"""Graph projection: draft / revision nodes on the Canvas (D0004 §3.2 Graph row, P0008 S1-3~S1-5).

- ``project_graph`` (ownership graph / conflict): the viewed definition's nodes and execution
  edges, every live document not bound to a Graph node as a free document node, and reference
  edges. The root-bound start node shows the bound root document (the live end of its supersede
  chain) as its result; that document's own node is absorbed, so reference edges touching it
  end at the Graph node (D0004 §3.6 / §3.7, P0008 S1-3). With a run (revision view) a node
  carries its ``node_run`` and run state, and its live result document is absorbed the same way.
- ``add_draft`` (unassigned): the documents projection plus the draft's nodes and edges.
- flowgate.v02.0053 T#1: every Graph / draft node carries its card lines (``card_documents``); a node
  showing no document otherwise shows its first line's document (title and state stay the node's).
  The canvas service hands each view the lines of the kind that view gives the node (a retyped draft
  node and its confirmed revision each show their own; ``card_document_ports.card_view``).
  The documents a card made are never drawn as free document nodes (``hidden_doc_ids``).
- flowgate.v02.0053 T#8: a Custom card (``custom`` node) is titled with the name a person gave it (its
  label), also once a run bound a result document to it — that name is all that tells one apart.
- flowgate.v02.0064 T-B4b (0062 D0005 §3.1, P0006 §4.2.1): every Graph / draft node carries its card's title
  state (``title_state``: title, source, version; version 0 without a row) and shows that title first; with
  none it keeps the rules above. The canvas service hands each view the Flow's states read in one statement.
- flowgate.v02.0064 T-B4d (0062 L0006 2.5): the card's document (``card_document``) and the title it shows before its
  title state (``card_title_source``) are pure functions the AI naming judgement shares, so it reads a card exactly
  as it is drawn.
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
# The card line authors (card_documents ``author``) a node without a document shows first: a person's line,
# and an instruction its instruction AI or a person writes (flowgate.v02.0056 T#2).
_WRITTEN_AUTHORS = ("human", "instruction_ai_or_human")
_TITLE_SOURCES = ("ai", "human")


@dataclass
class GraphView:
    mode: str                                         # "draft" | "revision"
    nodes: list[dict] = field(default_factory=list)   # definition store rows
    edges: list[dict] = field(default_factory=list)
    bound_node_key: Optional[str] = None              # root-bound start node, if any
    bound_doc_id: Optional[str] = None                # the bound root document (live end)
    node_runs: dict = field(default_factory=dict)     # node_key -> P0008 §2.6 ``node_run`` block
    card_docs: dict = field(default_factory=dict)     # node_key -> card lines of the node's kind in this view
    hidden_doc_ids: frozenset = frozenset()            # documents a card made (never free nodes)
    title_states: dict = field(default_factory=dict)  # node_key -> stored title state (0064 T-B4b)


def to_graph_node(row: dict) -> GraphNode:
    return GraphNode(
        key=row["node_key"], node_type=row["node_type"], doc_type=row.get("doc_type"), label=row["label"],
        palette_item_id=row.get("palette_item_id"), assignee_kind=row.get("assignee_kind"),
        assignee_user_id=row.get("assignee_user_id"), assignee_provider_id=row.get("assignee_provider_id"),
    )


def title_state_block(state: Optional[dict]) -> dict:
    """P0006 §4.2.1 ``title_state`` of a card: no row is version 0 with no title; the source ``none`` is null."""
    state = state or {}
    source = state.get("title_source")
    return {"title": state.get("title") or None, "title_source": source if source in _TITLE_SOURCES else None,
            "version": int(state.get("version") or 0)}


def card_document(view: GraphView, row: dict, by_id: dict) -> Optional[dict]:
    """0062 L0006 2.5 ``card_document``: the document a card shows — the bound root (live end) of the root-bound node,
    else the node run's result while it is alive; None for neither."""
    bound = row["node_key"] == view.bound_node_key
    doc = by_id.get(view.bound_doc_id) if bound and view.bound_doc_id else None
    run = view.node_runs.get(row["node_key"])
    if run is not None and not bound and run.get("result_doc_id"):
        result = by_id.get(run["result_doc_id"])
        doc = result if result is not None and cm.is_alive(result, result.get("group_id")) else None
    return doc


def card_title_source(row: dict, document: Optional[dict]) -> tuple[str, Optional[str], Optional[str]]:
    """0062 L0006 2.5 ``card_title_source``, the title before the card's title state: a Custom card's label, else its
    document's title, else its label -> ``(kind, text, doc_id)``, kind "label" or "document"."""
    if row.get("node_type") == "custom":
        return "label", row.get("label"), None
    if document and document.get("title"):
        return "document", document["title"], document.get("doc_id")
    return "label", row.get("label"), None


def graph_node(row: dict, origin: str, ownership: str, document: Optional[dict], supersedes,
               root_bound: bool, lines: Optional[list] = None, line_doc: Optional[dict] = None,
               title_state: Optional[dict] = None) -> dict:
    node = to_graph_node(row)
    state = title_state_block(title_state)
    shown = card_title_source(row, document)[1]
    return {
        "node_key": node.key,
        "identity_source": "graph_node",
        "origin": origin,
        "kind": node_kind(node, cm.kind_for_type),
        "doc_type": node.doc_type,
        "title": state["title"] or shown,
        "state": (cm.document_state(document.get("doc_review_status"), document.get("type_code"))
                  if document else "draft"),
        "editable": cm.editable_for(ownership, origin),
        "document": document_block(document or line_doc, supersedes),
        "slot": None,
        "graph_node": {
            "node_type": node.node_type, "label": node.label, "palette_item_id": node.palette_item_id,
            "assignee": node.assignee(), "root_bound": root_bound,
        },
        "node_run": None,
        "card_documents": list(lines or []),
        "title_state": state,
    }


def first_line_document(lines: list, by_id: dict) -> Optional[dict]:
    """The document a node without one shows: its first line a person (or, for an instruction, the card's
    instruction AI or a person — flowgate.v02.0056 T#2) writes, else its first line."""
    filled = [line for line in lines or [] if line.get("doc_id") in by_id]
    human = [line for line in filled if line.get("author") in _WRITTEN_AUTHORS]
    pick = (human or filled or [None])[0]
    return by_id.get(pick["doc_id"]) if pick else None


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
        doc = card_document(view, row, by_id)
        run = view.node_runs.get(row["node_key"])
        lines = view.card_docs.get(row["node_key"]) or []
        node = graph_node(row, origin, ownership, doc, supersedes, bound, lines,
                          None if doc is not None else first_line_document(lines, by_id),
                          view.title_states.get(row["node_key"]))
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
        if doc["doc_id"] in drawn or doc["doc_id"] in view.hidden_doc_ids:
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
    by_id = {d["doc_id"]: d for d in documents if d.get("doc_id") and cm.is_alive(d, d.get("group_id"))}
    _graph_nodes(proj, view, ownership, by_id, supersedes, {}, [])
    _execution_edges(proj, view, cm.editable_for(ownership, VIEW_ORIGIN[view.mode])["structure"])
    return proj
