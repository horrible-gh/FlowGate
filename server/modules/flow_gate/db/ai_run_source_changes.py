"""Per-run source change summaries for chat AI runs (flowgate.default.0670 T0004).

A row exists only for a run that actually changed at least one file (NR0003 §11.4):
"no change" is represented by the absence of a row, never by an empty card.
"""
from __future__ import annotations

import json
from typing import Optional

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
           files: list[dict], insertions: Optional[int], deletions: Optional[int]) -> Optional[dict]:
    get_store()._execute(
        "INSERT INTO ai_run_source_changes (run_id, doc_id, project_id, group_id, start_tree, "
        "end_tree, run_started_at, run_finished_at, files_changed, insertions, deletions, "
        "files_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (run_id) DO UPDATE SET end_tree = excluded.end_tree, "
        "run_finished_at = excluded.run_finished_at, files_changed = excluded.files_changed, "
        "insertions = excluded.insertions, deletions = excluded.deletions, "
        "files_json = excluded.files_json",
        [run_id, doc_id, project_id, group_id, start_tree, end_tree, run_started_at,
         run_finished_at, len(files), insertions, deletions,
         json.dumps(files, ensure_ascii=False), now_iso()],
    )
    return get(run_id)


def get(run_id: str) -> Optional[dict]:
    return _normalize(get_store()._fetch_one(
        "SELECT * FROM ai_run_source_changes WHERE run_id = ?", [run_id]
    ))


def list_for_doc(doc_id: str, limit: int = 200) -> list[dict]:
    rows = get_store()._fetch_all(
        "SELECT * FROM ai_run_source_changes WHERE doc_id = ? "
        "ORDER BY created_at DESC, run_id DESC LIMIT ?",
        [doc_id, int(limit)],
    )
    return [_normalize(row) for row in reversed(rows)]
