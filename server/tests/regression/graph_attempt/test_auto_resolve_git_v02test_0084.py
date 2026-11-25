"""Real Git/SQLite reproduction of F82-5-shaped comment conflicts and candidate validation."""
from __future__ import annotations

import sqlite3
from unittest.mock import Mock

import pytest

from tests.regression.tr_commit.test_tr_conflict_session_0332 import (
    _GROUP, _commit, _git, git_active, real_store, repo)
from tests.regression.graph_attempt.test_card_merge_realgit_v02test_0042 import (
    merge_on_repo, _card_and_latest, _row)
from flowgate.card_merge import auto_resolve as auto
from flowgate.graph_run import result_merge as rm
from modules.flow_gate.db import group_ai_leases, ai_invoke_runs, git_integration as sessions
from modules.flow_gate.services import ai_invoke_service as ai
from modules.flow_gate.services.git import conflict


@pytest.fixture
def harness(monkeypatch, real_store, merge_on_repo):
    real_store.table_exists = lambda name: real_store._fetch_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", [name]) is not None
    monkeypatch.setattr(auto, "get_store", lambda: real_store)
    monkeypatch.setattr(auto, "ensure_loop", lambda: None)
    monkeypatch.setattr(auto.run_store, "run_by_id", lambda rid: {"status": "running"})
    monkeypatch.setattr(group_ai_leases, "get_active", lambda gid: None)
    monkeypatch.setattr(ai, "get_run_record", lambda rid: {"status": "finished", "outcome": "done"})
    monkeypatch.setattr(ai_invoke_runs, "get", lambda rid: None)
    monkeypatch.setattr(auto, "notify", Mock())
    monkeypatch.setattr(auto, "providers", lambda row: ({"project_id":"flowgate"}, "user", {"id":"ai", "name":"AI"}))
    rows = {}
    monkeypatch.setattr(rm.workspace, "attempt", lambda aid: rows[aid])
    def start(files):
        result = _card_and_latest(merge_on_repo, files)
        row = _row(result)
        row["input_commit"] = _git(["merge-base", "HEAD", result], merge_on_repo).strip()
        row["status"] = "active"
        rows["att"] = row
        status, mid = rm._merge(row, True, automatic=True)
        assert status == rm.CONFLICT
        auto.start(row, mid)
        return mid, row
    return merge_on_repo, start, real_store


def resolver(monkeypatch, mode):
    def launch(row, group, user, provider):
        mid, no = row["merge_id"], row["data"]["attempt"]
        token = auto._dispatch.set((mid, no))
        try:
            auto.bind_run({"run_id": "resolver-" + str(no)})
        finally:
            auto._dispatch.reset(token)
        if mode is None:
            return
        conflict.resolve_conflicts(_GROUP, mid, [{"path": "sample.py", "content": mode}], True,
                                   resolver_run_id="resolver-" + str(no))
    monkeypatch.setattr(auto, "launch", launch)


def test_f82_comment_shape_resolves_and_commits_without_person(harness, monkeypatch):
    root, start, store = harness
    mid, row = start({"sample.py": ("# card provider preference\nvalue = 1\n", "# latest provider preference\nvalue = 1\n")})
    # A deterministic provider response; resolution validation and commit run unmocked.
    resolver(monkeypatch, "# card provider preference\n# latest provider preference\nvalue = 1\n")
    auto.tick(mid)
    assert auto.working("run") and sessions.get_session(mid)["status"] == "open"
    auto.tick(mid)
    out = auto.wire(auto.get(mid))
    assert out["state"] == "succeeded" and out["attempt"] == 1
    assert sessions.get_session(mid)["status"] == "done"
    assert row["result_commit"] in _git(["log", "-1", "--format=%P"], root).split()
    assert not auto.working("run")
    assert "<<<<<<<" not in (root / "sample.py").read_text()


