"""Root binding judgement (D0004 §3.7, L0009 §2.4.1 / §2.4.2, P0008 S3-5 / S4-5).

On the first confirmation of an unassigned Flow, the Flow's existing root document (its one live
R or B) is bound to the one start node of the same document type. Nobody picks the pair; if it
cannot be decided the confirmation is refused with the reason. A Graph Flow keeps its binding
while the bound start node stays a start node of the same document type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .graph_model import ROOT_DOC_TYPES, Graph
from .run_scope_ports import run_scope
from .graph_validator import issue


@dataclass
class BindingCheck:
    status: str                       # "bindable" | "not_needed" | "inherited" | "blocked"
    node_key: Optional[str] = None
    doc_id: Optional[str] = None
    doc_type: Optional[str] = None
    issues: list = field(default_factory=list)

    def preview(self) -> dict:
        """P0008 S3-5 ``root_binding_preview``."""
        return {
            "status": self.status,
            "node_key": self.node_key if self.status != "blocked" else None,
            "doc_id": self.doc_id if self.status != "blocked" else None,
            "reason": self.issues[0]["code"] if self.issues else None,
        }


def doc_key(doc_id: str) -> str:
    return f"doc:{doc_id}"


def resolve_root(alive_docs: list[dict]) -> tuple[str, list[dict]]:
    """L0009 §2.4.1 over the Flow's live documents: ("none" | "one" | "ambiguous", candidates)."""
    candidates = sorted(
        (d for d in alive_docs if str(d.get("type_code") or "").upper() in ROOT_DOC_TYPES),
        key=lambda d: d["doc_id"],
    )
    if not candidates:
        return "none", []
    return ("one" if len(candidates) == 1 else "ambiguous"), candidates


def _blocked(code: str, node_keys, **extra) -> BindingCheck:
    return BindingCheck(status="blocked", issues=[issue(code, node_keys, [], **extra)])


def check_root_binding(
    graph: Graph,
    ownership: str,
    alive_docs: list[dict],
    current: Optional[dict],
    follow_supersede: Callable[[str], str] = lambda doc_id: doc_id,
) -> BindingCheck:
    """L0009 §2.4.2 (confirmation and validation share it).

    ``current``: the Flow's binding record (``node_key``, ``doc_id``, ``doc_type``) or None.
    """
    starts = set(run_scope(graph).starts)
    if ownership in ("graph", "conflict"):
        if current is None:
            return BindingCheck(status="not_needed")
        node = graph.node(current["node_key"])
        if node is None:
            return _blocked("root_binding_start_removed", [], removed_node_key=current["node_key"])
        if node.key not in starts or node.node_type != "document" or node.doc_type != current.get("doc_type"):
            return _blocked("root_binding_start_removed", [node.key], removed_node_key=node.key,
                            _condition_changed=True)
        return BindingCheck(status="inherited", node_key=node.key, doc_id=follow_supersede(current["doc_id"]),
                            doc_type=current.get("doc_type"))
    status, candidates = resolve_root(alive_docs)
    if status == "none":
        return BindingCheck(status="not_needed")
    if status == "ambiguous":
        return _blocked("root_binding_ambiguous_root", [doc_key(d["doc_id"]) for d in candidates],
                        _root_count=len(candidates))
    root = candidates[0]
    root_type = str(root.get("type_code") or "").upper()
    same = [n.key for n in graph.nodes
            if n.key in starts and n.node_type == "document" and n.doc_type == root_type]
    if not same:
        return _blocked("root_binding_no_start_node", [doc_key(root["doc_id"])], _root_doc_type=root_type)
    if len(same) >= 2:
        return _blocked("root_binding_ambiguous_start", sorted(same), _root_doc_type=root_type)
    return BindingCheck(status="bindable", node_key=same[0], doc_id=root["doc_id"], doc_type=root_type)
