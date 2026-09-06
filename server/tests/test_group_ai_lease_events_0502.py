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
from contextlib import contextmanager
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
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import dialect as _dialect  # noqa: E402
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


def test_lease_admission_rejected_snapshot_resolves_provider_identity(monkeypatch):
    """T0004 SS5/SS23(2) rev5 finding: requested_token_id must be present (NULL, since
    no token is ever minted before a lease is acquired), and both sides' provider
    identity must be reconstructible -- the requesting side from its own resolved
    provider_id, the blocking side one hop further via the blocking token's stored
    provider_id (0164 DB0005 tokens.provider_id), never left silently absent."""
    monkeypatch.setattr(
        admission, "resolve_pinned_provider_name",
        lambda project_id, provider_id: {"prov_req": "Requester Provider", "prov_block": "Blocker Provider"}.get(provider_id),
    )
    monkeypatch.setattr(
        admission.db_tokens, "get_by_id",
        lambda token_id: {"token_id": token_id, "provider_id": "prov_block"} if token_id == "TOKEN-A" else None,
    )
    active = {
        "run_id": "RUN-A", "token_id": "TOKEN-A", "chain_id": "RUN-A",
        "action_scope": "new", "worker_identity": "WORKER-A", "state": "active",
        "generation": 1, "acquired_at": "2026-09-01T17:20:00+00:00",
        "heartbeat_at": "2026-09-01T17:24:00+00:00", "expires_at": "2026-09-01T21:29:00+00:00",
    }
    admission._record_lease_admission_rejected(
        group_id=GROUP, project_id=PROJECT, doc_ref=f"{GROUP}.0001-B", action_scope="new",
        chain_id="RUN-B", issued_to="WORKER-B", provider_id="prov_req",
        active=active, handoff_allowed=False, admission_stage="pre_acquire",
    )
    ev = db_lease_events.list_for_group(GROUP)[0]
    assert ev["requested"]["requested_token_id"] is None
    assert ev["requested"]["requested_provider_id"] == "prov_req"
    assert ev["requested"]["requested_provider_name"] == "Requester Provider"
    assert ev["blocking"]["blocking_provider_id"] == "prov_block"
    assert ev["blocking"]["blocking_provider_name"] == "Blocker Provider"
    assert ev["blocking"]["lease_owner_identity"] == "WORKER-A"


def test_lease_admission_rejected_snapshot_leaves_provider_name_null_when_unresolvable():
    """No provider_id / no blocking token -> both provider_name fields stay NULL
    rather than guessing (T0004 SS6)."""
    active = {
        "run_id": "RUN-A", "token_id": None, "chain_id": "RUN-A",
        "action_scope": "new", "worker_identity": "WORKER-A", "state": "acquiring",
        "generation": 1, "acquired_at": "2026-09-01T17:20:00+00:00",
        "heartbeat_at": "2026-09-01T17:24:00+00:00", "expires_at": "2026-09-01T21:29:00+00:00",
    }
    admission._record_lease_admission_rejected(
        group_id=GROUP, project_id=PROJECT, doc_ref=f"{GROUP}.0001-B", action_scope="new",
        chain_id="RUN-B", issued_to="WORKER-B", provider_id=None,
        active=active, handoff_allowed=False, admission_stage="pre_acquire",
    )
    ev = db_lease_events.list_for_group(GROUP)[0]
    assert ev["requested"]["requested_token_id"] is None
    assert ev["requested"]["requested_provider_name"] is None
    assert ev["blocking"]["blocking_provider_id"] is None
    assert ev["blocking"]["blocking_provider_name"] is None
    assert ev["blocking"]["lease_owner_identity"] == "WORKER-A"


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


