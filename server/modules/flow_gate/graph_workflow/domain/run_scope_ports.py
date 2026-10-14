"""Run scope injection boundary for v0.2 Graph execution.

Startup supplies flowgate.graph_validation.run_scope.run_scope. There is deliberately
no second scope algorithm: an unwired scope fails closed, before a run is written.
The dependency stays flowgate -> modules.flow_gate.
"""
from __future__ import annotations

from typing import Callable, Optional

from .graph_model import Graph

_handler: Optional[Callable] = None


def register(handler: Callable) -> None:
    global _handler
    if not callable(handler):
        raise TypeError("run scope handler must be callable")
    _handler = handler


def run_scope(graph: Graph):
    if _handler is None:
        raise RuntimeError("Graph run scope is not registered")
    return _handler(graph)
