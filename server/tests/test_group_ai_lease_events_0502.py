"""Append-only group AI lease/admission forensic history (flowgate.default.0502 T0004).

NR0003 v4's confirmed gap: `group_ai_leases` is a current-state table with no ownership
history, and a 409 `run_in_progress` admission reject happens before any
`ai_invoke_runs` row exists -- so the exact requested/blocking identity behind a past
incident like 0502's own B0001 could not be reconstructed after the fact. This file
pins the fix: `group_ai_lease_events` records one immutable row per lifecycle
transition (acquire / transfer / activate / handoff-begin / release / expiry-reclaim /
startup-reclaim / admission-reject), and a write failure there must never change an
admission or release decision (T0004 SS10).

Runs entirely through group_ai_leases' and group_ai_lease_events' own `_memory`
fallbacks (PYTEST_CURRENT_TEST forces `_using_memory()` True for both, same convention
as test_ai_invoke_lease_recovery_0401.py).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.db import group_ai_lease_events as db_lease_events  # noqa: E402
from modules.flow_gate.db import group_ai_leases as db_leases  # noqa: E402
from modules.flow_gate.services.ai_invoke import admission  # noqa: E402

GROUP = "flowgate.default.0502"
PROJECT = "flowgate"


@pytest.fixture(autouse=True)
def clean_state():
    db_leases._memory.clear()
    db_lease_events._memory.clear()
    yield
    db_leases._memory.clear()
    db_lease_events._memory.clear()


def _acquire(run_id="R1", chain_id=None, worker="w1"):
    return db_leases.acquire(
        group_id=GROUP, project_id=PROJECT, run_id=run_id,
        chain_id=chain_id or run_id, action_scope="new", worker_identity=worker,
    )


# ── SS17.2 normal acquire ────────────────────────────────────────────────────────────

def test_acquire_logs_lease_acquired_event():
    _acquire()
    events = db_lease_events.list_for_group(GROUP)
    assert [e["event_type"] for e in events] == ["lease_acquired"]
    assert events[0]["run_id"] == "R1"
    assert events[0]["lease_generation"] == 1
    assert events[0]["reason"] == "new_acquire"


def test_activate_logs_lease_activated_event():
    _acquire()
    db_leases.activate(GROUP, "R1", "TOK1", "new", "w1", 3600)
    events = db_lease_events.list_for_group(GROUP, event_type="lease_activated")
    assert len(events) == 1
    assert events[0]["token_id"] == "TOK1"
    assert events[0]["run_id"] == "R1"


# ── SS17.6 handoff / transfer ────────────────────────────────────────────────────────

def test_handoff_and_transfer_log_paired_events_with_before_identity():
    _acquire(run_id="R1", chain_id="CHAIN1")
    db_leases.activate(GROUP, "R1", "TOK1", "new", "w1", 3600)
    ok = db_leases.begin_handoff(GROUP, "R1")
    assert ok is True
    _acquire(run_id="R2", chain_id="CHAIN1", worker="w2")
    events = db_lease_events.list_for_group(GROUP)
    assert [e["event_type"] for e in events] == [
        "lease_acquired", "lease_activated", "lease_handoff_begin", "lease_transferred",
    ]
    transferred = events[-1]
    assert transferred["run_id"] == "R2"
    assert transferred["lease_generation"] == 2
    assert transferred["detail"]["before_run_id"] == "R1"
    assert transferred["reason"] == "handoff_transfer"


# ── SS17.3 normal release, vs a manual/forced one (SS7 distinguishes by `reason`) ────

def test_release_logs_lease_released_with_the_given_reason():
    _acquire()
    released = db_leases.release(GROUP, "R1", reason="normal_finish")
    assert released is True
    events = db_lease_events.list_for_group(GROUP, event_type="lease_released")
    assert len(events) == 1
    assert events[0]["reason"] == "normal_finish"


def test_release_defaults_reason_when_caller_passes_none():
    _acquire()
    db_leases.release(GROUP, "R1")
    events = db_lease_events.list_for_group(GROUP, event_type="lease_released")
    assert events[0]["reason"] == "released"


def test_force_release_group_lease_records_manual_reason():
    _acquire()
    # R1 was never registered in the in-process run registry, so is_run_live(R1) is
    # naturally False -- an orphaned lease, exactly like test_ai_invoke_lease_recovery
    # _0401's own force-release tests rely on.
    result = admission.force_release_group_lease(GROUP)
    assert result["released"] is True
    events = db_lease_events.list_for_group(GROUP, event_type="lease_released")
    assert len(events) == 1
    assert events[0]["reason"] == "manual_force_release"


# ── SS17.5 expiry reclaim vs SS17.4 startup reclaim -- distinct event types ─────────

def test_recover_expired_logs_lease_expired_reclaimed():
    _acquire()
    db_leases._memory[GROUP]["expires_at"] = "2000-01-01T00:00:00+00:00"
    count = db_leases.recover_expired(GROUP)
    assert count == 1
    events = db_lease_events.list_for_group(GROUP, event_type="lease_expired_reclaimed")
    assert len(events) == 1
    assert events[0]["reason"] == "ttl_expired"
    assert events[0]["run_id"] == "R1"


def test_reclaim_orphaned_logs_lease_startup_reclaimed():
    _acquire()
    db_leases._memory[GROUP]["acquired_at"] = "2000-01-01T00:00:00+00:00"
    victims = db_leases.reclaim_orphaned("2026-01-01T00:00:00+00:00")
    assert len(victims) == 1
    events = db_lease_events.list_for_group(GROUP, event_type="lease_startup_reclaimed")
    assert len(events) == 1
    assert events[0]["reason"] == "orphaned_by_restart"


def test_acquire_logs_lease_expired_reclaimed_when_it_reclaims_stale_lease_itself():
    """acquire() has its own expired-lease cleanup branch, separate from
    recover_expired() -- a new invoke arriving after the old owner's TTL lapsed,
    before any sweep ever runs recover_expired(), reclaims the stale row inline.
    That path must log the same lease_expired_reclaimed event recover_expired()
    does, or this lifecycle branch stays invisible to durable history."""
    _acquire(run_id="R1")
    db_leases._memory[GROUP]["expires_at"] = "2000-01-01T00:00:00+00:00"
    lease = _acquire(run_id="R2")
    assert lease is not None and lease["run_id"] == "R2"
    events = db_lease_events.list_for_group(GROUP)
    assert [e["event_type"] for e in events] == [
        "lease_acquired", "lease_expired_reclaimed", "lease_acquired",
    ]
    reclaimed = events[1]
    assert reclaimed["run_id"] == "R1"
    assert reclaimed["reason"] == "ttl_expired"


# ── T0004 rev2: acquire() hands back the blocker's own snapshot on conflict ─────────

def test_acquire_returns_the_blocking_snapshot_instead_of_none_when_blocked():
    """0502 T0004 rev2: a conflicting acquire() must not discard the row it already
    read -- the caller needs it to attribute a 409 without a second, racy query."""
    first = _acquire(run_id="R1", chain_id="C1", worker="w1")
    blocked = db_leases.acquire(
        group_id=GROUP, project_id=PROJECT, run_id="R2", chain_id="C2",
        action_scope="new", worker_identity="w2",
    )
    assert blocked is not None
    assert blocked["run_id"] == first["run_id"] == "R1"
    assert blocked["token_id"] == first["token_id"]
    assert blocked["worker_identity"] == "w1"


# ── SS14 read path time-range filter ─────────────────────────────────────────────────

def test_list_for_group_filters_by_since_and_until():
    # now_iso() has second-level resolution, so two appends made back-to-back can
    # legitimately land on the identical created_at -- stamp them explicitly,
    # seconds apart, so the since/until bounds under test are unambiguous.
    db_lease_events.append(event_type="lease_acquired", group_id=GROUP, run_id="R1",
                            detail={"seq": 1})
    db_lease_events._memory[0]["created_at"] = "2026-01-01T00:00:00+00:00"
    db_lease_events.append(event_type="lease_released", group_id=GROUP, run_id="R1",
                            detail={"seq": 2})
    db_lease_events._memory[1]["created_at"] = "2026-01-01T00:05:00+00:00"
    only_first = db_lease_events.list_for_group(GROUP, until="2026-01-01T00:02:00+00:00")
    assert [e["event_type"] for e in only_first] == ["lease_acquired"]
    only_second = db_lease_events.list_for_group(GROUP, since="2026-01-01T00:02:00+00:00")
    assert [e["event_type"] for e in only_second] == ["lease_released"]
    both = db_lease_events.list_for_group(
        GROUP, since="2026-01-01T00:00:00+00:00", until="2026-01-01T00:05:00+00:00",
    )
    assert [e["event_type"] for e in both] == ["lease_acquired", "lease_released"]


def test_lease_events_route_accepts_since_and_until_query_params(client):
    admission._record_lease_admission_rejected(
        group_id=GROUP, project_id=PROJECT, doc_ref="doc", action_scope="new",
        chain_id="c1", issued_to="w1", provider_id=None,
        active={"run_id": "RUN-A", "token_id": "TOKEN-A"}, handoff_allowed=False,
        admission_stage="pre_acquire",
    )
    stamp = db_lease_events.list_for_group(GROUP)[0]["created_at"]
    resp = client.get(
        "/api/v1/ai-invoke/lease-events",
        params={"group_id": GROUP, "since": "2000-01-01T00:00:00+00:00", "until": stamp},
        headers=_auth(),
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    resp_excluded = client.get(
        "/api/v1/ai-invoke/lease-events",
        params={"group_id": GROUP, "since": "2999-01-01T00:00:00+00:00"},
        headers=_auth(),
    )
    assert resp_excluded.json()["items"] == []


def test_expired_and_startup_reclaim_use_different_event_types():
    """SS7's hard requirement: normal release must never be confused with a
    reclaim/forced cleanup, and the two reclaim paths must stay distinguishable
    from each other too."""
    _acquire(run_id="R1")
    db_leases._memory[GROUP]["expires_at"] = "2000-01-01T00:00:00+00:00"
    db_leases.recover_expired(GROUP)
    _acquire(run_id="R2")
    db_leases._memory[GROUP]["acquired_at"] = "2000-01-01T00:00:00+00:00"
    db_leases.reclaim_orphaned("2026-01-01T00:00:00+00:00")
    types = {e["event_type"] for e in db_lease_events.list_for_group(GROUP)}
    assert "lease_expired_reclaimed" in types
    assert "lease_startup_reclaimed" in types
    assert types.isdisjoint({"lease_released"})


# ── SS17.1 / SS18: the 0502 incident shape -- admission reject snapshot ──────────────

def test_lease_admission_rejected_captures_requested_and_blocking_identity():
    """T0004 SS18's own regression shape: an existing lease (RUN-A/TOKEN-A/WORKER-A)
    blocks a new invoke (WORKER-B) against B0001. After the fact, the requested and
    blocking identities must both be recoverable from durable history alone."""
    active = {
        "run_id": "RUN-A", "token_id": "TOKEN-A", "chain_id": "RUN-A",
        "action_scope": "new", "worker_identity": "WORKER-A", "state": "active",
        "generation": 1, "acquired_at": "2026-09-01T17:20:00+00:00",
        "heartbeat_at": "2026-09-01T17:24:00+00:00", "expires_at": "2026-09-01T21:29:00+00:00",
    }
    admission._record_lease_admission_rejected(
        group_id=GROUP, project_id=PROJECT, doc_ref=f"{GROUP}.0001-B", action_scope="new",
        chain_id="RUN-B", issued_to="WORKER-B", provider_id="prov_1",
        active=active, handoff_allowed=False, admission_stage="pre_acquire",
    )
    events = db_lease_events.list_for_group(GROUP)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "lease_admission_rejected"
    assert ev["reason"] == "group_lease_active"
    assert ev["requested"]["requested_worker_id"] == "WORKER-B"
    assert ev["requested"]["doc_ref"] == f"{GROUP}.0001-B"
    assert ev["requested"]["requested_chain_id"] == "RUN-B"
    assert ev["blocking"]["blocking_run_id"] == "RUN-A"
    assert ev["blocking"]["blocking_token_id"] == "TOKEN-A"
    assert ev["blocking"]["blocking_worker_id"] == "WORKER-A"
    assert ev["detail"]["admission_stage"] == "pre_acquire"
    assert ev["detail"]["handoff_allowed"] is False
    # No ai_invoke_runs row was ever implied by this snapshot -- it carries no run_id
    # of its own for the REQUESTED side, exactly because admission never reached one.
    assert ev["run_id"] is None


def test_lease_admission_rejected_acquire_race_captures_the_requested_run_id():
    """T0004 SS5/SS23(1)-(2): unlike pre_acquire, the acquire_race stage fires AFTER
    start_run already minted a run_id for the losing candidate (db_group_ai_leases
    .acquire()'s own RunIdCollision-retry counterpart) -- that identity must survive
    into the durable event, both in the requested snapshot and the top-level
    correlation column list_for_group's run_id filter reads."""
    active = {
        "run_id": "RUN-A", "token_id": "TOKEN-A", "chain_id": "RUN-A",
        "action_scope": "new", "worker_identity": "WORKER-A", "state": "active",
        "generation": 1, "acquired_at": "2026-09-05T17:20:00+00:00",
        "heartbeat_at": "2026-09-05T17:24:00+00:00", "expires_at": "2026-09-05T21:29:00+00:00",
    }
    admission._record_lease_admission_rejected(
        group_id=GROUP, project_id=PROJECT, doc_ref=f"{GROUP}.0001-B", action_scope="new",
        chain_id="RUN-B", issued_to="WORKER-B", provider_id="prov_1",
        active=active, handoff_allowed=False, admission_stage="acquire_race",
        requested_run_id="RUN-B",
    )
    events = db_lease_events.list_for_group(GROUP)
    assert len(events) == 1
    ev = events[0]
    assert ev["detail"]["admission_stage"] == "acquire_race"
    # The requested run_id must be reconstructible both from the JSON snapshot...
    assert ev["requested"]["requested_run_id"] == "RUN-B"
    # ...and from the event's own correlation column, so list_for_group(run_id=...)
    # finds this rejection without unpacking the snapshot.
    assert ev["run_id"] == "RUN-B"
    by_run_id = db_lease_events.list_for_group(GROUP, run_id="RUN-B")
    assert len(by_run_id) == 1 and by_run_id[0]["event_id"] == ev["event_id"]
    # Blocking identity (the OTHER run) is unaffected by the requested-side fix.
    assert ev["blocking"]["blocking_run_id"] == "RUN-A"


def test_start_run_calls_the_forensic_recorder_before_both_run_in_progress_raises():
    """Structural pin (mirrors the existing RunIdCollision retry-shape test): both
    409 run_in_progress sites in start_run must record the forensic snapshot BEFORE
    raising, and neither may skip it."""
    import inspect

    source = inspect.getsource(admission.start_run)
    assert source.count("_record_lease_admission_rejected(") == 2
    assert source.count('"run_in_progress"') == 2
    first_call = source.index("_record_lease_admission_rejected(")
    first_raise = source.index('"run_in_progress"')
    assert first_call < first_raise


# ── SS17.7 forensic write failure isolation ──────────────────────────────────────────

def test_admission_rejected_recorder_swallows_a_forensic_write_failure(monkeypatch):
    monkeypatch.setattr(
        db_lease_events, "append",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    # Must not raise -- the caller's own 409 must still be the only thing that fires.
    admission._record_lease_admission_rejected(
        group_id=GROUP, project_id=PROJECT, doc_ref="doc", action_scope="new",
        chain_id="c1", issued_to="w1", provider_id=None,
        active={"run_id": "r1"}, handoff_allowed=False, admission_stage="pre_acquire",
    )
    assert db_lease_events.list_for_group(GROUP) == []


def test_acquire_and_release_succeed_even_if_forensic_append_fails(monkeypatch):
    monkeypatch.setattr(
        db_lease_events, "append",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    lease = _acquire()
    assert lease is not None and lease["run_id"] == "R1"
    released = db_leases.release(GROUP, "R1")
    assert released is True


# ── SS14 read path: GET /api/v1/ai-invoke/lease-events (admin-only) ────────────────

@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_1", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    return TestClient(app, raise_server_exceptions=False)


def _auth():
    return {"Authorization": "Bearer tok"}


def test_lease_events_route_returns_group_history_for_admin(client):
    admission._record_lease_admission_rejected(
        group_id=GROUP, project_id=PROJECT, doc_ref="doc", action_scope="new",
        chain_id="c1", issued_to="w1", provider_id=None,
        active={"run_id": "RUN-A", "token_id": "TOKEN-A"}, handoff_allowed=False,
        admission_stage="pre_acquire",
    )
    resp = client.get("/api/v1/ai-invoke/lease-events", params={"group_id": GROUP}, headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["group_id"] == GROUP
    assert len(body["items"]) == 1
    assert body["items"][0]["event_type"] == "lease_admission_rejected"


def test_lease_events_route_rejects_non_admin_even_with_document_read(client, monkeypatch):
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_1", "is_admin": False},
    )
    monkeypatch.setattr(ai_invoke_routes, "has_permission", lambda *a, **k: True)
    resp = client.get("/api/v1/ai-invoke/lease-events", params={"group_id": GROUP}, headers=_auth())
    assert resp.status_code == 403


def test_lease_events_route_is_declared_ahead_of_the_run_id_route():
    source = Path(ai_invoke_routes.__file__).read_text(encoding="utf-8")
    assert source.index('@router.get("/lease-events")') < source.index('@router.get("/{run_id}")')
