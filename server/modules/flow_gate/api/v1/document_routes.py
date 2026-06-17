"""Single-document retrieval endpoints (D021 §4-2, §4-3).

GET /api/v1/document/{id}
GET /api/v1/document/{id}/path
GET /api/v1/document/{project}/branches/{branch}/{module}/{group}/{doc}  ← T247 path-style
"""
from __future__ import annotations

import json
import re as _re
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import document_reviews as db_reviews
from modules.flow_gate.storage.paths import resolve_storage_path
from modules.flow_gate.services.auth_outbound import verify_bearer
from modules.flow_gate.services.q_service import get_answers_for_document
from modules.flow_gate.utils.id_validators import (
    validate_project_id,
    validate_group_id,
    validate_doc_id,
)
import LogAssist.log as logger

router = APIRouter(prefix="/api/v1", tags=["OutboundDocument"])

_HELP_URL = "https://example.com/api/v1/help"


def _parse_rejection_history(raw: Any) -> list:
    """Convert DB rejection_history JSON string to a Python list. Returns an empty list on parse failure."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"ok": False, "http_status": status,
                 "error_message": message, "help_url": _HELP_URL},
    )


def _shape_review(row: dict) -> dict:
    raw_findings = row.get("findings")
    findings: list = []
    if isinstance(raw_findings, str):
        try:
            parsed = json.loads(raw_findings)
            if isinstance(parsed, list):
                findings = parsed
        except (json.JSONDecodeError, TypeError):
            findings = []
    elif isinstance(raw_findings, list):
        findings = raw_findings
    return {
        "id": row.get("id"),
        "revision_no": row.get("revision_no"),
        "reviewer_id": row.get("reviewer_id"),
        "verdict": row.get("verdict"),
        "finding_count": len(findings),
        "findings": findings,
        "comment": row.get("comment"),
        "reviewed_at": row.get("reviewed_at"),
        "created_at": row.get("created_at"),
    }


def _load_reviews(doc_id: str) -> tuple[Optional[dict], list[dict]]:
    try:
        rows = db_reviews.list_by_doc(doc_id)
    except Exception:
        return None, []
    history = [_shape_review(row) for row in rows]
    return (history[0] if history else None), history


_LEGACY_PROJECT_RE = _re.compile(r"^[a-z0-9_\-\u3131-\u318E\uAC00-\uD7A3]+$")
_LEGACY_GROUP_SEQ_RE = _re.compile(r"^\d{4}$")
_LEGACY_DOC_CODE_RE = _re.compile(r"^[A-Z]+\d{4}$")
_LEGACY_GROUP_ID_RE = _re.compile(r"^[a-z0-9_\-\u3131-\u318E\uAC00-\uD7A3]+-__ALL__-\d{4}$")
_LEGACY_DOC_ID_RE = _re.compile(r"^[a-z0-9_\-\u3131-\u318E\uAC00-\uD7A3]+-__ALL__-\d{4}-[A-Z]+\d{4}$")


def _validate_outbound_project_id(project: str) -> None:
    try:
        validate_project_id(project)
    except ValueError:
        if not _LEGACY_PROJECT_RE.fullmatch(project):
            raise ValueError(f"project_id format is invalid: {project!r}")


def _validate_outbound_doc_id(doc_id: str) -> None:
    if _LEGACY_DOC_ID_RE.fullmatch(doc_id):
        return
    validate_doc_id(doc_id)


def _compose_group_doc_ids(project: str, module: str, group: str, doc: str) -> tuple[str, str]:
    _validate_outbound_project_id(project)

    if _LEGACY_GROUP_ID_RE.fullmatch(group):
        group_id = group
    else:
        if not _LEGACY_GROUP_SEQ_RE.fullmatch(group):
            raise ValueError(f"group_id format is invalid: {group!r}")
        group_id = f"{project}-{module}-{group}"

    if _LEGACY_DOC_ID_RE.fullmatch(doc):
        doc_id = doc
        if not doc_id.startswith(f"{group_id}-"):
            raise ValueError(f"doc_id format is invalid: {doc!r}")
    else:
        if not _LEGACY_DOC_CODE_RE.fullmatch(doc):
            raise ValueError(f"doc_id format is invalid: {doc!r}")
        doc_id = f"{group_id}-{doc}"

    return group_id, doc_id


# _fallback_file_path was removed in L0054.0002 §4 — the branch-segment-drift
# fallback is now absorbed into storage.paths.resolve_storage_path().


@router.get("/document")
def get_document_rpc(doc_id: str = Query(...), request: Request = None):
    return get_document(request, doc_id)


@router.get("/document/path")
def get_document_path_rpc(doc_id: str = Query(...), request: Request = None):
    return get_document_path(request, doc_id)


# T247/T564 — path-style endpoint (including branch). Registered before the 1-segment {doc_id} endpoint.
@router.get("/document/{project}/branches/{branch}/{module}/{group}/{doc}")
def get_document_by_path(
    request: Request, project: str, branch: str, module: str, group: str, doc: str
):
    """Path-style document retrieval (T247/T564): /{project}/branches/{branch}/{module}/{group}/{doc}."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    if module != "__ALL__":
        return _fail(400, f"module currently only supports __ALL__ (input: {module})")

    try:
        _, doc_canonical = _compose_group_doc_ids(project, module, group, doc)
    except ValueError as exc:
        return _fail(422, str(exc))

    document = db_docs.get_by_id(doc_canonical)
    if document is None:
        return _fail(404, f"document not found: {doc_canonical}")
    doc_id = document["doc_id"]

    content: str | None = None
    file_path = document.get("file_path")
    if file_path:
        branch_val = document.get("branch", "main") or "main"
        resolved = resolve_storage_path(file_path, document.get("project_id"), branch=branch_val)
        if resolved is not None:
            try:
                content = resolved.read_text(encoding="utf-8")
            except OSError as e:
                logger.debug(f"[document/{doc_id}] failed to read file: {e}")
                return _fail(500, "An error occurred while reading the document content")

    resp: dict = {
        "ok": True,
        "doc_id": doc_id,
        "type": document.get("type_code"),
        "title": document.get("title"),
        "status": document.get("status"),
        "revision_no": document.get("revision_no", 0),
        "owner_id": document.get("owner_id"),
        "triggered_by": document.get("triggered_by"),
        "group_id": document.get("group_id"),
        "project": document.get("project_id"),
        "branch": document.get("branch", branch) or branch or "main",
        "module": document.get("module"),
        "stored_path": file_path,
        "content": content,
        "doc_review_status": document.get("doc_review_status"),
        "rejection_reason": document.get("rejection_reason"),
        "rejection_history": _parse_rejection_history(document.get("rejection_history")),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
    }
    # group 0022: Q&A is sub-data of every document. Attach the answers key only to
    # documents that have a container (query) — Q doc-type gating removed (Q type retired).
    qa_pairs = get_answers_for_document(doc_id)
    if qa_pairs:
        resp["answers"] = qa_pairs
    return JSONResponse(content=resp)


