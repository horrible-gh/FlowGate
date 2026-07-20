"""0279 T0009 — regression guard for the four unbounded/blocking resources (NR0003 P3-9…P3-12).

Why this test exists
--------------------
0006-TR and 0008-TR removed every *direct* event-loop block from the request path.
What remained were four places where a resource had no ceiling: a queue that blocked
when full, a query with no LIMIT, a cache with no eviction, and a cache that pinned a
failure forever. None of them misbehave on a small, fresh instance — all of them
degrade as the instance accumulates history, which is why R0001 reported a stall that
appeared *gradually* ("초반보다는 많이 빨라지긴 했지만 간혹 …멈춢떄가 있다") rather than
from day one.

The rule this guard encodes:

    A resource on the request path must be bounded. A queue must not block when full,
    a list query must carry a LIMIT, a cache must evict, and a failed load must not be
    cached as if it were a result.

P3-9 is checked behaviourally (the deadlock is reproducible in-process and the fix is
the only one with real semantic risk). The other three are checked statically against
the source, in the same no-DB / no-network style as
``test_event_loop_blocking_0279.py`` — these guard against the *pattern* silently
coming back, not against a specific runtime value.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_FG = _SERVER_DIR / "modules" / "flow_gate"
_PUBLISHER = _FG / "api" / "v1" / "events" / "publisher.py"
_DASHBOARD = _FG / "services" / "dashboard_service.py"
_CONTENT_SEARCH = _FG / "services" / "content_search_service.py"
_TYPE_LABELS = _FG / "db" / "document_type_labels.py"


def _load_publisher():
    """Import publisher.py straight off disk.

    Its imports are all stdlib (asyncio/dataclasses/datetime/typing/logging), so this
    needs no package context, no DB and no app instance — same isolation guarantee the
    sibling static guards give.
    """
    spec = importlib.util.spec_from_file_location("_pub_under_test", _PUBLISHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# P3-9 — SSE publisher must never block on a full subscriber queue
# --------------------------------------------------------------------------

def test_publish_to_full_queue_does_not_block():
    """A subscriber that stopped draining must not stall the publisher.

    Pre-fix this deadlocked: ``await q.put(event)`` parked forever on a full queue
    (maxsize=100) *while holding ``_lock``*, so every later publish, subscribe and
    unsubscribe blocked behind it process-wide. One abandoned browser tab was enough.
    """
    pub = _load_publisher()

    async def scenario():
        q = asyncio.Queue(maxsize=100)
        for i in range(100):
            q.put_nowait(f"old{i}")
        assert q.full()
        pub._subscribers.clear()
        pub._subscribers["u1"] = [q]
        event = pub.FlowEvent(event_type="doc_created", payload={}, audience="u1")
        # The timeout IS the assertion: pre-fix this never returns.
        delivered = await asyncio.wait_for(pub.publish_event(event), timeout=5.0)
        return q, event, delivered

    q, event, delivered = asyncio.run(scenario())
    assert delivered == 1
    drained = [q.get_nowait() for _ in range(q.qsize())]
    assert len(drained) == 100, "queue must stay at its bound"
    assert drained[-1] is event, "the newest event must be the one retained"
    assert "old0" not in drained, "the oldest event is the one dropped, not the newest"


def test_full_queue_does_not_wedge_the_subscriber_lock():
    """After a full-queue publish, subscribe/unsubscribe must still be responsive.

    ``_lock`` guards the subscriber registry as well as the publish loop, so a publisher
    parked while holding it also stopped *new* SSE clients from connecting — the failure
    compounded instead of staying isolated to the one bad subscriber.
    """
    pub = _load_publisher()

    async def scenario():
        q = asyncio.Queue(maxsize=100)
        for i in range(100):
            q.put_nowait(i)
        pub._subscribers.clear()
        pub._subscribers["stuck"] = [q]
        await asyncio.wait_for(pub.publish_event(pub.FlowEvent("x", {}, "stuck")), timeout=5.0)
        new_q = await asyncio.wait_for(pub.subscribe("fresh"), timeout=5.0)
        await asyncio.wait_for(pub.unsubscribe("fresh", new_q), timeout=5.0)
        assert not pub._lock.locked(), "_lock must be released"

    asyncio.run(scenario())


def test_broadcast_reaches_healthy_subscribers_past_a_wedged_one():
    """A single non-draining subscriber must not cost everyone else their events."""
    pub = _load_publisher()

    async def scenario():
        stuck = asyncio.Queue(maxsize=100)
        for i in range(100):
            stuck.put_nowait(i)
        healthy = asyncio.Queue(maxsize=100)
        pub._subscribers.clear()
        pub._subscribers["stuck"] = [stuck]
        pub._subscribers["healthy"] = [healthy]
        await asyncio.wait_for(pub.broadcast_event(pub.FlowEvent("z", {}, "*")), timeout=5.0)
        assert healthy.qsize() == 1, "the healthy subscriber must still receive the event"

    asyncio.run(scenario())


def test_normal_delivery_is_unchanged():
    """The fix must not alter the healthy path: same routing, same delivered counts."""
    pub = _load_publisher()

    async def scenario():
        a, b = asyncio.Queue(maxsize=100), asyncio.Queue(maxsize=100)
        pub._subscribers.clear()
        pub._subscribers["u1"] = [a]
        pub._subscribers["u2"] = [b]
        assert await pub.publish_event(pub.FlowEvent("x", {}, "u1")) == 1
        assert a.qsize() == 1 and b.qsize() == 0, "audience scoping must still hold"
        assert await pub.broadcast_event(pub.FlowEvent("y", {}, "*")) == 2
        assert a.qsize() == 2 and b.qsize() == 1

    asyncio.run(scenario())


def test_publisher_has_no_blocking_put():
    """No ``await q.put(...)`` may return to publisher.py.

    ``put_nowait`` is the whole point; an ``await q.put`` reintroduces the deadlock.
    """
    tree = ast.parse(_source(_PUBLISHER))
    for node in ast.walk(tree):
        if isinstance(node, ast.Await):
            call = node.value
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "put"
            ):
                pytest.fail(
                    f"publisher.py:{node.lineno}: `await q.put(...)` blocks when the "
                    "subscriber queue is full, and does so while holding _lock. "
                    "Use the non-blocking _offer() helper instead (0279 P3-9)."
                )


# --------------------------------------------------------------------------
# P3-10 — the dashboard event query must be bounded
# --------------------------------------------------------------------------

def test_dashboard_event_query_has_a_limit():
    """``_event_rows`` must not scan the whole workflow_events history.

    It runs on every dashboard load and every notification poll, joins twice, and every
    returned row is then JSON-parsed and normalized in Python — only for the caller to
    display the first ~10. Without a LIMIT that cost grows with the event log forever.
    """
    tree = ast.parse(_source(_DASHBOARD))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_event_rows"),
        None,
    )
    assert fn is not None, "_event_rows disappeared — update this guard"
    sql = " ".join(
        node.value for node in ast.walk(fn)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    assert "LIMIT" in sql.upper(), (
        "_event_rows must carry a LIMIT: it selects every matching workflow_event for "
        "the project and normalizes all of them in Python (0279 P3-10)."
    )


# --------------------------------------------------------------------------
# P3-11 — the search body cache must evict
# --------------------------------------------------------------------------

def test_content_search_cache_is_bounded():
    """``_CACHE`` must have a ceiling and an eviction call.

    A facet-less body search reads every candidate document, and each entry stores the
    body twice (original + lowercased), so an unbounded dict admits ~2x the whole corpus
    and never releases it.
    """
    src = _source(_CONTENT_SEARCH)
    assert "_CACHE_MAX_ENTRIES" in src, "the body cache must declare a maximum size"
    assert "OrderedDict()" in src, "the body cache must be an OrderedDict to evict LRU-first"
    assert "popitem(last=False)" in src, (
        "the body cache must evict its least-recently-used entry once over the cap "
        "(0279 P3-11)."
    )


# --------------------------------------------------------------------------
# P3-12 — a failed label load must not be cached as a result
# --------------------------------------------------------------------------

def test_type_label_cache_does_not_store_empty_results():
    """An empty load must not be written to ``_cache``.

    ``_load_type_names_map`` returns {} when its query *raises*, so caching that value
    pinned every type to its bare type_code for the life of the process after one
    transient DB error.
    """
    tree = ast.parse(_source(_TYPE_LABELS))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "get_type_names_map"),
        None,
    )
    assert fn is not None, "get_type_names_map disappeared — update this guard"
    src = ast.unparse(fn)
    assert "if not loaded" in src, (
        "get_type_names_map must skip the cache write when the load came back empty, "
        "so a transient DB failure is retried instead of pinned (0279 P3-12)."
    )
    # And the anti-stampede lock must stay: the load belongs INSIDE `with _lock`.
    assert any(
        isinstance(n, ast.With) and any(
            isinstance(c, ast.Call) and getattr(c.func, "id", None) == "_load_type_names_map"
            for c in ast.walk(n)
        )
        for n in ast.walk(fn)
    ), (
        "the load must stay inside the double-checked `with _lock` block — moving it out "
        "makes every concurrent cold request issue its own duplicate query for no latency "
        "gain (measured: 8 concurrent lookups, 0.30s either way, 1 query vs 8)."
    )
