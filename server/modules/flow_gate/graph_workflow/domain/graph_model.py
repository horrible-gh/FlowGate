"""Graph definition model (D0004 §2.2 "Graph 모델", P0008 §5.1) — pure data, storage-agnostic.

A Graph is the meaning half of a Flow's definition: nodes (what to do) and execution edges
(what comes next). Coordinates never live here (D0004 §2.1 의미와 모양의 분리).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

NODE_TYPES = ("document", "gate", "custom")
# P0008 §5 S3-5 / L0009 §2.5.1: the Palette's document types and the root types.
PALETTE_DOC_TYPES = frozenset({"D", "P", "L", "DB", "N", "WP", "T", "TS"})
ROOT_DOC_TYPES = frozenset({"R", "B"})
# L0009 §2.0 보고 타입 (AUTO_REPORT_MAP values).
REPORT_DOC_TYPES = frozenset({"NR", "TR", "TR2", "TSR"})

NODE_KEY_PREFIX = "gn_"
EDGE_ID_PREFIX = "ge_"
CLIENT_KEY_PREFIX = "tmp_"
NODE_KEY_MAX_LEN = 120
LABEL_MAX_LEN = 200
PALETTE_ITEM_ID_MAX_LEN = 128
DRAFT_NODES_MAX = 300
DRAFT_EDGES_MAX = 600


@dataclass(frozen=True)
class GraphNode:
    key: str
    node_type: str
    doc_type: Optional[str]
    label: str
    palette_item_id: Optional[str] = None
    assignee_kind: Optional[str] = None
    assignee_user_id: Optional[str] = None
    assignee_provider_id: Optional[str] = None

    def assignee(self) -> Optional[dict]:
        if self.assignee_kind is None:
            return None
        return {"kind": self.assignee_kind, "provider_id": self.assignee_provider_id, "user_id": self.assignee_user_id}

    def as_wire(self) -> dict:
        """P0008 §5.2 node shape."""
        return {
            "node_key": self.key,
            "node_type": self.node_type,
            "doc_type": self.doc_type,
            "palette_item_id": self.palette_item_id,
            "label": self.label,
            "assignee": self.assignee(),
        }


@dataclass(frozen=True)
class GraphEdge:
    edge_id: str
    source: str
    target: str
    condition: Any = None

    def as_wire(self) -> dict:
        return {"edge_id": self.edge_id, "source": self.source, "target": self.target, "condition": self.condition}


@dataclass(frozen=True)
class Graph:
    nodes: tuple = field(default_factory=tuple)     # tuple[GraphNode, ...] in stored order
    edges: tuple = field(default_factory=tuple)     # tuple[GraphEdge, ...] in stored order

    def node(self, key: str) -> Optional[GraphNode]:
        for n in self.nodes:
            if n.key == key:
                return n
        return None

    def node_keys(self) -> list[str]:
        return [n.key for n in self.nodes]

    def live_edges(self) -> list[GraphEdge]:
        """Edges whose two ends are nodes of this Graph (L0009 §2.5.1 ``E``)."""
        keys = set(self.node_keys())
        return [e for e in self.edges if e.source in keys and e.target in keys]

    def in_degree(self) -> dict[str, int]:
        deg = {n.key: 0 for n in self.nodes}
        for e in self.live_edges():
            deg[e.target] += 1
        return deg

    def start_keys(self) -> list[str]:
        """Nodes with no incoming edge (L0009 §2.0 시작 노드), in stored order."""
        deg = self.in_degree()
        return [n.key for n in self.nodes if deg[n.key] == 0]


def node_kind(node: GraphNode, kind_for_type) -> str:
    """P0008 §2.2: document -> kind of its doc_type, gate -> approval, custom -> its Palette id."""
    if node.node_type == "document":
        return kind_for_type(node.doc_type)
    if node.node_type == "gate":
        return "approval"
    return node.palette_item_id or "unknown"
