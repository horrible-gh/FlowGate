"""Who may place cards and start the run: a live chat run of this Flow's group, in edit mode.

On top of the chat's Graph read boundary (``graph_read.authz.authorize``: the chat token bound to its live run, the
token's own group as the Flow, its project, the conversation document of that group, the read permission):

- the token is a chat token (card workers never place cards or start runs);
- its effective source access is ``read_write`` (an edit or edit-once chat; reading mode is refused with 403
  ``chat_edit_mode_required``), the same rule the chat card document edit applies (``chat_authz.chat_recheck``);
- no edit-once claim of this token is still pending;
- the issuing user may write the Flow (``perm_document_update``, the Canvas draft save and Run button permission).
"""
from __future__ import annotations

from flowgate.card_document_edit import chat_authz
from flowgate.graph_read import authz as graph_authz
from modules.flow_gate.rbac.permission_service import has_permission

WRITE_PERMISSION = "perm_document_update"
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
