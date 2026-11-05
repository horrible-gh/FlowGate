"""Agent-only submission plan; validators and normalizers stay in their existing modules."""
import json
import logging
from pathlib import Path
from modules.flow_gate.db import document_revisions
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.storage import paths
from . import common_checks, type_rules
from .contracts import Built
from .errors import invalid

_log = logging.getLogger(__name__)


def reject_response(result, doc, field="content", legacy=False):
    if result is not None:
        if legacy:
            from .agent_adapter import BoundaryRejected
            raise BoundaryRejected(result.status_code, json.loads(result.body))
        raise invalid("invalid_content", field, "invalid_format", doc, validation=json.loads(result.body))


class AgentRule:
    def __init__(self, req, base):
        self.req, self.base = req, base
        self.normalizations = []

    def prepare(self, text, doc, body):
        from modules.flow_gate.api import inbox_routes as inbox
        text, self.normalizations = inbox._normalize_submission_body(text, where="edit")
        self.provenance = []
        if doc["type_code"] == "TR":
            text, self.provenance = inbox.snapshot_access_service.inject_tr_provenance(text, self.req.origin.run_id or "")
        return text

    def validate_and_build(self, text, doc, body):
        from modules.flow_gate.api import inbox_routes as inbox
        from modules.flow_gate.services import work_plan_service as wp, tr_scope_service
        req, submitted = self.req, self.req.transport_extras["submission"]
        def reject(result):
            reject_response(result, doc, legacy=bool(req.transport_extras.get("legacy")))
        if req.linked_doc_id:
            linked = inbox.db_docs.get_by_id(req.linked_doc_id)
            if linked is None:
                raise invalid("invalid_content", "linked_doc_id", "invalid_format", doc)
        if not req.transport_extras.get("legacy"):
            reject(inbox._conversation_full_body_edit_fail(doc))
        if not req.transport_extras.get("legacy") and not text.strip():
            raise invalid("invalid_content", "content", "invalid_format", doc, validation={"reason": "empty"})
        if not req.transport_extras.get("legacy") and len(req.content.encode("utf-8")) > inbox._content_max():
            raise invalid("invalid_content", "content", "invalid_format", doc, validation={"reason": "too_long"})
        reject(inbox._design_template_submission_error(project=doc["project_id"],
            doc_type=doc["type_code"], locale=req.locale, content=text))
        if doc["type_code"] in {"WP", "TR2"}:
            try:
                built = self.base.validate_and_build(text, doc, body)
            except Exception as exc:
                from .errors import GraphApiError
                if not req.transport_extras.get("legacy") or not isinstance(exc, GraphApiError):
                    raise
                details = exc.params.get("errors", [{}])[0].get("validation", {})
                if doc["type_code"] == "TR2" and details.get("code") in inbox.TR2_ERRORS:
                    reject_response(inbox.JSONResponse(status_code=inbox.TR2_ERRORS[details["code"]].http_status,
                        content=inbox.error_payload(details["code"], details={k:v for k,v in details.items() if k != "code"})),
                        doc, legacy=True)
                if doc["type_code"] == "WP":
                    if details.get("reason") == "invalid_json":
                        message = wp.inbox_not_json_message(text, None, req.locale)
                    else:
                        message = wp.inbox_error_message(wp.WorkPlanValidationError(details.get("errors", []), action="save"), req.locale)
                    reject_response(inbox._fail(400, message), doc, legacy=True)
                raise
        else:
            if doc["type_code"] == "TS":
                reject(inbox._spec_ts_submission_failure(text))
            # Duplicate and scope checks retain their defensive best-effort lookup contract.
            fingerprints, twin, scope = {}, None, None
            try:
                fingerprints = inbox._content_fingerprints(text)
                twin = inbox._find_body_twin(fingerprints, exclude_group_id=doc.get("group_id"))
            except Exception:
                fingerprints = {}
            if twin is not None:
                if req.transport_extras.get("legacy"):
                    reject_response(inbox._fail(409, "Duplicate body: this content matches "
                        f"{twin['doc_id']} in another group. The submitted body looks like "
                        "stale/reused content (correct title, wrong body). Re-send with this document's own content."), doc, legacy=True)
                raise invalid("invalid_content", "content", "invalid_format", doc,
                              validation={"reason": "duplicate_body"})
            if doc["type_code"] in inbox.tool_registry.MUTATING_STEP_TYPES:
                try:
                    scope = tr_scope_service.evaluate(project_id=doc["project_id"], group_id=doc.get("group_id"),
                        body=text, locale=req.locale, prior_declared=inbox._prior_tr_declared(doc.get("group_id"), exclude_doc_id=doc["doc_id"]),
                        attempt_scope=tr_scope_service.resolve_attempt_scope(req.origin.token, doc["project_id"], doc.get("group_id")))
                except Exception:
                    _log.warning("Agent TR scope lookup unavailable", exc_info=True)
                if scope and scope.get("verdict") == tr_scope_service.VERDICT_REJECT:
                    if req.transport_extras.get("legacy"):
                        reject_response(inbox._fail(422, scope.get("notice") or inbox._TR_SCOPE_FALLBACK_COPY[req.locale]), doc, legacy=True)
                    raise invalid("invalid_content", "content", "invalid_format", doc, validation=scope)
            try:
                meta = json.loads(doc.get("meta") or "{}")
            except (TypeError, ValueError):
                meta = {}
            meta = meta if isinstance(meta, dict) else {}
            for key in ("content_sha256", "content_sha256_norm", "tr_scope"):
                meta.pop(key, None)
            meta.update(fingerprints)
            if scope and scope.get("verdict") != tr_scope_service.VERDICT_SKIPPED:
                meta["tr_scope"] = inbox._tr_scope_meta(scope)
            if submitted.get("force_encoding_reason"):
                meta["force_encoding_reason"] = submitted["force_encoding_reason"]
            built = Built(text.encode("utf-8"), row_updates={"meta": json.dumps(meta) if meta else None})
        if doc["type_code"] == "WP":
            meta = json.loads(built.row_updates.get("meta") or "{}")
            meta.pop("content_sha256", None)
            meta.pop("content_sha256_norm", None)
            meta.update(inbox._content_fingerprints(text))
            built.row_updates["meta"] = json.dumps(meta, ensure_ascii=False)
        if req.edit_reason == "rejected":
            built.row_updates["rejection_history"] = rejection_history(doc, submitted, req)
        if built.target_path is None and body.path is None:
            group = inbox.db_groups.get_by_id(doc["group_id"])
            built.target_path = inbox._resolve_storage_path(doc["project_id"], doc.get("module") or "none", group,
                doc["doc_id"], branch=doc.get("branch") or "main", doc_type=doc["type_code"])
        result = dict(built.result)
        checks = ["auth", "context_binding", "permission", "referential_integrity", "editable"]
        if submitted.get("content") is not None:
            checks.append("content_size")
        if req.linked_doc_id:
            checks.append("linked")
        if submitted.get("doc_path") is not None:
            checks.append("doc_path")
        if doc["type_code"] not in {"WP", "TR2"}:
            checks.append("frontmatter_identity")
            if fingerprints:
                checks.append("dup_body")
            if scope and scope.get("verdict") != tr_scope_service.VERDICT_SKIPPED:
                checks.append("tr_scope")
                result["_agent_tr_scope"] = scope
        if doc["type_code"] == "WP":
            checks.append("work_plan_body")
        if self.normalizations:
            checks.append("header_normalized")
        result["_agent_checks"] = checks
        if req.edit_reason == "rejected":
            result["_agent_review_status_before"] = doc.get("doc_review_status")
        if doc["type_code"] == "WP":
            result.update(doc_type="WP", title=built.row_updates["title"])
        elif doc["type_code"] == "TR2":
            result["doc_type"] = "TR2"
        if self.normalizations:
            result["normalizations"] = self.normalizations
        if doc["type_code"] in inbox.tool_registry.MUTATING_STEP_TYPES:
            result["worktree_untracked"] = inbox.git_service.worktree_untracked_summary(doc["project_id"], doc.get("group_id"))
        try:
            if doc["type_code"] == "WP":
                result["change_summary"] = wp.change_summary(result["body"], json.loads(body.text) if body.text is not None else None, locale=req.locale)
            elif doc["type_code"] == "TR2":
                spec = result["body"]["edit_spec"]
                result["change_summary"] = {"changed": f"edit {sum(e['kind'] == 'edit' for e in spec['edits'])}, "
                    f"create_file {sum(e['kind'] == 'create_file' for e in spec['edits'])}, "
                    f"deferred {len(spec['deferred'])}; {spec['termination']}; gate commands {len(spec['gate']['commands'])}"}
            else:
                from modules.flow_gate.services.change_summary_service import build_text
                result["change_summary"] = build_text(doc_id=doc["doc_id"], after_text=built.stored_bytes.decode("utf-8"),
                    after_revision_no=doc["revision_no"]+1, before_text=body.text, before_revision_no=doc["revision_no"])
        except Exception:
            result["change_summary"] = {"changed": None, "error": "summary unavailable"}
        target_path = built.target_path or body.path
        result["stored_path"] = str(target_path)
        previous = document_revisions.get_single_by_doc_revision(doc["doc_id"], doc["revision_no"])
        result["previous_revision_path"] = str(paths.resolve_storage_dir(previous["backup_path"], doc["project_id"])) if previous else (
            str(Path(target_path).parent / "revisions" / f"{doc['doc_id']}.r{doc['revision_no']}{Path(target_path).suffix or '.md'}") if body.data is not None and built.snapshot_policy == "PREVIOUS" else None)
        if self.provenance and req.origin.run_id:
            built.type_tasks.append(lambda: inbox.snapshot_access_service.attach_tr(req.origin.run_id, doc["doc_id"]))
        built.result = result
        return built


