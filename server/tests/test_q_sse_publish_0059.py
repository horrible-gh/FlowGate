"""0059 B0001 rev3 regression — does the worker-question SSE event 'actually get published'.

Symptom (same repro rev0~rev2): when the worker registers a question, it isn't visible on screen until F5.
Real cause (rev3): q_service._notify_q_registered called asyncio.get_event_loop() from a sync FastAPI route
(= an anyio worker thread with no running event loop) → RuntimeError → swallowed by the outer
except → the qna_q_registered event was never published. The client bridge
(fg:q_registered), anchor redirect, and deployment were all fine, but it was moot since the server never emitted the event.

Fix: replace the publish with publish_event_threadsafe (schedules run_coroutine_threadsafe on the
captured main loop — the same mechanism as the known-good workflow-decision broadcast). Keeps the audience scope (PM only).

This test verifies that, with the SSE subscription queue owned by the main loop, calling the registration
notification from a 'worker thread' actually delivers the event to the queue (the server-side half of the no-F5-needed path).
"""
from __future__ import annotations

import asyncio

import pytest

from modules.flow_gate.api.v1.events import publisher
from modules.flow_gate.services import q_service


@pytest.mark.asyncio
async def test_notify_q_registered_delivers_from_worker_thread():
    """Even when called from a sync route (worker thread), the event is delivered to the audience queue."""
    audience = "pm-user-0059"
    q = await publisher.subscribe(audience)
    publisher._main_loop = asyncio.get_running_loop()  # mimic what sse_routes captures on connect
    try:
        def worker():  # threadpool context where a FastAPI sync route runs
            q_service._notify_q_registered(
                audience=audience,
                doc_id="flowgate.default.0059.0004-T",
                project_id="flowgate",
                titles=["why is X ambiguous?"],
            )

        await asyncio.to_thread(worker)

        ev = await asyncio.wait_for(q.get(), timeout=2.0)
        assert getattr(ev.event_type, "value", ev.event_type) == "qna_q_registered"
        assert ev.doc_id == "flowgate.default.0059.0004-T"
        assert ev.payload["titles"] == ["why is X ambiguous?"]
    finally:
        await publisher.unsubscribe(audience, q)


@pytest.mark.asyncio
async def test_notify_q_registered_is_audience_scoped():
    """Not delivered to other users' queues (PM-only, not a broadcast)."""
    pm = await publisher.subscribe("pm-scoped")
    other = await publisher.subscribe("other-user")
    publisher._main_loop = asyncio.get_running_loop()
    try:
        def worker():
            q_service._notify_q_registered(
                audience="pm-scoped", doc_id="d", project_id="p", titles=["t"],
            )

        await asyncio.to_thread(worker)
        ev = await asyncio.wait_for(pm.get(), timeout=2.0)
        assert ev.doc_id == "d"
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(other.get(), timeout=0.5)
    finally:
        await publisher.unsubscribe("pm-scoped", pm)
        await publisher.unsubscribe("other-user", other)
