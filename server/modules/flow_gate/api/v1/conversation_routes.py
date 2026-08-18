"""Token-authenticated worker adapter for appending one conversation turn."""
from __future__ import annotations

from typing import Optional

import anyio.to_thread
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.services import (
    conversation_query_service,
    conversation_turn_service,
    token_service,
)
from modules.flow_gate.utils.help_url import help_url

router = APIRouter(prefix="/api/v1/conversation", tags=["Conversation"])


class WorkerConversationTurnAppend(BaseModel):
    body: str
    idempotency_key: str
    based_on_seq: Optional[int] = None
    display_name: Optional[str] = None
    dry_run: bool = False
    # 0391 T0005 §7-4: optional body fingerprint (proposal 4) + bypass door (proposal 3 §5-6),
    # same field names as the inbox submit paths.
    body_sha256: Optional[str] = None
    body_chars: Optional[int] = None
    force_encoding_reason: Optional[str] = None


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


def _bearer(request: Request) -> Optional[str]:
    value = request.headers.get("Authorization", "")
    return value[7:] if value.startswith("Bearer ") and len(value) > 7 else None


def _authenticate(doc_id: str, raw_token: str) -> tuple[Optional[dict], Optional[JSONResponse]]:
    """Resolve a chat-scoped worker token bound to this exact conversation.

    ``inspect_for_replay`` intentionally does NOT reject an already-consumed token:
    reads never consume one at all (P0003 §0-1), and on the append path the
    idempotency lookup has to run before the single-use rule so a legitimate retry
    replays instead of 401ing (P0003 scenario 11).
    """
    try:
        token = token_service.inspect_for_replay(raw_token)
    except Exception as exc:
        status = getattr(exc, "status_code", 401)
        return None, _fail(status, str(getattr(exc, "detail", exc)))

    if token.get("action_scope") != "chat" or token.get("doc_ref") != doc_id:
        return None, _fail(403, "Context binding mismatch. Use the correct token.")
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return None, _fail(404, f"Document not found: {doc_id}")
    if token.get("project") != doc.get("project_id") or token.get("group_id") != doc.get("group_id"):
        return None, _fail(403, "Context binding mismatch. Use the correct token.")
    return token, None


def _list_authenticated(
    doc_id: str, raw_token: str, after_seq: Optional[int], before_seq: Optional[int],
    limit: Optional[int], include_head: bool,
) -> JSONResponse:
    token, failure = _authenticate(doc_id, raw_token)
    if failure is not None:
        return failure
    try:
        result = conversation_query_service.list_turns(
            doc_id=doc_id,
            actor={"kind": "worker", "token": token},
            after_seq=after_seq,
            before_seq=before_seq,
            limit=limit,
            include_head=include_head,
        )
    except conversation_turn_service.ConversationTurnError as exc:
        return _fail(exc.status_code, exc.message)
    # Reading does not consume the token — a worker must be able to page through a
    # long conversation before it has anything to say.
    return JSONResponse(status_code=200, content=result)


def _append_authenticated(
    doc_id: str, body: WorkerConversationTurnAppend, raw_token: str
) -> JSONResponse:
    token, failure = _authenticate(doc_id, raw_token)
    if failure is not None:
        return failure
    if body.idempotency_key.strip() != token.get("token_id"):
        return _fail(422, "idempotency_key must equal the token id.")

    actor = {"kind": "worker", "token": token}
    # T0009 task 4: the encoding/fingerprint violation messages route through the
    # worker's own continuation_locale (same field mention_service already reads),
    # unchanged default "ko" when absent — mirrors the pattern remote_tool_service uses.
    from modules.flow_gate import template_provision
    locale = template_provision.normalize_locale(token.get("continuation_locale"))
    if body.dry_run:
        # T0004: validate-only path, checked before the single-use/replay branch so a
        # dry-run never depends on (or inspects) whether the token was already consumed.
        try:
            result = conversation_turn_service.dry_run_append(
                doc_id=doc_id,
                actor=actor,
                body_raw=body.body,
                idempotency_key=body.idempotency_key,
                token_rec=token,
                body_sha256=body.body_sha256,
                body_chars=body.body_chars,
                force_encoding_reason=body.force_encoding_reason,
                locale=locale,
            )
        except conversation_turn_service.ConversationTurnError as exc:
            return _fail(exc.status_code, exc.message)
        return JSONResponse(status_code=200, content=result)

    try:
        if token.get("consumed_at"):
            replay = conversation_turn_service.replay_turn(
                doc_id=doc_id,
                actor=actor,
                body_raw=body.body,
                idempotency_key=body.idempotency_key,
                display_name_hint=body.display_name,
            )
            if replay is None:
                return _fail(401, "Token has already been used")
            replay["message"] = f"Turn {replay['turn']['seq']} already recorded. You may end the session."
            return JSONResponse(status_code=200, content=replay)

        result = conversation_turn_service.append_turn(
            doc_id=doc_id,
            actor=actor,
            body_raw=body.body,
            idempotency_key=body.idempotency_key,
            based_on_seq=body.based_on_seq,
            display_name_hint=body.display_name,
            body_sha256=body.body_sha256,
            body_chars=body.body_chars,
            force_encoding_reason=body.force_encoding_reason,
            locale=locale,
        )
        result["message"] = (
            f"Turn {result['turn']['seq']} already recorded. You may end the session."
            if result["replayed"]
            else f"Turn {result['turn']['seq']} appended. You may end the session."
        )
        return JSONResponse(status_code=200 if result["replayed"] else 201, content=result)
    except conversation_turn_service.ConversationTurnError as exc:
        return _fail(exc.status_code, exc.message)


@router.get("/{doc_id}/turns")
async def list_worker_conversation_turns(
    doc_id: str,
    request: Request,
    after_seq: Optional[int] = Query(default=None),
    before_seq: Optional[int] = Query(default=None),
    limit: Optional[int] = Query(default=None),
    include_head: Optional[int] = Query(default=None),
):
    """Hand a worker the range it has not read yet (P0003 scenarios 9 and 13).

    The server advances this worker's cursor to the last turn actually included in the
    response, so the next call continues rather than repeating — the worker is never
    asked to compute where it left off (D0002 §3-4).
    """
    raw_token = _bearer(request)
    if raw_token is None:
        return _fail(401, "Authorization header is required")
    # Every DB touch below is synchronous, so it runs off the event loop.
    return await anyio.to_thread.run_sync(
        _list_authenticated, doc_id, raw_token, after_seq, before_seq, limit,
        bool(include_head),
    )


@router.post("/{doc_id}/turn", status_code=201)
async def append_worker_conversation_turn(doc_id: str, request: Request):
    raw_token = _bearer(request)
    if raw_token is None:
        return _fail(401, "Authorization header is required")
    try:
        payload = await request.json()
    except Exception:
        return _fail(400, "Request body is not valid JSON")
    try:
        body = WorkerConversationTurnAppend.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(part) for part in first.get("loc", ()))
        message = f"{field}: {first.get('msg', 'invalid request')}" if field else first.get("msg", "invalid request")
        return _fail(422, message)
    return await anyio.to_thread.run_sync(_append_authenticated, doc_id, body, raw_token)