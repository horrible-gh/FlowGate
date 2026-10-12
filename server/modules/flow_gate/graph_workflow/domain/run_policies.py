"""Run policy implementations and the policies a revision needs (L0009 §2.6.2 / §2.6.3).

An implementation is registered under ``(key, value, impl_version)``; registration only adds
(L0009 §1.6 POLICY_IMPL_RETENTION). A run stores the nine ``(value, impl_version)`` pairs it
started with, and every later step rebuilds its policies from that snapshot only — never from
the current default (``policies_of``; an unknown triple is E-33, not a fallback).

The ``undecided`` implementation of every key is always registered: any call raises
``PolicyUndecided``. Slot contracts (inputs and outputs of L0009 §2.6.1) as the resolver calls them:

- ``start_node.select(revision, state) -> [node_key]``
- ``edge_condition.select(node_key, result, out_edges) -> [GraphEdge]``
- ``parallelism.admit(candidates, open_node_keys) -> [node_key]``
- ``join.openable(node_key, in_edge_states) -> "open" | "wait" | "never"``
- ``rejection_path.route(node_key, revision, state) -> (open [node_key], close [node_key])``
- ``completion.is_complete(revision, state) -> bool``
- ``gate_node.judge(node_key, decision, comment) -> "confirmed" | "rejected"`` (S6 gate decision)
- ``report_pair`` / ``custom_node``: slots only (Q-5 / Q-11)

flowgate.v02.0053 T#8: no revision needs ``custom_node`` any more. A Custom card runs as a plain document
card — a work instruction (``graph_model.run_doc_type``) — so it needs what an instruction card needs
(``report_pair``). The slot stays one of the nine a run snapshots (stored runs keep their entries).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from .graph_model import Graph, run_doc_type
from .policies import POLICY_KEYS, UNDECIDED, UNDECIDED_IMPL_VERSION

# L0009 §2.6.3 RULE_REF — the D0004 §8 question each slot waits on.
RULE_REF = {
    "start_node": "Q-1", "edge_condition": "Q-2", "parallelism": "Q-3", "join": "Q-4",
    "report_pair": "Q-5", "rejection_path": "Q-6", "completion": "Q-10",
    "gate_node": "Q-11", "custom_node": "Q-11",
}
# L0009 §2.0 지시 타입 (AUTO_REPORT_MAP keys).
INSTRUCTION_DOC_TYPES = frozenset({"N", "T", "T2", "TS"})
DONE_REVIEW_STATUSES = frozenset({"approved", "wf_done"})


class PolicyUndecided(Exception):
    """An undecided slot was asked to decide (L0009 §2.6.1). Callers refuse or roll back."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


class PolicySnapshotUnavailable(Exception):
    """The snapshot names an implementation that is not registered (L0009 §5 E-33)."""

    def __init__(self, key: str, entry: Mapping[str, Any]):
        super().__init__(f"{key}={entry.get('value')}@{entry.get('impl_version')}")
        self.key = key
        self.entry = dict(entry)


class UndecidedPolicy:
    """The ``undecided`` implementation: every slot method raises ``PolicyUndecided``."""

    def __init__(self, key: str):
        self.key = key

    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)

        def refuse(*_args, **_kwargs):
            raise PolicyUndecided(self.key)

        return refuse


_implementations: dict[tuple[str, str, str], Any] = {
    (key, UNDECIDED, UNDECIDED_IMPL_VERSION): UndecidedPolicy(key) for key in POLICY_KEYS
}


def register(key: str, value: str, impl_version: str, implementation: Any) -> None:
    """Add an implementation. A taken triple is never replaced (a change needs a new version)."""
    if key not in POLICY_KEYS:
        raise KeyError(key)
    slot = (key, value, impl_version)
    if slot in _implementations and _implementations[slot] is not implementation:
        raise ValueError(f"policy implementation already registered: {slot}")
    _implementations[slot] = implementation


def implementation(key: str, value: str, impl_version: str) -> Optional[Any]:
    return _implementations.get((key, value, impl_version))


def policies_of(entries: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """L0009 §2.6.2 ``policies_of``: the snapshot's nine entries -> implementations."""
    out = {}
    for key in POLICY_KEYS:
        entry = entries.get(key) or {}
        impl = implementation(key, str(entry.get("value")), str(entry.get("impl_version")))
        if impl is None:
            raise PolicySnapshotUnavailable(key, entry)
        out[key] = impl
    return out


def required_policies(revision: Graph, bound_node_key: Optional[str] = None,
                      bound_review_status: Optional[str] = None) -> set[str]:
    """L0009 §2.6.3 ``required_policies`` for one revision and its root binding."""
    req = {"start_node", "completion"}
    bound_done = bound_review_status in DONE_REVIEW_STATUSES
    if any(not (n.key == bound_node_key and bound_done) for n in revision.nodes):
        req.add("rejection_path")
    if any((run_doc_type(n.node_type, n.doc_type) or "").upper() in INSTRUCTION_DOC_TYPES for n in revision.nodes):
        req.add("report_pair")
    live = revision.live_edges()
    out_degree: dict[str, int] = {}
    for e in live:
        out_degree[e.source] = out_degree.get(e.source, 0) + 1
    if any(d >= 2 for d in out_degree.values()) or any(e.condition is not None for e in revision.edges):
        req |= {"edge_condition", "parallelism"}
    if any(d >= 2 for d in revision.in_degree().values()):
        req.add("join")
    if any(n.node_type == "gate" for n in revision.nodes):
        req.add("gate_node")
    return req


def _rule_number(key: str) -> int:
    return int(RULE_REF[key].split("-")[1])


def undecided_for(required: set[str], entries: Mapping[str, Mapping[str, Any]]) -> list[dict]:
    """L0009 §2.6.3 ``undecided_for``: P0008 S5-1 ``undecided_policies``, by question number."""
    missing = [k for k in required if (entries.get(k) or {}).get("value") == UNDECIDED]
    missing.sort(key=lambda k: (_rule_number(k), POLICY_KEYS.index(k)))
    return [{"policy": k, "rule_ref": RULE_REF[k]} for k in missing]
