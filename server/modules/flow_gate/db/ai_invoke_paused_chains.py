"""ai_invoke_paused_chains CRUD (group 0252 DB0010).

Single source of truth for the miniplayer pause/resume state (L0009 paused_store).
One row = one user-paused continuous chain. UNIQUE(group_id) enforces "at most one
paused row per group" and is the upsert conflict key; pending_q_doc_ids is NEVER
stored here — it is derived live from the group's open Q documents (DB0010 §4).
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso


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
    stop_kind: str = "user",
    stop_code: Optional[str] = None,
    stop_run_id: Optional[str] = None,
    stop_last_message_excerpt: Optional[str] = None,
) -> None:
    """Record (or refresh) the paused row for a group — idempotent on repeat pause.

    chain_id/chain_docs_target/chain_docs_reached (group 0357 T0004) carry the
    CHAIN-lifetime progress across the per-hop runs an unmanned continuous chain is
    made of, so a resumed chain keeps counting from where the chain — not the last
    hop — left off.

    stop_kind/stop_code/stop_run_id/stop_last_message_excerpt (group 0359 DB0008 Q7)
    mark a chain that a *system* stop (not the user) parked here so the miniplayer
    can surface it with the same [resume] affordance. Existing call sites (user
    pauses) omit these and get stop_kind='user' with the other three NULL, matching
    how a legacy row (NULL stop_kind) is read back as 'user' (DB0008 §2.3).
    """
    now = now_iso()
    get_store()._execute(
        "INSERT INTO ai_invoke_paused_chains"
        "(group_id, doc_ref, mode, paused_by, paused_at,"
        " continuation_target_seq, docs_target, docs_reached,"
        " chain_id, chain_docs_target, chain_docs_reached,"
        " stop_kind, stop_code, stop_run_id, stop_last_message_excerpt,"
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
        "stop_kind = excluded.stop_kind, "
        "stop_code = excluded.stop_code, "
        "stop_run_id = excluded.stop_run_id, "
        "stop_last_message_excerpt = excluded.stop_last_message_excerpt, "
        "updated_at = excluded.updated_at",
        [group_id, doc_ref, paused_by, paused_at,
         continuation_target_seq, docs_target, docs_reached,
         chain_id, chain_docs_target, chain_docs_reached,
         stop_kind, stop_code, stop_run_id, stop_last_message_excerpt, now, now],
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
