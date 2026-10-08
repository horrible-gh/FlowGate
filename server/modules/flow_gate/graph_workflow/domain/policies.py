"""Execution policy slots (D0004 §2.2 실행 정책 묶음, L0009 §2.6.1) — names only, no rules.

No question of D0004 §8 that a slot depends on has been answered, so every slot is
``undecided``: the validator reports the structures that need it as ``rule_undecided`` and
nothing here decides whether they are allowed. A slot is filled by registering an object with a
``validate(graph, occurrence) -> issues`` method under its key once L0009 fixes the rule.
"""
from __future__ import annotations

from typing import Any

UNDECIDED = "undecided"

# L0009 §2.6.1 — the nine slot keys, in table order.
POLICY_KEYS = (
    "start_node", "edge_condition", "parallelism", "join", "report_pair",
    "rejection_path", "completion", "gate_node", "custom_node",
)
# P0008 §5.2 ``GET /graph`` ``policies``: the first seven (gate/custom only appear once runs exist).
GRAPH_POLICY_KEYS = POLICY_KEYS[:7]

_registered: dict[str, Any] = {}


def current() -> dict[str, Any]:
    """The system default policy set: every slot ``undecided`` unless a rule was registered."""
    return {key: _registered.get(key, UNDECIDED) for key in POLICY_KEYS}


def policy_states() -> dict[str, str]:
    """P0008 §5.2 ``policies`` object: ``"undecided"`` or the registered policy's key."""
    values = current()
    return {key: values[key] if isinstance(values[key], str) else getattr(values[key], "key", key)
            for key in GRAPH_POLICY_KEYS}
