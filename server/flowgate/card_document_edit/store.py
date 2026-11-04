"""L#1 §2.4: affected-row CAS -> snapshot -> event -> restorable body replacement."""
import hashlib
import logging
from pathlib import Path
from modules.flow_gate.db import documents, document_revisions, workflow_events
from modules.flow_gate.db.connection import get_store, after_commit, after_rollback, now_iso
from modules.flow_gate.storage import files, paths, body as storage_body
from .contracts import ContactPlan, EVENT_SCHEMA
from .errors import conflict, internal_error
from . import idempotency

_log = logging.getLogger(__name__)


def edit_reason(req):
    if req.actor.kind in {"person", "person_delegated"}:
        return "user_comment"
    reason = req.edit_reason or "worker_self"
    if reason not in {"rejected", "qna_followup", "user_comment", "worker_self"}:
        raise internal_error()
    return reason


def _conflict(inp):
    fresh = documents.get_by_id(inp.current["doc_id"])
    if hasattr(inp.req, "rule_call"):
        raise inp.req.rule_call.conflict(fresh)
    raise conflict(inp.req, fresh)


def snapshot_policy_apply(inp, now):
    doc, built, body = inp.current, inp.built, inp.body
    current = doc["revision_no"]
    if built.snapshot_policy == "PREVIOUS":
        number = current
        old = document_revisions.get_single_by_doc_revision(doc["doc_id"], number)
        if old is not None:
            if doc["type_code"] == "WP":
                from modules.flow_gate.services import work_plan_service as wp
                wp.resolve_revision_snapshot(old, project_id=doc["project_id"], doc_id=doc["doc_id"])
            else:
                path = paths.resolve_storage_path(old["backup_path"], doc["project_id"], doc.get("branch") or "main")
                if path is None or not path.is_file():
                    raise internal_error()
            return
        if body.data is None:
            if inp.allow_missing_previous:
                return
            raise internal_error()
        data = body.data
        base = body.path
    elif built.snapshot_policy == "CURRENT":
        number, data = current + 1, built.stored_bytes
        if document_revisions.get_single_by_doc_revision(doc["doc_id"], number) is not None:
            raise internal_error()
        base = built.target_path or body.path
    else:
        raise internal_error()
    base = Path(base)
    suffix = base.suffix or (".json" if doc["type_code"] in {"WP", "TR2"} else ".md")
    backup = base.parent / "revisions" / f"{doc['doc_id']}.r{number}{suffix}"
    files.write_new_file(backup, data)
    _, created = document_revisions.create_once({
        "doc_id": doc["doc_id"], "revision_no": number,
        "backup_path": paths.to_storage_relative(backup, doc["project_id"]),
        "edit_reason": ("worker_self" if inp.req.actor.kind == "worker" else "user_comment")
        if doc["type_code"] == "TR2" else edit_reason(inp.req), "linked_doc_id": inp.req.linked_doc_id,
        "created_by": inp.req.actor.user_id, "created_at": now})
    if not created:
        raise internal_error()


def body_replace(inp):
    target = Path(inp.built.target_path or inp.body.path)
    if target.exists():
        identity = inp.body.identity if target == inp.body.path else files.file_identity(target)
        # A dedicated whole replacement may replace unreadable current bytes.
        if identity is None and hasattr(inp.req, "rule_call") and not inp.req.rule_call.needs_current:
            identity = files.file_identity(target)
        replaced = files.replace_restorably(target, inp.built.stored_bytes, expected_identity=identity)
        runner = files.cleanup_runner("card document edit restore " + str(target))
        def restore():
            try:
                ok = runner(replaced.restore) if runner else replaced.restore()
            except Exception:
                ok = False
            if not ok:
                inp.restore_log.add(inp.current["doc_id"], replaced.keep)
                _log.error("document_restore_incomplete doc=%s keep=%s", inp.current["doc_id"], replaced.keep)
        assert after_rollback(restore)
        assert after_commit(replaced.release)
    else:
        # A previously read file disappearing is a concurrent filesystem change, not a create.
        if target == inp.body.path and inp.body.identity is not None:
            raise internal_error()
        files.write_new_file(target, inp.built.stored_bytes, exclude_doc_ids=[inp.current["doc_id"]])


def commit_revision(ctx, inp):
    ctx.assert_held()
    doc, built, req = inp.current, inp.built, inp.req
    if ctx.doc_id != doc["doc_id"]:
        raise internal_error()
    updates = dict(built.row_updates)
    extras = req.transport_extras.get("row_updates", {})
    if set(updates) - {"title", "meta", "filename"} or set(extras) - {"rejection_history"}:
        raise internal_error()
    if extras and req.actor.kind != "worker":
        raise internal_error()
    updates.update(extras)
    now = now_iso()
    rel = paths.to_storage_relative(built.target_path or inp.body.path, doc["project_id"])
    columns = sorted(updates)
    setters = ["revision_no = revision_no + 1", "updated_at = ?", "file_path = ?"]
    setters += [key + " = ?" for key in columns]
    affected = get_store()._execute_affected(
        "UPDATE documents SET " + ", ".join(setters) + " WHERE doc_id = ? AND revision_no = ?",
        [now, rel, *[updates[key] for key in columns], doc["doc_id"], doc["revision_no"]])
    if affected == 0:
        _conflict(inp)
    if type(affected) is not int or affected != 1:
        raise internal_error()
    snapshot_policy_apply(inp, now)
    meta = {k: v for k, v in inp.binding.items() if k not in {"project_id", "actor_user_id"}}
    meta.update(schema=EVENT_SCHEMA, revision_no=doc["revision_no"] + 1, updated_at=now,
                edit_reason=edit_reason(req), linked_doc_id=req.linked_doc_id,
                stored_sha256=hashlib.sha256(built.stored_bytes).hexdigest(),
                run_id=req.origin.run_id, token_id=req.origin.token_id,
                server_call_id=req.origin.server_call_id, event_reason=getattr(req, "event_reason", None))
    workflow_events.create({"event_type": "doc_edited", "project_id": doc["project_id"],
                            "group_id": doc.get("group_id"), "document_id": doc["id"],
                            "actor_user_id": req.actor.user_id, "metadata": idempotency.dumps(meta),
                            "created_at": now})
    if built.body_saved_source:
        storage_body.record_body_saved(doc, source=built.body_saved_source, file_path=rel,
                                       size=len(built.stored_bytes))
    body_replace(inp)
    saved_doc = {**doc, **updates, "revision_no": doc["revision_no"] + 1, "updated_at": now, "file_path": rel}
    return ContactPlan(doc["project_id"], doc.get("group_id"), saved_doc, req.actor.user_id,
                       req.origin.token_id if req.actor.kind == "worker" else None,
                       type_tasks=list(built.type_tasks))
