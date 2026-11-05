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
    if built.snapshot_policy in {"RECOVERY_BEFORE", "ABSENT_CONVERSION"}:
        if built.snapshot_policy == "ABSENT_CONVERSION" and not (
                inp.req.origin.kind == "root_conversion" and built.body_action == "REPOINT_ABSENT"
                and body.data is None):
            raise internal_error()
        # Trusted recovery/restore or an identity conversion without a physical before-image.
        old = document_revisions.get_single_by_doc_revision(doc["doc_id"], current)
        if old is not None:
            backup = paths.resolve_storage_path(old["backup_path"], doc["project_id"], doc.get("branch") or "main")
            if backup is None or not backup.is_file() or files.document_using_file(backup, (doc["doc_id"],)):
                raise internal_error()
        built.event_metadata.update(before_image="absent" if body.data is None else "raw",
                                    before_body_state=body.state, before_file_path=doc.get("file_path"))
        if body.data is None:
            if body.state not in {"file_path_missing", "file_not_found", "path_unresolved"}:
                raise internal_error()
            return
        if body.identity is None or not files.is_same_file_as(body.path, body.identity):
            raise internal_error()
        if old is not None:
            base = Path(body.path)
            sidecar = base.parent / "revisions" / (f"{doc['doc_id']}.r{current}.unreadable-before-r{current + 1}" + (base.suffix or ".md"))
            exclusive_snapshot(sidecar, body.data)
            built.event_metadata.update(unreadable_backup_path=paths.to_storage_relative(sidecar, doc["project_id"]),
                                        unreadable_backup_is_sidecar=True)
            return
        number, data, base = current, body.data, body.path
    elif built.snapshot_policy == "PREVIOUS":
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
    if built.snapshot_policy == "RECOVERY_BEFORE":
        exclusive_snapshot(backup, data)
    else:
        files.write_new_file(backup, data)
    if built.snapshot_policy == "RECOVERY_BEFORE":
        built.event_metadata.update(unreadable_backup_path=paths.to_storage_relative(backup, doc["project_id"]),
                                    unreadable_backup_is_sidecar=False)
    _, created = document_revisions.create_once({
        "doc_id": doc["doc_id"], "revision_no": number,
        "backup_path": paths.to_storage_relative(backup, doc["project_id"]),
        "edit_reason": ("worker_self" if inp.req.actor.kind == "worker" else "user_comment")
        if doc["type_code"] == "TR2" else edit_reason(inp.req), "linked_doc_id": inp.req.linked_doc_id,
        "created_by": inp.req.actor.user_id, "created_at": now})
    if not created:
        raise internal_error()


def exclusive_snapshot(path, data):
    # Never move an existing revision/sidecar aside as an orphan.
    path.parent.mkdir(parents=True, exist_ok=True)
    if files.document_using_file(path, ()) is not None:
        raise internal_error()
    import os
    from modules.flow_gate.db.connection import track_new_file
    # Track even a partial write/fsync failure. The open descriptor identifies our own file.
    with path.open("xb", buffering=0) as stream:
        try:
            view = memoryview(data)
            while view:
                written = stream.write(view)
                if not written:
                    raise OSError("short revision snapshot write")
                view = view[written:]
            os.fsync(stream.fileno())
        finally:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            track_new_file(path, still_ours=lambda p: files.is_same_file_as(p, identity),
                           under=files.cleanup_runner("revision rollback " + str(path)))


def body_replace(inp):
    if inp.built.body_action == "REPOINT_ABSENT":
        # A path-only root conversion preserves missing-body behavior and never invents a file.
        from .body import read_body_bytes
        if inp.req.origin.kind != "root_conversion" or inp.body.data is not None:
            raise internal_error()
        fresh = read_body_bytes(inp.current)
        if fresh.state != inp.body.state or fresh.data is not None:
            raise internal_error()
        if inp.built.target_reference:
            for candidate, inside in paths.storage_path_candidates(inp.built.target_reference, inp.current["project_id"]):
                if inside and (candidate.exists() or files.document_using_file(candidate, (inp.current["doc_id"],))):
                    raise files.FileChanged(candidate)
        return
    target = Path(inp.built.target_path or inp.body.path)
    if files.document_using_file(target, (inp.current["doc_id"],)) is not None:
        raise internal_error()
    if inp.built.body_action == "RELINK":
        if not files.is_same_file_as(target, inp.built.target_identity):
            raise files.FileChanged(target)
        return
    if inp.built.body_action == "CREATE_FILE" and target.exists():
        raise files.FileChanged(target)
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
    from .write_gate import revision
    with revision(ctx, inp):
        return _commit_revision(ctx, inp)


def _commit_revision(ctx, inp):
    ctx.assert_held()
    doc, built, req = inp.current, inp.built, inp.req
    if ctx.doc_id != doc["doc_id"]:
        raise internal_error()
    updates = dict(built.row_updates)
    extras = req.transport_extras.get("row_updates", {})
    if set(updates) - ({"title", "meta", "filename", "rejection_history"} if req.actor.kind == "worker" else {"title", "meta", "filename"}) or set(extras) - {"rejection_history"}:
        raise internal_error()
    if extras and req.actor.kind != "worker":
        raise internal_error()
    updates.update(extras)
    now = now_iso()
    rel = (built.target_reference if built.body_action == "REPOINT_ABSENT" else
           paths.to_storage_relative(built.target_path or inp.body.path, doc["project_id"]))
    columns = sorted(updates)
    setters = ["revision_no = revision_no + 1", "updated_at = ?", "file_path = ?"]
    setters += [key + " = ?" for key in columns]
    affected = get_store()._execute_affected(
        "UPDATE documents SET " + ", ".join(setters) + " WHERE doc_id = ? AND revision_no = ?",
        [now, rel, *[updates[key] for key in columns], doc["doc_id"], doc["revision_no"]])
    if affected == 0:
        _conflict(inp)
    if type(affected) is not int or affected != 1:
        if hasattr(inp.rule, "invariant_failure"):
            raise inp.rule.invariant_failure(affected)
        raise internal_error()
    snapshot_policy_apply(inp, now)
    meta = {k: v for k, v in inp.binding.items() if k not in {"project_id", "actor_user_id"}}
    meta.update(schema=EVENT_SCHEMA, revision_no=doc["revision_no"] + 1, updated_at=now,
                edit_reason=edit_reason(req), linked_doc_id=req.linked_doc_id,
                stored_sha256=hashlib.sha256(built.stored_bytes).hexdigest(),
                run_id=req.origin.run_id, token_id=req.origin.token_id,
                server_call_id=req.origin.server_call_id, event_reason=getattr(req, "event_reason", None))
    meta.update(built.event_metadata)
    if req.origin.kind == "agent":
        meta["agent_result"] = built.result
    workflow_events.create({"event_type": "doc_edited", "project_id": doc["project_id"],
                            "group_id": doc.get("group_id"), "document_id": doc["id"],
                            "actor_user_id": req.actor.user_id, "metadata": idempotency.dumps(meta),
                            "created_at": now})
    if built.body_saved_source:
        storage_body.record_body_saved(doc, source=built.body_saved_source, file_path=rel,
                                       size=len(built.stored_bytes))
    body_replace(inp)
    for task in built.transaction_tasks:
        task()
    saved_doc = {**doc, **updates, "revision_no": doc["revision_no"] + 1, "updated_at": now, "file_path": rel}
    return ContactPlan(doc["project_id"], doc.get("group_id"), saved_doc, req.actor.user_id,
                       req.origin.token_id if req.actor.kind == "worker" else None,
                       type_tasks=list(built.type_tasks))
