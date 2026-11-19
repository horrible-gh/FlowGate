"""0110 T#3 rework: a Graph attempt run's W follows the run's proof, like its Group's G.

An attempt token's source writes (``graph_run.workspace.mutation``) take the attempt's W alone -- no G.
0053 1-B: a Self-check run of an attempt is queued with a snapshot of the attempt tree and runs in that snapshot,
so it takes neither G nor the attempt's W:

1. while the run executes, an attempt write goes through; what it writes does not reach the run;
2. when the run ends with its process tree not proved empty (a partial spawn), only its execution slot stays taken
   (recovery_incomplete, its reservation) -- no G or W is protected, attempt writes go on -- and the reaper ends
   it once the tree is proved empty;
3. (pre-queue runs) a stale W (its owner instance judged DEAD) is reclaimed only through the Self-check gate: owner alive -> kept
   (BUSY), owner dead but a child unproved -> protected (RECOVERY_REQUIRED, run recovery_incomplete) until a
   re-verification proves the tree and frees G and W together, owner dead and tree empty -> reclaimed.

Fixtures: ``flow`` of tests.e2e.self_check.test_tr_self_check_draft_e2e_0638 and ``world`` of
test_tr_self_check_recover_proof_0080 (migrated SQLite, real ``lock_manager`` / ``resource_lock``, real
``_start`` / ``_finish`` / reaper / gate, real short python children). The TR(new) token is mapped to a Graph attempt
whose tree is a throwaway repository: the port's mapping, root handler and attempt row read are answered here, and no
Stop gate handler is registered (the gate has its own cases in test_graph_attempt_workspaces_0061). An attempt write
is the W acquire ``workspace.mutation`` makes (same key from ``workspace.location``, same holder kind). Injected, as in
0080: a failing spawn (``executor.spawn``) and an unreadable identity (``executor.probe_start_identity``).
"""
from __future__ import annotations

import os
import subprocess
import threading
from types import SimpleNamespace

import pytest

from tests.e2e.self_check.test_tr_self_check_draft_e2e_0638 import DRAFT_RUNS, P, RAW, _as, _row, flow  # noqa: F401 -- fixture
from tests.regression.self_check.test_tr_self_check_recover_proof_0080 import _g, world  # noqa: F401 -- fixture

from modules.flow_gate.services import graph_source_hooks as port
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_service as service
from modules.flow_gate.services.git import job_sweeper
from modules.flow_gate.services.git import lock_manager

ATTEMPT = "att_0110w_selfcheck"


@pytest.fixture
def attempt(world, monkeypatch, tmp_path):
    from flowgate.graph_run import workspace
    root = tmp_path / "attempt"
    root.mkdir()
    (root / "check_ok.py").write_text("print('selfcheck-0110-attempt')\n", encoding="utf-8")
    (root / "check_gate.py").write_text(
        "import os, time\nwhile not os.path.exists('go.flag'):\n    time.sleep(.05)\n", encoding="utf-8")
    for args in (("init", "-q"), ("add", "-A"),
                 ("-c", "user.email=e2e@test", "-c", "user.name=e2e", "commit", "-q", "-m", "attempt")):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()
    monkeypatch.setattr(port, "binding", lambda token: ATTEMPT if (token or {}).get("token_id") == "tok_e2e_new" else None)
    monkeypatch.setattr(port, "_handler", lambda attempt_id, **_kw: root)
    monkeypatch.setattr(port, "_fence_handler", None)
    monkeypatch.setattr(port, "run_store", SimpleNamespace(
        _one=lambda _sql, _params: {"input_commit": head, "input_source": "run_base"}))
    monkeypatch.setattr(workspace.git, "get_storage_root", lambda: tmp_path / "storage")
    monkeypatch.setenv("FLOWGATE_INTERACTIVE_WAIT_SEC", "0")
    branch = workspace.location({"attempt_id": ATTEMPT}, P)[2]
    return SimpleNamespace(root=root, head=head, branch=branch, **world)


def _w(store, branch):
    return store._fetch_one("SELECT * FROM resource_lock WHERE lock_key = ?",
                            [lock_manager.lock_key("W", P, target_key=branch)])


