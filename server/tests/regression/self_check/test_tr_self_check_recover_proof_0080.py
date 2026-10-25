"""0080 T#1: restart recovery ends a Self-check run only on proof (NR0007 §5-0, §6 item 5).

recover() may fail a run (interrupted_by_restart) and delete its Group's G only after the
owning server is proved dead and then the run's whole process tree is proved empty. The
card's three core checks:

1. an identity lookup that fails while the process is really alive releases nothing --
   neither for the run's target (owner proved dead: kept protected, ``tree_unproven``)
   nor for the owning server (owner unproven: the row is not touched at all);
2. a group / Job member that outlives the direct child and the kill attempt keeps the run
   protected (``tree_unproven``);
3. control: owner dead, target and tree gone -> failed + G deleted.

And the two prevention paths of the same card:

4. a spawn that fails after it may have created processes (handshake failure, a cleanup
   wait that timed out) ends nothing while the tree is MATCH or UNKNOWN: the run keeps its
   ``active_key``, goes ``recovery_incomplete`` (``tree_unproven``) and G stays protected;
5. the POSIX supervisor never signals a group it cannot prove the run's: a reaped target's
   number (possibly reused) and a group failing the shared ownership rule get no group signal.

Fixture: ``flow`` of tests.e2e.self_check.test_tr_self_check_draft_e2e_0638 (migrated SQLite, project P / group
G, open TR(new) token). The run row, the owner's server_instance row and the run's G are
written directly; the processes are real short python children. What the OS cannot be made
to do on demand is injected at the proof's probes: the identity lookup failure
(``executor.probe_start_identity``), survivors that a kill does not remove
(``proof.posix_group_state`` / ``proof.windows_job_state``), an unreadable tree
(``proof.marker_scan``), a failing spawn (``executor.spawn``) and the ``/proc`` table the
group ownership rule reads (``proof._proc_stat_table``).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from tests.e2e.self_check.test_tr_self_check_draft_e2e_0638 import DRAFT_RUNS, G, P, RAW, SEQ_DOC, _as, _row, flow  # noqa: F401 -- fixture

from modules.flow_gate.db import git_concurrency as gdb
from modules.flow_gate.db import tr_self_check_runs as db_runs
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_service as service
from modules.flow_gate.services import tr_self_check_supervisor as supervisor
from modules.flow_gate.services.git import instance_registry as registry
from modules.flow_gate.services.git import lock_manager


@pytest.fixture
def world(flow):
    store, procs = flow["store"], []

    def process(alive: bool):
        """A real child and its start marker; ``alive=False`` = it has really finished."""
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        procs.append(proc)
        deadline = time.monotonic() + 10
        identity = executor.start_identity(proc.pid)
        while identity is None and time.monotonic() < deadline:
            time.sleep(.05)
            identity = executor.start_identity(proc.pid)
        assert identity, "no start marker for a fresh child"
        if not alive:
            proc.kill()
            proc.wait(timeout=10)
        return proc, identity

    def run(owner_proc, target_proc):
        """A running run owned by ``owner_proc`` (as a same-node server instance) whose
        target is ``target_proc``, plus its Group's G held by that owner."""
        rid = f"scr_0080{uuid.uuid4().hex[:20]}"
        owner = f"inst_0080{uuid.uuid4().hex[:20]}"
        gdb.insert_instance(owner, registry.node_key(), owner_proc[0].pid, owner_proc[1], now_iso())
        epoch = lock_manager._new_epoch()
        with store.transaction():
            gdb.insert_lock(lock_key=lock_manager.lock_key("G", P, G), domain="G", project_id=P, group_id=G,
                            target_key=None, holder_ctx_id=lock_manager.selfcheck_ctx_id(rid),
                            holder_kind="selfcheck", job_id=None, instance_id=owner, lock_epoch=epoch,
                            hold_class="long", acquired_at=now_iso(),
                            heartbeat_until=registry.add_seconds(now_iso(), 90))
        db_runs.create_pending(P, G, None, None, "0080", "python", ["check_slow.py"], sys.executable,
                               "python", "path", ".", 60, ["PATH", proof.MARKER_ENV], run_id=rid,
                               source_lock_holder=lock_manager.selfcheck_ctx_id(rid),
                               owner_token_id="tok_e2e_new", draft_doc_ref=SEQ_DOC,
                               owner_instance_id=owner, owner_marker=proof.owner_marker(rid))
        ownership = {"process_owner_kind": "windows_job", "target_pid": target_proc[0].pid,
                     "target_start_identity": target_proc[1]}
        if os.name != "nt":
            supervisor = process(alive=False)
            ownership.update(process_owner_kind="posix_supervisor", supervisor_pid=supervisor[0].pid,
                             supervisor_start_identity=supervisor[1])
        assert db_runs.mark_running(rid, source_lock_holder=lock_manager.selfcheck_ctx_id(rid), **ownership)
        return rid, epoch

    try:
        yield {"store": store, "client": flow["client"], "process": process, "run": run}
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        # These rows have no worker: end them so the e2e fixture's drain does not wait on them.
        store._execute("UPDATE tr_self_check_runs SET status = 'failed', active_key = NULL"
                       " WHERE project_id = ? AND status IN ('pending', 'running')", [P])