# T565/R2: backward-compatible endpoint — supports pre-T564 URL (branch='main' fixed)
@router.get("/document/{project}/{module}/{group}/{doc}")
def get_document_by_path_legacy(
    request: Request, project: str, module: str, group: str, doc: str
):
    """T565/R2: Backward compatibility for old path-style URLs (branch='main' fixed)."""
    return get_document_by_path(request, project, "main", module, group, doc)


@router.get("/document/{doc_id}/path")
def get_document_path(request: Request, doc_id: str):
    """Document file path lookup (D021 §4-3)."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        _validate_outbound_doc_id(doc_id)
    except ValueError as exc:
        return _fail(422, str(exc))

    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist")

    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "stored_path": doc.get("file_path"),
        "branch": doc.get("branch", "main"),
    })


@router.get("/document/{doc_id}/reviews")
def get_document_reviews(request: Request, doc_id: str):
    """Retrieve structured AI review history for a document."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        _validate_outbound_doc_id(doc_id)
    except ValueError as exc:
        return _fail(422, str(exc))
    if db_docs.get_by_id(doc_id) is None:
        return _fail(404, f"Document {doc_id} does not exist")

    latest, history = _load_reviews(doc_id)
    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "ai_review": latest,
        "ai_review_history": history,
    })


@router.get("/document/{doc_id}")
def get_document(request: Request, doc_id: str):
    """Retrieve document content and metadata (D021 §4-2)."""
    auth = verify_bearer(request)
    if isinstance(auth, JSONResponse):
        return auth

    try:
        _validate_outbound_doc_id(doc_id)
    except ValueError as exc:
        return _fail(422, str(exc))

    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist")

    content: str | None = None
    file_path = doc.get("file_path")
    if file_path:
        branch_val = doc.get("branch", "main") or "main"
        resolved = resolve_storage_path(file_path, doc.get("project_id"), branch=branch_val)
        if resolved is not None:
            try:
                content = resolved.read_text(encoding="utf-8")
            except OSError as e:
                logger.debug(f"[document/{doc_id}] failed to read file: {e}")
                return _fail(500, "An error occurred while reading the document content")

    resp: dict = {
        "ok": True,
        "doc_id": doc_id,
        "type": doc.get("type_code"),
        "title": doc.get("title"),
        "status": doc.get("status"),
        "revision_no": doc.get("revision_no", 0),
        "owner_id": doc.get("owner_id"),
        "triggered_by": doc.get("triggered_by"),
        "group_id": doc.get("group_id"),
        "project": doc.get("project_id"),
        "module": doc.get("module"),
        "branch": doc.get("branch", "main"),
        "stored_path": file_path,
        "content": content,
        "doc_review_status": doc.get("doc_review_status"),
        "rejection_reason": doc.get("rejection_reason"),
        "rejection_history": _parse_rejection_history(doc.get("rejection_history")),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }
    qa_pairs = get_answers_for_document(doc_id)
    if qa_pairs:
        resp["answers"] = qa_pairs
    resp["ai_review"], resp["ai_review_history"] = _load_reviews(doc_id)
    return JSONResponse(content=resp)
