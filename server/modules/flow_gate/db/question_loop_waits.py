"""Parked document review loops waiting for their own questions (flowgate.v02test.0051 T0005 A1, migration 200).

One row per AI run whose review loop reached a hop boundary while questions that run asked were still unanswered.
Every transition is a compare-and-swap on ``status`` so a repeated answer event, a second process or a restart can
never resume the same wait twice:

    waiting  --claim_resume-->  resuming (resume_exec_id)  --mark_resumed-->  resumed (resumed_run_id)
    waiting | resuming  --cancel-->  cancelled (end_reason)
    resuming (its request cancelled / lost before any run took it)  --reclaim_resume-->  resuming (new exec)
"""
from __future__ import annotations

import json
import secrets
from typing import Optional

from .connection import get_store, now_iso

TABLE = "question_loop_waits"
WAITING = "waiting"
RESUMING = "resuming"
RESUMED = "resumed"
CANCELLED = "cancelled"
OPEN = (WAITING, RESUMING)

_COLUMNS = (
    "wait_id", "group_id", "doc_ref", "loop_run_id", "graph_exec_id", "graph_attempt_id", "graph_run_id",
    "node_key", "assignment_stage", "assignment_seq", "asked_hop_kind", "asker_inv_id", "asked_round_no",
    "hop_outcome", "resume_stage", "resume_round_no", "resume_attempts_used", "question_item_ids", "status",
    "waited_at", "created_at", "updated_at",
)


def available() -> bool:
    return bool(get_store().table_exists(TABLE))


def _decode(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    row = dict(row)
    try:
        row["question_item_ids"] = [int(i) for i in json.loads(row.get("question_item_ids") or "[]")]
    except (TypeError, ValueError):
        row["question_item_ids"] = []
    return row


def insert(row: dict) -> dict:
    """Store a new ``waiting`` row; ``question_item_ids`` is a list of ints."""
    stamp = row.get("waited_at") or now_iso()
    values = dict(row)
    values.update(
        wait_id=row.get("wait_id") or "qlw_" + secrets.token_hex(12),
        question_item_ids=json.dumps([int(i) for i in row.get("question_item_ids") or []]),
        status=WAITING, waited_at=stamp, created_at=stamp, updated_at=stamp,
        resume_attempts_used=int(row.get("resume_attempts_used") or 0),
    )
    for key in ("asked_hop_kind", "asked_round_no", "resume_stage", "resume_round_no", "group_id", "doc_ref",
                "loop_run_id"):
        if values.get(key) is None:
            raise ValueError("missing question loop wait field: " + key)
    get_store()._execute(
        "INSERT INTO question_loop_waits (" + ",".join(_COLUMNS) + ") VALUES (" + ",".join("?" for _ in _COLUMNS)
        + ")", [values.get(c) for c in _COLUMNS])
    return get(values["wait_id"])


def get(wait_id: str) -> Optional[dict]:
    return _decode(get_store()._fetch_one("SELECT * FROM question_loop_waits WHERE wait_id = ?", [wait_id]))


def get_by_loop_run(loop_run_id: Optional[str]) -> Optional[dict]:
    if not loop_run_id:
        return None
    return _decode(get_store()._fetch_one("SELECT * FROM question_loop_waits WHERE loop_run_id = ?",
                                          [loop_run_id]))


def get_by_resume_exec(exec_id: Optional[str]) -> Optional[dict]:
    if not exec_id:
        return None
    return _decode(get_store()._fetch_one("SELECT * FROM question_loop_waits WHERE resume_exec_id = ?", [exec_id]))


def list_open_by_group(group_id: str) -> list[dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM question_loop_waits WHERE group_id = ? AND status IN ('waiting', 'resuming') "
        "ORDER BY waited_at, wait_id", [group_id]) or []
    return [_decode(r) for r in rows]


def list_by_loop_runs(loop_run_ids) -> list[dict]:
    """Every wait (any status) parked by one of ``loop_run_ids`` -- the runs one review loop was carried by
    (flowgate.v02test.0051 T0007 A2: the loop's question wait time and its question-caused reviews)."""
    ids = sorted({str(i) for i in loop_run_ids or () if i})
    if not ids:
        return []
    rows = get_store()._fetch_all(
        "SELECT * FROM question_loop_waits WHERE loop_run_id IN (" + ",".join("?" for _ in ids) + ") "
        "ORDER BY waited_at, wait_id", ids) or []
    return [_decode(r) for r in rows]


def claim_resume(wait_id: str, exec_id: str, *, at: Optional[str] = None) -> bool:
    """waiting -> resuming, once: the caller requested ``exec_id`` in the same transaction."""
    stamp = at or now_iso()
    return get_store()._execute_affected(
        "UPDATE question_loop_waits SET status = 'resuming', resume_exec_id = ?, answered_at = COALESCE(answered_at, ?), "
        "resume_requested_at = ?, updated_at = ? WHERE wait_id = ? AND status = 'waiting'",
        [exec_id, stamp, stamp, stamp, wait_id]) == 1


def reclaim_resume(wait_id: str, expected_exec_id: str, exec_id: str, *, at: Optional[str] = None) -> bool:
    """resuming (its request never taken by a run) -> resuming again with a new request, once."""
    stamp = at or now_iso()
    return get_store()._execute_affected(
        "UPDATE question_loop_waits SET resume_exec_id = ?, resume_requested_at = ?, updated_at = ? "
        "WHERE wait_id = ? AND status = 'resuming' AND resume_exec_id = ? AND resumed_run_id IS NULL",
        [exec_id, stamp, stamp, wait_id, expected_exec_id]) == 1


def mark_resumed(wait_id: str, exec_id: str, run_id: str, *, at: Optional[str] = None) -> bool:
    """resuming -> resumed: the successor run of ``exec_id`` was admitted."""
    stamp = at or now_iso()
    return get_store()._execute_affected(
        "UPDATE question_loop_waits SET status = 'resumed', resumed_run_id = ?, resumed_at = ?, ended_at = ?, "
        "updated_at = ? WHERE wait_id = ? AND status = 'resuming' AND resume_exec_id = ?",
        [run_id, stamp, stamp, stamp, wait_id, exec_id]) == 1


def cancel(wait_id: str, reason: str, *, at: Optional[str] = None) -> bool:
    stamp = at or now_iso()
    return get_store()._execute_affected(
        "UPDATE question_loop_waits SET status = 'cancelled', end_reason = ?, ended_at = ?, updated_at = ? "
        "WHERE wait_id = ? AND status IN ('waiting', 'resuming')", [str(reason)[:191], stamp, stamp, wait_id]) == 1
