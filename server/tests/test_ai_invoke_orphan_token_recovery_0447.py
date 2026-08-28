"""Orphan-token revoke on the startup lease sweep (0447 T0007).

flowgate.default.0447.0006-TR's own §4 named this gap: ``startup_recover_leases()``
already reclaims a dead ``group_ai_leases`` row and gives its run an
``orphaned_by_restart`` end record (0401 NR0003 / T0004), but it never touches the
``token_id`` the lease was issued with. An unused single-use token then survives to
its own 24h TTL and can be replayed back into the same group. This file pins:

  * ``ai_invoke_service._reclaim_orphan_lease_token`` -- best-effort, idempotent
    revoke of an active victim token, wired independently of the end-record write
    inside ``startup_recover_leases()``'s victim loop.
  * The persist-then-crash scenario named in T0007 item 3-2: a victim whose run
    already has a normal end record (the process died after persisting
    ``ai_invoke_runs`` but before releasing the lease) must still get its token
    revoked, without disturbing the existing end record.
  * consumed/revoked/no-token victims are safe no-ops -- no ``token_service.revoke``
    call and no state change.
  * ``token_service.revoke()``'s own idempotency: revoking an already-revoked
    token twice raises no error and writes exactly one ``token_revoked`` event;
    revoking a token_id that never existed still 404s.
  * ``token_service.revoke()``'s idempotency also holds when two callers race
    across the query-then-revoke window at the same instant (0447 T0007 review
    rev1) -- the winner is decided by the atomic claim marker in
    ``db_tokens.revoke()``, not by any in-process lock.
  * ``db_tokens.revoke()``'s guarded UPDATE also loses to a concurrent
    ``consume()`` that lands in the same query-then-revoke window (0447 T0007
    review rev2) -- a token consumed after ``token_service.revoke()``'s own
    query read it as still active must stay consumed, not flip to revoked,
    and must draw no ``token_revoked`` event.

Runs through the same in-memory ``group_ai_leases`` fallback and dict-backed
``ai_invoke_runs`` fake as test_ai_invoke_lease_recovery_0401.py. ``db_tokens`` and
``token_service.revoke`` are monkeypatched with a small in-memory token store so no
real DB or pepper/env setup is needed.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import group_ai_leases as db_leases  # noqa: E402
from modules.flow_gate.db import tokens as db_tokens  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import token_service  # noqa: E402
from modules.flow_gate.db import workflow_events as db_events  # noqa: E402

GROUP = "flowgate.default.0447"
PROJECT = "flowgate"
RUN_ID = "aiv_20260828_000099"
TOKEN_ID = "tok_20260828_000001"
PAST = "2000-01-01T00:00:00+00:00"


def _seed_lease(token_id: str | None = TOKEN_ID) -> dict:
    db_leases.acquire(
        group_id=GROUP, project_id=PROJECT, run_id=RUN_ID,
        chain_id=RUN_ID, action_scope="new", worker_identity="usr_ai",
    )
    row = db_leases._memory[GROUP]
    row["acquired_at"] = PAST
    row["token_id"] = token_id
    return row


class FakeRunsStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def get(self, run_id):
        row = self.rows.get(run_id)
        return dict(row) if row else None

    def upsert(self, row):
        self.rows[row["run_id"]] = dict(row)

    def max_serial_for_date(self, date_str):
        return 0


class FakeTokenStore:
    """Minimal in-memory stand-in for db/tokens.py's get_by_id/revoke/consume
    trio, so token_service.revoke()'s real check-then-write logic runs
    unmodified against a row this test controls directly (no pepper/DB setup
    needed).

    ``revoke()`` mirrors the real store's guarded-UPDATE contract: the
    revoked_at/revoke_claim pair only ever changes once per token_id, and only
    while consumed_at is still NULL (0447 T0007 review rev2), and that
    check-and-set is protected by ``_engine_lock`` -- standing in for the real
    DB engine's own atomicity of a single UPDATE statement (ACID, not a
    Python-level design choice), which is what lets two *different processes*
    race this call safely with no shared in-process lock between them.
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self._engine_lock = threading.Lock()

    def seed(self, token_id, *, consumed_at=None, revoked_at=None):
        self.rows[token_id] = {
            "token_id": token_id, "project": PROJECT, "group_id": GROUP,
            "issued_to": "usr_ai", "consumed_at": consumed_at, "revoked_at": revoked_at,
            "revoke_claim": None,
        }

    def get_by_id(self, token_id):
        row = self.rows.get(token_id)
        return dict(row) if row else None

    def revoke(self, token_id, claim=None):
        with self._engine_lock:
            row = self.rows.get(token_id)
            if row is not None and row.get("revoked_at") is None and row.get("consumed_at") is None:
                row["revoked_at"] = "2026-08-28T10:00:00+00:00"
                row["revoke_claim"] = claim
            return dict(row) if row else None

    def consume(self, token_id):
        with self._engine_lock:
            row = self.rows.get(token_id)
            if row is not None and row.get("consumed_at") is None:
                row["consumed_at"] = "2026-08-28T10:00:00+00:00"
            return dict(row) if row else None