def test_second_reapply_reads_advanced_flow_head(harness, monkeypatch):
    root, start, store = harness
    mid, row = start({"sample.py": ("# card\nvalue = 1\n", "# latest\nvalue = 1\n")})
    resolver(monkeypatch, None)
    auto.tick(mid); auto.tick(mid)
    # A newer flow commit arrives before replay (the previous in-flight merge is aborted).
    _git(["merge", "--abort"], root)
    (root / "new.txt").write_text("new flow head\n")
    latest = _commit(root, "new flow change")
    auto.tick(mid)
    context = sessions.session_context(sessions.get_session(mid))
    assert context["reapplied_head"] == latest
    assert auto.get(mid)["data"]["attempt"] == 2
    assert auto.get(mid)["data"]["attempts"][1]["mode"] == "rebase_ai"
    assert _git(["rev-parse", "HEAD"], root).strip() == latest
    assert (root / "new.txt").read_text() == "new flow head\n"


def test_broken_resolved_python_does_not_commit(harness, monkeypatch):
    root, start, store = harness
    mid, row = start({"sample.py": ("# card\nvalue = 1\n", "# latest\nvalue = 1\n")})
    before = _git(["rev-parse", "HEAD"], root)
    resolver(monkeypatch, "# card\n# latest\nvalue = 1\ndef broken(:\n")
    auto.tick(mid); auto.tick(mid)
    assert auto.get(mid)["data"]["attempts"][0]["result"] == "check_failed"
    assert sessions.get_session(mid)["status"] == "open"
    assert _git(["rev-parse", "HEAD"], root) == before


def test_reopen_database_preserves_second_attempt_and_prevents_dispatch(harness, monkeypatch, tmp_path):
    root, start, store = harness
    mid, row = start({"sample.py": ("# card\nvalue = 1\n", "# latest\nvalue = 1\n")})
    resolver(monkeypatch, None)
    auto.tick(mid); auto.tick(mid); auto.tick(mid)
    before = auto.get(mid)["payload"]
    db = tmp_path / "restart.db"
    copy = sqlite3.connect(db)
    store._conn.backup(copy)
    copy.close()
    from tests.regression.tr_commit.test_tr_conflict_session_0332 import _SqliteStore
    restarted = _SqliteStore(str(db))
    restarted.table_exists = store.table_exists
    monkeypatch.setattr(auto, "get_store", lambda: restarted)
    monkeypatch.setattr(group_ai_leases, "get_active", lambda gid: {"run_id": "resolver-2"})
    started = Mock()
    monkeypatch.setattr(auto, "launch", started)
    try:
        auto.tick(mid)
        assert auto.get(mid)["payload"] == before
        started.assert_not_called()
    finally:
        restarted._conn.close()


def test_clean_second_reapply_commits_without_ai(harness, monkeypatch):
    root, start, store = harness
    mid, row = start({"sample.py": ("# card\nvalue = 1\n", "# latest\nvalue = 1\n")})
    resolver(monkeypatch, None)
    auto.tick(mid); auto.tick(mid)
    _git(["merge", "--abort"], root)
    (root / "sample.py").write_text("# card\nvalue = 1\n")
    _commit(root, "latest converged with card")
    started = Mock(side_effect=AssertionError("clean replay must not call AI"))
    monkeypatch.setattr(auto, "launch", started)
    auto.tick(mid)
    assert auto.get(mid)["state"] == "succeeded"
    assert auto.get(mid)["data"]["attempts"][1]["mode"] == "rebase"
    assert row["result_commit"] in _git(["log", "-1", "--format=%P"], root).split()
    started.assert_not_called()


def test_replay_already_integrated_result_closes_without_empty_commit(harness, monkeypatch):
    root, start, store = harness
    mid, row = start({"sample.py": ("# card\nvalue = 1\n", "# latest\nvalue = 1\n")})
    resolver(monkeypatch, None)
    auto.tick(mid); auto.tick(mid)
    _git(["merge", "--abort"], root)
    _git(["merge", "-s", "ours", "--no-ff", "-m", "result already incorporated", row["result_commit"]], root)
    before = _git(["rev-parse", "HEAD"], root)
    auto.tick(mid)
    assert auto.get(mid)["state"] == "succeeded"
    assert sessions.get_session(mid)["status"] == "done"
    assert _git(["rev-parse", "HEAD"], root) == before
