"""Human and direct API for the TR2 canonical document."""
from __future__ import annotations

import logging

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
log = logging.getLogger(__name__)


class Tr2Save(BaseModel):
    expected_revision: int
    body: dict | str


class Tr2ItemWrite(BaseModel):
    expected_revision: int
    collection: str | None = None
    item: dict


class Tr2Restore(BaseModel):
    expected_revision: int
    revision_no: int


class Tr2NewProposal(BaseModel):
    expected_revision: int


def _doc(doc_id: str) -> dict:
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.get("type_code") != tr2.TR2_TYPE_CODE:
        raise HTTPException(status_code=422, detail="Not a TR2 document")
    return doc


def _failure(exc: tr2.Tr2ValidationError) -> JSONResponse:
    payload = error_payload(exc.code, details=exc.details)
    if exc.code in tr2.BODY_ERROR_CODES:
        # The screen swaps to the proposal recovery surface instead of a dead end.
        payload["recovery"] = True
    return JSONResponse(status_code=TR2_ERRORS[exc.code].http_status, content=payload)


def _unexpected(doc_id: str, exc: Exception) -> JSONResponse:
    # Exception text and host paths stay in the operator log (T0030 §5).
    log.exception("TR2 request failed for %s", doc_id)
    return JSONResponse(status_code=500, content=error_payload("tr2_internal_error"))


def _run(doc_id: str, call):
    try:
        return call()
    except HTTPException:
        raise
    except tr2.Tr2ValidationError as exc:
        return _failure(exc)
    except Exception as exc:  # noqa: BLE001 — mapped to a path-free internal error
        return _unexpected(doc_id, exc)


@router.get("/{doc_id}/tr2")
@require_permission("perm_document_read")
def get_tr2(doc_id: str, current_user: dict = Depends(get_current_user)):
    _doc(doc_id)
    return _run(doc_id, lambda: tr2.read(doc_id))


@router.post("/{doc_id}/tr2/precheck")
@require_permission("perm_document_read")
def post_tr2_precheck(doc_id: str, current_user: dict = Depends(get_current_user)):
    from modules.flow_gate.documents.tr2_precheck import diagnostic_precheck
    _doc(doc_id)
    return _run(doc_id, lambda: diagnostic_precheck(doc_id))


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
    return _run(doc_id, lambda: tr2.save(doc_id, body.body, actor=current_user["user_id"],
                                         expected_revision=body.expected_revision))


def _mutate(doc_id: str, operation: str, current_user: dict, **kwargs):
    _writable_doc(doc_id)
    return _run(doc_id, lambda: tr2.mutate(doc_id, operation, actor=current_user["user_id"],
                                           **kwargs))


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
    return _run(doc_id, lambda: tr2.read_item(doc_id, item_id))


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
    return _run(doc_id, lambda: tr2.read_file_projection(doc_id, file_path))


@router.get("/{doc_id}/tr2/attempts")
@require_permission("perm_document_read")
def get_tr2_attempts(doc_id: str, current_user: dict = Depends(get_current_user)):
    _doc(doc_id)
    from modules.flow_gate.db import tr2_approval_attempts
    items = [tr2.public_attempt(row) for row in tr2_approval_attempts.list_by_doc(doc_id)]
    return {"items": items, "total": len(items), "next_cursor": None}


# ── Proposal recovery (반영안 복구, T0030 §6) — never the approval rollback ─────────

@router.get("/{doc_id}/tr2/recovery")
@require_permission("perm_document_read")
def get_tr2_recovery(doc_id: str, current_user: dict = Depends(get_current_user)):
    _doc(doc_id)
    return _run(doc_id, lambda: tr2.recovery_view(doc_id))


@router.get("/{doc_id}/tr2/revisions/{revision_no}")
@require_permission("perm_document_read")
def get_tr2_revision(doc_id: str, revision_no: int,
                     current_user: dict = Depends(get_current_user)):
    _doc(doc_id)
    return _run(doc_id, lambda: tr2.read_revision(doc_id, revision_no))


@router.get("/{doc_id}/tr2/raw")
@require_permission("perm_document_read")
def get_tr2_raw(doc_id: str, current_user: dict = Depends(get_current_user)):
    """Whatever is still stored for the current body, for inspection and download."""
    _doc(doc_id)
    return _run(doc_id, lambda: tr2.read_raw(doc_id))


@router.post("/{doc_id}/tr2/recovery/restore")
@require_permission("perm_document_update")
def post_tr2_restore(doc_id: str, body: Tr2Restore,
                     current_user: dict = Depends(get_current_user)):
    _writable_doc(doc_id)
    return _run(doc_id, lambda: tr2.restore_revision(
        doc_id, body.revision_no, actor=current_user["user_id"],
        expected_revision=body.expected_revision))


@router.post("/{doc_id}/tr2/recovery/new")
@require_permission("perm_document_update")
def post_tr2_new_proposal(doc_id: str, body: Tr2NewProposal,
                          current_user: dict = Depends(get_current_user)):
    _writable_doc(doc_id)
    return _run(doc_id, lambda: tr2.start_new(
        doc_id, actor=current_user["user_id"], expected_revision=body.expected_revision))
