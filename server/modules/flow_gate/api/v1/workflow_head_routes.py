"""Workflow sequence head lookup API (T300 — R016 T-A).

Endpoints:
  GET  /api/v1/workflow/{doc_id}/head      Retrieve sequence head step
  GET  /api/v1/workflow/{doc_id}/sequence  Retrieve entire sequence + head (P002 §6)
  Query-form GET /workflow/sequence is canonical in workflow_decision_routes.

Auth: Authorization: Bearer <token>  (auth_outbound.verify_bearer)

T-A scope:
  - Head lookup only. Head advancement (advance) and workflow decision (decide) are in T-C.
  - No automatic head consumption branches.
  - Includes doc_class (R/Q/B) in response.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.services import tr_commit_service
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.services.workflow_decision_service import (
    provider_view_of,
    resolve_row_provider,
)

router = APIRouter(prefix="/api/v1", tags=["WorkflowHead"])

# R016 §10 Next-step candidate mapping based on business rules (read-only constant)
_NEXT_CANDIDATES: dict[str, list[str]] = {
    "R":   ["DS", "T", "N", "D", "P", "L", "DB", "M"],
    "B":   ["DS", "T", "N"],
    "DS":  ["D", "P", "L", "DB", "T", "N", "DS", "M"],
    "D":   ["T", "DS", "P", "L", "M"],
    "P":   ["T", "DS", "D", "L", "M"],
    "L":   ["T", "DS", "D", "P", "M"],
    "DB":  ["T", "DS", "M"],
    "N":   ["NR"],
    "NR":  ["T", "DS", "M"],
    "T":   ["TR"],
    "TR":  ["V", "T", "M"],
    "TS":  ["TSR"],
    "TSR": ["V", "T", "M"],
    "V":   ["AC"],
    "AC":  ["C"],
    "C":   [],
    "M":   ["T", "DS", "D", "P", "L", "DB"],
}


def _derive_status(result_doc_id, result_review: str | None) -> str:
    """Derive workflow slot status from result_doc_id + doc_review_status (D030 §2 SSOT)."""
    if result_doc_id is None:
        return "pending"
    if result_review == "approved":
        return "done"
    return "in_progress"


def _head_response(
    doc_id: str,
    doc_class: str,
    decided: bool,
    head: dict | None,
) -> dict:
    head_out = None
    if head:
        head_type = head.get("type", "")
        head_out = {
            "id":        head.get("id"),
            "item_seq":  head.get("item_seq"),
            "type":      head_type,
            "label":     head.get("label", ""),
            "doc_class": head.get("doc_class", doc_class),
            "status":    _derive_status(head.get("result_doc_id"), head.get("result_doc_review_status")),
            "sort_order": head.get("sort_order"),
            "next_candidates": _NEXT_CANDIDATES.get(head_type, []),
        }
    return {
        "doc_id":   doc_id,
        "doc_class": doc_class,
        "decided":  decided,
        "head":     head_out,
    }


@router.get("/workflow/head")
def get_workflow_head_rpc(doc_id: str = Query(...), request: Request = None):
    return get_workflow_head(doc_id, request)




# ── GET /workflow/{doc_id}/head ───────────────────────────────────────────────

@router.get("/workflow/{doc_id}/head")
def get_workflow_head(doc_id: str, request: Request):
    """Retrieve the current workflow sequence head step.

        - Undecided sequence → decided=false, head=null
        - Entire sequence complete → decided=true, head=null
        - Normal → decided=true, head={step metadata}
        Auth: Bearer token required.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return JSONResponse(
            status_code=404,
            content={"error": "doc_not_found", "doc_id": doc_id},
        )

    doc_class = _resolve_doc_class(doc)
    seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    if seq is None:
        return JSONResponse(
            content=_head_response(doc_id, doc_class, decided=False, head=None)
        )

    head = db_wfseq.get_effective_head(seq["id"])
    return JSONResponse(
        content=_head_response(doc_id, doc_class, decided=True, head=head)
    )


