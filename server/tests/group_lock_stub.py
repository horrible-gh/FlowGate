"""Stand-in for the Group lock (G) in suites that mock the git layer (flowgate.default.0669).

Units 2~4 moved write/patch, Bundle, TR2, TR commit, Time Machine and TR conflict from the
project mutex (``db_git.try_acquire_lock``) to G through ``lock_manager.acquire_group``.
Suites that stubbed the old mutex to "granted" or "busy" stub G the same way here, so they
keep testing what they were written for instead of reaching a store they never set up.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services.git import lock_manager as lm


def stub_group_lock(monkeypatch, *, grant=True, held: list | None = None) -> None:
    """``grant`` is a bool or a ``(project_id, group_id, holder_kind) -> bool`` callable.
    ``held`` (optional) collects the holder_kind of every G still held."""
    def acquire_group(project_id, group_id, *, holder_kind, mode="interactive"):
        key = lm.lock_key("G", project_id, group_id)
        ok = grant(project_id, group_id, holder_kind) if callable(grant) else grant
        if not ok:
            return lm.LockOutcome(lm.BUSY, key, blocker={"holder_kind": "test"}), lm.new_context()
        if held is not None:
            held.append(holder_kind)
        return lm.LockOutcome(lm.ACQUIRED, key, lock_epoch="stub"), lm.new_context()

    def release(ctx_or_key, key=None):
        if held:
            held.pop()

    monkeypatch.setattr(lm, "acquire_group", acquire_group)
    monkeypatch.setattr(lm, "release", release)
    monkeypatch.setattr(lm, "still_held", lambda ctx, key: True)


@contextmanager
def hold_group_lock(project_id: str, group_id: str, holder_kind: str = "source_mutation"):
    """Another holder really owns this Group's G in the store (suites on a real DB that
    used to insert a foreign git_project_lock row to stand for "someone else is busy")."""
    ctx = lm.new_context()
    outcome = lm.acquire("G", project_id, group_id=group_id, holder_kind=holder_kind,
                         mode=lm.NO_WAIT, ctx=ctx)
    assert outcome.ok, outcome
    try:
        yield outcome
    finally:
        lm.release(ctx, outcome.lock_key)


@contextmanager
def hold_lock(domain: str, project_id: str, *, group_id: str | None = None,
              target_key: str | None = None, holder_kind: str = "base_mutation"):
    """Another holder really owns this domain lock (G/W/B/R/M) in the store."""
    ctx = lm.new_context()
    outcome = lm.acquire(domain, project_id, group_id=group_id, target_key=target_key,
                         holder_kind=holder_kind, mode=lm.NO_WAIT, ctx=ctx)
    assert outcome.ok, outcome
    try:
        yield outcome
    finally:
        lm.release(ctx, outcome.lock_key)


def spy_acquire(monkeypatch) -> list[dict]:
    """Record every ``lock_manager.acquire`` call while still going through the real store."""
    calls: list[dict] = []
    real = lm.acquire

    def acquire(domain, project_id, **kw):
        outcome = real(domain, project_id, **kw)
        calls.append({"domain": domain, "project_id": project_id, "group_id": kw.get("group_id"),
                      "target_key": kw.get("target_key"), "holder_kind": kw.get("holder_kind"),
                      "mode": kw.get("mode"), "outcome": outcome})
        return outcome

    monkeypatch.setattr(lm, "acquire", acquire)
    return calls


def held_locks(project_id: str = "flowgate") -> list[dict]:
    """The resource_lock rows that are still in the store for the project."""
    from modules.flow_gate.db import git_concurrency as gc
    return list(gc.list_locks_in_scope(project_id))


class DomainLockStub:
    """What ``stub_domain_locks`` installs: every domain lock (G/W/B/R/M) is granted or refused
    in memory, never read from or written to a store. ``calls`` lists each request as
    ``(domain, project_id, group_id, target_key, holder_kind, granted)``; ``held`` the lock keys
    still held. Lock order and re-entry follow the real rules (``check_order``, hold counts)."""

    def __init__(self, grant=True):
        self.grant = grant
        self.calls: list[tuple] = []
        self.held: list[str] = []

    def acquire(self, domain, project_id, *, group_id=None, target_key=None, holder_kind,
                mode="interactive", hold_class=None, ctx=None):
        ctx = ctx or lm.current_context() or lm.new_context("req")
        key = lm.lock_key(domain, project_id, group_id, target_key)
        h = ctx.find_held(key)
        if h is not None:
            h.count += 1
            return lm.LockOutcome(lm.ACQUIRED, key, lock_epoch=h.lock_epoch, reentrant=True)
        lm.check_order(ctx, domain, key)
        granted = (self.grant(domain, project_id, group_id, target_key, holder_kind)
                   if callable(self.grant) else bool(self.grant))
        self.calls.append((domain, project_id, group_id, target_key, holder_kind, granted))
        if not granted:
            return lm.LockOutcome(lm.BUSY, key, blocker={
                "domain": domain, "project_id": project_id, "group_id": group_id,
                "target_key": target_key, "holder_kind": "test"})
        ctx.held.append(lm.Held(key, domain, project_id, group_id, target_key, "stub", "short"))
        self.held.append(key)
        return lm.LockOutcome(lm.ACQUIRED, key, lock_epoch="stub")

    def release(self, ctx_or_key, key=None):
        if key is None:
            ctx, key = lm.current_context() or lm.new_context("req"), ctx_or_key
        else:
            ctx = ctx_or_key
        h = ctx.find_held(key)
        if h is None:
            raise lm.LockProgramError("release_not_held", lock_key=key)
        h.count -= 1
        if h.count > 0:
            return
        ctx.held.remove(h)
        self.held.remove(key)


def stub_domain_locks(monkeypatch, grant=True) -> DomainLockStub:
    """For suites that mock the git/DB layer around the lock (they used to stub the removed
    project mutex to "granted"/"busy"). ``grant`` is a bool or a
    ``(domain, project_id, group_id, target_key, holder_kind) -> bool`` callable."""
    stub = DomainLockStub(grant)
    monkeypatch.setattr(lm, "acquire", stub.acquire)
    monkeypatch.setattr(lm, "release", stub.release)
    monkeypatch.setattr(lm, "still_held", lambda ctx, key: ctx.find_held(key) is not None)
    # the Group freeze guard reads the durable claim from the store; no claim exists here
    monkeypatch.setattr(lm, "_guard_group_admission", lambda ctx, key, project_id, group_id, outcome: outcome)
    return stub


def stub_work_base_floor(monkeypatch) -> None:
    """update-from-base suites that mock the DB: the scope floor (verified work-base record) is
    read from group_git_state, which these suites never seed. The floor rules have their own
    suites (test_group_work_base_scope_0665 and friends); here the pinned source is simply the
    resolved ref and the floor is the Group's current HEAD."""
    from modules.flow_gate.services.git import scope_base

    def guard(project_id, group_id, wt_path, *, source_ref, state=None, config=None):
        head = scope_base.rev_commit(wt_path, "HEAD")
        return {"floor_sha": head, "work_base_sha": head, "work_base_sync_sha": head,
                "work_base_ref": source_ref, "work_base_state": "verified",
                "source_sha": scope_base.rev_commit(wt_path, source_ref), "source_ref": source_ref}

    monkeypatch.setattr(scope_base, "guard_update_source", guard)
    def record(project_id, group_id, wt_path, *, source_ref, source_sha, floor_before, path,
               merge_id=None, config=None):
        return {"source_ref": source_ref, "source_sha": source_sha,
                "result_head": scope_base.rev_commit(wt_path, "HEAD")}

    monkeypatch.setattr(scope_base, "record_update", record)


