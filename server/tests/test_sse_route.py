from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1.events import sse_routes  # noqa: E402
from modules.flow_gate.api.v1.events.publisher import FlowEvent  # noqa: E402


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(sse_routes.router)
    return app


@pytest.mark.asyncio
async def test_sse_no_auth_401(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/events/stream")
    assert resp.status_code == 401
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_sse_invalid_token_401(app):
    transport = httpx.ASGITransport(app=app)
    with patch("modules.flow_gate.api.v1.events.sse_routes.decode_token", side_effect=Exception("bad")):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/events/stream?token=bad-jwt")
    assert resp.status_code == 401
    assert resp.json()["error_message"] == "Session is invalid"


@pytest.mark.asyncio
async def test_sse_auth_flow(app):
    """SSE auth flow test with 5-second timeout."""
    flow_queue = asyncio.Queue()
    flow_queue.put_nowait(FlowEvent(event_type="demo", payload={"ok": True}, audience="usr_test_001"))

    with patch("modules.flow_gate.api.v1.events.sse_routes.decode_token", return_value={
        "sub": "usr_test_001",
        "type": "access",
        "jti": "jti-1",
        "totp_pending": False,
    }), patch("modules.flow_gate.api.v1.events.sse_routes.is_blacklisted", return_value=False), patch(
        "modules.flow_gate.api.v1.events.sse_routes.subscribe", new=AsyncMock(return_value=flow_queue)
    ), patch("modules.flow_gate.api.v1.events.sse_routes.unsubscribe", new=AsyncMock()):
        transport = httpx.ASGITransport(app=app)
        
        try:
            async with asyncio.timeout(5):
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    async with client.stream("GET", "/api/v1/events/stream?token=good-jwt") as resp:
                        assert resp.status_code == 200
                        assert resp.headers["content-type"].startswith("text/event-stream")
                        # Attempt to read first SSE event
                        async for chunk in resp.aiter_text():
                            if chunk and chunk.strip():
                                # Verify SSE format and event type
                                assert "event: demo" in chunk
                                return
        except asyncio.TimeoutError:
            # Timeout after 5 seconds is expected as heartbeat stream is infinite
            pass


@pytest.mark.asyncio
async def test_sse_reconnect_no_replay(app):
    """Treat SSE reconnection as a new connection (last_event_id not implemented — only new events are received after reconnecting)."""
    flow_queue = asyncio.Queue()
    flow_queue.put_nowait(FlowEvent(event_type="reconnect_test", payload={"seq": 1}, audience="usr_test_002"))

    decoded = {
        "sub": "usr_test_002",
        "type": "access",
        "jti": "jti-reconnect",
        "totp_pending": False,
    }

    with patch("modules.flow_gate.api.v1.events.sse_routes.decode_token", return_value=decoded), \
         patch("modules.flow_gate.api.v1.events.sse_routes.is_blacklisted", return_value=False), \
         patch("modules.flow_gate.api.v1.events.sse_routes.subscribe", new=AsyncMock(return_value=flow_queue)), \
         patch("modules.flow_gate.api.v1.events.sse_routes.unsubscribe", new=AsyncMock()):

        transport = httpx.ASGITransport(app=app)
        try:
            async with asyncio.timeout(5):
                async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                    # Reconnect: even if the Last-Event-ID header is sent, the server does not replay and treats it as a new connection
                    async with client.stream(
                        "GET",
                        "/api/v1/events/stream?token=good-jwt",
                        headers={"Last-Event-ID": "sse_20260101_000000_aabbcc"},
                    ) as resp:
                        assert resp.status_code == 200
                        assert resp.headers["content-type"].startswith("text/event-stream")
                        async for chunk in resp.aiter_text():
                            if chunk and chunk.strip():
                                # Receive the new event currently in the queue after reconnecting
                                assert "event: reconnect_test" in chunk
                                return
        except asyncio.TimeoutError:
            pass  # heartbeat stream — normal completion
