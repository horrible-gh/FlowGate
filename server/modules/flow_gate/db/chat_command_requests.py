"""Durable chat command requests (flowgate.default.0670 T0004, NR0003 §7.1).

One row per command an AI chat run asked to execute. The row is the single source of
truth for the approval state machine -- the browser only ever renders it and posts a
decision; the waiting AI run reads the same row back. Status transitions are guarded
compare-and-set updates (``WHERE status IN (...)``) so a late click, a policy
decision and a run finalization can never both win.

flowgate.default.0675 T0004: each row also carries its conversation anchor
(``chat_activity_anchor``), decided here on insert and re-decided when the run's AI
reply turn lands, so the screen places it without guessing from loaded turns.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from . import chat_activity_anchor as anchor
from .connection import get_store, now_iso

_MAX_TAIL_BYTES = 64 * 1024

STATUSES = (
    "pending_approval", "approved", "rejected", "running",
    "succeeded", "failed", "timed_out", "cancelled",
)
TERMINAL_STATUSES = ("rejected", "succeeded", "failed", "timed_out", "cancelled")
OPEN_STATUSES = ("pending_approval", "approved", "running")


def _truncate_tail(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    raw = text.encode("utf-8")
    if len(raw) <= _MAX_TAIL_BYTES:
        return text
    return raw[-_MAX_TAIL_BYTES:].decode("utf-8", errors="ignore")


def _normalize(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    item = dict(row)
    try:
        item["args"] = json.loads(item.pop("args_json", None) or "[]")
    except (TypeError, ValueError):
        item["args"] = []
    item["timed_out"] = bool(item.get("timed_out"))
    return item


def create(*, request_id: str, ai_run_id: str, doc_id: str, project_id: str, group_id: str,
           token_id: Optional[str], issued_to: Optional[str], provider_name: Optional[str],
           program: str, args: list[str], cwd_relative: str, timeout_seconds: int,
           category: str, policy: str, status: str, decision_source: Optional[str] = None,
           error_code: Optional[str] = None, run_start_seq: Optional[int] = None) -> dict:
    now = now_iso()
    terminal = status in TERMINAL_STATUSES
    get_store()._execute(
        "INSERT INTO chat_command_requests (request_id, ai_run_id, doc_id, project_id, group_id, "
        "token_id, issued_to, provider_name, program, args_json, cwd_relative, timeout_seconds, "
        "category, policy, status, decision_source, decided_at, finished_at, error_code, "
        "run_start_seq, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [request_id, ai_run_id, doc_id, project_id, group_id, token_id, issued_to, provider_name,
         program, json.dumps(list(args), ensure_ascii=False), cwd_relative, int(timeout_seconds),
         category, policy, status, decision_source,
         now if decision_source else None, now if terminal else None, error_code,
         None if run_start_seq is None else int(run_start_seq), now, now],
    )
    # Decided after the insert is visible: a reply turn committing concurrently is
    # either seen here or re-resolves this row itself (resolve_anchors_for_run).
    resolve_anchors_for_run(doc_id, ai_run_id)
    return get(request_id)


def get(request_id: str) -> Optional[dict]:
    return _normalize(get_store()._fetch_one(
        "SELECT * FROM chat_command_requests WHERE request_id = ?", [request_id]
    ))


def list_for_doc(doc_id: str, limit: int = 200, *, from_seq: Optional[int] = None,
                 to_seq: Optional[int] = None, include_unplaced: bool = True) -> list[dict]:
    """Newest ``limit`` rows of a doc, returned oldest first.

    ``from_seq`` / ``to_seq`` (inclusive) restrict to rows anchored in that turn range --
    the page of turns a screen holds; ``include_unplaced`` adds rows with no anchor seq.
    """
    sql, params = anchor.range_filter("doc_id = ?", [doc_id], from_seq, to_seq, include_unplaced)
    rows = get_store()._fetch_all(
        f"SELECT * FROM chat_command_requests WHERE {sql} "
        "ORDER BY created_at DESC, request_id DESC LIMIT ?",
        [*params, int(limit)],
    )
    return [_normalize(row) for row in reversed(rows)]


def resolve_anchors_for_run(doc_id: str, ai_run_id: str) -> int:
    """(Re)decide the anchor of every row of one run; returns how many rows moved.

    Idempotent: re-running it with the same turns changes nothing, and each UPDATE is a
    compare-and-set on the anchor it read, so a stale resolver can never overwrite a
    newer one (``anchor.should_replace`` also refuses downgrades).
    """
    store = get_store()
    rows = store._fetch_all(
        "SELECT request_id, run_start_seq, anchor_seq, anchor_position, anchor_state "
        "FROM chat_command_requests WHERE doc_id = ? AND ai_run_id = ?",
        [doc_id, ai_run_id],
    )
    if not rows:
        return 0
    replies = anchor.reply_seqs(store, doc_id, ai_run_id)
    moved = 0
    for row in rows:
        new = anchor.compute(anchor.KIND_COMMAND, replies, row.get("run_start_seq"))
        if not anchor.should_replace(anchor.KIND_COMMAND, row, new):
            continue
        guard, guard_params = anchor.cas_guard(row)
        moved += store._execute_affected(
            "UPDATE chat_command_requests SET anchor_seq = ?, anchor_position = ?, anchor_state = ? "
            f"WHERE request_id = ? AND {guard}",
            [*new, row["request_id"], *guard_params],
        ) or 0
    return moved


def list_runs_to_resolve(doc_id: Optional[str] = None) -> list[dict]:
    """(doc_id, ai_run_id) pairs with rows whose anchor is undecided or behind the
    recorded replies (``anchor.needs_resolve``); one doc or all of them."""
    sql = ("SELECT DISTINCT r.doc_id, r.ai_run_id FROM chat_command_requests r WHERE "
           + anchor.needs_resolve(anchor.KIND_COMMAND, "r", "ai_run_id"))
    if doc_id is None:
        return get_store()._fetch_all(sql)
    return get_store()._fetch_all(sql + " AND r.doc_id = ?", [doc_id])


def anchor_state_counts() -> dict:
    rows = get_store()._fetch_all(
        "SELECT anchor_state, COUNT(*) AS total FROM chat_command_requests GROUP BY anchor_state"
    )
    return anchor.summarize_counts(rows)


def list_open_for_run(ai_run_id: str) -> list[dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM chat_command_requests WHERE ai_run_id = ? AND status IN (?, ?, ?) "
        "ORDER BY created_at ASC",
        [ai_run_id, *OPEN_STATUSES],
    )
    return [_normalize(row) for row in rows]


def list_open() -> list[dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM chat_command_requests WHERE status IN (?, ?, ?)", list(OPEN_STATUSES)
    )
    return [_normalize(row) for row in rows]


def transition(request_id: str, from_statuses: Iterable[str], to_status: str, **fields) -> Optional[dict]:
    """CAS one row from any of ``from_statuses`` to ``to_status``; None when it lost."""
    allowed = list(from_statuses)
    if not allowed:
        return None
    now = now_iso()
    sets: dict = {"status": to_status, "updated_at": now}
    if to_status in TERMINAL_STATUSES:
        sets["finished_at"] = now
    for key, value in fields.items():
        if key in ("stdout_tail", "stderr_tail"):
            value = _truncate_tail(value)
        if key == "timed_out":
            value = 1 if value else 0
        sets[key] = value
    columns = ", ".join(f"{key} = ?" for key in sets)
    placeholders = ", ".join("?" for _ in allowed)
    affected = get_store()._execute_affected(
        f"UPDATE chat_command_requests SET {columns} "
        f"WHERE request_id = ? AND status IN ({placeholders})",
        [*sets.values(), request_id, *allowed],
    )
    return get(request_id) if affected else None
