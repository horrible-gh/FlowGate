"""ai_invoke_paused_chains CRUD (group 0252 DB0010).

Single source of truth for the miniplayer pause/resume state (L0009 paused_store).
One row = one user-paused continuous chain. UNIQUE(group_id) enforces "at most one
paused row per group" and is the upsert conflict key; pending_q_doc_ids is NEVER
stored here — it is derived live from the group's open Q documents (DB0010 §4).
"""
from __future__ import annotations

import json
from typing import Optional

from .connection import get_store, now_iso


def load_json_map(value) -> Optional[dict]:
    """Read one of the JSON-text selection columns back, defensively (0365 DB0004 §2-2).

    Missing, corrupt, or non-object text degrades to None: a single damaged row must never
    block a resume — it only loses that row's per-step selections. Accepts a dict as-is so
    callers can pass either a stored column or an in-memory map.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value) or None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    return parsed


def dump_json_map(value) -> Optional[str]:
    """Serialize a selection map for storage (0365 DB0004 §2-2).

    ensure_ascii=False keeps the Korean [전달멘트] text readable in the column. "No
    selection" has exactly ONE representation — NULL — so an empty or unusable map
    normalizes to None (invariant I4).
    """
    parsed = load_json_map(value)
    if parsed is None:
        return None
    try:
        return json.dumps(parsed, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _clean_text(value) -> Optional[str]:
    """Blank text is "no selection" too — normalize it to NULL like the maps above."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def upsert(
    *,
    group_id: str,
    doc_ref: str,
    paused_by: str,
    paused_at: str,
    continuation_target_seq: Optional[int],
    docs_target: Optional[int],
    docs_reached: int,
    chain_id: Optional[str] = None,
    chain_docs_target: Optional[int] = None,
    chain_docs_reached: int = 0,
    # 0365 DB0004: the provider / [전달멘트] selections the run was started with. Every
    # caller MUST pass them (invariant I3) — this upsert overwrites every column, so a
    # call that omits them wipes the stored selections and the resume falls back to the
    # project default chain again, which is the exact bug B0001 reported.
    continuation_base_provider_id: Optional[str] = None,
    continuation_provider_overrides=None,
    continuation_default_note: Optional[str] = None,
    continuation_note_overrides=None,
) -> None:
    """Record (or refresh) the paused row for a group — idempotent on repeat pause."""
    now = now_iso()
    get_store()._execute(
        "INSERT INTO ai_invoke_paused_chains"
        "(group_id, doc_ref, mode, paused_by, paused_at,"
        " continuation_target_seq, docs_target, docs_reached,"
        " chain_id, chain_docs_target, chain_docs_reached,"
        " continuation_base_provider_id, continuation_provider_overrides,"
        " continuation_default_note, continuation_note_overrides,"
        " created_at, updated_at) "
        "VALUES (?, ?, 'continuous', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(group_id) DO UPDATE SET "
        "doc_ref = excluded.doc_ref, "
        "paused_by = excluded.paused_by, "
        "paused_at = excluded.paused_at, "
        "continuation_target_seq = excluded.continuation_target_seq, "
        "docs_target = excluded.docs_target, "
        "docs_reached = excluded.docs_reached, "
        "chain_id = excluded.chain_id, "
        "chain_docs_target = excluded.chain_docs_target, "
        "chain_docs_reached = excluded.chain_docs_reached, "
        "continuation_base_provider_id = excluded.continuation_base_provider_id, "
        "continuation_provider_overrides = excluded.continuation_provider_overrides, "
        "continuation_default_note = excluded.continuation_default_note, "
        "continuation_note_overrides = excluded.continuation_note_overrides, "
        "updated_at = excluded.updated_at",
        [group_id, doc_ref, paused_by, paused_at,
         continuation_target_seq, docs_target, docs_reached,
         chain_id, chain_docs_target, chain_docs_reached,
         _clean_text(continuation_base_provider_id),
         dump_json_map(continuation_provider_overrides),
         _clean_text(continuation_default_note),
         dump_json_map(continuation_note_overrides),
         now, now],
    )


def get_by_group(group_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM ai_invoke_paused_chains WHERE group_id = ?",
        [group_id],
    )


def exists(group_id: str) -> bool:
    return get_by_group(group_id) is not None


def delete_and_return(group_id: str) -> Optional[dict]:
    """Atomically consume the paused row (L0009 2.4 step 3).

    Returns the row when THIS call removed it, None when there was nothing to
    consume (another resume path already took it → resume_conflict). SELECT +
    DELETE run in one transaction; the group lock in ai_invoke_service is the
    first-line serializer, this is the second line of defense (DB0010 §4).
    """
    store = get_store()
    with store.transaction():
        row = store._fetch_one(
            "SELECT * FROM ai_invoke_paused_chains WHERE group_id = ?",
            [group_id],
        )
        if row is None:
            return None
        store._execute(
            "DELETE FROM ai_invoke_paused_chains WHERE group_id = ?",
            [group_id],
        )
        return row


def delete_by_group(group_id: str) -> None:
    """Chain-termination cleanup (L0009 §3 transition table): a chain that ends for
    any reason other than the user pause must not leave a ghost paused card."""
    get_store()._execute(
        "DELETE FROM ai_invoke_paused_chains WHERE group_id = ?",
        [group_id],
    )


def list_by_user(user_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM ai_invoke_paused_chains WHERE paused_by = ? ORDER BY paused_at DESC",
        [user_id],
    )
