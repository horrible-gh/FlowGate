"""Card recovery and unwritten WP healing: protected diagnosis and N -> N+1."""
import json
from pathlib import Path
from modules.flow_gate.db import documents
from modules.flow_gate.db.connection import after_commit
from modules.flow_gate.documents import body_recovery as old
from modules.flow_gate.services import graph_source_hooks
from modules.flow_gate.storage import files, paths
from . import boundary, dedicated_bridge, target, contacts, guards
from .contracts import Actor, Built, ContactHolder, DedicatedRequest, Origin, RestoreLog
from .errors import internal_error


def own(doc, actor, build, reason):
    holder, log = ContactHolder(), RestoreLog()
    class Rule:
        needs_current = False
        def precheck(self, fresh):
            if fresh is None:
                raise old.RecoveryRefused("not_found", status=404)
            old.recover_write_checks(fresh)
        def conflict(self, fresh):
            return old.RecoveryRefused("diagnosis_changed")
        def build(self, fresh, body):
            return build(fresh, body)
    def stage(ctx):
        fresh = documents.get_by_id(doc["doc_id"])
        rule = Rule()
        rule.precheck(fresh)
        req = DedicatedRequest(doc["doc_id"], fresh["revision_no"], Actor(actor), rule,
                               Origin("recovery"), event_reason=reason)
        result, plan = dedicated_bridge.stage_dedicated(ctx, req, log)
        assert after_commit(lambda: holder.put(plan))
        return result
    try:
        with graph_source_hooks.write_session():
            result = boundary.enter(doc, boundary.OWN, stage)
    except Exception as exc:
        if log.has_failures():
            raise internal_error() from exc
        raise
    plan = holder.take()
    plan.card_slot = target.notify_scope(target.classify_card_slot(plan.doc))
    contacts.run(plan)
    return result, plan.doc


def recover(doc_id, expected_method, actor):
    doc = documents.get_by_id(doc_id)
    if doc is None:
        raise old.RecoveryRefused("not_found", f"Document not found: {doc_id}", status=404)
    classification = target.classify_card_slot(doc)
    if classification == target.NON_CARD:
        return old._recover_legacy(doc_id, expected_method, actor)
    if classification != target.CARD:
        raise internal_error()
    def build(fresh, body):
        state = body.state
        if state == "read_error":
            raise old.RecoveryRefused("read_error")
        if state == "readable":
            raise old.RecoveryRefused("file_exists")
        found = old.diagnose_doc(fresh, state, old.storage_root_info(fresh.get("project_id")))
        method = found["method"]
        if expected_method is not None and expected_method != method:
            raise old.RecoveryRefused("diagnosis_changed", evidence=old._summary(found))
        if method == old.FIX_STORAGE_ROOT:
            raise old.RecoveryRefused("storage_root_mismatch", evidence=found["evidence"])
        if method == old.READ_ERROR_MANUAL:
            raise old.RecoveryRefused("read_error")
        identity = None
        source_check = None
        provenance = {}
        if method in old.RELINKS:
            path = Path(found["candidate"])
            identity = files.file_identity(path)
            data = path.read_bytes()
            data.decode("utf-8", errors="strict")
            if identity is None or not files.is_same_file_as(path, identity) or not old.front_matter_matches(path, fresh):
                raise old.RecoveryRefused("diagnosis_changed")
        elif method == old.RESTORE_REVISION:
            path = old.recorded_or_standard(fresh)
            data, provenance, source_check = revision_source(fresh, found["revision"])
        elif method == old.REGENERATE_CARD_DRAFT:
            path, data = old.standard_location(fresh), old.card_draft_bytes(fresh)
        else:
            path, data = old.recorded_or_standard(fresh), old.stub_bytes(fresh)
        user = files.document_using_file(path, (doc_id,))
        if user:
            raise old._in_use(path, fresh.get("project_id"), user)
        if method not in old.RELINKS and path.exists():
            raise old.RecoveryRefused("diagnosis_changed")
        before = fresh.get("file_path") or ""
        after = paths.to_storage_relative(path, fresh["project_id"])
        evidence = {"method": method, "before": before, "after": after,
                    "body_lost": found["body_lost"], "reason": state}
        def record():
            from modules.flow_gate.db.events import insert_event
            insert_event(doc_id, "doc_body_recovered", reason=state, note=json.dumps(evidence, ensure_ascii=False))
        loss = old.BODY_LOSS[found["body_lost"]]
        return Built(data, target_path=path, snapshot_policy="RECOVERY_BEFORE",
            body_action="RELINK" if method in old.RELINKS else "CREATE_FILE", target_identity=identity,
            event_metadata={**evidence, **provenance},
            transaction_tasks=([source_check] if source_check else []) + [record], result={"restored_from": old.RESTORED_FROM[method],
                "method": method, "body_lost": loss != "none", "body_loss": loss,
                "before_file_path": before, "after_file_path": after, "content": data.decode("utf-8")})
    result, saved = own(doc, actor, build, "body_recovered")
    return {key: value for key, value in result.items() if key not in {"doc_id", "revision_no", "updated_at"}} | {"data": saved}