@pytest.fixture
def fake_runs(monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs as db_runs

    store = FakeRunsStore()
    monkeypatch.setattr(db_runs, "get", store.get)
    monkeypatch.setattr(db_runs, "upsert", store.upsert)
    monkeypatch.setattr(db_runs, "max_serial_for_date", store.max_serial_for_date)
    return store


@pytest.fixture
def fake_tokens(monkeypatch):
    store = FakeTokenStore()
    monkeypatch.setattr(db_tokens, "get_by_id", store.get_by_id)
    monkeypatch.setattr(db_tokens, "revoke", store.revoke)
    monkeypatch.setattr(db_tokens, "consume", store.consume)
    monkeypatch.setattr(db_tokens, "_invalidate_token_cache", lambda: None)
    return store


@pytest.fixture
def event_log(monkeypatch):
    events = []

    def _create(payload):
        events.append(dict(payload))

    monkeypatch.setattr(db_events, "create", _create)
    return events


@pytest.fixture(autouse=True)
def clean_state():
    db_leases._memory.clear()
    with svc._runs_lock:
        svc._runs.clear()
    yield
    db_leases._memory.clear()
    with svc._runs_lock:
        svc._runs.clear()


# ── startup_recover_leases -> orphan token revoke (item 1-2) ──────────────────────────

def test_startup_recover_revokes_the_active_orphan_token_exactly_once(fake_runs, fake_tokens, event_log):
    _seed_lease()
    fake_tokens.seed(TOKEN_ID)
    count = svc.startup_recover_leases()
    assert count == 1
    assert fake_tokens.rows[TOKEN_ID]["revoked_at"] is not None
    revoke_events = [e for e in event_log if e["event_type"] == "token_revoked"]
    assert len(revoke_events) == 1
    assert revoke_events[0]["metadata"] == (
        f'{{"token_id":"{TOKEN_ID}","reason":"orphaned_by_restart"}}'
    )
    assert fake_runs.rows[RUN_ID]["end_reason"] == "orphaned_by_restart"


def test_persist_then_crash_before_release_still_revokes_the_token(fake_runs, fake_tokens, event_log):
    """T0007 item 3-2: the run already persisted a normal end record before the
    process died with the lease still held -- the crash happened between that
    persist and the lease release. Recovery must not overwrite the real end
    reason, and must still revoke the active token exactly once."""
    _seed_lease()
    fake_tokens.seed(TOKEN_ID)
    fake_runs.rows[RUN_ID] = {"run_id": RUN_ID, "end_reason": "exited"}

    count = svc.startup_recover_leases()

    assert count == 1
    assert GROUP not in db_leases._memory
    assert fake_runs.rows[RUN_ID]["end_reason"] == "exited"
    assert fake_tokens.rows[TOKEN_ID]["revoked_at"] is not None
    revoke_events = [e for e in event_log if e["event_type"] == "token_revoked"]
    assert len(revoke_events) == 1


def test_consumed_token_is_left_alone(fake_runs, fake_tokens, event_log):
    _seed_lease()
    fake_tokens.seed(TOKEN_ID, consumed_at="2026-08-28T09:00:00+00:00")
    svc.startup_recover_leases()
    assert fake_tokens.rows[TOKEN_ID]["revoked_at"] is None
    assert fake_tokens.rows[TOKEN_ID]["consumed_at"] == "2026-08-28T09:00:00+00:00"
    assert not [e for e in event_log if e["event_type"] == "token_revoked"]


def test_already_revoked_token_draws_no_second_event(fake_runs, fake_tokens, event_log):
    _seed_lease()
    fake_tokens.seed(TOKEN_ID, revoked_at="2026-08-28T08:00:00+00:00")
    svc.startup_recover_leases()
    assert fake_tokens.rows[TOKEN_ID]["revoked_at"] == "2026-08-28T08:00:00+00:00"
    assert not [e for e in event_log if e["event_type"] == "token_revoked"]


def test_lease_without_a_token_id_is_a_safe_no_op(fake_runs, fake_tokens, event_log):
    _seed_lease(token_id=None)
    count = svc.startup_recover_leases()
    assert count == 1
    assert not [e for e in event_log if e["event_type"] == "token_revoked"]


def test_token_lookup_miss_is_a_safe_no_op(fake_runs, fake_tokens, event_log):
    """The lease points at a token_id that no longer resolves (e.g. hard-deleted
    by delete_expired) -- reclaim must not raise."""
    _seed_lease(token_id="tok_ghost_not_here")
    count = svc.startup_recover_leases()
    assert count == 1
    assert not [e for e in event_log if e["event_type"] == "token_revoked"]


def test_end_record_failure_does_not_block_token_revoke(fake_runs, fake_tokens, event_log, monkeypatch):
    def _boom(_row, _reason):
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_record_orphaned_lease_run", _boom)
    _seed_lease()
    fake_tokens.seed(TOKEN_ID)
    count = svc.startup_recover_leases()
    assert count == 1
    assert fake_tokens.rows[TOKEN_ID]["revoked_at"] is not None


def test_token_revoke_failure_does_not_block_lease_reclaim_or_handoff_recovery(
    fake_runs, fake_tokens, event_log, monkeypatch
):
    def _boom(_token_id, reason=None):
        raise RuntimeError("token service down")

    monkeypatch.setattr(token_service, "revoke", _boom)
    handoff_calls = []
    monkeypatch.setattr(svc, "startup_recover_handoffs", lambda: handoff_calls.append(1) or 0)
    _seed_lease()
    fake_tokens.seed(TOKEN_ID)

    count = svc.startup_recover_leases()

    assert count == 1
    assert GROUP not in db_leases._memory
    assert handoff_calls == [1]
    assert fake_runs.rows[RUN_ID]["end_reason"] == "orphaned_by_restart"


# ── token_service.revoke idempotency (item 1 last bullet) ─────────────────────────────

def test_revoke_twice_writes_exactly_one_event(fake_tokens, event_log):
    fake_tokens.seed(TOKEN_ID)
    token_service.revoke(TOKEN_ID, reason="user_cancel")
    token_service.revoke(TOKEN_ID, reason="user_cancel")
    assert fake_tokens.rows[TOKEN_ID]["revoked_at"] is not None
    revoke_events = [e for e in event_log if e["event_type"] == "token_revoked"]
    assert len(revoke_events) == 1


def test_revoke_missing_token_still_404s(fake_tokens, event_log):
    with pytest.raises(HTTPException) as exc_info:
        token_service.revoke("tok_ghost_not_here")
    assert exc_info.value.status_code == 404
    assert not event_log


def test_concurrent_revoke_race_across_the_query_window_writes_exactly_one_event(
    fake_tokens, event_log, monkeypatch
):
    """0447 T0007 review rev1: the rejected revision's idempotency held only within
    a single process (a bare threading.Lock). This reproduces the exact race the
    review named -- two callers (standing in for two separate OS processes, which
    share no Python object, let alone a lock) both query the same active token_id
    before either has written -- and asserts it still collapses to exactly one
    token_revoked event, with neither caller raising.

    The synchronization barrier sits inside the patched get_by_id, which is the
    "query" half of token_service.revoke()'s query-then-revoke sequence -- so both
    threads are forced to observe revoked_at IS NULL before either reaches the
    guarded UPDATE, reproducing the review's exact window rather than a race that
    just happens to serialize on the GIL.
    """
    fake_tokens.seed(TOKEN_ID)
    barrier = threading.Barrier(2)
    real_get_by_id = fake_tokens.get_by_id

    def _synced_get_by_id(token_id):
        row = real_get_by_id(token_id)
        barrier.wait(timeout=5)
        return row

    monkeypatch.setattr(db_tokens, "get_by_id", _synced_get_by_id)

    results = []

    def _racer(reason):
        try:
            token_service.revoke(TOKEN_ID, reason=reason)
            results.append("ok")
        except Exception as exc:  # pragma: no cover - failure surfaces via assert below
            results.append(exc)

    threads = [
        threading.Thread(target=_racer, args=("racer_a",)),
        threading.Thread(target=_racer, args=("racer_b",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert results == ["ok", "ok"]
    assert fake_tokens.rows[TOKEN_ID]["revoked_at"] is not None
    revoke_events = [e for e in event_log if e["event_type"] == "token_revoked"]
    assert len(revoke_events) == 1
    assert revoke_events[0]["metadata"] in (
        f'{{"token_id":"{TOKEN_ID}","reason":"racer_a"}}',
        f'{{"token_id":"{TOKEN_ID}","reason":"racer_b"}}',
    )


def test_consume_landing_in_the_query_window_leaves_the_token_consumed_not_revoked(
    fake_tokens, event_log, monkeypatch
):
    """0447 T0007 review rev2: the rev1 fix guarded the UPDATE only on
    ``revoked_at IS NULL``, which stops a second *revoke* from landing but says
    nothing about a *consume* that lands between token_service.revoke()'s query
    (``db_tokens.get_by_id``) and its own guarded UPDATE. This deterministically
    forces that exact window -- the revoke call's query observes the token as
    still active, a concurrent consume() then completes, and only afterward
    does the revoke call's own UPDATE run -- and asserts the guarded UPDATE
    must lose: the consumed state is preserved (consumed_at set, revoked_at
    still NULL) and no token_revoked event is written for what is actually a
    consumed token.
    """
    fake_tokens.seed(TOKEN_ID)
    read_done = threading.Event()
    consume_done = threading.Event()
    real_get_by_id = fake_tokens.get_by_id

    def _synced_get_by_id(token_id):
        row = real_get_by_id(token_id)
        read_done.set()
        assert consume_done.wait(timeout=5)
        return row

    monkeypatch.setattr(db_tokens, "get_by_id", _synced_get_by_id)

    def _consumer():
        assert read_done.wait(timeout=5)
        db_tokens.consume(TOKEN_ID)
        consume_done.set()

    consumer_thread = threading.Thread(target=_consumer)
    consumer_thread.start()

    token_service.revoke(TOKEN_ID, reason="lost_to_consume")

    consumer_thread.join(timeout=5)

    assert fake_tokens.rows[TOKEN_ID]["consumed_at"] is not None
    assert fake_tokens.rows[TOKEN_ID]["revoked_at"] is None
    assert not [e for e in event_log if e["event_type"] == "token_revoked"]