# ── SS17.7 / SS10 failure isolation on the transactional (PostgreSQL) acquire path ───
#
# The tests above drive both stores' `_memory` fallback, where an append failure is a
# plain Python exception. Production's acquire() takes a different shape: it mutates
# group_ai_leases inside `store.transaction()` and appends the forensic event on that
# same connection. Under PostgreSQL a failed statement aborts the WHOLE transaction --
# every later statement and the COMMIT itself then fail -- so merely catching the
# append's exception would still roll the lease acquisition back and surface a 500 on
# an admission that had already succeeded (T0004 SS10 / SS23(6)). These tests run the real
# FlowGateStore.transaction() against a backend that reproduces that abort semantics.


class _FakeTxn:
    def __init__(self, db: "_PgLikeDB"):
        self._db = db
        self._row = None

    def execute(self, sql, params=None):
        self._row = self._db.run(sql, params or [], in_txn=True)
        return self

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class _PgLikeDB:
    """sqloader-shaped backend with PostgreSQL's "a failed statement aborts the
    transaction" behaviour, and just enough table emulation for acquire().

    ``commit_reports_failure`` covers both endings the abort has. Verified against the
    live PostgreSQL backend (192.168.0.250 flowgate, inside a rolled-back transaction):
    a failed statement leaves the session in 25P02, and psycopg2's ``commit()`` on that
    session RETURNS SUCCESSFULLY because the server turns COMMIT on an aborted
    transaction into ROLLBACK -- so sqloader's PostgreSQLTransaction.__exit__ raises
    nothing and the lease INSERT is discarded *silently*. False models that (the real
    production shape); True models the driver reporting the failure instead. The
    durable assertion below is the same either way: the lease must still be there.
    """

    db_type = _dialect.POSTGRESQL

    def __init__(self, *, event_writes_fail: bool, commit_reports_failure: bool = False):
        self.event_writes_fail = event_writes_fail
        self.commit_reports_failure = commit_reports_failure
        self.leases: dict[str, dict] = {}
        self.events: list[dict] = []
        self.log: list[tuple[str, str]] = []   # (in_txn|autocommit, sql)
        self.aborted = False

    # -- statement dispatch (matched on table name; the SQL itself is dialect-translated)
    def run(self, sql, params, *, in_txn: bool):
        self.log.append(("in_txn" if in_txn else "autocommit", sql))
        if in_txn and self.aborted:
            raise RuntimeError("current transaction is aborted, commands ignored")
        if "group_ai_lease_events" in sql:
            if self.event_writes_fail:
                if in_txn:
                    self.aborted = True          # exactly what psycopg2 does
                raise RuntimeError("relation \"group_ai_lease_events\" does not exist")
            if sql.lstrip().upper().startswith("INSERT"):
                self.events.append({"sql": sql, "params": list(params)})
                return None
            return {"event_id": params[0] if params else None}
        if "group_ai_leases" in sql:
            head = sql.lstrip().upper()
            if head.startswith("INSERT"):
                (group_id, project_id, run_id, chain_id, action_scope, worker_identity,
                 acquired_at, heartbeat_at, expires_at, updated_at) = params
                self.leases.setdefault(group_id, {
                    "group_id": group_id, "project_id": project_id, "run_id": run_id,
                    "chain_id": chain_id, "token_id": None, "action_scope": action_scope,
                    "worker_identity": worker_identity, "state": "acquiring", "generation": 1,
                    "acquired_at": acquired_at, "heartbeat_at": heartbeat_at,
                    "expires_at": expires_at, "updated_at": updated_at,
                })
                return None
            if head.startswith("SELECT 1"):
                return None                       # no run_id collision
            if head.startswith("SELECT"):
                return self.leases.get(params[0]) if params else None
            if head.startswith("DELETE"):
                self.leases.pop(params[0], None)
                return None
        raise AssertionError(f"unexpected statement in test backend: {sql}")

    # -- FlowGateStore's non-transactional path
    def execute(self, sql, params=None):
        self.run(sql, params or [], in_txn=False)
        return self

    def fetch_one(self, sql, params=None):
        return self.run(sql, params or [], in_txn=False)

    @contextmanager
    def begin_transaction(self):
        snapshot = {gid: dict(row) for gid, row in self.leases.items()}
        self.aborted = False
        try:
            yield _FakeTxn(self)
        except BaseException:
            self.leases = snapshot
            raise
        if self.aborted:
            # COMMIT on an aborted PostgreSQL transaction is executed as ROLLBACK: the
            # writes are gone whether or not the driver reports that back to the caller.
            self.leases = snapshot
            if self.commit_reports_failure:
                raise RuntimeError("current transaction is aborted, commands ignored")


