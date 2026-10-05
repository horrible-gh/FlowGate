"""Per-run source change summaries for chat AI runs (flowgate.default.0670 T0004).

A row exists only for a run that actually changed at least one file (NR0003 §11.4):
"no change" is represented by the absence of a row, never by an empty card.

flowgate.default.0675 T0004: the row carries its conversation anchor
(``chat_activity_anchor``) -- after the run's last AI reply, or after the turn the run
started from when there is none.
"""
from __future__ import annotations

import json
from typing import Optional

from . import chat_activity_anchor as anchor
from .connection import get_store, now_iso


def _normalize(row: Optional[dict]) -> Optional[dict]:
    if row is None:
        return None
    item = dict(row)
    try:
        item["files"] = json.loads(item.pop("files_json", None) or "[]")
    except (TypeError, ValueError):
        item["files"] = []
    return item


def upsert(*, run_id: str, doc_id: str, project_id: str, group_id: str, start_tree: str,
           end_tree: str, run_started_at: Optional[str], run_finished_at: Optional[str],
           files: list[dict], insertions: Optional[int], deletions: Optional[int],
           run_start_seq: Optional[int] = None) -> Optional[dict]:
    # A retried finalize keeps the first run_start_seq and anchor: the conflict branch
    # only refreshes the measured content; the anchor is re-resolved from the turns.
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
    resolve_anchor_for_run(doc_id, run_id)
    return get(run_id)


def get(run_id: str) -> Optional[dict]:
    return _normalize(get_store()._fetch_one(
        "SELECT * FROM ai_run_source_changes WHERE run_id = ?", [run_id]
    ))


def list_for_doc(doc_id: str, limit: int = 200, *, from_seq: Optional[int] = None,
                 to_seq: Optional[int] = None, include_unplaced: bool = True) -> list[dict]:
    """Same window contract as ``chat_command_requests.list_for_doc``."""
    sql, params = anchor.range_filter("doc_id = ?", [doc_id], from_seq, to_seq, include_unplaced)
    rows = get_store()._fetch_all(
        f"SELECT * FROM ai_run_source_changes WHERE {sql} "
        "ORDER BY created_at DESC, run_id DESC LIMIT ?",
        [*params, int(limit)],
    )
    return [_normalize(row) for row in reversed(rows)]


def resolve_anchor_for_run(doc_id: str, run_id: str) -> int:
    """(Re)decide this run's change-summary anchor; 1 when it moved, else 0."""
    store = get_store()
    row = store._fetch_one(
        "SELECT run_id, run_start_seq, anchor_seq, anchor_position, anchor_state "
        "FROM ai_run_source_changes WHERE run_id = ? AND doc_id = ?",
        [run_id, doc_id],
    )
    if row is None:
        return 0
    new = anchor.compute(anchor.KIND_CHANGE, anchor.reply_seqs(store, doc_id, run_id),
                         row.get("run_start_seq"))
    if not anchor.should_replace(anchor.KIND_CHANGE, row, new):
        return 0
    guard, guard_params = anchor.cas_guard(row)
    return store._execute_affected(
        "UPDATE ai_run_source_changes SET anchor_seq = ?, anchor_position = ?, anchor_state = ? "
        f"WHERE run_id = ? AND {guard}",
        [*new, run_id, *guard_params],
    ) or 0


def list_runs_to_resolve(doc_id: Optional[str] = None) -> list[dict]:
    """(doc_id, ai_run_id) pairs whose summary anchor is undecided or behind the
    recorded replies (``anchor.needs_resolve``); one doc or all of them."""
    sql = ("SELECT r.doc_id, r.run_id AS ai_run_id FROM ai_run_source_changes r WHERE "
           + anchor.needs_resolve(anchor.KIND_CHANGE, "r", "run_id"))
    if doc_id is None:
        return get_store()._fetch_all(sql)
    return get_store()._fetch_all(sql + " AND r.doc_id = ?", [doc_id])


def anchor_state_counts() -> dict:
    rows = get_store()._fetch_all(
        "SELECT anchor_state, COUNT(*) AS total FROM ai_run_source_changes GROUP BY anchor_state"
    )
    return anchor.summarize_counts(rows)
