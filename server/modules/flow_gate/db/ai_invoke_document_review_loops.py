"""Persistent state for document-scoped AI review loops (0417 DB0010)."""
from __future__ import annotations

from typing import Optional
from .connection import get_store, now_iso

_MUTABLE = frozenset({"round_no", "current_stage", "stop_reason", "stop_detail", "last_hop_kind", "last_hop_outcome", "attempts_used", "updated_at"})

def insert(row: dict) -> dict:
    required = ("run_id","group_id","doc_ref","review_count","reviewer_provider_id","review_criteria","rework_provider_id","rework_timeout_sec","failure_restart_max_attempts","total_timeout_sec","review_baseline_id","baseline_revision_no","starts_with_rework","started_at","deadline_at","current_stage")
    missing = [key for key in required if row.get(key) is None]
    if missing:
        raise ValueError("missing loop fields: " + ", ".join(missing))
    stamp = row.get("created_at") or now_iso()
    values = [row[k] for k in required] + [row.get("rework_message", ""), int(row.get("round_no", 1)), int(row.get("attempts_used", 0)), stamp, row.get("updated_at") or stamp]
    columns = list(required) + ["rework_message","round_no","attempts_used","created_at","updated_at"]
    get_store()._execute("INSERT INTO ai_invoke_document_review_loops (" + ",".join(columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", values)
    return get(row["run_id"])

def get(run_id: str) -> Optional[dict]:
    return get_store()._fetch_one("SELECT * FROM ai_invoke_document_review_loops WHERE run_id = ?", [run_id])

def checkpoint(run_id: str, *, expected_round_no: int, expected_stage: str, expected_updated_at: str, **updates) -> tuple[bool, Optional[dict]]:
    illegal = set(updates) - _MUTABLE
    if illegal:
        raise ValueError("immutable loop fields: " + ", ".join(sorted(illegal)))
    updates["updated_at"] = updates.get("updated_at") or now_iso()
    store = get_store()
    with store.transaction():
        sets = ", ".join(f"{key} = ?" for key in updates)
        affected = store._execute_affected("UPDATE ai_invoke_document_review_loops SET " + sets + " WHERE run_id = ? AND round_no = ? AND current_stage = ? AND updated_at = ?", [*updates.values(), run_id, expected_round_no, expected_stage, expected_updated_at])
        latest = store._fetch_one("SELECT * FROM ai_invoke_document_review_loops WHERE run_id = ?", [run_id])
        return affected == 1, latest

def dismiss_card(run_id: str, *, at: Optional[str] = None) -> bool:
    """Mark this loop's MONITOR CARD as removed by its owner (0529 B0001).

    Not a delete: the loop row and every round it recorded stay readable through the
    run detail and the run list. Only `ai_invoke_runs.list_review_loops_by_user` -- the
    one query /ai-invoke/active-all rebuilds bootstrap cards from -- skips it afterwards.

    The `card_dismissed_at IS NULL` guard makes this a compare-and-swap rather than a
    blind UPDATE, so the caller can tell a first removal (True) from a replay of one
    that already happened (False) instead of reporting success twice for one card.
    """
    return get_store()._execute_affected(
        "UPDATE ai_invoke_document_review_loops SET card_dismissed_at = ? "
        "WHERE run_id = ? AND card_dismissed_at IS NULL",
        [at or now_iso(), run_id],
    ) == 1

def list_by_group(group_id: str, limit: int = 50) -> list[dict]:
    return get_store()._fetch_all("SELECT * FROM ai_invoke_document_review_loops WHERE group_id = ? ORDER BY updated_at DESC LIMIT ?", [group_id, limit])

def list_by_document(doc_ref: str, limit: int = 50) -> list[dict]:
    return get_store()._fetch_all("SELECT * FROM ai_invoke_document_review_loops WHERE doc_ref = ? ORDER BY updated_at DESC LIMIT ?", [doc_ref, limit])
