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
from pydantic import BaseModel, field_validator, model_validator

from modules.flow_gate.auth.middleware import get_current_user, verify_token
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.services import q_answer_invoke_service, q_service, token_service
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


def _reject_conversation_doc(doc_id: str) -> Optional[JSONResponse]:
    """Reject query registration against a CH (conversation) document — B0001 / NR0004 (group 0261).

    Gate on the TARGET DOCUMENT, not on the credential, because a "chat token" does not exist
    on this side: the invoke path folds "chat" into the edit grant before issuing
    (ai_invoke_routes._TOKEN_SCOPE), and the manual [멘트복사] path asks /token/issue for
    action_scope='edit' outright, so nothing on the token records that it was minted for a
    conversation. What both chat paths do share is the document they are bound to.

    CH has no Q surface at all — DocInfoPanel is switched off for CH (MainPanel.
    canShowDocInfoPanel, TR0044.0010 rev8), so a query registered here is stored and then
    rendered nowhere. That is true regardless of who registered it, which is why this rejects
    a human [+query] as well; that path is unreachable in the UI for exactly the same reason.

    Callers must run this BEFORE resolve_question_anchor() so the check sees the document the
    writer aimed at rather than a re-aimed anchor.
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return None  # not ours to report — _doc_project_or_403 raises the 404 downstream
    if doc.get("type_code") != "CH":
        return None
    return _fail(
        400,
        "This is a conversation (CH) document and has no Q container. You are talking to a "
        "person right now — ask your question directly in your reply turn instead of "
        "registering a query.",
    )


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
    # Plain label strings — ids are always server-assigned (L0008 §2.1/§2.3), so the
    # request surface never takes one. q_service re-validates: it is the final gate for
    # callers that reach the service without passing through this route.
    options: List[str] = []

    @field_validator("body")
    @classmethod
    def body_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("body must not be empty")
        return v

    @field_validator("options")
    @classmethod
    def options_within_limits(cls, v: list) -> list:
        if len(v) > q_service.MAX_OPTIONS:
            raise ValueError(f"options must contain at most {q_service.MAX_OPTIONS} items")
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


class AiAnswerRequest(BaseModel):
    """[Request AI answer] body — optional. Omitted/empty ⇒ the project's provider chain."""
    provider_id: Optional[str] = None


