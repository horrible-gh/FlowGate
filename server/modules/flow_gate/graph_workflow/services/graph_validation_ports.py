"""Graph validation port (flowgate.v02.0061, D0006 §2.1 / §2.3 / §4.1).

What a draft must look like before it is confirmed — and so before any run of it — has decided rules since 0061
(0002-CH turn 13): an isolated node and a path not starting from a root node (R / B) are no error but left out
of a run, a left-out path joining the run path is refused, a cycle is refused, a branch and an AND join are
allowed. Those rules are a v0.2 feature (``flowgate.graph_validation``), and ``modules.flow_gate`` never imports
``flowgate``, so this module offers the port ``graph_service.check_draft`` validates through and the composition
root (``startup.py``) registers the handler.

    validate    draft validation and confirmation, inside their Flow guard: ``(graph, policies) -> issues`` in
                the shape of ``graph_validator.issue``, ordered; root binding issues are appended by the caller

* With no handler registered ``validate`` runs the Graph workflow's own ``graph_validator.validate_graph``,
  exactly as before the port existed.
* A handler exception propagates: the caller runs inside a Flow guard and the draft is neither confirmed nor
  reported valid.
* One handler. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Mapping, Optional

from modules.flow_gate.graph_workflow.domain.graph_model import Graph
from modules.flow_gate.graph_workflow.domain.graph_validator import validate_graph

VALIDATE = "validate"
PORT_NAMES = (VALIDATE,)

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in PORT_NAMES:
        raise ValueError(f"unknown graph validation port: {name}")
    if not callable(handler):
        raise TypeError(f"graph validation port {name} handler is not callable")
    with _lock:
        _handlers[name] = handler


def unregister(name: str) -> None:
    with _lock:
        _handlers.pop(name, None)


def registered(name: str) -> bool:
    with _lock:
        return name in _handlers


def _handler(name: str) -> Optional[Callable[..., Any]]:
    with _lock:
        return _handlers.get(name)


def validate(graph: Graph, policies: Mapping[str, Any]) -> list[dict]:
    """``policies``: the validator's policy set (``policies.current()``). Ordered issues, binding issues not included."""
    handler = _handler(VALIDATE)
    if handler is None:
        return validate_graph(graph, policies)
    return list(handler(graph, policies))
