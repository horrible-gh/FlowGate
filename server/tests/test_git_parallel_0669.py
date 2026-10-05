"""Parallel Git lock scenarios on the real domain-lock store (flowgate.default.0669 T0003).

Real resource_lock rows on SQLite, real threads, no mocked lock manager:
1. different groups run in parallel without ``busy``
2. one group serializes (never two holders at once)
3. a finalize-style hold (G+B+R) makes another base-domain request wait, then proceed;
   unrelated groups are not blocked meanwhile
4. locks are released after a failure inside the guarded block and after cancel
5. two real git worktrees keep their changes apart while both groups work at once
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
import time
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
P = "p_0669par"


@pytest.fixture(scope="module")
def db_path(migrated_sqlite_db):
    return migrated_sqlite_db("test_git_parallel_0669.db")


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
            "VALUES (?, 'P 0669 par', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING",
            [P, NOW, NOW],
        )
    monkeypatch.setattr(tr_self_check_runs, "has_group_recovery_incomplete", lambda _p, _g: False)
    monkeypatch.setattr(reg, "_start_identity", lambda pid: "self-marker")
    monkeypatch.setattr(reg, "node_key", lambda: "node-a")
    monkeypatch.setattr(lm, "poll_interval_sec", lambda: 0.02)
    monkeypatch.setattr(lm, "wait_budget", lambda domain, mode: 0.0 if mode == lm.NO_WAIT else 10.0)
    monkeypatch.setattr(gc, "_current_instance_id", None)
    reg.register()
    yield s
    gc.set_current_instance_id(None)


def _run_threads(workers):
    errors = []

    def wrap(fn):
        def inner():
            try:
                fn()
            except BaseException as exc:  # surfaced after join
                errors.append(exc)
        return inner

    threads = [threading.Thread(target=wrap(fn), daemon=True) for fn in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads), "worker thread hung"
    assert not errors, errors


def test_independent_groups_hold_g_at_the_same_time(store):
    n = 4
    barrier = threading.Barrier(n, timeout=20)
    outcomes = {}

    def worker(i):
        def run():
            ctx = lm.new_context()
            out = lm.acquire("G", P, group_id=f"g{i}", holder_kind="source_mutation",
                             ctx=ctx, mode=lm.NO_WAIT)
            outcomes[i] = out.kind
            barrier.wait()          # everyone holds its own G at this moment
            lm.release(ctx, out.lock_key)
        return run

    _run_threads([worker(i) for i in range(n)])
    assert outcomes == {i: lm.ACQUIRED for i in range(n)}
    assert gc.list_locks_in_scope(P) == []


def test_same_group_never_has_two_holders_and_all_finish(store):
    n = 5
    state = {"inside": 0, "max": 0, "done": 0}
    guard = threading.Lock()

    def worker():
        ctx = lm.new_context()
        out = lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=ctx, mode="job")
        assert out.ok, out
        with guard:
            state["inside"] += 1
            state["max"] = max(state["max"], state["inside"])
        time.sleep(0.05)
        with guard:
            state["inside"] -= 1
            state["done"] += 1
        lm.release(ctx, out.lock_key)

    _run_threads([worker] * n)
    assert state["max"] == 1 and state["done"] == n
    assert gc.list_locks_in_scope(P) == []


def test_finalize_style_hold_makes_base_waiters_wait_but_not_other_groups(store):
    holder = lm.new_context()
    held = [lm.acquire("G", P, group_id="g1", holder_kind="publish", ctx=holder),
            lm.acquire("B", P, holder_kind="publish", ctx=holder),
            lm.acquire("R", P, holder_kind="publish", ctx=holder)]
    assert all(h.ok for h in held)

    # NO_WAIT callers see the blocker immediately (the old git_busy case) ...
    probe = lm.new_context()
    busy = lm.acquire("B", P, holder_kind="base_mutation", ctx=probe, mode=lm.NO_WAIT)
    assert busy.kind == lm.BUSY and busy.blocker["holder_kind"] == "publish"
    # ... while another group's G is untouched.
    other = lm.new_context()
    free = lm.acquire("G", P, group_id="g2", holder_kind="source_mutation", ctx=other, mode=lm.NO_WAIT)
    assert free.ok
    lm.release(other, free.lock_key)

    waited = {}

    def waiter():
        ctx = lm.new_context()
        t0 = time.monotonic()
        out = lm.acquire("B", P, holder_kind="publish", ctx=ctx, mode="job")
        waited["kind"], waited["sec"] = out.kind, time.monotonic() - t0
        if out.ok:
            lm.release(ctx, out.lock_key)

    def releaser():
        time.sleep(0.4)
        for h in reversed(held):
            lm.release(holder, h.lock_key)

    _run_threads([waiter, releaser])
    assert waited["kind"] == lm.ACQUIRED and waited["sec"] >= 0.3
    assert gc.list_locks_in_scope(P) == []


def test_locks_are_released_after_failure_and_after_cancel(store):
    ctx = lm.new_context()
    out = lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=ctx)
    with pytest.raises(RuntimeError):
        try:
            assert out.ok
            raise RuntimeError("boom inside the guarded block")
        finally:
            lm.release(ctx, out.lock_key)
    assert gc.list_locks_in_scope(P) == []

    # cancel: a worker is interrupted by an event and releases on the way out
    cancel = threading.Event()
    got = threading.Event()

    def job():
        c = lm.new_context()
        o = lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=c)
        assert o.ok
        got.set()
        try:
            cancel.wait(10)
        finally:
            lm.release(c, o.lock_key)

    t = threading.Thread(target=job, daemon=True)
    t.start()
    assert got.wait(10)
    assert len(gc.list_locks_in_scope(P, "g1")) == 1
    cancel.set()
    t.join(10)
    assert gc.list_locks_in_scope(P) == []
    again = lm.new_context()
    assert lm.acquire("G", P, group_id="g1", holder_kind="source_mutation", ctx=again, mode=lm.NO_WAIT).ok
    lm.release(again, lm.lock_key("G", P, "g1"))


@pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")
def test_two_worktrees_stay_apart_while_both_groups_work(store, tmp_path):
    def git(cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout

    base = tmp_path / "base"
    base.mkdir()
    git(base, "init", "-q", "-b", "main")
    git(base, "config", "user.email", "t@example.com")
    git(base, "config", "user.name", "t")
    (base / "seed.txt").write_text("seed\n")
    git(base, "add", "-A")
    git(base, "commit", "-q", "-m", "seed")
    roots = {}
    for g in ("g1", "g2"):
        roots[g] = tmp_path / f"wt_{g}"
        git(base, "worktree", "add", "-q", "-b", f"work/{g}", str(roots[g]))

    barrier = threading.Barrier(2, timeout=20)

    def worker(g):
        def run():
            ctx = lm.new_context()
            out = lm.acquire("G", P, group_id=g, holder_kind="source_mutation", ctx=ctx, mode=lm.NO_WAIT)
            assert out.ok, out
            barrier.wait()      # both groups are inside their G at the same time
            for i in range(20):
                (roots[g] / f"{g}_{i}.txt").write_text(f"{g}-{i}\n")
            lm.release(ctx, out.lock_key)
        return run

    _run_threads([worker("g1"), worker("g2")])
    for g, other in (("g1", "g2"), ("g2", "g1")):
        status = git(roots[g], "status", "--porcelain")
        assert f"{g}_0.txt" in status and other not in status, status
        assert len([line for line in status.splitlines() if line]) == 20
    assert gc.list_locks_in_scope(P) == []
