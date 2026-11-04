"""WP and TR2 window adapters. HTTP shapes stay owned by their existing transports."""
import json
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from modules.flow_gate.db import documents, document_revisions
from modules.flow_gate.documents import document_service, tr2_service as tr2
from modules.flow_gate.documents.routers import work_plan as old
from modules.flow_gate.services import graph_source_hooks, work_plan_service as wp
from modules.flow_gate.services import work_plan_apply_service as wpa, work_plan_attachment_service as attachments
from . import dedicated_bridge, contacts
from .contracts import Actor, Built, DedicatedRequest, Origin


class ResponseFailure(Exception):
    def __init__(self, response):
        self.response = response


def editable(doc):
    from modules.flow_gate.documents.routers.documents import _reject_if_group_ai_running, _reject_if_group_disposed
    if doc is None:
        raise HTTPException(404, "Document not found")
    _reject_if_group_disposed(doc)
    _reject_if_group_ai_running(doc)
    final = document_service.is_final_approved(doc)
    if doc.get("status") == "closed" or not document_service.is_document_editable(doc, final_approved=final):
        raise HTTPException(422, "Modification not allowed after final approval." if final else
                            f"Modification not allowed for status: {doc.get('status')}")


class WorkPlanSave:
    needs_current = True

    def __init__(self, body, actor, locale, restore=None):
        self.input, self.actor, self.locale, self.restore = body, actor, locale, restore
        self.cleanup_warnings = []

    def precheck(self, doc):
        editable(doc)

    def conflict(self, doc):
        return ResponseFailure(old._revision_conflict_response(doc or {}, self.locale,
                               self.input.base_revision_no, (doc or {}).get("revision_no", 0)))

    def build(self, doc, body):
        try:
            previous = json.loads(body.text) if body.text is not None else None
            if not isinstance(previous, dict):
                previous = None
        except ValueError:
            previous = None
        provenance = {}
        tasks = []
        if self.restore is not None:
            plan, row = restore_source(doc, self.restore)
            tasks.append(lambda: check_restore_source(row["_source_path"], row["_source_identity"]))
            if body.path is None or body.data is None:
                raise HTTPException(422, "Current work plan body is missing.")
            try:
                document_revisions.get_single_by_doc_revision(doc["doc_id"], doc["revision_no"])
            except document_revisions.RevisionAmbiguityError:
                raise ResponseFailure(JSONResponse(status_code=422, content={
                    "code": "wp_revision_not_restorable", "message": "The current work plan revision is ambiguous.",
                    "reason": "revision_ambiguous"}))
            provenance = {"restored_revision_no": self.restore, "restored_revision_id": row.get("id"),
                          "restored_revision_created_by": row.get("created_by")}
        else:
            try:
                plan = wp.validate(wp.assign_card_ids(self.input.body, previous,
                    project_id=doc["project_id"], action="save"), project_id=doc["project_id"],
                    doc_id=doc["doc_id"], action="save")
            except wp.WorkPlanValidationError as exc:
                raise ResponseFailure(old._validation_response(exc, self.locale)) from exc
        errors = wpa.instruction_mode_lock_errors(doc, previous, plan)
        if errors:
            if self.restore is not None:
                rendered = wp.render_errors(errors, self.locale)
                raise ResponseFailure(JSONResponse(status_code=422, content={
                    "code": "wp_revision_not_restorable", "message": rendered[0]["msg"],
                    "reason": "instruction_mode_started", "errors": rendered}))
            raise ResponseFailure(old._validation_response(wp.WorkPlanValidationError(errors, action="save"), self.locale))
        plan = wp.instruction_mode_defaults(previous, plan)
        locale = wp.resolve_title_locale(doc.get("meta"), current_title=doc.get("title"))
        title = wp.derived_title(plan, locale)
        meta = wp.metadata_with_title_locale(doc.get("meta"), locale)
        def cleanup():
            self.cleanup_warnings.extend(attachments.cleanup_unreferenced(documents.get_by_id(doc["doc_id"]), plan))
        return Built(wp.dumps(plan).encode("utf-8"), target_path=old._plan_path(doc),
                     row_updates={"title": title, "meta": json.dumps(meta, ensure_ascii=False)},
                     snapshot_policy="RECOVERY_BEFORE" if self.restore is not None else "PREVIOUS",
                     body_saved_source="work_plan_save", event_metadata=provenance, transaction_tasks=tasks,
                     type_tasks=[lambda: contacts.work_plan_refresh(doc, self.actor), cleanup],
                     result={"body": plan})