def rejection_history(doc, submitted, req):
    from modules.flow_gate.api import inbox_routes as inbox
    from modules.flow_gate.workflow.pipeline_service import AI_RESPONSE_MAX_LEN, resolve_rejection_target
    from modules.flow_gate.services.ai_invoke.provenance import resolve_run_provenance
    try:
        history = json.loads(doc.get("rejection_history") or "[]")
    except (ValueError, TypeError):
        history = []
    history = history if isinstance(history, list) else []
    review_body = dict(submitted)
    if not req.transport_extras.get("legacy") and "rejection_review_id" in submitted:
        review_body["review_id"] = submitted["rejection_review_id"]
    target = resolve_rejection_target(history, rejection_id=inbox._submitted_rejection_id(submitted),
                                      review_id=inbox._submitted_review_id(review_body))
    if target is None:
        if req.transport_extras.get("legacy"):
            reject_response(inbox._fail(409,
                "The submitted rejection target does not match the current rejection history. "
                "Refresh the document and retry."), doc, legacy=True)
        raise invalid("invalid_content", "rejection_id", "invalid_format", doc,
                      validation={"reason": "rejection_target_mismatch"})
    provenance = resolve_run_provenance(req.origin.run_id, doc_id=None, allowed_action_scopes=("edit",))
    target.update(ai_response=submitted["rejection_response"].strip()[:AI_RESPONSE_MAX_LEN], responded_at=now_iso(),
        response_revision_no=doc["revision_no"]+1, response_ai_run_id=provenance.get("ai_run_id"),
        response_actual_provider_id=provenance.get("actual_provider_id"),
        response_actual_provider_name=provenance.get("actual_provider_name"),
        response_recorded_by=provenance.get("actual_provider_id") or req.actor.user_id,
        response_requested_by=req.actor.user_id)
    return json.dumps(history, ensure_ascii=False)