def _lookup_fails(monkeypatch, *pids):
    real = executor.probe_start_identity
    monkeypatch.setattr(executor, "probe_start_identity",
                        lambda pid: ("unknown", None) if int(pid) in pids else real(pid))


def _g(store):
    return store._fetch_one("SELECT * FROM resource_lock WHERE lock_key = ?", [lock_manager.lock_key("G", P, G)])


def _assert_not_released(store, rid, epoch):
    row = _row(store, rid)
    assert row["status"] == "running" and row["active_key"], row
    assert row["error_code"] is None and row["finished_at"] is None, row
    lock = _g(store)
    assert lock is not None and lock["lock_epoch"] == epoch, "G was deleted or replaced"
    return row, lock


@pytest.mark.parametrize("failing", ["target", "owner_server"])
def test_a_failed_identity_lookup_of_a_live_process_releases_nothing(world, monkeypatch, failing):
    process, store = world["process"], world["store"]
    if failing == "target":
        # The owner is really gone; the target is alive but its lookup fails.
        owner, target = process(alive=False), process(alive=True)
        live = target[0]
    else:
        # The owner server is alive but its lookup fails; the target already exited.
        owner, target = process(alive=True), process(alive=False)
        live = owner[0]
    rid, epoch = world["run"](owner, target)
    _lookup_fails(monkeypatch, live.pid)

    service.recover()

    row, lock = _assert_not_released(store, rid, epoch)
    assert live.poll() is None, "a process whose identity could not be read was killed"
    if failing == "target":
        # Owner proved dead, tree unproven: kept as recovery_incomplete with G protected.
        assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
        assert int(lock["protected"]) == 1
    else:
        # Owner not proved dead: the row is not touched at all (no fencing of a live worker).
        assert (row["recovery_state"], row["recovery_reason"]) == ("none", None)
        assert int(lock["protected"]) == 0


def test_a_group_member_left_after_the_direct_child_keeps_the_run_protected(world, monkeypatch):
    process, store = world["process"], world["store"]
    rid, epoch = world["run"](process(alive=False), process(alive=False))
    # Target and supervisor are gone, but the target's group / the run's Job still has a
    # member that the verified kill does not remove.
    calls = []
    monkeypatch.setattr(proof, "posix_group_state", lambda pgid: proof.MATCH)
    monkeypatch.setattr(proof, "windows_job_state",
                        lambda name, **kw: calls.append(kw) or proof.MATCH)

    service.recover()

    row, lock = _assert_not_released(store, rid, epoch)
    assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
    assert int(lock["protected"]) == 1
    if os.name == "nt":
        assert any(kw.get("terminate") for kw in calls), "the run's Job was not asked to terminate"


def test_a_dead_owner_with_an_empty_tree_is_ended_and_its_g_released(world):
    process, store = world["process"], world["store"]
    rid, _epoch = world["run"](process(alive=False), process(alive=False))

    service.recover()

    row = _row(store, rid)
    assert (row["status"], row["error_code"], row["recovery_state"]) == ("failed", "interrupted_by_restart", "recovered")
    assert row["active_key"] is None
    assert _g(store) is None


