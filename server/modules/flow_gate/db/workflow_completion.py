"""0428 T#1 storage primitives; no public proceed command or dispatch worker.

The command layer must enforce permission/current-head/execution/recovery guards
before calling record_decision. This layer CAS-checks document and slot identity,
never mutates document review status, and atomically stores continuation intent.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from uuid import uuid4

from .connection import FlowGateStore, get_store, now_iso


class CompletionConflict(ValueError):
    pass


def _sql(store, name):
    loader = store._sql if hasattr(store, "_sql") else lambda key: FlowGateStore._sql(store, key)
    return loader("workflow_completion." + name)


def _key(row):
    return tuple(row[name] for name in (
        "sequence_id", "slot_id", "slot_generation", "result_doc_id", "document_revision",
    ))


@contextmanager
def decision_transaction(sequence_id: int, slot_id: int):
    """Serialize command admission and its audit with the decision's slot CAS."""
    store = get_store()
    with store.transaction():
        store._execute(_sql(store, "serialize_slot"), [sequence_id, slot_id])
        yield


def get_decision(decision_id: str) -> dict | None:
    store = get_store()
    return store._fetch_one(_sql(store, "get_decision"), [decision_id])


def get_dispatch(decision_id: str, chain_id: str, stop_run_id: str) -> dict | None:
    store = get_store()
    return store._fetch_one(_sql(store, "get_dispatch"), [decision_id, chain_id, stop_run_id])


def record_decision(
    *, sequence_id: int, slot_id: int, slot_generation: int,
    result_doc_id: str, document_revision: int, actor_user_id: str,
    request_key: str, dispatch: dict | None = None,
) -> dict:
    """Persist/replay one decision and optional exact chain/stop intent in one tx.

    Different request keys for the same generation bind to the same decision.
    Reusing a key for a different validity key fails and rolls back all writes.
    A replay returns the original actor/time even after invalidation; callers must
    separately check current validity before authorizing any continuation.
    """
    if not actor_user_id or not request_key or len(request_key) > 128:
        raise ValueError("completion_request_invalid")
    expected = (sequence_id, slot_id, slot_generation, result_doc_id, document_revision)
    store = get_store()
    with store.transaction():
        # Take the slot's write lock before reads (SQLite deferred tx upgrade and
        # PostgreSQL concurrent clicks). This no-op is a lock, not a CAS verdict.
        store._execute(_sql(store, "serialize_slot"), [sequence_id, slot_id])
        decision = store._fetch_one(_sql(store, "get_request"), [request_key])
        if decision is not None:
            if _key(decision) != expected:
                raise CompletionConflict("completion_request_key_conflict")
            # Replay cannot add a new chain intent to an old confirmed request.
            return decision
        decision_id = "wcd_" + uuid4().hex
        affected = store._execute_affected(_sql(store, "claim_slot"), [
            decision_id, sequence_id, slot_id, slot_generation, result_doc_id, document_revision,
        ])
        timestamp = now_iso()
        if affected == 1:
            store._execute(_sql(store, "insert_decision"), [
                decision_id, *expected, actor_user_id, timestamp,
            ])
            decision = store._fetch_one(_sql(store, "get_decision"), [decision_id])
        else:
            decision = store._fetch_one(_sql(store, "get_slot_decision"), [slot_id])
            if decision is None or _key(decision) != expected or decision["invalidated_at"] is not None:
                raise CompletionConflict("completion_slot_changed")
            # CAS loser uses the committed canonical decision (no second decision).
            decision_id = decision["decision_id"]
        store._execute(_sql(store, "insert_request"), [request_key, decision_id, actor_user_id, timestamp])
        if dispatch is not None:
            _record_dispatch(store, decision_id, dispatch, timestamp)
        return decision


def _record_dispatch(store, decision_id, intent, timestamp):
    chain_id, stop_run_id = intent["chain_id"], intent["stop_run_id"]
    if not chain_id or not stop_run_id:
        raise ValueError("completion_dispatch_identity_missing")
    existing = store._fetch_one(_sql(store, "get_dispatch"), [decision_id, chain_id, stop_run_id])
    if existing is not None:
        return existing
    store._execute(_sql(store, "insert_dispatch"), [
        "wcp_" + uuid4().hex, decision_id, chain_id, stop_run_id,
        "completion:" + decision_id,
        json.dumps(intent.get("stop_snapshot") or {}, ensure_ascii=False, separators=(",", ":")),
        timestamp, timestamp,
    ])
    return store._fetch_one(_sql(store, "get_dispatch"), [decision_id, chain_id, stop_run_id])


def pending_dispatches() -> list[dict]:
    """Only current valid decisions are candidates; no worker is started here."""
    store = get_store()
    return store._fetch_all(_sql(store, "list_pending_dispatches"), [])


