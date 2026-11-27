"""Who may place cards and start the run: a live chat run of this Flow's group, in edit mode.

On top of the chat's Graph read boundary (``graph_read.authz.authorize``: the chat token bound to its live run, the
token's own group as the Flow, its project, the conversation document of that group, the read permission):

- the token is a chat token (card workers never place cards or start runs);
- its effective source access is ``read_write`` (an edit or edit-once chat; reading mode is refused with 403
  ``chat_edit_mode_required``), the same rule the chat card document edit applies (``chat_authz.chat_recheck``);
- no edit-once claim of this token is still pending;
- the issuing user may write the Flow (``project.document.update``, the Canvas draft save and Run button permission).

the card AI role API keeps the token and run of the request (``Caller``): a read
(``read_caller``) is the Graph read boundary of a chat token alone, a change (``write_caller``) the boundary above; the
change re-judges the same inside its Flow guard (``card_roles``). ``owner`` is what a placement records about the chat
that placed a card (its CH conversation document, token, AI run, user).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from flowgate.card_document_edit import chat_authz
from flowgate.graph_read import authz as graph_authz
from modules.flow_gate.rbac.permission_service import has_permission

WRITE_PERMISSION = "project.document.update"
EDIT_MODE_REQUIRED = "chat_edit_mode_required"


def authorize(token, flow_id, run_hint=None):
    """``(scope, group)`` of an allowed request, else a refusal (``GraphApiError``)."""
    if not isinstance(token, dict) or token.get("action_scope") != "chat":
        graph_authz.refuse("token_kind_not_allowed")
    scope, group = graph_authz.authorize(token, flow_id, run_hint)
    if chat_authz.effective_write_kind(token) != chat_authz.CHAT_WRITE_KIND:
        graph_authz.refuse(EDIT_MODE_REQUIRED)
    if chat_authz.claim_pending(token):
        graph_authz.refuse()
    if not has_permission(scope.user_id, scope.project_id, WRITE_PERMISSION):
        graph_authz.refuse("flow_access_denied")
    return scope, group


def from_bearer(raw, flow_id):
    """CLI transport: the request's Bearer."""
    return authorize(graph_authz.verify(raw), flow_id)


def from_run(run, flow_id):
    """API transport: the run the worker hands over (its current token)."""
    current, token = chat_authz.authorize_chat_run(chat_authz.API, None, run)
    return authorize(token, flow_id, current)


# ── the earlier change: callers that keep their token and run ──────────────

@dataclass(frozen=True)
class Caller:
    scope: Any
    group: dict
    token: dict
    run: Optional[dict]

    @property
    def chat_doc_id(self) -> Optional[str]:
        """The chat's conversation document (CH): the token's ``doc_ref``, bound to its run's."""
        value = (self.token or {}).get("doc_ref")
        return str(value) if value else None


def _chat_token(token) -> None:
    if not isinstance(token, dict) or token.get("action_scope") != "chat":
        graph_authz.refuse("token_kind_not_allowed")


def _run_of(token, run):
    return run if run is not None else chat_authz._get_run_record(token.get("ai_run_id"))


def read_caller(token, flow_id, run=None) -> Caller:
    """A chat token's Graph read boundary (any mode). Card workers are refused (``token_kind_not_allowed``)."""
    _chat_token(token)
    run = _run_of(token, run)
    scope, group = graph_authz.authorize(token, flow_id, run)
    return Caller(scope, group, token, run)


def write_caller(token, flow_id, run=None) -> Caller:
    """``authorize`` above (edit mode, no pending claim, Flow write permission), keeping the token and run."""
    _chat_token(token)
    run = _run_of(token, run)
    scope, group = authorize(token, flow_id, run)
    return Caller(scope, group, token, run)


def caller_from_bearer(raw, flow_id, write: bool) -> Caller:
    """CLI transport: the request's Bearer."""
    token = graph_authz.verify(raw)
    return (write_caller if write else read_caller)(token, flow_id)


def caller_from_run(run, flow_id, write: bool) -> Caller:
    """API transport: the run the worker hands over (its current token)."""
    current, token = chat_authz.authorize_chat_run(chat_authz.API, None, run)
    return (write_caller if write else read_caller)(token, flow_id, current)


def can_write(caller: Caller) -> bool:
    """Whether this chat would pass ``authorize`` now (a read's hint; never an authority by itself)."""
    try:
        return (chat_authz.effective_write_kind(caller.token) == chat_authz.CHAT_WRITE_KIND
                and not chat_authz.claim_pending(caller.token)
                and bool(has_permission(caller.scope.user_id, caller.scope.project_id, WRITE_PERMISSION)))
    except Exception:
        return False


def owner(scope, chat_doc_id) -> Optional[dict]:
    """What a placement records about the placing chat; None without its conversation document."""
    if not chat_doc_id:
        return None
    return {"chat_doc_id": str(chat_doc_id), "token_id": getattr(scope, "token_id", None),
            "ai_run_id": getattr(scope, "ai_run_id", None), "user_id": getattr(scope, "user_id", None)}
