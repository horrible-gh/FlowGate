from __future__ import annotations

import asyncio
import threading

import pytest

from modules.flow_gate.api.v1.events import publisher
from modules.flow_gate.api.v1.events.publisher import FlowEvent


@pytest.mark.asyncio
async def test_threadsafe_broadcast_waits_until_subscriber_queue_receives_event():
    queue = await publisher.subscribe("user-1")
    event = FlowEvent(event_type="test", payload={"ok": True}, audience="*")
    result: list[int] = []

    thread = threading.Thread(
        target=lambda: result.append(publisher.broadcast_event_threadsafe(event)),
    )
    thread.start()
    await asyncio.to_thread(thread.join)

    assert result == [1]
    assert await asyncio.wait_for(queue.get(), timeout=1) is event
    await publisher.unsubscribe("user-1", queue)
