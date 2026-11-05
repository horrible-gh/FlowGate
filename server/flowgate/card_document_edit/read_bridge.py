"""Existing Agent reads compose their content and document revision from one snapshot."""
from . import target
from .read import _snapshot_parts
from .agent_binding import missing


def read(doc):
    classification = target.classify_card_slot(doc)
    if classification == target.UNAVAILABLE:
        raise RuntimeError("card_edit_unavailable")
    if classification == target.NON_CARD:
        from modules.flow_gate.api.v1 import document_routes
        content = document_routes._resolve_live_content(doc)
        return doc, content, document_routes._content_state(doc, content)
    fresh, body = _snapshot_parts(doc["doc_id"])
    return {**fresh, "_snapshot_content_state": body.state}, body.text if body.state == "readable" else None, body.state


def mention(doc):
    if target.classify_card_slot(doc) == target.NON_CARD:
        return doc, None
    fresh, content, state = read(doc)
    return {**fresh, "content": content, "content_state": state}, state
