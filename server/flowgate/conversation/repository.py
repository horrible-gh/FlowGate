"""Storage of the Conversation contract: command requests and run changes (D0006 §2.2, §4.3).

Two tables, both created by migration 137 and anchored by 138:

* ``chat_command_requests`` -- one durable row per command a chat AI run asked for. The
  row is the single source of truth of the approval state machine: the browser renders
  it and posts a decision, the waiting run reads the same row back. Every status change
  is a compare-and-set (``WHERE status IN (...)``) so a late click, a policy decision,
  a run finalization and a timeout can never both win.
* ``ai_run_source_changes`` -- the start-tree -> end-tree change summary of one chat
  run, at most one row per run and only when something changed.

This module is SQL only; the anchor rule, the state machine and the snapshots live in
``activity``, ``commands`` and ``run_changes``.
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from modules.flow_gate.db.connection import get_store, now_iso

from flowgate.conversation import contracts as c

# How much of each stream a command row keeps (the screen shows it; the model gets a
# shorter tail, see commands.ai_result).
MAX_TAIL_BYTES = 64 * 1024
DOC_LIST_LIMIT = 200


def truncate_tail(text: Optional[str]) -> tuple[Optional[str], bool]:
    """(tail kept within MAX_TAIL_BYTES, whether anything was cut)."""
    if text is None:
        return None, False
    raw = text.encode("utf-8")
    if len(raw) <= MAX_TAIL_BYTES:
        return text, False
    return raw[-MAX_TAIL_BYTES:].decode("utf-8", errors="ignore"), True


def _normalize_command(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    item = dict(row)
    try:
        item["args"] = json.loads(item.pop("args_json", None) or "[]")
    except (TypeError, ValueError):
        item["args"] = []
    for flag in ("timed_out", "stdout_truncated", "stderr_truncated"):
        item[flag] = bool(item.get(flag))
    return item


def _normalize_change(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    item = dict(row)
    try:
        item["files"] = json.loads(item.pop("files_json", None) or "[]")
    except (TypeError, ValueError):
        item["files"] = []
    return item


def _range_filter(base: str, params: list, from_seq: Optional[int], to_seq: Optional[int],
                  include_unplaced: bool) -> tuple[str, list]:
    """Restrict a doc query to rows anchored in [from_seq, to_seq] (either end open)."""
    if from_seq is None and to_seq is None:
        return base, list(params)
    conds: list[str] = []
    extra: list = []
    if from_seq is not None:
        conds.append("anchor_seq >= ?")
        extra.append(int(from_seq))
    if to_seq is not None:
        conds.append("anchor_seq <= ?")
        extra.append(int(to_seq))
    window = " AND ".join(conds)
    if include_unplaced:
        window = f"(({window}) OR anchor_seq IS NULL)"
    return f"{base} AND {window}", [*params, *extra]


def _needs_resolve(alias: str, run_column: str, reply_moves_back: bool) -> str:
    """WHERE fragment: rows whose stored anchor is undecided or behind the recorded replies.

    * no anchor state yet (the insert-time resolve failed);
    * ``run_start`` / ``unresolved`` while an AI turn of the run is recorded;
    * ``reply`` while a recorded reply lies where the anchor still has to move
      (command: an earlier first reply, change: a later last reply).
    """
    turns = (f"SELECT 1 FROM conversation_turns t WHERE t.doc_id = {alias}.doc_id "
             f"AND t.source_run_id = {alias}.{run_column} AND t.speaker = 'ai'")
    moved = f"t.seq < {alias}.anchor_seq" if reply_moves_back else f"t.seq > {alias}.anchor_seq"
    return (f"({alias}.anchor_state IS NULL"
            f" OR ({alias}.anchor_state IN ('{c.ANCHOR_RUN_START}', '{c.ANCHOR_UNRESOLVED}') AND EXISTS ({turns}))"
            f" OR ({alias}.anchor_state = '{c.ANCHOR_REPLY}' AND EXISTS ({turns} AND {moved})))")


def _anchor_cas_guard(current: dict) -> tuple[str, list]:
    """WHERE fragment matching exactly the anchor a resolver read."""
    parts: list[str] = []
    params: list = []
    for column in ("anchor_seq", "anchor_state"):
        value = current.get(column)
        if value is None:
            parts.append(f"{column} IS NULL")
        else:
            parts.append(f"{column} = ?")
            params.append(value)
    return " AND ".join(parts), params


# ── conversation turns (read only) ───────────────────────────────────────────

def reply_seqs(doc_id: str, run_id: str) -> list[int]:
    """Seqs of the AI turns ``run_id`` itself appended to ``doc_id``.

    ``source_run_id`` is written from the run's own token, so this is a recorded
    relation, never a guess from timestamps.
    """
    rows = get_store()._fetch_all(
        "SELECT seq FROM conversation_turns WHERE doc_id = ? AND source_run_id = ? "
        "AND speaker = 'ai' ORDER BY seq ASC",
        [doc_id, run_id],
    )
    return [int(row["seq"]) for row in rows]


# ── chat_command_requests ────────────────────────────────────────────────────

def insert_command(*, request_id: str, ai_run_id: str, doc_id: str, project_id: str, group_id: str,
                   token_id: Optional[str], issued_to: Optional[str], provider_name: Optional[str],
                   program: str, args: list[str], cwd_relative: str, timeout_seconds: int,
                   category: str, policy: str, status: str, decision_source: Optional[str] = None,
                   error_code: Optional[str] = None, run_start_seq: Optional[int] = None) -> dict:
    now = now_iso()
    terminal = status in c.TERMINAL_STATUSES
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
    return get_command(request_id)


def get_command(request_id: str) -> Optional[dict]:
    return _normalize_command(get_store()._fetch_one(
        "SELECT * FROM chat_command_requests WHERE request_id = ?", [request_id]
    ))


def list_commands_for_doc(doc_id: str, limit: int = DOC_LIST_LIMIT, *, from_seq: Optional[int] = None,
                          to_seq: Optional[int] = None, include_unplaced: bool = True) -> list[dict]:
    """Newest ``limit`` rows of one CH, returned oldest first."""
    sql, params = _range_filter("doc_id = ?", [doc_id], from_seq, to_seq, include_unplaced)
    rows = get_store()._fetch_all(
        f"SELECT * FROM chat_command_requests WHERE {sql} "
        "ORDER BY created_at DESC, request_id DESC LIMIT ?",
        [*params, int(limit)],
    )
    return [_normalize_command(row) for row in reversed(rows)]


def list_open_commands_for_run(ai_run_id: str) -> list[dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM chat_command_requests WHERE ai_run_id = ? AND status IN (?, ?, ?) "
        "ORDER BY created_at ASC, request_id ASC",
        [ai_run_id, *c.OPEN_STATUSES],
    )
    return [_normalize_command(row) for row in rows]


def list_open_commands() -> list[dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM chat_command_requests WHERE status IN (?, ?, ?)", list(c.OPEN_STATUSES)
    )
    return [_normalize_command(row) for row in rows]


def transition_command(request_id: str, from_statuses: Iterable[str], to_status: str,
                       **fields) -> Optional[dict]:
    """CAS one row from any of ``from_statuses`` to ``to_status``; None when it lost."""
    allowed = list(from_statuses)
    if not allowed:
        return None
    now = now_iso()
    sets: dict = {"status": to_status, "updated_at": now}
    if to_status in c.TERMINAL_STATUSES:
        sets["finished_at"] = now
    for key, value in fields.items():
        if key in ("stdout_tail", "stderr_tail"):
            value, cut = truncate_tail(value)
            if cut:
                sets[key.replace("_tail", "_truncated")] = 1
        elif key in ("timed_out", "stdout_truncated", "stderr_truncated"):
            value = 1 if value else 0
            if sets.get(key):
                continue  # already marked by a tail cut above
        sets[key] = value
    columns = ", ".join(f"{key} = ?" for key in sets)
    placeholders = ", ".join("?" for _ in allowed)
    affected = get_store()._execute_affected(
        f"UPDATE chat_command_requests SET {columns} "
        f"WHERE request_id = ? AND status IN ({placeholders})",
        [*sets.values(), request_id, *allowed],
    )
    return get_command(request_id) if affected else None


def command_anchor_rows(doc_id: str, ai_run_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT request_id, run_start_seq, anchor_seq, anchor_position, anchor_state "
        "FROM chat_command_requests WHERE doc_id = ? AND ai_run_id = ?",
        [doc_id, ai_run_id],
    )


def set_command_anchor(request_id: str, new: tuple, current: dict) -> int:
    guard, guard_params = _anchor_cas_guard(current)
    return get_store()._execute_affected(
        "UPDATE chat_command_requests SET anchor_seq = ?, anchor_position = ?, anchor_state = ? "
        f"WHERE request_id = ? AND {guard}",
        [*new, request_id, *guard_params],
    ) or 0


def command_runs_to_resolve(doc_id: Optional[str] = None) -> list[dict]:
    sql = ("SELECT DISTINCT r.doc_id, r.ai_run_id FROM chat_command_requests r WHERE "
           + _needs_resolve("r", "ai_run_id", reply_moves_back=True))
    if doc_id is None:
        return get_store()._fetch_all(sql)
    return get_store()._fetch_all(sql + " AND r.doc_id = ?", [doc_id])


# ── ai_run_source_changes ────────────────────────────────────────────────────

def upsert_run_change(*, run_id: str, doc_id: str, project_id: str, group_id: str, start_tree: str,
                      end_tree: str, run_started_at: Optional[str], run_finished_at: Optional[str],
                      files: list[dict], insertions: Optional[int], deletions: Optional[int],
                      run_start_seq: Optional[int] = None) -> Optional[dict]:
    """One row per run. A retried finalize refreshes the measured content and keeps the
    first ``run_start_seq`` and anchor (the anchor is re-resolved from the turns)."""
    get_store()._execute(
        "INSERT INTO ai_run_source_changes (run_id, doc_id, project_id, group_id, start_tree, "
        "end_tree, run_started_at, run_finished_at, files_changed, insertions, deletions, "
        "files_json, run_start_seq, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (run_id) DO UPDATE SET end_tree = excluded.end_tree, "
        "run_finished_at = excluded.run_finished_at, files_changed = excluded.files_changed, "
        "insertions = excluded.insertions, deletions = excluded.deletions, "
        "files_json = excluded.files_json",
        [run_id, doc_id, project_id, group_id, start_tree, end_tree, run_started_at,
         run_finished_at, len(files), insertions, deletions,
         json.dumps(files, ensure_ascii=False),
         None if run_start_seq is None else int(run_start_seq), now_iso()],
    )
    return get_run_change(run_id)


def get_run_change(run_id: str) -> Optional[dict]:
    return _normalize_change(get_store()._fetch_one(
        "SELECT * FROM ai_run_source_changes WHERE run_id = ?", [run_id]
    ))


def list_run_changes_for_doc(doc_id: str, limit: int = DOC_LIST_LIMIT, *, from_seq: Optional[int] = None,
                             to_seq: Optional[int] = None, include_unplaced: bool = True) -> list[dict]:
    sql, params = _range_filter("doc_id = ?", [doc_id], from_seq, to_seq, include_unplaced)
    rows = get_store()._fetch_all(
        f"SELECT * FROM ai_run_source_changes WHERE {sql} "
        "ORDER BY created_at DESC, run_id DESC LIMIT ?",
        [*params, int(limit)],
    )
    return [_normalize_change(row) for row in reversed(rows)]


def run_change_anchor_row(doc_id: str, run_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT run_id, run_start_seq, anchor_seq, anchor_position, anchor_state "
        "FROM ai_run_source_changes WHERE run_id = ? AND doc_id = ?",
        [run_id, doc_id],
    )


def set_run_change_anchor(run_id: str, new: tuple, current: dict) -> int:
    guard, guard_params = _anchor_cas_guard(current)
    return get_store()._execute_affected(
        "UPDATE ai_run_source_changes SET anchor_seq = ?, anchor_position = ?, anchor_state = ? "
        f"WHERE run_id = ? AND {guard}",
        [*new, run_id, *guard_params],
    ) or 0


def run_change_runs_to_resolve(doc_id: Optional[str] = None) -> list[dict]:
    sql = ("SELECT r.doc_id, r.run_id AS ai_run_id FROM ai_run_source_changes r WHERE "
           + _needs_resolve("r", "run_id", reply_moves_back=False))
    if doc_id is None:
        return get_store()._fetch_all(sql)
    return get_store()._fetch_all(sql + " AND r.doc_id = ?", [doc_id])
