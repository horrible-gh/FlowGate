"""0573 T0008: terminal convergence, ownership, and secondary failure injection."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from modules.flow_gate.db import group_ai_leases as leases
from modules.flow_gate.db import project_ai_leases as project_leases
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services import process_runner
from modules.flow_gate.services.ai_invoke import chain, finalize, terminal, worker


def fail(*args, **kwargs):
    raise RuntimeError("injected failure")


@pytest.fixture
def env(monkeypatch):
    run = {
        "run_id": "aiv_terminal_0573", "group_id": "flowgate.default.0573",
        "project_id": "flowgate", "doc_ref": "flowgate.default.0573.0001-B",
        "action_scope": "new", "mode": "single", "status": "running",
        "cancel_event": threading.Event(), "started_mono": time.monotonic(),
        "started_at": "2026-09-17T00:00:00+00:00", "docs_target": 1,
        "docs_reached": 1, "reached_doc_ids": ["result"], "chain_id": "chain_0573",
        "chain_docs_target": 1, "chain_docs_reached": 0, "attempt_no": 1,
        "exit_code": 0, "outcome": "complete", "end_reason": "exited",
        "scratch_dir": "unused", "last_message": None, "last_message_received": False,
        "source_dirty": False, "source_dirty_files": [], "scratch_retained": None,
        "fallback_history": [], "raw_token": "test-credential",
    }
    rows, events, parks = [], [], []
    monkeypatch.setattr(svc, "_runs", {run["run_id"]: run})
    monkeypatch.setattr(svc, "_auto_resume", {})
    monkeypatch.setattr(svc, "_persist_run_record", lambda r: rows.append(dict(r)))
    monkeypatch.setattr(svc, "finished_payload", lambda r: {
        "run_id": r["run_id"], "status": r["status"], "stop_code": r.get("stop_code")})
    monkeypatch.setattr(svc, "_broadcast", lambda r, event, payload: events.append((event, payload)))
    monkeypatch.setattr(svc, "_write_handoff_row", lambda *a, **kw: parks.append((a, kw)))
    monkeypatch.setattr(svc, "_clear_handoff_row", lambda *a: None)
    monkeypatch.setattr(svc, "_git_status_paths", lambda *a: set())
    monkeypatch.setattr(finalize, "_mark_scratch_completed", lambda *a: False)
    monkeypatch.setattr(finalize, "_safe_scratch_log", lambda *a: None)
    monkeypatch.setattr(finalize, "_apply_stop_row", lambda *a: None)
    monkeypatch.setattr(finalize, "_notify_chain_failure_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(worker, "_retry_eligible", lambda r: False)
    monkeypatch.setattr(worker.oracle_module, "_provider_brief", lambda p: dict(p))
    monkeypatch.setattr(svc, "_execute_provider_chain", lambda *a: True)
    monkeypatch.setattr(svc, "_classify_end_reason", lambda r, ok: None)
    monkeypatch.setattr(svc, "_judge_hop", lambda r: None)
    monkeypatch.setattr(project_leases, "_memory_mode", lambda: True)
    project_leases._memory.clear()
    leases._memory.clear()
    monkeypatch.setattr(leases, "_sync_test_scope", lambda: None)
    leases.acquire(group_id=run["group_id"], project_id="flowgate", run_id=run["run_id"],
                   chain_id=run["chain_id"], action_scope="new", worker_identity="test")
    leases.activate(run["group_id"], run["run_id"], None, "new", "test", 3600)
    yield run, rows, events, parks
    project_leases._memory.clear()


def execute(run):
    worker._worker(run, [{"id": "test", "name": "Test"}], "test prompt")


def assert_terminal(run):
    assert run["status"] == "finished"
    assert not svc.is_run_live(run["run_id"])
    assert leases.get(run["group_id"]) is None
    assert not run["lease_cleanup_pending"]


def test_normal_completion_persists_before_broadcast_and_releases(env, monkeypatch):
    run, rows, events, _ = env
    def broadcast(r, event, payload):
        if event == "ai_invoke_finished":
            assert rows
            assert leases.get(r["group_id"]) is None
        events.append((event, payload))
    monkeypatch.setattr(svc, "_broadcast", broadcast)
    execute(run)
    assert_terminal(run)
    assert run["chain_docs_reached"] == 1
    assert "raw_token" not in run
    assert len([e for e, _ in events if e == "ai_invoke_finished"]) == 1


def test_worker_and_cleanup_judge_both_fail(env, monkeypatch):
    run, rows, _, _ = env
    monkeypatch.setattr(svc, "_execute_provider_chain", fail)
    monkeypatch.setattr(svc, "_judge_hop", fail)
    execute(run)
    assert_terminal(run)
    assert rows[-1]["end_reason"] == "worker_error"


def test_finalize_itself_never_enters(env, monkeypatch):
    run, rows, _, _ = env
    monkeypatch.setattr(svc, "_finalize_run", fail)
    execute(run)
    assert_terminal(run)
    assert rows


@pytest.mark.parametrize("step", ["_finalize_scratch", "_finalize_source",
                                  "_finalize_diagnostics", "_apply_stop_row",
                                  "_finalize_review_checkpoint", "_notify_chain_failure_if_needed"])
def test_failure_before_release_does_not_skip_terminal_cleanup(env, monkeypatch, step):
    run, rows, events, _ = env
    monkeypatch.setattr(finalize, step, fail)
    execute(run)
    assert_terminal(run)
    assert rows
    assert run["terminal_cleanup_errors"]


@pytest.mark.parametrize("dependency", ["_persist_run_record", "_broadcast"])
def test_durable_or_event_failure_does_not_skip_release(env, monkeypatch, dependency):
    run, _, _, _ = env
    monkeypatch.setattr(svc, dependency, fail)
    execute(run)
    assert_terminal(run)
    assert run["terminal_cleanup_errors"]


def test_release_failure_is_visible_and_retry_preserves_other_owner(env, monkeypatch):
    run, _, _, _ = env
    real = leases.release
    monkeypatch.setattr(leases, "release", fail)
    assert terminal.cleanup(run) is False
    assert run["lease_cleanup_pending"]
    assert leases.get(run["group_id"])["run_id"] == run["run_id"]
    assert not svc.is_run_live(run["run_id"])
    monkeypatch.setattr(leases, "release", real)
    assert terminal.cleanup(run)
    leases.acquire(group_id=run["group_id"], project_id="flowgate", run_id="successor",
                   chain_id="other-chain", action_scope="new", worker_identity="test")
    assert terminal.cleanup(run)
    assert leases.get(run["group_id"])["run_id"] == "successor"


def test_identical_cleanup_is_idempotent(env):
    run, rows, events, _ = env
    assert terminal.cleanup(run)
    stamp = run["finished_at"]
    assert terminal.cleanup(run)
    assert run["finished_at"] == stamp
    assert len(rows) == 1
    assert len(events) == 2


def test_project_lease_uses_project_and_run_owner(env):
    run, _, _, _ = env
    run["action_scope"] = "resolve_base_dirty"
    project_leases.acquire("flowgate", "admission")
    project_leases.activate("flowgate", "admission", run["run_id"])
    assert terminal.cleanup(run)
    assert project_leases.get_active("flowgate") is None
    # A synthetic group label must not cause group-lease cleanup in project scope.
    assert leases.get(run["group_id"])["run_id"] == run["run_id"]
    project_leases.acquire("flowgate", "other-owner")
    assert terminal.cleanup(run)
    assert project_leases.get_active("flowgate")["run_id"] == "other-owner"


def test_handoff_transfers_generation_and_late_cleanup_preserves_successor(env, monkeypatch):
    run, _, _, _ = env
    run["mode"] = "continuous"
    pending = {"doc_ref": run["doc_ref"], "target_seq": 2}
    svc._auto_resume[run["group_id"]] = pending
    def spawn(group, bundle, predecessor):
        row = leases.get(group)
        assert row["state"] == "releasing"
        adopted = leases.acquire(group_id=group, project_id="flowgate", run_id="successor",
            chain_id=run["chain_id"], action_scope="new", worker_identity="test")
        assert adopted["run_id"] == "successor" and adopted["generation"] == 2
        return True
    monkeypatch.setattr(chain.review, "run_review_gate", spawn)
    execute(run)
    assert run["_handoff_succeeded"]
    assert leases.get(run["group_id"])["run_id"] == "successor"
    terminal.cleanup(run, handoff=True)
    assert leases.get(run["group_id"])["run_id"] == "successor"


@pytest.mark.parametrize("park_failure", [False, True])
def test_successor_failure_and_park_secondary_failure_release_lease(env, monkeypatch, park_failure):
    run, _, _, parks = env
    run["mode"] = "continuous"
    svc._auto_resume[run["group_id"]] = {"doc_ref": run["doc_ref"], "target_seq": 2}
    monkeypatch.setattr(chain.review, "run_review_gate", fail)
    if park_failure:
        monkeypatch.setattr(svc, "_write_handoff_row", fail)
    execute(run)
    assert_terminal(run)
    assert svc.peek_auto_resume(run["group_id"]) is None
    assert run["stop_code"] == "hop_handoff_failed"
    if park_failure:
        assert run["terminal_cleanup_errors"]
    else:
        assert any(kw.get("stop_code") == "hop_handoff_failed" for _, kw in parks)


@pytest.mark.parametrize("cancelled", [False, True])
def test_no_exit_code_stalled_judge_is_recovered(env, monkeypatch, cancelled):
    run, _, _, _ = env
    run["exit_code"] = None
    entered, unblock = threading.Event(), threading.Event()
    monkeypatch.setattr(worker, "POST_PROCESS_RECOVERY_SEC", 0.03)
    def judge(r):
        entered.set()
        assert unblock.wait(2)
    monkeypatch.setattr(svc, "_judge_hop", judge)
    if cancelled:
        run["cancel_event"].set()
    thread = threading.Thread(target=execute, args=(run,))
    thread.start()
    try:
        assert entered.wait(1)
        deadline = time.monotonic() + 1
        while run["status"] != "finished" and time.monotonic() < deadline:
            time.sleep(0.005)
        assert_terminal(run)
        assert run["stop_code"] == ("cancelled" if cancelled else "post_process_timeout")
    finally:
        unblock.set()
        thread.join(2)
    assert not thread.is_alive()


def test_live_api_provider_is_not_terminalized(env):
    run, _, _, _ = env
    run["_provider_active"] = True
    assert terminal.cleanup(run) is False
    assert run["status"] == "running"
    assert leases.get(run["group_id"]) is not None


@pytest.mark.skipif(os.name != "nt", reason="real Windows Job Object")
def test_cancel_real_process_releases_only_after_provider_return(env, monkeypatch):
    run, _, _, _ = env
    ready, return_provider = threading.Event(), threading.Event()
    owner = process_runner.WindowsProcessOwner(run["run_id"])
    assert owner.active
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
        creationflags=owner.creationflags(subprocess.CREATE_NEW_PROCESS_GROUP))
    assert owner.attach(proc)
    run["proc"] = proc
    def provider(r, providers, prompt):
        ready.set()
        r["exit_code"] = proc.wait(timeout=5)
        assert return_provider.wait(2)
        return True
    monkeypatch.setattr(svc, "_execute_provider_chain", provider)
    thread = threading.Thread(target=execute, args=(run,))
    thread.start()
    try:
        assert ready.wait(1)
        response = chain.cancel_run(run["run_id"])
        assert response["status"] == "cancelling"
        proc.wait(timeout=5)
        # Process death alone is not worker completion. The provider still owns the run.
        assert leases.get(run["group_id"]) is not None
        assert run["status"] == "cancelling"
        return_provider.set()
        thread.join(2)
        assert not thread.is_alive()
        assert_terminal(run)
    finally:
        return_provider.set()
        process_runner.kill_process_tree(proc)
        owner.close()
        proc.wait(timeout=5)
        thread.join(2)

def test_recovery_prevents_late_successor_gate_open(env):
    from modules.flow_gate.services.ai_invoke.runtime import HandoffGate
    run, _, _, _ = env
    gate = HandoffGate()
    assert terminal.claim_abandon(run)
    context = terminal.handoff_parent.set(run)
    try:
        assert not terminal.open_successor_gate(gate)
        assert gate.abort()
    finally:
        terminal.handoff_parent.reset(context)
    assert not run.get("_handoff_succeeded")


def test_successor_gate_open_prevents_false_timeout_and_park(env):
    from modules.flow_gate.services.ai_invoke.runtime import HandoffGate
    run, _, _, parks = env
    gate = HandoffGate()
    context = terminal.handoff_parent.set(run)
    try:
        assert terminal.open_successor_gate(gate)
    finally:
        terminal.handoff_parent.reset(context)
    assert not terminal.claim_abandon(run)
    terminal.abandon_handoff(run, stop_code="post_process_timeout")
    assert not parks
    assert not run.get("_terminal_abandoned")


def test_gate_open_and_recovery_have_exactly_one_winner(env):
    from modules.flow_gate.services.ai_invoke.runtime import HandoffGate
    for _ in range(20):
        parent = {"cancel_event": threading.Event()}
        gate = HandoffGate()
        barrier = threading.Barrier(2)
        results = []
        def open_gate():
            context = terminal.handoff_parent.set(parent)
            try:
                barrier.wait(timeout=1)
                results.append(terminal.open_successor_gate(gate))
            finally:
                terminal.handoff_parent.reset(context)
        thread = threading.Thread(target=open_gate)
        thread.start()
        barrier.wait(timeout=1)
        results.append(terminal.claim_abandon(parent))
        thread.join(1)
        assert not thread.is_alive()
        assert sorted(results) == [False, True]


def test_real_record_store_failure_remains_retryable(env, monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs
    run, _, _, _ = env
    monkeypatch.setattr(svc, "_persist_run_record", finalize._persist_run_record)
    monkeypatch.setattr(ai_invoke_runs, "upsert", fail)
    terminal.cleanup(run)
    assert any(key.startswith("persist:") for key in run["terminal_cleanup_errors"])
    assert_terminal(run)
    saved = []
    monkeypatch.setattr(ai_invoke_runs, "upsert", lambda row: saved.append(row))
    monkeypatch.setattr(ai_invoke_runs, "maybe_purge", lambda: None)
    monkeypatch.setattr(finalize, "_persist_register_context_failures", lambda *a: None)
    terminal.cleanup(run)
    assert len(saved) == 1
    assert not any(key.startswith("persist:") for key in run["terminal_cleanup_errors"])

def test_late_judge_cannot_overwrite_recovery_verdict(env, monkeypatch):
    run, rows, _, _ = env
    entered, unblock = threading.Event(), threading.Event()
    monkeypatch.setattr(worker, "POST_PROCESS_RECOVERY_SEC", 0.03)
    def late_judge(result):
        entered.set()
        assert unblock.wait(2)
        result.update(outcome="complete", docs_reached=99)
    monkeypatch.setattr(svc, "_judge_hop", late_judge)
    thread = threading.Thread(target=execute, args=(run,))
    thread.start()
    try:
        assert entered.wait(1)
        deadline = time.monotonic() + 1
        while not run.get("post_process_recovered") and time.monotonic() < deadline:
            time.sleep(0.005)
        assert run.get("post_process_recovered")
    finally:
        unblock.set()
        thread.join(2)
    assert not thread.is_alive()
    assert_terminal(run)
    assert run["outcome"] == "none"
    assert run["docs_reached"] != 99
    assert rows[-1]["outcome"] == "none"

def test_group_lease_sql_handoff_and_release_in_memory_sqlite(env, monkeypatch):
    import sqlite3
    from pathlib import Path
    run, _, _, _ = env
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    class Store:
        def transaction(self):
            return conn
        def _execute(self, sql, params):
            return conn.execute(sql, params)
        def _fetch_one(self, sql, params):
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None
    try:
        schema = Path(__file__).resolve().parents[1] / "sql/migrations/sqlite/077_group_ai_leases.sql"
        conn.executescript(schema.read_text(encoding="utf-8"))
        monkeypatch.setattr(leases, "_using_memory", lambda: False)
        monkeypatch.setattr(leases, "get_store", lambda: Store())
        monkeypatch.setattr(leases, "_append_event", lambda **kw: None)
        leases.acquire(group_id=run["group_id"], project_id="flowgate", run_id=run["run_id"],
                       chain_id=run["chain_id"], action_scope="new", worker_identity="test")
        leases.activate(run["group_id"], run["run_id"], None, "new", "test", 3600)
        assert terminal.cleanup(run, handoff=True)
        assert leases.get(run["group_id"])["state"] == "releasing"
        adopted = leases.acquire(group_id=run["group_id"], project_id="flowgate", run_id="sql-successor",
                                 chain_id=run["chain_id"], action_scope="new", worker_identity="test")
        assert adopted["generation"] == 2
        assert terminal.cleanup(run, handoff=False)
        assert leases.get(run["group_id"])["run_id"] == "sql-successor"
        successor = dict(run, run_id="sql-successor")
        for key in ("_terminal_done", "_terminal_busy", "_terminal_lock"):
            successor.pop(key, None)
        assert terminal.cleanup(successor)
        assert leases.get(run["group_id"]) is None
    finally:
        conn.close()
