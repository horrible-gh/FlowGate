# api/v1/events/publisher.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
import asyncio
import logging

_log = logging.getLogger(__name__)

@dataclass
class FlowEvent:
    event_type: str                    # EventType enum value
    payload: dict[str, Any]            # Per-event data
    audience: str                      # Target user_id ("*" = broadcast, unused)
    project: Optional[str] = None      # Routing meta
    group_id: Optional[str] = None
    doc_id: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

# Subscriber registry: {user_id: [asyncio.Queue, ...]}  (multi-tab fanout)
_subscribers: dict[str, list[asyncio.Queue]] = {}
_lock = asyncio.Lock()

# Reference to the server's main event loop, captured when an SSE client subscribes
# (subscribe() always runs on the main loop). Used by broadcast_event_threadsafe to
# deliver events from sync route handlers, which FastAPI runs in a worker thread
# without access to the loop that owns the subscriber queues.
_main_loop: Optional["asyncio.AbstractEventLoop"] = None


def _offer(q: asyncio.Queue, event: FlowEvent) -> bool:
    """Enqueue *event* without ever blocking. True when it was queued.

    This is the whole of the 0279 P3-9 fix. Both publish paths iterate the
    subscriber queues **while holding ``_lock``**, and the queues are bounded
    (``maxsize=100``). The previous ``await q.put(event)`` therefore parked on the
    first full queue *with the lock still held*, which froze every other publish,
    every ``subscribe`` and every ``unsubscribe`` process-wide — permanently, since
    the only thing that could drain that queue was an SSE client that had already
    stopped reading. One abandoned browser tab could wedge event delivery for
    everyone, and because ``_lock`` also guards subscribe/unsubscribe, new tabs hung
    on connect instead of recovering. That is the "로딩중에서 멈춘다" shape R0001 asked
    about, arriving from the event path rather than from the DB or the filesystem.

    A queue that is full means the subscriber is not draining, so something must be
    dropped. We drop the **oldest** event to make room for the newest: these events
    are refresh signals, a client this far behind is going to re-sync on its next
    full fetch anyway, and the newest signal is the one that reflects current state.
    Dropping the newest instead would leave the client pinned to stale data.
    """
    try:
        q.put_nowait(event)
        return True
    except asyncio.QueueFull:
        pass
    try:
        q.get_nowait()
    except asyncio.QueueEmpty:  # pragma: no cover - drained between the two calls
        pass
    try:
        q.put_nowait(event)
        _log.warning(
            "SSE subscriber queue full; dropped oldest event to admit %s",
            event.event_type,
        )
        return True
    except asyncio.QueueFull:  # pragma: no cover - refilled between the two calls
        _log.warning("SSE subscriber queue full; dropped event %s", event.event_type)
        return False


async def publish_event(event: FlowEvent) -> int:
    """Transport-agnostic publish — push to all subscriber queues for the given user_id.

    Returns the number of queues the event was delivered to (0 = no active
    subscribers for that audience). Existing callers ignore the return value.
    """
    delivered = 0
    async with _lock:
        queues = _subscribers.get(event.audience, [])
        for q in queues:
            if _offer(q, event):
                delivered += 1
    return delivered

async def subscribe(user_id: str) -> asyncio.Queue:
    """Called when SSE route connects — create and register a new queue"""
    global _main_loop
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    async with _lock:
        _subscribers.setdefault(user_id, []).append(q)
    return q

async def unsubscribe(user_id: str, q: asyncio.Queue) -> None:
    """Called when SSE route disconnects — unregister that queue"""
    async with _lock:
        queues = _subscribers.get(user_id, [])
        if q in queues:
            queues.remove(q)
        if not queues:
            _subscribers.pop(user_id, None)


async def broadcast_event(event: FlowEvent) -> int:
    """Publish an event to all subscribers (project-related broadcast)."""
    delivered = 0
    async with _lock:
        for queues in list(_subscribers.values()):
            for q in queues:
                if _offer(q, event):
                    delivered += 1
    return delivered


def publish_event_threadsafe(event: FlowEvent) -> int:
    """Audience-scoped publish from a synchronous context (a sync FastAPI route running
    in a worker thread). Mirrors broadcast_event_threadsafe but routes to event.audience
    only, so a worker-registered Q notifies just its PM rather than every connected user.

    FastAPI runs non-async path operations in a threadpool, where the queues' owning loop
    is not the running loop, and asyncio.get_event_loop() raises in a non-main thread. The
    previous q_service implementation (get_event_loop + run_until_complete) therefore raised
    and was swallowed, so the qna_q_registered event was never emitted — the F5-only
    symptom in 0059 B0001. Schedule the coroutine onto the captured main loop instead so
    delivery actually reaches the subscriber. Returns the delivered-queue count, -1 if
    scheduled from an async context, 0 if there is no subscriber / no usable loop.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    try:
        if running is not None:
            asyncio.ensure_future(publish_event(event))
            return -1
        elif _main_loop is not None and _main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(publish_event(event), _main_loop)
            return future.result(timeout=2.0)
        else:
            return asyncio.run(publish_event(event))
    except Exception:
        # SSE delivery is best-effort; never let it break the request path.
        return 0


def broadcast_event_threadsafe(event: FlowEvent) -> int:
    """Broadcast from a synchronous context (e.g. a sync FastAPI route running in a
    worker thread). Wait until the event is queued before returning.

    FastAPI runs non-async path operations in a threadpool, where the queues' owning
    loop is not the running loop. Schedule the coroutine onto the captured main loop
    so delivery actually reaches subscribers. A return value of 0 means there were no
    active subscribers; -1 means delivery was scheduled from an async context.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    try:
        if running is not None:
            asyncio.ensure_future(broadcast_event(event))
            return -1
        elif _main_loop is not None and _main_loop.is_running():
            future = asyncio.run_coroutine_threadsafe(broadcast_event(event), _main_loop)
            return future.result(timeout=2.0)
        else:
            return asyncio.run(broadcast_event(event))
    except Exception:
        # SSE delivery is best-effort; never let it break the request path.
        return 0
