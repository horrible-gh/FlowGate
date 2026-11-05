"""L#1 §2.11 storage interface for the T#2 legacy inbox adapter (certain non-cards only).

0127 CH0028: the chat doc_id path (``chat_documents``) uses the same storage with two
optional additions that the legacy inbox never sends: ``expected_revision`` (a stale base
is the rule call's ``conflict``) and a request ``idempotency_key`` (the L#1 §2.5 replay /
reuse / stale judgement, before the revision comparison, as in ``revise.judge_and_store``).
"""
from dataclasses import replace
from modules.flow_gate.db import documents
from modules.flow_gate.db.connection import in_transaction, after_commit
from . import boundary, target, store, idempotency
from .body import read_body_bytes, keep_sibling_exists
from .contracts import ContactHolder, ContactPlan, RestoreLog, StoreInput
from .errors import GraphApiError, internal_error


class LegacyReplay(Exception):
    """An exact earlier save of the same idempotency key: its result, never a second write."""

    def __init__(self, result, plan):
        self.result, self.plan = result, plan


def _judge_idempotency(req, fresh, binding):
    key = getattr(req, "idempotency_key", None)
    if key is None:
        return
    verdict = idempotency.judge(idempotency.lookup(fresh["project_id"], key), binding, fresh)
    if verdict.kind == idempotency.REPLAY:
        meta = verdict.record["parsed"]
        plan = ContactPlan(fresh["project_id"], fresh.get("group_id"), dict(fresh), req.actor.user_id, None, False)
        plan.legacy_non_card = True
        raise LegacyReplay({"doc_id": meta["doc_id"], "revision_no": meta["revision_no"],
                            "updated_at": meta["updated_at"], "replayed": True}, plan)
    if verdict.kind == idempotency.STALE:
        raise req.rule_call.conflict(fresh)
    if verdict.kind == idempotency.REUSED:
        raise GraphApiError(409, "idempotency_key_reused",
                            {"doc_id": fresh["doc_id"], "revision_no": fresh["revision_no"]})


def legacy_store(req, *, expected_revision=None):
    """DedicatedRequest-like input. Caller owns existing response and post-commit processing.

    Its build must provide a canonical target when the old file is missing. Without
    ``expected_revision`` (the legacy client) the protected current revision is used; with it,
    a different protected revision is refused through ``rule_call.conflict``.
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
        base = fresh["revision_no"] if expected_revision is None else expected_revision
        current_request = replace(req, expected_revision=base)
        body = read_body_bytes(fresh)
        if keep_sibling_exists(body.path):
            raise internal_error()
        binding = idempotency.make_binding(current_request, fresh)
        _judge_idempotency(req, fresh, binding)
        if expected_revision is not None and fresh["revision_no"] != expected_revision:
            raise req.rule_call.conflict(fresh)
        built = req.rule_call.build(fresh, body)
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
