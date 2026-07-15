"""Q&A T-API endpoints — document-bound query/answer (group 0022 Q/A/V revamp).

Endpoints:
  POST /api/v1/q/{doc_id}/questions             — add query ([+query] / AI registration)
  POST /api/v1/q/{doc_id}/items/{item_id}/answers — register answer (human/AI)
  GET  /api/v1/q/{doc_id}                        — document Q&A tree
  GET  /api/v1/q                                 — 'open queries' aggregate (dashboard)

Q&A is sub-data of a document, so it all follows the parent document's permissions (D0005 §3.2/§4).
Auth:
  Read (get_current_user)  : login session JWT. Read=perm_document_read.
  Write (register query/answer) : login session JWT (human) **or** inbox/edit worker token (AI).
     The AI worker has no login session and holds only an edit-scoped token, so when the token's
     doc_ref matches the path {doc_id}, registration is allowed under perm_document_create for issued_to.
     Without this path the worker cannot leave ambiguities as a Q and ends up guessing (0022/TR0009 rev0 rejection).
"""
from __future__ import annotations

from typing import List, Optional, Tuple, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from modules.flow_gate.auth.middleware import get_current_user, verify_token
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.services import q_service, token_service
from modules.flow_gate.utils.help_url import help_url
from modules.flow_gate.utils.id_validators import (
    validate_doc_id,
    validate_group_id,
    validate_project_id,
)

router = APIRouter(prefix="/api/v1", tags=["QTapi"])


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "http_status": status,
            "error_message": message,
            "help_url": help_url(),
        },
    )


def _compose_doc_id(project_id: str, module: str, group_seq: str, doc_code: str) -> str:
    """Slash path segments → canonical doc_id."""
    group_id = f"{project_id}.{module}.{group_seq}"
    doc_id = f"{group_id}.{doc_code}"
    validate_project_id(project_id)
    validate_group_id(group_id)
    validate_doc_id(doc_id)
    return doc_id


