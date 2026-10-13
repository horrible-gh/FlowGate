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
        # Ordinary v0.1 callers have no Graph execution and retain their behavior.
        if action in ("launch", "finish", "transition") and args and args[0].get("graph_exec_id"):
            raise RuntimeError("Graph execution lifecycle is not registered")
        if action in ("request_write", "working", "validate_write", "loop_state", "prepare_workspace"):
            raise RuntimeError("Graph execution lifecycle is not registered")
        return None
    return _handler(action, *args, **kwargs)
