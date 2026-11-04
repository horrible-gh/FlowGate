"""Root conversion and test-run reports participate in the common revision store."""
from pathlib import Path
from modules.flow_gate.db import documents, test_runs, users
from modules.flow_gate.db.connection import in_transaction, after_commit
from modules.flow_gate.graph_workflow.services import flow_guard
from modules.flow_gate.services import graph_source_hooks
from modules.flow_gate.storage import files, paths
from . import boundary, dedicated_bridge
from .contracts import Actor, Built, DedicatedRequest, Origin
from .pending import enqueue
from .errors import internal_error


def convert(doc_id, new_type, actor):
    from modules.flow_gate.documents import root_conversion as old
    def execute():
        flow_guard.lock_document_row(doc_id)
        return old._execute_in_guard(doc_id, new_type, actor)
    try:
        with graph_source_hooks.write_session():
            return flow_guard.run_in_document_guard(doc_id, execute)
    except flow_guard.GuardNotHeld:
        raise old.ConversionRefused("transaction_required", doc_id=doc_id)


def conversion_stage(doc, new_id, new_type, mode, actor):
    from modules.flow_gate.documents import root_conversion as old
    fresh = documents.get_by_id(doc["doc_id"])
    staged, data = old.prepare_converted_file(fresh, new_id, new_type, mode)
    if mode == old.SUCCESSION:
        if not in_transaction() or documents.get_by_id(new_id) is not None:
            raise internal_error()
        if staged.staged is not None:
            if staged.staged.exists() or files.document_using_file(staged.staged, ()):
                raise files.FileChanged(staged.staged)
            files.write_new_file(staged.staged, data)
        return staged, fresh, None
    # Refuse an occupied conversion target before CAS or snapshot creation as well as at S4.
    if staged.staged is not None and (staged.staged.exists() or files.document_using_file(staged.staged, ())):
        raise files.FileChanged(staged.staged)
    class Rule:
        needs_current = True
        def precheck(self, current):
            if current is None:
                raise internal_error()
        def conflict(self, current):
            return old.ConversionRefused("transaction_required", doc_id=doc["doc_id"])
        def build(self, current, body):
            if data is None and body.data is None and body.state in {
                    "file_path_missing", "file_not_found", "path_unresolved"}:
                return Built(b"", row_updates={"filename": staged.filename}, body_action="REPOINT_ABSENT",
                    target_reference=staged.path_value, snapshot_policy="ABSENT_CONVERSION",
                    event_metadata={"stored_sha256": None, "old_doc_id": doc["doc_id"],
                        "final_doc_id": new_id, "old_type": doc["type_code"], "new_type": new_type})
            if body.data is None or body.identity != staged.old_identity:
                raise old.ConversionRefused("root_body_unreadable", doc_id=doc["doc_id"], reason=body.state, mode=mode)
            return Built(data, target_path=staged.staged, row_updates={"filename": staged.filename},
                         body_action="CREATE_FILE", event_metadata={"old_doc_id": doc["doc_id"],
                             "final_doc_id": new_id, "old_type": doc["type_code"], "new_type": new_type})
    req = DedicatedRequest(doc["doc_id"], fresh["revision_no"], Actor(actor), Rule(),
                           Origin("root_conversion"), event_reason="root_conversion")
    _, pending = dedicated_bridge.dedicated_join(req)
    return staged, pending.plan.doc, pending


def conversion_finish(pending, inserted, draft_changed=False):
    if pending is None:
        return
    pending.plan.doc = inserted
    pending.plan.canvas_already_queued = draft_changed
    pending.plan.flow_id = inserted.get("group_id")
    enqueue(pending)


