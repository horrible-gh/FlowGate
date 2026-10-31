"""Pre-change basis (anchor) store for Self-check baseline reuse (flowgate.v02.0114 T#3).

One anchor per work unit -- a TR and its rework (D 0006 3.3). It is written once, when the
unit's first token is issued, and never rewritten: later tokens of the unit only add a row to
``tr_self_check_anchor_tokens``. Both writes are "create only when absent" on a UNIQUE key
(``open_key`` for an unregistered TR(new) draft, ``tr_doc_id`` for a registered TR), so
concurrent issues of one unit leave one anchor (migration 163).
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from .connection import get_store, now_iso

RECORDED = "recorded"
NONE = "none"


def open_key(project_id: str, group_id: str, prev_doc_ref: str) -> str:
    """sha256(UTF-8(project) NUL UTF-8(group) NUL UTF-8(prev doc_ref)), lowercase hex."""
    return hashlib.sha256(f"{project_id}\0{group_id}\0{prev_doc_ref}".encode("utf-8")).hexdigest()


def get(anchor_id: str) -> Optional[dict]:
    return get_store()._fetch_one("SELECT * FROM tr_self_check_anchors WHERE anchor_id = ?", [anchor_id])


def get_open(project_id: str, group_id: str, prev_doc_ref: str) -> Optional[dict]:
    """The still-unregistered draft anchor of (project, group, prev doc_ref), if any."""
    return get_store()._fetch_one("SELECT * FROM tr_self_check_anchors WHERE open_key = ?",
                                  [open_key(project_id, group_id, prev_doc_ref)])


def get_for_tr(tr_doc_id: str) -> Optional[dict]:
    return get_store()._fetch_one("SELECT * FROM tr_self_check_anchors WHERE tr_doc_id = ?", [tr_doc_id])


def get_for_token(token_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT a.* FROM tr_self_check_anchors a JOIN tr_self_check_anchor_tokens t"
        " ON t.anchor_id = a.anchor_id WHERE t.token_id = ?", [token_id])


def _parse_ts(value) -> Optional[datetime]:
    try:
        stamp = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def unaccounted_draft_issues(project_id: str, group_id: str, prev_doc_ref: str, token_id: str) -> list[str]:
    """Other TR(new) draft tokens of (project, group, prev doc_ref) that this unit issued before
    and that no anchor accounts for: the proof that ``token_id`` is NOT the unit's first issue.

    The draft unit's issues are the ``new`` tokens of that key issued since the key's latest
    registration (a consumed ``new`` token closes the previous unit). One that is still
    unconsumed and was never mapped to an anchor is an earlier issue whose anchor was not
    written -- its recording failed, or it was issued before anchors existed. An unreadable
    timestamp is counted (it cannot be placed outside the unit). Raises when the token store
    cannot be read; the caller then has no proof either way.
    """
    rows = get_store()._fetch_all(
        "SELECT t.token_id, t.created_at, t.consumed_at, m.anchor_id AS mapped FROM tokens t"
        " LEFT JOIN tr_self_check_anchor_tokens m ON m.token_id = t.token_id"
        " WHERE t.project = ? AND t.group_id = ? AND t.doc_ref = ? AND t.action_scope = 'new'",
        [project_id, group_id, prev_doc_ref]) or []
    closed = [_parse_ts(r.get("consumed_at")) for r in rows
              if r.get("consumed_at") and r.get("token_id") != token_id]
    if any(stamp is None for stamp in closed):
        closed = []          # an unreadable registration time cannot open a new unit
    since = max(closed) if closed else None
    out = []
    for row in rows:
        if row.get("token_id") == token_id or row.get("consumed_at") or row.get("mapped"):
            continue
        created = _parse_ts(row.get("created_at"))
        if since is not None and created is not None and created < since:
            continue         # issued for a unit that a later registration already closed
        out.append(str(row.get("token_id")))
    return sorted(out)


def _insert(project_id: str, group_id: str, *, prev_doc_ref: Optional[str], key: Optional[str],
            tr_doc_id: Optional[str], state: str, base_commit: Optional[str], reason: Optional[str],
            token_id: Optional[str], lookup) -> dict:
    """Insert unless the unit already has its anchor; return the unit's one anchor.

    A losing concurrent insert fails on the UNIQUE key; the row that won is then read back.
    Any other failure (no row afterwards) is raised to the caller."""
    if state == RECORDED and not base_commit:
        raise ValueError("a recorded anchor needs its base commit")
    if state == NONE:
        base_commit = None
    existing = lookup()
    if existing is not None:
        return existing
    anchor_id = f"anc_{uuid.uuid4().hex[:24]}"
    now = now_iso()
    try:
        get_store()._execute(
            "INSERT INTO tr_self_check_anchors (anchor_id, project_id, group_id, prev_doc_ref, open_key,"
            " tr_doc_id, state, base_commit, reason, first_token_id, created_at, linked_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [anchor_id, project_id, group_id, prev_doc_ref, key, tr_doc_id, state, base_commit,
             reason, token_id, now, now if tr_doc_id else None])
    except Exception:
        existing = lookup()
        if existing is not None:
            return existing
        raise
    return get(anchor_id)  # type: ignore[return-value]


def create_open(project_id: str, group_id: str, prev_doc_ref: str, *, state: str,
                base_commit: Optional[str], reason: Optional[str], token_id: Optional[str]) -> dict:
    """The draft unit's anchor: created only when (project, group, prev doc_ref) has none open."""
    return _insert(project_id, group_id, prev_doc_ref=prev_doc_ref,
                   key=open_key(project_id, group_id, prev_doc_ref), tr_doc_id=None, state=state,
                   base_commit=base_commit, reason=reason, token_id=token_id,
                   lookup=lambda: get_open(project_id, group_id, prev_doc_ref))


def create_for_tr(project_id: str, group_id: str, tr_doc_id: str, *, state: str,
                  base_commit: Optional[str], reason: Optional[str], token_id: Optional[str]) -> dict:
    """A registered TR's anchor: created only when the TR has none."""
    return _insert(project_id, group_id, prev_doc_ref=None, key=None, tr_doc_id=tr_doc_id, state=state,
                   base_commit=base_commit, reason=reason, token_id=token_id,
                   lookup=lambda: get_for_tr(tr_doc_id))


def map_token(token_id: str, anchor_id: str) -> None:
    """Remember which anchor a token was issued against. A token maps once; a repeat is a no-op."""
    store = get_store()
    if store._fetch_one("SELECT 1 AS x FROM tr_self_check_anchor_tokens WHERE token_id = ?", [token_id]):
        return
    try:
        store._execute("INSERT INTO tr_self_check_anchor_tokens (token_id, anchor_id, created_at) VALUES (?, ?, ?)",
                       [token_id, anchor_id, now_iso()])
    except Exception:
        if store._fetch_one("SELECT 1 AS x FROM tr_self_check_anchor_tokens WHERE token_id = ?", [token_id]):
            return
        raise


def link_to_tr(anchor_id: str, tr_doc_id: str) -> int:
    """Attach an open draft anchor to the TR its token registered. Only an unlinked row moves;
    the commit, state and reason are never touched."""
    now = now_iso()
    return get_store()._execute_affected(
        "UPDATE tr_self_check_anchors SET tr_doc_id = ?, open_key = NULL, linked_at = ?"
        " WHERE anchor_id = ? AND tr_doc_id IS NULL",
        [tr_doc_id, now, anchor_id])
