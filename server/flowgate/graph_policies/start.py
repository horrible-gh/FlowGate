"""Start node execution policy — flowgate.v02.0061 (R §2.1 시작점, 0002-CH turns 3 / 7 / 13 / 15).

The ``start_node`` slot (L0009 §2.6.1, D0004 §8 Q-1) gets its decided implementation here, for what a run
opens on its ``start`` control event:

- ``start_node`` = ``root_start``: the run start nodes of ``flowgate.graph_validation.run_scope`` — a requirement
  / bug node (``R`` / ``B``) with no incoming live edge and at least one outgoing live edge. A root node with no
  edge at all is an isolated node (rule 1) and is not a start (turn 15 rule 3); a root node with an incoming edge
  is no start either (L0009 §2.0), so the path through it is left out as a whole (rule 2).
- A path the start nodes do not reach is never opened (turn 15 rule 4): the resolver (``frontier``) opens only
  what ``select`` returns and what an approval propagates to along its out-edges, so nothing reaches a node the
  run scope leaves out. New runs do not create node run rows for left-out nodes.
- The root-bound start node (``run_service._root_bound_start``) is already ``done`` / ``review`` when ``select``
  is asked; ``openable_only`` skips it and its recorded propagation is drained by the same ``start`` decision,
  as before. A root start node that is not bound is ``pending`` and is opened like any other node.

Not decided (Q-1): a revision with two or more run start nodes. ``select`` raises ``PolicyUndecided`` for it,
which the run start refuses as ``run_policy_undecided`` (Q-1) — the same answer the undecided slot gave — and
``validate`` keeps reporting the ``multiple_start`` occurrence as ``rule_undecided`` (Q-1), so such a draft is
still refused at confirmation. A revision with no run start node opens nothing (``[]``): the validator passes
it (turn 14 constraint 3), the run start is not refused here.

Pure: no DB, HTTP or registry. ``SLOTS`` is what ``integration.register`` registers.
"""
from __future__ import annotations

from typing import Any, Mapping

from flowgate.graph_policies.legacy_adapter import Graph, PolicyUndecided, issue
from flowgate.graph_validation.run_scope import run_starts

ROOT_START = "root_start"
IMPL_VERSION = "1"

MULTIPLE_START = "multiple_start"             # the validator's structure name of two or more start nodes
RULE_REF = "Q-1"                              # D0004 §8: the question several start nodes still wait on
SLOT_KEY = "start_node"


class RootStart:
    """``start_node`` slot: a run starts at the requirement / bug nodes of ``run_scope.run_starts``."""

    key = ROOT_START

    def select(self, revision: Graph, _state: Any) -> list[str]:
        starts = run_starts(revision)
        if len(starts) >= 2:                                     # Q-1: not decided, refuse the start
            raise PolicyUndecided(SLOT_KEY)
        return starts                                            # stored order; [] opens nothing

    def validate(self, _graph: Graph, occurrence: Mapping[str, Any]) -> list[dict]:
        if occurrence.get("structure") == MULTIPLE_START:        # Q-1: still undecided at confirmation
            return [issue("rule_undecided", occurrence.get("node_keys", ()), occurrence.get("edge_ids", ()),
                          rule_ref=RULE_REF, structure=MULTIPLE_START)]
        return []


ROOT_START_NODE = RootStart()

# (slot key, policy value, impl_version, implementation)
SLOTS = (
    (SLOT_KEY, ROOT_START, IMPL_VERSION, ROOT_START_NODE),
)
