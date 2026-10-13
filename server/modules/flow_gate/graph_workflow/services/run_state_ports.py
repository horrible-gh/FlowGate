"""Run state ports (flowgate.v02.0061 T#2, D0006 §2.1 / §2.3).

Whether a Graph run goes on after a step — running, idle with its reason, or the candidates its command may still
open — and what a step / full command records are v0.2 rules: they live in the application package
``flowgate.graph_run``. ``modules.flow_gate`` never imports ``flowgate``, so this module offers named ports the run
engine calls and the composition root (``startup.py``) registers the handlers.

    settle               inside a step's Flow guard, after its decision (``run_apply.settle_run``): a running run
                         with no work going on opens what its command may still open, else goes idle with its
                         reason; an idle run whose existing work goes on again is running; an idle run's reason
                         follows its nodes (``ctx`` -> None)
    settle_plan          what ``settle`` would do now, nothing written (``ctx`` -> a plan or None): a run read asks
                         it before it takes the Flow guard
    command_of           a paused / idle run goes on: the new command's mode, or None when a paused run goes on
                         with its own command (``run, mode`` -> ``"step" | "full" | None``)
    record_command       the running run's new command, before its run status event (``ctx, mode`` -> None)
    record_step_targets  a command's decision: a step's targets are the nodes it opens (``ctx, mode, decision``)

* No handler registered: the port raises (fails closed, as ``run_scope_ports``) — a run step rolls back instead of
  inventing a run state.
* A handler exception propagates: the callers run inside a run step's Flow guard or a run read.
* One handler per port. Registering again replaces it (startup may run more than once in a process).
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

SETTLE = "settle"
SETTLE_PLAN = "settle_plan"
COMMAND_OF = "command_of"
RECORD_COMMAND = "record_command"
RECORD_STEP_TARGETS = "record_step_targets"
PORT_NAMES = (SETTLE, SETTLE_PLAN, COMMAND_OF, RECORD_COMMAND, RECORD_STEP_TARGETS)

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in PORT_NAMES:
        raise ValueError(f"unknown run state port: {name}")
    if not callable(handler):
        raise TypeError(f"run state port {name} handler is not callable")
    with _lock:
        _handlers[name] = handler


def unregister(name: str) -> None:
    with _lock:
        _handlers.pop(name, None)


def registered(name: str) -> bool:
    with _lock:
        return name in _handlers


def _handler(name: str) -> Callable[..., Any]:
    with _lock:
        handler = _handlers.get(name)
    if handler is None:
        raise RuntimeError(f"Graph run state port {name} is not registered")
    return handler


def settle(ctx: Any) -> None:
    _handler(SETTLE)(ctx)


def settle_plan(ctx: Any) -> Optional[tuple]:
    return _handler(SETTLE_PLAN)(ctx)


def command_of(run: dict, mode: Optional[str]) -> Optional[str]:
    return _handler(COMMAND_OF)(run, mode)


def record_command(ctx: Any, mode: str) -> None:
    _handler(RECORD_COMMAND)(ctx, mode)


def record_step_targets(ctx: Any, mode: str, decision: Any) -> None:
    _handler(RECORD_STEP_TARGETS)(ctx, mode, decision)
