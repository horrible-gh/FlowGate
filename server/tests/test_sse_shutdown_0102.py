"""Regression guard for group 0102 R0001: long-lived SSE streams must not block
graceful server shutdown.

Root cause (NR0003): an open EventSource keeps `event_generator` running on a
`while True` loop whose only exit conditions were a client disconnect or task
cancellation. uvicorn's graceful shutdown waits on that in-flight response, so
the server only exited once the browser was closed.

Fix (T0004, candidate A — cooperative shutdown): `routers.main.lifespan` puts an
`asyncio.Event` on `app.state.shutdown_event` and sets it on shutdown; the SSE
generator races it against the subscriber queue and stops promptly when set.
(Candidate B — `timeout_graceful_shutdown` on every uvicorn entry point — is the
backstop and is asserted separately in `test_uvicorn_graceful_timeout_0102`.)

The generator is exercised directly off the StreamingResponse body iterator,
matching `test_sse_heartbeat_ping` — the shutdown path is driven purely by an
empty queue plus the shutdown event, with no need for an ASGI transport.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1.events import sse_routes  # noqa: E402
from modules.flow_gate.api.v1.events.publisher import FlowEvent  # noqa: E402

_DECODED = {
    "sub": "usr_test_shutdown",
    "type": "access",
    "jti": "jti-shutdown",
    "totp_pending": False,
}


def _patches(queue):
    return (
        patch("modules.flow_gate.api.v1.events.sse_routes.decode_token", return_value=_DECODED),
        patch("modules.flow_gate.api.v1.events.sse_routes.is_blacklisted", return_value=False),
        patch("modules.flow_gate.api.v1.events.sse_routes.subscribe", new=AsyncMock(return_value=queue)),
        patch("modules.flow_gate.api.v1.events.sse_routes.unsubscribe", new=AsyncMock()),
    )


def _request(shutdown_event):
    request = Mock()
    request.is_disconnected = AsyncMock(return_value=False)
    request.app.state.shutdown_event = shutdown_event
    return request


@pytest.mark.asyncio
async def test_sse_stops_immediately_when_shutdown_already_set():
    """A stream that opens while the server is already shutting down ends at once."""
    empty_queue = asyncio.Queue()
    shutdown_event = asyncio.Event()
    shutdown_event.set()
    request = _request(shutdown_event)

    p1, p2, p3, p4 = _patches(empty_queue)
    with p1, p2, p3, p4:
        resp = await sse_routes.sse_stream(request, token="good-jwt")
        gen = resp.body_iterator
        with pytest.raises(StopAsyncIteration):
            # No frame is produced; the generator returns, so __anext__ raises.
            await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        await gen.aclose()


@pytest.mark.asyncio
async def test_sse_stops_when_shutdown_fires_while_blocked():
    """A stream blocked waiting for events stops as soon as shutdown is signalled,
    without waiting for a client disconnect or the heartbeat timeout."""
    empty_queue = asyncio.Queue()  # never delivers -> generator blocks in the race
    shutdown_event = asyncio.Event()
    request = _request(shutdown_event)

    p1, p2, p3, p4 = _patches(empty_queue)
    # Keep the heartbeat cadence long so the stop is attributable to shutdown,
    # not to an idle ping firing first.
    with p1, p2, p3, p4, \
         patch("modules.flow_gate.api.v1.events.sse_routes._SSE_HEARTBEAT_TIMEOUT", 30.0):
        resp = await sse_routes.sse_stream(request, token="good-jwt")
        gen = resp.body_iterator

        next_frame = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)  # let the generator reach the blocking race
        assert not next_frame.done(), "generator should still be waiting for an event"

        shutdown_event.set()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(next_frame, timeout=2.0)
        await gen.aclose()


@pytest.mark.asyncio
async def test_sse_delivers_pending_event_before_shutdown_check():
    """A queued event is still delivered even if shutdown is set in the same tick:
    the race prefers the event so a final notification is not dropped."""
    queue = asyncio.Queue()
    queue.put_nowait(FlowEvent(event_type="demo", payload={"ok": True}, audience="usr_test_shutdown"))
    shutdown_event = asyncio.Event()  # unset for the first read
    request = _request(shutdown_event)

    p1, p2, p3, p4 = _patches(queue)
    with p1, p2, p3, p4:
        resp = await sse_routes.sse_stream(request, token="good-jwt")
        gen = resp.body_iterator
        first = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert "event: demo" in first
        await gen.aclose()