def tsr(doc, *, run, kind="result", state=None, reason=None, cases=None, locale="ko", run_chain=True):
    from modules.flow_gate.services import test_run_service as old
    if not run or not run.get("run_id"):
        raise internal_error()
    ts_id = doc["doc_id"]
    def build(ts, current_run):
        if kind == "result":
            return render_result(ts, current_run, test_runs.list_cases(current_run["run_id"]), locale)
        meta = old.test_spec_service.load_result_meta(current_run.get("result_meta"))
        return old._pending_report(ts, current_run, meta.get("spec_cases") or cases or [],
                                   state=state, reason=reason, locale=locale)
    def write():
        flow_guard.lock_document_row(ts_id)
        ts = documents.get_by_id(ts_id)
        current_run = test_runs.get_run(run["run_id"])
        if (ts is None or current_run is None or current_run.get("doc_id") != ts_id
                or not current_run.get("runner_id") or users.get_by_id(current_run["runner_id"]) is None):
            raise internal_error()
        latest = test_runs.latest_by_doc(ts_id)
        if latest is None or latest["run_id"] != current_run["run_id"]:
            raise internal_error()
        actor = current_run["runner_id"]
        active = old._active_tsr_for_ts(ts)
        run_meta = old.test_spec_service.load_result_meta(current_run.get("result_meta"))
        bound = current_run.get("tsr_doc_id") or run_meta.get("tsr_doc_id")
        if bound and (active is None or active["doc_id"] != bound):
            raise internal_error()
        if active is not None:
            flow_guard.lock_document_row(active["doc_id"])
            active = documents.get_by_id(active["doc_id"])
            if active.get("doc_review_status") == "approved" or active.get("status") in {"closed", "cancelled", "archived"}:
                raise old._http_error(409, "tsr_already_approved", tsr_doc_id=active["doc_id"])
            if active.get("target_id") != ts_id or active.get("group_id") != ts.get("group_id"):
                raise internal_error()
            owner = test_runs.latest_by_tsr_doc(active["doc_id"])
            # An admitted run may take over a previous report; a late older run may not.
            if owner is not None and owner["run_id"] != current_run["run_id"] and owner.get("created_at", "") > current_run.get("created_at", ""):
                raise internal_error()
            class Rule:
                needs_current = False
                def precheck(self, fresh):
                    if fresh is None or fresh.get("target_id") != ts_id:
                        raise internal_error()
                def conflict(self, fresh):
                    return internal_error()
                def build(self, fresh, body):
                    if body.state not in {"readable", "file_path_missing", "file_not_found", "path_unresolved"}:
                        raise internal_error()
                    text, title = build(ts, current_run)
                    path = report_path(ts, fresh["doc_id"])
                    return Built(text.encode("utf-8"), target_path=path, row_updates={"title": title},
                        snapshot_policy="PREVIOUS" if body.data is not None else "RECOVERY_BEFORE",
                        body_action="REPLACE" if body.path == path and body.identity is not None else "CREATE_FILE")
            req = DedicatedRequest(active["doc_id"], active["revision_no"], Actor(actor), Rule(),
                                   Origin("test_run", run_id=current_run["run_id"]), event_reason="test_run")
            _, pending = dedicated_bridge.dedicated_join(req)
            enqueue(pending)
            tsr_id, path = active["doc_id"], report_path(ts, active["doc_id"])
        else:
            # Genuine CREATE: reserved new id, fresh file, new row in this outer transaction.
            from modules.flow_gate.numbering import numbering_service, id_formatter
            code = numbering_service.reserve_document(ts["group_id"], "TSR", module=ts.get("module") or "none")
            tsr_id = ts["group_id"] + "." + code
            _, seq = id_formatter.parse_doc_code(code)
            if documents.get_by_id(tsr_id) is not None:
                raise internal_error()
            text, title = build(ts, current_run)
            path = report_path(ts, tsr_id)
            if path.exists() or files.document_using_file(path, ()) is not None:
                raise files.FileChanged(path)
            files.write_new_file(path, text.encode("utf-8"))
            documents.create({"doc_id": tsr_id, "project_id": ts["project_id"], "branch": ts.get("branch") or "main",
                "module": ts.get("module") or "none", "group_id": ts["group_id"], "type_code": "TSR", "seq": seq,
                "title": title, "file_path": paths.to_storage_relative(path, ts["project_id"]), "status": "open",
                "owner_id": ts.get("owner_id"), "target_id": ts_id, "triggered_by": ts_id})
            from .contracts import ContactPlan, RestoreLog
            pending = dedicated_bridge.PendingContacts(RestoreLog())
            pending.plan = ContactPlan(ts["project_id"], ts["group_id"], documents.get_by_id(tsr_id), actor)
            from modules.flow_gate.db.connection import after_rollback
            assert after_commit(pending.mark_committed)
            assert after_rollback(pending.discard)
            enqueue(pending)
        old._register_tsr_result(ts, tsr_id, path, run_chain)
        return tsr_id
    return generated_boundary(doc, write)


