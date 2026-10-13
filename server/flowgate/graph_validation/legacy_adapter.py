"""Reuse boundary of the Graph pre-run validation (D0006 §2.2 / §4.1).

Everything this package takes from ``modules.flow_gate`` goes through here, called only — never changed: the
Graph model and its root document types; the pieces of the Graph workflow's validator this package composes —
``issue`` / ``order`` (the issue shape and the L0009 §2.5.2 order), ``well_formed_issues`` (dangling and
duplicate edges), ``reachable`` / ``strongly_connected`` (graph walks), ``structural_issues`` (the structures
put to the policy slots, with the start nodes given and the cycle occurrence left out); and the validation
port ``services.graph_validation_ports`` the handler is registered at.
"""
from __future__ import annotations

from typing import Callable

from modules.flow_gate.graph_workflow.domain.graph_model import ROOT_DOC_TYPES, Graph, GraphEdge
from modules.flow_gate.graph_workflow.domain.graph_validator import (
    issue,
    order,
    reachable,
    strongly_connected,
    structural_issues,
    well_formed_issues,
)
from modules.flow_gate.graph_workflow.services import graph_validation_ports as ports
from modules.flow_gate.graph_workflow.domain.run_scope_ports import register as register_scope

__all__ = [
    "ROOT_DOC_TYPES", "Graph", "GraphEdge", "issue", "order", "reachable", "strongly_connected",
    "structural_issues", "well_formed_issues", "register_validator", "unregister_validator", "register_scope",
]


def register_validator(handler: Callable) -> None:
    """Make ``handler(graph, policies) -> issues`` what ``graph_service.check_draft`` validates with."""
    ports.register(ports.VALIDATE, handler)


def unregister_validator() -> None:
    ports.unregister(ports.VALIDATE)
