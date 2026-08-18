"""SSE UI push routes (D021 §5).

GET /api/v1/events/stream
Authentication: JWT via query param ?token=<jwt> or Authorization Bearer header

Authentication model (0371 T0012, from NR0007 §4)
-------------------------------------------------
Connect time runs the same checks the ordinary API runs before a handler starts
(auth/middleware.verify_token + get_current_user): decode, ``type == access``,
2FA finished, access token not blacklisted, **session still active**, and the
**user still exists and is active**. The last two were missing here, so a stream
could be opened with a token whose session had been revoked on another device —
or whose account had been deactivated — and every screen push kept flowing.

An open stream is re-checked too, because this loop lives for hours: revoking a
session has to stop the push, not just the next API call. The check runs before
every event frame and at every heartbeat, so the heartbeat cadence bounds the
worst case (~30 s by default). Cutting it finer means wiring a cancel signal
from the revoke path into every subscriber, which NR0007 §4 deliberately left
out of this step.

The re-check does NOT re-decode the JWT. An access token expiring mid-stream is
not a revocation, and ending every stream once per token lifetime would produce
a reconnect + full-resync storm — the client rotates its token on its own
schedule and deliberately keeps healthy streams open across rotations
(client/src/main/composables/useFlowGateSse.ts, group 0028 T0004).

All of these lookups are synchronous DB reads, so they go through
``anyio.to_thread.run_sync``: an ``async def`` handler may not block the event
loop (0279 T0005 guard).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

import anyio
import jwt
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from modules.flow_gate.auth.jwt_service import decode_token
from modules.flow_gate.auth.token_store import is_blacklisted
from modules.flow_gate.auth.session_store import is_session_active
from modules.flow_gate.auth import auth_cache as _auth_cache
from modules.flow_gate.auth import auth_preamble as _auth_preamble
from modules.flow_gate.api.v1.events.publisher import subscribe, unsubscribe
from modules.flow_gate.utils.help_url import help_url
import LogAssist.log as logger

router = APIRouter(prefix="/api/v1", tags=["SSE"])

# Idle heartbeat cadence (seconds). Module constant so tests can shrink it to
# force the idle path without a real 30s wait. It doubles as the upper bound on
# how long an idle stream can outlive its session (see the module docstring).
_SSE_HEARTBEAT_TIMEOUT = 30.0

# Terminal frame name sent when the stream is closed for an authentication
# reason rather than a network/shutdown one.
_AUTH_REVOKED_EVENT = "auth_revoked"


async def _await_next_event(q, shutdown_event, timeout):
    """Wait up to ``timeout`` seconds for the next queued event.

    Returns the FlowEvent if one arrives, or ``None`` if ``shutdown_event``
    fires first (server is shutting down). Raises ``asyncio.TimeoutError`` on an
    idle timeout so the caller can emit a heartbeat ping.

    Racing the subscriber queue against the shutdown event lets a graceful
    server shutdown stop a long-lived SSE stream immediately, instead of
    blocking uvicorn's shutdown until the browser disconnects
    (group 0102 R0001 — open SSE streams blocked graceful shutdown forever).
    """
    get_task = asyncio.ensure_future(q.get())
    waiters = {get_task}
    shutdown_task = None
    if shutdown_event is not None:
        shutdown_task = asyncio.ensure_future(shutdown_event.wait())
        waiters.add(shutdown_task)
    try:
        done, _pending = await asyncio.wait(
            waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            raise asyncio.TimeoutError
        # Prefer a delivered event over the shutdown signal if both fired in the
        # same tick, so a final queued event is never dropped on the way out.
        if get_task in done:
            return get_task.result()
        return None
    finally:
        for t in (get_task, shutdown_task):
            if t is not None and not t.done():
                t.cancel()


def _extract_jwt(request: Request, token_param: Optional[str]) -> Optional[str]:
    """Extract JWT: prefer query param, then Authorization header."""
    if token_param:
        return token_param
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


def _frame_id() -> str:
    """SSE frame id: UTC timestamp plus a short random suffix."""
    return (
        f"sse_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        f"_{uuid.uuid4().hex[:6]}"
    )


def _auth_revoked_frame(reason: str) -> str:
    """Terminal frame telling the client why the stream ended.

    Without it the browser sees an ordinary close and reconnects on the usual
    backoff — forever, because every attempt 401s against the same revoked
    session. The client listens for this event and stops instead
    (client/src/main/composables/useFlowGateSse.ts).
    """
    data = json.dumps({"reason": reason}, ensure_ascii=False)
    return f"id: {_frame_id()}\nevent: {_AUTH_REVOKED_EVENT}\ndata: {data}\n\n"


def _claims(payload: dict) -> Optional[dict]:
    """Token-only checks; returns the auth context, or None if it is unusable.

    The same three rules auth/middleware.verify_token applies before it touches
    the DB. ``sid`` and ``jti`` are carried along because the live-state check
    below needs them for the whole life of the stream, not just at connect.
    """
    if payload.get("type") != "access":
        return None
    if payload.get("totp_pending"):
        return None
    user_id = payload.get("sub")
    if not user_id:
        return None
    return {"user_id": user_id, "sid": payload.get("sid"), "jti": payload.get("jti")}


def _load_user(user_id: str) -> Optional[dict]:
    """The users row, through the very cache entry the ordinary API uses.

    Sharing it matters: db.users.create/update/delete invalidate that entry, so
    a deactivation lands here immediately instead of after the cache TTL.
    """
    from modules.flow_gate.db import users as db_users

    return _auth_cache.user_cache().get_or_load(
        user_id, lambda: db_users.get_by_id(user_id)
    )


def _revocation_reason(ctx: dict) -> Optional[str]:
    """None while this login may keep streaming; otherwise why it may not.

    These are the three per-request lookups auth/middleware runs — access token
    blacklisted, session revoked, user missing or deactivated — and they are
    exactly what SSE skipped before 0371.

    Blocking (three single-row reads at worst, usually all cache hits), so call
    it through ``anyio.to_thread.run_sync``, never straight from the loop.

    A lookup that *raises* is reported as a stop reason instead of being
    ignored: this is the check that decides whether a revoked login keeps
    receiving pushes, so the unknown answer has to be the closed one. The client
    reconnects, and a reconnect made while the DB is unreachable fails at the
    door rather than silently inheriting a stream nobody can validate.
    """
    jti, sid, user_id = ctx.get("jti"), ctx.get("sid"), ctx["user_id"]
    try:
        # Fills whichever of the three auth caches are cold in a single query
        # (0291 T1); it never decides anything by itself and swallows failures.
        _auth_preamble.prefetch(jti, sid, user_id)
        if jti and is_blacklisted(jti):
            return "token_revoked"
        if sid and not is_session_active(sid):
            return "session_revoked"
        user = _load_user(user_id)
    except Exception as exc:
        logger.debug(f"[SSE] auth re-check failed: {exc}")
        return "auth_check_failed"
    if not user:
        return "user_not_found"
    if not user.get("is_active"):
        return "user_inactive"
    return None


def _authenticate(jwt_token: str) -> Optional[dict]:
    """Full connect-time validation: the auth context, or None to answer 401.

    Blocking for the same reason _revocation_reason is; the handler offloads it.
    """
    try:
        payload = decode_token(jwt_token)
    except Exception:
        return None
    ctx = _claims(payload)
    if ctx is None:
        return None
    if _revocation_reason(ctx) is not None:
        return None
    return ctx


@router.get("/events/stream")
async def sse_stream(
    request: Request,
    token: Optional[str] = None,
    project: Optional[str] = None,
):
    """SSE UI push stream.

    ``project`` is the project this screen is currently showing (0291 D0005 §3-2).
    Broadcast events carrying a different project are not delivered to this stream.
    It is optional: a client that omits it receives every broadcast, as before.
    Because the interest is bound at connect time, the client reconnects when the
    user switches project — and its existing reconnect handler re-reads the screen,
    which covers the events missed during the switch (§3-3).
    """
    jwt_token = _extract_jwt(request, token)
    if jwt_token is None:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "http_status": 401,
                     "error_message": "Authentication required",
                     "help_url": help_url()}
        )

    # The blocking DB reads sit inside _authenticate, so it runs in a worker
    # thread: an `async def` handler must not block the event loop (0279 T0005).
    ctx = await anyio.to_thread.run_sync(_authenticate, jwt_token)
    if ctx is None:
        return JSONResponse(
            status_code=401,
            content={"ok": False, "http_status": 401,
                     "error_message": "Session is invalid",
                     "help_url": help_url()}
        )
    user_id = ctx["user_id"]

    interest_project = project or None
    q = await subscribe(user_id, interest_project)
    logger.debug(f"[SSE] subscribed user={user_id} project={interest_project}")

    # Set in routers.main.lifespan; absent in lightweight test apps (then None,
    # which simply disables the shutdown race and keeps the heartbeat behaviour).
    shutdown_event = getattr(request.app.state, "shutdown_event", None)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                if shutdown_event is not None and shutdown_event.is_set():
                    break
                try:
                    event = await _await_next_event(
                        q, shutdown_event, _SSE_HEARTBEAT_TIMEOUT
                    )
                    if event is None:
                        # shutdown_event fired — end the stream so uvicorn's
                        # graceful shutdown can complete instead of hanging.
                        break
                    # Re-check before *delivering*: a session revoked while this
                    # event sat in the queue must not receive it (0371 T0012).
                    reason = await anyio.to_thread.run_sync(_revocation_reason, ctx)
                    if reason is not None:
                        logger.debug(
                            f"[SSE] auth ended stream user={user_id} reason={reason}"
                        )
                        yield _auth_revoked_frame(reason)
                        break
                    event_id = _frame_id()
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
                    # The heartbeat is also the revocation deadline: an idle
                    # stream is re-checked once per cadence, so a revoked login
                    # stops receiving pushes within ~_SSE_HEARTBEAT_TIMEOUT
                    # seconds (NR0007 §4 recommendation 2).
                    reason = await anyio.to_thread.run_sync(_revocation_reason, ctx)
                    if reason is not None:
                        logger.debug(
                            f"[SSE] auth ended stream user={user_id} reason={reason}"
                        )
                        yield _auth_revoked_frame(reason)
                        break
                    yield f"id: {_frame_id()}\nevent: ping\ndata: {{}}\n\n"
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

