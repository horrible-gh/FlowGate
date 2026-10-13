"""Fork (branch) execution policies — flowgate.v02.0061 (R §2.4 분기, 0002-CH turns 3 / 7 / 9 / 11).

Two of the nine slots (L0009 §2.6.1) get a decided implementation here, both for the structure the
validator reports as ``branch`` (several outgoing edges from one node):

- ``edge_condition`` = ``fork_all``: every live outgoing edge of an approved node is followed. There is
  no IF branch (0002-CH turn 3, Q5), so an edge ``condition`` is never interpreted; a conditioned edge
  keeps ``rule_undecided/edge_condition`` at confirmation.
- ``parallelism`` = ``unbounded``: every target the branch reaches is admitted at once; nothing is held
  back by the number of nodes already open. This is logical opening only. The actual concurrent AI
  execution limit (default 4 per Flow, configurable) is applied in T#6, not by this policy.

What the resolver (``frontier.propagate``) already does is relied on, not repeated: the targets are
collected as a set (one node reached by two edges opens once), ``openable_only`` opens only a ``pending``
node not chosen before in the same decision (``open_decided``), and each node's approval propagates along
its own out-edges only, so two branches progress independently of each other. A target with several
incoming edges is asked of the ``join`` slot first (``join.AndJoin``), so a branch that later rejoins
waits there for its other branches.

Pure: no DB, HTTP or registry. ``SLOTS`` is what ``integration.register`` registers.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from flowgate.graph_policies.legacy_adapter import Graph, GraphEdge, issue

FORK_ALL = "fork_all"
UNBOUNDED = "unbounded"
IMPL_VERSION = "1"

CONDITIONED_EDGE = "edge_condition"          # the validator's structure name of an edge with a condition
RULE_REF = "Q-2"                             # D0004 §8: the question a conditioned edge still waits on


class ForkAllEdges:
    """``edge_condition`` slot: an approved node is followed along all of its live outgoing edges."""

    key = FORK_ALL

    def select(self, _node_key: str, _result: Mapping[str, Any],
               out_edges: Iterable[GraphEdge]) -> list[GraphEdge]:
        return list(out_edges)                                   # stored order; conditions are not read

    def validate(self, _graph: Graph, occurrence: Mapping[str, Any]) -> list[dict]:
        if occurrence.get("structure") == CONDITIONED_EDGE:      # Q5: no IF branch in this scope
            return [issue("rule_undecided", occurrence.get("node_keys", ()), occurrence.get("edge_ids", ()),
                          rule_ref=RULE_REF, structure=CONDITIONED_EDGE)]
        return []                                                # ``branch``: allowed


class UnboundedParallelism:
    """``parallelism`` slot: every candidate a branch reaches is admitted, whatever is open already."""

    key = UNBOUNDED

    def admit(self, candidates: Iterable[str], _open_node_keys: Iterable[str]) -> list[str]:
        return list(dict.fromkeys(candidates))                   # order kept, no repeats

    def validate(self, _graph: Graph, _occurrence: Mapping[str, Any]) -> list[dict]:
        return []


FORK_ALL_EDGES = ForkAllEdges()
UNBOUNDED_PARALLELISM = UnboundedParallelism()

# (slot key, policy value, impl_version, implementation)
SLOTS = (
    ("edge_condition", FORK_ALL, IMPL_VERSION, FORK_ALL_EDGES),
    ("parallelism", UNBOUNDED, IMPL_VERSION, UNBOUNDED_PARALLELISM),
)