@pytest.fixture
def domain_lock_stub(monkeypatch) -> DomainLockStub:
    return stub_domain_locks(monkeypatch)


# ── real store (SQLite) for suites that used to simulate the project mutex in-process ──

_NOW = "2026-10-04T12:00:00+09:00"

# projects the suites that mock the git layer name; resource_lock.project_id has an FK
PROJECT_IDS = ("flowgate", "p", "p1", "p2", "proj", "test2", "demo", "gitprj", "origin-sync",
               "prj", "proj1", "proj2", "connected")


class LockState(dict):
    """``state["locked"]`` is read from the real resource_lock rows of the project."""

    def __init__(self, project_id: str = "flowgate", **kw):
        super().__init__(**kw)
        self._project_id = project_id

    def __getitem__(self, key):
        if key == "locked":
            return group_locked(self._project_id)
        return super().__getitem__(key)


def group_locked(project_id: str = "flowgate") -> bool:
    from modules.flow_gate.db import git_concurrency as gc
    return bool(gc.list_locks_in_scope(project_id))


def _build_store(migrated_sqlite_db, monkeypatch):
    from sqloader.sqlite3 import SQLiteWrapper

    from modules.flow_gate.db import connection
    from modules.flow_gate.db import git_concurrency as gc
    from modules.flow_gate.db import tr_self_check_runs
    from modules.flow_gate.services.git import instance_registry as reg

    s = object.__new__(connection.FlowGateStore)
    s._db = SQLiteWrapper(migrated_sqlite_db("group_lock_stub.db"))
    s._sq = None
    from modules.flow_gate.db import operation_job, tr_history_recovery  # noqa: F401
    for name, module in list(sys.modules.items()):
        if name.startswith("modules.flow_gate.") and hasattr(module, "get_store"):
            monkeypatch.setattr(module, "get_store", lambda s=s: s)
    with s.transaction():
        for table in ("resource_lock", "server_instance"):
            s._execute(f"DELETE FROM {table}")
        for pid in PROJECT_IDS:
            s._execute(
                "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
                "VALUES (?, ?, 1, ?, ?) ON CONFLICT(project_id) DO NOTHING",
                [pid, pid, _NOW, _NOW],
            )
    monkeypatch.setattr(tr_self_check_runs, "has_group_recovery_incomplete", lambda _p, _g: False)
    monkeypatch.setattr(reg, "_start_identity", lambda pid: "self-marker")
    monkeypatch.setattr(reg, "node_key", lambda: "node-a")
    monkeypatch.setattr(lm, "poll_interval_sec", lambda: 0.02)
    monkeypatch.setattr(lm, "wait_budget", lambda domain, mode: 0.0 if mode == lm.NO_WAIT else 3.0)
    monkeypatch.setattr(gc, "_current_instance_id", None)
    reg.register()
    return s


@pytest.fixture
def group_store(migrated_sqlite_db, monkeypatch):
    """The real lock manager on a migrated SQLite DB; every FlowGate module reads this DB.
    Projects in ``PROJECT_IDS`` exist."""
    from modules.flow_gate.db import git_concurrency as gc

    from modules.flow_gate.services.git import instance_registry as reg

    reg.shutdown()          # a heartbeat thread an earlier test started belongs to another DB
    s = _build_store(migrated_sqlite_db, monkeypatch)
    yield s
    reg.shutdown()
    gc.set_current_instance_id(None)
