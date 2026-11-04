"""L#1 §2.8: pure type builders; no revision, file, event or notification writes."""
import json
from pathlib import Path
from .contracts import Built
from .errors import invalid


def _failure(doc, details):
    raise invalid("invalid_content", "content", "invalid_format", doc, validation=details)


class MarkdownRule:
    def __init__(self, request=None, kind="MARKDOWN"):
        self.request, self.kind = request, kind

    def validate_and_build(self, text, doc, body):
        from modules.flow_gate.api import inbox_routes as inbox
        from modules.flow_gate import template_provision
        if not text.strip():
            _failure(doc, {"reason": "empty"})
        locale = getattr(self.request, "locale", "ko")
        if self.kind == "DESIGN":
            result = template_provision.validate_design_document_structure(doc["project_id"], doc["type_code"], locale, text)
            if not result.get("valid"):
                _failure(doc, result)
        if self.kind == "TS":
            result = inbox._spec_ts_submission_failure(text)
            if result is not None:
                _failure(doc, json.loads(result.body))
        try:
            meta = json.loads(doc.get("meta") or "{}")
        except (ValueError, TypeError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta.pop("content_sha256", None)
        meta.pop("content_sha256_norm", None)
        meta.update(inbox._content_fingerprints(text))
        meta.pop("tr_scope", None)
        if doc["type_code"] in inbox.tool_registry.MUTATING_STEP_TYPES:
            from modules.flow_gate.services import tr_scope_service
            token = self.request.origin.token if self.request is not None else None
            result = tr_scope_service.evaluate(
                project_id=doc["project_id"], group_id=doc.get("group_id"), body=text, locale=locale,
                prior_declared=inbox._prior_tr_declared(doc.get("group_id"), exclude_doc_id=doc["doc_id"]),
                attempt_scope=tr_scope_service.resolve_attempt_scope(token, doc["project_id"], doc.get("group_id")) if token else None)
            if result.get("verdict") == tr_scope_service.VERDICT_REJECT:
                _failure(doc, result)
            if result.get("verdict") != tr_scope_service.VERDICT_SKIPPED:
                meta["tr_scope"] = inbox._tr_scope_meta(result)
        extras = getattr(self.request, "transport_extras", {})
        reason = extras.get("encoding", {}).get("force_encoding_reason")
        if reason:
            meta["force_encoding_reason"] = reason
        return Built(text.encode("utf-8"), row_updates={"meta": json.dumps(meta) if meta else None})


class WorkPlanRule:
    def __init__(self, request=None):
        self.request = request

    def validate_and_build(self, text, doc, body):
        from modules.flow_gate.services import work_plan_service as wp, work_plan_apply_service as apply
        from modules.flow_gate.services import work_plan_attachment_service as attachments
        from .contacts import work_plan_refresh
        try:
            raw, previous = json.loads(text), json.loads(body.text)
            if not isinstance(raw, dict) or not isinstance(previous, dict):
                _failure(doc, {"reason": "not_an_object"})
            # Current plan comes only from the protected byte read, never load_previous_body().
            plan = wp.validate(wp.assign_card_ids(raw, previous, project_id=doc["project_id"], action="save"),
                               project_id=doc["project_id"], doc_id=doc["doc_id"], action="save",
                               allow_pre_instruction_attachments=False)
            errors = apply.instruction_mode_lock_errors(doc, previous, plan)
            if errors:
                raise wp.WorkPlanValidationError(errors, action="save")
            plan = wp.instruction_mode_defaults(previous, plan)
        except wp.WorkPlanValidationError as exc:
            _failure(doc, {"errors": exc.errors})
        except (ValueError, TypeError) as exc:
            _failure(doc, {"reason": "invalid_json"})
        locale = wp.resolve_title_locale(doc.get("meta"), current_title=doc.get("title"))
        title = wp.derived_title(plan, locale)
        meta = wp.metadata_with_title_locale(doc.get("meta"), locale)
        actor = self.request.actor.user_id if self.request is not None else doc.get("owner_id")
        return Built(wp.dumps(plan).encode("utf-8"), row_updates={"title": title, "meta": json.dumps(meta, ensure_ascii=False)},
                     body_saved_source="work_plan_save",
                     type_tasks=[lambda: work_plan_refresh(doc, actor), lambda: attachments.cleanup_unreferenced(doc, plan)],
                     result={"body": plan, "title": title})


class Tr2Rule:
    def validate_and_build(self, text, doc, body):
        from modules.flow_gate.documents import tr2_service as tr2
        try:
            tr2._assert_mutable(doc)
            raw = tr2.parse(text)
            dropped = [k for k in ("codebase_root", "source_honey") if k in raw]
            validated = tr2.validate(raw, doc=doc)
            tr2.verify_pair(doc["doc_id"], validated)
            canonical = tr2.canonicalize(validated)
            spec = canonical["edit_spec"]
            tr2.target_set(spec)
            root = tr2.resolve_source_root(doc["project_id"], doc["group_id"])
            canonical["baseline_fingerprint"] = tr2.target_fingerprint(spec, root)
            derived = {"files": tr2.derived_files(spec, root), "spec_fingerprint": tr2.spec_fingerprint(spec)}
            path = tr2.canonical_path_for_doc(doc)
        except tr2.Tr2ValidationError as exc:
            _failure(doc, {"code": exc.code, **exc.details})
        return Built(tr2.dumps(canonical).encode("utf-8"), target_path=path,
                     row_updates={"filename": path.name}, snapshot_policy="CURRENT", body_saved_source="tr2_save",
                     result={"body": canonical, "derived": derived, "dropped_keys": dropped})


def rule_for(doc, request=None):
    from modules.flow_gate import template_provision
    code = doc.get("type_code")
    if code == "CH":
        return None
    if code == "WP":
        return WorkPlanRule(request)
    if code == "TR2":
        return Tr2Rule()
    if code == "TS":
        return MarkdownRule(request, "TS")
    if template_provision.is_design_type(code):
        return MarkdownRule(request, "DESIGN")
    if Path(doc.get("file_path") or "").suffix.lower() == ".md":
        return MarkdownRule(request)
    return None