def _notify_sequence(sequence_id, decision_id=None):
    """After-commit refresh for invalidation and asynchronous dispatch recovery."""
    from .connection import after_commit
    def publish():
        try:
            root = get_store()._fetch_one(
                "SELECT d.project_id, d.group_id FROM workflow_sequences ws "
                "JOIN documents d ON d.doc_id = ws.doc_id WHERE ws.id = ?", [sequence_id])
            if not root:
                return
            from modules.flow_gate.api.v1.events.event_types import EventType
            from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
            broadcast_event_threadsafe(FlowEvent(
                event_type=EventType.WORKFLOW_COMPLETION_CHANGED, audience="*",
                project=root["project_id"], group_id=root["group_id"],
                payload={"sequence_id": sequence_id, "decision_id": decision_id}))
        except Exception:
            pass  # Refresh/focus re-reads the durable state.
    if not after_commit(publish):
        publish()


def cas_dispatch(
    dispatch_id: str, *, expected_state: str, expected_version: int, state: str,
    run_id: str | None = None, token_id: str | None = None, error_code: str | None = None,
) -> bool:
    """Versioned CAS avoids ABA when a dispatch returns to pending after a retry.

    This records state only. Future workers must recheck decision validity and
    exact paused-row ownership before consuming the stop or starting a run.
    """
    transitions = {
        "pending": {"running", "cancelled", "recovery_required", "retryable_failed"},
        "running": {"running", "completed", "retryable_failed", "recovery_required"},
        "retryable_failed": {"pending", "running", "cancelled", "recovery_required"},
        "recovery_required": {"pending", "completed", "cancelled"},
    }
    if state not in transitions.get(expected_state, set()):
        raise ValueError("completion_dispatch_transition_invalid")
    store = get_store()
    changed = store._execute_affected(_sql(store, "cas_dispatch"), [
        state, run_id, token_id, error_code, now_iso(), dispatch_id, expected_state, expected_version,
        run_id, run_id, token_id, token_id, state,
    ]) == 1
    if changed:
        dispatch = dispatch_by_id(dispatch_id)
        decision = get_decision(dispatch["decision_id"]) if dispatch else None
        if decision:
            _notify_sequence(decision["sequence_id"], decision["decision_id"])
    return changed


def invalidate_slot(
    sequence_id: int, slot_id: int, *, expected_generation: int, reason: str,
) -> bool:
    """Primitive for T#3 event hooks: advance generation, keep audit, cancel waiting intent."""
    if not reason:
        raise ValueError("completion_invalidation_reason_missing")
    store = get_store()
    with store.transaction():
        if store._execute_affected(_sql(store, "bump_slot"), [sequence_id, slot_id, expected_generation]) != 1:
            return False
        timestamp = now_iso()
        store._execute(_sql(store, "invalidate_decisions"), [timestamp, reason, sequence_id, slot_id])
        store._execute(_sql(store, "cancel_dispatches"), [timestamp, sequence_id, slot_id])
        _notify_sequence(sequence_id)
        return True


def dispatch_for_decision(decision_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM workflow_completion_dispatches WHERE decision_id = ? ORDER BY created_at, dispatch_id LIMIT 1",
        [decision_id])


def dispatch_by_id(dispatch_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM workflow_completion_dispatches WHERE dispatch_id = ?", [dispatch_id])


def attach_stop(dispatch, snapshot):
    """Bind a late stop once, retaining the confirmed chain/stop identity."""
    return get_store()._execute_affected(
        "UPDATE workflow_completion_dispatches SET stop_snapshot_json = ?, "
        "state_version = state_version + 1, updated_at = ? "
        "WHERE dispatch_id = ? AND state = 'pending' AND state_version = ?",
        [json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), now_iso(),
         dispatch["dispatch_id"], dispatch["state_version"]]) == 1


def unfinished_dispatches():
    return get_store()._fetch_all(
        "SELECT * FROM workflow_completion_dispatches WHERE state IN "
        "('pending','retryable_failed','running','recovery_required') ORDER BY created_at, dispatch_id", [])


def dispatch_for_stop(chain_id, stop_run_id, *, include_pending=False):
    states = "('pending','retryable_failed','running','completed','recovery_required')" if include_pending else "('running','completed','recovery_required')"
    return get_store()._fetch_one(
        "SELECT * FROM workflow_completion_dispatches WHERE chain_id = ? AND stop_run_id = ? "
        "AND state IN " + states + " ORDER BY created_at LIMIT 1",
        [chain_id, stop_run_id])



def history_for_group(project_id, group_id):
    return get_store()._fetch_all(
        "SELECT cd.*, d.type_code FROM workflow_completion_decisions cd "
        "JOIN workflow_sequences ws ON ws.id = cd.sequence_id "
        "JOIN documents root ON root.doc_id = ws.doc_id "
        "LEFT JOIN documents d ON d.doc_id = cd.result_doc_id "
        "WHERE root.project_id = ? AND root.group_id = ? ORDER BY cd.decided_at, cd.decision_id",
        [project_id, group_id])


def decision_for_request(request_key):
    store = get_store()
    return store._fetch_one(_sql(store, "get_request"), [request_key])