def _attempt_write(branch):
    """The W an attempt token's source write takes (``workspace.mutation``: W alone, holder_kind source_mutation)."""
    ctx = lock_manager.new_context("req")
    outcome = lock_manager.acquire("W", P, target_key=branch, holder_kind="source_mutation", ctx=ctx)
    if outcome.ok:
        lock_manager.release(ctx, outcome.lock_key)
    return outcome


def _worker_done(rid):
    for worker in [t for t in threading.enumerate() if t.name == f"selfcheck-{rid}"]:
        worker.join(timeout=60)
        assert not worker.is_alive(), "the run's worker did not end"


def _settled(store, rid, timeout=60.0):
    """Wait until a queued run was claimed and its worker is done with it (ended or held)."""
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = _row(store, rid)
        if row["status"] in ("completed", "failed", "cancelled") or row["recovery_state"] != "none":
            break
        time.sleep(0.05)
    _worker_done(rid)
    return _row(store, rid)


def test_an_attempt_run_is_queued_from_its_tree_and_never_takes_its_w(attempt):
    import time
    store, client = attempt.store, attempt.client
    started = client.post(DRAFT_RUNS, headers=_as(RAW["new"]), json={"program": "python", "args": ["check_gate.py"]})
    assert started.status_code == 202, started.text
    body = started.json()
    rid = body["self_check_run_id"]
    assert (body["attempt_id"], body["input_commit"]) == (ATTEMPT, attempt.head)
    assert _row(store, rid)["queue_key"] == f"attempt:{ATTEMPT}"
    deadline = time.monotonic() + 30
    while _row(store, rid)["status"] != "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert _row(store, rid)["status"] == "running"
    assert _w(store, attempt.branch) is None and _g(store) is None            # no W, no G
    assert _attempt_write(attempt.branch).ok                                  # attempt writes go on
    (attempt.root / "go.flag").write_text("", encoding="utf-8")               # written to the attempt tree...
    time.sleep(0.5)
    assert _row(store, rid)["status"] == "running"                            # ...does not reach the snapshot
    snapshot_tree = os.path.join(_row(store, rid)["snapshot_path"], "tree")
    with open(os.path.join(snapshot_tree, "go.flag"), "w", encoding="utf-8"):
        pass
    row = _settled(store, rid)
    assert (row["status"], row["exit_code"], row["attempt_id"]) == ("completed", 0, ATTEMPT), row
    assert _w(store, attempt.branch) is None and _g(store) is None
    assert _attempt_write(attempt.branch).ok


def test_an_unproved_end_keeps_only_the_runs_slot_until_the_tree_is_proved(attempt, monkeypatch):
    """The real ``_finish`` after a partial spawn (a live process learnt, as 0080 ⑤): the run is held as
    recovery_incomplete with its reservation; no G or W is protected."""
    from modules.flow_gate.db import tr_self_check_runs as runs_db
    store, client = attempt.store, attempt.client
    live, identity = attempt.process(alive=True)
    learnt = ({"process_owner_kind": "windows_job", "target_pid": live.pid, "target_start_identity": identity}
              if os.name == "nt" else
              {"process_owner_kind": "posix_supervisor", "supervisor_pid": live.pid,
               "supervisor_start_identity": identity})

    def failing_spawn(*args, record=None, **kwargs):
        record("spawning", {"process_owner_kind": learnt["process_owner_kind"]})
        raise executor.OwnershipError("selfcheck_process_owner_unavailable", ownership=learnt)

    monkeypatch.setattr(executor, "spawn", failing_spawn)
    started = client.post(DRAFT_RUNS, headers=_as(RAW["new"]), json={"program": "python", "args": ["check_ok.py"]})
    assert started.status_code == 202, started.text
    rid = started.json()["self_check_run_id"]
    row = _settled(store, rid)
    assert row["status"] == "pending" and row["active_key"] is None, row
    assert (row["recovery_state"], row["recovery_reason"]) == ("incomplete", proof.TREE_UNPROVEN)
    assert _g(store) is None and _w(store, attempt.branch) is None            # nothing protected but the slot
    assert _attempt_write(attempt.branch).ok
    reserved = row["cpu_reservation"]
    assert reserved >= 1 and runs_db.host_reservations(row["host_node_key"], 64)[1] >= reserved

    assert job_sweeper.step_selfcheck_reaper() == 0                 # tree still unproved: the slot stays
    assert _row(store, rid)["recovery_state"] == "incomplete" and live.poll() is None

    live.kill()
    live.wait(timeout=10)
    assert job_sweeper.step_selfcheck_reaper() == 1

    row = _row(store, rid)
    assert (row["status"], row["error_code"], row["active_key"]) == ("failed", "interrupted_by_restart", None), row
    assert _w(store, attempt.branch) is None and _g(store) is None
    assert runs_db.host_reservations(row["host_node_key"], 64) == (0, 0)


