"""A person writes a card line that has no document (flowgate.v02.0056 T#3, 0002-CH turn 6 §5 / turn 10 O3).

The document tab shows every line of a card; a line with no live document (its type was not registered when the
card was placed, its document was cancelled or deleted) offers [작성]. Saving it makes the line's document through
the card's own creation path (``legacy_adapter.create_document``: numbered, filed and logged as a v0.1 related
document, ``doc_created``) with the person's text after its front matter, and links the line to it:

- **the node is open in the Flow's active run and the line is of its result's type** (the type the result
  binder takes, ``result_binder.result_type``): the document is the node's result, registered with the node's
  dispatch token (L0009 E-23: the token stays a person's). Everything that decides and spends the token is one
  Flow guard transaction, so of two writes on one node only one ever registers: the node is judged again under
  the guard (open, its result's type, not in its card's instruction stage, its AI and the card's review not
  working, its dispatch token not spent), the document is made, the token is claimed (a compare-and-swap
  consume: ``token_consumed`` with the document, the record the run reconciliation binds from), the document is
  opened and submitted (pending_review) and the line takes it as a run result takes its line (``adopt_result``:
  the card's untouched empty draft is superseded; a line with no stored row or no live document gets its row).
  A write that loses (the token spent, the line taken) leaves no document, no consume and no line change: 409.
  After the commit the Graph is told (``after_inbox_registered``: the binder binds it). A person's result is
  never approved by itself: the node goes to review and waits for the card approval (O1 ``awaiting_approval``).
  Refused while the node is in its card's instruction stage, while its AI (or the card's review) still works on
  it, with no text, or when its dispatch token is already spent. An AI start that holds the group lease but has not
  mapped its token yet loses to the write: its mapping guard finds the registered result and refuses the start
  (``ai_node_starter``), and the run's end never reviews or approves a result its tokens did not register
  (``card_settings.legacy_adapter.node_of_ai_run``).
- **otherwise**: a draft of the line's type (the person's text in it), linked to the line as the card's own.

A line that already shows a written document, or a document that is not the card's untouched draft (outside the
result path), is refused (409 ``slot_has_document``): that document is opened and edited instead. The scenario
line of a test card shows its instruction's document and is never written on its own. The write runs inside the
Flow guard; only the result path's Graph contact follows its commit, taking the Graph's own guard as the inbox's
does.
"""
from __future__ import annotations

from typing import Any, Optional

from flowgate.card_documents import legacy_adapter as legacy
from flowgate.card_documents import lines as card_lines
from flowgate.card_documents import repository
from flowgate.card_documents import slots as cs
from flowgate.card_settings import repository as card_settings

CONTENT_MAX_LEN = 500_000
_BODY_KEYS = {"content"}


def parse_body(body: Any) -> tuple[Optional[str], list[dict]]:
    """``(content, errors)`` of a write request: ``{"content": <text> | null}`` (absent = no text)."""
    if body is None:
        return None, []
    if not isinstance(body, dict):
        return None, [{"field": "body", "reason": "not_an_object"}]
    errors = [{"field": key, "reason": "unknown_field"} for key in sorted(set(body) - _BODY_KEYS)]
    content = body.get("content")
    if content is not None and not isinstance(content, str):
        errors.append({"field": "content", "reason": "not_a_text"})
        content = None
    elif content is not None and len(content) > CONTENT_MAX_LEN:
        errors.append({"field": "content", "reason": "too_long", "max": CONTENT_MAX_LEN})
    return content, errors


def _refused(status: int, code: str, params: dict, message: str) -> legacy.GraphApiError:
    return legacy.GraphApiError(status, code, params, message=message)


def _invalid(field: str, reason: str, **extra: Any) -> legacy.GraphApiError:
    return legacy.GraphApiError(422, "invalid_request", {"errors": [{"field": field, "reason": reason, **extra}]},
                                message="The request does not fit this card line.")


def _card(flow_id: str, node_key: str) -> dict:
    node = card_settings.card_node(flow_id, node_key)
    if node is None:
        raise _refused(404, "node_not_found", {"flow_id": flow_id, "node_key": node_key},
                       "The card does not exist in this flow.")
    return node


def _slot(flow_id: str, node: dict, slot_key: str, registered: set[str]) -> tuple[cs.CardSlot, int]:
    spec = card_lines._slots(flow_id, node)
    slot = next((s for s in spec if s.slot == slot_key), None)
    if slot is None:
        raise _invalid("slot", "not_a_slot_of_this_card", slots=[s.slot for s in spec])
    if slot.shares:
        raise _invalid("slot", "slot_shares_a_document", shares=slot.shares)
    if slot.doc_type not in registered:
        raise _invalid("slot", "doc_type_not_registered", doc_type=slot.doc_type)
    return slot, spec.index(slot)


def _line(flow_id: str, node: dict, slot: cs.CardSlot) -> tuple[Optional[dict], Optional[dict]]:
    """(the stored line row of this kind, the live document it shows) — either may be None."""
    row = next((r for r in repository.rows(flow_id, [node["node_key"]])
                if (r["slot"], r["doc_type"]) == cs.slot_signature(slot)), None)
    shown = card_lines.line_document(flow_id, node["node_key"], slot.slot, node.get("node_type"), node.get("doc_type"))
    return row, (shown or {}).get("document")