def restore_source(doc, revision):
    try:
        row = document_revisions.get_single_by_doc_revision(doc["doc_id"], revision)
    except document_revisions.RevisionAmbiguityError:
        raise ResponseFailure(JSONResponse(status_code=422, content={"code": "wp_revision_not_restorable",
            "message": "The selected work plan revision cannot be restored.", "reason": "revision_ambiguous"}))
    if row is None:
        raise HTTPException(404, "Work plan revision not found.")
    from modules.flow_gate.storage import files, paths
    source = paths.resolve_storage_path(row.get("backup_path"), doc["project_id"], doc.get("branch") or "main")
    identity = files.file_identity(source) if source is not None else None
    resolved, plan, reason = old._revision_backup(doc, row)
    if reason or plan is None:
        raise ResponseFailure(JSONResponse(status_code=422, content={"code": "wp_revision_not_restorable",
            "message": "The selected work plan revision cannot be restored.", "reason": reason}))
    check_restore_source(resolved, identity)
    return plan, {**row, "_source_path": resolved, "_source_identity": identity}


def check_restore_source(path, identity):
    from modules.flow_gate.storage import files
    if path is None or identity is None or not files.is_same_file_as(path, identity) or files.document_using_file(path, ()):
        raise ResponseFailure(JSONResponse(status_code=422, content={"code": "wp_revision_not_restorable",
            "message": "The selected work plan revision cannot be restored.", "reason": "backup_unavailable"}))


