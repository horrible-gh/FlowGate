"""0428: durable, single-dispatch continuation of a confirmed TSR test stop.

Decisions and execution outcomes are separate. Uncertain launches require recovery.
"""
from __future__ import annotations

import json
import logging
import threading
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar

_launch_dispatch = ContextVar("completion_launch_dispatch", default=None)

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
        if row is not None:
            return None
        # A consumed continuation token and the original test-gate hop are the
        # proof for a pending intent. Never invent a pause from another stop.
        from modules.flow_gate.services import test_run_service
        ts = db_docs.get_by_id(doc.get("target_id") or doc.get("triggered_by"))
        token = test_run_service._continuation_token_for_doc(ts) if ts else None
        origin = runtime.get_run_record((token or {}).get("ai_run_id")) if token else None
        paired = db_seq.get_paired_instruction_item(doc["doc_id"], "TS")
        if (not origin or not token or not paired or paired["sequence_id"] != item["sequence_id"]
                or paired.get("result_doc_id") != ts["doc_id"]
                or origin.get("group_id") != doc["group_id"]
                or origin.get("test_gate_doc_id") != ts["doc_id"]
                or origin.get("issued_to") != token.get("issued_to")
                or origin.get("mode") != "continuous" or not origin.get("chain_id")
                or origin.get("stop_code") not in {"test_run_pending", "test_gate_blocked"}):
            return None
        return {"chain_id": origin["chain_id"], "stop_run_id": token["ai_run_id"],
                "stop_snapshot": {"row": None, "group_id": doc["group_id"],
                    "paused_by": token["issued_to"], "ts_doc_id": ts["doc_id"],
                    "api_base_url": test_run_service._chain_api_base(None, ts) or api_base_url,
                    "locale": token.get("continuation_locale") or locale}}
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
        binding = _launch_dispatch.set(dispatch_id)
        try:
            yield dispatch
        finally:
            _launch_dispatch.reset(binding)


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
    snapshot = json.loads(dispatch["stop_snapshot_json"])
    group_id = (snapshot.get("row") or snapshot)["group_id"]
    with runtime.group_resume_lock(group_id):
        return _consume_locked(dispatch_id)


def _consume_locked(dispatch_id):
    dispatch = db_completion.dispatch_by_id(dispatch_id)
    if dispatch["state"] in {"running", "recovery_required"}:
        return recover(dispatch)
    if dispatch["state"] not in {"pending", "retryable_failed"}:
        return projection(dispatch)
    decision = db_completion.get_decision(dispatch["decision_id"])
    current = next((i for i in db_seq.get_sequence_items(decision["sequence_id"])
                    if i["id"] == decision["slot_id"]), None)
    if (not current or current.get("completion_decision_id") != decision["decision_id"]
            or completion_reason(current) != "proceeded_unapproved"):
        _transition(dispatch, "cancelled", error_code="completion_invalidated")
        return projection(db_completion.dispatch_by_id(dispatch_id))
    snapshot = json.loads(dispatch["stop_snapshot_json"])
    if snapshot.get("row") is None:
        row = db_paused.get_by_group(snapshot["group_id"])
        if row is None:
            return projection(dispatch)  # finalizer has not parked yet
        decision = db_completion.get_decision(dispatch["decision_id"])
        doc = db_docs.get_by_id(decision["result_doc_id"])
        item = next((i for i in db_seq.get_sequence_items(decision["sequence_id"])
                     if i["id"] == decision["slot_id"]), None)
        if (not item or item.get("completion_decision_id") != decision["decision_id"]
                or completion_reason(item) != "proceeded_unapproved"
                or row.get("chain_id") != dispatch["chain_id"]
                or row.get("stop_run_id") != dispatch["stop_run_id"]
                or row.get("paused_by") != snapshot["paused_by"]
                or not _matches_report(doc, item, row)):
            _transition(dispatch, "recovery_required", error_code="completion_late_stop_changed")
            return projection(db_completion.dispatch_by_id(dispatch_id))
        snapshot["row"] = row
        if not db_completion.attach_stop(dispatch, snapshot):
            return projection(db_completion.dispatch_by_id(dispatch_id))
        dispatch = db_completion.dispatch_by_id(dispatch_id)
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
            # Once admission crossed its durable launch boundary even a known
            # HTTP/ValueError may be a lost response from an existing worker.
            crossed = fresh.get("error_code") == "completion_launch_attempted"
            if fresh.get("token_id") or crossed:
                known_failure = False
            state = "retryable_failed" if restored and known_failure else "recovery_required"
            if state != fresh["state"]:
                _transition(fresh, state, error_code=code[:160])
    return projection(db_completion.dispatch_by_id(dispatch_id))


