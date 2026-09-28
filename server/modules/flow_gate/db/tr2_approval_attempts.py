"""Durable, per-round TR2 approval journal. Source writes never happen in this module."""
from __future__ import annotations

import json
import uuid
from typing import Any

from .connection import get_store, now_iso

_JSON = frozenset({"precheck_json", "apply_json", "validation_json", "commit_json", "ledger_json"})
_FIELDS = frozenset({
    "state", "phase", "result_code", "error_code", "error_detail", "live_fingerprint",
    "precheck_json", "apply_json", "validation_json", "commit_json", "ledger_json",
    "backup_bundle_id", "pre_apply_head_sha", "commit_sha", "ledger_row_id",
    "finished_at", "heartbeat_at",
})


def by_request_key(request_key: str | None) -> dict | None:
    if not request_key:
        return None
    return get_store()._fetch_one(
        "SELECT * FROM tr2_approval_attempts WHERE request_key = ?", [request_key])


def by_id(attempt_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM tr2_approval_attempts WHERE attempt_id = ?", [attempt_id])


def latest_by_doc(doc_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM tr2_approval_attempts WHERE tr2_doc_id = ? ORDER BY id DESC LIMIT 1",
        [doc_id])


def latest_success(doc_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM tr2_approval_attempts WHERE tr2_doc_id = ? "
        "AND state = 'succeeded' ORDER BY id DESC LIMIT 1", [doc_id])


def list_by_doc(doc_id: str, limit: int = 50) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM tr2_approval_attempts WHERE tr2_doc_id = ? ORDER BY id DESC LIMIT ?",
        [doc_id, limit])


def in_progress(doc_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM tr2_approval_attempts WHERE tr2_doc_id = ? "
        "AND state = 'in_progress' ORDER BY id DESC LIMIT 1", [doc_id])


def recovery_required(doc_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM tr2_approval_attempts WHERE tr2_doc_id = ? "
        "AND state = 'recovery_required' ORDER BY id DESC LIMIT 1", [doc_id])


def successful_root(ledger_row_id: int) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM tr2_approval_attempts WHERE ledger_row_id = ? "
        "AND state = 'succeeded' ORDER BY id DESC LIMIT 1", [ledger_row_id])


def _notify(row: dict | None) -> None:
    """Best-effort SSE for a state/phase change, delivered after the enclosing commit."""
    if not row:
        return

    def publish():
        try:
            from modules.flow_gate.api.v1.events.event_types import EventType
            from modules.flow_gate.api.v1.events.publisher import (
                FlowEvent, broadcast_event_threadsafe,
            )
            broadcast_event_threadsafe(FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": row["group_id"], "reason": "tr2_approval_changed",
                         "doc_id": row["tr2_doc_id"], "attempt_id": row["attempt_id"],
                         "state": row["state"], "phase": row["phase"]},
                audience="*", project=row["project_id"], group_id=row["group_id"],
                doc_id=row["tr2_doc_id"]))
        except Exception:
            pass  # The journal row is durable; the screen also re-reads on focus.

    from .connection import after_commit
    if not after_commit(publish):
        publish()


def create(*, doc: dict, actor_user_id: str, spec_fingerprint: str,
           baseline_fingerprint: str, request_key: str | None) -> dict:
    """Called under the project source lock after all admission checks."""
    store = get_store()
    now = now_iso()
    previous = latest_by_doc(doc["doc_id"])
    round_no = int(previous["approval_round"]) + 1 if previous else 1
    attempt_id = uuid.uuid4().hex
    store._execute(
        "INSERT INTO tr2_approval_attempts "
        "(attempt_id,request_key,tr2_doc_id,project_id,group_id,document_revision,"
        "document_etag,approval_round,actor_user_id,spec_fingerprint,baseline_fingerprint,"
        "state,phase,started_at,heartbeat_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,'in_progress','created',?,?,?)",
        [attempt_id, request_key, doc["doc_id"], doc["project_id"], doc["group_id"],
         int(doc.get("revision_no") or 0), doc.get("etag"), round_no, actor_user_id,
         spec_fingerprint, baseline_fingerprint, now, now, now],
    )
    row = by_id(attempt_id)
    _notify(row)
    return row


def update(attempt_id: str, **fields: Any) -> dict:
    unknown = set(fields) - _FIELDS
    if unknown:
        raise ValueError(f"Unknown TR2 attempt fields: {sorted(unknown)}")
    fields = dict(fields)
    for name in _JSON & fields.keys():
        if not isinstance(fields[name], str) and fields[name] is not None:
            fields[name] = json.dumps(fields[name], ensure_ascii=False, sort_keys=True)
    now = now_iso()
    fields.setdefault("heartbeat_at", now)
    fields["updated_at"] = now
    set_sql = ", ".join(f"{key} = ?" for key in fields)
    get_store()._execute(
        f"UPDATE tr2_approval_attempts SET {set_sql} WHERE attempt_id = ?",
        [*fields.values(), attempt_id],
    )
    row = by_id(attempt_id)
    if row is None:
        raise LookupError(attempt_id)
    if "state" in fields or "phase" in fields:
        _notify(row)
    return row


def finish(attempt_id: str, *, state: str, result_code: str,
           error_code: str | None = None, error_detail: str | None = None,
           **fields: Any) -> dict:
    if state not in {"succeeded", "failed", "recovery_required"}:
        raise ValueError(state)
    return update(attempt_id, state=state,
                  phase="rollback" if state == "recovery_required" else "complete",
                  result_code=result_code, error_code=error_code,
                  error_detail=error_detail, finished_at=now_iso(), **fields)