def _untouched_draft(row: Optional[dict], doc: Optional[dict]) -> bool:
    """The line's document is the empty draft the card made for it (a run result supersedes it)."""
    return bool(row and row["owned"] and row["doc_id"] == (doc or {}).get("doc_id")
                and cs.is_disposable_draft(doc) and not card_lines.has_body(doc))


def _link(flow_id: str, node_key: str, slot: cs.CardSlot, ordinal: int, row: Optional[dict], doc_id: str,
          user_id: str, owned: bool = True) -> None:
    """The line now holds ``doc_id`` — as the card's own, or (``owned`` False) as a run result is held, never
    deleted with the card. Its old row, holding a gone document, is replaced."""
    if row is not None:
        repository.delete(flow_id, node_key, row["slot"], row["doc_type"])
    repository.insert(flow_id, node_key, slot.slot, slot.doc_type, doc_id, owned, ordinal, user_id)


def _shown_line(flow_id: str, node: dict, slot: cs.CardSlot) -> Optional[dict]:
    found = card_lines.line_document(flow_id, node["node_key"], slot.slot, node.get("node_type"), node.get("doc_type"))
    return {k: v for k, v in found.items() if k != "document"} if found else None


def write_line(group: dict, node_key: str, slot_key: str, content: Optional[str], user_id: str) -> dict:
    """``POST /flows/{flow_id}/nodes/{node_key}/slots/{slot}/document``."""
    flow_id = group["group_id"]
    legacy.require_writable(flow_id)
    if not repository.available():
        raise _refused(409, "card_documents_unavailable", {"flow_id": flow_id},
                       "Card documents are not available on this server yet.")
    registered = legacy.registered_doc_types(group.get("project_id"))
    node = _card(flow_id, node_key)
    slot, ordinal = _slot(flow_id, node, slot_key, registered)
    if slot.doc_type == "WP" and content is not None:
        raise _invalid("content", "not_for_this_slot")       # a work plan is made empty and filled in its editor

    def body() -> tuple[Optional[tuple[str, str]], Optional[dict]]:
        running = legacy.running_node(flow_id, node_key)       # judged under the guard: one writer at a time
        if running is not None and running["result_type"] == slot.doc_type:
            return _reserve_result(group, node, slot, ordinal, running, content, user_id), None
        row, doc = _line(flow_id, node, slot)
        if doc is not None:
            raise _refused(409, "slot_has_document", {"flow_id": flow_id, "node_key": node_key, "slot": slot.slot,
                                                      "doc_id": doc["doc_id"]}, "This line already has a document.")
        doc_id = legacy.create_document(group, node, slot, user_id, content)
        _link(flow_id, node_key, slot, ordinal, row, doc_id, user_id)
        return None, _answer(flow_id, node, slot, doc_id, False)

    reserved, answer = legacy.with_flow_guard(flow_id, body)
    if reserved is None:
        return answer
    doc_id, token_id = reserved
    legacy.tell_graph(flow_id, doc_id, slot.doc_type, token_id)   # after the commit, as the inbox tells it
    return _answer(flow_id, node, slot, doc_id, _bound(flow_id, node_key, doc_id))


def _reserve_result(group: dict, node: dict, slot: cs.CardSlot, ordinal: int, running: dict,
                    content: Optional[str], user_id: str) -> tuple[str, str]:
    """Inside the Flow guard: the node's result made, its dispatch token claimed, the document submitted and the
    line holding it — ``(doc_id, token_id)``. Any refusal raises, so the guard's transaction leaves nothing."""
    flow_id, node_key = group["group_id"], node["node_key"]
    params = {"flow_id": flow_id, "node_key": node_key, "slot": slot.slot}
    if running["instruction_stage"]:
        raise _refused(409, "node_in_instruction_stage", params, "The card is writing its instruction now.")
    if running["ai_working"]:
        raise _refused(409, "node_ai_working", params, "The card's AI is still working on it.")
    if not (content or "").strip():
        raise _invalid("content", "empty_body")               # a result is approved only with a body
    token_id = running["token_id"]
    if not token_id:
        raise _refused(409, "node_token_unavailable", params, "The card's run token is already used.")
    row, doc = _line(flow_id, node, slot)
    if doc is not None and not _untouched_draft(row, doc):
        raise _refused(409, "slot_has_document", {**params, "doc_id": doc["doc_id"]},
                       "This line already has a document.")
    doc_id = legacy.create_document(group, node, slot, user_id, content)
    if not legacy.register_result(doc_id, token_id, user_id):    # another writer spent the token first
        legacy.discard_file(doc_id)                            # the rollback removes its rows, not its file
        raise _refused(409, "node_token_unavailable", params, "The card's run token is already used.")
    if doc is None:                                            # no row, or a row whose document is gone
        _link(flow_id, node_key, slot, ordinal, row, doc_id, user_id, owned=False)
    else:                                                      # the card's empty draft: superseded by the result
        card_lines.adopt_result(flow_id, node_key, doc_id)
    return doc_id, token_id


def _bound(flow_id: str, node_key: str, doc_id: str) -> bool:
    return legacy.node_result(flow_id, node_key) == doc_id


def _answer(flow_id: str, node: dict, slot: cs.CardSlot, doc_id: str, bound: bool) -> dict:
    return {"flow_id": flow_id, "node_key": node["node_key"], "slot": slot.slot, "doc_type": slot.doc_type,
            "doc_id": doc_id, "bound_as_result": bound, "line": _shown_line(flow_id, node, slot)}
