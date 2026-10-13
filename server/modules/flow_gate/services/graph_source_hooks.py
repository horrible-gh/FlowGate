"""Attempt source port; unmapped v0.1 tokens keep their original routing."""
from __future__ import annotations

from fastapi import HTTPException
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.api.graph_messages import SOURCE_MESSAGES

_handler = None
_mutation_handler = None


class GraphSourceError(HTTPException):
    def __init__(self, code, *, locale="ko", **details):
        messages = SOURCE_MESSAGES.get(code, {})
        message = messages.get(locale) or messages.get("ko") or code
        self.code, self.message, self.details = code, message, details
        super().__init__(409, {"code": code, "message": message, **details})


def register(handler, mutation_handler):
    global _handler, _mutation_handler
    _handler, _mutation_handler = handler, mutation_handler


def unregister():
    global _handler, _mutation_handler
    _handler = _mutation_handler = None


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


def mutation(token):
    attempt_id = binding(token)
    if not attempt_id or _mutation_handler is None:
        raise GraphSourceError("graph_worktree_unavailable")
    return _mutation_handler(attempt_id, project_id=token.get("project"), group_id=token.get("group_id"))


def reject_group_source(token, operation):
    if binding(token):
        # Gives document-only attempts the same Git-required refusal as remote CRUD.
        source_root(token)
        raise GraphSourceError("graph_attempt_source_unsupported", operation=operation)