@pytest.mark.parametrize("tree", ["match", "unknown"])
def test_a_spawn_that_fails_after_creating_processes_ends_nothing_unproved(world, monkeypatch, tree):
    store, client = world["store"], world["client"]
    if tree == "match":
        # The handshake failed after the supervisor (POSIX) / target (Windows) was created and
        # its cleanup wait timed out: spawn learnt a process that is still alive.
        live, identity = world["process"](alive=True)
        learnt = ({"process_owner_kind": "windows_job", "target_pid": live.pid, "target_start_identity": identity}
                  if os.name == "nt" else
                  {"process_owner_kind": "posix_supervisor", "supervisor_pid": live.pid,
                   "supervisor_start_identity": identity})
    else:
        # Nothing was learnt and the tree cannot be seen (Job / marker scan unreadable).
        live, learnt = None, {}
        monkeypatch.setattr(proof, "windows_job_state", lambda name, **kw: proof.UNKNOWN)
        monkeypatch.setattr(proof, "marker_scan", lambda run_id: (proof.UNKNOWN, ()))

    def failing_spawn(*args, **kwargs):
        raise executor.OwnershipError("selfcheck_process_owner_unavailable", ownership=learnt)

    monkeypatch.setattr(executor, "spawn", failing_spawn)

    started = client.post(DRAFT_RUNS, headers=_as(RAW["new"]), json={"program": "python", "args": ["check_ok.py"]})
    assert started.status_code == 202, started.text
    rid = started.json()["self_check_run_id"]
    for worker in [t for t in threading.enumerate() if t.name == f"selfcheck-{rid}"]:
        worker.join(timeout=30)
        assert not worker.is_alive()

    row = _row(store, rid)
    assert row["status"] == "pending" and row["active_key"], row
    assert row["error_code"] is None and row["finished_at"] is None, row
    assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
    lock = _g(store)
    assert lock is not None and lock["holder_ctx_id"] == lock_manager.selfcheck_ctx_id(rid), "G was released"
    assert int(lock["protected"]) == 1
    if live is not None:
        assert live.poll() is None, "a process the run could not prove its own was killed"


@pytest.mark.parametrize("case", ["reaped", "reused_leader_pid", "unreadable", "owned"])
def test_the_supervisor_signals_no_group_it_cannot_prove_the_runs(monkeypatch, case):
    leader, start = 4242, "1000"
    # The /proc table the shared rule (proof.posix_group_members) reads.
    if case == "reused_leader_pid":
        # The target's number now names another process (later start) that made its own
        # session/group, with a child of its own.
        table, complete = [(leader, leader, leader, 5000), (leader + 1, leader, leader, 5001)], True
    elif case == "unreadable":
        table, complete = [(leader, leader, leader, 1000)], False
    else:
        # The run's group: the exited (unreaped) target and a descendant it left behind.
        table, complete = [(leader, leader, leader, 1000), (leader + 1, leader, leader, 1001)], True
    monkeypatch.setattr(proof, "_proc_stat_table", lambda: (list(table), complete))
    exited = {"value": case != "unreadable"}
    sent = []

    def group(pgid, sig):
        sent.append(("group", pgid, sig))
        table[:] = [entry for entry in table if entry[0] == leader]   # the descendant dies

    def leader_signal(pid, sig):
        sent.append(("leader", pid, sig))
        exited["value"] = True

    monkeypatch.setattr(supervisor, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
    monkeypatch.setattr(supervisor, "_signal_group", group)
    monkeypatch.setattr(supervisor, "_signal_leader", leader_signal)
    monkeypatch.setattr(supervisor, "_leader_exited", lambda proc: proc.returncode is not None or exited["value"])
    reaped = []
    proc = SimpleNamespace(pid=leader, returncode=0 if case == "reaped" else None)
    proc.wait = lambda: reaped.append(True)

    supervisor._terminate(proc, start)

    if case in ("reaped", "reused_leader_pid"):
        # A reaped target's number may be someone else's group; a group whose leader PID
        # is another process fails the rule: no TERM, no KILL.
        assert sent == []
    elif case == "unreadable":
        # Ownership unproven: only the unreaped target itself (its PID is still held) is signalled.
        assert sent == [("leader", leader, 15)]
    else:
        assert sent == [("group", leader, 15)]   # proved the run's: the leftover goes
    assert reaped == ([] if case == "reaped" else [True])
