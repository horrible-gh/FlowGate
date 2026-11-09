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


@pytest.mark.asyncio
async def test_nowait_broadcast_does_not_wait_for_a_busy_main_loop_and_still_delivers():
    queue = await publisher.subscribe("user-2")
    event = FlowEvent(event_type="test", payload={"ok": True}, audience="*")
    result: list[int] = []

    thread = threading.Thread(
        target=lambda: result.append(publisher.broadcast_event_nowait(event)),
    )
    thread.start()
    # Join on the loop thread: the loop stays busy. The waiting variant would time out
    # here (2s) and return 0; the nowait variant returns -1 at once.
    thread.join(timeout=5)

    assert result == [-1]
    assert await asyncio.wait_for(queue.get(), timeout=1) is event
    await publisher.unsubscribe("user-2", queue)
