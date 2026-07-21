"""0291 T — SSE broadcast project routing (D0005 §3-1).

A document write used to push a refresh signal into every connected stream, so a
user looking at project B re-queried their whole screen because someone edited a
document in project A. These tests pin the routing rules that stop that, and —
just as important — the two fallbacks that keep it from turning into missed
updates.
"""
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

from modules.flow_gate.api.v1.events import publisher as pub  # noqa: E402
from modules.flow_gate.api.v1.events.publisher import (  # noqa: E402
    FlowEvent,
    broadcast_event,
    publish_event,
    subscribe,
)


@pytest.fixture(autouse=True)
def reset_registry():
    pub._subscribers.clear()
    pub._subscriber_projects.clear()
    pub.reset_routing_stats()
    yield
    pub._subscribers.clear()
    pub._subscriber_projects.clear()
    pub.reset_routing_stats()


def _event(project, event_type="document_explorer_refresh"):
    return FlowEvent(
        event_type=event_type, payload={}, audience="*", project=project
    )


@pytest.mark.asyncio
async def test_broadcast_reaches_only_the_matching_project():
    """The core of the change: unrelated screens are not woken up."""
    a = await subscribe("user-a", "proj_alpha")
    b = await subscribe("user-b", "proj_beta")

    delivered = await broadcast_event(_event("proj_alpha"))

    assert delivered == 1
    assert a.qsize() == 1
    assert b.qsize() == 0


@pytest.mark.asyncio
async def test_same_project_multi_tab_still_fans_out():
    """Two tabs on the same project both keep receiving — no regression."""
    t1 = await subscribe("user-a", "proj_alpha")
    t2 = await subscribe("user-a", "proj_alpha")
    t3 = await subscribe("user-b", "proj_alpha")

    delivered = await broadcast_event(_event("proj_alpha"))

    assert delivered == 3
    assert (t1.qsize(), t2.qsize(), t3.qsize()) == (1, 1, 1)


@pytest.mark.asyncio
async def test_event_without_project_still_reaches_everyone():
    """Fallback rule 3 — no project on the event means no basis to filter.

    Dropping it would be a silent missed refresh, which is a worse failure than
    the fanout being removed here.
    """
    a = await subscribe("user-a", "proj_alpha")
    b = await subscribe("user-b", "proj_beta")

    delivered = await broadcast_event(_event(None))

    assert delivered == 2
    assert a.qsize() == 1 and b.qsize() == 1
    stats = pub.get_routing_stats()
    assert stats["events_without_project"] == 1
    assert stats["fallback_deliveries"] == 2


@pytest.mark.asyncio
async def test_subscriber_without_project_still_receives_everything():
    """Fallback rule 4 — an older client that does not declare a project.

    The server can be deployed before the client without breaking open screens.
    """
    legacy = await subscribe("user-legacy")
    scoped = await subscribe("user-b", "proj_beta")

    delivered = await broadcast_event(_event("proj_alpha"))

    assert delivered == 1
    assert legacy.qsize() == 1
    assert scoped.qsize() == 0


@pytest.mark.asyncio
async def test_directly_injected_queue_is_treated_as_unscoped():
    """Queues registered outside subscribe() (existing tests do this) still work."""
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    pub._subscribers["raw"] = [q]

    assert await broadcast_event(_event("proj_alpha")) == 1
    assert q.qsize() == 1


@pytest.mark.asyncio
async def test_user_targeted_publish_ignores_project_routing():
    """Rule 1 — personal notifications are never project-filtered.

    A Q registered for a PM must reach them even while they are looking at a
    different project; filtering this path would drop the notification silently.
    """
    q = await subscribe("pm-user", "proj_alpha")

    delivered = await publish_event(
        FlowEvent(
            event_type="qna_q_registered",
            payload={},
            audience="pm-user",
            project="proj_beta",
        )
    )

    assert delivered == 1
    assert q.qsize() == 1


@pytest.mark.asyncio
async def test_unsubscribe_clears_the_interest_entry():
    """The side table must not outlive the queue, or it leaks per connection."""
    q = await subscribe("user-a", "proj_alpha")
    assert q in pub._subscriber_projects

    await pub.unsubscribe("user-a", q)

    assert q not in pub._subscriber_projects


@pytest.mark.asyncio
async def test_routing_stats_count_suppressed_deliveries():
    """The suppressed count is the measurable effect of this change (NR0003 4-8)."""
    await subscribe("user-a", "proj_alpha")
    await subscribe("user-b", "proj_beta")
    await subscribe("user-c", "proj_beta")

    await broadcast_event(_event("proj_alpha"))

    stats = pub.get_routing_stats()
    assert stats["broadcasts"] == 1
    assert stats["delivered"] == 1
    assert stats["skipped_other_project"] == 2
    assert stats["fallback_deliveries"] == 0
