"""Registration of the Graph execution policies: how the Graph workflow gets its decided rules (D0006 §2.3).

The composition root (``startup.py``) calls :func:`register` once at startup. Start, fork, join,
completion, rejection_path, report_pair and gate_node are registered through ``legacy_adapter.register_policy``
(run registry, validator registry, system default). The shared run scope is wired at the same time.
Multiple starts remain undecided; ordinary linear/fork/join runs have all required slots.

flowgate.v02.0085 T#2 (D0007 §3.1): gate_node = ``human_confirm_v1`` (``gate``), the same implementation version in
the validator and the run registry. A new run snapshots it; a run already stored keeps its own snapshot.

The registries only add, so there is no ``unregister``; registering again is a no-op.
"""
from __future__ import annotations

from flowgate.graph_policies import completion, fork, gate, join, legacy_adapter, rejection_path, report_pair, start
from flowgate.graph_validation.legacy_adapter import register_scope
from flowgate.graph_validation.run_scope import run_scope


def register() -> None:
    register_scope(run_scope)
    slots = (start.SLOTS + fork.SLOTS + join.SLOTS + completion.SLOTS + rejection_path.SLOTS + report_pair.SLOTS
             + gate.SLOTS)
    for key, value, impl_version, implementation in slots:
        legacy_adapter.register_policy(key, value, impl_version, implementation)
