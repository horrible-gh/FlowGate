"""AND join execution policy — flowgate.v02.0061 (R §2.5 합류, 0002-CH turns 3 / 11).

The ``join`` slot (L0009 §2.6.1, D0004 §8 Q-4) gets its decided implementation here, for the structure the
validator reports as ``join`` (several incoming edges into one node):

- ``join`` = ``and_all``: the node opens only when every live incoming edge is ``completed`` (its source
  node approved, ``done``). While any predecessor is still to run or in review the answer is ``wait``: the
  node stays ``pending`` and nothing is opened for it.

How "once, after the last predecessor" comes out of the resolver (``frontier.propagate``), which is not
changed: a predecessor's approval propagates along its out-edges and asks this slot for every target with
two or more incoming edges, with the state that already includes that approval. Every approval but the last
gets ``wait``, so the target is not a candidate; the last one sees all edges ``completed`` and gets ``open``.
``openable_only`` then opens the target only while it is still ``pending`` and not chosen before in the same
decision (``open_decided``), so two approvals drained together on resume, or two edges from one fork landing
on the same node, open it once. A branch opened by ``fork.ForkAllEdges`` reaches a join the same way, one
predecessor at a time.

``never`` is not returned: an edge whose source was rejected or closed (``inactive``) is treated as not yet
complete. The rejection_path ``stop_path`` policy leaves descendants pending and opens nothing;
a rejected root may still be revised and approved under its existing contract. A join on that path
cannot open while any predecessor remains rejected or incomplete.

Pure: no DB, HTTP or registry. ``SLOTS`` is what ``integration.register`` registers.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from flowgate.graph_policies.legacy_adapter import Graph

AND_ALL = "and_all"
IMPL_VERSION = "1"

# ``frontier.in_edge_states`` edge states and the slot's answers (``run_policies`` slot contract).
COMPLETED = "completed"
OPEN = "open"
WAIT = "wait"


class AndJoin:
    """``join`` slot: a node with several incoming edges opens once every one of them is ``completed``."""

    key = AND_ALL

    def openable(self, _node_key: str, in_edge_states: Iterable[Mapping[str, Any]]) -> str:
        if all(edge.get("state") == COMPLETED for edge in in_edge_states):
            return OPEN
        return WAIT

    def validate(self, _graph: Graph, _occurrence: Mapping[str, Any]) -> list[dict]:
        return []                                                # ``join``: allowed


AND_JOIN = AndJoin()

# (slot key, policy value, impl_version, implementation)
SLOTS = (
    ("join", AND_ALL, IMPL_VERSION, AND_JOIN),
)
