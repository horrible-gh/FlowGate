"""0146: parent boundaries with a real Self-check worker/tree and temporary DB/G."""
from __future__ import annotations

import threading
import time

import pytest

from tests.e2e.self_check.test_tr_self_check_draft_e2e_0638 import (
    flow, P, G, RAW, DRAFT_RUNS, _as, _wait, _document, TR_DOC, SEQ_DOC)  # noqa: F401
from tests.modules.flow_gate.services.test_self_check_progress_0146 import Clock, TickEvent, Proc
from modules.flow_gate.db import tr_self_check_runs as db
from modules.flow_gate.services import tr_self_check_progress as progress
from modules.flow_gate.services import tr_self_check_service as service
from modules.flow_gate.services import tr_self_check_proof as proof
from modules.flow_gate.services import tr_self_check_executor as executor
from modules.flow_gate.services import ai_invoke_service as ai
from modules.flow_gate.services import token_service, process_runner
from modules.flow_gate.services.ai_invoke import worker, chain, provider_cli
from modules.flow_gate.services.git import lock_manager


@pytest.fixture(autouse=True)
def registry_isolation(monkeypatch):
    monkeypatch.setattr(progress, "_bindings", {})
    monkeypatch.setattr(progress, "_managed", {})
    monkeypatch.setattr(progress, "_samples", {})
    monkeypatch.setattr(progress, "_probe", None)


def parent():
    return dict(run_id="aiv_parent0146", project_id=P, group_id=G,
                token_id="tok_e2e_new", fallback_history=[], cancel_event=threading.Event(),
                timed_out=False, status="running", baseline_seq=0, timeout_sec=5,
                started_mono=1000., stall_anchor_mono=1000.)


def start(flow, script="check_slow.py"):
    response = flow["client"].post(DRAFT_RUNS, headers=_as(RAW["new"]),
                                  json=dict(program="python", args=[script], timeout_seconds=60))
    assert response.status_code == 202, response.text
    return response.json()["self_check_run_id"]


def wait(flow, rid, statuses={"completed", "failed", "cancelled"}):
    return _wait(flow["client"], DRAFT_RUNS + "/" + rid, _as(RAW["new"]), statuses=statuses)


def released(flow, rid):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        lock = flow["store"]._fetch_one("SELECT * FROM resource_lock WHERE lock_key = ?",
                                       [lock_manager.lock_key("G", P, G)])
        if lock is None and rid not in service._controls:
            break
        time.sleep(.02)
    assert lock is None and rid not in service._controls
    assert db.get_run(rid)["active_key"] is None
    assert db.host_reservations(None, 64)[0] == 0


@pytest.mark.parametrize("mode", ["no_progress", "absolute_cap", "user_cancel", "forced_exit",
                                 "provider_exception", "startup_failure"])
