"""Run scope of a Graph — flowgate.v02.0061 (0002-CH turns 7 / 13 / 15, rules 1-2).

A run follows edges from its start nodes, so what a run may reach is decided by the graph alone:

- a run start node is a start node (no incoming live edge, L0009 §2.0) whose document type is a root type
  (``R`` 요구사항 / ``B`` 버그, ``graph_model.ROOT_DOC_TYPES``) and that has at least one outgoing live edge: a
  root node with no edge at all is an isolated node (rule 1), not a start (turn 15 rule 3), so it neither
  counts for ``multiple_start`` nor is opened by a run;
- the run targets are the nodes reachable from a run start node along live edges, the start nodes included;
- every other node is left out: an isolated node (rule 1) and a path whose head is no root node (rule 2), as a
  whole — nothing behind a left-out node is reached either.

Not decided here (Q-1, ``start_node`` slot): what a graph with several run start nodes does. The start nodes
are reported as they are; ``validator`` leaves ``multiple_start`` to the slot, as before, and the slot's
implementation (``flowgate.graph_policies.start``) opens them through ``run_starts`` on a run's start.

Pure: no DB, HTTP or registry.
"""
from __future__ import annotations

from dataclasses import dataclass

from flowgate.graph_validation.legacy_adapter import ROOT_DOC_TYPES, Graph, reachable


@dataclass(frozen=True)
class RunScope:
    starts: tuple           # tuple[str, ...]: the run start nodes, stored order
    targets: frozenset      # frozenset[str]: the nodes a run may reach, the start nodes included
    excluded: tuple         # tuple[str, ...]: the nodes left out of a run, stored order

    def is_target(self, node_key: str) -> bool:
        return node_key in self.targets


def adjacency(graph: Graph) -> dict[str, list[str]]:
    """``source -> [target]`` over the live edges, every node a key, edges in stored order."""
    out: dict[str, list[str]] = {key: [] for key in graph.node_keys()}
    for edge in graph.live_edges():
        out[edge.source].append(edge.target)
    return out


def run_starts(graph: Graph) -> list[str]:
    """The run start nodes: start nodes (no incoming live edge) of a root document type with an outgoing live
    edge (an isolated root node is no start), stored order."""
    starts = set(graph.start_keys())
    out = adjacency(graph)
    return [n.key for n in graph.nodes
            if n.key in starts and out[n.key] and n.node_type == "document" and n.doc_type in ROOT_DOC_TYPES]


def run_scope(graph: Graph) -> RunScope:
    starts = run_starts(graph)
    targets = reachable(starts, adjacency(graph))
    return RunScope(
        starts=tuple(starts),
        targets=frozenset(targets),
        excluded=tuple(key for key in graph.node_keys() if key not in targets),
    )
