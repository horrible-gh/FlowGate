"""Pre-run validation of a Graph draft — flowgate.v02.0061 (0002-CH turn 13, rules 1-5).

``validate_graph(graph, policies)`` is what ``graph_validation_ports.validate`` runs in place of the Graph
workflow's ``graph_validator.validate_graph``: the same issue shape and order, the same well-formedness checks
(``empty_graph``, ``dangling_edge``, ``duplicate_edge``) and the same structures put to the policy slots, with
these differences:

- no ``unreachable_node``: a node a run does not reach is no error. An isolated node (rule 1) and a path not
  starting from a root node (rule 2) are left out of the run (``run_scope``), silently;
- ``excluded_path_join`` (rule 3): a live edge from a left-out node into a run target. One issue per such edge,
  naming its two nodes and the edge. The run path must not wait on a node that never runs (``join`` =
  ``and_all`` would wait for it forever);
- ``cycle`` (rule 4): every strongly connected component with an edge inside, anywhere in the graph, is an
  error — no longer an occurrence of the ``rejection_path`` slot (``rule_undecided/cycle``);
- the ``multiple_start`` occurrence counts the run start nodes only (root nodes without an incoming edge and
  with an outgoing one), not every node without an incoming edge: the head of a left-out path and an isolated
  root node are no start nodes. Two or more run start nodes are still put to the ``start_node`` slot (Q-1,
  undecided — ``flowgate.graph_policies.start`` keeps them ``rule_undecided``), not decided here;
- a branch and a join (rule 5) are put to their slots as before: allowed once ``flowgate.graph_policies`` is
  registered, ``rule_undecided`` otherwise.

Every other structure (a conditioned edge, a report document node, a gate, a non-Palette document type) is
checked over the whole graph as before, left-out nodes included.

Pure: no DB, HTTP or registry.
"""
from __future__ import annotations

from typing import Any, Mapping

from flowgate.graph_validation.legacy_adapter import (
    Graph,
    issue,
    order,
    strongly_connected,
    structural_issues,
    well_formed_issues,
)
from flowgate.graph_validation.run_scope import RunScope, adjacency, run_scope

EMPTY_GRAPH = "empty_graph"
EXCLUDED_PATH_JOIN = "excluded_path_join"
CYCLE = "cycle"


def excluded_join_issues(graph: Graph, scope: RunScope) -> list[dict]:
    """Rule 3: a live edge from a node left out of the run into a run target, one issue per edge."""
    return [issue(EXCLUDED_PATH_JOIN, [edge.source, edge.target], [edge.edge_id])
            for edge in graph.live_edges()
            if edge.target in scope.targets and edge.source not in scope.targets]


def cycle_issues(graph: Graph) -> list[dict]:
    """Rule 4: one issue per cycle (a strongly connected component with an edge inside): its nodes and edges."""
    edges = graph.live_edges()
    out: list[dict] = []
    for comp in strongly_connected(graph.node_keys(), adjacency(graph)):
        members = set(comp)
        inside = sorted(e.edge_id for e in edges if e.source in members and e.target in members)
        if len(comp) >= 2 or inside:
            out.append(issue(CYCLE, comp, inside))
    return out


def validate_graph(graph: Graph, policies: Mapping[str, Any]) -> list[dict]:
    """Rules 1-5 over ``graph`` with the validator's policy set, ordered like ``graph_validator.validate_graph``.
    Root binding issues are appended by the caller."""
    if not graph.nodes:
        return [issue(EMPTY_GRAPH)]
    scope = run_scope(graph)
    issues = well_formed_issues(graph)
    issues.extend(excluded_join_issues(graph, scope))
    issues.extend(cycle_issues(graph))
    issues.extend(structural_issues(graph, policies, starts=list(scope.starts), cycle=False))
    return order(issues)