def test_provider_boundary_stops_real_owned_child_and_allows_next_attempt(flow, monkeypatch, mode):
    run, saved = parent(), {}
    monkeypatch.setattr(worker, "_provider_prompt", lambda run, provider, prompt: prompt)
    monkeypatch.setattr(worker.role_invocations, "begin", lambda *a: None)
    monkeypatch.setattr(worker.role_invocations, "finish", lambda *a: None)
    def execute(provider, prompt, current):
        rid = start(flow)
        wait(flow, rid, {"running"})
        saved["rid"] = rid
        row = db.get_run(rid)
        assert proof.tree_state(row).state != proof.ABSENT
        if mode in ("no_progress", "absolute_cap"):
            clock = Clock()
            monkeypatch.setattr(ai, "_now_mono", clock)
            monkeypatch.setattr(ai.db_docs, "get_group_max_seq", lambda _: 0)
            monkeypatch.setattr(progress, "poll", lambda *a: False)
            monkeypatch.setattr(process_runner, "process_tree_snapshot", lambda _: None)
            monkeypatch.setattr(process_runner, "kill_process_tree", lambda _: None)
            if mode == "absolute_cap":
                monkeypatch.setattr(provider_cli, "_absolute_cap_sec", lambda: 3)
            verdict = ai._progress_watchdog_loop(current, Proc(), TickEvent(clock, lambda _: None), 1)
            assert verdict == mode and current["timed_out"]
            saved["watchdog"] = dict(current["watchdog_kill"])
        elif mode == "user_cancel":
            monkeypatch.setattr(ai, "get_run_record", lambda _: current)
            assert chain.cancel_run(current["run_id"])["status"] == "cancelling"
        elif mode == "provider_exception":
            raise OSError("controlled provider exit")
        else:
            current["exit_code"] = -9 if mode == "forced_exit" else 2
        return ("fast_fail" if mode == "startup_failure" else "started_ok"), None
    monkeypatch.setattr(ai, "_cli_execute", execute)
    if mode == "provider_exception":
        with pytest.raises(OSError):
            worker._execute_provider_chain(run, [dict(id="test", exec_type="cli")], "test")
    else:
        worker._execute_provider_chain(run, [dict(id="test", exec_type="cli")], "test")
    rid = saved["rid"]
    stopped = wait(flow, rid)
    assert stopped["status"] == "cancelled" and stopped["cancel_requested"]
    assert proof.tree_state(db.get_run(rid)).state == proof.ABSENT
    released(flow, rid)
    if mode in ("no_progress", "absolute_cap"):
        assert run["watchdog_kill"] == saved["watchdog"] and run["timed_out"]
    before = db.get_run(rid)
    progress.end_owned(run, boundary="run", reason="duplicate")
    assert db.get_run(rid) == before
    next_run = parent()
    next_run["run_id"] = "aiv_next0146"
    progress.open_attempt(next_run)
    next_id = start(flow, "check_ok.py")
    done = wait(flow, next_id)
    assert done["status"] == "completed" and done["exit_code"] == 0
    released(flow, next_id)
    progress.end_owned(run, boundary="run", reason="delayed_previous")
    assert db.get_run(next_id)["status"] == "completed"
    assert not db.get_run(next_id)["cancel_requested"]
    progress.end_owned(next_run, boundary="run")


@pytest.mark.parametrize("phase", ["before_worker", "after_spawn"])
def test_close_around_worker_spawn_preserves_slot_until_real_stop(flow, monkeypatch, phase):
    run = parent()
    progress.open_attempt(run)
    entered, release = threading.Event(), threading.Event()
    spawned = []
    real_spawn, real_finish = executor.spawn, service._finish
    def spawn(*a, **kw):
        result = real_spawn(*a, **kw)
        spawned.append(result)
        if phase == "after_spawn":
            entered.set()
            assert release.wait(10)
        return result
    def finish(*a, **kw):
        if phase == "before_worker":
            entered.set()
            assert release.wait(10)
        return real_finish(*a, **kw)
    monkeypatch.setattr(executor, "spawn", spawn)
    monkeypatch.setattr(service, "_finish", finish)
    rid = start(flow)
    try:
        assert entered.wait(10)
        progress.end_owned(run, boundary="hop", reason="concurrent_end")
        row = db.get_run(rid)
        assert row["status"] == "pending" and row["cancel_requested"] and row["active_key"]
        assert db.host_reservations(None, 64)[0] >= 1
        refused = flow["client"].post(DRAFT_RUNS, headers=_as(RAW["new"]),
                                     json=dict(program="python", args=["check_ok.py"]))
        assert refused.status_code == 409
    finally:
        release.set()
    assert wait(flow, rid)["status"] == "cancelled"
    assert bool(spawned) == (phase == "after_spawn")
    if spawned:
        assert spawned[0][0].proc.poll() is not None
        assert proof.tree_state(db.get_run(rid)).state == proof.ABSENT
    released(flow, rid)


def test_normal_provider_detach_natural_completion_and_linked_result_remain(flow):
    run = parent()
    progress.open_attempt(run)
    rid = start(flow, "check_ok.py")
    progress.end_owned(run, cancel=False, reason="provider_return")
    result = wait(flow, rid)
    assert result["status"] == "completed" and not result["cancel_requested"]
    released(flow, rid)
    before_owner = flow["store"]._fetch_one(
        "SELECT * FROM tr_self_check_run_progress WHERE self_check_run_id = ?", [rid])
    _document(flow["store"], TR_DOC, G, "TR", 3)
    assert token_service.consume("tok_e2e_new", P, doc_id=TR_DOC, require_claim=True)
    before = db.get_run(rid)
    progress.end_owned(run, boundary="run", reason="hop_complete")
    assert db.get_run(rid) == before
    assert service.read(TR_DOC, rid)["status"] == "completed"
    assert flow["store"]._fetch_one(
        "SELECT * FROM tr_self_check_run_progress WHERE self_check_run_id = ?", [rid]) == before_owner


