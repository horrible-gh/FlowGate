"""Live delivery of a single appended conversation turn (L0004 §2-11, T2).

Kept out of ``conversation_turn_service`` so the append path has no import-time
dependency on the SSE layer, and so a broadcast failure has one obvious place to be
swallowed: the turn is already committed and must never be rolled back for this
(L0004 §2-1, judgement 3).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)


def build_payload(doc: dict, result: dict) -> dict:
    """P0003 scenario 6 payload — the turn itself, not a "something changed" ping."""
    return {
        "doc_id": result["doc_id"],
        "head_seq": result["head_seq"],
        "turn": result["turn"],
        "participant": result.get("me"),
        "title": doc.get("title"),
    }


def broadcast_turn_appended(doc: Optional[dict], result: dict[str, Any]) -> int:
    """Push one turn to every open screen.  Best effort — never raises.

    Called from a synchronous route running in a worker thread, so it must go through
    the threadsafe broadcaster: a bare ``asyncio.get_event_loop()`` raises there (the
    0059 trap).
    """
    if doc is None or not result.get("turn"):
        return 0
    try:
        from modules.flow_gate.api.v1.events.event_types import EventType
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )

        return broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.CONVERSATION_TURN_APPENDED,
            payload=build_payload(doc, result),
            audience="*",
            project=doc.get("project_id"),
            group_id=doc.get("group_id"),
            doc_id=result["doc_id"],
        ))
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("[conversation turn] SSE publish failed (ignored): %s", exc)
        return 0
