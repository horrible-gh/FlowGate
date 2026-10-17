"""0061 T#8 regression specifications. TR compiles; TS/TSR runs behavior.

SQLite uses the real additive migrations. Git tests create two linked attempt
worktrees. Process/Flow/event/config/lock boundaries are controlled by fixtures.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import shutil
import subprocess
import threading

import pytest

from test_graph_attempt_runs_0061 import db, MIGRATIONS  # shared SQLite execution fixture
from flowgate.graph_run import execution_store, fencing, result_commit, workspace
from modules.flow_gate.db import tr_commit_ledger
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services import git_service, tr_commit_service
from modules.flow_gate.services.git import commit, lock_manager

BASE = "a" * 40
RESULT = "b" * 40


@pytest.fixture
def result_db(db, monkeypatch, tmp_path):
    db.db.executescript("""
        CREATE TABLE groups(group_id TEXT PRIMARY KEY);
        CREATE TABLE documents(doc_id TEXT PRIMARY KEY, group_id TEXT, type_code TEXT,
                               seq INTEGER, commit_message TEXT);
        CREATE TABLE group_git_state(group_id TEXT PRIMARY KEY);
        CREATE TABLE workflow_events(id INTEGER PRIMARY KEY, group_id TEXT, event_type TEXT, metadata TEXT);
        CREATE TABLE tokens(token_id TEXT PRIMARY KEY, action_scope TEXT, ai_run_id TEXT);
        INSERT INTO groups VALUES ('flow');
        INSERT INTO documents VALUES ('doc','flow','TR',1,'feat(graph): commit attempt');
        INSERT INTO documents VALUES ('legacy','flow','TR',2,'fix(graph): legacy');
    """)
    db.db.executescript((MIGRATIONS / "sqlite" / "086d_tr_commit_ledger.sql").read_text(encoding="utf-8"))
    db._execute("INSERT INTO tr_commit_ledger(group_id,doc_id,state,skip_reason,created_at,updated_at) "
                "VALUES ('flow','legacy','no_commit','no_changes','now','now')")
    db.db.executescript((MIGRATIONS / "sqlite" / "153a_graph_attempt_results.sql").read_text(encoding="utf-8"))
    db._execute("UPDATE graph_node_attempts SET input_commit=?, input_source='integration_head' "
                "WHERE attempt_id=?", [BASE, db.attempt])
    db._execute("INSERT INTO tokens VALUES ('author','new',NULL)")
    run_store.map_token("author", "run", "node", "result", map_kind=run_store.MAP_AI_START)
    db._execute("INSERT INTO workflow_events VALUES (1,'flow','token_consumed',?)",
                [json.dumps({"token_id": "author", "doc_id": "doc"})])
    monkeypatch.setattr(tr_commit_ledger, "get_store", lambda: db)
    monkeypatch.setattr(result_commit.documents, "get_by_id",
                        lambda did: db._fetch_one("SELECT * FROM documents WHERE doc_id=?", [did]))
    def guard(_flow, body):
        with db.transaction():
            return body()
    monkeypatch.setattr(fencing, "with_flow_guard", guard)
    monkeypatch.setattr(fencing, "in_transaction", lambda: db.db.in_transaction)
    monkeypatch.setattr(fencing, "record", Mock())
    root = tmp_path / "attempt"
    monkeypatch.setattr(workspace, "source_root", lambda *a, **kw: root)
    monkeypatch.setattr(workspace, "_project", lambda *a, **kw: "project")
    monkeypatch.setattr(workspace, "location", lambda *a: (tmp_path, root, "attempt"))
    return db


def outcome(*, changed=True, reason="no_changes"):
    return {"committed": changed, "commit": RESULT[:7] if changed else None,
            "commit_sha": RESULT if changed else None, "subject": "feat(graph): commit attempt",
            "skipped_reason": None if changed else reason, "excluded_artifacts": [],
            "committed_paths": ["source.py"] if changed else []}


def test_migration_preserves_legacy_and_allows_open_keys(result_db):
    old = tr_commit_ledger._latest_for_doc("legacy")
    assert old["attempt_id"] is None and old["node_key"] is None
    result_db._execute("UPDATE graph_node_attempts SET integration_status='future_state', "
                       "input_source='future_source' WHERE attempt_id=?", [result_db.attempt])
    assert workspace.attempt(result_db.attempt)["result_commit"] is None


@pytest.mark.parametrize("state", ["active", "ended", "invalidated"])
def test_graph_approval_defers_without_group_git_or_ledger(result_db, monkeypatch, state):
    result_db._execute("UPDATE graph_node_attempts SET status=? WHERE attempt_id=?",
                       [state, result_db.attempt])
    group_commit = Mock(side_effect=AssertionError("group worktree must not be used"))
    record = Mock(side_effect=AssertionError("deferred approval must not write ledger"))
    monkeypatch.setattr(git_service, "create_tr_commit", group_commit)
    monkeypatch.setattr(tr_commit_service, "_record", record)
    payload = tr_commit_service.on_document_approved("doc")
    assert payload["skipped_reason"] == "graph_attempt_deferred"
    assert payload["quiet"] and not payload["committed"]
    assert payload["commit"] is None and payload["reported_diff"] == {"unreported": [], "missing": []}
    group_commit.assert_not_called()
    record.assert_not_called()


def test_non_graph_approval_retains_existing_commit_and_payload(result_db, monkeypatch):
    group_commit = Mock(return_value=outcome())
    monkeypatch.setattr(git_service, "create_tr_commit", group_commit)
    monkeypatch.setattr(tr_commit_service, "_reported_paths", lambda doc: ["source.py"])
    payload = tr_commit_service.on_document_approved("legacy")
    group_commit.assert_called_once_with("flow", "fix(graph): legacy")
    assert payload["committed"] and payload["commit"] == RESULT[:7] and not payload["quiet"]
    row = tr_commit_ledger._latest_for_doc("legacy")
    assert row["state"] == "live" and row["attempt_id"] is None


def test_review_token_does_not_reattribute_authored_document(result_db):
    other = run_store.open_attempt("run", "another", 1)
    result_db._execute("INSERT INTO tokens VALUES ('reviewer','review','ai')")
    run_store.map_token("reviewer", "run", "another", map_kind=run_store.MAP_ATTEMPT)
    result_db._execute("INSERT INTO workflow_events VALUES (2,'flow','token_consumed',?)",
                       [json.dumps({"token_id": "reviewer", "doc_id": "doc"})])
    binding = run_store.registered_document_attempt("flow", "doc")
    assert binding["attempt_id"] == result_db.attempt and binding["attempt_id"] != other


@pytest.mark.parametrize("changed,reason", [(True, None), (False, "no_changes"), (False, "artifacts_only")])
def test_one_result_and_one_owned_ledger_row(result_db, monkeypatch, changed, reason):
    make = Mock(return_value=outcome(changed=changed, reason=reason))
    monkeypatch.setattr(commit, "create_attempt_commit", make)
    first = result_commit.commit_result(result_db.attempt, "doc")
    second = result_commit.commit_result(result_db.attempt, "doc")
    assert first["result_commit"] == second["result_commit"] == (RESULT if changed else BASE)
    assert second["reused"] and make.call_count == 1
    rows = result_db._fetch_all("SELECT * FROM tr_commit_ledger WHERE attempt_id=?", [result_db.attempt])
    assert len(rows) == 1 and rows[0]["node_key"] == "node"
    assert rows[0]["state"] == ("live" if changed else "no_commit")


def test_document_only_leaves_no_result_or_ledger(result_db, monkeypatch):
    monkeypatch.setattr(workspace, "source_root", lambda *a, **kw: None)
    make = Mock(side_effect=AssertionError("document-only attempt must not call Git"))
    monkeypatch.setattr(commit, "create_attempt_commit", make)
    for _ in range(2):
        assert result_commit.commit_result(result_db.attempt, "doc")["result_commit"] is None
    row = workspace.attempt(result_db.attempt)
    assert row["input_source"] == "document_only" and row["result_commit"] is None
    assert tr_commit_ledger.by_attempt(result_db.attempt) is None
    make.assert_not_called()


@pytest.mark.parametrize("running", [False, True])
def test_requested_or_running_execution_prevents_commit(result_db, monkeypatch, running):
    eid = execution_store.request(result_db.attempt, "run", "write", {})
    if running:
        execution_store.acquire(eid, "ai")
    make = Mock()
    monkeypatch.setattr(commit, "create_attempt_commit", make)
    with pytest.raises(fencing.port.GraphAttemptInactive):
        result_commit.commit_result(result_db.attempt, "doc")
    make.assert_not_called()
    assert workspace.attempt(result_db.attempt)["integration_status"] is None


@pytest.mark.parametrize("status", ["stopping", "invalidated"])
def test_inactive_result_has_no_git_side_effect(result_db, monkeypatch, status):
    if status == "stopping":
        result_db._execute("UPDATE graph_runs SET status='stopping'")
    else:
        result_db._execute("UPDATE graph_node_attempts SET status='invalidated'")
    make = Mock()
    monkeypatch.setattr(commit, "create_attempt_commit", make)
    with pytest.raises(fencing.port.GraphAttemptInactive):
        result_commit.commit_result(result_db.attempt, "doc")
    make.assert_not_called()
    fencing.record.assert_called_once()


def test_idle_approval_can_commit_but_reservation_blocks_new_execution(result_db, monkeypatch):
    result_db._execute("UPDATE graph_runs SET status='idle'")
    def make(*args):
        assert not result_db.db.in_transaction  # never Git inside a Flow transaction
        with pytest.raises(execution_store.LeaseLost):
            execution_store.request(result_db.attempt, "run", "review", {})
        why, _ = fencing._token_reason({"token_id": "author"}, result_db.attempt, False, "inbox_edit")
        assert why == "attempt_result_frozen"
        return outcome()
    monkeypatch.setattr(commit, "create_attempt_commit", make)
    assert result_commit.commit_result(result_db.attempt, "doc")["result_commit"] == RESULT


def test_git_failure_is_retryable_without_a_result_row(result_db, monkeypatch):
    make = Mock(side_effect=[outcome(changed=False, reason="git_busy"), outcome()])
    monkeypatch.setattr(commit, "create_attempt_commit", make)
    first = result_commit.commit_result(result_db.attempt, "doc")
    assert first["result_commit"] is None and tr_commit_ledger.by_attempt(result_db.attempt) is None
    assert workspace.attempt(result_db.attempt)["integration_reason"] == "result_commit:git_busy"
    assert result_commit.commit_result(result_db.attempt, "doc")["result_commit"] == RESULT


def test_result_and_ledger_rollback_together_after_git_success(result_db, monkeypatch):
    monkeypatch.setattr(commit, "create_attempt_commit", Mock(return_value=outcome()))
    real = run_store._affected
    def fail_record(sql, params):
        if "SET result_commit" in sql:
            raise RuntimeError("simulated DB loss")
        return real(sql, params)
    monkeypatch.setattr(run_store, "_affected", fail_record)
    with pytest.raises(RuntimeError, match="DB loss"):
        result_commit.commit_result(result_db.attempt, "doc")
    assert tr_commit_ledger.by_attempt(result_db.attempt) is None
    assert workspace.attempt(result_db.attempt)["result_commit"] is None
    assert workspace.attempt(result_db.attempt)["integration_status"] == "pending"
    monkeypatch.setattr(run_store, "_affected", real)
    assert result_commit.commit_result(result_db.attempt, "doc")["result_commit"] == RESULT
    assert tr_commit_ledger.by_attempt(result_db.attempt)["commit_sha"] == RESULT


def test_concurrent_result_requests_create_one_commit(result_db, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def make(*args):
        entered.set()
        assert release.wait(5)
        return outcome()
    create = Mock(side_effect=make)
    monkeypatch.setattr(commit, "create_attempt_commit", create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(result_commit.commit_result, result_db.attempt, "doc")
        try:
            assert entered.wait(5)
            second = pool.submit(result_commit.commit_result, result_db.attempt, "doc")
        finally:
            release.set()
        assert first.result(5)["result_commit"] == second.result(5)["result_commit"] == RESULT
    assert create.call_count == 1
    assert len(result_db._fetch_all("SELECT * FROM tr_commit_ledger WHERE attempt_id=?",
                                   [result_db.attempt])) == 1


def test_nested_approval_hold_does_not_hide_other_token_writer():
    other_ready, release, exclusive_entered = threading.Event(), threading.Event(), threading.Event()
    def other_writer():
        with fencing.shared("nested-result-test"):
            other_ready.set()
            assert release.wait(5)
    def approve():
        with fencing.shared("nested-result-test"), fencing.shared("nested-result-test"):
            with fencing.exclusive("nested-result-test", own_ok=True):
                exclusive_entered.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(other_writer)
        assert other_ready.wait(5)
        approval = pool.submit(approve)
        try:
            assert not exclusive_entered.wait(0.1)
        finally:
            release.set()
        writer.result(5)
        approval.result(5)
    assert exclusive_entered.is_set()


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


@pytest.fixture
def trees(tmp_path, monkeypatch):
    if not shutil.which("git"):
        pytest.skip("Git not installed in TS environment")
    repo = tmp_path / "group"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Attempt Test")
    git(repo, "config", "user.email", "attempt@example.invalid")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "chore: base")
    base = git(repo, "rev-parse", "HEAD")
    first, second = tmp_path / "first", tmp_path / "second"
    git(repo, "worktree", "add", "-b", "first", str(first), base)
    git(repo, "worktree", "add", "-b", "second", str(second), base)
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _: {"enabled": True})
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(commit, "_author_env_from_cfg", lambda _: None)
    acquire = Mock(return_value=SimpleNamespace(ok=True, lock_key="W:attempt"))
    release = Mock()
    monkeypatch.setattr(lock_manager, "new_context", lambda *_: object())
    monkeypatch.setattr(lock_manager, "acquire", acquire)
    monkeypatch.setattr(lock_manager, "release", release)
    return SimpleNamespace(group=repo, first=first, second=second, base=base,
                           acquire=acquire, release=release)


def test_real_git_isolates_attempt_deletions_and_reuses_recovery_trailer(trees):
    (trees.first / "tracked.txt").unlink()
    (trees.first / "only-first.py").write_text("value = 1\n", encoding="utf-8")
    (trees.second / "only-second.py").write_text("value = 2\n", encoding="utf-8")
    (trees.group / "group-only.py").write_text("value = 3\n", encoding="utf-8")
    debris = trees.first / "server" / ".test-tmp-result"
    debris.mkdir(parents=True)
    (debris / "cache.txt").write_text("temporary", encoding="utf-8")
    args = ("project", trees.first, "first", trees.base, "att-first", "doc", "feat(graph): first")
    initial = commit.create_attempt_commit(*args)
    assert initial["committed"]
    assert initial["committed_paths"] == ["only-first.py", "tracked.txt"]
    assert "server/.test-tmp-result/cache.txt" in initial["excluded_artifacts"]
    assert git(trees.group, "rev-parse", "HEAD") == git(trees.second, "rev-parse", "HEAD") == trees.base
    # Simulated server restart: no DB result exists, only Git's durable commit/trailer.
    retry = commit.create_attempt_commit(*args)
    assert retry["recovered"] and retry["commit_sha"] == initial["commit_sha"]
    assert git(trees.first, "rev-list", "--count", trees.base + "..HEAD") == "1"
    assert all(call.args[0] == "W" for call in trees.acquire.call_args_list)
    assert trees.release.call_count == 2


def test_real_git_no_change_and_foreign_head(trees):
    args = ("project", trees.first, "first", trees.base, "att-first", "doc", "feat(graph): first")
    assert commit.create_attempt_commit(*args)["skipped_reason"] == "no_changes"
    git(trees.first, "commit", "--allow-empty", "-m", "chore: unrelated")
    foreign = git(trees.first, "rev-parse", "HEAD")
    refused = commit.create_attempt_commit(*args)
    assert not refused["committed"] and refused["skipped_reason"] == "attempt_head_mismatch"
    assert git(trees.first, "rev-parse", "HEAD") == foreign


def test_restart_after_db_failure_recovers_same_git_commit(result_db, trees, monkeypatch):
    result_db._execute("UPDATE graph_node_attempts SET input_commit=? WHERE attempt_id=?",
                       [trees.base, result_db.attempt])
    monkeypatch.setattr(workspace, "source_root", lambda *a, **kw: trees.first)
    monkeypatch.setattr(workspace, "location", lambda *a: (trees.first.parent, trees.first, "first"))
    (trees.first / "source.py").write_text("value = 1\n", encoding="utf-8")
    real_record = tr_commit_ledger.record_commit
    monkeypatch.setattr(tr_commit_ledger, "record_commit", Mock(side_effect=RuntimeError("DB offline")))
    with pytest.raises(RuntimeError, match="DB offline"):
        result_commit.commit_result(result_db.attempt, "doc")
    sha = git(trees.first, "rev-parse", "HEAD")
    assert sha != trees.base and workspace.attempt(result_db.attempt)["result_commit"] is None
    monkeypatch.setattr(tr_commit_ledger, "record_commit", real_record)
    recovered = result_commit.commit_result(result_db.attempt, "doc")
    assert recovered["result_commit"] == sha and recovered["recovered"]
    assert git(trees.first, "rev-list", "--count", trees.base + "..HEAD") == "1"
    assert tr_commit_ledger.by_attempt(result_db.attempt)["commit_sha"] == sha