@pytest.mark.parametrize("timed_out", [False, True])
def test_late_cancel_after_executor_result_keeps_natural_or_timeout_result(flow, monkeypatch, timed_out):
    run = parent()
    progress.open_attempt(run)
    real = executor.wait
    def wait_then_cancel(control, timeout, cancelled):
        result = real(control, 1 if timed_out else timeout, cancelled)
        assert result.timed_out == timed_out and not result.cancelled
        # Parent closure wins the DB request flag after the actual result already exists.
        progress.end_owned(run, boundary="hop", reason="late_parent_exit")
        return result
    monkeypatch.setattr(executor, "wait", wait_then_cancel)
    rid = start(flow, "check_slow.py" if timed_out else "check_ok.py")
    result = wait(flow, rid)
    assert result["status"] == "completed" and result["timed_out"] == timed_out
    assert result["cancel_requested"]
    if not timed_out:
        assert result["exit_code"] == 0 and "selfcheck-0638-ok" in result["stdout_tail"]
    released(flow, rid)


def test_terminate_failure_keeps_tree_slot_and_cpu_until_real_stop(flow, monkeypatch):
    run = parent()
    progress.open_attempt(run)
    rid = start(flow)
    wait(flow, rid, {"running"})
    control = service._controls[rid][0]
    real = control.cancel
    monkeypatch.setattr(control, "cancel", lambda: False)
    try:
        progress.end_owned(run, boundary="run", reason="failed_terminate")
        row = db.get_run(rid)
        assert row["status"] == "running" and row["active_key"] and row["cancel_requested"]
        assert row["finished_at"] is None and control.proc.poll() is None
        assert db.host_reservations(None, 64)[0] >= 1
        assert flow["store"]._fetch_one("SELECT * FROM resource_lock WHERE lock_key = ?",
                                      [lock_manager.lock_key("G", P, G)])
    finally:
        monkeypatch.setattr(control, "cancel", real)
        assert real()
    assert wait(flow, rid)["status"] == "cancelled"
    released(flow, rid)


def test_database_cancel_watcher_handles_absent_local_controller(flow, monkeypatch):
    run = parent()
    progress.open_attempt(run)
    rid = start(flow)
    wait(flow, rid, {"running"})
    # Model a request handled by a server without this worker's local control.
    item = service._controls.pop(rid)
    try:
        progress.end_owned(run, boundary="hop", reason="other_server")
        assert db.get_run(rid)["cancel_requested"]
        assert wait(flow, rid)["status"] == "cancelled"
        assert item[0].proc.poll() is not None
    finally:
        # The worker removes its registry entry itself; never revive it afterwards.
        if db.get_run(rid)["status"] in {"pending", "running"}:
            service._controls[rid] = item
            item[0].cancel()
    released(flow, rid)


def test_live_child_continues_after_successful_provider_return_until_natural_exit(flow, monkeypatch, tmp_path):
    run = parent()
    release = tmp_path / "release-child"
    root = service._worktree(dict(project_id=P, group_id=G))
    (root / "controlled.py").write_text(
        "import pathlib,time\np=pathlib.Path(" + repr(str(release)) + ")\n"
        "deadline=time.monotonic()+20\n"
        "while not p.exists() and time.monotonic()<deadline: time.sleep(.02)\n"
        "assert p.exists()\nprint('natural-owned-child')\n", encoding="utf-8")
    saved = {}
    monkeypatch.setattr(worker, "_provider_prompt", lambda *a: "test")
    monkeypatch.setattr(worker.role_invocations, "begin", lambda *a: None)
    monkeypatch.setattr(worker.role_invocations, "finish", lambda *a: None)
    def execute(*args):
        saved["rid"] = start(flow, "controlled.py")
        wait(flow, saved["rid"], {"running"})
        return "started_ok", None
    monkeypatch.setattr(ai, "_cli_execute", execute)
    assert worker._execute_provider_chain(run, [dict(id="normal", exec_type="cli")], "test")
    rid = saved["rid"]
    row = db.get_run(rid)
    assert row["status"] == "running" and not row["cancel_requested"]
    release.write_text("finish", encoding="utf-8")
    done = wait(flow, rid)
    assert done["status"] == "completed" and done["exit_code"] == 0
    assert "natural-owned-child" in done["stdout_tail"]
    released(flow, rid)
    before = db.get_run(rid)
    progress.end_owned(run, boundary="hop", reason="normal_hop_end")
    assert db.get_run(rid) == before