class AlreadyHealed(Exception):
    def __init__(self, doc, body):
        self.doc, self.body = doc, body


def heal(doc, exc, actor):
    from modules.flow_gate.documents.routers import work_plan as window
    from modules.flow_gate.services import work_plan_service as wp
    from .dedicated_windows import editable
    if exc.reason != "not_json" or not wp.is_unwritten_plan(exc.raw):
        return None
    if not guards.has_permission(actor, doc["project_id"], "perm_document_update"):
        return None
    def build(fresh, body):
        editable(fresh)
        if not guards.has_permission(actor, fresh["project_id"], "perm_document_update"):
            raise internal_error()
        try:
            current = wp.load_body(window._plan_path(fresh), project_id=fresh["project_id"], doc_id=fresh["doc_id"])
        except wp.WorkPlanUnreadable as problem:
            if problem.reason != "not_json" or not wp.is_unwritten_plan(problem.raw):
                raise
        else:
            raise AlreadyHealed(fresh, current)
        if body.data is None or body.identity is None:
            raise old.RecoveryRefused("read_error")
        plan = wp.auto_plan_body(fresh["project_id"], window._workflow_items(fresh))
        locale = wp.resolve_title_locale(fresh.get("meta"), current_title=fresh.get("title"))
        path = window._canonical_path(fresh)
        return Built(wp.dumps(plan).encode("utf-8"), target_path=path,
            row_updates={"title": wp.derived_title(plan, locale),
                         "meta": json.dumps(wp.metadata_with_title_locale(fresh.get("meta"), locale), ensure_ascii=False)},
            snapshot_policy="RECOVERY_BEFORE", body_action="REPLACE" if path == body.path else "CREATE_FILE",
            type_tasks=[lambda: contacts.work_plan_refresh(fresh, actor)], result={"body": plan})
    try:
        result, saved = own(doc, actor, build, "wp_unwritten_plan_healed")
    except AlreadyHealed as done:
        doc.update(done.doc)
        return done.body
    doc.update(saved)
    return result["body"]


def draft_recovery_lineage(doc):
    """Only a complete chain of absent-body empty-draft recoveries remains unwritten."""
    from modules.flow_gate.db.connection import get_store
    n = doc.get("revision_no")
    if type(n) is not int or n < 0:
        return False
    rows = get_store()._fetch_all(
        "SELECT metadata FROM workflow_events WHERE document_id = ? AND event_type = 'doc_edited' ORDER BY id",
        [doc["id"]])
    if len(rows) != n:
        return False
    for i, row in enumerate(rows):
        try:
            meta = json.loads(row.get("metadata") or "{}")
        except (ValueError, TypeError):
            return False
        if not (meta.get("origin") == "recovery" and meta.get("event_reason") == "body_recovered"
                and meta.get("method") == old.REGENERATE_CARD_DRAFT and meta.get("before_image") == "absent"
                and meta.get("doc_id") == doc["doc_id"] and meta.get("base_revision_no") == i
                and meta.get("revision_no") == i + 1):
            return False
    return True



def revision_source(doc, diagnosed_text):
    """Resolve the same newest readable snapshot with unambiguous identity and reference checks."""
    from modules.flow_gate.db import document_revisions
    for row in document_revisions.list_by_doc(doc["doc_id"]):
        path = paths.resolve_storage_path(row.get("backup_path"), doc["project_id"], doc.get("branch") or "main")
        if path is None or not path.is_file():
            continue
        identity = files.file_identity(path)
        try:
            data = path.read_bytes()
            text = data.decode("utf-8")
        except (OSError, UnicodeError):
            continue  # preserve the diagnosis's next-readable-snapshot rule
        try:
            single = document_revisions.get_single_by_doc_revision(doc["doc_id"], row["revision_no"])
        except document_revisions.RevisionAmbiguityError:
            raise old.RecoveryRefused("diagnosis_changed")
        if single is None or text.replace("\r\n", "\n").replace("\r", "\n") != diagnosed_text:
            raise old.RecoveryRefused("diagnosis_changed")
        def check(path=path, identity=identity):
            if identity is None or not files.is_same_file_as(path, identity) or files.document_using_file(path, ()):
                raise old.RecoveryRefused("diagnosis_changed")
        check()
        return data, {"restored_revision_no": row["revision_no"], "restored_revision_id": row["id"]}, check
    raise old.RecoveryRefused("diagnosis_changed")
