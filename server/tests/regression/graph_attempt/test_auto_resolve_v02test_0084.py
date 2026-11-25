"""Bounded card merge recovery: durable SQLite, real Git candidate checks, provider boundary doubles."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from flowgate.card_merge import auto_resolve as auto
from flowgate.card_merge import service
from flowgate.graph_run import result_merge as rm
from modules.flow_gate.db import group_ai_leases, ai_invoke_runs
from modules.flow_gate.services import ai_invoke_service as ai
from modules.flow_gate.services.git.credentials import GitServiceError


REAL_LAUNCH = auto.launch
REAL_UNRESOLVED = auto.unresolved


class Store:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        sql = Path(__file__).resolve().parents[3] / "sql/migrations/sqlite/212_graph_merge_auto_resolve.sql"
        self.conn.executescript(sql.read_text())

    def table_exists(self, name):
        return True

    def _execute(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.rowcount

    _execute_affected = _execute

    def _fetch_one(self, sql, params=()):
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _fetch_all(self, sql, params=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]


@pytest.fixture
def env(monkeypatch):
    store = Store()
    monkeypatch.setattr(auto, "get_store", lambda: store)
    monkeypatch.setattr(auto, "ensure_loop", lambda: None)
    monkeypatch.setattr(rm, "_project_of", lambda flow: "p")
    monkeypatch.setattr(rm.workspace, "attempt", lambda aid: {})
    monkeypatch.setattr(rm.git_commit, "_acquire_group_lock", lambda *a, **k: object())
    monkeypatch.setattr(rm.git_commit, "_release_group_lock", lambda lock: None)
    run = {"status": "running", "started_by": "user"}
    monkeypatch.setattr(auto.run_store, "run_by_id", lambda run_id: run)
    session = {"status": "open", "merge_id": 7, "context": "{}"}
    remaining = ["a.py"]
    monkeypatch.setattr(auto.sessions, "get_session", lambda mid: session)
    monkeypatch.setattr(auto.sessions, "remaining_conflicts", lambda mid: list(remaining))
    monkeypatch.setattr(auto.sessions, "session_files", lambda mid: [{"path": p} for p in remaining])
    monkeypatch.setattr(auto.sessions, "replace_session_files", lambda mid, paths: None)
    monkeypatch.setattr(auto.sessions, "set_session_context", lambda mid, data: None)
    lease = {}
    monkeypatch.setattr(group_ai_leases, "get_active", lambda flow: lease or None)
    records = {}
    monkeypatch.setattr(ai, "get_run_record", lambda rid: records.get(rid))
    monkeypatch.setattr(ai_invoke_runs, "get", lambda rid: records.get(rid))
    notifications = Mock()
    monkeypatch.setattr(auto, "notify", notifications)
    monkeypatch.setattr(auto, "unresolved", lambda row: [{"path": "a.py", "hunks": [{"start_line": 1, "end_line": 5}]}])
    cancel = Mock()
    monkeypatch.setattr(auto, "cancel", cancel)
    provider = lambda row: ({"project_id": "p"}, "user", {"id": "other" if row["data"]["attempt"] == 3 else "first", "name": "AI"})
    monkeypatch.setattr(auto, "providers", provider)
    launches = []
    def launch(row, group, user, provider):
        no = row["data"]["attempt"]
        launches.append((no, provider, json.loads(json.dumps(row["data"]["attempts"]))))
        token = auto._dispatch.set((7, no))
        try:
            auto.bind_run({"run_id": "ai-" + str(no)})
        finally:
            auto._dispatch.reset(token)
        records["ai-" + str(no)] = {"status": "finished", "outcome": "done", "end_reason": "normal_finish"}
    monkeypatch.setattr(auto, "launch", launch)
    replay = Mock(return_value=True)
    monkeypatch.setattr(auto, "reapply", replay)
    commit = Mock(side_effect=lambda row: session.update(status="done"))
    monkeypatch.setattr(auto, "commit_candidate", commit)
    attempt = {"attempt_id": "att", "group_id": "flow", "node_key": "node", "run_id": "graph"}
    auto.start(attempt, 7)
    yield SimpleNamespace(**locals())
    store.conn.close()


def test_success_has_no_idle_gap_and_exposes_contract(env, monkeypatch):
    auto.tick(7)
    assert auto.working("graph")
    assert auto.view("flow", "node", "att")["state"] == "running"
    env.remaining.clear()
    auto.tick(7)
    out = auto.view("flow", "node", "att")
    assert out["state"] == "succeeded" and out["attempt"] == 1
    assert out["attempts"][0]["result"] == "resolved"
    assert env.commit.call_count == 1 and not auto.working("graph")
    assert set(out) == {"state", "attempt", "max_attempts", "attempts", "unresolved", "count"}


def test_three_failures_rebase_switch_and_persistent_reasons(env):
    for _ in range(6):
        auto.tick(7)
    out = auto.wire(auto.get(7))
    assert [n for n, p, h in env.launches] == [1, 2, 3]
    assert env.replay.call_count == 1
    assert env.launches[2][1]["id"] == "other"
    assert env.launches[1][2][0]["result"] == "conflicts_left"
    assert len(env.launches[2][2]) == 3
    assert [a["mode"] for a in out["attempts"]] == ["ai", "rebase_ai", "switch_ai"]
    assert out["state"] == "exhausted" and out["unresolved"][0]["hunks"]
    for _ in range(5):
        auto.tick(7)
    assert len(env.launches) == 3
    env.commit.assert_not_called()


def test_rebase_can_finish_without_second_ai(env):
    auto.tick(7); auto.tick(7)
    env.replay.side_effect = lambda row: (env.remaining.clear() or True)
    auto.tick(7)
    assert auto.get(7)["state"] == "succeeded"
    assert len(env.launches) == 1
    assert auto.get(7)["data"]["attempts"][1]["mode"] == "rebase"


def test_candidate_check_failure_counts_and_does_not_integrate(env):
    auto.tick(7)
    env.remaining.clear()
    env.commit.side_effect = GitServiceError(422, "verification_failed", "a.py: syntax error")
    auto.tick(7)
    row = auto.get(7)
    assert row["state"] == "running"
    assert row["data"]["attempts"][0]["result"] == "check_failed"
    assert "syntax error" in row["data"]["attempts"][0]["reason"]
    assert env.session["status"] == "open"


@pytest.mark.parametrize("status", ["paused", "stopping"])
def test_stop_never_dispatches(env, status):
    env.run["status"] = status
    auto.tick(7)
    assert not env.launches and auto.get(7)["state"] == "skipped_paused"


def test_restart_in_second_attempt_does_not_relaunch(env):
    auto.tick(7); auto.tick(7); auto.tick(7)
    env.lease.update(run_id="ai-2")
    before = auto.get(7)["payload"]
    # A new coordinator has no process-local attempt state: it re-reads the database.
    for _ in range(3):
        auto.tick(7)
    assert auto.get(7)["payload"] == before
    assert [n for n, _, _ in env.launches] == [1, 2]
    env.lease.clear()
    auto.tick(7)
    assert auto.get(7)["data"]["attempts"][1]["result"] == "conflicts_left"


def test_two_coordinators_claim_one_dispatch(env):
    barrier = threading.Barrier(3)
    def work():
        barrier.wait()
        auto.tick(7)
    threads = [threading.Thread(target=work) for _ in range(2)]
    for t in threads: t.start()
    barrier.wait()
    for t in threads: t.join()
    assert len(env.launches) == 1


def test_human_write_stops_and_fences_old_token_but_read_does_not(env):
    auto.tick(7)
    auto.view("flow", "node", "att")
    assert auto.get(7)["state"] == "running"
    auto.before_resolve(7, "ai-1")
    assert auto.get(7)["state"] == "running"
    auto.before_resolve(7, None)
    assert auto.get(7)["state"] == "stopped_by_person"
    with pytest.raises(GitServiceError):
        auto.before_resolve(7, "ai-1")
    auto.tick(7)
    assert len(env.launches) == 1
    env.cancel.assert_called_once()


def test_manual_ai_admission_stops_auto(env):
    auto.tick(7)
    auto.admission_requested(7)
    assert auto.get(7)["state"] == "stopped_by_person"


def test_no_other_provider_is_third_failure(env, monkeypatch):
    for _ in range(4): auto.tick(7)
    monkeypatch.setattr(auto, "providers", lambda row: ({}, "user", None))
    auto.tick(7)
    assert auto.get(7)["state"] == "exhausted"
    assert auto.get(7)["data"]["attempts"][2]["result"] == "no_other_ai"
    assert len(env.launches) == 2


def test_timeout_waits_for_worker_lease_to_end(env):
    auto.tick(7)
    row = auto.get(7)
    row["data"]["attempts"][0]["started_at"] = "2000-01-01T00:00:00+00:00"
    auto.save(row)
    env.lease.update(run_id="ai-1")
    auto.tick(7)
    env.cancel.assert_called_once()
    assert len(env.launches) == 1
    env.lease.clear()
    auto.tick(7)
    assert auto.get(7)["data"]["attempts"][0]["result"] == "timeout"


def test_interrupted_admission_is_counted_not_replayed(env, monkeypatch):
    monkeypatch.setattr(auto, "launch", lambda *args: None)
    auto.tick(7)
    auto.tick(7)
    assert auto.get(7)["data"]["attempts"][0]["result"] == "ai_error"
    assert auto.get(7)["data"]["phase"] == "next"


def test_human_wins_stale_coordinator_save(env):
    stale = auto.get(7)
    auto.stop_by_person(7)
    stale["data"]["state"] = "running"
    with pytest.raises(RuntimeError): auto.save(stale)
    assert auto.get(7)["state"] == "stopped_by_person"


def test_card_view_uses_catching_up(env, monkeypatch):
    row = {**env.attempt, "integration_status": "conflict", "integration_reason": "merge_session:7"}
    monkeypatch.setattr(service, "_attempt", lambda *a: (env.run, row))
    monkeypatch.setattr(service, "_default_provider", lambda *a: None)
    monkeypatch.setattr(service, "_session_kinds", lambda *a: {})
    monkeypatch.setattr(service, "_resolving", lambda *a: None)
    monkeypatch.setattr(service, "_waiting_cards", lambda *a: [])
    out = service.view("flow", "node")
    assert out["state"] == "catching_up" and out["auto_resolve"]["state"] == "running"
    assert "pick" in out["actions"] and "ai_resolve" in out["actions"]


def test_real_launch_adapter_pins_provider_budget_and_prior_failures(env, monkeypatch):
    # Exercise the actual launch adapter rather than the provider boundary double.
    import importlib
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    auto.tick(7); auto.tick(7); auto.tick(7); auto.tick(7)
    row = auto.get(7)
    row["data"]["attempt"] = 3
    row["data"]["phase"] = "launching"
    row["data"]["attempts"].append({"no": 3, "mode": "switch_ai", "result": "running", "reason": None})
    auto.save(row)
    captured = {}
    monkeypatch.setattr(ai_node_starter, "api_base_url", lambda user: "https://example.test/flowgate/api/v1")
    monkeypatch.setattr(git_routes, "_resolve_conflict_mention_builder", lambda **kw: captured.update(kw) or (lambda *a: "prompt"))
    calls = []
    monkeypatch.setattr(ai, "start_run", lambda **kw: calls.append(kw))
    REAL_LAUNCH(row, {"project_id": "p", "module": "m"}, "user", {"id": "other", "name": "Other"})
    assert calls[0]["action_scope"] == "resolve_conflict"
    assert calls[0]["provider_id"] == "other" and calls[0]["provider_pinned"] is True
    assert calls[0]["continuation_step_timeout_sec"] == 600
    assert "Attempt 1" in captured["messages"][1] and "Attempt 2" in captured["messages"][2]
    assert "remaining_conflicts" in captured["messages"][1]


def test_expired_coordinator_claim_can_recover_but_not_duplicate_live_ai(env):
    auto.tick(7)
    env.lease.update(run_id="ai-1")
    env.store._execute("UPDATE graph_merge_auto_resolve SET owner='dead-server', claim_until=1 WHERE merge_id=7")
    auto.tick(7)
    assert len(env.launches) == 1
    assert auto.get(7)["owner"] is None


def test_startup_recovers_session_before_history_insert(env, monkeypatch):
    env.store._execute("DELETE FROM graph_merge_auto_resolve")
    session = {"merge_id": 7, "kind": auto.sessions.SESSION_KIND_GRAPH_ATTEMPT_MERGE,
               "context": json.dumps({"auto_resolve_requested": True, "attempt_id": "att"})}
    monkeypatch.setattr(auto.sessions, "list_open_sessions", lambda: [session])
    monkeypatch.setattr(rm.workspace, "attempt", lambda aid: {**env.attempt, "status": "active"})
    auto.recover()
    assert auto.get(7)["data"]["phase"] == "next"
    auto.tick(7)
    assert len(env.launches) == 1


def test_git_busy_replay_is_also_bounded(env):
    auto.tick(7); auto.tick(7)
    env.replay.return_value = False
    auto.tick(7)
    row = auto.get(7)
    row["data"]["attempts"][-1]["started_at"] = "2000-01-01T00:00:00+00:00"
    auto.save(row)
    auto.tick(7)
    assert auto.get(7)["data"]["attempts"][1]["result"] == "timeout"
    assert len(env.launches) == 1


def test_lost_coordinator_cannot_write_or_launch(env):
    assert auto.claim(7, "old")
    stale = auto.get(7)
    env.store._execute("UPDATE graph_merge_auto_resolve SET claim_until=1 WHERE merge_id=7")
    assert auto.claim(7, "new")
    token = auto._claim_owner.set("old")
    try:
        with pytest.raises(GitServiceError): auto.assert_running(7)
        with pytest.raises(RuntimeError): auto.save(stale)
    finally:
        auto._claim_owner.reset(token)


def test_notification_failure_preserves_recorded_conflicts_result(env):
    env.notifications.side_effect = RuntimeError("transport down")
    auto.tick(7); auto.tick(7)
    assert auto.get(7)["data"]["attempts"][0]["result"] == "conflicts_left"


@pytest.mark.parametrize("record", [
    {"outcome": "done", "exit_code": 7, "end_reason": "normal_finish"},
    {"outcome": "done", "stop_code": "provider_failed"},
    {"outcome": "error", "error_message": "provider unavailable"},
])
def test_ai_error_is_not_conflicts_left(env, record):
    auto.tick(7)
    env.records["ai-1"] = {"status": "finished", **record}
    auto.tick(7)
    item = auto.wire(auto.get(7))["attempts"][0]
    assert item["result"] == "ai_error"
    assert "AI 실행 실패" in item["reason_summary"]
    assert auto.get(7)["data"]["phase"] == "next"
    if record.get("exit_code"):
        assert "7" in item["reason_summary"]


def test_check_summary_keeps_raw_diagnostic(env):
    auto.tick(7)
    env.remaining.clear()
    raw = json.dumps({"status": "failed", "failures": [
        {"checker": "python_compile", "path": "a.py", "line": 12, "message": "invalid syntax"}]})
    env.commit.side_effect = GitServiceError(422, "verification_failed", raw)
    auto.tick(7)
    item = auto.wire(auto.get(7))["attempts"][0]
    assert item["result"] == "check_failed" and item["reason"] == raw
    assert item["reason_summary"] == "python_compile · a.py:12: invalid syntax"


def test_paused_count_and_resume_notification(env):
    env.run["status"] = "paused"
    auto.tick(7)
    out = auto.wire(auto.get(7))
    assert out["state"] == "skipped_paused" and out["count"] == 1
    env.run["status"] = "idle"
    auto.tick(7)
    assert auto.get(7)["state"] == "running"
    assert len(env.launches) == 1
    env.notifications.assert_called_once()


def test_idle_merge_conflict_with_auto_work_is_running(env, monkeypatch):
    from flowgate.graph_run import settle, execution_store
    ctx = SimpleNamespace(run_id="graph", run={"status": "idle", "idle_reason": "merge_conflict"})
    monkeypatch.setattr(execution_store, "working", lambda rid: False)
    assert settle.working(ctx, [])
    ctx.run["idle_reason"] = "stopped"
    assert not settle.working(ctx, [])
    ctx.run.update(status="paused", idle_reason="merge_conflict")
    monkeypatch.setattr(execution_store, "handing_off", lambda rid: False)
    monkeypatch.setattr(rm, "in_progress", lambda rid: False)
    from flowgate.gate_decisions import run_hold
    monkeypatch.setattr(run_hold, "gate_live", lambda rid: False)
    assert not settle.working(ctx, [])


def test_exhausted_hunks_use_session_root(env, monkeypatch, tmp_path):
    from modules.flow_gate.services.git import conflict
    actual_root = tmp_path / "session"
    actual_root.mkdir()
    (actual_root / "a.py").write_text("common\n<<<<<<< ours\na\n||||||| base\nb\n=======\nc\n>>>>>>> theirs\n", encoding="utf-8")
    monkeypatch.setattr(conflict, "_session_context", lambda *a: ({}, {}, "p", actual_root))
    # The old group-root lookup must never be used for conflict file coordinates.
    monkeypatch.setattr(rm, "_group_root", lambda *a: (_ for _ in ()).throw(AssertionError("wrong root")))
    monkeypatch.setattr(auto, "unresolved", REAL_UNRESOLVED)
    for _ in range(6):
        auto.tick(7)
    out = auto.wire(auto.get(7))
    assert out["state"] == "exhausted"
    assert out["unresolved"] == [{"path": "a.py", "hunks": [{"start_line": 2, "end_line": 8}]}]


def test_settle_plan_wakes_resumed_auto_merge(env, monkeypatch):
    from flowgate.graph_run import settle
    ctx = SimpleNamespace(run_id="graph", flow_id="flow", run={"status": "idle", "idle_reason": "merge_conflict"})
    monkeypatch.setattr(settle.engine, "node_runs", lambda rid: [])
    monkeypatch.setattr(settle.engine, "read_run_state", lambda ctx: object())
    assert settle.settle_plan(ctx) == ("running", None)


def test_conflict_session_ai_error_response(env, monkeypatch):
    from modules.flow_gate.services.git import conflict
    from modules.flow_gate.services import git_service as git
    session = {"status": "open", "context": "{}", "group_id": "flow"}
    monkeypatch.setattr(git.db_git, "get_session", lambda mid: session)
    saved = {}
    monkeypatch.setattr(git.db_git, "set_session_context", lambda mid, data: saved.update(data))
    monkeypatch.setattr(conflict, "_session_project_id", lambda session: None)
    assert conflict.note_resolver_finished(7, {"run_id": "failed", "exit_code": 9, "stop_code": "provider_failed"})
    item = saved["resolver_attempt"]
    assert item["code"] == "ai_error" and item["remaining"] == 1
    assert "exit code 9" in item["reason_summary"]
