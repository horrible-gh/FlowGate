"""Legacy projector: v0.1 sequence / documents -> Canvas nodes and edges (D0004 §3.2, P0008 §2·§3).

Pure over its inputs (the snapshot, the identity keys of sequence rows and already-fetched
external documents). Positions, viewport and collapse are applied by the canvas service.

- ``sequence`` projection (ownership v01 / conflict): root document + one node per slot,
  execution edges between adjacent slots, plus every other live document as a free node.
  Only live documents (P0008 §2.4) are drawn or bound: a superseded root is shown as the live
  end of its supersede chain, a cancelled root is not drawn (D0004 §3.7 "root 문서"), and a slot
  whose result document is no longer alive keeps its slot node without the document.
- ``documents`` projection (unassigned): one node per live document, no execution edges. A
  Graph draft of the Flow is added on top by ``graph_projector.add_draft``.
The ``graph`` projection (ownership graph / conflict) is built by ``graph_projector``.
Reference edges (``triggered_by`` / ``target``) are drawn only between documents drawn in this
Flow; anything else goes to ``external_relations`` (P0008 §2.7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping, Optional

from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.domain.rematch import doc_key
from modules.flow_gate.graph_workflow.domain.supersede import live_end

RELATIONS = (("triggered_by", "triggered_by"), ("target", "target_id"))


@dataclass
class Projection:
    projection: str                                       # "sequence" | "documents" | "graph"
    nodes: list[dict] = field(default_factory=list)       # P0008 §2.6 without "position"
    edges: list[dict] = field(default_factory=list)       # P0008 §2.7
    external_relations: list[dict] = field(default_factory=list)
    chain: list[str] = field(default_factory=list)        # root + slots, execution order
    free_docs: list[str] = field(default_factory=list)    # unbound document nodes, document order
    graph_nodes: list[str] = field(default_factory=list)  # Graph / draft node keys, stored order
    graph_edges: list[tuple] = field(default_factory=list)  # (source, target) of Graph / draft edges
    sequence_root_doc_id: Optional[str] = None


def doc_order(doc: dict):
    seq = doc.get("seq")
    return (seq if isinstance(seq, int) else 0, doc.get("doc_id") or "")


def document_block(doc: Optional[dict], supersedes: Mapping[str, list[str]]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "doc_id": doc["doc_id"],
        "type_code": doc.get("type_code"),
        "seq": doc.get("seq"),
        "title": doc.get("title"),
        "doc_review_status": doc.get("doc_review_status"),
        "status": doc.get("status"),
        "supersedes": list(supersedes.get(doc["doc_id"], [])),
    }


def _node(key, source, origin, doc_type, title, state, ownership, document=None, slot=None) -> dict:
    return {
        "node_key": key,
        "identity_source": source,
        "origin": origin,
        "kind": cm.kind_for_type(doc_type),
        "doc_type": cm.normalize_type(doc_type) or None,
        "title": title,
        "state": state,
        "editable": cm.editable_for(ownership, origin),
        "document": document,
        "slot": slot,
        "graph_node": None,
        "node_run": None,
    }


def supersedes_map(documents: list[dict]) -> dict[str, list[str]]:
    """doc_id -> the doc_ids it superseded, in document order (``document.supersedes``)."""
    out: dict[str, list[str]] = {}
    for d in sorted(documents, key=doc_order):
        if d.get("superseded_by"):
            out.setdefault(d["superseded_by"], []).append(d["doc_id"])
    return out


def doc_node(doc: dict, ownership: str, supersedes) -> dict:
    return _node(
        doc_key(doc["doc_id"]), "document", "document", doc.get("type_code"), doc.get("title"),
        cm.document_state(doc.get("doc_review_status"), doc.get("type_code")), ownership,
        document=document_block(doc, supersedes),
    )


def project(
    flow_id: str,
    ownership: str,
    documents: list[dict],
    sequence: Optional[dict],
    rows: list[dict],
    row_keys: Mapping[int, tuple[str, str]],
    protected: set[int],
    lookup: Callable[[list[str]], dict[str, dict]] = lambda _ids: {},
) -> Projection:
    by_id = {d["doc_id"]: d for d in documents if d.get("doc_id")}
    supersedes = supersedes_map(documents)
    alive = sorted((d for d in documents if cm.is_alive(d, flow_id)), key=doc_order)

    use_sequence = sequence is not None and ownership in ("v01", "conflict")
    out = Projection(projection="sequence" if use_sequence else "documents")
    drawn: dict[str, str] = {}          # doc_id -> node_key of the node showing it
    drawn_docs: list[dict] = []

    if use_sequence:
        missing = [sequence["doc_id"], *(r["result_doc_id"] for r in rows if r.get("result_doc_id"))]
        extra = lookup([d for d in missing if d not in by_id])
        result_ids = {r["result_doc_id"] for r in rows if r.get("result_doc_id")}
        root = _sequence_root(flow_id, sequence["doc_id"], by_id, extra, result_ids)
        if root:
            root_node = doc_node(root, ownership, supersedes)
            out.nodes.append(root_node)
            out.chain.append(root_node["node_key"])
            out.sequence_root_doc_id = root["doc_id"]
            drawn[root["doc_id"]] = root_node["node_key"]
            drawn_docs.append(root)
        for row in rows:
            key, source = row_keys[int(row["id"])]
            rid = row.get("result_doc_id")
            found = (by_id.get(rid) or extra.get(rid)) if rid else None
            doc = found if found and cm.is_alive(found, flow_id) else None
            review = found.get("doc_review_status") if found else row.get("result_doc_review_status")
            slot = {
                "item_id": int(row["id"]),
                "item_seq": row.get("item_seq"),
                "order": int(row.get("sort_order") or 0) + 1,
                "type": cm.normalize_type(row.get("type")),
                "label": row.get("label"),
                "derived_status": row.get("status") or ("pending" if rid is None else "in_progress"),
                "protected": int(row["id"]) in protected,
                "source_wp_card_id": row.get("source_wp_card_id"),
            }
            out.nodes.append(_node(
                key, source, "sequence_slot", row.get("type"),
                (doc or {}).get("title") or row.get("label"),
                cm.slot_state(rid, review, row.get("type")), ownership,
                document=document_block(doc, supersedes), slot=slot,
            ))
            out.chain.append(key)
            if doc:
                drawn[rid] = key
                drawn_docs.append(doc)
        for a, b in zip(out.chain, out.chain[1:]):
            out.edges.append(_execution_edge(a, b))

    for doc in alive:
        if doc["doc_id"] in drawn:
            continue
        node = doc_node(doc, ownership, supersedes)
        out.nodes.append(node)
        out.free_docs.append(node["node_key"])
        drawn[doc["doc_id"]] = node["node_key"]
        drawn_docs.append(doc)

    reference_edges(out, flow_id, drawn, sorted(drawn_docs, key=doc_order), by_id, lookup)
    return out


def _sequence_root(flow_id, root_id, by_id, extra, result_ids) -> Optional[dict]:
    """The live document shown as the sequence root, or None (P0008 §2.4, D0004 §3.7).

    The v0.1 root lookup also returns cancelled / superseded R·B. A superseded root is shown as
    the live end of its chain in this Flow; a cancelled, foreign or missing root is not drawn.
    A live end already shown as a slot result stays that slot (no second node for it).
    """
    doc = by_id.get(root_id) or extra.get(root_id)
    end, _hops = live_end(flow_id, doc, by_id)
    if end is None or end["doc_id"] in result_ids:
        return None
    return end


def _execution_edge(source: str, target: str) -> dict:
    return {
        "edge_id": f"seq:{source}>{target}", "edge_class": "execution", "origin": "sequence",
        "source": source, "target": target, "relation": None, "condition": None, "editable": False,
    }


def reference_edges(out: Projection, flow_id, drawn, drawn_docs, by_id, lookup) -> None:
    pending: list[tuple[dict, str, str]] = []
    for doc in drawn_docs:
        for relation, column in RELATIONS:
            other = doc.get(column)
            if not other or other == doc["doc_id"]:
                continue
            if other in drawn:
                out.edges.append({
                    "edge_id": f"ref:{relation}:{other}>{doc['doc_id']}",
                    "edge_class": "reference", "origin": "document_relation",
                    "source": drawn[other], "target": drawn[doc["doc_id"]],
                    "relation": relation, "condition": None, "editable": False,
                })
            else:
                pending.append((doc, relation, other))
    extra = lookup([o for _d, _r, o in pending if o not in by_id])
    for doc, relation, other in pending:
        other_doc = by_id.get(other) or extra.get(other)
        out.external_relations.append({
            "node_key": drawn[doc["doc_id"]],
            "relation": relation,
            "other_doc_id": other,
            "other_flow_id": (other_doc or {}).get("group_id"),
            "other_title": (other_doc or {}).get("title"),
        })
