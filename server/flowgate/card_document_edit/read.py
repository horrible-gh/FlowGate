"""L#1 §2.6 consistent body/revision reads and L#3 W6 pure response composition."""
import time
from modules.flow_gate.db import documents
from modules.flow_gate.db.connection import get_store, in_transaction
from modules.flow_gate.graph_workflow.services.flow_guard import lock_document_row
from flowgate.card_documents import legacy_adapter as legacy
from . import target, guards
from .body import read_body_bytes, keep_sibling_exists
from .errors import GraphApiError, internal_error

READ_UNSTABLE_RETRY = 3
READ_UNSTABLE_WAIT_MS = 50
READ_RESOLVE_RETRY = 1
VIEW_KEYS = ("doc_id", "revision_no", "title", "status", "doc_review_status", "updated_at")


def _snapshot_parts(doc_id):
    assert not in_transaction(), "snapshot must own its read transaction"
    for attempt in range(READ_UNSTABLE_RETRY + 1):
        with get_store().transaction():
            lock_document_row(doc_id)  # first SQL; no Flow guard and no bump
            doc = documents.get_by_id(doc_id)
            if doc is None:
                raise internal_error()
            body = read_body_bytes(doc)
            unstable = keep_sibling_exists(body.path)
        if not unstable:
            return doc, body
        if attempt < READ_UNSTABLE_RETRY:
            time.sleep(READ_UNSTABLE_WAIT_MS / 1000)
    raise internal_error()


def snapshot(doc_id):
    doc, body = _snapshot_parts(doc_id)
    return {**{key: doc.get(key) for key in VIEW_KEYS}, "body_state": body.state,
            "content": body.text if body.state == "readable" else None}


def compose_get_body(current, body, line):
    """Pure: also used by the API-chat size guard. Never reads DB or files."""
    return {**{key: current.get(key) for key in VIEW_KEYS},
            "content": body.text if body.state == "readable" else None, "body_state": body.state,
            "line": target.public_line(line)}


def read_card(flow_id, node_key, slot, actor, origin=None):
    flow = legacy.load_flow(flow_id)
    node, spec = target.check_card(flow, node_key, slot)
    guards.authorize(actor, flow["project_id"], "perm_document_read", flow_id=flow_id)
    for _ in range(READ_RESOLVE_RETRY + 1):
        node, spec = target.check_card(flow, node_key, slot)
        line = target.resolve_current(flow_id, node, spec)
        if line is None or line.get("document") is None:
            raise GraphApiError(404, "no_document", {"flow_id": flow_id, "node_key": node_key, "slot": slot,
                                                     "doc_id": None, "revision_no": None})
        doc, body = _snapshot_parts(line["doc_id"])
        line2 = target.resolve_current(flow_id, node, spec)
        if line2 is not None and line2.get("doc_id") == doc["doc_id"]:
            return compose_get_body(doc, body, target.with_body(line2, doc, body))
    raise internal_error()
