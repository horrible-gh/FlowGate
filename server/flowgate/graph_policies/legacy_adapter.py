"""Reuse boundary of the Graph execution policies (D0006 §2.2 / §4.1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never changed:
the two policy registries of the Graph workflow and its system default (``domain.policies`` for the
validator, ``domain.run_policies`` for a run's snapshot), the validator's issue shape, the Graph model
types a slot implementation receives and ``PolicyUndecided`` — what a slot raises for a case D0004 §8 has
not answered (the run start refuses it as ``run_policy_undecided``).

A decided slot has to be known in three places, which ``register_policy`` fills together:

- ``run_policies.register``: a run stores ``(value, impl_version)`` per slot at start and later resolves
  the implementation from that snapshot only (L0009 §2.6.2).
- ``policies.register``: the validator (``graph_service.check_draft`` → ``validate_graph``) reads
  ``policies.current()``; a slot with an implementation there no longer reports ``rule_undecided``.
- ``policies.set_system_default``: the pair a new run snapshots (``policy_snapshot.policy_set_for_run``).

Both registries only add (L0009 §1.6 POLICY_IMPL_RETENTION): the same object again is a no-op, a different
object under a taken key is refused. There is no unregistration.
"""
from __future__ import annotations

from typing import Any

from modules.flow_gate.graph_workflow.domain import policies, run_policies
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge
from modules.flow_gate.graph_workflow.domain.graph_validator import issue
from modules.flow_gate.graph_workflow.domain.run_policies import PolicyUndecided

__all__ = ["Graph", "GraphEdge", "PolicyUndecided", "issue", "register_policy"]


def register_policy(key: str, value: str, impl_version: str, implementation: Any) -> None:
    """Make ``implementation`` the decided rule of slot ``key`` as ``(value, impl_version)``: registered for
    runs, registered for the validator, and the system default a new run snapshots. Idempotent."""
    run_policies.register(key, value, impl_version, implementation)
    policies.register(key, implementation)
    policies.set_system_default(key, value, impl_version)
