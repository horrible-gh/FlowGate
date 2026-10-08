"""Graph structure validator (D0004 §2.2 그래프 검증기, L0009 §2.5, P0008 S3-5 issue table).

Every problem is reported at once, ordered by L0009 §2.5.2. A structure whose execution rule is
still undecided (D0004 §8) is neither allowed nor forbidden: it is reported as ``rule_undecided``
with the question it waits on, which only blocks confirmation. Messages are added by the API
layer (locale); issues here carry codes and keys only.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .graph_model import PALETTE_DOC_TYPES, REPORT_DOC_TYPES, ROOT_DOC_TYPES, Graph
from .policies import UNDECIDED

_CODE_RANK = [
    "empty_graph", "dangling_edge", "duplicate_edge", "unreachable_node",
    "rule_undecided/multiple_start", "rule_undecided/branch", "rule_undecided/edge_condition",
    "rule_undecided/join", "rule_undecided/report_pair", "rule_undecided/cycle",
    "rule_undecided/gate_node", "rule_undecided/custom_node", "rule_undecided/non_palette_doc_type",
]


def issue(code: str, node_keys=(), edge_ids=(), rule_ref: Optional[str] = None,
          structure: Optional[str] = None, **extra) -> dict:
    return {
        "code": code, "severity": "error", "rule_ref": rule_ref, "structure": structure,
        "node_keys": list(node_keys), "edge_ids": list(edge_ids), "message": None, **extra,
    }


def _rank(item: dict) -> int:
    key = item["code"] if item["code"] != "rule_undecided" else f"rule_undecided/{item['structure']}"
    return _CODE_RANK.index(key) if key in _CODE_RANK else len(_CODE_RANK)


def order(issues: list[dict]) -> list[dict]:
    """L0009 §2.5.2: issues naming nodes by (min node key, code rank), then edge-only issues."""
    with_nodes = [i for i in issues if i["node_keys"]]
    edge_only = [i for i in issues if not i["node_keys"]]
    with_nodes.sort(key=lambda i: (min(i["node_keys"]), _rank(i)))
    edge_only.sort(key=lambda i: (min(i["edge_ids"]) if i["edge_ids"] else "", _rank(i)))
    return with_nodes + edge_only


def _reachable(starts: list[str], adjacency: Mapping[str, list[str]]) -> set[str]:
    seen = set(starts)
    stack = list(starts)
    while stack:
        for nxt in adjacency.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def strongly_connected(keys: list[str], adjacency: Mapping[str, list[str]]) -> list[list[str]]:
    """Tarjan's SCC, iterative (no recursion limit on long chains)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in keys:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, i = work.pop()
            if i == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            succ = adjacency.get(node, [])
            if i < len(succ):
                work.append((node, i + 1))
                nxt = succ[i]
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            if work and work[-1][0] != node:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    top = stack.pop()
                    on_stack.discard(top)
                    comp.append(top)
                    if top == node:
                        break
                out.append(sorted(comp))
    return out


def _structural(g: Graph, policies: Mapping[str, Any]) -> list[dict]:
    edges = g.live_edges()
    out_edges: dict[str, list] = {k: [] for k in g.node_keys()}
    in_edges: dict[str, list] = {k: [] for k in g.node_keys()}
    for e in edges:
        out_edges[e.source].append(e)
        in_edges[e.target].append(e)
    starts = g.start_keys()
    occurrences: list[tuple[str, str, str, list[str], list[str]]] = []
    if len(starts) >= 2:
        occurrences.append(("multiple_start", "Q-1", "start_node", sorted(starts), []))
    for n in g.nodes:
        if len(out_edges[n.key]) >= 2:
            occurrences.append(("branch", "Q-2", "edge_condition", [n.key], sorted(e.edge_id for e in out_edges[n.key])))
    for e in edges:
        if e.condition is not None:
            occurrences.append(("edge_condition", "Q-2", "edge_condition", [], [e.edge_id]))
    for n in g.nodes:
        if len(in_edges[n.key]) >= 2:
            occurrences.append(("join", "Q-4", "join", [n.key], sorted(e.edge_id for e in in_edges[n.key])))
    for n in g.nodes:
        if n.node_type == "document" and n.doc_type in REPORT_DOC_TYPES:
            occurrences.append(("report_pair", "Q-5", "report_pair", [n.key], []))
    adjacency = {k: [e.target for e in out_edges[k]] for k in g.node_keys()}
    for comp in strongly_connected(g.node_keys(), adjacency):
        members = set(comp)
        inside = sorted(e.edge_id for e in edges if e.source in members and e.target in members)
        if len(comp) >= 2 or inside:
            occurrences.append(("cycle", "Q-6", "rejection_path", comp, inside))
    for n in g.nodes:
        if n.node_type in ("gate", "custom"):
            structure = "gate_node" if n.node_type == "gate" else "custom_node"
            occurrences.append((structure, "Q-11", structure, [n.key], []))
    issues: list[dict] = []
    for structure, ref, policy_key, node_keys, edge_ids in occurrences:
        policy = policies.get(policy_key, UNDECIDED)
        if policy == UNDECIDED:
            issues.append(issue("rule_undecided", node_keys, edge_ids, rule_ref=ref, structure=structure))
        elif hasattr(policy, "validate"):
            issues.extend(policy.validate(g, {"structure": structure, "node_keys": node_keys, "edge_ids": edge_ids}))
    allowed = PALETTE_DOC_TYPES | ROOT_DOC_TYPES | REPORT_DOC_TYPES
    for n in g.nodes:
        if n.node_type == "document" and n.doc_type not in allowed:
            issues.append(issue("rule_undecided", [n.key], [], rule_ref="L", structure="non_palette_doc_type"))
    return issues


def validate_graph(g: Graph, policies: Mapping[str, Any]) -> list[dict]:
    """L0009 §2.5.1. Root binding issues are appended by the caller after ``order``."""
    if not g.nodes:
        return [issue("empty_graph")]
    issues: list[dict] = []
    keys = set(g.node_keys())
    for e in g.edges:
        if e.source not in keys or e.target not in keys:
            issues.append(issue("dangling_edge", [], [e.edge_id]))
    edges = g.live_edges()
    pairs: dict[tuple[str, str], list[str]] = {}
    for e in edges:
        pairs.setdefault((e.source, e.target), []).append(e.edge_id)
    for (s, t), ids in pairs.items():
        if len(ids) >= 2:
            issues.append(issue("duplicate_edge", sorted({s, t}), sorted(ids)))
    adjacency: dict[str, list[str]] = {}
    for e in edges:
        adjacency.setdefault(e.source, []).append(e.target)
    reach = _reachable(g.start_keys(), adjacency)
    for n in g.nodes:
        if n.key not in reach:
            issues.append(issue("unreachable_node", [n.key], []))
    issues.extend(_structural(g, policies))
    return order(issues)
