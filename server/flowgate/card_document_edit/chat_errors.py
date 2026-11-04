"""Chat transport errors and the tool-result shape (L#3 §2.3·§2.4, P#2 §6·§5.2).

Codes and params are P#2 §6 verbatim; no new code names. An API chat tool result is
``(status, body)`` where ``body`` is the very JSON the CLI HTTP route answers: both are
produced by the same Graph envelope (``legacy_adapter.run``), so ``status`` is the HTTP
status and the body carries ``ok``/``server_ts`` and, on failure, ``error.code/message/params``.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from .errors import GraphApiError, invalid

FORBIDDEN = "chat_document_forbidden"
RUN_CANCELLED = "chat_document_run_cancelled"
RUN_FINISHED = "chat_document_run_finished"
EDIT_MODE_DENIED = "chat_document_edit_mode_denied"
UNAUTHORIZED = "unauthorized"
CALL_IDENTITY_UNAVAILABLE = "call_identity_unavailable"
REQUIRED_KIND = "read_write"
EFFECTIVE_KINDS = ("read", "none")


def unauthorized(message: Optional[str] = None) -> GraphApiError:
    """401, params ``{}``; ``message`` keeps an existing authentication failure text."""
    return GraphApiError(401, UNAUTHORIZED, {}, message=message)


def forbidden() -> GraphApiError:
    """403, params ``{}``: binding failures and project mismatch. No document facts."""
    return GraphApiError(403, FORBIDDEN, {})


def run_cancelled(run_id) -> GraphApiError:
    return GraphApiError(409, RUN_CANCELLED, {"run_id": run_id})


def run_finished(run_id) -> GraphApiError:
    return GraphApiError(409, RUN_FINISHED, {"run_id": run_id})


def edit_mode_denied(run_id, effective_kind) -> GraphApiError:
    """403 for a write whose effective kind is not ``read_write`` (P#2 §6: read/none only)."""
    kind = effective_kind if effective_kind in EFFECTIVE_KINDS else "none"
    return GraphApiError(403, EDIT_MODE_DENIED,
                         {"run_id": run_id, "effective_kind": kind, "required_kind": REQUIRED_KIND})


def call_identity_unavailable() -> GraphApiError:
    """422 for a lost / unknown / mismatched call record (P#2 §5.2, §9.49). Cause is logged only."""
    return invalid("invalid_request", "call_context", "invalid_format",
                   validation={"reason": CALL_IDENTITY_UNAVAILABLE})


def tool_call_id_required() -> GraphApiError:
    """422 for a call record without a provider ``tool_call_id`` (L#3 §4.3 last branch)."""
    return invalid("invalid_request", "tool_call_id", "required")


def tool_result(handler: Callable[[], dict], request=None) -> tuple[int, dict]:
    """Run ``handler()`` through the Graph envelope and return ``(status, body)``.

    A ``GraphApiError`` becomes its status/code/params, anything else a 500
    ``internal_error`` with a trace id (never a success, never a raw exception).
    """
    from flowgate.card_documents import legacy_adapter as legacy
    response = legacy.run(request, handler)
    return response.status_code, json.loads(response.body)
