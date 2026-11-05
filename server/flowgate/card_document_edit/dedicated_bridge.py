"""Dedicated OWN/CREATE and trusted internal JOIN storage (L#1 §2.9, L#2 §2.7)."""
import logging
from modules.flow_gate.db import documents
from modules.flow_gate.db.connection import in_transaction, after_commit, after_rollback
from modules.flow_gate.graph_workflow.services.flow_guard import held_guard
from . import boundary, target, contacts, store, idempotency
from .body import read_body_bytes, keep_sibling_exists
from .contracts import ContactHolder, RestoreLog, StoreInput
from .errors import internal_error

_log = logging.getLogger(__name__)


def stage_dedicated(ctx, req, restore_log):
    fresh = documents.get_by_id(req.doc_id)
    req.rule_call.precheck(fresh)
    if fresh is None or fresh["revision_no"] != req.expected_revision:
        raise req.rule_call.conflict(fresh)
    if ctx.locked_type != boundary.type_lock_of(fresh.get("type_code")):
        raise internal_error()
    body = read_body_bytes(fresh)
    if keep_sibling_exists(body.path):
        raise internal_error()
    built = req.rule_call.build(fresh, body)
    binding = idempotency.make_binding(req, fresh)
    plan = store.commit_revision(ctx, StoreInput(fresh, body, built, req.rule_call, req, binding, restore_log))
    plan.card_slot = None
    result = {**built.result, "doc_id": fresh["doc_id"], "revision_no": plan.doc["revision_no"],
              "updated_at": plan.doc["updated_at"]}
    return result, plan


def dedicated_store(req):
    if in_transaction():
        raise internal_error()
    doc = documents.get_by_id(req.doc_id)
    if doc is None:
        req.rule_call.precheck(doc)
        raise internal_error()
    holder, restore_log = ContactHolder(), RestoreLog()
    def stage(ctx):
        result, plan = stage_dedicated(ctx, req, restore_log)
        assert after_commit(lambda: holder.put(plan))
        return result
    try:
        result = boundary.enter(doc, boundary.OWN, stage)
    except Exception as exc:
        if restore_log.has_failures():
            _log.error("document_restore_incomplete %s", restore_log.failures)
            raise internal_error() from exc
        raise
    plan = holder.take()
    if plan is None:
        raise internal_error()
    return result, plan


def dedicated_save(req):
    result, plan = dedicated_store(req)
    plan.card_slot = target.notify_scope(target.classify_card_slot(documents.get_by_id(req.doc_id)))
    contacts.run(plan)
    return result


class PendingContacts:
    def __init__(self, restore_log):
        self.state, self.plan, self.restore_log = "STAGED", None, restore_log

    def mark_committed(self):
        self.state = "COMMITTED"

    def discard(self):
        self.state, self.plan = "DISCARDED", None

    def run_if_committed(self):
        assert not in_transaction() and held_guard() is None
        if self.state in {"DISCARDED", "RUN"} or self.plan is None:
            return "NOT_RUN"
        if self.state != "COMMITTED":
            _log.error("pending_contacts_not_committed")
            return "NOT_RUN"
        self.plan.card_slot = target.notify_scope(target.classify_card_slot(
            documents.get_by_id(self.plan.doc["doc_id"])))
        contacts.run(self.plan)
        self.state = "RUN"
        return "RUN"


def dedicated_create(req):
    if not in_transaction() or not req.new_document or req.expected_revision != 0:
        raise internal_error()
    doc = documents.get_by_id(req.doc_id)
    if doc is None or doc["revision_no"] != 0:
        raise internal_error()
    restore_log = RestoreLog()
    pending = PendingContacts(restore_log)
    def stage(ctx):
        result, plan = stage_dedicated(ctx, req, restore_log)
        pending.plan = plan
        assert after_commit(pending.mark_committed)
        assert after_rollback(pending.discard)
        return result
    result = boundary.enter(doc, boundary.CREATE, stage, new_document=True)
    return result, pending


def dedicated_join(req, restore_log=None):
    """Join only the matching durable guard; contacts belong to the outer commit."""
    doc = documents.get_by_id(req.doc_id)
    if doc is None:
        req.rule_call.precheck(doc)
        raise internal_error()
    restore_log = restore_log if restore_log is not None else RestoreLog()
    pending = PendingContacts(restore_log)
    def stage(ctx):
        result, pending.plan = stage_dedicated(ctx, req, restore_log)
        assert after_commit(pending.mark_committed)
        assert after_rollback(pending.discard)
        return result
    result = boundary.enter(doc, boundary.JOIN, stage)
    return result, pending
