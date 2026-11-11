"""Chat error history: failures of a chat's own requests, kept in its conversation history
(flowgate.v02test.0024 B, decision 1).

A failed send, AI call or command decision used to show only as the notice line above the
composer: gone on F5, on reopening the chat and on the next notice. The browser now places
it in the chat's timeline and records it so it comes back:

* the server refused the request (4xx/5xx) -> the browser posts the error here;
* the request got no answer (the network / the server is down) -> the same post would fail
  too, so the browser keeps it in its own storage instead.

Storage is the existing ``workflow_events`` log (no new table): one ``chat_error`` row per
error, ``group_id`` left NULL so the group "last action" and group event listings never pick
it up, the chat and the error in ``metadata``. Only the code, the server's words and the time
are kept -- redacted, and never a token or a request header. Each user reads back only the
errors of their own requests.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from modules.flow_gate.db.connection import get_store, now_iso

from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter

EVENT_TYPE = "chat_error"

# The conversation-progress failures the timeline keeps, with their tone (decision 1 (a)).
CODES: dict[str, str] = {
    "send_failed": "danger",
    "invoke_failed": "danger",
    "command_decision_failed": "danger",
    "commit_required": "warning",
    "source_locked": "warning",
}

MAX_MESSAGE_CHARS = 4000
LIST_LIMIT = 100
# A user's errors are few; the scan is bounded and filtered per chat afterwards.
SCAN_LIMIT = 1000
_CLIENT_ID = re.compile(r"^[A-Za-z0-9.:-]{8,200}$")
_SECRETS = (
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(\b(?:authorization|token|access_token|api[_-]?key|password|secret)\b\s*[=:]\s*)[^\s,;&\"']+"),
)


def redact(text: Optional[str]) -> str:
    """The message without anything that looks like a credential."""
    value = str(text or "")
    try:
        value = legacy_adapter.redact(value)
    except Exception:
        pass
    for pattern in _SECRETS:
        value = pattern.sub(lambda m: m.group(1) + "***", value)
    return value[:MAX_MESSAGE_CHARS]


def _occurred_at(value: Any) -> str:
    """The client's time of the error when it is a valid ISO time, else now."""
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat()
        except ValueError:
            pass
    return now_iso()


def _invalid(message: str) -> c.ConversationError:
    return c.ConversationError(422, c.ERR_CONVERSATION_INVALID_REQUEST, message)


def _rows(project_id: str, user_id: str) -> list[dict]:
    return get_store()._fetch_all(
        "SELECT id, metadata, created_at FROM workflow_events "
        "WHERE event_type = ? AND project_id = ? AND actor_user_id = ? "
        "ORDER BY id DESC LIMIT ?",
        [EVENT_TYPE, project_id, user_id, SCAN_LIMIT],
    )


def _public(meta: dict) -> dict:
    return {
        "id": meta.get("client_error_id"),
        "code": meta.get("code"),
        "tone": CODES.get(str(meta.get("code")), "danger"),
        "message": meta.get("message") or "",
        "occurred_at": meta.get("occurred_at"),
    }


def _metadata(row: dict) -> Optional[dict]:
    try:
        meta = json.loads(row.get("metadata") or "")
    except (TypeError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def list_for(doc: dict, user: dict) -> list[dict]:
    """The recorded errors of ``user``'s own requests in this chat, oldest first."""
    user_id = str((user or {}).get("user_id") or "")
    if not user_id:
        return []
    doc_id = doc.get("doc_id")
    out: list[dict] = []
    for row in _rows(str(doc.get("project_id") or ""), user_id):
        meta = _metadata(row)
        if not meta or meta.get("doc_id") != doc_id or meta.get("code") not in CODES:
            continue
        out.append(_public(meta))
        if len(out) >= LIST_LIMIT:
            break
    out.reverse()
    return out


def record(doc: dict, user: dict, body: Any) -> dict:
    """Store one error of this chat; a repeat of the same ``id`` answers the stored one."""
    payload = body if isinstance(body, dict) else {}
    client_id = payload.get("id")
    code = payload.get("code")
    if not isinstance(client_id, str) or not _CLIENT_ID.match(client_id):
        raise _invalid("id must be 8..200 characters of [A-Za-z0-9.:-].")
    if code not in CODES:
        raise _invalid("code must be one of " + ", ".join(CODES) + ".")
    user_id = str((user or {}).get("user_id") or "")
    if not user_id:
        raise c.ConversationError(403, c.ERR_ACTIVITY_FORBIDDEN, "a signed-in user is required")
    doc_id = doc.get("doc_id")
    project_id = str(doc.get("project_id") or "")
    for row in _rows(project_id, user_id):
        meta = _metadata(row)
        if meta and meta.get("doc_id") == doc_id and meta.get("client_error_id") == client_id:
            return {"ok": True, "replayed": True, "error": _public(meta)}
    meta = {
        "doc_id": doc_id,
        "client_error_id": client_id,
        "code": code,
        "message": redact(payload.get("message")),
        "occurred_at": _occurred_at(payload.get("occurred_at")),
    }
    from modules.flow_gate.db import workflow_events

    workflow_events.create({
        "event_type": EVENT_TYPE,
        "project_id": project_id,
        "group_id": None,
        "document_id": None,
        "actor_user_id": user_id,
        "metadata": json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
    })
    return {"ok": True, "replayed": False, "error": _public(meta)}