def _doc_project_or_403(
    doc_id: str, user_id: str, perm: str, reject_disposed: bool = False
) -> Union[str, JSONResponse]:
    """Resolve the parent document's project and enforce `perm`. Returns project_id or a 403/404.

    When ``reject_disposed`` is set (write paths: register query/answer), a document whose group
    has been disposed (DC) is rejected with 409 — TR0079.0003 rework (3rd pass). Read paths
    leave it False so the disposed group's Q&A stays viewable. Shares process_service.
    is_group_disposed with the document-router / inbox / workflow guards (single signal,
    fail-open for live groups).
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist")
    if reject_disposed:
        from modules.flow_gate import process_service as _process_service
        if _process_service.is_group_disposed(doc.get("group_id")):
            return _fail(409, "Modification not allowed: the group has been disposed.")
    project_id = doc.get("project_id")
    if project_id and not has_permission(user_id, project_id, perm):
        return _fail(403, f"Insufficient permissions for this operation ({perm} required)")
    return project_id or ""


def _extract_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


def _resolve_writer(request: Request, doc_id: str) -> Union[Tuple[str, Optional[str]], JSONResponse]:
    """Dual auth for register-query/answer endpoints.

    An AI worker holds an inbox/edit token (issued by [Request AI answer] or the inbox flow),
    *not* a login session — so verifying only the OAuth2 session JWT returned 401 and forced
    the worker to guess instead of registering a Q (0022/TR0009 rev0 rejection). We therefore accept
    either credential:
      1) inbox/edit worker token → token_service.verify, doc_ref must bind to {doc_id};
         the writer is the token's issued_to and the kind is forced to 'ai'.
      2) login session JWT (human [+query]) → get_current_user; kind stays as requested.
    Parent-document perm_document_create is enforced downstream by _doc_project_or_403 against
    the resolved writer (issued_to for tokens, user_id for sessions).

    Returns (writer_user_id, forced_kind) where forced_kind='ai' for worker tokens and None for
    sessions, or a JSONResponse on auth failure (caller must return it immediately).
    """
    raw = _extract_bearer(request)
    if raw is None:
        return _fail(401, "Authorization header is required")

    # 1) inbox/edit worker token (what an AI worker actually holds)
    try:
        token_rec = token_service.verify(raw)
    except HTTPException:
        token_rec = None
    if token_rec is not None:
        # Context binding: an edit token may only register against its own document.
        if token_rec.get("doc_ref") not in (None, "", doc_id):
            return _fail(403, "Context binding mismatch. Use the correct token.")
        return token_rec["issued_to"], "ai"

    # 2) login session JWT (human via [+query])
    try:
        user = get_current_user(verify_token(raw))
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)
    return user["user_id"], None


# ── Request schemas ──────────────────────────────────────────────────────────

class QuestionItemIn(BaseModel):
    title: Optional[str] = None
    body: str

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("body must not be empty")
        return v


class AddQuestionsRequest(BaseModel):
    questions: List[QuestionItemIn]
    asker_kind: str = "human"

    @field_validator("questions")
    @classmethod
    def questions_not_empty(cls, v: list) -> list:
        if not v:
            raise ValueError("questions must contain at least one item")
        return v

    @field_validator("asker_kind")
    @classmethod
    def valid_asker_kind(cls, v: str) -> str:
        if v not in ("human", "ai"):
            raise ValueError("asker_kind must be 'human' or 'ai'")
        return v


class RegisterAnswerRequest(BaseModel):
    body: str
    author_kind: str = "human"

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("body must not be empty")
        return v

    @field_validator("author_kind")
    @classmethod
    def valid_author_kind(cls, v: str) -> str:
        if v not in ("human", "ai"):
            raise ValueError("author_kind must be 'human' or 'ai'")
        return v


# ── shared handlers ───────────────────────────────────────────────────────────

def _add_questions_response(doc_id: str, body: AddQuestionsRequest, user_id: str) -> JSONResponse:
    project_id = _doc_project_or_403(doc_id, user_id, "perm_document_create", reject_disposed=True)
    if isinstance(project_id, JSONResponse):
        return project_id
    try:
        result = q_service.add_questions(
            doc_id=doc_id,
            questions=[q.model_dump() for q in body.questions],
            asker_kind=body.asker_kind,
            # For human queries the user is the container owner; for AI queries the service uses 'u-system'
            created_by=user_id if body.asker_kind == "human" else None,
            project_id=project_id or None,
            notify_audience=user_id if body.asker_kind == "ai" else None,
        )
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)
    return JSONResponse(content={"ok": True, **result})


def _register_answer_response(
    doc_id: str, item_id: int, body: RegisterAnswerRequest, user_id: str
) -> JSONResponse:
    project_id = _doc_project_or_403(doc_id, user_id, "perm_document_create", reject_disposed=True)
    if isinstance(project_id, JSONResponse):
        return project_id
    try:
        result = q_service.register_answer(
            doc_id=doc_id,
            item_id=item_id,
            body=body.body,
            author_kind=body.author_kind,
            author_id=user_id if body.author_kind == "human" else None,
        )
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)
    return JSONResponse(content={"ok": True, **result})


def _detail_response(doc_id: str, user_id: str) -> JSONResponse:
    project_id = _doc_project_or_403(doc_id, user_id, "perm_document_read")
    if isinstance(project_id, JSONResponse):
        return project_id
    return JSONResponse(content={"ok": True, "qa": q_service.get_qa_detail(doc_id)})


# ── POST /q/{doc_id}/questions — add query ──────────────────────────────────────

@router.post("/q/{doc_id}/questions")
def post_add_questions(
    doc_id: str,
    body: AddQuestionsRequest,
    request: Request,
):
    """Add N queries to a document (lazily creates the container if missing).

    asker_kind='human' ([+query], login session) or 'ai' (AI worker registers ambiguities
    as document queries, §4). The AI worker has no login session, so it calls with an
    inbox/edit token, in which case asker_kind is forced to 'ai'.
    """
    auth = _resolve_writer(request, doc_id)
    if isinstance(auth, JSONResponse):
        return auth
    user_id, forced_kind = auth
    if forced_kind is not None:
        body.asker_kind = forced_kind
        # B0001 / NR0003 (group 0059): the worker token is bound to the workflow spine
        # (the sequence-owning R/B), so a clarifying question would otherwise land on
        # that far-upstream anchor where the console user never looks. Re-aim it at the
        # current work-context document (in-progress report doc, else its predecessor
        # instruction doc). Human [+query] (forced_kind is None) registers where it clicks.
        doc_id = q_service.resolve_question_anchor(doc_id)
    return _add_questions_response(doc_id, body, user_id)


# ── POST /q/{doc_id}/items/{item_id}/answers — register answer ────────────────────────

@router.post("/q/{project_id}/{module}/{group_seq}/{doc_code}/items/{item_id}/answers")
def post_register_answer_by_path(
    project_id: str,
    module: str,
    group_seq: str,
    doc_code: str,
    item_id: int,
    body: RegisterAnswerRequest,
    request: Request,
):
    """Register answer via slash-path doc ID."""
    try:
        doc_id = _compose_doc_id(project_id, module, group_seq, doc_code)
    except ValueError as exc:
        return _fail(422, str(exc))
    auth = _resolve_writer(request, doc_id)
    if isinstance(auth, JSONResponse):
        return auth
    user_id, forced_kind = auth
    if forced_kind is not None:
        body.author_kind = forced_kind
    return _register_answer_response(doc_id, item_id, body, user_id)


@router.post("/q/{doc_id}/items/{item_id}/answers")
def post_register_answer(
    doc_id: str,
    item_id: int,
    body: RegisterAnswerRequest,
    request: Request,
):
    """Register an answer to a query item (human=author_kind 'human', AI='ai').

    AI answers are called with the edit token issued by [Request AI answer], and author_kind is forced to 'ai'.
    """
    auth = _resolve_writer(request, doc_id)
    if isinstance(auth, JSONResponse):
        return auth
    user_id, forced_kind = auth
    if forced_kind is not None:
        body.author_kind = forced_kind
    return _register_answer_response(doc_id, item_id, body, user_id)


# ── POST /q/{doc_id}/items/{item_id}/answers/ai-request — [Request AI answer] ───────
# D0005 §3.2 / L0007 §3.4: issue an edit-scoped token to hand the query off to the AI worker.
# The worker posts the answer with that token via POST /answers (author_kind='ai'); no separate A document.

@router.post("/q/{doc_id}/items/{item_id}/answers/ai-request")
def post_request_ai_answer(
    doc_id: str,
    item_id: int,
    current_user: dict = Depends(get_current_user),
):
    user_id: str = current_user["user_id"]
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist")
    # TR0079.0003 rework (3rd pass): do not hand out an edit-scoped token for a disposed
    # group — that would let an AI worker answer into the discarded group's Q&A.
    from modules.flow_gate import process_service as _process_service
    if _process_service.is_group_disposed(doc.get("group_id")):
        return _fail(409, "Modification not allowed: the group has been disposed.")
    project_id = doc.get("project_id")
    if project_id and not has_permission(user_id, project_id, "perm_document_create"):
        return _fail(403, "Insufficient permissions for this operation (perm_document_create required)")
    # Verify the item belongs to this document's container
    container = q_service.get_qa_detail(doc_id)
    if not any(it.get("id") == item_id for it in container.get("items", [])):
        return _fail(404, f"question_item {item_id} does not belong to document {doc_id}")
    try:
        issued = token_service.issue(
            project=project_id or "",
            group_id=doc.get("group_id"),
            action_scope="edit",
            doc_ref=doc_id,
            issued_to=user_id,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(500, f"Failed to issue AI dispatch token: {exc}")
    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "item_id": item_id,
        "raw_token": issued.get("raw_token"),
        "token_id": issued.get("token_id"),
        "expires_at": issued.get("expires_at"),
        "scratch_dir": issued.get("scratch_dir"),
    })


# ── GET /q — 'open queries' aggregate (dashboard, D0005 §3.7) ────────────────────────────

@router.get("/q")
def get_open_queries(
    project_id: Optional[str] = Query(None, description="Project ID (omit = all projects)"),
    current_user: dict = Depends(get_current_user),
):
    """List of queries awaiting an answer (parent document ID + Q number + title)."""
    user_id: str = current_user["user_id"]
    if project_id and not has_permission(user_id, project_id, "perm_document_read"):
        return _fail(403, "Insufficient permissions for this operation (perm_document_read required)")
    items = q_service.list_open_items(project_id=project_id)
    return JSONResponse(content={"ok": True, "items": items})


# ── GET /q/{doc_id} — document Q&A tree ────────────────────────────────────────

@router.get("/q/{project_id}/{module}/{group_seq}/{doc_code}")
def get_qa_detail_by_path(
    project_id: str,
    module: str,
    group_seq: str,
    doc_code: str,
    current_user: dict = Depends(get_current_user),
):
    """Fetch QA detail via slash-path doc ID."""
    try:
        doc_id = _compose_doc_id(project_id, module, group_seq, doc_code)
    except ValueError as exc:
        return _fail(422, str(exc))
    return _detail_response(doc_id, current_user["user_id"])


@router.get("/q/{doc_id}")
def get_qa_detail(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Document's Q&A tree (empty items if no container exists)."""
    return _detail_response(doc_id, current_user["user_id"])
