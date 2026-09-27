"""Human and direct API for the TR2 canonical document."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.documents import document_service
from modules.flow_gate.documents import tr2_service as tr2
from modules.flow_gate.documents.tr2_errors import TR2_ERRORS, error_payload

try:
    from rbac.decorators import require_permission
except ImportError:
    def require_permission(_permission):
        return lambda function: function

router = APIRouter(prefix="/documents", tags=["Documents"])


class Tr2Save(BaseModel):
    expected_revision: int
    body: dict | str


def _doc(doc_id: str) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("type_code") != tr2.TR2_TYPE_CODE:
        raise HTTPException(status_code=422, detail="Not a TR2 document")
    return doc


def _failure(exc: tr2.Tr2ValidationError) -> JSONResponse:
    return JSONResponse(status_code=TR2_ERRORS[exc.code].http_status,
                        content=error_payload(exc.code, details=exc.details))


@router.get("/{doc_id}/tr2")
@require_permission("perm_document_read")
def get_tr2(doc_id: str, current_user: dict = Depends(get_current_user)):
    doc = _doc(doc_id)
    try:
        body = tr2.load_body(tr2.canonical_path_for_doc(doc))
        return tr2.read_view(doc, body)
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)
    except (OSError, ValueError) as exc:
        return JSONResponse(status_code=409,
                            content=error_payload("tr2_spec_invalid",
                                                  details={"loc": "body", "reason": str(exc)}))


@router.put("/{doc_id}/tr2")
@require_permission("perm_document_update")
def put_tr2(request: Request, doc_id: str, body: Tr2Save,
            current_user: dict = Depends(get_current_user)):
    from modules.flow_gate.documents.routers.documents import (
        _reject_if_group_ai_running, _reject_if_group_disposed,
    )
    doc = _doc(doc_id)
    _reject_if_group_disposed(doc)
    _reject_if_group_ai_running(doc)
    if not document_service.is_document_editable(
        doc, final_approved=document_service.is_final_approved(doc)
    ):
        raise HTTPException(status_code=422, detail="Document is not editable")
    try:
        return tr2.save(doc_id, body.body, actor=current_user["user_id"],
                        expected_revision=body.expected_revision)
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)


@router.get("/{doc_id}/tr2/attempts")
@require_permission("perm_document_read")
def get_tr2_attempts(doc_id: str, current_user: dict = Depends(get_current_user)):
    _doc(doc_id)
    return {"items": [], "total": 0, "next_cursor": None}
