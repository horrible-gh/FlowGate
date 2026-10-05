"""Unit 1b of the group-lock migration (flowgate.default.0669 chat seq 18/27; 0666 0009-L §2.3~2.9).

1. lock_key / order check / re-entry (L 2.1.2, 2.3.3, 2.4)
2. acquire / release on resource_lock (L 2.5)
3. (removed in unit 9c: the legacy bridge and Gate no longer exist)
4. stale reclaim of a DEAD owner's row (L 2.9)
5. unit 2 callers: ordinary source mutation and Bundle ensure hold G (D 3.9, 3.10)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import connection  # noqa: E402
from modules.flow_gate.db import git_concurrency as gc  # noqa: E402
from modules.flow_gate.db import tr_self_check_runs  # noqa: E402
from modules.flow_gate.services.git import instance_registry as reg  # noqa: E402
from modules.flow_gate.services.git import lock_manager as lm  # noqa: E402

NOW = "2026-10-04T12:00:00+09:00"
P = "p_0669b"


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_git_lock_manager_0669.db")


@pytest.fixture
def store(db_path, monkeypatch):
    from sqloader.sqlite3 import SQLiteWrapper

    s = object.__new__(connection.FlowGateStore)
    s._db = SQLiteWrapper(db_path)
    s._sq = None
    from modules.flow_gate.db import operation_job, tr_history_recovery  # noqa: F401
    for name, module in list(sys.modules.items()):
        # every db/service module that bound get_store at import time (freeze-claim read etc.)
        if name.startswith("modules.flow_gate.") and hasattr(module, "get_store"):
            monkeypatch.setattr(module, "get_store", lambda s=s: s)
    with s.transaction():
        for table in ("resource_lock", "server_instance"):
            s._execute(f"DELETE FROM {table}")
        s._execute(
            "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
            "VALUES (?, 'P 0669b', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING",
            [P, NOW, NOW],
        )
    monkeypatch.setattr(tr_self_check_runs, "has_group_recovery_incomplete", lambda _pid, _gid: False)
    monkeypatch.setattr(reg, "_start_identity", lambda pid: "self-marker")
    monkeypatch.setattr(reg, "node_key", lambda: "node-a")
    monkeypatch.setattr(lm, "poll_interval_sec", lambda: 0.05)
    monkeypatch.setattr(gc, "_current_instance_id", None)
    reg.register()
    yield s
    gc.set_current_instance_id(None)


def _ctx():
    return lm.new_context()


# ── 1. keys, order, re-entry ─────────────────────────────────────────────────

def test_lock_key_is_scoped_and_fixed_length():
    a = lm.lock_key("G", P, "g1")
    assert len(a) == 66 and a.startswith("G:")
    assert a != lm.lock_key("G", P, "g2")
    assert lm.lock_key("B", P) != lm.lock_key("M", P)
    with pytest.raises(lm.LockProgramError):
        lm.lock_key("G", P)


def test_no_context_is_a_program_error():
    with pytest.raises(lm.LockProgramError) as exc:
        lm.acquire("G", P, group_id="g1", holder_kind="source_mutation")
    assert exc.value.code == "no_execution_context"


def test_order_violation_is_raised_before_any_wait(store):
    ctx = _ctx()
    assert lm.acquire("B", P, holder_kind="base_mutation", ctx=ctx).ok
    with pytest.raises(lm.LockProgramError) as exc:
        lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=ctx)
    assert exc.value.details["reason"] == "rank_order"
    lm.release(ctx, lm.lock_key("B", P))


def test_reentry_counts_and_releases_on_last(store):
    ctx = _ctx()
    first = lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=ctx)
    again = lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=ctx)
    assert first.ok and again.reentrant
    lm.release(ctx, first.lock_key)
    assert gc.get_resource_lock(first.lock_key) is not None
    lm.release(ctx, first.lock_key)
    assert gc.get_resource_lock(first.lock_key) is None


# ── 2. new path ──────────────────────────────────────────────────────────────

def test_other_groups_do_not_block_each_other_but_same_group_does(store):
    a, b, c = _ctx(), _ctx(), _ctx()
    assert lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=a).ok
    assert lm.acquire("G", P, group_id="g2", holder_kind="bundle", ctx=b, mode="job").ok
    busy = lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=c, mode="job")
    assert busy.kind == lm.BUSY and busy.blocker["holder_ctx_id"] == a.ctx_id


# ── 4. stale reclaim ─────────────────────────────────────────────────────────

def test_dead_owner_row_is_reclaimed_on_acquire(store):
    dead = "inst_" + "d" * 32
    gc.insert_instance(dead, "node-a", 999, "gone", NOW)
    gc.mark_instance_dead(dead)
    key = lm.lock_key("G", P, "g1")
    gc.insert_lock(lock_key=key, domain="G", project_id=P, group_id="g1", target_key=None,
                   holder_ctx_id="req:dead", holder_kind="source_mutation", job_id=None,
                   instance_id=dead, lock_epoch="le_" + "0" * 24, hold_class="short",
                   acquired_at=NOW, heartbeat_until=None)
    ctx = _ctx()
    out = lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=ctx, mode="job")
    assert out.ok
    assert gc.get_resource_lock(key)["holder_ctx_id"] == ctx.ctx_id
    lm.release(ctx, key)


# ── 5. unit 2 callers (D 3.9, 3.10) ─────────────────────────────────────────

@pytest.fixture
def g_callers(store, monkeypatch, tmp_path):
    from modules.flow_gate.services import source_bundle_service as bundles
    from modules.flow_gate.services import tr2_file_policy as policy

    monkeypatch.setattr(lm, "wait_budget", lambda domain, mode: 0.0)
    monkeypatch.setattr(policy, "_group_root", lambda *_: tmp_path)
    monkeypatch.setattr(policy, "managed_paths", lambda _gid: set())
    monkeypatch.setattr(policy.db_recovery, "has_unresolved", lambda _gid: False)
    monkeypatch.setattr(bundles, "_ensure_under_lock", lambda pid, gid: {"bundle_id": gid})
    return policy, bundles


def test_source_mutation_and_bundle_of_other_groups_run_alongside(g_callers):
    policy, bundles = g_callers
    with policy.general_source_mutation(P, "g1", exact_paths=["a.py"], allow_missing_leaf=True):
        with policy.general_source_mutation(P, "g2", exact_paths=["a.py"], allow_missing_leaf=True):
            pass
        assert bundles.ensure(P, "g2") == {"bundle_id": "g2"}
    assert gc.list_locks_in_scope(P) == []


def test_same_group_mutation_and_bundle_wait_on_g(g_callers):
    policy, bundles = g_callers
    with policy.general_source_mutation(P, "g1", exact_paths=["a.py"], allow_missing_leaf=True):
        with pytest.raises(policy.Tr2FilePolicyError) as busy:
            with policy.general_source_mutation(P, "g1", exact_paths=["a.py"], allow_missing_leaf=True):
                pass
        assert busy.value.code == policy.SOURCE_MUTATION_BUSY
        assert busy.value.details["reason_code"] == "lock_busy"
        assert busy.value.details["blocker"]["holder_kind"] == "source_mutation"
        with pytest.raises(bundles.materializer.SourceBundleError) as bundle_busy:
            bundles.ensure(P, "g1")
        assert bundle_busy.value.code == "source_busy"
