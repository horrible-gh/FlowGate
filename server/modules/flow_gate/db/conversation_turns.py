"""Persistence primitives for append-only conversation turns.

All SQL is authored in the repository's canonical SQLite dialect and passes through
FlowGateStore's runtime translator.  Transaction ownership stays with callers so a
unique violation can be rolled back before PostgreSQL is queried again.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from .connection import get_store, now_iso

PARTICIPANT_KEY_MAX = 160
MIGRATION_LOCK_TTL_SECONDS = 600
_JST = timezone(timedelta(hours=9))


def compose_participant_key(prefix: str, identity: str) -> str:
    """Build a deterministic, MySQL-index-safe participant key."""
    full = f"{prefix}:{identity}"
    if len(full) <= PARTICIPANT_KEY_MAX:
        return full
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:12]
    return f"{full[:PARTICIPANT_KEY_MAX - 13]}~{digest}"


def current_head_seq(doc_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COALESCE(MAX(seq), 0) AS head_seq FROM conversation_turns WHERE doc_id = ?",
        [doc_id],
    )
    return int((row or {}).get("head_seq") or 0)


def get_turn(doc_id: str, seq: int) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM conversation_turns WHERE doc_id = ? AND seq = ?",
        [doc_id, seq],
    )


def get_turn_by_idempotency_hash(doc_id: str, idempotency_hash: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM conversation_turns WHERE doc_id = ? AND idempotency_hash = ?",
        [doc_id, idempotency_hash],
    )


def list_turns(doc_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM conversation_turns WHERE doc_id = ? ORDER BY seq ASC", [doc_id]
    )


def count_turns(doc_id: str) -> int:
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS total FROM conversation_turns WHERE doc_id = ?", [doc_id]
    )
    return int((row or {}).get("total") or 0)


def fetch_turns_after(doc_id: str, after_seq: int, limit: int) -> list[dict]:
    """Forward page.  Callers pass limit+1 so "is there more" needs no second query."""
    return get_store()._fetch_all(
        "SELECT * FROM conversation_turns WHERE doc_id = ? AND seq > ? "
        "ORDER BY seq ASC LIMIT ?",
        [doc_id, after_seq, limit],
    )


def fetch_turns_before(doc_id: str, before_seq: int, limit: int) -> list[dict]:
    """Backward page (scroll-up).  Rows come back newest-first; the caller reverses."""
    return get_store()._fetch_all(
        "SELECT * FROM conversation_turns WHERE doc_id = ? AND seq < ? "
        "ORDER BY seq DESC LIMIT ?",
        [doc_id, before_seq, limit],
    )


def first_turns(doc_id: str, limit: int) -> list[dict]:
    return fetch_turns_after(doc_id, 0, limit)


def insert_turn_with_next_seq(
    *,
    doc_id: str,
    speaker: str,
    participant_key: str,
    display_name: Optional[str],
    locale: Optional[str],
    body: str,
    body_hash: str,
    based_on_seq: int,
    source_run_id: Optional[str],
    idempotency_key: str,
    idempotency_hash: str,
    created_at: str,
) -> dict:
    """Allocate the next per-document sequence and insert it in one statement.

    Deliberately has no ON CONFLICT/IGNORE clause.  A uniqueness error is the signal
    that the outer service must roll back and retry in a fresh transaction.
    """
    store = get_store()
    store._execute(
        "INSERT INTO conversation_turns "
        "(doc_id, seq, speaker, participant_key, display_name, locale, body, body_hash, "
        "based_on_seq, stale_since_seq, source_run_id, idempotency_key, "
        "idempotency_hash, created_at) "
        "SELECT ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ? "
        "FROM conversation_turns WHERE doc_id = ?",
        [
            doc_id, speaker, participant_key, display_name, locale, body, body_hash,
            based_on_seq, source_run_id, idempotency_key, idempotency_hash,
            created_at, doc_id,
        ],
    )
    row = get_turn_by_idempotency_hash(doc_id, idempotency_hash)
    if row is None:
        raise RuntimeError("conversation turn insert produced no row")
    return row


def insert_migrated_turn(
    *, doc_id: str, seq: int, speaker: str, participant_key: str,
    display_name: Optional[str], locale: Optional[str], body: str,
    body_hash: str, based_on_seq: int, idempotency_key: str,
    idempotency_hash: str, created_at: str,
) -> dict:
    get_store()._execute(
        "INSERT INTO conversation_turns "
        "(doc_id, seq, speaker, participant_key, display_name, locale, body, body_hash, "
        "based_on_seq, stale_since_seq, source_run_id, idempotency_key, "
        "idempotency_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?)",
        [doc_id, seq, speaker, participant_key, display_name, locale, body, body_hash,
         based_on_seq, idempotency_key, idempotency_hash, created_at],
    )
    row = get_turn(doc_id, seq)
    if row is None:
        raise RuntimeError("migrated conversation turn insert produced no row")
    return row


def compute_stale_since(
    doc_id: str, based_on_seq: int, assigned_seq: int, participant_key: str
) -> Optional[int]:
    if based_on_seq >= assigned_seq - 1:
        return None
    row = get_store()._fetch_one(
        "SELECT MIN(seq) AS stale_since_seq FROM conversation_turns "
        "WHERE doc_id = ? AND seq > ? AND seq < ? AND participant_key <> ?",
        [doc_id, based_on_seq, assigned_seq, participant_key],
    )
    value = (row or {}).get("stale_since_seq")
    return int(value) if value is not None else None


def set_stale_since(doc_id: str, seq: int, stale_since_seq: Optional[int]) -> None:
    get_store()._execute(
        "UPDATE conversation_turns SET stale_since_seq = ? WHERE doc_id = ? AND seq = ?",
        [stale_since_seq, doc_id, seq],
    )


def get_participant(doc_id: str, participant_key: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM conversation_participants WHERE doc_id = ? AND participant_key = ?",
        [doc_id, participant_key],
    )


def list_participants(doc_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM conversation_participants WHERE doc_id = ? ORDER BY first_seen_seq, id",
        [doc_id],
    )


def get_last_read_seq(doc_id: str, participant_key: str) -> int:
    row = get_participant(doc_id, participant_key)
    return int((row or {}).get("last_read_seq") or 0)


def touch_participant(
    *, doc_id: str, participant_key: str, kind: str,
    display_name: Optional[str], written_seq: Optional[int] = None,
    read_upto: Optional[int] = None, viewed_upto: Optional[int] = None,
    seen_at: Optional[str] = None,
) -> dict:
    """Create/touch a participant while making every cursor monotonic."""
    store = get_store()
    ts = seen_at or now_iso()
    first_seen = int(written_seq or 0)
    store._execute(
        "INSERT INTO conversation_participants "
        "(doc_id, participant_key, kind, display_name, first_seen_seq, last_read_seq, "
        "last_viewed_seq, last_written_seq, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?) "
        "ON CONFLICT (doc_id, participant_key) DO NOTHING",
        [doc_id, participant_key, kind, display_name, first_seen, ts],
    )
    if display_name:
        store._execute(
            "UPDATE conversation_participants SET display_name = ?, last_seen_at = ? "
            "WHERE doc_id = ? AND participant_key = ?",
            [display_name, ts, doc_id, participant_key],
        )
    else:
        store._execute(
            "UPDATE conversation_participants SET last_seen_at = ? "
            "WHERE doc_id = ? AND participant_key = ?",
            [ts, doc_id, participant_key],
        )
    if written_seq is not None:
        store._execute(
            "UPDATE conversation_participants SET last_written_seq = ? "
            "WHERE doc_id = ? AND participant_key = ? AND last_written_seq < ?",
            [written_seq, doc_id, participant_key, written_seq],
        )
    if read_upto is not None:
        store._execute(
            "UPDATE conversation_participants SET last_read_seq = ? "
            "WHERE doc_id = ? AND participant_key = ? AND last_read_seq < ?",
            [read_upto, doc_id, participant_key, read_upto],
        )
    if viewed_upto is not None:
        # Read first: reversing these statements can violate viewed <= read.
        store._execute(
            "UPDATE conversation_participants SET last_read_seq = ?, last_seen_at = ? "
            "WHERE doc_id = ? AND participant_key = ? AND last_read_seq < ?",
            [viewed_upto, ts, doc_id, participant_key, viewed_upto],
        )
        store._execute(
            "UPDATE conversation_participants SET last_viewed_seq = ? "
            "WHERE doc_id = ? AND participant_key = ? AND last_viewed_seq < ?",
            [viewed_upto, doc_id, participant_key, viewed_upto],
        )
    row = get_participant(doc_id, participant_key)
    if row is None:
        raise RuntimeError("conversation participant upsert produced no row")
    return row


def advance_participant_cursor(
    doc_id: str, participant_key: str, upto_seq: int, reason: str
) -> Optional[dict]:
    """Monotonically advance delivered/viewed state, clamped to the current head."""
    if reason not in {"delivered", "viewed"}:
        raise ValueError("unknown conversation read reason")
    if upto_seq < 0:
        raise ValueError("last_read_seq must be >= 0")
    row = get_participant(doc_id, participant_key)
    if row is None:
        return None
    upto = min(int(upto_seq), current_head_seq(doc_id))
    ts = now_iso()
    store = get_store()
    store._execute(
        "UPDATE conversation_participants SET last_read_seq = ?, last_seen_at = ? "
        "WHERE doc_id = ? AND participant_key = ? AND last_read_seq < ?",
        [upto, ts, doc_id, participant_key, upto],
    )
    if reason == "viewed":
        store._execute(
            "UPDATE conversation_participants SET last_viewed_seq = ? "
            "WHERE doc_id = ? AND participant_key = ? AND last_viewed_seq < ?",
            [upto, doc_id, participant_key, upto],
        )
    return get_participant(doc_id, participant_key)


def record_backward_page_audit(
    *, doc_id: str, participant_key: str, actor_kind: str,
    before_seq: int, returned_count: int,
) -> None:
    """Append an audit fact for a scroll-up read without touching participant cursors."""
    get_store()._execute(
        "INSERT INTO conversation_backward_page_audit "
        "(doc_id, participant_key, actor_kind, before_seq, returned_count, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [doc_id, participant_key, actor_kind, int(before_seq), int(returned_count), now_iso()],
    )


def list_ch_docs_needing_migration(limit: Optional[int] = None) -> list[dict]:
    """CH documents whose migration is not yet ``migrated`` (L0004 §2-14, bulk driver).

    A CH with no ``conversation_docs`` row has never been touched (pending); the LEFT
    JOIN is what makes that "no row" case visible without a separate existence check.
    Ordered by ``doc_id`` so repeated calls make steady, deterministic forward progress
    through the backlog rather than re-shuffling on ``updated_at`` drift.
    """
    sql = (
        "SELECT d.doc_id AS doc_id FROM documents d "
        "LEFT JOIN conversation_docs c ON c.doc_id = d.doc_id "
        "WHERE d.type_code = 'CH' AND COALESCE(c.migration_state, 'pending') <> 'migrated' "
        "ORDER BY d.doc_id ASC"
    )
    params: list = []
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return get_store()._fetch_all(sql, params)


def get_migration(doc_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM conversation_docs WHERE doc_id = ?", [doc_id]
    )


def migration_state(doc_id: str) -> str:
    row = get_migration(doc_id)
    return str((row or {}).get("migration_state") or "pending")


def ensure_migration_row(doc_id: str) -> dict:
    ts = now_iso()
    get_store()._execute(
        "INSERT INTO conversation_docs "
        "(doc_id, migration_state, turns_migrated, created_at, updated_at) "
        "VALUES (?, 'pending', 0, ?, ?) ON CONFLICT (doc_id) DO NOTHING",
        [doc_id, ts, ts],
    )
    row = get_migration(doc_id)
    if row is None:
        raise RuntimeError("conversation migration row insert produced no row")
    return row


def acquire_migration_lock(doc_id: str, owner: str) -> bool:
    ensure_migration_row(doc_id)
    now_dt = datetime.now(_JST)
    now_value = now_dt.isoformat(timespec="seconds")
    cutoff = (now_dt - timedelta(seconds=MIGRATION_LOCK_TTL_SECONDS)).isoformat(timespec="seconds")
    get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'in_progress', lock_owner = ?, "
        "lock_acquired_at = ?, failure_reason = NULL, updated_at = ? WHERE doc_id = ? "
        "AND (migration_state IN ('pending', 'failed') OR "
        "(migration_state = 'in_progress' AND lock_acquired_at < ?))",
        [owner, now_value, now_value, doc_id, cutoff],
    )
    row = get_migration(doc_id)
    return bool(row and row.get("migration_state") == "in_progress" and row.get("lock_owner") == owner)


def reset_migration_data(doc_id: str, owner: str) -> None:
    """Discard partial batches after taking or reclaiming a migration lock."""
    store = get_store()
    with store.transaction():
        row = get_migration(doc_id)
        if not row or row.get("lock_owner") != owner:
            raise RuntimeError("conversation migration lock is not owned")
        store._execute("DELETE FROM conversation_participants WHERE doc_id = ?", [doc_id])
        store._execute("DELETE FROM conversation_turns WHERE doc_id = ?", [doc_id])
        store._execute(
            "UPDATE conversation_docs SET turns_migrated = 0, updated_at = ? "
            "WHERE doc_id = ? AND lock_owner = ?",
            [now_iso(), doc_id, owner],
        )

def mark_migrated(doc_id: str, owner: str, intro: str, turns_migrated: int) -> None:
    ts = now_iso()
    get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated', intro = ?, "
        "failure_reason = NULL, turns_migrated = ?, lock_owner = NULL, "
        "lock_acquired_at = NULL, migrated_at = ?, updated_at = ? "
        "WHERE doc_id = ? AND lock_owner = ?",
        [intro, turns_migrated, ts, ts, doc_id, owner],
    )


def mark_failed(doc_id: str, owner: str, reason: str) -> None:
    ts = now_iso()
    store = get_store()
    with store.transaction():
        store._execute("DELETE FROM conversation_participants WHERE doc_id = ?", [doc_id])
        store._execute("DELETE FROM conversation_turns WHERE doc_id = ?", [doc_id])
        store._execute(
            "UPDATE conversation_docs SET migration_state = 'failed', failure_reason = ?, "
            "turns_migrated = 0, lock_owner = NULL, lock_acquired_at = NULL, updated_at = ? "
            "WHERE doc_id = ? AND lock_owner = ?",
            [reason[:2000], ts, doc_id, owner],
        )


def rebuild_participants(doc_id: str) -> None:
    store = get_store()
    turns = list_turns(doc_id)
    grouped: dict[str, dict] = {}
    for turn in turns:
        key = turn["participant_key"]
        item = grouped.setdefault(key, {
            "kind": turn["speaker"], "display_name": turn.get("display_name"),
            "first": int(turn["seq"]), "last": int(turn["seq"]),
            "seen": turn["created_at"],
        })
        item["last"] = int(turn["seq"])
        item["seen"] = turn["created_at"]
        if turn.get("display_name"):
            item["display_name"] = turn["display_name"]
    store._execute("DELETE FROM conversation_participants WHERE doc_id = ?", [doc_id])
    for key, item in grouped.items():
        store._execute(
            "INSERT INTO conversation_participants "
            "(doc_id, participant_key, kind, display_name, first_seen_seq, last_read_seq, "
            "last_viewed_seq, last_written_seq, last_seen_at) VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?)",
            [doc_id, key, item["kind"], item["display_name"], item["first"],
             item["last"], item["seen"]],
        )