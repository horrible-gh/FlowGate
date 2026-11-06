"""Ordered submission outcomes of a live run (flowgate.v02.0137 T0004 §2-1~3, TR0005 rev1).

`register_errors` says that SOME submission failed; it never said in which order the
failures and the successes happened. TR0005 rev0 let a run scratch be deleted when any
document had been reached, so "submit OK -> write a revised draft -> last submit 403/422"
read as settled and the newest, unsubmitted draft went with the scratch.

This ledger keeps the order. Every submission attempt a run's token makes is appended
with a monotonically increasing `seq`, its HTTP status, the document / revision the server
answered with and the review-loop hop it belongs to:

* the inbox records every non-dry-run request whose token is bound to a live run
  (`ai_run_id`) — a CLI worker's own curl, an API worker's self-HTTP call, a 401 for a
  spent token, a 403 binding refusal and a 422 validation refusal alike;
* the API worker records the refusals it produces in-process (binding / preflight /
  chat / sequence registration) and its own successes, so nothing that never reached the
  inbox is missed. A duplicate of an inbox event changes no order.

`finalize._submission_settlement` reads it: a run scratch is deleted only when no failure
happened in the current hop, or a VERIFIED success came after the last failure. Memory
only, bounded, no raw token / body / path — identifiers, statuses and counters.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Optional

EVENTS_KEY = "submission_events"
SEQ_KEY = "submission_seq"
HOP_KEY = "submission_hop"
DROPPED_KEY = "submission_events_dropped"
EVENTS_LIMIT = 200

_LOCK = threading.Lock()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def start_hop(run: dict) -> int:
    """A review-loop stage switch: later events belong to the new hop."""
    with _LOCK:
        hop = int(run.get(HOP_KEY) or 0) + 1
        run[HOP_KEY] = hop
    return hop


def record(run: Optional[dict], *, ok: bool, status: Any, via: str, action: Any = None,
           doc_id: Any = None, revision_no: Any = None, token_id: Any = None,
           token_doc_ref: Any = None) -> Optional[dict]:
    """Append one attempt. Never raises; returns the stored event (or None)."""
    if run is None:
        return None
    try:
        try:
            status_value: Optional[int] = int(status)
        except (TypeError, ValueError):
            status_value = None
        try:
            revision_value: Optional[int] = None if revision_no is None else int(revision_no)
        except (TypeError, ValueError):
            revision_value = None
        with _LOCK:
            seq = int(run.get(SEQ_KEY) or 0) + 1
            run[SEQ_KEY] = seq
            event = {
                "seq": seq,
                "hop": int(run.get(HOP_KEY) or 0),
                "at": datetime.now(timezone.utc).isoformat(),
                "ok": bool(ok),
                "status": status_value,
                "via": via,
                "action": _text(action) or None,
                "doc_id": _text(doc_id) or None,
                "revision_no": revision_value,
                "token_id": _text(token_id) or None,
                "token_doc_ref": _text(token_doc_ref) or None,
            }
            events = run.get(EVENTS_KEY)
            if not isinstance(events, list):
                events = []
                run[EVENTS_KEY] = events
            events.append(event)
            overflow = len(events) - EVENTS_LIMIT
            if overflow > 0:
                del events[:overflow]
                run[DROPPED_KEY] = int(run.get(DROPPED_KEY) or 0) + overflow
        return event
    except Exception:
        return None


def hop_events(run: dict) -> list[dict]:
    """This hop's events, oldest first (a snapshot)."""
    hop = int(run.get(HOP_KEY) or 0)
    with _LOCK:
        events = list(run.get(EVENTS_KEY) or [])
    return [dict(e) for e in events if isinstance(e, dict) and int(e.get("hop") or 0) == hop]


def note_inbox_response(token_rec: Optional[dict], body: Any, status: int, payload: Any) -> bool:
    """Called by POST /inbox for a request whose token names a live run. Never raises.

    The token must belong to that run's project and requester (0138: a run id alone can
    repeat across servers, so the id is not trusted by itself). Dry-runs never reach here.
    """
    try:
        rec = token_rec or {}
        run_id = _text(rec.get("ai_run_id"))
        if not run_id:
            return False
        from modules.flow_gate.services import ai_invoke_service

        run = ai_invoke_service.get_run_record(run_id)
        if run is None:
            return False
        if _text(rec.get("project")) and _text(run.get("project_id")) \
                and _text(rec.get("project")) != _text(run.get("project_id")):
            return False
        if _text(rec.get("issued_to")) and _text(run.get("issued_to")) \
                and _text(rec.get("issued_to")) != _text(run.get("issued_to")):
            return False
        data = payload if isinstance(payload, dict) else {}
        request = body if isinstance(body, dict) else {}
        ok = 200 <= int(status) < 300 and data.get("ok", True) is not False
        doc_id = data.get("doc_id")
        if not doc_id and isinstance(data.get("document"), dict):
            doc_id = data["document"].get("doc_id")
        record(run, ok=ok, status=status, via="inbox", action=request.get("action"),
               doc_id=doc_id or (request.get("doc_id") if ok else None),
               revision_no=data.get("revision_no"), token_id=rec.get("token_id"),
               token_doc_ref=rec.get("doc_ref"))
        return True
    except Exception:
        return False