def consume_pending():
    """Startup and finalizer replay; uncertain claims are reconciled, never relaunched."""
    results = []
    for dispatch in db_completion.unfinished_dispatches():
        try:
            results.append(consume(dispatch["dispatch_id"]))
        except Exception:
            import logging
            logging.getLogger(__name__).exception("completion dispatch sweep failed for %s", dispatch["dispatch_id"])
    return results


def same_stop(row, snapshot):
    # Re-insertion after a proven advance failure gets new storage bookkeeping.
    # Ownership, paused_at, chain, stop identity and EVERY setting still match.
    if row is None:
        return False
    ignored = {"id", "created_at", "updated_at"}
    return {k: v for k, v in row.items() if k not in ignored} == {
        k: v for k, v in snapshot.items() if k not in ignored}


def reserve_run(run_id):
    ident = _launch_dispatch.get()
    if ident is None:
        return run_id
    dispatch = db_completion.dispatch_by_id(ident)
    reserved = dispatch.get("run_id") or run_id
    if not _transition(dispatch, "running", run_id=reserved):
        raise ValueError("completion_dispatch_conflict")
    return reserved


def bind_token(run_id, token_id):
    ident = _launch_dispatch.get()
    if ident is not None:
        dispatch = db_completion.dispatch_by_id(ident)
        if not token_id or not _transition(dispatch, "running", run_id=run_id, token_id=token_id):
            raise ValueError("completion_dispatch_conflict")


def before_worker(run):
    ident = _launch_dispatch.get()
    if ident is not None:
        dispatch = db_completion.dispatch_by_id(ident)
        if (dispatch.get("run_id") != run["run_id"] or not dispatch.get("token_id")
                or not _transition(dispatch, "running", error_code="completion_launch_attempted")):
            raise ValueError("completion_dispatch_conflict")
        run["completion_dispatch_id"] = ident


def worker_started(run):
    ident = _launch_dispatch.get()
    if ident is not None:
        run["completion_launch_confirmed"] = True
        dispatch = db_completion.dispatch_by_id(ident)
        if not _transition(dispatch, "completed", run_id=run["run_id"]):
            raise ValueError("completion_dispatch_conflict")


def recover(dispatch):
    """The caller owns the group lock. Only affirmative run evidence settles a claim."""
    from modules.flow_gate.db import ai_invoke_runs, group_ai_leases
    from modules.flow_gate.db.connection import get_store
    snapshot = json.loads(dispatch["stop_snapshot_json"])
    row = snapshot.get("row")
    run_id = dispatch.get("run_id")
    live = runtime.get_run_record(run_id) if run_id else None
    persisted = ai_invoke_runs.get(run_id) if run_id else None
    confirmed = live if (live or {}).get("completion_launch_confirmed") else persisted
    if (confirmed and row and confirmed.get("group_id") == row["group_id"]
            and ((confirmed is live and confirmed.get("chain_id") == dispatch["chain_id"])
                 or (confirmed is persisted and confirmed.get("doc_ref") == row["doc_ref"]))
            and confirmed.get("issued_to") == row["paused_by"]
            and confirmed.get("token_id") == dispatch.get("token_id")
            and confirmed.get("end_reason") not in {"orphaned_by_restart", "lease_expired"}):
        _transition(dispatch, "completed", run_id=run_id)
    elif row and run_id and (group_ai_leases.get_active(row["group_id"]) or {}).get("run_id") == run_id:
        return projection(dispatch)  # another server may still own admission/worker
    elif (row and dispatch["state"] == "running" and not dispatch.get("token_id")
          and dispatch.get("error_code") != "completion_launch_attempted"
          and live is None and persisted is None
          and runtime.active_run_for_group(row["group_id"]) is None
          and group_ai_leases.get_active(row["group_id"]) is None):
        # The source G lock spans claim through admission on every server.
        # Absence of a token/lease alone is not proof that another process died:
        # it may still be between stop consumption and run reservation.
        _recover_prelaunch(dispatch, row)
    elif dispatch["state"] == "running":
        _transition(dispatch, "recovery_required", error_code="completion_launch_unconfirmed")
    return projection(db_completion.dispatch_by_id(dispatch["dispatch_id"]))



