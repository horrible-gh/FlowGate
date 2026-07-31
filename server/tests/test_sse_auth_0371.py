"""0371 T0012 — SSE streams must obey session/user revocation (NR0007 §4).

Before this change ``/api/v1/events/stream`` validated only the JWT itself:
decode, ``type == access``, 2FA finished, jti not blacklisted. The two lookups
every ordinary API request also makes — is this session still active, does this
user still exist and is it active — were missing, and nothing was re-checked
once the stream was open. So "log out my other device", "revoke all sessions"
and "deactivate this account" left the pushed stream running: the account kept
receiving every screen event for as long as the browser stayed open.

These tests pin both halves of the fix:

  * connect time refuses a revoked session / missing / inactive user, and
  * an already-open stream ends at the next event or heartbeat, whichever comes
    first, with a terminal ``auth_revoked`` frame telling the client why.

The generator is driven straight off the StreamingResponse body iterator, the
way test_sse_heartbeat_ping and test_sse_shutdown_0102 do it: the idle path is a
queue that never yields, which makes an ASGI-transport test brittle.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from fastapi import FastAPI

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1.events import sse_routes  # noqa: E402
from modules.flow_gate.api.v1.events.publisher import FlowEvent  # noqa: E402
from modules.flow_gate.auth import auth_cache  # noqa: E402

_MOD = "modules.flow_gate.api.v1.events.sse_routes"

_DECODED = {
    "sub": "usr_0371",
    "type": "access",
    "jti": "jti-0371",
    "sid": "sid-0371",
    "totp_pending": False,
}
_CTX = {"user_id": "usr_0371", "sid": "sid-0371", "jti": "jti-0371"}
_ACTIVE_USER = {"user_id": "usr_0371", "is_active": True}
_INACTIVE_USER = {"user_id": "usr_0371", "is_active": False}


def _answer(value):
    """patch() kwargs for a fixed answer or a callable that can change its mind."""
    return {"side_effect": value} if callable(value) else {"return_value": value}


def _patches(queue, *, decoded=None, blacklisted=False, session_active=True,
             user=_ACTIVE_USER):
    """The four auth lookups plus the publisher, all mocked.

    Every answer may be a plain value or a callable, so a test can flip it while
    the stream is open — that is the whole point of the re-check.
    """
    return (
        patch(f"{_MOD}.decode_token", return_value=decoded or dict(_DECODED)),
        patch(f"{_MOD}.is_blacklisted", **_answer(blacklisted)),
        patch(f"{_MOD}.is_session_active", **_answer(session_active)),
        patch(f"{_MOD}._load_user", **_answer(user)),
        # The prefetch only warms the shared auth caches and decides nothing, so
        # it is stubbed out and these tests need no database at all. That it is
        # still called is asserted separately below.
        patch(f"{_MOD}._auth_preamble.prefetch", Mock()),
        patch(f"{_MOD}.subscribe", new=AsyncMock(return_value=queue)),
        patch(f"{_MOD}.unsubscribe", new=AsyncMock()),
    )


def _enter(patchers):
    for p in patchers:
        p.__enter__()


def _exit(patchers):
    for p in reversed(patchers):
        p.__exit__(None, None, None)


def _live_until_connected():
    """An answer that is True for the connect check and False from then on.

    Models the interesting case: the login was valid when the stream opened and
    was revoked while it stayed open.
    """
    calls = {"n": 0}

    def check(_key):
        calls["n"] += 1
        return calls["n"] <= 1

    return check


def _request():
    request = Mock()
    request.is_disconnected = AsyncMock(return_value=False)
    request.app.state.shutdown_event = asyncio.Event()  # unset: keep streaming
    return request


async def _open_stream(queue=None, **kw):
    """Open a stream with a fast heartbeat; returns (frame iterator, patchers)."""
    patchers = [
        *_patches(queue if queue is not None else asyncio.Queue(), **kw),
        patch(f"{_MOD}._SSE_HEARTBEAT_TIMEOUT", 0.05),
    ]
    _enter(patchers)
    try:
        resp = await sse_routes.sse_stream(_request(), token="good-jwt")
        return resp.body_iterator, patchers
    except BaseException:
        _exit(patchers)  # never leak a patch into the next test
        raise


def _reason_of(frame):
    return json.loads(frame.split("data: ", 1)[1].strip())["reason"]


# ── connect time ─────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(sse_routes.router)
    return app


async def _connect_status(**kw):
    """Status of one connection attempt, taken off the response object itself.

    The route is called directly rather than through an ASGI transport: a granted
    stream never ends, and the transport would sit there reading it forever.
    The refusals below are ordinary JSON responses, and the one that goes through
    the real router end-to-end is test_a_refused_connection_leaves_no_subscriber_behind.
    """
    patchers = list(_patches(asyncio.Queue(), **kw))
    _enter(patchers)
    try:
        resp = await sse_routes.sse_stream(_request(), token="good-jwt")
        if hasattr(resp, "body_iterator"):
            await resp.body_iterator.aclose()
        return resp.status_code
    finally:
        _exit(patchers)


@pytest.mark.asyncio
async def test_connect_accepted_when_session_and_user_are_live():
    """The happy path still connects — the new checks must not lock everyone out."""
    assert await _connect_status() == 200


@pytest.mark.asyncio
async def test_connect_rejected_when_session_revoked():
    """Logged out on another device: the token still decodes, the session is gone."""
    assert await _connect_status(session_active=False) == 401


@pytest.mark.asyncio
async def test_connect_rejected_when_user_row_is_gone():
    assert await _connect_status(user=None) == 401


@pytest.mark.asyncio
async def test_connect_rejected_when_user_is_inactive():
    assert await _connect_status(user=_INACTIVE_USER) == 401


@pytest.mark.asyncio
async def test_connect_rejected_when_token_blacklisted():
    """Kept from the pre-0371 behaviour — the one check SSE already had."""
    assert await _connect_status(blacklisted=True) == 401


@pytest.mark.asyncio
async def test_connect_rejected_when_a_lookup_raises():
    """Fail closed: an unanswerable auth question is not an affirmative answer."""
    def boom(_user_id):
        raise RuntimeError("db down")

    assert await _connect_status(user=boom) == 401


@pytest.mark.asyncio
async def test_connect_rejected_for_a_refresh_token():
    assert await _connect_status(decoded=dict(_DECODED, type="refresh")) == 401


@pytest.mark.asyncio
async def test_connect_rejected_while_2fa_is_pending():
    assert await _connect_status(decoded=dict(_DECODED, totp_pending=True)) == 401


@pytest.mark.asyncio
async def test_connect_rejected_without_a_subject():
    decoded = dict(_DECODED)
    decoded.pop("sub")
    assert await _connect_status(decoded=decoded) == 401


@pytest.mark.asyncio
async def test_a_refused_connection_leaves_no_subscriber_behind(app):
    """Registering first and validating afterwards would leak one subscription
    per rejected attempt — exactly the kind of thing that can be looped."""
    patchers = list(_patches(asyncio.Queue(), session_active=False))
    subscribe_patcher = patchers[5]
    _enter(patchers)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/events/stream?token=good-jwt")
        assert resp.status_code == 401
        assert subscribe_patcher.new.await_count == 0
    finally:
        _exit(patchers)


# ── open streams ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_open_stream_ends_at_the_next_heartbeat_after_a_revoke():
    """The bound NR0007 §4 asks for: a revoke lands within one heartbeat."""
    live = {"active": True}
    gen, patchers = await _open_stream(session_active=lambda sid: live["active"])
    try:
        first = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert "event: ping" in first  # healthy while the session is live

        live["active"] = False  # revoked from another device

        closing = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert "event: auth_revoked" in closing
        assert _reason_of(closing) == "session_revoked"

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    finally:
        await gen.aclose()
        _exit(patchers)


@pytest.mark.asyncio
async def test_a_queued_event_is_not_delivered_to_a_revoked_session():
    """The re-check runs BEFORE the frame is written, not after: checking
    afterwards would still hand one last screen event to a revoked session."""
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(FlowEvent(event_type="demo", payload={"ok": True}, audience="usr_0371"))
    gen, patchers = await _open_stream(queue=queue, session_active=_live_until_connected())
    try:
        first = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert "event: demo" not in first
        assert "event: auth_revoked" in first
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    finally:
        await gen.aclose()
        _exit(patchers)


@pytest.mark.asyncio
async def test_open_stream_ends_when_the_account_is_deactivated():
    live = {"user": _ACTIVE_USER}
    gen, patchers = await _open_stream(user=lambda uid: live["user"])
    try:
        assert "event: ping" in await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        live["user"] = _INACTIVE_USER
        assert _reason_of(await asyncio.wait_for(gen.__anext__(), timeout=2.0)) == "user_inactive"
    finally:
        await gen.aclose()
        _exit(patchers)


@pytest.mark.asyncio
async def test_open_stream_ends_when_the_access_token_is_blacklisted():
    live = {"revoked": False}
    gen, patchers = await _open_stream(blacklisted=lambda jti: live["revoked"])
    try:
        assert "event: ping" in await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        live["revoked"] = True
        assert _reason_of(await asyncio.wait_for(gen.__anext__(), timeout=2.0)) == "token_revoked"
    finally:
        await gen.aclose()
        _exit(patchers)


@pytest.mark.asyncio
async def test_a_healthy_stream_keeps_streaming_across_rechecks():
    """The re-check must not become a slow leak that kills good streams."""
    gen, patchers = await _open_stream()
    try:
        for _ in range(4):
            frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
            assert "event: ping" in frame
            assert "auth_revoked" not in frame
    finally:
        await gen.aclose()
        _exit(patchers)


@pytest.mark.asyncio
async def test_a_healthy_stream_still_delivers_events():
    queue: asyncio.Queue = asyncio.Queue()
    queue.put_nowait(FlowEvent(event_type="demo", payload={"ok": True}, audience="usr_0371"))
    gen, patchers = await _open_stream(queue=queue)
    try:
        assert "event: demo" in await asyncio.wait_for(gen.__anext__(), timeout=2.0)
    finally:
        await gen.aclose()
        _exit(patchers)


@pytest.mark.asyncio
async def test_a_stream_ended_by_auth_still_unsubscribes():
    """Ending on a revoke must release the subscriber slot like any other exit."""
    patchers = [
        *_patches(asyncio.Queue(), session_active=_live_until_connected()),
        patch(f"{_MOD}._SSE_HEARTBEAT_TIMEOUT", 0.05),
    ]
    unsubscribe_patcher = patchers[6]
    _enter(patchers)
    try:
        resp = await sse_routes.sse_stream(_request(), token="good-jwt")
        gen = resp.body_iterator
        assert "event: auth_revoked" in await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        await gen.aclose()
        assert unsubscribe_patcher.new.await_count == 1
    finally:
        _exit(patchers)


# ── the checks themselves ────────────────────────────────────────────────────

def test_claims_carry_sid_and_jti():
    """The stream needs both for its whole life, not just at connect."""
    assert sse_routes._claims(dict(_DECODED)) == _CTX


def test_a_token_without_sid_skips_the_session_check():
    """Tokens minted before sessions existed still work; there is nothing to ask."""
    payload = dict(_DECODED)
    payload.pop("sid")
    ctx = sse_routes._claims(payload)
    assert ctx["sid"] is None

    session_check = Mock(return_value=True)
    with patch(f"{_MOD}.is_blacklisted", return_value=False), \
         patch(f"{_MOD}.is_session_active", session_check), \
         patch(f"{_MOD}._load_user", return_value=_ACTIVE_USER), \
         patch(f"{_MOD}._auth_preamble.prefetch", Mock()):
        assert sse_routes._revocation_reason(ctx) is None
    session_check.assert_not_called()


@pytest.mark.parametrize(
    "blacklisted, session_active, user, expected",
    [
        (False, True, _ACTIVE_USER, None),
        (True, True, _ACTIVE_USER, "token_revoked"),
        (False, False, _ACTIVE_USER, "session_revoked"),
        (False, True, None, "user_not_found"),
        (False, True, _INACTIVE_USER, "user_inactive"),
    ],
    ids=["live", "token_revoked", "session_revoked", "user_not_found", "user_inactive"],
)
def test_revocation_reason_names_what_stopped_the_stream(
    blacklisted, session_active, user, expected
):
    """A named reason, not a bare False: it is what the client and the log get."""
    with patch(f"{_MOD}.is_blacklisted", return_value=blacklisted), \
         patch(f"{_MOD}.is_session_active", return_value=session_active), \
         patch(f"{_MOD}._load_user", return_value=user), \
         patch(f"{_MOD}._auth_preamble.prefetch", Mock()):
        assert sse_routes._revocation_reason(dict(_CTX)) == expected


def test_revocation_reason_fails_closed_when_a_lookup_raises():
    with patch(f"{_MOD}.is_blacklisted", side_effect=RuntimeError("db down")), \
         patch(f"{_MOD}._auth_preamble.prefetch", Mock()):
        assert sse_routes._revocation_reason(dict(_CTX)) == "auth_check_failed"


def test_revocation_reason_warms_the_shared_auth_caches():
    """These are the same three lookups the ordinary API makes, so the same
    single-query prefetch (0291 T1) applies — without it every re-check on every
    open stream would be three cold round trips."""
    prefetch = Mock()
    with patch(f"{_MOD}.is_blacklisted", return_value=False), \
         patch(f"{_MOD}.is_session_active", return_value=True), \
         patch(f"{_MOD}._load_user", return_value=_ACTIVE_USER), \
         patch(f"{_MOD}._auth_preamble.prefetch", prefetch):
        sse_routes._revocation_reason(dict(_CTX))
    prefetch.assert_called_once_with("jti-0371", "sid-0371", "usr_0371")


def test_load_user_reads_through_the_shared_user_cache():
    """Sharing auth_cache is what makes a deactivation land immediately: the user
    write paths invalidate that entry, and a private copy would not see it."""
    from modules.flow_gate.db import users as db_users

    with patch.dict(os.environ, {"FLOWGATE_AUTH_CACHE_TTL": "5"}):
        auth_cache.invalidate_everything()
        with patch.object(db_users, "get_by_id", return_value=_ACTIVE_USER) as get_by_id:
            assert sse_routes._load_user("usr_0371") == _ACTIVE_USER
            assert sse_routes._load_user("usr_0371") == _ACTIVE_USER
            assert get_by_id.call_count == 1  # second read served from the cache

            auth_cache.invalidate_user("usr_0371")  # what db.users.update() calls
            assert sse_routes._load_user("usr_0371") == _ACTIVE_USER
            assert get_by_id.call_count == 2  # invalidation is honoured at once
        auth_cache.invalidate_everything()


def test_auth_revoked_frame_is_a_parseable_named_event():
    frame = sse_routes._auth_revoked_frame("session_revoked")
    assert frame.startswith("id: sse_")
    assert "event: auth_revoked" in frame
    assert frame.endswith("\n\n")
    assert json.loads(frame.split("data: ", 1)[1].strip()) == {"reason": "session_revoked"}


# ── static guards ────────────────────────────────────────────────────────────

_SOURCE = (
    _SERVER_DIR / "modules" / "flow_gate" / "api" / "v1" / "events" / "sse_routes.py"
).read_text(encoding="utf-8")


def test_auth_lookups_never_run_on_the_event_loop():
    """These are synchronous DB reads inside an `async def` handler — exactly the
    0279 T0005 pattern that once froze the whole server for 40 seconds. The
    connect check and both in-stream re-checks must be offloaded."""
    assert "await anyio.to_thread.run_sync(_authenticate, jwt_token)" in _SOURCE
    assert _SOURCE.count("await anyio.to_thread.run_sync(_revocation_reason, ctx)") == 2
    # …and no direct call from the loop slipped back in.
    assert "= _authenticate(" not in _SOURCE
    assert "= _revocation_reason(" not in _SOURCE


def test_the_recheck_does_not_re_decode_the_jwt():
    """Deliberate (see the module docstring): an access token expiring mid-stream
    is not a revocation, and ending every stream once per token lifetime would
    cause a reconnect + full-resync storm on the client."""
    assert _SOURCE.count("decode_token(") == 1
