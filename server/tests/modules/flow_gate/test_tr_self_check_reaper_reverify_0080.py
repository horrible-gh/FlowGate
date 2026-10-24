"""0080 T#3: the Self-check reaper, operator re-verification and a lost G heartbeat.

The card's core checks (NR0007 §5 items 5, 6, 7):

1. the sweeper's reaper step (``job_sweeper.step_selfcheck_reaper``) ends nothing while the
   owning server or the process tree is UNKNOWN -- an owner whose lookup fails (its
   ``status='dead'`` hint included) is not written at all, an unproved tree stays
   recovery_incomplete with G protected, tick after tick -- and with the owner proved dead
   and the tree empty it ends the run and deletes its G in the same pass, from a fresh row
   and from a recovery_incomplete one (``retry_recovery_incomplete``);
2. a console user's re-verification of a recovery_incomplete run is refused while the proof
   fails (``selfcheck_ownership_unknown`` / ``selfcheck_tree_unproven``, nothing released),
   is closed to worker tokens and to users without perm_document_update, and releases the
   run when the proof holds;
3. after a lost heartbeat the holder's G is renewed when its row is still there, protected
   when the renewal fails (``release()`` then leaves it; the holder's recovery release
   deletes it), and reported gone when the row is no longer its own.

And the review fixes of the same card, each as the regression it closes:

4. a real run whose G heartbeat is lost is not recorded as a valid result before the loss
   is judged: G gone -> ``failed(selfcheck_lock_lost)``, also when the keeper's first store
   read failed and when the worker then hits an error; not decidable -> held
   (recovery_incomplete, G protected), never ``completed``;
5. what a failed spawn learnt (its target / supervisor) is on the stored row, and a spawn
   that began without a recorded target is not proved by its empty Job, so the reaper keeps
   such a run protected after its worker is gone;
6. the reaper reaches an ended run's protected G behind protected rows it cannot prove.

Fixtures: ``flow`` of test_tr_self_check_draft_e2e_0638 and ``world`` of
test_tr_self_check_recover_proof_0080 (run rows, owner server rows and G written directly,
real short python children). Injected, as in T#1/T#2: the identity lookup failure
(``executor.probe_start_identity``), a failing heartbeat write, the keeper's store read
(``lock_manager.selfcheck_regain``), a failing spawn / result read (``executor.spawn`` /
``executor.wait``) and the candidate page size. Not covered: the UI, POSIX, multi-server.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

import pytest

from test_tr_self_check_draft_e2e_0638 import DRAFT_RUNS, G, P, RAW, USER_JWT, _as, _row, flow  # noqa: F401 -- fixture
from test_tr_self_check_recover_proof_0080 import _assert_not_released, _g, _lookup_fails, world  # noqa: F401 -- fixture

from modules.flow_gate.api.v1 import self_check_routes as routes
from modules.flow_gate.db import git_concurrency as gdb
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_service as service
from modules.flow_gate.services.git import instance_registry as registry
from modules.flow_gate.services.git import job_sweeper
from modules.flow_gate.services.git import lock_manager


def _hold_incomplete(store, rid, reason):
    """What recover() / the gate leave behind for an unproved run: incomplete + G protected."""
    store._execute("UPDATE tr_self_check_runs SET recovery_state = 'incomplete', recovery_reason = ?"
                   " WHERE self_check_run_id = ?", [reason, rid])
    store._execute("UPDATE resource_lock SET protected = 1, protect_reason = 'selfcheck_reap_unconfirmed',"
                   " protected_at = ?, heartbeat_until = NULL WHERE lock_key = ?",
                   [now_iso(), lock_manager.lock_key("G", P, G)])


@pytest.mark.parametrize("unknown", ["owner_server", "tree"])
def test_the_reaper_ends_nothing_unproved(world, monkeypatch, unknown):
    process, store = world["process"], world["store"]
    if unknown == "owner_server":
        owner, target = process(alive=True), process(alive=True)        # owner alive, unreadable
        _lookup_fails(monkeypatch, owner[0].pid)
        store._execute("UPDATE server_instance SET status = 'dead' WHERE pid = ?", [owner[0].pid])
    else:
        owner, target = process(alive=False), process(alive=True)       # owner dead, target unreadable
        _lookup_fails(monkeypatch, target[0].pid)
    rid, epoch = world["run"](owner, target)

    first = job_sweeper.step_selfcheck_reaper()
    second = job_sweeper.step_selfcheck_reaper()                         # the retry of the next tick

    assert first == second == 0
    row, lock = _assert_not_released(store, rid, epoch)
    assert target[0].poll() is None, "a process whose identity could not be read was signalled"
    if unknown == "owner_server":
        assert owner[0].poll() is None
        assert (row["recovery_state"], int(lock["protected"])) == ("none", 0)          # not written at all
    else:
        assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
        assert int(lock["protected"]) == 1


@pytest.mark.parametrize("start", ["none", "incomplete"])
def test_the_reaper_ends_a_proved_run_and_its_g_together(world, start):
    process, store = world["process"], world["store"]
    rid, _epoch = world["run"](process(alive=False), process(alive=False))   # owner, target, tree gone
    if start == "incomplete":
        _hold_incomplete(store, rid, proof.TREE_UNPROVEN)

    assert job_sweeper.step_selfcheck_reaper() == 1

    row = _row(store, rid)
    assert (row["status"], row["error_code"], row["recovery_state"], row["active_key"]) == (
        "failed", "interrupted_by_restart", "recovered", None)
    assert _g(store) is None


@pytest.mark.parametrize("case", ["owner_unknown", "tree_unproven", "proved"])
def test_a_reverify_is_refused_without_proof(world, monkeypatch, case):
    process, store, client = world["process"], world["store"], world["client"]
    client.app.include_router(routes.recovery_router)
    if case == "owner_unknown":
        owner, target = process(alive=True), process(alive=False)
        _lookup_fails(monkeypatch, owner[0].pid)
        reason = proof.OWNER_SERVER_UNPROVEN
    elif case == "tree_unproven":
        owner, target = process(alive=False), process(alive=True)
        _lookup_fails(monkeypatch, target[0].pid)
        reason = proof.TREE_UNPROVEN
    else:
        owner, target, reason = process(alive=False), process(alive=False), proof.TREE_UNPROVEN
    rid, epoch = world["run"](owner, target)
    _hold_incomplete(store, rid, reason)
    path = f"/api/v1/self-check/recovery/runs/{rid}/reverify"

    # Worker tokens never reach it; a user needs perm_document_update.
    assert client.post(path, headers=_as(RAW["new"])).status_code == 403
    monkeypatch.setattr(routes, "has_permission", lambda _user, _project, perm: perm != "perm_document_update")
    assert client.post(path, headers=_as(USER_JWT)).status_code == 403
    monkeypatch.setattr(routes, "has_permission", lambda *_: True)
    assert _row(store, rid)["recovery_state"] == "incomplete"

    response = client.post(path, headers=_as(USER_JWT))

    if case == "proved":
        assert response.status_code == 200, response.text
        assert response.json()["reverify"] == {"released": True, "reason": None}
        row = _row(store, rid)
        assert (row["status"], row["error_code"], row["active_key"]) == ("failed", "interrupted_by_restart", None)
        assert _g(store) is None
        return
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == ("selfcheck_ownership_unknown" if case == "owner_unknown" else "selfcheck_tree_unproven")
    assert error["details"]["reverify"] == {"released": False, "reason": reason}
    assert error["details"]["run"]["self_check_run_id"] == rid
    row, lock = _assert_not_released(store, rid, epoch)
    assert (row["recovery_state"], row["recovery_reason"], int(lock["protected"])) == ("incomplete", reason, 1)
    assert target[0].poll() is None if case == "tree_unproven" else owner[0].poll() is None


def test_a_lost_heartbeat_is_renewed_protected_or_reported_gone(flow, monkeypatch):
    store = flow["store"]
    ctx = lock_manager.new_context("scr", "scr_0080keeper")
    outcome = lock_manager.acquire("G", P, group_id=G, holder_kind="selfcheck", mode="selfcheck_start", ctx=ctx)
    assert outcome.ok, outcome
    key, held = outcome.lock_key, ctx.find_held(outcome.lock_key)

    def lose():
        lock_manager._heartbeats._lost(ctx, held)          # as after a long DB outage
        assert held.lost and ctx.lock_lost

    # (a) the row is still ours: renewed, back on the heartbeat
    lose()
    assert lock_manager.selfcheck_regain(ctx, key) == lock_manager.SELFCHECK_RENEWED
    assert not held.lost and not ctx.lock_lost and id(held) in lock_manager._heartbeats._entries

    # (b) the renewal fails: protected (nobody reclaims or takes it); release() leaves it
    lose()
    monkeypatch.setattr(gdb, "heartbeat_long_lock", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("store down")))
    assert lock_manager.selfcheck_regain(ctx, key) == lock_manager.SELFCHECK_PROTECTED
    lock = _g(store)
    assert (lock["lock_epoch"], int(lock["protected"]), lock["protect_reason"]) == (
        held.lock_epoch, 1, "selfcheck_lock_lost")
    assert lock_manager.is_stale(lock) == lock_manager.PROTECTED
    other = lock_manager.new_context("req")
    taken = lock_manager.acquire("G", P, group_id=G, holder_kind="source_mutation", mode="job", ctx=other)
    assert taken.kind == lock_manager.RECOVERY_REQUIRED, taken
    lock_manager.release(ctx, key)
    assert _g(store) is not None, "release() deleted a protected row"
    lock_manager.selfcheck_release_recovered(P, G, "scr_0080keeper")    # what _finish does after the tree proof
    assert _g(store) is None

    # (c) the row is no longer this hold's: gone
    ctx2 = lock_manager.new_context("scr", "scr_0080keeper2")
    outcome = lock_manager.acquire("G", P, group_id=G, holder_kind="selfcheck", mode="selfcheck_start", ctx=ctx2)
    assert outcome.ok, outcome
    store._execute("DELETE FROM resource_lock WHERE lock_key = ?", [outcome.lock_key])
    assert lock_manager.selfcheck_regain(ctx2, outcome.lock_key) == lock_manager.SELFCHECK_GONE
    lock_manager._heartbeats.remove(ctx2.find_held(outcome.lock_key))


# ── review fixes: lock loss vs. the terminal write, partial spawn records, G candidates ──

def _worker_done(rid):
    for worker in [t for t in threading.enumerate() if t.name == f"selfcheck-{rid}"]:
        worker.join(timeout=60)
        assert not worker.is_alive(), "the run's worker did not end"


def _running_hold(store, rid):
    """The live context of a started run and its G hold, once the run is running."""
    key, holder = lock_manager.lock_key("G", P, G), lock_manager.selfcheck_ctx_id(rid)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if _row(store, rid)["status"] == "running":
            for ctx in list(lock_manager._live_contexts):
                if ctx.ctx_id == holder and ctx.find_held(key) is not None:
                    return ctx, ctx.find_held(key)
        time.sleep(.05)
    raise AssertionError("the run did not start")


@pytest.mark.parametrize("case", ["gone", "gone_then_error", "unconfirmed"])
def test_a_lost_g_is_judged_before_the_run_is_recorded(world, monkeypatch, case):
    """④: the keeper and the worker of a real run. ``gone``: the keeper's first store read
    fails and the target then ends normally -- the worker judges the loss itself before its
    terminal write. ``gone_then_error``: the keeper finds G gone and cancels; the worker's
    result read then fails. ``unconfirmed``: the store never answers."""
    store, client = world["store"], world["client"]
    root = Path(service.git_service.effective_src_root_ex(P, G)[0])
    (root / "check_gate.py").write_text(
        "import os, time\nwhile not os.path.exists('go.flag'):\n    time.sleep(.05)\n", encoding="utf-8")
    real_regain, reads = lock_manager.selfcheck_regain, []

    def regain(ctx, key):
        reads.append(threading.current_thread().name)
        if case == "unconfirmed" or (case == "gone" and len(reads) == 1):
            return None                                   # the store could not be read
        return real_regain(ctx, key)

    monkeypatch.setattr(lock_manager, "selfcheck_regain", regain)
    if case == "gone_then_error":
        real_wait = executor.wait

        def failing_wait(*args):
            real_wait(*args)
            raise RuntimeError("result read failed")

        monkeypatch.setattr(executor, "wait", failing_wait)

    started = client.post(DRAFT_RUNS, headers=_as(RAW["new"]), json={"program": "python", "args": ["check_gate.py"]})
    assert started.status_code == 202, started.text
    rid = started.json()["self_check_run_id"]
    ctx, held = _running_hold(store, rid)
    if case != "unconfirmed":
        store._execute("DELETE FROM resource_lock WHERE lock_key = ?", [held.lock_key])   # G gone under the run
    lock_manager._heartbeats._lost(ctx, held)
    deadline = time.monotonic() + 10
    while not reads and time.monotonic() < deadline:
        time.sleep(.02)
    assert reads, "the keeper did not judge the lost hold"
    if case != "gone_then_error":
        (root / "go.flag").write_text("", encoding="utf-8")                # the target ends normally
    _worker_done(rid)

    row = _row(store, rid)
    assert row["status"] != "completed", "a run whose G loss was not judged was recorded as a result"
    if case == "unconfirmed":
        assert row["status"] == "running" and row["active_key"] and row["finished_at"] is None, row
        assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", service.LOCK_UNCONFIRMED)
        lock = _g(store)
        assert lock is not None and lock["lock_epoch"] == held.lock_epoch and int(lock["protected"]) == 1
        return
    assert (row["status"], row["error_code"], row["active_key"]) == ("failed", "selfcheck_lock_lost", None), row


@pytest.mark.parametrize("known", ["target", "nothing"])
def test_a_partial_spawn_stays_protected_after_its_worker(world, monkeypatch, known):
    """⑤: the spawn stamped the row and failed with a process still alive. ``target``: the
    error carries it (Windows target / POSIX supervisor) -- it must reach the stored row.
    ``nothing``: no PID was learnt (as when the server stops between Popen and the Job
    assignment) -- the empty Job must not prove the tree. Then the reaper, worker gone."""
    if known == "nothing" and os.name != "nt":
        pytest.skip("POSIX: the run marker scan covers a process no record names")
    store, client = world["store"], world["client"]
    live, identity = world["process"](alive=True)
    learnt = ({"process_owner_kind": "windows_job", "target_pid": live.pid, "target_start_identity": identity}
              if os.name == "nt" else
              {"process_owner_kind": "posix_supervisor", "supervisor_pid": live.pid,
               "supervisor_start_identity": identity})

    def failing_spawn(*args, record=None, **kwargs):
        record("spawning", {"process_owner_kind": learnt["process_owner_kind"]})
        raise executor.OwnershipError("selfcheck_process_owner_unavailable",
                                      ownership=learnt if known == "target" else {})

    monkeypatch.setattr(executor, "spawn", failing_spawn)
    started = client.post(DRAFT_RUNS, headers=_as(RAW["new"]), json={"program": "python", "args": ["check_ok.py"]})
    assert started.status_code == 202, started.text
    rid = started.json()["self_check_run_id"]
    _worker_done(rid)
    stored = _row(store, rid)
    if known == "target":
        pid_key = "target_pid" if os.name == "nt" else "supervisor_pid"
        assert stored[pid_key] == live.pid, "what the failed spawn learnt is not on the stored row"

    assert job_sweeper.step_selfcheck_reaper() == 0
    assert job_sweeper.step_selfcheck_reaper() == 0

    row = _row(store, rid)
    assert row["status"] == "pending" and row["active_key"] and row["error_code"] is None, row
    assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
    lock = _g(store)
    assert lock is not None and lock["holder_ctx_id"] == lock_manager.selfcheck_ctx_id(rid), "G was released"
    assert int(lock["protected"]) == 1
    assert live.poll() is None, "a process the run could not prove its own was killed"


def test_the_reaper_reaches_an_ended_runs_g_behind_unprovable_ones(world, monkeypatch):
    """⑥: protected Self-check G rows that never prove (their run is unknown here) sort
    before an ended run's protected G; with pages of 2 the third and later rows are past
    the first page."""
    process, store = world["process"], world["store"]
    monkeypatch.setattr(lock_manager, "SELFCHECK_PROTECTED_PAGE", 2)
    owner = f"inst_0080{uuid.uuid4().hex[:20]}"
    gdb.insert_instance(owner, registry.node_key(), os.getpid(), executor.start_identity(os.getpid()), now_iso())
    ahead = [lock_manager.lock_key("G", P, f"g_0080_ahead_{i}") for i in range(3)]
    with store.transaction():
        for i, key in enumerate(ahead):
            ahead_epoch = lock_manager._new_epoch()
            gdb.insert_lock(lock_key=key, domain="G", project_id=P, group_id=f"g_0080_ahead_{i}", target_key=None,
                            holder_ctx_id=lock_manager.selfcheck_ctx_id(f"scr_0080unknown{i}"), holder_kind="selfcheck",
                            job_id=None, instance_id=owner, lock_epoch=ahead_epoch, hold_class="long",
                            acquired_at=f"2000-01-0{i + 1}T00:00:00+00:00", heartbeat_until=None)
            gdb.protect_lock(key, ahead_epoch, "selfcheck_reap_unconfirmed", now_iso())
    rid, epoch = world["run"](process(alive=False), process(alive=False))    # owner, target, tree gone
    store._execute("UPDATE tr_self_check_runs SET status = 'failed', active_key = NULL, finished_at = ?"
                   " WHERE self_check_run_id = ?", [now_iso(), rid])
    gdb.protect_lock(lock_manager.lock_key("G", P, G), epoch, "selfcheck_reap_unconfirmed", now_iso())

    assert job_sweeper.step_selfcheck_reaper() == 1

    assert _g(store) is None, "the ended run's protected G was never reached"
    left = store._fetch_all("SELECT lock_key FROM resource_lock WHERE protected = 1 AND holder_kind = 'selfcheck'")
    assert sorted(r["lock_key"] for r in left) == sorted(ahead)
