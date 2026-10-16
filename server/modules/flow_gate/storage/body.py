"""Shared body reads and durable save evidence (0064 T-B1c)."""
from __future__ import annotations

import json
import logging

from .paths import locate_storage_path

_log = logging.getLogger(__name__)


def body_path_state(doc: dict):
    """Locate the durable body without reading it; blank paths have their own state."""
    raw = (doc.get("file_path") or "").strip()
    if not raw:
        return None, "file_path_missing"
    return locate_storage_path(raw, doc.get("project_id"), doc.get("branch") or "main")


def file_body_read_state(doc: dict):
    path, state = body_path_state(doc)
    return state, path.read_text(encoding="utf-8") if path is not None else None


def body_read_state(doc: dict):
    """(state, text). Read errors propagate; callers that aggregate documents isolate them.

    Migrated conversations use live turns, with the existing legacy snapshot fallback.
    A frontmatter-only file is readable even though it is not an approvable body.
    """
    if doc.get("type_code") == "CH":
        from modules.flow_gate.db import conversation_turns
        from modules.flow_gate.services import conversation_markdown_service
        from modules.flow_gate.services.conversation_turn_service import ConversationTurnError

        try:
            live = conversation_turns.migration_state(doc["doc_id"]) == "migrated"
        except Exception:
            _log.warning("CH body state unavailable: %s", doc.get("doc_id"), exc_info=True)
            live = False
        if live:
            try:
                return "readable", conversation_markdown_service.render_markdown(doc["doc_id"])["content"]
            except ConversationTurnError:
                _log.warning("CH rendering failed, using snapshot: %s", doc.get("doc_id"), exc_info=True)
    return file_body_read_state(doc)


def record_body_saved(doc: dict, *, source: str, file_path: str, size: int) -> None:
    """Record in the caller's save transaction, never as a best-effort audit event."""
    from modules.flow_gate.db.connection import in_transaction
    from modules.flow_gate.db.events import insert_event

    assert in_transaction(), "body save evidence must share the save transaction"
    insert_event(doc["doc_id"], "doc_body_saved", note=json.dumps({
        "source": source, "file_path": file_path, "size": size,
    }, ensure_ascii=False))
