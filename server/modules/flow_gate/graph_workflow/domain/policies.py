"""Execution policy slots (D0004 §2.2 실행 정책 묶음, L0009 §2.6.1) — names only, no rules.

No question of D0004 §8 that a slot depends on has been answered, so every slot is
``undecided``: the validator reports the structures that need it as ``rule_undecided`` and
nothing here decides whether they are allowed. A slot is filled by registering an object with a
``validate(graph, occurrence) -> issues`` method under its key once L0009 fixes the rule.

The run side (L0009 §2.6.2) keeps the system default policy set: per key the policy value and
the implementation version a run snapshots at start. Both stay ``undecided`` until §8 answers;
``set_system_default`` is the registration point for a decided value.

flowgate.v02.0061: ``register`` fills a slot for the validator side (``_registered``);
``flowgate.graph_policies.integration.register`` (startup) is what uses it, with the decided rules of
``start_node`` (the start), ``edge_condition`` / ``parallelism`` (a branch) and ``join`` (a join).
"""
from __future__ import annotations

from typing import Any

UNDECIDED = "undecided"
UNDECIDED_IMPL_VERSION = "undecided.1"

# L0009 §2.6.1 — the nine slot keys, in table order. flowgate.v02.0053 T#8: ``custom_node`` stays a slot (a run
# snapshots all nine) but no structure needs it: a Custom card is a plain document card.
POLICY_KEYS = (
    "start_node", "edge_condition", "parallelism", "join", "report_pair",
    "rejection_path", "completion", "gate_node", "custom_node",
)
# P0008 §5.2 ``GET /graph`` ``policies``: the first seven (gate/custom only appear once runs exist).
GRAPH_POLICY_KEYS = POLICY_KEYS[:7]

# DB0010 DB-8: no per-Flow policy set is stored yet; every Flow resolves to this one.
SYSTEM_SET_SOURCE = "system_default"
SYSTEM_SET_ID = "system_default"
SYSTEM_SET_VERSION = "1"

_registered: dict[str, Any] = {}
_system_default: dict[str, tuple[str, str]] = {key: (UNDECIDED, UNDECIDED_IMPL_VERSION) for key in POLICY_KEYS}


def register(key: str, implementation: Any) -> None:
    """Fill a slot for the validator: ``implementation.validate(graph, occurrence) -> issues`` decides the
    structures the slot governs, and its ``key`` attribute is what ``policy_states`` reports. Registering the
    same object again is a no-op; a different object is refused (a change is a new slot value)."""
    if key not in POLICY_KEYS:
        raise KeyError(key)
    taken = _registered.get(key)
    if taken is not None and taken is not implementation:
        raise ValueError(f"policy already registered for slot {key}")
    _registered[key] = implementation


def current() -> dict[str, Any]:
    """The system default policy set: every slot ``undecided`` unless a rule was registered."""
    return {key: _registered.get(key, UNDECIDED) for key in POLICY_KEYS}


def policy_states() -> dict[str, str]:
    """P0008 §5.2 ``policies`` object: ``"undecided"`` or the decided policy's key."""
    values = current()
    out = {}
    for key in GRAPH_POLICY_KEYS:
        value = values[key]
        if not isinstance(value, str):
            out[key] = getattr(value, "key", key)
        else:
            out[key] = _system_default[key][0]
    return out


def system_default_set() -> dict:
    """L0009 §2.6.2 ``policy_registry.resolve(flow_id)`` — today always the system default."""
    return {
        "source": SYSTEM_SET_SOURCE,
        "set_id": SYSTEM_SET_ID,
        "set_version": SYSTEM_SET_VERSION,
        "entries": {key: {"value": value, "impl_version": version}
                    for key, (value, version) in _system_default.items()},
    }


def set_system_default(key: str, value: str, impl_version: str) -> tuple[str, str]:
    """Point the system default of ``key`` at a registered implementation. Returns the old pair."""
    if key not in POLICY_KEYS:
        raise KeyError(key)
    old = _system_default[key]
    _system_default[key] = (value, impl_version)
    return old
