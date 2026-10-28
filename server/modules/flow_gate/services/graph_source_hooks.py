"""Attempt source port; unmapped v0.1 tokens keep their original routing."""
from __future__ import annotations

import contextlib
import threading

from fastapi import HTTPException
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.api.graph_messages import SOURCE_MESSAGES
from modules.flow_gate.services.graph_execution_hooks import ExecutionLeaseLost

_handler = None
_mutation_handler = None
_fence_handler = None
_session = threading.local()


class GraphSourceError(HTTPException):
    def __init__(self, code, *, locale="ko", **details):
        messages = SOURCE_MESSAGES.get(code, {})
        message = messages.get(locale) or messages.get("ko") or code
        self.code, self.message, self.details = code, message, details
        super().__init__(409, {"code": code, "message": message, **details})


class GraphAttemptInactive(GraphSourceError, ExecutionLeaseLost):
    """0061 T#7: an attempt token's write or result arrived when its run is stopping or its attempt / execution no
    longer owns it (409 ``graph_attempt_inactive``). Also an ``ExecutionLeaseLost`` for the internal Graph paths."""

    def __init__(self, *, locale="ko", **details):
        GraphSourceError.__init__(self, "graph_attempt_inactive", locale=locale, **details)


def register(handler, mutation_handler, fence_handler=None):
    global _handler, _mutation_handler, _fence_handler
    _handler, _mutation_handler, _fence_handler = handler, mutation_handler, fence_handler


def unregister():
    global _handler, _mutation_handler, _fence_handler
    _handler = _mutation_handler = _fence_handler = None


def binding(token):
    token = token or {}
    if token.get("_is_user_jwt") or not token.get("token_id"):
        return None
    if not get_store().table_exists("graph_node_attempts"):
        return None
    row = run_store._one(
        "SELECT attempt_id FROM graph_run_token_map WHERE token_id = ?", [token["token_id"]])
    return row["attempt_id"] if row and row.get("attempt_id") else None


def source_root(token, *, project_id=None, group_id=None):
    attempt_id = binding(token)
    if not attempt_id:
        return None
    if _handler is None:
        raise GraphSourceError("graph_worktree_unavailable", attempt_id=attempt_id)
    root = _handler(attempt_id, project_id=project_id or token.get("project"),
                    group_id=group_id or token.get("group_id"),
                    locale=token.get("continuation_locale") or "ko")
    if root is None:
        raise GraphSourceError("graph_worktree_unavailable", attempt_id=attempt_id)
    return root


def attempt_scope(token, *, project_id=None, group_id=None):
    """0110 T#2 (NR0003 §2 T-A): what a mapped token's work is measured against — the attempt's validated tree and
    its ``input_commit``. ``source_root`` has already held the marker's ``input_commit`` equal to the attempt row's,
    so for a Join this is the marker's final input (its merged results or the integration head), not a predecessor's.
    ``None`` for an unmapped v0.1 token, which keeps the group worktree. Raises ``GraphSourceError``; an attempt that
    cannot be resolved never falls back to the group worktree."""
    attempt_id = binding(token)
    if not attempt_id:
        return None
    root = source_root(token, project_id=project_id, group_id=group_id)
    row = run_store._one(
        "SELECT input_commit, input_source FROM graph_node_attempts WHERE attempt_id = ?", [attempt_id])
    if not row or not row.get("input_commit"):
        raise GraphSourceError("graph_worktree_unavailable", attempt_id=attempt_id, reason="input_missing")
    return {"attempt_id": attempt_id, "root": root, "input_commit": row["input_commit"],
            "input_source": row.get("input_source")}


def mutation(token):
    attempt_id = binding(token)
    if not attempt_id or _mutation_handler is None:
        raise GraphSourceError("graph_worktree_unavailable")
    return _mutation_handler(attempt_id, project_id=token.get("project"), group_id=token.get("group_id"))


@contextlib.contextmanager
def write_session():
    """0061 T#7: the span of an attempt token's side effects (a document registration and its commit, a chat
    command's creation or spawn). A ``require_writable`` inside it keeps the Stop gate's shared hold it was judged
    under until the span ends, so no ``stopping`` commits between the judgement and the side effects."""
    previous = getattr(_session, "stack", None)
    with contextlib.ExitStack() as stack:
        _session.stack = stack
        try:
            yield
        finally:
            _session.stack = previous


def require_writable(token, operation, *, report=False):
    """0061 T#7: an attempt token may still write or register a result (``report``: an execution's own report, a
    review verdict). Raises ``GraphAttemptInactive``; an unmapped v0.1 token, or no handler, passes. Inside a
    ``write_session`` the judgement's hold is kept for the session."""
    if _fence_handler is None or not binding(token):
        return
    _fence_handler(token, operation, report=report, keep=getattr(_session, "stack", None))


def reject_group_source(token, operation):
    if binding(token):
        # Gives document-only attempts the same Git-required refusal as remote CRUD.
        source_root(token)
        raise GraphSourceError("graph_attempt_source_unsupported", operation=operation)
