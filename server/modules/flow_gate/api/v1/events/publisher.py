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

# Per-queue "interest project" (0291 D0005 §3-2). Kept as a side table keyed by the
# queue object rather than folded into _subscribers, so the {user_id: [queue]} shape
# every existing caller and test relies on is unchanged. A queue missing from this map
# (or mapped to None) is a subscriber that did not declare an interest project — an
# older client — and keeps receiving everything (§3-1 rule 4).
_subscriber_projects: dict[asyncio.Queue, Optional[str]] = {}

# Broadcast routing counters (0291 D0005 §3-1). The two fallback rules are meant to be
# temporary: rule 3 (event carries no project) and rule 4 (subscriber declared no
# project) both degrade to "deliver to everyone", which is exactly the fanout this
# change exists to remove. Without a number attached to them there is no way to tell
# whether the fallbacks are a thin safety net or where all the traffic still goes, so
# count them and expose the counts for the measurement task (NR0003 4-8).
_routing_stats: dict[str, int] = {
    "broadcasts": 0,             # broadcast_event calls
    "delivered": 0,              # queue deliveries made
    "skipped_other_project": 0,  # queue deliveries suppressed by project routing
    "events_without_project": 0, # broadcasts that hit fallback rule 3
    "fallback_deliveries": 0,    # deliveries that only happened because of rule 3/4
}

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

async def subscribe(user_id: str, project: Optional[str] = None) -> asyncio.Queue:
    """Called when SSE route connects — create and register a new queue.

    *project* is the project the connecting screen is currently looking at (0291
    D0005 §3-2). It is recorded once, at connect time; a user switching projects
    reconnects, which the client already does for token rotation and dropped
    streams. Omitting it (older clients) keeps the pre-0291 receive-everything
    behaviour.
    """
    global _main_loop
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        pass
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    async with _lock:
        _subscribers.setdefault(user_id, []).append(q)
        _subscriber_projects[q] = project or None
    return q

async def unsubscribe(user_id: str, q: asyncio.Queue) -> None:
    """Called when SSE route disconnects — unregister that queue"""
    async with _lock:
        queues = _subscribers.get(user_id, [])
        if q in queues:
            queues.remove(q)
        if not queues:
            _subscribers.pop(user_id, None)
        _subscriber_projects.pop(q, None)


def _should_deliver(event_project: Optional[str], sub_project: Optional[str]) -> bool:
    """Delivery decision for one (broadcast event, subscriber) pair — D0005 §3-1.

    Both "unknown" cases deliver. An event with no project (rule 3) and a subscriber
    that never declared one (rule 4) are situations where we have no basis to decide,
    and silently dropping a refresh signal turns a performance fix into a correctness
    regression ("sometimes the view just doesn't update"), which is far worse than the
    fanout we are trying to remove. Both are counted so the fallbacks can be retired
    on evidence rather than on hope.
    """
    if event_project is None:
        return True
    if sub_project is None:
        return True
    return sub_project == event_project


async def broadcast_event(event: FlowEvent) -> int:
    """Publish an event to every subscriber *interested in the event's project*.

    Before 0291 this pushed to every connected queue unconditionally, so one document
    write re-queried the entire screen of every logged-in user regardless of which
    project they were looking at — the write-amplification NR0003 finding 1 measured.
    Now the event's ``project`` is matched against each subscriber's declared interest
    project and unrelated screens are skipped (D0005 §3-1 rule 2).

    User-targeted delivery is *not* affected: that goes through publish_event, which
    keeps routing purely by audience (rule 1). Applying a project filter there would
    silently drop personal notifications addressed to a user who happens to be looking
    somewhere else.
    """
    delivered = 0
    skipped = 0
    fallback = 0
    event_project = event.project or None
    async with _lock:
        _routing_stats["broadcasts"] += 1
        if event_project is None:
            _routing_stats["events_without_project"] += 1
        for queues in list(_subscribers.values()):
            for q in queues:
                sub_project = _subscriber_projects.get(q)
                if not _should_deliver(event_project, sub_project):
                    skipped += 1
                    continue
                if _offer(q, event):
                    delivered += 1
                    if event_project is None or sub_project is None:
                        fallback += 1
        _routing_stats["delivered"] += delivered
        _routing_stats["skipped_other_project"] += skipped
        _routing_stats["fallback_deliveries"] += fallback
    if event_project is None:
        _log.debug(
            "broadcast %s has no project; delivered to all %d subscriber queue(s)",
            event.event_type, delivered,
        )
    return delivered


def get_routing_stats() -> dict[str, int]:
    """Snapshot of the broadcast routing counters (see ``_routing_stats``).

    Read-only copy — for the instrumentation/measurement task and for tests that
    assert the fallback paths are or are not being taken.
    """
    return dict(_routing_stats)


def reset_routing_stats() -> None:
    """Zero the routing counters (test/measurement-window helper)."""
    for key in _routing_stats:
        _routing_stats[key] = 0


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
