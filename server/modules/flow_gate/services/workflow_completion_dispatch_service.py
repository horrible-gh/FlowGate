"""0428 T#4: continuation of an explicitly confirmed TSR system test stop.

Decisions and execution outcomes are separate. No approval/result writer is called.
T#5 supplies startup/late-finalizer scheduling and reserved execution identity recovery.
"""
from __future__ import annotations

import json
from contextlib import contextmanager, ExitStack

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_sequences as db_seq
from modules.flow_gate.db import workflow_completion as db_completion
from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
from modules.flow_gate.services.ai_invoke import runtime
from modules.flow_gate.services.workflow_completion_service import completion_reason
from modules.flow_gate.services.mutation_policy import human_principal


def _matches_report(doc, item, row):
    """Resolve the TS/report and chain stop through the consumed server token."""
    from modules.flow_gate.services import test_run_service
    if (not row or row.get("stop_kind") != "system"
            or row.get("stop_code") not in {"test_gate_blocked", "test_run_pending"}
            or not row.get("stop_run_id") or not row.get("chain_id")
            or not row.get("paused_by") or row.get("group_id") != doc.get("group_id")):
        return False
    sequence = db_seq.get_sequence_for_member_doc(row["doc_ref"])
    paired = db_seq.get_paired_instruction_item(doc["doc_id"], "TS")
    ts_id = doc.get("target_id") or doc.get("triggered_by")
    ts = db_docs.get_by_id(ts_id) if ts_id else None
    if (not sequence or sequence["id"] != item["sequence_id"] or not paired
            or paired["sequence_id"] != item["sequence_id"]
            or paired.get("result_doc_id") != ts_id or not ts
            or ts.get("group_id") != doc.get("group_id")
            or ts.get("project_id") != doc.get("project_id") or ts.get("type_code") != "TS"):
        return False
    token = test_run_service._continuation_token_for_doc(ts)
    if (not token or token.get("ai_run_id") != row["stop_run_id"]
            or token.get("issued_to") != row["paused_by"]):
        return False
    origin = runtime.get_run_record(row["stop_run_id"])
    if origin is not None and (origin.get("chain_id") != row["chain_id"]
                               or origin.get("test_gate_doc_id") != ts_id):
        return False
    return True


def intent_for_report(doc, item, *, api_base_url=None, locale="ko"):
    row = db_paused.get_by_group(doc["group_id"])
    if not _matches_report(doc, item, row):
        return None
    from modules.flow_gate.services import test_run_service
    ts = db_docs.get_by_id(doc.get("target_id") or doc.get("triggered_by"))
    token = test_run_service._continuation_token_for_doc(ts)
    return {"chain_id": row["chain_id"], "stop_run_id": row["stop_run_id"],
            "stop_snapshot": {"row": row, "ts_doc_id": ts["doc_id"],
                              "api_base_url": test_run_service._chain_api_base(None, ts) or api_base_url,
                              "locale": (token or {}).get("continuation_locale") or locale}}


def projection(dispatch):
    state = dispatch["state"]
    status = ("started" if dispatch.get("run_id") else "ended") if state == "completed" else state
    return {"continuation_status": status, "dispatch_id": dispatch["dispatch_id"],
            "dispatch_state": state, "run_id": dispatch.get("run_id"),
            "continuation_error_code": dispatch.get("error_code")}


def _transition(dispatch, state, **kwargs):
    return db_completion.cas_dispatch(dispatch["dispatch_id"], expected_state=dispatch["state"],
        expected_version=dispatch["state_version"], state=state, **kwargs)


@contextmanager
def launch_context(dispatch_id):
    """Called inside resume's group lock: admission mutex -> G lock -> fresh basis."""
    from modules.flow_gate.services import workflow_proceed_service as command
    from modules.flow_gate.services import test_run_service, test_basis_service
    from modules.flow_gate.db.workflow_invalidation import observe_basis
    dispatch = db_completion.dispatch_by_id(dispatch_id)
    decision = db_completion.get_decision(dispatch["decision_id"]) if dispatch else None
    doc = db_docs.get_by_id(decision["result_doc_id"]) if decision else None
    if not doc or doc.get("type_code") != "TSR":
        raise ValueError("completion_dispatch_invalid")
    with ExitStack() as stack:
        stack.enter_context(test_run_service._admission_lock)
        if command.lock_manager.current_context() is None:
            bound = command.lock_manager.new_context("completion_dispatch")
            command.lock_manager.bind(bound)
            stack.callback(command.lock_manager.unbind, bound)
        ctx, key = stack.enter_context(command._group_lock(doc))
        ts = db_docs.get_by_id(doc.get("target_id") or doc.get("triggered_by"))
        command._safety(doc, human_principal({"user_id": json.loads(dispatch["stop_snapshot_json"])["row"]["paused_by"]}))
        live = test_basis_service.live_state(ts)
        observe_basis(ts["doc_id"], (live.get("live") or {}).get("basis_id")
                      or "unverifiable:" + str(live.get("state")))
        # Observation commits invalidation even when this launch is refused.
        dispatch = db_completion.dispatch_by_id(dispatch_id)
        items = db_seq.get_sequence_items(decision["sequence_id"])
        item = next((i for i in items if i["id"] == decision["slot_id"]), None)
        if (dispatch["state"] not in {"pending", "retryable_failed"} or not item
                or item.get("completion_decision_id") != decision["decision_id"]
                or completion_reason(item) != "proceeded_unapproved"):
            raise ValueError("completion_dispatch_invalidated")
        snapshot = json.loads(dispatch["stop_snapshot_json"])["row"]
        row = db_paused.get_by_group(doc["group_id"])
        if not same_stop(row, snapshot) or not _matches_report(doc, item, row):
            raise ValueError("completion_stop_changed")
        if not command.lock_manager.still_held(ctx, key):
            raise ValueError("completion_source_locked")
        yield dispatch


