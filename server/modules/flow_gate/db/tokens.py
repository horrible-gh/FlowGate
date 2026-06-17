"""tokens table CRUD (D020 §5-1)."""
from __future__ import annotations

from typing import Any, Optional

from .connection import get_store, now_iso, iso_days_ago


def get_by_id(token_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM tokens WHERE token_id = ?", [token_id]
    )


def get_by_hash(token_hash: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM tokens WHERE hash = ?", [token_hash]
    )


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    store._execute(
        "INSERT INTO tokens "
        "(token_id, hash, pepper_id, project, group_id, doc_ref, "
        "action_scope, issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir, "
        "continuation_target_seq, continuation_review_mode, continuation_locale) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            data["token_id"], data["hash"], data["pepper_id"],
            data["project"], data.get("group_id"), data.get("doc_ref"),
            data["action_scope"], data["issued_to"],
            data["created_at"], data["expires_at"],
            data.get("consumed_at"), data.get("revoked_at"),
            data.get("scratch_dir"),
            # Continuous work (group 0051 / migration 050). NULL/0 for ordinary tokens.
            data.get("continuation_target_seq"),
            1 if data.get("continuation_review_mode") else 0,
            # Chosen locale carried across the unmanned self-chain (group 0099 / migration
            # 051). NULL for ordinary + legacy continuation tokens → header/ko fallback.
            data.get("continuation_locale"),
        ],
    )
    return get_by_id(data["token_id"])  # type: ignore[return-value]


def consume(token_id: str) -> Optional[dict]:
    """Record consumed_at = now()."""
    store = get_store()
    store._execute(
        "UPDATE tokens SET consumed_at = ? WHERE token_id = ? AND consumed_at IS NULL",
        [now_iso(), token_id],
    )
    return get_by_id(token_id)


def increment_dry_run(token_id: str) -> None:
    """Atomically bump the per-token dry-run attempt counter (R0001 dry-run, group 0050).

    Single-statement read-modify-write (`= dry_run_count + 1`) so concurrent dry-runs
    can't lose an update. Returns nothing: the handler already holds the post-increment
    value as `cnt + 1` (no re-SELECT needed — DB0008 §3.1).
    """
    get_store()._execute(
        "UPDATE tokens SET dry_run_count = dry_run_count + 1 WHERE token_id = ?",
        [token_id],
    )


def revoke(token_id: str) -> Optional[dict]:
    """Record revoked_at = now()."""
    store = get_store()
    store._execute(
        "UPDATE tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL",
        [now_iso(), token_id],
    )
    return get_by_id(token_id)


def delete_expired(days_grace: int = 30) -> int:
    """Hard-delete tokens after days_grace days have passed since expiration (D020 §2-6)."""
    store = get_store()
    # Compute the cutoff in Python and bind it: portable across SQLite/MySQL/PostgreSQL
    # and matches the JST ISO format stored in expires_at (0088).
    store._execute(
        "DELETE FROM tokens WHERE expires_at < ?",
        [iso_days_ago(days_grace)],
    )
    return 0  # rowcount is not exposed; for logging


def count_by_date_prefix(date_str: str) -> int:
    """Count records where token_id LIKE 'tok_<date>_%' (for token_id numbering)."""
    row = get_store()._fetch_one(
        "SELECT COUNT(*) AS cnt FROM tokens WHERE token_id LIKE ?",
        [f"tok_{date_str}_%"],
    )
    return row["cnt"] if row else 0


def get_unconsumed_by_doc_ref(doc_ref: str) -> Optional[dict]:
    """Return an active next-document token for the given doc_ref (Q149 guard)."""
    # Bind the current time as a JST ISO string (same format as the stored
    # expires_at) instead of the SQLite-only strftime('now'); portable + also
    # fixes the prior format mismatch (stored +09:00 vs. the old UTC 'Z') (0088).
    return get_store()._fetch_one(
        "SELECT token_id FROM tokens WHERE doc_ref = ? AND action_scope = 'new' "
        "AND consumed_at IS NULL AND revoked_at IS NULL "
        "AND expires_at > ? LIMIT 1",
        [doc_ref, now_iso()],
    )
