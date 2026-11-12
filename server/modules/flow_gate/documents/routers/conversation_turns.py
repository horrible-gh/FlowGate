"""Session-authenticated conversation turn adapter (append + cursor reads)."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import conversation_turns as turn_store
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.documents import document_service
from modules.flow_gate.services import (
    chat_doc_settings_service,
    conversation_markdown_service,
    conversation_query_service,
    conversation_turn_service,
)

try:
    from rbac.decorators import require_permission  # type: ignore[import]
except ImportError:
    def require_permission(_permission: str):
        def decorator(func):
            return func
        return decorator

router = APIRouter(prefix="/documents", tags=["Documents"])


class ConversationTurnAppend(BaseModel):
    body: str
    idempotency_key: str
    based_on_seq: Optional[int] = None
    display_name: Optional[str] = None
    # Accepted only for backward wire compatibility.  The session boundary always
    # resolves the actor to a user and never forwards this claim to the service.
    speaker: Literal["user", "ai"] = "user"
    # 0391 T0005 §7-5: escape hatch for a human whose message is falsely flagged by
    # the corrupted-body guard (§5-4). Fingerprints are not exposed here — a browser
    # cannot reliably compute a UTF-8 byte hash of its own textarea the way a file-based
    # worker submission can (§6-4), so only the bypass, not the fingerprint fields.
    force_encoding_reason: Optional[str] = None


class ConversationReadMark(BaseModel):
    last_read_seq: int
    # P0003 scenario 5 carries both values on the wire; a session screen only ever
    # reports what it actually showed, so "viewed" is the default.
    reason: Literal["viewed", "delivered"] = "viewed"


def _bearer(request: Request) -> Optional[str]:
    value = request.headers.get("Authorization", "")
    return value[7:] if value.startswith("Bearer ") and len(value) > 7 else None


def _worker_token_redirect(leaf: str, method: str):
    """Answer a worker token sent to this session route with the worker route to use.

    flowgate.v02.0157: the plural ``/documents/{doc_id}/conversation/*`` routes accept only a
    signed-in session JWT, so a worker's chat token sent here used to come back as the JWT
    decoder's generic 401 ``Invalid authentication credentials``. That reads as "the token is
    bad", while the token was fine and only the route was wrong -- the worker then reports an
    authentication failure instead of reading the conversation at
    ``/conversation/{doc_id}/turns``. This check keeps the session boundary exactly as it was
    (still 401, still no worker access here) and only names the right route.

    It runs before ``get_current_user`` (route-level dependency) and stays out of every session
    request's way: no bearer, or a JWT-shaped one (three dot-separated parts), returns at once
    and the session check decides as before. A non-JWT bearer that is not a known worker token
    also falls through, so a garbage value keeps the generic 401.
    """

    def _check(request: Request, doc_id: str) -> None:
        raw = _bearer(request)
        if raw is None or raw.count(".") == 2:
            return
        try:
            from modules.flow_gate.services import token_service

            known = token_service._find_token_by_raw(raw) is not None
        except Exception:  # noqa: BLE001 -- a lookup failure must not change the session answer
            return
        if not known:
            return
        path = request.url.path
        cut = path.rfind("/documents/")
        worker_url = f"{path[:cut] if cut >= 0 else ''}/conversation/{doc_id}/{leaf}"
        raise HTTPException(
            status_code=401,
            detail=(
                "This is the console session API and it does not accept worker tokens. "
                f"Use {method} {worker_url} with a chat token bound to this conversation."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _check


def _session_actor(request: Request, current_user: dict) -> dict:
    locale = (request.headers.get("X-Locale") or "ko").strip().lower().split("-")[0]
    return {
        "kind": "session",
        "user_id": current_user.get("user_id"),
        "user_name": current_user.get("username") or current_user.get("display_name"),
        "locale": locale,
    }


@router.get(
    "/{doc_id}/conversation/turns",
    dependencies=[Depends(_worker_token_redirect("turns", "GET"))],
)
@require_permission("perm_document_read")
def list_conversation_turns(
    doc_id: str,
    request: Request,
    after_seq: Optional[int] = Query(default=None),
    before_seq: Optional[int] = Query(default=None),
    limit: Optional[int] = Query(default=None),
    include_head: Optional[int] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Read one page of this conversation around a cursor (P0003 scenarios 1, 2 and 7).

    Deliberately readable even when the group is disposed or the document is final —
    disposal blocks change, not the record (D0002 §3-6).  A session read never moves
    a cursor; the screen reports what it actually displayed via ``/conversation/read``.
    """
    try:
        return conversation_query_service.list_turns(
            doc_id=doc_id,
            actor=_session_actor(request, current_user),
            after_seq=after_seq,
            before_seq=before_seq,
            limit=limit,
            include_head=bool(include_head),
        )
    except conversation_turn_service.ConversationTurnError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{doc_id}/conversation/read")
@require_permission("perm_document_read")
def mark_conversation_read(
    doc_id: str,
    body: ConversationReadMark,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Move this user's read boundary forward (P0003 scenario 5).

    Backwards values are absorbed silently by the monotonic cursor rather than
    rejected — a scrolled-up screen legitimately sends one.
    """
    try:
        return conversation_query_service.record_read(
            doc_id=doc_id,
            actor=_session_actor(request, current_user),
            last_read_seq=body.last_read_seq,
            reason=body.reason,
        )
    except conversation_turn_service.ConversationTurnError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _conversation_doc(doc_id: str) -> dict:
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    if (doc.get("type_code") or "").upper() not in conversation_turn_service.CONVERSATION_TYPE_CODES:
        raise HTTPException(status_code=400, detail="Not a conversation document.")
    return doc


@router.get("/{doc_id}/conversation/settings")
@require_permission("perm_document_read")
def get_conversation_settings(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    """flowgate.v02test.0025: the caller's settings of this chat (its AI and context checkpoint)."""
    _conversation_doc(doc_id)
    return {
        "ok": True,
        "doc_id": doc_id,
        "settings": chat_doc_settings_service.get_settings(current_user["user_id"], doc_id),
    }


@router.patch("/{doc_id}/conversation/settings")
@require_permission("perm_document_read")
def patch_conversation_settings(
    doc_id: str,
    body: dict = Body(...),
    current_user: dict = Depends(get_current_user),
):
    """Save only the sent fields: ``provider_id`` (null = my default AI), ``context_checkpoint_seq``
    (null = no checkpoint) and ``context_checkpoint_kind`` (``include`` = from this message, ``exclude`` = from the
    next message; defaults to ``include`` with a seq). 422 names the refused field."""
    doc = _conversation_doc(doc_id)
    try:
        settings = chat_doc_settings_service.save_settings(
            current_user["user_id"], doc_id, body, project_id=doc.get("project_id"),
        )
    except chat_doc_settings_service.ChatDocSettingsError as exc:
        return JSONResponse(
            status_code=422,
            content={"ok": False, "code": "validation_failed", "field": exc.field,
                     "reason": exc.reason, "message": exc.message},
        )
    return {"ok": True, "doc_id": doc_id, "settings": settings}


@router.get("/{doc_id}/conversation/markdown")
@require_permission("perm_document_read")
def get_conversation_markdown(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Deterministic markdown artifact for a CH document (P0003 scenario 14, L0004 §4-4).

    Same 404/400 shape as the other conversation routes. A LEGACY (migration ``failed``)
    conversation returns the file verbatim with ``projection: false`` — that file IS the
    record of truth for it. Everything else lazily migrates on first read (same as the
    turn-page route) and returns the rendered markdown with ``projection: true``.
    """
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    if (doc.get("type_code") or "").upper() not in conversation_turn_service.CONVERSATION_TYPE_CODES:
        raise HTTPException(status_code=400, detail="Not a conversation document.")

    state = conversation_query_service._ensure_readable_rows(doc_id)
    if state == "failed":
        path = conversation_turn_service._document_path(doc)
        content = path.read_text(encoding="utf-8") if path and path.is_file() else ""
        return {
            "ok": True,
            "doc_id": doc_id,
            "projection": False,
            "head_seq": turn_store.current_head_seq(doc_id),
            "fingerprint": conversation_markdown_service._fingerprint(content),
            "rendered_at": now_iso(),
            "content": content,
        }

    try:
        rendered = conversation_markdown_service.render_markdown(doc_id)
    except conversation_turn_service.ConversationTurnError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {
        "ok": True,
        "doc_id": doc_id,
        "projection": True,
        "head_seq": rendered["head_seq"],
        "fingerprint": rendered["fingerprint"],
        "rendered_at": rendered["rendered_at"],
        "content": rendered["content"],
    }


@router.post(
    "/{doc_id}/conversation/turn",
    status_code=201,
    dependencies=[Depends(_worker_token_redirect("turn", "POST"))],
)
@require_permission("perm_document_update")
def append_conversation_turn(
    doc_id: str,
    body: ConversationTurnAppend,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    timing = conversation_turn_service.TurnTiming(request.headers.get("X-Conversation-Trace", ""), doc_id)
    timing.mark("received")
    try:
        result = conversation_turn_service.append_turn(
            doc_id=doc_id,
            actor=_session_actor(request, current_user),
            body_raw=body.body,
            idempotency_key=body.idempotency_key,
            based_on_seq=body.based_on_seq,
            display_name_hint=body.display_name,
            force_encoding_reason=body.force_encoding_reason,
            timing=timing,
        )
    except conversation_turn_service.ConversationTurnError as exc:
        timing.mark("response", exc.status_code)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    status = 200 if result["replayed"] else 201
    timing.mark("response", status)
    return JSONResponse(status_code=status, content=result)