def report_path(ts, tsr_id):
    return paths.document_path(project_id=ts["project_id"], group_code=ts["group_id"],
        doc_code=tsr_id[len(ts["group_id"]) + 1:], filename="document.md", module=ts.get("module") or "none",
        branch=ts.get("branch") or "main")


def render_result(doc, run, cases, locale):
    from modules.flow_gate.services.test_run_service import _tsr_title, _tsr_basis_section, _tsr_content, test_spec_service
    title = _tsr_title(doc, locale)
    if run.get("contract_version") == test_spec_service.CONTRACT_SPEC:
        # 0549 T0008: a test report — every TS Case ID with its verdict, expected/actual,
        # evidence and source identity, whatever the overall verdict is.
        meta = test_spec_service.load_result_meta(run.get("result_meta"))
        content = test_spec_service.render_report_markdown(
            doc=doc,
            run=run,
            rows=[
                test_spec_service.stored_case_to_row(case)
                for case in cases
                if (case.get("kind") or "case") == "case"
            ],
            summary={
                "overall": run.get("overall"),
                "counts": meta.get("counts") or {},
                "required_counts": meta.get("required_counts") or {},
                "optional_counts": meta.get("optional_counts") or {},
            },
            unmapped=meta.get("unmapped") or [],
            conflicts=meta.get("conflicts") or [],
            title=title,
            locale=locale,
        )
        if meta.get("test_basis") or meta.get("live_verdict"):
            content += _tsr_basis_section(meta, locale)
    else:
        content = _tsr_content(doc, run, cases, title, locale)
    return content, title


def generated_boundary(doc, call):
    from contextlib import contextmanager
    from modules.flow_gate.services import test_run_service
    @contextmanager
    def admission():
        # JOIN cannot wait for an admission owner that may itself be waiting for this Flow.
        lock = test_run_service._admission_lock
        if not lock.acquire(blocking=not in_transaction()):
            raise internal_error()
        try:
            yield
        finally:
            lock.release()
    def locked():
        flow_guard.lock_document_row(doc["doc_id"])
        return call()
    with admission(), graph_source_hooks.write_session():
        return flow_guard.run_in_document_guard(doc["doc_id"], locked)


def open_tsr(doc, cases, run, *, locale="ko"):
    from modules.flow_gate.services import test_run_service as old
    return generated_boundary(doc, lambda: old._open_execution_tsr_in_guard(doc, cases, run, locale=locale))


def conversion_canvas(**payload):
    from modules.flow_gate.graph_workflow.services import canvas_events
    from .pending import enqueue_action
    enqueue_action(lambda: canvas_events.publish_canvas_changed(**payload))



def conversion_identity(doc, new_row):
    """Copy the committed-store revision into the replacement identity, never reset it to CREATE r0."""
    import json
    from modules.flow_gate.db.connection import get_store
    store = get_store()
    if not in_transaction() or flow_guard.held_guard() != flow_guard.document_guard_key(doc, doc["doc_id"]):
        raise internal_error()
    fresh = documents.get_by_id(doc["doc_id"])
    event = store._fetch_one(
        "SELECT metadata FROM workflow_events WHERE document_id = ? AND event_type = 'doc_edited' ORDER BY id DESC LIMIT 1",
        [doc["id"]])
    meta = json.loads((event or {}).get("metadata") or "{}")
    if not (fresh and fresh["revision_no"] == doc["revision_no"] == new_row["revision_no"]
            and fresh["file_path"] == new_row["file_path"] and meta.get("origin") == "root_conversion"
            and meta.get("revision_no") == doc["revision_no"] and meta.get("old_doc_id") == doc["doc_id"]
            and meta.get("final_doc_id") == new_row["doc_id"] and documents.get_by_id(new_row["doc_id"]) is None):
        raise internal_error()
    inserted = documents.create(new_row)
    # documents.create deliberately initializes revision 0. This is identity transfer of an
    # already revised row, not another edit or an arbitrary caller-supplied creation revision.
    affected = store._execute_affected(
        "UPDATE documents SET revision_no = ?, updated_at = ? WHERE id = ? AND revision_no = 0",
        [doc["revision_no"], doc["updated_at"], inserted["id"]])
    if type(affected) is not int or affected != 1:
        raise internal_error()
    return documents.get_by_id(new_row["doc_id"])