@pytest.mark.parametrize("case", ["owner_alive", "child_alive", "proved"])
def test_a_stale_attempt_w_is_reclaimed_only_on_the_selfcheck_proof(attempt, monkeypatch, case):
    store, process = attempt.store, attempt.process
    owner = process(alive=case == "owner_alive")
    target = process(alive=case != "proved")
    unreadable = set()
    real_probe = executor.probe_start_identity
    monkeypatch.setattr(executor, "probe_start_identity",
                        lambda pid: ("unknown", None) if int(pid) in unreadable else real_probe(pid))
    if case == "child_alive":
        unreadable.add(target[0].pid)
    rid, _epoch = attempt.run(owner, target)                       # a running run and its G, held by `owner`
    owner_instance = _g(store)["instance_id"]
    store._execute("UPDATE tr_self_check_runs SET attempt_id = ?, input_commit = ? WHERE self_check_run_id = ?",
                   [ATTEMPT, attempt.head, rid])
    ctx = lock_manager.new_context("scr", rid)
    w_key = service._attempt_lock(ctx, P, ATTEMPT)                 # the W _admit takes for the run
    ctx.held.clear()                                               # the owner server holds it, not this process
    store._execute("UPDATE resource_lock SET instance_id = ? WHERE lock_key = ?", [owner_instance, w_key])
    store._execute("UPDATE server_instance SET status = 'dead' WHERE instance_id = ?", [owner_instance])
    w = _w(store, attempt.branch)
    assert w["lock_key"] == w_key and w["holder_kind"] == "selfcheck"
    assert lock_manager.is_stale(w) == lock_manager.STALE_RECLAIMABLE    # the instance state alone nominates it

    outcome = _attempt_write(attempt.branch)

    row = _row(store, rid)
    if case == "owner_alive":
        # The owner's process still runs (its DEAD instance row is no evidence): kept as it is.
        assert outcome.kind == lock_manager.BUSY, outcome
        kept = _w(store, attempt.branch)
        assert kept["lock_epoch"] == w["lock_epoch"] and int(kept["protected"]) == 0
        assert (row["status"], row["recovery_state"]) == ("running", "none")
        assert owner[0].poll() is None and target[0].poll() is None
        return
    if case == "child_alive":
        assert outcome.kind == lock_manager.RECOVERY_REQUIRED, outcome
        assert int(_w(store, attempt.branch)["protected"]) == 1
        assert (row["status"], row["recovery_state"], row["recovery_reason"]) == (
            "running", "incomplete", proof.TREE_UNPROVEN)
        assert target[0].poll() is None, "a process whose identity could not be read was signalled"
        # Proved later: the re-verification ends the run and frees its G and W together.
        target[0].kill()
        target[0].wait(timeout=10)
        unreadable.clear()
        released = service.reverify(rid)
        assert released["reverify"] == {"released": True, "reason": None}
        assert _w(store, attempt.branch) is None and _g(store) is None
        assert _attempt_write(attempt.branch).ok
        return
    assert outcome.ok, outcome
    assert (row["status"], row["error_code"]) == ("failed", "interrupted_by_restart"), row
    assert _w(store, attempt.branch) is None
