"""The v0.1 side of the engine router (D0004 §4.3 접점 목록, P0008 §9).

The only Graph module the v0.1 routes import. Each contact adds one call after the existing v0.1
guards; refusals use the v0.1 flat error shape (``{"error", "doc_id", "group_id",
"graph_revision_no"}``, HTTP 409) because v0.1 clients already read it. For an unassigned or v0.1
Flow every call returns exactly what v0.1 would have.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi.responses import JSONResponse

from modules.flow_gate.graph_workflow.services import v01_contact as _contact

REFUSAL_STATUS = 409


def guarded_assignment(doc: Optional[dict], call: Callable[[], Any], actor: Optional[str] = None) -> Any:
    """decide / sequence save of the v0.1 document ``doc`` (as the route already read it): the
    v0.1 call's result, or a 409 JSONResponse refusal.

    On an unassigned Flow the call runs inside the Flow guard so that it and a Graph
    confirmation cannot both assign the Flow (L0009 §2.2.3).
    """
    outcome = _contact.guarded_v01_assignment(doc, call, actor)
    if isinstance(outcome, _contact.V01Rejection):
        return JSONResponse(status_code=REFUSAL_STATUS, content=outcome.body())
    return outcome


def guarded_root_conversion(doc: Optional[dict], call: Callable[[], Any], actor: Optional[str] = None) -> Any:
    """R↔B root-type conversion of ``doc``: guarded like a sequence save (``guarded_assignment``,
    flowgate.v02.0048); inside that guard a card line holding the root follows the converted
    document (flowgate.v02.0053 T#1)."""
    return guarded_assignment(doc, lambda: _contact.follow_root_conversion(doc, call()), actor)


def progress_refusal(doc: Optional[dict]) -> Optional[tuple[str, dict]]:
    """advance: ``(code, flat body)`` to answer with 409, or None to continue as v0.1."""
    refused = _contact.progress_refusal(doc)
    return (refused.error, refused.body()) if refused is not None else None


def time_machine_refusal(doc: Optional[dict], request=None) -> Optional[JSONResponse]:
    """0061 T#12: v0.1 reopen/restore stop before touching Graph-owned state.

    Reuse the engine router's existing read-only ownership contact. v0.1 and
    unassigned flows pass unchanged; conflicts retain the ownership-conflict code.
    """
    from . import envelope
    refused = _contact.progress_refusal(doc)
    if refused is None:
        return None
    body = refused.body()
    code = "graph_engine_owned" if refused.error == "flow_owned_by_graph" else refused.error
    body.update(error=code, code=code, message=envelope.message(code, request))
    return JSONResponse(status_code=REFUSAL_STATUS, content=body)


def after_inbox_registered(flow_id: Optional[str], doc_id: str, doc_type: str, token_id: Optional[str]) -> None:
    """inbox registration committed (Step 7.5 done). Never raises, never changes the response."""
    _contact.after_document_event("inbox_registered", flow_id,
                                  {"doc_id": doc_id, "doc_type": doc_type, "token_id": token_id})


def after_review_decided(flow_id: Optional[str], doc_id: str, action: str) -> None:
    """review decision committed (approve / reject / mark_revised). Never raises."""
    _contact.after_document_event("review_decided", flow_id, {"doc_id": doc_id, "action": action})
