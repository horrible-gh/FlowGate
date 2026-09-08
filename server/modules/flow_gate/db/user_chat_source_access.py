"""CRUD for the user_chat_source_access table (group 0515, DB0008).

One row per user, holding the per-user source-editing mode
(``read_only``/``edit``/``edit_once``) and the ``edit_once`` one-shot claim marker
(DB0008 §2.1). The absence of a row is itself the answer "this user has never
chosen" -- it means ``read_only`` (D0006 §2.2, L0007 §2.1.1) -- so nothing here
creates a row implicitly.

The three CAS statements (:func:`claim`, :func:`commit`, :func:`rollback`) are the
exact SQL DB0008 §4.2~§4.4 confirmed after two rejection rounds; do not
reintroduce a two-step SELECT-then-UPDATE here -- that is precisely the race the
guarded UPDATE closes (DB0008 §4.2).
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store

# Kept beside the persisted value so every consumer — settings responses and token
# issuance alike — gets the same fail-closed interpretation of corrupt storage.
SOURCE_ACCESS_DOMAIN = ("read_only", "edit", "edit_once")
SOURCE_ACCESS_DEFAULT = "read_only"

_SELECT = (
    "SELECT user_id, source_access_mode, one_shot_token_id, one_shot_claimed_at, "
    "created_at, updated_at FROM user_chat_source_access WHERE user_id = ?"
)


def get(user_id: str) -> Optional[dict]:
    """Return this user's stored row, or None when they have never saved (DB0008 §4.1)."""
    return get_store()._fetch_one(_SELECT, [user_id])


def resolve_source_access_mode(user_id: str) -> str:
    """Resolve persisted mode fail-closed (DB0008 §4.1, L0007 §2.1.1).

    A missing row and any value outside :data:`SOURCE_ACCESS_DOMAIN` both mean
    ``read_only``. This is intentionally at the storage boundary: settings responses
    and CH-token issuance share this resolver, so a corrupted stored value cannot be
    exposed by one path or granted by another.
    """
    row = get(user_id)
    mode = row.get("source_access_mode") if row else None
    return mode if mode in SOURCE_ACCESS_DOMAIN else SOURCE_ACCESS_DEFAULT


def upsert(user_id: str, mode: str, updated_at: str) -> None:
    """Save the user's explicit choice (DB0008 §4.5, L0007 §2.2.3).

    The marker pair is unconditionally set to NULL on every call -- not merely left
    alone -- because an explicit save invalidates any one-shot claim in flight
    (L0007 §2.2.3, T0009 §3.4): the request that carries this PATCH has no opinion
    about a pending claim, so "no opinion" here means "clear it", not "keep it".
    ``created_at`` mirrors ``user_chat_settings.upsert()``'s convention: on first
    INSERT it is set to the same value as ``updated_at``; ON CONFLICT it is left
    untouched (not in the SET list) so it keeps meaning "when this row first
    appeared".
    """
    get_store()._execute(
        "INSERT INTO user_chat_source_access "
        "(user_id, source_access_mode, one_shot_token_id, one_shot_claimed_at, "
        "created_at, updated_at) "
        "VALUES (?, ?, NULL, NULL, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET "
        "source_access_mode = excluded.source_access_mode, "
        "one_shot_token_id = NULL, "
        "one_shot_claimed_at = NULL, "
        "updated_at = excluded.updated_at",
        [user_id, mode, updated_at, updated_at],
    )


def claim(user_id: str, token_id: str, claimed_at: str, updated_at: str) -> dict:
    """AVAILABLE -> CLAIMED (DB0008 §4.2, L0007 §2.4.1). One guarded UPDATE, CAS.

    Returns ``{"source_access": "read_write"|"read", "one_shot_claimed": bool}`` --
    the caller (token_service.issue) never needs a second SELECT to learn who won.
    """
    affected = get_store()._execute_affected(
        "UPDATE user_chat_source_access "
        "SET source_access_mode = 'read_only', "
        "one_shot_token_id = ?, "
        "one_shot_claimed_at = ?, "
        "updated_at = ? "
        "WHERE user_id = ? "
        "AND source_access_mode = 'edit_once' "
        "AND one_shot_token_id IS NULL",
        [token_id, claimed_at, updated_at, user_id],
    )
    won = affected == 1
    return {"source_access": "read_write" if won else "read", "one_shot_claimed": won}


def commit(user_id: str, token_id: str, updated_at: str) -> str:
    """CLAIMED -> CONSUMED (DB0008 §4.3, L0007 §2.4.2). Marker-only; mode stays read_only.

    Returns ``"COMMITTED"`` (this call cleared the marker) or ``"ALREADY_CLEARED"``
    (a PATCH or a rollback got there first) -- never collapsed to a bool (T0009 §4.2).
    """
    affected = get_store()._execute_affected(
        "UPDATE user_chat_source_access "
        "SET one_shot_token_id = NULL, "
        "one_shot_claimed_at = NULL, "
        "updated_at = ? "
        "WHERE user_id = ? AND one_shot_token_id = ?",
        [updated_at, user_id, token_id],
    )
    return "COMMITTED" if affected == 1 else "ALREADY_CLEARED"


def rollback(token_id: str, updated_at: str) -> bool:
    """CLAIMED -> AVAILABLE (DB0008 §4.4, L0007 §2.4.3). Restores ``edit_once``.

    Keyed on ``one_shot_token_id`` alone -- it identifies the row on its own, so no
    ``user_id`` is needed (DB0008 §4.4). Idempotent: a second call (e.g. the stale
    recovery sweep re-processing an already-recovered row) matches 0 rows and
    returns False rather than raising or re-restoring. Also reused verbatim for the
    A-type stale-claim recovery path (DB0008 §5.2) -- no separate recovery SQL.
    """
    affected = get_store()._execute_affected(
        "UPDATE user_chat_source_access "
        "SET source_access_mode = 'edit_once', "
        "one_shot_token_id = NULL, "
        "one_shot_claimed_at = NULL, "
        "updated_at = ? "
        "WHERE one_shot_token_id = ? AND source_access_mode = 'read_only'",
        [updated_at, token_id],
    )
    return affected == 1


def clear_b_type_marker(user_id: str, updated_at: str) -> bool:
    """Fail-closed repair for a B-type corrupted marker (DB0008 §4.7, §5.2).

    B-type: ``one_shot_token_id IS NULL`` but ``one_shot_claimed_at IS NOT NULL`` --
    no token identity survives, so this does NOT re-arm ``edit_once`` (unlike
    :func:`rollback`). ``source_access_mode`` is left untouched (it is already
    ``read_only`` whenever this marker shape is observed); only the stray
    ``one_shot_claimed_at`` is cleared. The ``one_shot_token_id IS NULL`` guard
    keeps this idempotent against a concurrent successful claim that fills the slot
    first (DB0008 §4.7).
    """
    affected = get_store()._execute_affected(
        "UPDATE user_chat_source_access "
        "SET one_shot_claimed_at = NULL, "
        "updated_at = ? "
        "WHERE user_id = ? "
        "AND one_shot_token_id IS NULL "
        "AND one_shot_claimed_at IS NOT NULL",
        [updated_at, user_id],
    )
    return affected == 1
