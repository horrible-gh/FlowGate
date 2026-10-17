"""Execution lifecycle port. The composition root supplies the v0.2 implementation."""
from __future__ import annotations

import contextlib
import threading
from contextvars import ContextVar

# flowgate.v02.0061 T#7 (0017-TR question 439): the span of a person's explicit AI request (the AI invoke start and
# [이어서 진행] routes). A review / rework hop requested inside it is the person's explicit review request, which the
# Graph also takes on an idle run; one requested anywhere else (an AI run's own chain, the card's review stage, a
# replay) is not. Bound to the request's own thread: never taken by an AI run's worker thread, even one that would
# inherit the context.
_explicit: ContextVar = ContextVar("graph_explicit_request", default=None)

class ExecutionLeaseLost(RuntimeError):
    """A Graph execution no longer owns the right to run or mutate."""


_handler = None


def register(handler):
    global _handler
    _handler = handler


def unregister():
    global _handler
    _handler = None


def call(action, *args, **kwargs):
    if _handler is None:
        if action == "legacy_acquire":
            return args[0](**kwargs)
        # Ordinary v0.1 callers have no Graph execution and retain their behavior.
        if action in ("launch", "finish", "transition") and args and args[0].get("graph_exec_id"):
            raise RuntimeError("Graph execution lifecycle is not registered")
        # 0066 D#1 §3-6(3): a manual merge fails closed when it cannot ask the Graph.
        if action in ("request_write", "working", "validate_write", "loop_state", "prepare_workspace", "admit", "admission", "opening", "can_open", "execution_wire", "manual_git_blocker"):
            raise RuntimeError("Graph execution lifecycle is not registered")
        return None
    return _handler(action, *args, **kwargs)


def admission(function):
    """Graph-only cleanup wrapper; retain the public start_run signature."""
    from functools import wraps
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not kwargs.get("graph_exec_id"):
            return function(*args, **kwargs)
        return call("admission", lambda: function(*args, **kwargs))
    return wrapped


@contextlib.contextmanager
def explicit_request():
    """flowgate.v02.0061 T#7: the span of a person's explicit AI request (see ``_explicit``)."""
    marker = _explicit.set(threading.get_ident())
    try:
        yield
    finally:
        _explicit.reset(marker)


def explicit() -> bool:
    return _explicit.get() == threading.get_ident()