def wp_save(request, doc_id, body, current_user):
    locale, actor = old._locale(request), current_user["user_id"]
    doc = old._load_doc(doc_id)
    editable(doc)
    # Pure shape/card-id checks and acknowledgement failures still precede revision conflict.
    try:
        plan = wp.validate(wp.assign_card_ids(body.body, wp.load_previous_body(doc),
                           project_id=doc["project_id"], action="save"),
                           project_id=doc["project_id"], doc_id=doc_id, action="save")
    except wp.WorkPlanValidationError as exc:
        return old._validation_response(exc, locale)
    warnings = wp.capability_warning_findings(plan, doc["project_id"])
    expected = {row["step_key"] for row in warnings}
    supplied = body.capability_warning_acks
    if len(supplied) != len(set(supplied)) or any(not isinstance(key, str) for key in supplied):
        return JSONResponse(status_code=422, content={"code": "validation_failed", "message": "Capability acknowledgement keys must be unique.", "errors": [{"loc": "capability_warning_acks", "msg": "duplicate or invalid acknowledgement"}]})
    if set(supplied) - expected:
        return JSONResponse(status_code=422, content={"code": "validation_failed", "message": "Capability acknowledgement is stale or not applicable.", "errors": [{"loc": "capability_warning_acks", "msg": "stale acknowledgement"}]})
    if warnings and set(supplied) != expected:
        return JSONResponse(status_code=422, content={"code": "provider_capability_confirmation_required", "message": "Confirm providers that cannot modify source or run tests.", "findings": warnings})
    rule = WorkPlanSave(body, actor, locale)
    try:
        with graph_source_hooks.write_session():
            result = dedicated_bridge.dedicated_save(DedicatedRequest(doc_id, body.base_revision_no,
                Actor(actor), rule, event_reason="work_plan_saved"))
    except ResponseFailure as exc:
        return exc.response
    except (document_revisions.RevisionAmbiguityError, wp.RevisionSnapshotError) as exc:
        raise HTTPException(500, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Storage error: {exc}") from exc
    fresh = documents.get_by_id(doc_id)
    plan = result["body"]
    return {"ok": True, "doc_id": doc_id, "revision_no": result["revision_no"],
            "updated_at": result["updated_at"], "updated_by": actor,
            "doc_review_status": fresh.get("doc_review_status"), "title": fresh.get("title"),
            "body": plan, "unassigned_step_count": wp.unassigned_step_count(plan),
            "assignment_summary": wp.assignment_summary(plan, old._providers(doc["project_id"])),
            "totals": wp.totals(plan), "warnings": [*warnings, *rule.cleanup_warnings]}


def wp_restore(request, doc_id, revision_no, body, current_user):
    locale, actor = old._locale(request), current_user["user_id"]
    doc = old._load_doc(doc_id)
    editable(doc)
    try:
        restore_source(doc, revision_no)
        rule = WorkPlanSave(body, actor, locale, revision_no)
        with graph_source_hooks.write_session():
            result = dedicated_bridge.dedicated_save(DedicatedRequest(doc_id, body.base_revision_no,
                Actor(actor), rule, event_reason="work_plan_revision_restore"))
        return old._read_view(documents.get_by_id(doc_id), result["body"])
    except ResponseFailure as exc:
        return exc.response
    except (document_revisions.RevisionAmbiguityError, wp.RevisionSnapshotError) as exc:
        raise HTTPException(500, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Storage error: {exc}") from exc


class Tr2Save:
    def __init__(self, build, needs_current):
        self.builder, self.needs_current = build, needs_current

    def precheck(self, doc):
        if not doc or doc.get("type_code") != tr2.TR2_TYPE_CODE:
            raise tr2.Tr2ValidationError("tr2_workflow_conflict", "doc_id")

    def invariant_failure(self, affected):
        return tr2.Tr2ValidationError("tr2_history_invariant_error", "revision_no",
                                      {"reason": f"revision CAS matched {affected} rows"})

    def conflict(self, doc):
        return tr2.Tr2ValidationError("tr2_spec_changed", "expected_revision",
            {"current_revision_no": (doc or {}).get("revision_no", 0), "updated_at": (doc or {}).get("updated_at")})

    def build(self, doc, body):
        from .tr2_rules import build
        tr2._assert_mutable(doc)
        if self.needs_current and doc["revision_no"]:
            if not tr2._registered_at_canonical(doc, tr2.canonical_relative_path(doc)):
                raise tr2._body_error("tr2_storage_mismatch", doc, "file_path",
                                     "document is registered with a non-canonical file")
            if body.data is None:
                missing = body.state in {"file_path_missing", "file_not_found", "path_unresolved"}
                raise tr2._body_error("tr2_body_missing" if missing else "tr2_body_corrupt", doc,
                    "document.json", "proposal file does not exist" if missing else "proposal file cannot be read")
            current = tr2._decode_canonical(body.data, doc, loc="document.json")
        else:
            current = None
        return build(self.builder(current), doc)


def _tr2_result(result, actor, operation):
    doc = documents.get_by_id(result["doc_id"])
    return {"ok": True, "doc_id": result["doc_id"], "new_revision": result["revision_no"],
            "operation": operation, "body": result["body"], "derived": result["derived"],
            "dropped_keys": result["dropped_keys"], "updated_at": result["updated_at"],
            "updated_by": actor, "doc_review_status": doc.get("doc_review_status")}


def tr2_save(doc_id, build, *, actor, expected_revision, origin, operation, needs_current):
    req = DedicatedRequest(doc_id, expected_revision, Actor(actor, "worker" if origin == tr2.ORIGIN_AI else "person"),
                           Tr2Save(build, needs_current), Origin("tr2_window"), event_reason=operation)
    with graph_source_hooks.write_session():
        result = dedicated_bridge.dedicated_save(req)
    return _tr2_result(result, actor, operation)


def tr2_create(doc_id, raw_body, *, actor, origin):
    from .pending import enqueue
    req = DedicatedRequest(doc_id, 0, Actor(actor, "worker" if origin == tr2.ORIGIN_AI else "person"),
        Tr2Save(lambda _current: raw_body, False), Origin("tr2_window"), event_reason="create", new_document=True)
    result, pending = dedicated_bridge.dedicated_create(req)
    enqueue(pending)
    return _tr2_result(result, actor, "create")



def tr2_registration(flow_id, doc_id, path, stage):
    """The inbox's reserved new row, workflow binding and canonical first save commit together."""
    from modules.flow_gate.graph_workflow.services import flow_guard
    from modules.flow_gate.db.connection import in_transaction
    from modules.flow_gate.storage import files
    from .errors import internal_error
    def create():
        if documents.get_by_id(doc_id) is not None or path.exists() or files.document_using_file(path, ()):
            raise internal_error()
        result = stage()
        if isinstance(result, JSONResponse) and result.status_code >= 400:
            raise ResponseFailure(result)
        return result
    try:
        with graph_source_hooks.write_session():
            if in_transaction():
                if flow_guard.held_guard() != ("flow", flow_id):
                    raise internal_error()
                return create()
            return flow_guard.with_flow_guard(flow_id, create)
    except ResponseFailure as exc:
        return exc.response
