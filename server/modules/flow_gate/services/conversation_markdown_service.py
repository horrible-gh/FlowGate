"""Deterministic markdown rendering for conversation (CH) documents (T4, L0004 §2-12).

The write side (``conversation_turn_service``) and the read side
(``conversation_query_service``) already own the turn store and the intro source; this
module only turns a stored turn set back into the exact markdown shape
``legacy_conversation.parse_conversation``/``serialize_conversation`` define, so a
migrated conversation's rendered artifact and its pre-migration file are the same bytes
for the same turns. It is deliberately NOT part of ``conversation.py`` — that module is
T5's (size cap / carry-over removal), and this render path must survive T5 untouched.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from modules.flow_gate import conversation as legacy_conversation
from modules.flow_gate.db import conversation_turns as turn_store
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.documents import document_service
from modules.flow_gate.services.conversation_turn_service import (
    CONVERSATION_TYPE_CODES,
    ConversationTurnError,
    _document_path,
)

_log = logging.getLogger(__name__)


def _row_to_legacy_turn(row: dict) -> legacy_conversation.Turn:
    """One stored turn row -> the Turn shape ``serialize_conversation`` accepts.

    Only the turn's own snapshot fields feed the render (L0004 §2-12): the human
    turn's stored ``locale`` and the AI turn's stored ``display_name`` (used as the
    header's provider slot), never a live provider/session lookup. ``ts`` is the
    stored ``created_at`` string verbatim — it is not re-parsed or re-formatted.
    """
    turn: legacy_conversation.Turn = {
        "speaker": row["speaker"],
        "ts": row["created_at"],
        "body": row.get("body") or "",
    }
    if row["speaker"] == "ai":
        if row.get("display_name"):
            turn["provider"] = row["display_name"]
    else:
        if row.get("locale"):
            turn["locale"] = row["locale"]
    return turn


def _fingerprint(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def render_markdown(doc_id: str) -> dict:
    """Render a CH document's current turns to markdown (P0003 시나리오 14).

    Returns ``{content, fingerprint, head_seq, rendered_at}``. Rendering the same turn
    set twice yields byte-identical ``content``/``fingerprint`` — ``rendered_at`` is the
    only field that ever changes, and it is computed AFTER the fingerprint so it never
    feeds the hash.
    """
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise ConversationTurnError(404, f"Document not found: {doc_id}")
    if (doc.get("type_code") or "").upper() not in CONVERSATION_TYPE_CODES:
        raise ConversationTurnError(400, "Not a conversation document.")

    from modules.flow_gate.services.conversation_query_service import intro_text

    intro = intro_text(doc)
    turns = [_row_to_legacy_turn(row) for row in turn_store.list_turns(doc_id)]
    content = legacy_conversation.serialize_conversation(turns, intro=intro)
    fingerprint = _fingerprint(content)
    return {
        "content": content,
        "fingerprint": fingerprint,
        "head_seq": turn_store.current_head_seq(doc_id),
        "rendered_at": now_iso(),
    }


def snapshot_group_conversations(project_id: str, group_id: str) -> dict:
    """Freeze every migrated CH document of a group into its markdown file (§6).

    Called at group finalize, before the group's worktree is committed — after this
    point the file entering the git snapshot matches the DB record of truth. A single
    document's render/write failure is logged and does not raise: the caller (git
    finalize) must never fail because an auxiliary artifact could not be written, and
    the DB stays authoritative regardless of whether the file update landed. A
    ``failed`` (LEGACY read-only) conversation is skipped — its file IS already the
    record of truth, so nothing needs freezing.
    """
    stats = {"scanned": 0, "written": 0, "skipped": 0, "failed": 0}
    docs = document_service.list_documents(
        project_id=project_id, group_id=group_id, type_code="CH", limit=500,
    )
    for doc in docs:
        doc_id = doc.get("doc_id")
        stats["scanned"] += 1
        if turn_store.migration_state(doc_id) != "migrated":
            stats["skipped"] += 1
            continue
        try:
            path = _document_path(doc)
            if path is None:
                raise RuntimeError("document has no resolvable file path")
            rendered = render_markdown(doc_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(rendered["content"], encoding="utf-8")
            stats["written"] += 1
        except Exception:
            _log.exception("conversation markdown snapshot failed for %s", doc_id)
            stats["failed"] += 1
    return stats