@pytest.fixture
def pg_like(monkeypatch):
    """Point both lease stores at a live FlowGateStore over the PG-like backend."""
    def _make(*, event_writes_fail: bool, commit_reports_failure: bool = False) -> tuple[_PgLikeDB, object]:
        db = _PgLikeDB(event_writes_fail=event_writes_fail,
                       commit_reports_failure=commit_reports_failure)
        store = db_connection.FlowGateStore.__new__(db_connection.FlowGateStore)
        store._db, store._sq = db, None
        for module in (db_leases, db_lease_events):
            monkeypatch.setattr(module, "_using_memory", lambda: False)
            monkeypatch.setattr(module, "get_store", lambda store=store: store)
        return db, store
    return _make


@pytest.mark.parametrize("commit_reports_failure", [False, True])
def test_acquire_survives_a_forensic_write_that_aborts_the_transaction(pg_like, commit_reports_failure):
    db, _ = pg_like(event_writes_fail=True, commit_reports_failure=commit_reports_failure)

    lease = db_leases.acquire(
        group_id=GROUP, project_id=PROJECT, run_id="R1", chain_id="c1",
        action_scope="new", worker_identity="w1",
    )

    # The admission succeeded and stayed committed: the event-store failure neither
    # raised out of acquire() nor rolled the lease row back with the transaction.
    assert lease is not None and lease["run_id"] == "R1"
    assert db.leases[GROUP]["run_id"] == "R1", "the lease INSERT was silently rolled back with the aborted transaction"
    # ...and it failed where it can do no harm: outside the transaction.
    event_writes = [entry for entry in db.log if "group_ai_lease_events" in entry[1]]
    assert event_writes and all(where == "autocommit" for where, _ in event_writes)


def test_acquire_appends_its_event_only_after_the_transaction_commits(pg_like):
    db, _ = pg_like(event_writes_fail=False)

    lease = db_leases.acquire(
        group_id=GROUP, project_id=PROJECT, run_id="R1", chain_id="c1",
        action_scope="new", worker_identity="w1",
    )

    assert lease is not None and lease["run_id"] == "R1"
    assert len(db.events) == 1
    assert "lease_acquired" in db.events[0]["params"]
    # The history is still written -- just on the post-commit connection, which is the
    # only placement where its failure cannot take the lease mutation with it.
    event_writes = [entry for entry in db.log if "group_ai_lease_events" in entry[1]]
    assert event_writes and all(where == "autocommit" for where, _ in event_writes)
    lease_writes = [i for i, (_, sql) in enumerate(db.log) if "group_ai_leases" in sql]
    assert max(lease_writes) < min(
        i for i, (_, sql) in enumerate(db.log) if "group_ai_lease_events" in sql
    )


def test_after_commit_queue_is_dropped_when_the_transaction_rolls_back(pg_like):
    db, store = pg_like(event_writes_fail=False)
    ran: list[str] = []

    with pytest.raises(RuntimeError, match="boom"):
        with store.transaction():
            assert db_connection.after_commit(lambda: ran.append("x")) is True
            raise RuntimeError("boom")

    # The write the callback described was rolled back with the transaction, so
    # recording it would fabricate history (T0004 SS7).
    assert ran == []
    assert db_connection.in_transaction() is False
    assert db_connection.after_commit(lambda: ran.append("y")) is False


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
