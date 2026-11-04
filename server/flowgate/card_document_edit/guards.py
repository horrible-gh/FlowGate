"""Authenticated user permission and writable-state checks, in the design's order."""
from flowgate.card_documents import legacy_adapter as legacy
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import mutation_policy, workflow_rework_service
from modules.flow_gate.documents import document_service
from .errors import GraphApiError, not_editable


def authorize(actor, project_id, perm, *, flow_id):
    if not has_permission(actor.user_id, project_id, perm):
        raise GraphApiError(403, "permission_denied", {"flow_id": flow_id, "required": perm})


def principal_of(req):
    if req.actor.kind in {"person", "person_delegated"}:
        return mutation_policy.human_principal({"user_id": req.actor.user_id})
    if req.actor.kind == "worker" and req.origin.token is not None:
        return mutation_policy.worker_principal(req.origin.token)
    raise RuntimeError("authenticated principal is required")


def operation_of(req):
    return "inbox_edit" if req.actor.kind == "worker" else "card_document_edit"


def check_writable(flow_id, principal, operation):
    legacy.require_writable(flow_id)
    try:
        mutation_policy.assert_group_mutation_allowed(flow_id, principal, operation)
    except mutation_policy.MutationPolicyError as exc:
        fields = {k: v for k, v in exc.error.items() if k not in {"code", "message"}}
        raise GraphApiError(exc.status_code, exc.error["code"], fields, message=exc.error["message"]) from exc
    try:
        workflow_rework_service.assert_no_revert_pending(flow_id)
    except Exception as exc:
        if getattr(exc, "code", None) == "workflow_revert_pending":
            raise GraphApiError(409, "workflow_revert_pending", {"group_id": flow_id}) from exc
        raise


def check_editable(req, doc):
    if doc.get("status") in {"cancelled", "archived"}:
        raise not_editable(req, doc, "terminal_status", status=doc["status"])
    if document_service.is_final_approved(doc):
        raise not_editable(req, doc, "final_approved")
