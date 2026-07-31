"""Regression guard for R0001 / group 0025 TR: the SSE idle heartbeat must be a
client-observable named `ping` event, not a bare `:` comment.

The browser EventSource swallows comment lines (they fire no JS event), so a
`: heartbeat` comment cannot drive the client liveness watchdog that detects a
silently-dead stream. The server must emit a real `event: ping` frame.

The generator is exercised directly (pulling the first frame off the StreamingResponse
body iterator) rather than through an ASGI transport — the idle path is driven purely
by a queue that never yields an event, and httpx/starlette streaming internals also lean
on asyncio.wait_for, which makes a transport-level test brittle.
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


@pytest.mark.asyncio
async def test_sse_idle_heartbeat_is_observable_ping_event():
    empty_queue = asyncio.Queue()  # never delivers an event -> generator hits the idle path

    request = Mock()
    request.is_disconnected = AsyncMock(return_value=False)
    # An unset shutdown event: the generator should keep streaming heartbeats,
    # not stop. Shrink the heartbeat cadence so the idle path fires immediately.
    request.app.state.shutdown_event = asyncio.Event()

    decoded = {
        "sub": "usr_test_hb",
        "type": "access",
        "jti": "jti-hb",
        "totp_pending": False,
    }

    with patch("modules.flow_gate.api.v1.events.sse_routes.decode_token", return_value=decoded), \
         patch("modules.flow_gate.api.v1.events.sse_routes.is_blacklisted", return_value=False), \
         patch("modules.flow_gate.api.v1.events.sse_routes._load_user",
               return_value={"user_id": "usr_test_hb", "is_active": True}), \
         patch("modules.flow_gate.api.v1.events.sse_routes.subscribe", new=AsyncMock(return_value=empty_queue)), \
         patch("modules.flow_gate.api.v1.events.sse_routes.unsubscribe", new=AsyncMock()), \
         patch("modules.flow_gate.api.v1.events.sse_routes._SSE_HEARTBEAT_TIMEOUT", 0.05):
        resp = await sse_routes.sse_stream(request, token="good-jwt")
        gen = resp.body_iterator
        try:
            first = await gen.__anext__()
        finally:
            await gen.aclose()

    assert "event: ping" in first
    assert ": heartbeat" not in first
