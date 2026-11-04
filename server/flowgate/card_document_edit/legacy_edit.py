"""Certain NON_CARD inbox edits share storage, never the card-coordinate contract."""
from dataclasses import replace
from modules.flow_gate.db import documents
from modules.flow_gate.rbac.permission_service import has_permission
from . import legacy_store, type_rules, common_checks, guards
from .contracts import DedicatedRequest, Outcome
from .errors import GraphApiError, invalid


class Validated(Exception):
    def __init__(self, result):
        self.result = result



def reject(result):
    from .agent_adapter import BoundaryRejected
    import json
    raise BoundaryRejected(result.status_code, json.loads(result.body))


def read_submission(source, token, routing):
    """P#2 §8: legacy input values and failure wording, after ordered boundary checks."""
    from modules.flow_gate.api import inbox_routes as inbox
    import os
    pairs = {"project": routing.get("project"), "module": routing.get("module"),
             "group_name": routing.get("group_name") or routing.get("group"),
             "doc_id": routing.get("doc_id"), "edit_reason": source.get("edit_reason")}
    for field, value in pairs.items():
        if not value:
            reject(inbox._fail(400, f"Required field missing: {field}"))
    reason = source["edit_reason"]
    if reason not in ("rejected", "qna_followup", "user_comment", "worker_self"):
        reject(inbox._fail(400, "edit_reason value is invalid"))
    path, content = source.get("doc_path"), source.get("content")
    if (path is not None) == (content is not None):
        reject(inbox._fail(400, "Exactly one of doc_path or content must be provided"))
    if path is not None:
        scratch = token.get("scratch_dir") or inbox._token_scratch_dir(token["project"], token["token_id"])
        if not inbox.validate_doc_path(path, scratch):
            reject(inbox._fail(422, f"doc_path is not accessible: {path}"))
        if not os.path.isfile(path):
            reject(inbox._fail(422, f"doc_path file does not exist: {path}"))
    if content is not None:
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        if len(content_bytes) > inbox._content_max():
            reject(inbox._fail(422, f"content size exceeds the limit (max {inbox._content_max()} bytes)"))
    try:
        return inbox._submission_text(path, content)
    except OSError as exc:
        reject(inbox._fail(422, f"doc_path file could not be read: {exc}"))


def edit(req):
    req = replace(req, transport_extras={**req.transport_extras, "legacy": True})
    doc = documents.get_by_id(req.expected_doc_id)
    if not has_permission(req.actor.user_id, doc["project_id"], "perm_document_update"):
        from modules.flow_gate.api import inbox_routes as inbox
        reject(inbox._permission_denied("Insufficient permissions for this operation"))

    class RuleCall:
        needs_current = False

        def precheck(self, fresh):
            from modules.flow_gate.api import inbox_routes as inbox
            guards.check_writable(fresh["group_id"], guards.principal_of(req), "inbox_edit")
            if req.recheck:
                req.recheck()
            from .agent_rules import reject_response
            final = inbox.document_service.is_final_approved(fresh)
            if not inbox.document_service.is_document_editable(fresh, final_approved=final):
                message = "Modification not allowed after final approval." if final else f"Modification not allowed for status: {fresh.get('status')}"
                reject_response(inbox._fail(422, message), fresh, legacy=True)
            if req.edit_reason == "rejected":
                response = req.transport_extras["submission"].get("rejection_response")
                if not (response or "").strip():
                    reject(inbox._fail(422, "rejection_response is required for a rejected resubmission."))
                from .agent_rules import rejection_history
                rejection_history(fresh, req.transport_extras["submission"], req)
            if req.linked_doc_id and documents.get_by_id(req.linked_doc_id) is None:
                reject(inbox._fail(422, f"Referenced document {req.linked_doc_id} does not exist"))
            reject_response(inbox._conversation_full_body_edit_fail(fresh), fresh, legacy=True)

        def conflict(self, fresh):
            return GraphApiError(409, "revision_conflict", {"doc_id": fresh["doc_id"], "revision_no": fresh["revision_no"]})

        def build(self, fresh, body):
            rule = type_rules.rule_for(fresh, req)
            if rule is None:
                # Legacy CH retains its full-body/migration guard, unlike card CH.
                from .agent_rules import AgentRule
                rule = AgentRule(req, type_rules.MarkdownRule(req))
            text = rule.prepare(req.content, fresh, body)
            from modules.flow_gate.api import inbox_routes as inbox
            from .agent_rules import reject_response
            reject_response(inbox._frontmatter_submission_guard(text,
                expected_project=fresh["project_id"], expected_module=fresh.get("module") or "none",
                expected_group_id=fresh.get("group_id") or "", expected_doc_type=fresh["type_code"],
                expected_doc_id=fresh["doc_id"], expected_target_id=fresh.get("target_id") or fresh.get("triggered_by"),
                locale=req.locale), fresh, legacy=True)
            common_checks.encoding_guard(req.content, fresh, req.transport_extras.get("encoding"), req.locale, legacy=True)
            built = rule.validate_and_build(text, fresh, body)
            if req.validate_only:
                raise Validated(built.result)
            return built

    dedicated = DedicatedRequest(req.expected_doc_id, 0, req.actor, RuleCall(), origin=req.origin,
        edit_reason=req.edit_reason, linked_doc_id=req.linked_doc_id,
        transport_extras=req.transport_extras, content=req.content)
    try:
        result, plan = legacy_store.legacy_store(dedicated)
    except Validated as validated:
        verdict = {"action": "edit", "doc_id": doc["doc_id"], "checks_passed": validated.result["_agent_checks"]}
        for key in ("normalizations", "worktree_untracked"):
            if key in validated.result:
                verdict[key] = validated.result[key]
        if "_agent_tr_scope" in validated.result:
            verdict["tr_scope"] = validated.result["_agent_tr_scope"]
        return Outcome("VALIDATED", verdict=verdict)
    return Outcome("SAVED", result=result, plan=plan, contacts="NORMAL")
