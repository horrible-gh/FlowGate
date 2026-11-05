"""P#1 errors, with only authorized current-document facts in their params."""
import secrets
from modules.flow_gate.graph_workflow.services.errors import GraphApiError


def internal_error():
    return GraphApiError(500, "internal_error", {"trace_id": "cde_" + secrets.token_hex(8)})


def invalid(code, field, reason, current=None, **extra):
    params = {"errors": [{"field": field, "reason": reason, **extra}]}
    if current is not None:
        params.update(doc_id=current["doc_id"], revision_no=current["revision_no"])
    return GraphApiError(422, code, params)


def coordinates(req, current=None):
    return {"flow_id": req.flow_id, "node_key": req.node_key, "slot": req.slot,
            "doc_id": current["doc_id"] if current else None,
            "revision_no": current["revision_no"] if current else None}


def conflict(req, current):
    return GraphApiError(409, "revision_conflict",
                         {**coordinates(req, current), "expected_revision": req.expected_revision})


def not_editable(req, current, reason, **extra):
    return GraphApiError(422, "not_editable", {**coordinates(req, current), "reason": reason, **extra})
