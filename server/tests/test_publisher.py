from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1.events.publisher import (  # noqa: E402
    FlowEvent,
    _subscribers,
    publish_event,
    subscribe,
    unsubscribe,
)


@pytest.fixture(autouse=True)
def reset_subscribers():
    _subscribers.clear()
    yield
    _subscribers.clear()


@pytest.mark.asyncio
async def test_publish_and_receive():
    q = await subscribe("usr_test_001")
    event = FlowEvent(event_type="demo", payload={"x": 1}, audience="usr_test_001")

    await publish_event(event)

    received = await asyncio.wait_for(q.get(), timeout=1)
    assert received.event_type == "demo"
    assert received.payload == {"x": 1}


@pytest.mark.asyncio
async def test_fanout_multi_tab():
    q1 = await subscribe("usr_test_001")
    q2 = await subscribe("usr_test_001")

    await publish_event(FlowEvent(event_type="fanout", payload={"ok": True}, audience="usr_test_001"))

    r1 = await asyncio.wait_for(q1.get(), timeout=1)
    r2 = await asyncio.wait_for(q2.get(), timeout=1)
    assert r1.event_type == "fanout"
    assert r2.event_type == "fanout"


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    q = await subscribe("usr_test_001")

    await unsubscribe("usr_test_001", q)

    assert "usr_test_001" not in _subscribers


@pytest.mark.asyncio
async def test_publish_after_disconnect_no_error():
    await publish_event(FlowEvent(event_type="noop", payload={}, audience="ghost"))
