"""0428 T#2: human-only TSR/TR2 slot completion without approval side effects.

The explicit POST is the confirmation command. T#4 records the matching TSR
continuation intent in its transaction and consumes it after committing.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager

from modules.flow_gate import process_service
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import test_runs as db_test_runs
from modules.flow_gate.db import tr2_approval_attempts as db_attempts
from modules.flow_gate.db import tr_history_recovery as db_recovery
from modules.flow_gate.db import workflow_completion as db_completion
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.documents import tr2_precheck, tr2_service
from modules.flow_gate.services import spec_execution_service, test_run_service
from modules.flow_gate.services.ai_invoke import runtime
from modules.flow_gate.services.git import lock_manager
from modules.flow_gate.services.mutation_policy import (
    MutationPolicyError, assert_group_mutation_allowed,
)
from modules.flow_gate.services.workflow_completion_service import completion_reason
from modules.flow_gate.workflow import pipeline_service
from modules.flow_gate.workflow.event_logger import log_event
from modules.flow_gate.workflow.transition_rules import check_permission


def _refuse(code: str, status: int = 409, **details):
    raise MutationPolicyError(status, code, code, **details)


@contextmanager
def _group_lock(doc):
    outcome, ctx = lock_manager.acquire_group(
        doc["project_id"], doc["group_id"], holder_kind="workflow_proceed_unapproved")
    if not outcome.ok:
        _refuse("completion_source_locked", **lock_manager.outcome_details(outcome))
    try:
        yield ctx, outcome.lock_key
    finally:
        lock_manager.release(ctx, outcome.lock_key)


def _identity(doc_id, expected_revision, expected_slot_id, expected_slot_generation):
    doc = db_docs.get_by_id(doc_id)
    if not doc:
        _refuse("completion_document_not_found", 404)
    if doc.get("type_code") not in {"TSR", "TR2"}:
        _refuse("completion_type_not_supported", 422)
    if (not doc.get("project_id") or not doc.get("group_id")
            or doc.get("status") not in {"open", "draft"}
            or doc.get("doc_review_status") not in {"pending_review", "rejected", "revised"}):
        _refuse("completion_document_not_eligible")
    if int(doc.get("revision_no") or 0) != expected_revision:
        _refuse("completion_revision_changed")
    try:
        slot = db_wfseq.get_item_by_result_doc_id(doc_id)
        if slot is None:
            _refuse("completion_slot_missing")
        if (slot.get("id") != expected_slot_id
                or slot.get("slot_generation") != expected_slot_generation
                or slot.get("type") != doc["type_code"]):
            _refuse("completion_slot_changed")
        sequence = db_wfseq.get_sequence_by_id(slot["sequence_id"])
        root = db_docs.get_by_id(sequence["doc_id"]) if sequence else None
        if (not root or root.get("group_id") != doc["group_id"]
                or root.get("project_id") != doc["project_id"] or sequence.get("done_at")):
            _refuse("completion_sequence_changed")
        items = db_wfseq.get_sequence_items(slot["sequence_id"])
        item = next((i for i in items if i.get("id") == slot["id"]), None)
        if item is None:
            _refuse("completion_slot_changed")
    except MutationPolicyError:
        raise
    except Exception as exc:
        raise MutationPolicyError(409, "workflow_head_unavailable", "workflow_head_unavailable") from exc
    return doc, item


def _safety(doc, principal):
    if process_service.is_group_disposed(doc["group_id"]):
        _refuse("group_disposed")
    assert_group_mutation_allowed(doc["group_id"], principal, "proceed_unapproved")
    if runtime.active_run_for_group(doc["group_id"]) is not None:
        _refuse("completion_ai_run_active")
    if db_test_runs.get_active_by_group(doc["group_id"]) is not None:
        _refuse("completion_test_run_active")
    if db_recovery.has_unresolved(doc["group_id"]):
        _refuse("tr_history_recovery_required")
    blocking = db_attempts.blocking_by_group(doc["group_id"])
    if blocking is not None:
        _refuse("tr2_recovery_required" if blocking["state"] == "recovery_required"
                else "tr2_in_progress")
    # The approval helper exempts the pending TR2 itself. This command must refuse
    # that document too: skipping cannot leave its earlier commit live.
    if tr2_service.group_revert_pending(doc["group_id"]):
        _refuse("workflow_revert_pending")
    if doc["type_code"] == "TSR":
        ts_id = doc.get("target_id") or doc.get("triggered_by")
        ts = db_docs.get_by_id(ts_id) if ts_id else None
        paired = db_wfseq.get_paired_instruction_item(doc["doc_id"], "TS")
        if (not ts or ts.get("type_code") != "TS" or ts.get("group_id") != doc["group_id"]
                or ts.get("project_id") != doc["project_id"]
                or not paired or paired.get("result_doc_id") != ts_id):
            _refuse("completion_test_basis_conflict")
        blocker = spec_execution_service.admission_blocker(ts_id)
        if blocker:
            _refuse("completion_test_not_settled", **blocker)


def _tr2_baseline(doc, ctx, key):
    """Read-only source identity check; readiness, apply and validation are absent."""
    if not lock_manager.still_held(ctx, key):
        _refuse("completion_source_locked")
    body = tr2_service.load_current(doc)
    canonical = tr2_service.canonicalize(tr2_service.validate(body, doc=doc))
    tr2_service.verify_pair(doc["doc_id"], canonical)
    baseline = body.get("baseline_fingerprint")
    if not isinstance(baseline, str) or not tr2_service._SOURCE_HEX.fullmatch(baseline):
        _refuse("tr2_history_invariant_error")
    root = tr2_precheck._approval_root(doc["project_id"], doc["group_id"])
    tr2_precheck._clean(root)
    if tr2_service.target_fingerprint(canonical["edit_spec"], root) != baseline:
        _refuse("tr2_source_drift")


def proceed_unapproved(*, doc_id: str, expected_revision: int, expected_slot_id: int,
                       expected_slot_generation: int, request_key: str,
                       actor_user_id: str, user_permissions: set[str], principal,
                       locale: str = "ko", api_base_url: str | None = None) -> dict:
    """Confirm exactly one current unapproved result, preserving its review/result."""
    if (principal is None or principal.kind != "human" or not actor_user_id
            or principal.user_id != actor_user_id):
        _refuse("completion_human_confirmation_required", 403)
    if not check_permission(user_permissions, ("document.approve",)):
        _refuse("completion_permission_denied", 403)
    if (any(type(n) is not int or n < minimum for n, minimum in (
            (expected_revision, 0), (expected_slot_id, 1), (expected_slot_generation, 0)))
            or not isinstance(request_key, str) or not request_key.strip()
            or len(request_key) > 128):
        _refuse("completion_request_invalid", 422)
    doc, item = _identity(doc_id, expected_revision, expected_slot_id, expected_slot_generation)
    with ExitStack() as stack:
        stack.enter_context(runtime.group_resume_lock(doc["group_id"]))
        # Same order as test admission (admission mutex before source G). It also
        # protects result entry while this command snapshots the current report.
        stack.enter_context(test_run_service._admission_lock)
        if doc["type_code"] == "TR2":
            stack.enter_context(tr2_service._lock(doc_id))
        ctx, key = stack.enter_context(_group_lock(doc))
        # Validate the linked TS/group before any durable Basis observation.
        _safety(doc, principal)
        if doc['type_code'] == 'TSR':
            from modules.flow_gate.services import test_basis_service
            from modules.flow_gate.db.workflow_invalidation import observe_basis
            ts_id = doc.get('target_id') or doc.get('triggered_by')
            ts = db_docs.get_by_id(ts_id) if ts_id else None
            state = test_basis_service.live_state(ts)
            basis_token = ((state.get('live') or {}).get('basis_id')
                           or 'unverifiable:' + str(state.get('state')))
            if ts is not None:
                observe_basis(ts['doc_id'], basis_token)
        stack.enter_context(db_completion.decision_transaction(item["sequence_id"], item["id"]))
        # Re-read after waiting and after the DB slot lock. Admission, audit and
        # decision CAS share this transaction; stale requests produce no decision.
        fresh, item = _identity(doc_id, expected_revision, expected_slot_id, expected_slot_generation)
        if (fresh["project_id"], fresh["group_id"], fresh["type_code"]) != (
                doc["project_id"], doc["group_id"], doc["type_code"]):
            _refuse("completion_document_changed")
        _safety(fresh, principal)
        replay = completion_reason(item) == "proceeded_unapproved"
        if not replay:
            try:
                pipeline_service._require_workflow_head_for_approval(fresh, locale)
            except pipeline_service.TransitionError as exc:
                raise MutationPolicyError(409, getattr(exc, "code", "workflow_not_head"), str(exc)) from exc
        if fresh["type_code"] == "TR2":
            _tr2_baseline(fresh, ctx, key)
        if not lock_manager.still_held(ctx, key):
            _refuse("completion_source_locked")
        from modules.flow_gate.services import workflow_completion_dispatch_service as dispatch_service
        intent = dispatch_service.intent_for_report(fresh, item, api_base_url=api_base_url,
                                                    locale=locale) if fresh["type_code"] == "TSR" and not replay else None
        try:
            decision = db_completion.record_decision(
                sequence_id=item["sequence_id"], slot_id=item["id"],
                slot_generation=expected_slot_generation, result_doc_id=doc_id,
                document_revision=expected_revision, actor_user_id=actor_user_id,
                request_key=request_key, dispatch=intent)
        except db_completion.CompletionConflict as exc:
            raise MutationPolicyError(409, str(exc), str(exc)) from exc
        if decision.get("invalidated_at") is not None:
            _refuse("completion_slot_changed")
        if not replay:
            log_event(event_type="workflow_proceeded_unapproved",
                      project_id=fresh["project_id"], group_id=fresh["group_id"],
                      document_id=fresh.get("id"), actor_user_id=decision["actor_user_id"],
                      metadata={"decision_id": decision["decision_id"], "doc_id": doc_id,
                                "sequence_id": item["sequence_id"], "slot_id": item["id"],
                                "slot_generation": expected_slot_generation,
                                "document_revision": expected_revision,
                                "decided_at": decision["decided_at"],
                                "doc_review_status": fresh["doc_review_status"],
                                "source_applied": False if fresh["type_code"] == "TR2" else None})
        response = {"decision_id": decision["decision_id"], "slot_completed": True,
                "completion_reason": "proceeded_unapproved", "doc_id": doc_id,
                "doc_review_status": fresh["doc_review_status"],
                "actor_user_id": decision["actor_user_id"], "decided_at": decision["decided_at"],
                "sequence_id": item["sequence_id"], "slot_id": item["id"],
                "slot_generation": expected_slot_generation, "document_revision": expected_revision,
                "continuation_status": "not_started", "run_id": None}

    # Commit the decision and intent, then release admission/source/resume locks
    # before the consumer takes the same locks in the established order.
    dispatch = db_completion.dispatch_for_decision(decision["decision_id"])
    if dispatch is not None:
        response.update(dispatch_service.consume(dispatch["dispatch_id"]))
    return response