def _recover_prelaunch(dispatch, row):
    from modules.flow_gate.services import workflow_proceed_service as command, test_run_service
    from modules.flow_gate.db import ai_invoke_runs, group_ai_leases
    from modules.flow_gate.db.connection import get_store
    decision = db_completion.get_decision(dispatch["decision_id"])
    doc = db_docs.get_by_id(decision["result_doc_id"])
    with ExitStack() as stack:
        stack.enter_context(test_run_service._admission_lock)
        if command.lock_manager.current_context() is None:
            bound = command.lock_manager.new_context("completion_recovery")
            command.lock_manager.bind(bound)
            stack.callback(command.lock_manager.unbind, bound)
        try:
            ctx, key = stack.enter_context(command._group_lock(doc))
        except Exception:
            return  # a live consumer may still be in admission
        with db_completion.decision_transaction(decision["sequence_id"], decision["slot_id"]):
            fresh = db_completion.dispatch_by_id(dispatch["dispatch_id"])
            run_id = fresh.get("run_id")
            if (fresh["state"] != "running" or fresh.get("token_id")
                    or fresh.get("error_code") == "completion_launch_attempted"
                    or (run_id and (runtime.get_run_record(run_id) or ai_invoke_runs.get(run_id)))
                    or runtime.active_run_for_group(row["group_id"]) is not None
                    or group_ai_leases.get_active(row["group_id"]) is not None
                    or not command.lock_manager.still_held(ctx, key)):
                return
            existing = db_paused.get_by_group(row["group_id"])
            if existing is None:
                fields = {k: v for k, v in row.items()
                          if k not in {"id", "mode", "created_at", "updated_at"}}
                db_paused.upsert(**fields)
                existing = db_paused.get_by_group(row["group_id"])
            if same_stop(existing, row):
                _transition(fresh, "retryable_failed", error_code="completion_prelaunch_recovered")
            else:
                _transition(fresh, "recovery_required", error_code="completion_stop_changed")

def continue_for_test(doc):
    """Return None for ordinary PASS; valid human decisions use one durable consumer."""
    from modules.flow_gate.services import test_run_service
    item = test_run_service._tsr_slot_item(doc, db_seq)
    if not item or completion_reason(item) != "proceeded_unapproved":
        return None
    dispatch = db_completion.dispatch_for_decision(item["completion_decision_id"])
    return consume(dispatch["dispatch_id"]) if dispatch else {"continuation_status":"not_started"}


_consumer_lock = threading.Lock()
_consumer_stop = threading.Event()
_consumer_thread = None


def start_consumer():
    """One bounded poller per server instance; DB CAS arbitrates other instances."""
    import threading
    global _consumer_thread
    with _consumer_lock:
        if _consumer_thread is not None and _consumer_thread.is_alive():
            return
        _consumer_stop.clear()
        def poll():
            import logging
            while not _consumer_stop.wait(5):
                try:
                    consume_pending()
                except Exception:
                    logging.getLogger(__name__).exception("completion dispatch recovery sweep failed")
        _consumer_thread = threading.Thread(target=poll, name="workflow-completion-dispatch", daemon=True)
        _consumer_thread.start()


def stop_consumer():
    with _consumer_lock:
        _consumer_stop.set()
        thread = _consumer_thread
    if thread is not None:
        thread.join(timeout=10)
