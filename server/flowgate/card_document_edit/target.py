"""Card coordinates and fail-closed three-valued slot classification, L#1 §2.10."""
import logging
from flowgate.card_documents import lines, repository, slots
from flowgate.card_documents import legacy_adapter as legacy
from flowgate.card_settings import repository as settings
from .errors import GraphApiError, invalid

CARD, NON_CARD, UNAVAILABLE = "CARD", "NON_CARD", "UNAVAILABLE"
_log = logging.getLogger(__name__)


EDIT_PORT = "card_document_edit"  # T#2 integration registers this named service port.


def card_edit_port_registered():
    from modules.flow_gate.graph_workflow.services import card_document_ports
    return card_document_ports.registered(EDIT_PORT)


def check_card(flow, node_key, slot):
    flow_id = flow["group_id"]
    node = settings.card_node(flow_id, node_key)
    if node is None:
        raise GraphApiError(404, "node_not_found", {"flow_id": flow_id, "node_key": node_key})
    spec = next((s for s in lines._slots(flow_id, node) if s.slot == slot), None)
    if spec is None:
        raise invalid("card_binding_missing", "slot", "not_a_slot_of_this_card")
    if spec.shares:
        raise invalid("card_binding_missing", "slot", "slot_shares_a_document", shares=spec.shares)
    if not repository.available():
        raise invalid("card_binding_missing", "card_binding", "invalid_format",
                      validation={"reason": "card_edit_unavailable"})
    return node, spec


def resolve_current(flow_id, node, slot):
    """Same live_end and slot projection as line_document, without rereading body files.

    The service supplies body facts from its single protected byte read.
    """
    docs = {d["doc_id"]: d for d in legacy.flow_documents(flow_id) if d.get("doc_id")}
    shown = []
    for row, doc in lines._shown_rows(flow_id, docs, [node["node_key"]]):
        count = legacy.review_counts([doc["doc_id"]]).get(doc["doc_id"], 0) if doc else 0
        rejected = legacy.rejection_count(doc)
        shown.append({"slot": row["slot"], "doc_type": row["doc_type"],
                      "author": slots.author_of(row["slot"], row["doc_type"]),
                      "doc_id": doc["doc_id"] if doc else None,
                      "title": (doc or {}).get("title"), "status": (doc or {}).get("status"),
                      "doc_review_status": (doc or {}).get("doc_review_status"),
                      "has_body": False, "body_state": None,
                      "review_count": count, "rejection_count": rejected, "history_count": count + rejected})
    key = slot.slot if hasattr(slot, "slot") else slot
    line = next((l for l in slots.lines_of(node.get("node_type"), node.get("doc_type"), shown)
                 if l["slot"] == key), None)
    return {**line, "document": docs.get(line["doc_id"])} if line else None


def with_body(line, current, body):
    from flowgate.card_completion import gates
    return {**line, "has_body": body.state == "readable" and gates.text_complete(current, body.text),
            "body_state": body.state}


def public_line(line):
    keys = ("slot", "doc_type", "author", "doc_id", "title", "status", "doc_review_status",
            "has_body", "body_state", "review_count", "rejection_count", "history_count")
    return {k: line[k] for k in keys}


def line_projection(flow_id, node, slot):
    line = lines.line_document(flow_id, node["node_key"], slot.slot, node.get("node_type"), node.get("doc_type"))
    if line is None:
        raise RuntimeError("committed card line cannot be projected")
    return public_line(line)


def classify_card_slot(doc):
    if not doc.get("group_id"):
        return NON_CARD
    try:
        if not card_edit_port_registered() or not repository.available():
            return UNAVAILABLE
        rows = repository.rows(doc["group_id"])
        if not repository.available():
            return UNAVAILABLE
        docs = {d["doc_id"]: d for d in legacy.flow_documents(doc["group_id"]) if d.get("doc_id")}
        found = False
        for row in rows:
            if row["doc_id"] == doc["doc_id"]:
                found = True
            old = docs.get(row["doc_id"])
            end = legacy.live_end(doc["group_id"], old, docs)[0] if old else None
            if end is not None and end["doc_id"] == doc["doc_id"]:
                found = True
        return CARD if found else NON_CARD
    except Exception:
        _log.warning("card_classification_unavailable doc=%s", doc.get("doc_id"), exc_info=True)
        return UNAVAILABLE


def notify_scope(classification):
    if classification == UNAVAILABLE:
        _log.warning("card_classification_unavailable after commit; notifying card consumers")
    return classification != NON_CARD


def require_non_card(doc, *, inside=False):
    classification = classify_card_slot(doc)
    if classification != NON_CARD:
        reason = "card_edit_unavailable" if classification == UNAVAILABLE else "ambiguous_or_inconsistent_binding"
        raise invalid("card_binding_missing", "card_binding", "invalid_format", validation={"reason": reason})
