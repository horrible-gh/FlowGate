"""Cursor-based conversation reads (L0004 §2-9 / §2-10 / §2-8, T2).

The write side lives in ``conversation_turn_service``; this module is the read side
and shares that module's actor resolution and wire builders so a turn looks the same
whichever route produced it (P0003 §0-1).

Nothing here creates a participant row for a plain session read: L0004 §5 says a human
merely looking at a conversation is not yet a participant.  A worker read DOES create
one, because the server has just handed that worker a range and must remember it did.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from modules.flow_gate import conversation as legacy_conversation
from modules.flow_gate.db import conversation_turns as turn_store
from modules.flow_gate.documents import document_service
from modules.flow_gate.services.conversation_turn_service import (
    CONVERSATION_TYPE_CODES,
    ConversationTurnError,
    _document_path,
    migrate_conversation,
    participant_wire,
    resolve_actor,
    turn_wire,
)

# ── L0004 §1-1 page size / §1-3 read position ───────────────────────────────
# Single source of truth for the read-side numbers.  Do not inline these values.
TURN_LIMIT_DEFAULT = 50
TURN_LIMIT_MAX = 200
RESPONSE_TURNS_BYTE_MAX = 262_144
TURN_WIRE_OVERHEAD = 320
OPENING_TURNS_MAX = 3
OPENING_TURNS_BYTE_MAX = 8_192
INTRO_BYTE_MAX = 16_384
READ_REASONS = ("viewed", "delivered")

_log = logging.getLogger(__name__)


def _byte_len(value: Optional[str]) -> int:
    return len((value or "").encode("utf-8"))


def _cut_to_bytes(value: str, limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    return encoded[:limit].decode("utf-8", errors="ignore")


def _readable_document(doc_id: str) -> dict:
    """Resolve a CH document for reading.

    Reading is deliberately NOT gated on group disposal or final approval: disposal
    stops future change, it does not hide the record (D0002 §3-6 / L0004 §5).
    """
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise ConversationTurnError(404, f"Document not found: {doc_id}")
    if (doc.get("type_code") or "").upper() not in CONVERSATION_TYPE_CODES:
        raise ConversationTurnError(400, "Not a conversation document.")
    return doc


def _ensure_readable_rows(doc_id: str) -> str:
    """Lazily migrate a legacy CH on first read (L0004 §3-1: a read/append request arrives).

    A read must never fail because the old markdown has not been moved yet — that
    would make every pre-existing conversation look empty the moment this screen ships.
    A failed migration degrades to the legacy read-only shape (zero turns, intro from
    the file) rather than raising; T4 owns the full compatibility surface.
    """
    state = turn_store.migration_state(doc_id)
    if state in {"migrated", "failed"}:
        return state
    try:
        migrate_conversation(doc_id)
    except ConversationTurnError:
        raise
    except Exception:  # pragma: no cover - defensive; reads must not break on this
        _log.exception("lazy conversation migration failed for %s", doc_id)
    return turn_store.migration_state(doc_id)


def intro_text(doc: dict) -> str:
    """L0004 §2-10 intro_text_of — makes a migrated and a native conversation identical.

    Public: the markdown render path (T4 / conversation_markdown_service) shares this
    exact source so the screen and the rendered artifact never disagree on the head.
    """
    doc_id = doc["doc_id"]
    if turn_store.migration_state(doc_id) == "migrated":
        stored = (turn_store.get_migration(doc_id) or {}).get("intro")
        if stored is not None:
            return str(stored)
    path = _document_path(doc)
    if path is not None and path.is_file():
        try:
            return legacy_conversation.parse_conversation(
                path.read_text(encoding="utf-8")
            )["intro"]
        except Exception:  # pragma: no cover - unreadable legacy file
            _log.warning("could not read conversation intro from %s", path)
    return _synthesized_intro(doc)


def _synthesized_intro(doc: dict) -> str:
    """Fallback head when the markdown file is gone (L0004 §5: document file missing)."""
    fields = [
        ("project", doc.get("project_id")),
        ("module", doc.get("module")),
        ("group_id", doc.get("group_id")),
        ("type", doc.get("type_code")),
        ("title", doc.get("title")),
        ("target_id", doc.get("target_id") or doc.get("triggered_by")),
    ]
    lines = ["---"]
    lines += [f"{name}: {value}" for name, value in fields if value]
    lines.append("---")
    return "\n".join(lines)


def _carried_over_from(doc: dict) -> Optional[str]:
    """Only conversations already split by the retired carry-over have a predecessor.

    The now-removed byte-cap carry-over (inbox_routes, group 0351 T5) created the
    successor with ``triggered_by`` pointing at the CH it continued; a CH triggered
    by any other document type is not a continuation. No NEW carry-over happens
    (D0002 §3-6) — this only interprets documents split before the removal.
    """
    origin = (doc.get("triggered_by") or "").strip()
    return origin if origin.upper().endswith("-CH") else None


def build_head(doc: dict) -> dict:
    """L0004 §2-10.  Same for every requester, so repeat AI calls see a stable background."""
    doc_id = doc["doc_id"]
    intro = intro_text(doc)
    if _byte_len(intro) > INTRO_BYTE_MAX:
        intro = _cut_to_bytes(intro, INTRO_BYTE_MAX) + "\n…(intro truncated)"

    opening: list[dict] = []
    used = 0
    for row in turn_store.first_turns(doc_id, OPENING_TURNS_MAX):
        size = _byte_len(row.get("body"))
        if opening and used + size > OPENING_TURNS_BYTE_MAX:
            break
        opening.append(turn_wire(row))
        used += size

    return {
        "doc_id": doc_id,
        "type": (doc.get("type_code") or "").upper(),
        "title": doc.get("title"),
        "status": doc.get("status"),
        "group_id": doc.get("group_id"),
        "target_id": doc.get("target_id") or doc.get("triggered_by"),
        "intro": intro,
        "opening_turns": opening,
        "carried_over_from": _carried_over_from(doc),
        "total_turns": turn_store.count_turns(doc_id),
        "head_seq": turn_store.current_head_seq(doc_id),
    }


def apply_budget(rows: list[dict], limit: int) -> tuple[list[dict], Optional[str], bool]:
    """Cut on whichever of count / bytes trips first (L0004 §2-9).

    ``rows`` is expected to hold up to limit+1 entries so "is there more" is decided
    without a second query.  The first row is always admitted regardless of size —
    that is the progress guarantee: returning zero rows would make the client ask for
    the same cursor forever.
    """
    picked: list[dict] = []
    used = 0
    truncated_by: Optional[str] = None
    for row in rows:
        if len(picked) >= limit:
            truncated_by = "limit"
            break
        size = _byte_len(row.get("body")) + TURN_WIRE_OVERHEAD
        # A budget of <= 0 is treated as "byte cutting disabled" rather than as a
        # conversation that can never be read.
        if picked and RESPONSE_TURNS_BYTE_MAX > 0 and used + size > RESPONSE_TURNS_BYTE_MAX:
            truncated_by = "bytes"
            break
        picked.append(row)
        used += size
    more = truncated_by is not None or len(rows) > len(picked)
    return picked, truncated_by, more


def _me_wire(doc_id: str, resolved: dict) -> dict:
    row = turn_store.get_participant(doc_id, resolved["participant_key"])
    if row is not None:
        return participant_wire(row)
    # Virtual row (L0004 §5): a participant who has not spoken yet reads from 0.
    return {
        "participant_key": resolved["participant_key"],
        "kind": resolved["speaker"],
        "display_name": resolved.get("display_name"),
        "first_seen_seq": 0,
        "last_read_seq": 0,
        "last_written_seq": 0,
        "last_seen_at": None,
    }


def list_turns(
    *,
    doc_id: str,
    actor: dict[str, Any],
    after_seq: Optional[int] = None,
    before_seq: Optional[int] = None,
    limit: Optional[int] = None,
    include_head: bool = False,
) -> dict:
    """Return one page of turns around a cursor (P0003 scenarios 1, 2, 7, 9 and 13)."""
    if after_seq is not None and before_seq is not None:
        raise ConversationTurnError(422, "after_seq and before_seq are mutually exclusive.")
    if after_seq is not None and after_seq < 0:
        raise ConversationTurnError(422, "after_seq must be >= 0.")
    if before_seq is not None and before_seq < 1:
        raise ConversationTurnError(422, "before_seq must be >= 1.")

    effective_limit = TURN_LIMIT_DEFAULT if limit is None else int(limit)
    if effective_limit < 1:
        raise ConversationTurnError(422, "limit must be >= 1.")
    # Over the ceiling is trimmed, not rejected (P0003 §0-6).  The response reports the
    # value actually applied.
    effective_limit = min(effective_limit, TURN_LIMIT_MAX)

    doc = _readable_document(doc_id)
    _ensure_readable_rows(doc_id)
    resolved = resolve_actor(actor)

    backward = before_seq is not None
    requested_after = after_seq
    if not backward and after_seq is None:
        # No cursor at all: resume from where the server remembers this participant was.
        requested_after = turn_store.get_last_read_seq(doc_id, resolved["participant_key"])

    if backward:
        rows = turn_store.fetch_turns_before(doc_id, before_seq, effective_limit + 1)
    else:
        rows = turn_store.fetch_turns_after(doc_id, requested_after, effective_limit + 1)

    picked, truncated_by, more = apply_budget(rows, effective_limit)
    if backward:
        picked = list(reversed(picked))  # the wire is always seq-ascending
        turn_store.record_backward_page_audit(
            doc_id=doc_id,
            participant_key=resolved["participant_key"],
            actor_kind=str(actor.get("kind") or resolved["speaker"]),
            before_seq=int(before_seq),
            returned_count=len(picked),
        )

    next_after_seq: Optional[int] = None
    prev_before_seq: Optional[int] = None
    if backward:
        prev_before_seq = int(picked[0]["seq"]) if (more and picked) else None
    else:
        next_after_seq = int(picked[-1]["seq"]) if (more and picked) else None

    # Delivered is recorded only for a worker, only forward, and only up to the LAST
    # TURN ACTUALLY IN THIS RESPONSE — advancing past a truncated range would skip
    # turns that were never handed to anyone (L0004 §2-8).
    if not backward and picked and actor.get("kind") == "worker":
        record_read(
            doc_id=doc_id, actor=actor, last_read_seq=int(picked[-1]["seq"]),
            reason="delivered",
        )

    response: dict[str, Any] = {
        "ok": True,
        "doc_id": doc_id,
        "after_seq": None if backward else requested_after,
        "before_seq": before_seq,
        "limit": effective_limit,
        "head_seq": turn_store.current_head_seq(doc_id),
        "next_after_seq": next_after_seq,
        "prev_before_seq": prev_before_seq,
        "has_more": more,
        "truncated_by": truncated_by,
        "head": build_head(doc) if include_head else None,
        "turns": [turn_wire(row) for row in picked],
        # The participant row and the read boundary must be complete after the single
        # entry call (D0002 §6); scroll-up pages leave them alone.
        "participants": (
            [participant_wire(row) for row in turn_store.list_participants(doc_id)]
            if include_head
            else []
        ),
        "me": _me_wire(doc_id, resolved) if include_head else None,
    }
    return response


def record_read(
    *,
    doc_id: str,
    actor: dict[str, Any],
    last_read_seq: int,
    reason: str = "viewed",
) -> dict:
    """Advance a participant's cursor monotonically (L0004 §2-8, P0003 scenario 5).

    A value that would move a cursor backwards is silently ignored rather than
    rejected: a scrolled-up screen or a late-arriving notice legitimately carries one.
    """
    if reason not in READ_REASONS:
        raise ConversationTurnError(422, "unknown reason.")
    if last_read_seq is None or int(last_read_seq) < 0:
        raise ConversationTurnError(422, "last_read_seq must be >= 0.")

    _readable_document(doc_id)
    _ensure_readable_rows(doc_id)
    resolved = resolve_actor(actor)
    # Nobody can claim to have read the future.
    upto = min(int(last_read_seq), turn_store.current_head_seq(doc_id))

    # Unlike a plain GET, this is a deliberate cursor write, so it DOES create the
    # participant row — there is nowhere else to persist the boundary.
    turn_store.touch_participant(
        doc_id=doc_id,
        participant_key=resolved["participant_key"],
        kind=resolved["speaker"],
        display_name=resolved.get("display_name"),
        written_seq=None,
        # "viewed" implies "delivered" — seen is also received.  The reverse does not
        # hold, so a delivered notice never moves the human's read boundary.
        read_upto=upto if reason == "delivered" else None,
        viewed_upto=upto if reason == "viewed" else None,
    )
    return {
        "ok": True,
        "doc_id": doc_id,
        "head_seq": turn_store.current_head_seq(doc_id),
        "me": _me_wire(doc_id, resolved),
    }
