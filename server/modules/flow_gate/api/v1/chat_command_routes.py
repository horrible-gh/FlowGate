"""Chat command execution and run change summaries (flowgate.default.0670 T0004).

Worker side (Bearer = the live chat run's own AI token, CLI providers):

    POST /api/v1/chat-commands                     create a request, long-poll its result
    GET  /api/v1/chat-commands/{request_id}?wait=  keep waiting for that result

User side (session):

    POST /api/v1/chat-commands/{request_id}/decision   {"decision": "approve"|"reject"|"cancel"}
    GET  /api/v1/chat-activity/{doc_id}                commands + change summaries of one CH
    GET  /api/v1/chat-activity/{doc_id}/runs/{run_id}/diff?path=   one file of one run

API providers never come through here -- their ``run_command`` tool calls the same
``chat_command_service`` in-process. One executor, two transports (NR0003 §4.3).

None of these paths name a group, so the group-mutation lease guard does not stand
between the human approving a command and the very AI run that is holding the lease
while it waits for that approval. Authority is checked here instead: the worker must
present the run's current token, the human must be the user who started the run.
All handlers are plain ``def`` (the store is synchronous; see test_event_loop_blocking).
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.rbac.decorators import _has_permission
from modules.flow_gate.services import chat_command_service as commands
from modules.flow_gate.services import chat_run_changes_service as changes
from modules.flow_gate.services import token_service
from modules.flow_gate.services.git_service import GitServiceError

router = APIRouter(tags=["ChatCommands"])


class CommandBody(BaseModel):
    model_config = ConfigDict(extra="allow")


class DecisionBody(BaseModel):
    decision: str


def _error(status: int, code: str, message: Optional[str] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={
        "ok": False, "error": {"code": code, "message": message or code},
    })


def _worker_run(request: Request) -> tuple[dict, dict]:
    raw = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    try:
        token = token_service.verify(raw)
    except Exception:
        raise commands.ChatCommandError(403, "chat_command_forbidden", "a live AI chat run token is required")
    from modules.flow_gate.services import ai_invoke_service

    run = ai_invoke_service.get_run_record(str(token.get("ai_run_id") or ""))
    commands.authorize_token(token, run)
    return token, run


def _readable_chat_doc(doc_id: str, user: dict) -> dict:
    doc = db_documents.get_by_id(doc_id)
    if not doc or str(doc.get("type_code") or doc.get("type") or "").upper() != "CH":
        raise commands.ChatCommandError(404, "chat_document_not_found")
    if not _has_permission(user, "perm_document_read", doc.get("project_id")):
        raise commands.ChatCommandError(403, "forbidden")
    return doc


@router.post("/chat-commands")
def create_chat_command(body: CommandBody, request: Request):
    try:
        token, run = _worker_run(request)
        return commands.cli_create(run, token, body.model_dump())
    except commands.ChatCommandError as exc:
        return _error(exc.status, exc.code, exc.message)


@router.get("/chat-commands/{request_id}")
def read_chat_command(request_id: str, request: Request, wait: Optional[float] = Query(default=None)):
    try:
        _token, run = _worker_run(request)
        return commands.cli_read(run, request_id, wait if wait is not None else commands.CLI_WAIT_DEFAULT_SEC)
    except commands.ChatCommandError as exc:
        return _error(exc.status, exc.code, exc.message)


@router.post("/chat-commands/{request_id}/decision")
def decide_chat_command(request_id: str, body: DecisionBody, user=Depends(get_current_user)):
    try:
        row = commands.db.get(request_id)
        if row is None:
            raise commands.ChatCommandError(404, "chat_command_not_found")
        _readable_chat_doc(row["doc_id"], user)
        updated = commands.decide(request_id, user, body.decision)
        return {"ok": True, "request": commands.public(updated)}
    except commands.ChatCommandError as exc:
        return _error(exc.status, exc.code, exc.message)


@router.get("/chat-activity/{doc_id}")
def chat_activity(doc_id: str, user=Depends(get_current_user)) -> Any:
    try:
        _readable_chat_doc(doc_id, user)
        return {"ok": True, "doc_id": doc_id,
                "commands": commands.list_for_doc(doc_id), "changes": changes.list_for_doc(doc_id)}
    except commands.ChatCommandError as exc:
        return _error(exc.status, exc.code, exc.message)


@router.get("/chat-activity/{doc_id}/runs/{run_id}/diff")
def chat_run_file_diff(doc_id: str, run_id: str, path: str = Query(...), user=Depends(get_current_user)):
    try:
        _readable_chat_doc(doc_id, user)
        return changes.file_diff(doc_id, run_id, path)
    except commands.ChatCommandError as exc:
        return _error(exc.status, exc.code, exc.message)
    except GitServiceError as exc:
        return _error(exc.status, exc.code, exc.message)
