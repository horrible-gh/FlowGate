"""L#1 §2.2: the single decision/store path. Returns plans; never executes contacts."""
import logging
from modules.flow_gate.db import documents
from modules.flow_gate.db.connection import in_transaction, after_commit
from flowgate.card_documents import legacy_adapter as legacy
from . import boundary, target, guards, idempotency, common_checks, type_rules, store
from .body import read_body_bytes, keep_sibling_exists
from .contracts import (CONTENT_MAX_CHARS, PROJECTION_FAILED, ContactHolder, ContactPlan, Outcome,
                        RestoreLog, StoreInput)
from .errors import GraphApiError, invalid, coordinates, conflict, not_editable, internal_error
from .validation import check_input_complete

_log = logging.getLogger(__name__)


class ValidateOnlyDone(Exception):
    def __init__(self, verdict):
        self.verdict = verdict


class ReplayDone(Exception):
    def __init__(self, result, plan):
        self.result, self.plan = result, plan


def _result(doc, replayed=False):
    return {"doc_id": doc["doc_id"], "revision_no": doc["revision_no"],
            "updated_at": doc["updated_at"], "replayed": replayed}


def _verdict(req, current):
    return {"action": "edit", "doc_id": current["doc_id"], "expected_revision": req.expected_revision,
            "card_binding": {"flow_id": req.flow_id, "node_key": req.node_key, "slot": req.slot,
                             "doc_id": current["doc_id"]},
            "checks_passed": ["auth", "context_binding", "permission", "editable", "idempotency"]}


def judge_and_store(ctx, req, flow, holder, restore_log):
    guards.check_writable(req.flow_id, guards.principal_of(req, req.flow_id), guards.operation_of(req))
    if req.recheck is not None:
        req.recheck()
    node, spec = target.check_card(flow, req.node_key, req.slot)
    line = target.resolve_current(req.flow_id, node, spec)
    current = (line or {}).get("document")
    if current is None:
        raise GraphApiError(404, "no_document", coordinates(req))
    if current["doc_id"] != req.expected_doc_id:
        raise GraphApiError(409, "slot_document_changed", {**coordinates(req, current), "expected_doc_id": req.expected_doc_id})
    if current.get("group_id") != req.flow_id or ctx.locked_type != boundary.type_lock_of(current.get("type_code")):
        raise internal_error()
    guards.check_editable(req, current)
    rule = type_rules.rule_for(current, req)
    if rule is None:
        raise not_editable(req, current, "unsupported_type", type_code=current.get("type_code"))
    body = read_body_bytes(current)
    if body.state != "readable":
        raise not_editable(req, current, "body_unreadable", body_state=body.state)
    if keep_sibling_exists(body.path):
        raise internal_error()
    if req.size_guard is not None:
        req.size_guard(current, body, target.with_body(line, current, body))
    try:
        binding = idempotency.make_binding(req, current)
    except UnicodeError as exc:
        raise invalid("invalid_content", "content", "invalid_format", current,
                      validation={"reason": "encoding"}) from exc
    if req.idempotency_key is not None:
        verdict = idempotency.judge(idempotency.lookup(flow["project_id"], req.idempotency_key), binding, current)
        if verdict.kind == idempotency.REPLAY:
            if req.validate_only:
                raise ValidateOnlyDone(_verdict(req, current))
            meta = verdict.record["parsed"]
            plan = ContactPlan(current["project_id"], current.get("group_id"), dict(current), req.actor.user_id,
                               meta.get("token_id") if req.actor.kind == "worker" else None,
                               bool(meta.get("node_key") and meta.get("slot")))
            raise ReplayDone({**meta.get("agent_result", {}), "doc_id": meta["doc_id"], "revision_no": meta["revision_no"],
                              "updated_at": meta["updated_at"], "replayed": True}, plan)
        if verdict.kind == idempotency.STALE:
            raise conflict(req, current)
        if verdict.kind == idempotency.REUSED:
            raise GraphApiError(409, "idempotency_key_reused", coordinates(req, current))
    if current["revision_no"] != req.expected_revision:
        raise conflict(req, current)
    if req.structured is not None:
        # §2.15.2: build the next body only here, from the fresh protected read.
        from . import structured_ts_adapter
        text = structured_ts_adapter.build_text(req.structured, current, body)
    else:
        text = req.content
    if len(text) > CONTENT_MAX_CHARS:
        raise invalid("invalid_content", "content", "invalid_format", current,
                      validation={"reason": "too_long", "max": CONTENT_MAX_CHARS})
    if req.structured is None and hasattr(rule, "prepare"):
        text = rule.prepare(req.content, current, body)
    common_checks.identity_frontmatter(text, current, rule, body)
    common_checks.encoding_guard(text, current, req.transport_extras.get("encoding"), req.locale)
    built = rule.validate_and_build(text, current, body)
    if req.validate_only:
        verdict = _verdict(req, current)
        if req.origin.kind == "agent" and "_agent_checks" in built.result:
            verdict["checks_passed"] = built.result["_agent_checks"]
            for key in ("normalizations", "worktree_untracked"):
                if key in built.result:
                    verdict[key] = built.result[key]
            if "_agent_tr_scope" in built.result:
                verdict["tr_scope"] = built.result["_agent_tr_scope"]
        raise ValidateOnlyDone(verdict)
    plan = store.commit_revision(ctx, StoreInput(current, body, built, rule, req, binding, restore_log))
    plan.card_slot = True
    assert after_commit(lambda: holder.put(plan))
    return {**built.result, **_result(plan.doc)}


def revise(req):
    if in_transaction():
        raise internal_error()
    check_input_complete(req)
    flow = legacy.load_flow(req.flow_id)
    node, spec = target.check_card(flow, req.node_key, req.slot)
    guards.authorize(req.actor, flow["project_id"], "perm_document_update", flow_id=req.flow_id)
    lock_doc = documents.get_by_id(req.expected_doc_id) or {"doc_id": req.expected_doc_id, "group_id": req.flow_id}
    holder, restore_log = ContactHolder(), RestoreLog()
    try:
        result = boundary.enter(lock_doc, boundary.OWN,
            lambda ctx: judge_and_store(ctx, req, flow, holder, restore_log), flow_id=req.flow_id)
    except ValidateOnlyDone as done:
        return Outcome("VALIDATED", verdict=done.verdict)
    except ReplayDone as done:
        outcome = Outcome("REPLAYED", result=done.result, plan=done.plan, contacts="REPLAY")
    except Exception as exc:
        if restore_log.has_failures():
            _log.error("document_restore_incomplete %s", restore_log.failures)
            raise internal_error() from exc
        if isinstance(exc, GraphApiError) and exc.code not in {"file_changed", "document_storage_unwritable"}:
            raise
        _log.exception("card document revision failed")
        raise internal_error() from exc
    else:
        plan = holder.take()
        if restore_log.has_failures() or plan is None:
            raise internal_error()
        outcome = Outcome("SAVED", result=result, plan=plan, contacts="NORMAL")
    try:
        outcome.line = target.line_projection(req.flow_id, node, spec)
    except Exception:
        _log.exception("committed card document line projection failed")
        outcome.line = PROJECTION_FAILED
    return outcome