class RegisterAnswerRequest(BaseModel):
    body: str = ""
    author_kind: str = "human"
    selected_option_ids: List[str] = []

    @model_validator(mode="after")
    def body_or_selection_present(self) -> "RegisterAnswerRequest":
        """A blank body is only allowed when an option was picked (L0008 §2.1).

        The field-level non-blank rule this replaces predates options. Picking an option
        without typing anything is now a valid answer — q_service fills body with the
        chosen label before storing, so answers.body is still never blank.
        """
        if (not self.body or not self.body.strip()) and not self.selected_option_ids:
            raise ValueError("body must not be empty")
        return self

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
            selected_option_ids=body.selected_option_ids,
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

    A CH (conversation) document takes no queries at all (B0001 / NR0004) — see
    _reject_conversation_doc.
    """
    auth = _resolve_writer(request, doc_id)
    if isinstance(auth, JSONResponse):
        return auth
    user_id, forced_kind = auth
    ch_rejected = _reject_conversation_doc(doc_id)
    if ch_rejected is not None:
        return ch_rejected
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


# ── Answer hand-off — give one query item to an AI worker ────────────────────────────
# D0005 §3.2 / L0007 §3.4: hand the query to an AI worker and let the answer land back on
# the same item as author_kind='ai' via POST /answers; no separate A document.
#
# Two routes, the same pair the legacy Q-document flow offers via qa_routes `dispatch_mode`:
#   /answers/ai-mention → mint the token, return the mention for the user's own worker.
#   /answers/ai-request → run it in-app through the shared ai_invoke engine.
#
# 0248 B0001: ai-request used to stop at token_service.issue and return the raw token to a
# browser that had nowhere to show it — no worker was launched and no UI surfaced the token,
# so the click returned 200 and did nothing (NR0003). Its response now carries the run handle
# only; the token is injected into the run server-side. ai-mention is the copy path that was
# missing altogether, which left a user with no provider configured no way to get an answer
# except to write it themselves.

def _resolve_dispatch_target(doc_id: str, item_id: int, user_id: str):
    """Shared guard for both hand-off routes: the document exists, its group is live, the
    caller may dispatch, and the item really belongs to this document.

    Returns (doc, item, None) to proceed, else (None, None, error_response). Both routes
    mint a worker token bound to the document, so neither may skip any of these.
    """
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return None, None, _fail(404, f"Document {doc_id} does not exist")
    # TR0079.0003 rework (3rd pass): do not hand out a token for a disposed group — that
    # would let an AI worker answer into the discarded group's Q&A.
    from modules.flow_gate import process_service as _process_service
    if _process_service.is_group_disposed(doc.get("group_id")):
        return None, None, _fail(409, "Modification not allowed: the group has been disposed.")
    project_id = doc.get("project_id")
    if project_id and not has_permission(user_id, project_id, "perm_document_create"):
        return None, None, _fail(
            403, "Insufficient permissions for this operation (perm_document_create required)")
    try:
        item = q_answer_invoke_service.resolve_item(doc_id, item_id)
    except HTTPException as exc:
        return None, None, _fail(exc.status_code, exc.detail)
    return {**doc, "doc_id": doc_id}, item, None


@router.post("/q/{doc_id}/items/{item_id}/answers/ai-mention")
def post_answer_ai_mention(
    doc_id: str,
    item_id: int,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """[멘트 복사] — mint the item-bound token and return the worker mention verbatim.

    The raw token is the point of this response: the user pastes it into their own AI
    session, exactly as /token/issue serves every other copy-mention site. This is the only
    answer path that works when the project has no AI provider configured.
    """
    user_id: str = current_user["user_id"]
    doc, item, denied = _resolve_dispatch_target(doc_id, item_id, user_id)
    if denied is not None:
        return denied

    # Local import mirrors ai_invoke_routes: token_routes pulls in the whole workflow
    # stack, so it is imported at call time rather than at module import.
    from modules.flow_gate.api import token_routes as _token_routes

    try:
        issued = q_answer_invoke_service.issue_answer_token(
            doc=doc,
            item=item,
            issued_to=user_id,
            api_base_url=_token_routes._build_api_base(request),
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(500, f"Failed to issue the answer mention token: {exc}")
    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "item_id": item_id,
        "raw_token": issued.get("raw_token"),
        "token_id": issued.get("token_id"),
        "expires_at": issued.get("expires_at"),
        "scratch_dir": issued.get("scratch_dir"),
        "mention": issued.get("mention"),
    })


@router.post("/q/{doc_id}/items/{item_id}/answers/ai-request")
def post_request_ai_answer(
    doc_id: str,
    item_id: int,
    request: Request,
    body: Optional[AiAnswerRequest] = None,
    current_user: dict = Depends(get_current_user),
):
    user_id: str = current_user["user_id"]
    doc, item, denied = _resolve_dispatch_target(doc_id, item_id, user_id)
    if denied is not None:
        return denied

    # Local import mirrors ai_invoke_routes: token_routes pulls in the whole workflow
    # stack, so it is imported at call time rather than at module import.
    from modules.flow_gate.api import token_routes as _token_routes

    try:
        run = q_answer_invoke_service.dispatch_answer_run(
            doc=doc,
            item=item,
            issued_to=user_id,
            api_base_url=_token_routes._build_api_base(request),
            provider_id=(body.provider_id if body else None),
        )
    except HTTPException as exc:
        # Admission failures (no_enabled_provider / run_in_progress / provider_unavailable)
        # arrive as the ai-invoke error envelope — pass it through unflattened so the UI
        # can branch on `code`, exactly as it does for /ai-invoke/start.
        detail = exc.detail if isinstance(exc.detail, dict) else {
            "code": "ai_dispatch_failed", "message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content={
            "ok": False,
            "http_status": exc.status_code,
            "error_message": detail.get("message") or "Failed to start the AI answer run.",
            "help_url": help_url(),
            "doc_id": doc_id,
            "item_id": item_id,
            **detail,
        })
    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "item_id": item_id,
        "run_id": run.get("run_id"),
        "status": run.get("status"),
        "provider": run.get("provider"),
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
