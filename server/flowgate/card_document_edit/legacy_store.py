"""L#1 §2.11 storage interface for the T#2 legacy inbox adapter (certain non-cards only)."""
from dataclasses import replace
from modules.flow_gate.db import documents
from modules.flow_gate.db.connection import in_transaction, after_commit
from . import boundary, target, store, idempotency
from .body import read_body_bytes, keep_sibling_exists
from .contracts import ContactHolder, RestoreLog, StoreInput
from .errors import internal_error


def legacy_store(req):
    """DedicatedRequest-like input. Caller owns existing response and post-commit processing.

    Its build must provide a canonical target when the old file is missing. No expected
    revision is accepted from the legacy client: the protected current revision is used.
    """
    if in_transaction():
        raise internal_error()
    doc = documents.get_by_id(req.doc_id)
    if doc is None:
        req.rule_call.precheck(doc)
        raise internal_error()
    target.require_non_card(doc)
    holder, restore_log = ContactHolder(), RestoreLog()
    def stage(ctx):
        fresh = documents.get_by_id(req.doc_id)
        if fresh is None:
            req.rule_call.precheck(fresh)
            raise internal_error()
        target.require_non_card(fresh, inside=True)  # first guarded judgement
        req.rule_call.precheck(fresh)
        current_request = replace(req, expected_revision=fresh["revision_no"])
        body = read_body_bytes(fresh)
        if keep_sibling_exists(body.path):
            raise internal_error()
        built = req.rule_call.build(fresh, body)
        binding = idempotency.make_binding(current_request, fresh)
        plan = store.commit_revision(ctx, StoreInput(fresh, body, built, req.rule_call, current_request,
                                                    binding, restore_log, allow_missing_previous=True))
        plan.card_slot, plan.legacy_non_card = False, True
        assert after_commit(lambda: holder.put(plan))
        return {**built.result, "doc_id": req.doc_id, "revision_no": plan.doc["revision_no"],
                "updated_at": plan.doc["updated_at"]}
    try:
        result = boundary.enter(doc, boundary.OWN, stage)
    except Exception as exc:
        if restore_log.has_failures():
            raise internal_error() from exc
        raise
    plan = holder.take()
    if plan is None:
        raise internal_error()
    return result, plan
