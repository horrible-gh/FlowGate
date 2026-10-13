"""Execution lifecycle port. The composition root supplies the v0.2 implementation."""
from __future__ import annotations

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
        if action in ("request_write", "working", "validate_write", "loop_state", "prepare_workspace", "admit", "admission", "opening", "can_open", "execution_wire"):
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