# ── GET /workflow/{doc_id}/sequence ──────────────────────────────────────────

@router.get("/workflow/{doc_id}/sequence")
def get_workflow_sequence(doc_id: str, request: Request):
    """Retrieve full sequence items + head (P002 §6).

        Auth: Bearer token required.
    """
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return JSONResponse(
            status_code=404,
            content={"error": "doc_not_found", "doc_id": doc_id},
        )

    doc_class = _resolve_doc_class(doc)
    seq = db_wfseq.get_sequence_by_doc_id(doc_id)
    if seq is None:
        return JSONResponse(content={
            "doc_id": doc_id,
            "doc_class": doc_class,
            "decided": False,
            "sequence": [],
            "head": None,
        })

    items = db_wfseq.get_sequence_items(seq["id"])
    provider_view = provider_view_of(doc.get("project_id"))
    head = db_wfseq.get_effective_head(seq["id"])
    # 0332 D0005 §6.1: the workflow strip's per-cell commit marker. Resolved by slot
    # identity (result_doc_id), so a repeated type marks the right cell, and looked up
    # for the whole strip in ONE query. Never raises — an empty map just means the
    # strip draws no markers and looks exactly as it did before.
    commit_states = tr_commit_service.slot_commit_states(
        [i.get("result_doc_id") for i in items if i.get("result_doc_id")]
    )

    head_out = None
    if head:
        head_type = head.get("type", "")
        head_out = {
            "id":        head.get("id"),
            "item_seq":  head.get("item_seq"),
            "type":      head_type,
            "label":     head.get("label", ""),
            "doc_class": head.get("doc_class", doc_class),
            "status":    _derive_status(head.get("result_doc_id"), head.get("result_doc_review_status")),
            "next_candidates": _NEXT_CANDIDATES.get(head_type, []),
        }

    return JSONResponse(content={
        "doc_id":   doc_id,
        "doc_class": doc_class,
        "decided":  True,
        "sequence": [_serialize_item(i, provider_view, commit_states) for i in items],
        "head":     head_out,
    })


# ── Internal helpers ───────────────────────────────────────────────────────────────

def _resolve_doc_class(doc: dict) -> str:
    """Extract doc_class (R/Q/B) from a document record.

        If type_code is R return 'R', if B return 'B', otherwise default to 'R'.
    """
    type_code = (doc.get("type_code") or "").upper()
    if type_code == "B":
        return "B"
    if type_code == "Q":
        return "Q"
    return "R"


def _serialize_item(
    item: dict,
    provider_view: Optional[dict] = None,
    commit_states: Optional[dict] = None,
) -> dict:
    return {
        # 0332 D0005 §6.1 — the slot's source commit, or None. A slot whose TR changed
        # no source carries None and the cell stays unmarked: "quiet" is the design,
        # not a missing value.
        "tr_commit": (commit_states or {}).get(item.get("result_doc_id")),
        "id":        item.get("id"),
        "item_seq":  item.get("item_seq"),
        "type":      item.get("type"),
        "label":     item.get("label"),
        "doc_class": item.get("doc_class"),
        "sort_order": item.get("sort_order"),
        "status":    _derive_status(item.get("result_doc_id"), item.get("result_doc_review_status")),
        # 0018 R0001 — workflow-strip time-machine: expose the slot's realised document
        # so the FE can map a clicked strip cell to its rollback target with slot identity
        # (repeated types resolve correctly). result_doc_id = the document that filled this
        # slot; result_seq = that document's documents.seq, i.e. the reopen target_seq.
        "result_doc_id": item.get("result_doc_id"),
        "result_seq":    item.get("result_seq"),
        "note": item.get("note") or "",
        "source_doc_id": item.get("source_doc_id"),
        "source_revision_no": item.get("source_revision_no"),
        **resolve_row_provider(
            item.get("provider_id"),
            item.get("provider_display_name"),
            provider_view or {"readable": False, "providers": {}},
        ),
    }