def claim_stop(dispatch, row):
    """Claim and consume together; CAS failure rolls the dispatch claim back."""
    with db_completion.decision_transaction(
            db_completion.get_decision(dispatch["decision_id"])["sequence_id"],
            db_completion.get_decision(dispatch["decision_id"])["slot_id"]):
        if not _transition(dispatch, "running"):
            raise ValueError("completion_dispatch_conflict")
        consumed = db_paused.release_system_test_snapshot(row)
        if consumed is None or isinstance(consumed, db_paused.ReleaseSuperseded):
            raise ValueError("completion_stop_changed")
        return consumed


def finish_empty(dispatch, row, target_seq):
    """End, exact stop consumption and event are a single durable transaction."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.workflow.event_logger import log_continuous_work_ended
    decision = db_completion.get_decision(dispatch["decision_id"])
    doc = db_docs.get_by_id(decision["result_doc_id"])
    with get_store().transaction():
        claim_stop(dispatch, row)
        log_continuous_work_ended(project_id=doc["project_id"], actor_user_id=row["paused_by"],
            document_id=doc["id"], doc_id=doc["doc_id"], group_id=doc["group_id"], target_seq=target_seq)
        running = db_completion.dispatch_by_id(dispatch["dispatch_id"])
        if not _transition(running, "completed"):
            raise ValueError("completion_dispatch_conflict")
    return {"ok": True, "status": "ended", "run_id": None, "target_seq": target_seq}


def consume(dispatch_id):
    """Consume a durable intent. Uncertain launch errors are kept for recovery."""
    dispatch = db_completion.dispatch_by_id(dispatch_id)
    if dispatch["state"] not in {"pending", "retryable_failed"}:
        return projection(dispatch)
    snapshot = json.loads(dispatch["stop_snapshot_json"])
    row = snapshot["row"]
    from modules.flow_gate.services.ai_invoke import chain
    try:
        started = chain._resume_chain_locked(group_id=row["group_id"], user_id=row["paused_by"],
            api_base_url=snapshot["api_base_url"], locale=snapshot["locale"],
            after_release=[], _completion_dispatch_id=dispatch_id)
        fresh = db_completion.dispatch_by_id(dispatch_id)
        if fresh["state"] == "running":
            if not started.get("run_id"):
                _transition(fresh, "recovery_required", error_code="completion_start_unconfirmed")
            else:
                _transition(fresh, "completed", run_id=started["run_id"])
    except Exception as exc:
        fresh = db_completion.dispatch_by_id(dispatch_id)
        code = str(exc)
        detail = getattr(exc, "detail", {})
        if isinstance(detail, dict):
            code = detail.get("code") or code
        if fresh["state"] in {"pending", "retryable_failed", "running"}:
            # A known advance/admission failure restores the exact stop. An unknown
            # error after consumption can include a successful but unreported start.
            restored = same_stop(db_paused.get_by_group(row["group_id"]), row)
            known_failure = fresh["state"] != "running" or (
                isinstance(detail, dict) and (detail.get("restored") or code in {
                    "no_enabled_provider", "provider_unavailable", "resume_advance_blocked",
                    "resume_advance_unavailable", "resume_lookup_failed"}))
            state = "retryable_failed" if restored and known_failure else "recovery_required"
            if state != fresh["state"]:
                _transition(fresh, state, error_code=code[:160])
    return projection(db_completion.dispatch_by_id(dispatch_id))


def consume_pending():
    """Scheduling seam for T#5; only valid stored decisions enter the consumer."""
    return [consume(d["dispatch_id"]) for d in db_completion.pending_dispatches()]


def same_stop(row, snapshot):
    # Re-insertion after a proven advance failure gets new storage bookkeeping.
    # Ownership, paused_at, chain, stop identity and EVERY setting still match.
    if row is None:
        return False
    ignored = {"id", "created_at", "updated_at"}
    return {k: v for k, v in row.items() if k not in ignored} == {
        k: v for k, v in snapshot.items() if k not in ignored}
