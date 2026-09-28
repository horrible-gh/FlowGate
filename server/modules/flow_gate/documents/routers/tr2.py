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


class Tr2ItemWrite(BaseModel):
    expected_revision: int
    collection: str | None = None
    item: dict


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
        return tr2.read(doc_id)
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)
    except (OSError, ValueError) as exc:
        return JSONResponse(status_code=409,
                            content=error_payload("tr2_spec_invalid",
                                                  details={"loc": "body", "reason": str(exc)}))


@router.post("/{doc_id}/tr2/precheck")
@require_permission("perm_document_read")
def post_tr2_precheck(doc_id: str, current_user: dict = Depends(get_current_user)):
    from modules.flow_gate.documents.tr2_precheck import diagnostic_precheck
    _doc(doc_id)
    try:
        return diagnostic_precheck(doc_id)
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)
    except (OSError, ValueError) as exc:
        return JSONResponse(status_code=409,
                            content=error_payload("tr2_spec_invalid",
                                                  details={"loc": "body", "reason": str(exc)}))


def _writable_doc(doc_id: str) -> dict:
    """Route-level write admission shared by every TR2 mutation entry point."""
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
    return doc


@router.put("/{doc_id}/tr2")
@require_permission("perm_document_update")
def put_tr2(request: Request, doc_id: str, body: Tr2Save,
            current_user: dict = Depends(get_current_user)):
    _writable_doc(doc_id)
    try:
        return tr2.save(doc_id, body.body, actor=current_user["user_id"],
                        expected_revision=body.expected_revision)
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)


def _mutate(doc_id: str, operation: str, current_user: dict, **kwargs):
    _writable_doc(doc_id)
    try:
        return tr2.mutate(doc_id, operation, actor=current_user["user_id"], **kwargs)
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)
    except (OSError, ValueError) as exc:
        return JSONResponse(status_code=409,
                            content=error_payload("tr2_spec_invalid",
                                                  details={"loc": "body", "reason": str(exc)}))


@router.delete("/{doc_id}/tr2/spec")
@require_permission("perm_document_update")
def delete_tr2_spec(doc_id: str, expected_revision: int,
                    current_user: dict = Depends(get_current_user)):
    """Reset the editable proposal to an empty spec as a new revision; history stays."""
    return _mutate(doc_id, "reset_spec", current_user, expected_revision=expected_revision)


@router.post("/{doc_id}/tr2/items")
@require_permission("perm_document_update")
def post_tr2_item(doc_id: str, body: Tr2ItemWrite,
                  current_user: dict = Depends(get_current_user)):
    return _mutate(doc_id, "add_item", current_user, expected_revision=body.expected_revision,
                   collection=body.collection, item=body.item)


@router.get("/{doc_id}/tr2/items/{item_id:path}")
@require_permission("perm_document_read")
def get_tr2_item(doc_id: str, item_id: str, current_user: dict = Depends(get_current_user)):
    _doc(doc_id)
    try:
        return tr2.read_item(doc_id, item_id)
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)
    except (OSError, ValueError) as exc:
        return JSONResponse(status_code=409,
                            content=error_payload("tr2_spec_invalid",
                                                  details={"loc": "body", "reason": str(exc)}))


@router.put("/{doc_id}/tr2/items/{item_id:path}")
@require_permission("perm_document_update")
def put_tr2_item(doc_id: str, item_id: str, body: Tr2ItemWrite,
                 current_user: dict = Depends(get_current_user)):
    return _mutate(doc_id, "replace_item", current_user,
                   expected_revision=body.expected_revision, item_id=item_id,
                   collection=body.collection, item=body.item)


@router.delete("/{doc_id}/tr2/items/{item_id:path}")
@require_permission("perm_document_update")
def delete_tr2_item(doc_id: str, item_id: str, expected_revision: int,
                    current_user: dict = Depends(get_current_user)):
    return _mutate(doc_id, "delete_item", current_user,
                   expected_revision=expected_revision, item_id=item_id)


@router.get("/{doc_id}/tr2/files/{file_path:path}")
@require_permission("perm_document_read")
def get_tr2_file(doc_id: str, file_path: str,
                 current_user: dict = Depends(get_current_user)):
    _doc(doc_id)
    try:
        return tr2.read_file_projection(doc_id, file_path)
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)
    except (OSError, ValueError) as exc:
        return JSONResponse(
            status_code=409,
            content=error_payload("tr2_spec_invalid",
                                  details={"loc": "file", "reason": str(exc)}))


@router.get("/{doc_id}/tr2/attempts")
@require_permission("perm_document_read")
def get_tr2_attempts(doc_id: str, current_user: dict = Depends(get_current_user)):
    _doc(doc_id)
    from modules.flow_gate.db import tr2_approval_attempts
    items = tr2_approval_attempts.list_by_doc(doc_id)
    return {"items": items, "total": len(items), "next_cursor": None}
