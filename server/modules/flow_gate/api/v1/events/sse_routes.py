"""SSE UI push routes (D021 §5).

GET /api/v1/events/stream
Authentication: JWT via query param ?token=<jwt> or Authorization Bearer header
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from modules.flow_gate.auth.jwt_service import decode_token
from modules.flow_gate.auth.token_store import is_blacklisted
from modules.flow_gate.api.v1.events.publisher import subscribe, unsubscribe
import LogAssist.log as logger

router = APIRouter(prefix="/api/v1", tags=["SSE"])


def _extract_jwt(request: Request, token_param: Optional[str]) -> Optional[str]:
    """Extract JWT: prefer query param, then Authorization header."""
    if token_param:
        return token_param
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


def _authenticate(jwt_token: str) -> Optional[str]:
    """Validate JWT and return user_id. Return None on failure."""
    try:
        payload = decode_token(jwt_token)
    except Exception:
        return None
    if payload.get("type") != "access":
        return None
    if payload.get("totp_pending"):
        return None
    jti = payload.get("jti")
    if jti:
        try:
            if is_blacklisted(jti):
                return None
        except Exception:
            return None
    return payload.get("sub")


@router.get("/events/stream")
async def sse_stream(request: Request, token: Optional[str] = None):
    """SSE UI push stream."""
    jwt_token = _extract_jwt(request, token)
    if jwt_token is None:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "http_status": 401,
                     "error_message": "Authentication required",
                     "help_url": "https://example.com/api/v1/help"}
        )

    user_id = _authenticate(jwt_token)
    if user_id is None:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "http_status": 401,
                     "error_message": "Session is invalid",
                     "help_url": "https://example.com/api/v1/help"}
        )

    q = await subscribe(user_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    event_id = (
                        f"sse_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                        f"_{uuid.uuid4().hex[:6]}"
                    )
                    event_name = (
                        event.event_type.value
                        if hasattr(event.event_type, 'value')
                        else event.event_type
                    )
                    data = json.dumps({
                        "event_type": event_name,
                        "payload": event.payload,
                        "timestamp": event.timestamp,
                        "project": event.project,
                        "group_id": event.group_id,
                        "doc_id": event.doc_id,
                    }, ensure_ascii=False)
                    yield f"id: {event_id}\nevent: {event_name}\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # Client-observable heartbeat. A bare `: comment` keeps the socket
                    # warm but is swallowed by the browser EventSource (no JS event
                    # fires), so the client cannot use it to detect a silently-dead
                    # connection (proxy idle-timeout / sleep-resume / network switch
                    # leave a zombie stream that never emits `error`). Emitting a real
                    # named `ping` event lets the client run a liveness watchdog and
                    # force-reconnect such streams. (R0001 / group 0025 TR)
                    ping_id = (
                        f"sse_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
                        f"_{uuid.uuid4().hex[:6]}"
                    )
                    yield f"id: {ping_id}\nevent: ping\ndata: {{}}\n\n"
        except (GeneratorExit, asyncio.CancelledError):
            pass
        finally:
            await unsubscribe(user_id, q)
            logger.debug(f"[SSE] unsubscribed user={user_id}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

