"""WP fill JOIN and unbounded deployment-only revision lineage (L#2 §2.8.3)."""
import json
from modules.flow_gate.db import documents
from modules.flow_gate.db.connection import get_store, after_commit, after_rollback
from modules.flow_gate.graph_workflow.services import flow_guard
from modules.flow_gate.storage import files
from flowgate.card_documents import repository
from flowgate.card_settings import repository as settings
from flowgate.work_plan_deploy import legacy_adapter as legacy, protection
from . import dedicated_bridge, target as card_target, contacts
from .contracts import Actor, Built, DedicatedRequest, Origin, RestoreLog, ContactPlan


def lock_rows(flow_id, wp_doc_id):
    if flow_guard.held_guard() != ("flow", flow_id):
        raise flow_guard.GuardNotHeld(("flow", flow_id), flow_guard.held_guard())
    for doc in sorted(legacy.flow_documents(flow_id), key=lambda d: d["doc_id"]):
        if doc["doc_id"] != wp_doc_id:
            flow_guard.lock_document_row(doc["doc_id"])


def deployment_only(doc, mark, flow_id, row):
    n = int(doc.get("revision_no") or 0)
    if (mark is not None and mark.get("instruction_doc_id") == doc["doc_id"]
            and mark.get("instruction_revision_no") is not None and mark["instruction_revision_no"] != n):
        return False
    events = get_store()._fetch_all(
        "SELECT metadata FROM workflow_events WHERE document_id = ? AND event_type = 'doc_edited' ORDER BY id",
        [doc["id"]])
    if n == 0:
        return not events
    if not mark or len(events) != n:
        return False
    for i, event in enumerate(events):
        meta = json.loads(event.get("metadata") or "{}")
        if not (meta.get("origin") == "wp_deploy" and meta.get("event_reason") == "wp_deploy_fill"
                and meta.get("base_revision_no") == i and meta.get("revision_no") == i + 1
                and meta.get("flow_id") == flow_id and meta.get("node_key") == row["node_key"]
                and meta.get("slot") == row["slot"] and meta.get("doc_id") == doc["doc_id"]
                and meta.get("body_saved_source") == legacy.DEPLOY_SOURCE
                and meta.get("wp_doc_id") == mark.get("wp_doc_id")):
            return False
    return True


def fill(state, key, instruction_target, verdict_doc, path, identity, text, instructions, actor, locale, run):
    from flowgate.work_plan_deploy import service
    doc_id = verdict_doc["doc_id"]
    flow_guard.lock_document_row(doc_id)
    fresh = documents.get_by_id(doc_id)
    row = next((r for r in repository.rows(state.flow_id, [key]) if r["doc_id"] == doc_id), None)
    mark = settings.values(state.flow_id, key).get(service.SOURCE_FIELD)
    verdict = protection.judge(state.flow_id, row, {doc_id: fresh},
        started=legacy.node_started(state.flow_id, key), deploy_mark=mark) if row else None
    if (verdict is None or verdict.state != protection.UNTOUCHED or verdict.identity != identity
            or fresh["revision_no"] != verdict_doc["revision_no"]):
        raise service.DeployError(409, "instruction_target_changed", {"doc_id": doc_id})
    data = service._instruction_file(verdict.text, fresh, instructions[instruction_target.card_id], locale).encode("utf-8")
    class Rule:
        needs_current = True
        def precheck(self, doc):
            if doc is None:
                raise self.conflict(doc)
        def conflict(self, doc):
            return service.DeployError(409, "instruction_target_changed", {"doc_id": doc_id})
        def build(self, doc, body):
            # protection.judge uses the legacy universal-newline reader, while
            # the revision snapshot preserves the exact bytes for the backup.
            # Compare text in that reader's representation without weakening the
            # file identity fence or changing the stored before-image.
            legacy_text = None if body.text is None else body.text.replace("\r\n", "\n").replace("\r", "\n")
            if body.identity != verdict.identity or legacy_text != verdict.text:
                raise self.conflict(doc)
            return Built(data, target_path=body.path, body_saved_source=legacy.DEPLOY_SOURCE,
                event_metadata={"node_key": key, "slot": row["slot"], "wp_doc_id": state.doc["doc_id"],
                                "body_saved_source": legacy.DEPLOY_SOURCE})
    class DeployRestoreLog(RestoreLog):
        def add(self, doc_id, keep):
            super().add(doc_id, keep)
            run.restore_failed.append(doc_id)
    log = DeployRestoreLog()
    req = DedicatedRequest(doc_id, fresh["revision_no"], Actor(actor), Rule(), Origin("wp_deploy"),
                           event_reason="wp_deploy_fill", content=data.decode("utf-8"))
    try:
        result, pending = dedicated_bridge.dedicated_join(req, log)
    except files.FileChanged as exc:
        raise service.DeployError(409, "instruction_target_changed", {"doc_id": doc_id}) from exc
    run.pending.append(pending)
    service._mark_instruction(state, key, instruction_target, state.doc["revision_no"], doc_id, data, actor)
    mark = dict(settings.values(state.flow_id, key)[service.SOURCE_FIELD])
    mark["instruction_revision_no"] = result["revision_no"]
    settings.put(state.flow_id, key, service.SOURCE_FIELD, mark, actor)


def created(doc, relative, size, actor, run):
    from modules.flow_gate.storage.body import record_body_saved
    record_body_saved(doc, source=legacy.DEPLOY_SOURCE, file_path=relative, size=size)
    pending = dedicated_bridge.PendingContacts(RestoreLog())
    pending.plan = ContactPlan(doc["project_id"], doc.get("group_id"), doc, actor)
    assert after_commit(pending.mark_committed)
    assert after_rollback(pending.discard)
    run.pending.append(pending)


def finish(run):
    # write_draft already owns the single Canvas notice of this deployment.
    seen = set()
    for pending in run.pending:
        if pending.state != "COMMITTED" or pending.plan.doc["doc_id"] in seen:
            continue
        seen.add(pending.plan.doc["doc_id"])
        pending.plan.card_slot = False
        pending.plan.legacy_non_card = True  # Graph contact, Canvas coalesced with draft publication.
        contacts.run(pending.plan)
        pending.state = "RUN"
