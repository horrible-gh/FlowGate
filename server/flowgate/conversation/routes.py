"""HTTP adapter of the Conversation contract (D0006 §5.1). Mounted under ``/api/v1``.

Worker side (Bearer = the live chat run's own current AI token, CLI providers)::

    POST /chat-commands                       create a request, optional long-poll
    GET  /chat-commands/{request_id}?wait=    keep waiting for its result

User side (session)::

    POST /chat-commands/{request_id}/decision          {"decision": "approve"|"reject"|"cancel"}
    GET  /chat-activity/{doc_id}?from_seq=&to_seq=&unplaced=
    GET  /chat-activity/{doc_id}/runs/{run_id}/diff?path=
    POST /conversations                                standalone CH, 201
    POST /conversations/{doc_id}/auto-title            the one AI title of a new chat

API providers never come through here: their ``run_command`` tool calls ``commands``
in-process. Errors use ``{"ok": false, "error": {"code", "message"}}``; authentication
and RBAC errors keep their existing shapes. All handlers are plain ``def`` (the store
is synchronous; see test_event_loop_blocking_0279).

The decision route and the two conversation routes are exempt from the group AI-run lease in
``GroupMutationPolicyMiddleware`` (D0006 §3.10.3); the handlers still check permission
and group state themselves.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from flowgate.conversation import activity
from flowgate.conversation import auto_title
from flowgate.conversation import chats
from flowgate.conversation import commands
from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter
from flowgate.conversation import run_changes

router = APIRouter(tags=["Conversation"])

get_current_user = legacy_adapter.current_user_dependency()


def _error(exc: c.ConversationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content=c.error_body(exc.code, exc.message))


def _worker_run(request: Request) -> tuple[dict, dict]:
    raw = request.headers.get("Authorization", "")
    raw = raw[7:].strip() if raw.startswith("Bearer ") else ""
    if not raw:
        raise c.ConversationError(403, c.ERR_FORBIDDEN, "a live AI chat run token is required")
    try:
        token = legacy_adapter.verify_token(raw)
    except Exception as exc:
        raise c.ConversationError(403, c.ERR_FORBIDDEN, "a live AI chat run token is required") from exc
    run = legacy_adapter.get_run_record(str(token.get("ai_run_id") or ""))
    commands.authorize_token(token, run)
    return token, run


@router.post("/chat-commands")
def create_chat_command(request: Request, body: Optional[dict[str, Any]] = Body(default=None)):
    try:
        token, run = _worker_run(request)
        return commands.cli_create(run, token, body or {})
    except c.ConversationError as exc:
        return _error(exc)


@router.get("/chat-commands/{request_id}")
def read_chat_command(request_id: str, request: Request, wait: Optional[str] = Query(default=None)):
    try:
        _token, run = _worker_run(request)
        return commands.cli_read(run, request_id, wait if wait is not None else c.CLI_WAIT_DEFAULT_SEC)
    except c.ConversationError as exc:
        return _error(exc)


@router.post("/chat-commands/{request_id}/decision")
def decide_chat_command(request_id: str, body: Optional[dict[str, Any]] = Body(default=None),
                        user=Depends(get_current_user)):
    # Only the decision value is accepted: no group/doc identifiers (D0006 §3.2).
    payload = body if isinstance(body, dict) else {}
    decision = payload.get("decision") if set(payload) <= {"decision"} else None
    try:
        updated = commands.decide(request_id, user, decision)
        return {"ok": True, "request": commands.public(updated)}
    except c.ConversationError as exc:
        return _error(exc)


@router.get("/chat-activity/{doc_id}")
def chat_activity(doc_id: str, user=Depends(get_current_user),
                  from_seq: Optional[int] = Query(default=None, ge=0),
                  to_seq: Optional[int] = Query(default=None, ge=0),
                  unplaced: bool = Query(default=True)):
    try:
        activity.readable_chat_document(doc_id, user)
        return activity.list_for_doc(doc_id, from_seq=from_seq, to_seq=to_seq, include_unplaced=unplaced)
    except c.ConversationError as exc:
        return _error(exc)


@router.get("/chat-activity/{doc_id}/runs/{run_id}/diff")
def chat_run_file_diff(doc_id: str, run_id: str, path: str = Query(...), user=Depends(get_current_user)):
    try:
        activity.readable_chat_document(doc_id, user)
        return run_changes.file_diff(doc_id, run_id, path)
    except c.ConversationError as exc:
        return _error(exc)


@router.post("/conversations", status_code=201)
def create_conversation(body: Any = Body(default=None), user=Depends(get_current_user)):
    try:
        return chats.create_chat(user, body)
    except chats.ChatCreateForbidden:
        raise HTTPException(status_code=403, detail="Forbidden")
    except c.ConversationError as exc:
        return _error(exc)


@router.post("/conversations/{doc_id}/auto-title")
def auto_title_conversation(doc_id: str, body: Any = Body(default=None), user=Depends(get_current_user)):
    try:
        return auto_title.generate(doc_id, user, body)
    except c.ConversationError as exc:
        return _error(exc)
