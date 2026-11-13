"""v02gui.0013: stopping a dirty attempt automatically preserves its work and releases the run.

Real Git worktrees and SQLite journal; process/merge gates are supplied by the fixture. Both legacy
branch and detached-ref layouts are covered, including retries after failed preservation/removal.
"""
import json
from types import SimpleNamespace

import pytest

from flowgate.graph_run import stop, workspace, execution_store as ledger
from modules.flow_gate.graph_workflow.store import run_store
from test_graph_attempt_cleanup_preserve_0111 import (
    harness, repo, layout, _attempt, _git, _gone, _kept, _fail_git, COMMIT,
)  # noqa: F401
from test_graph_attempt_workspaces_0061 import GROUP


def _stopped(repo):
    item = _attempt(repo)
    repo.db._execute("UPDATE graph_node_attempts SET invalidated_reason='user_stop', active_node_key=NULL WHERE attempt_id=?", [item.attempt])
    return item


def _refs(repo, item):
    return repo.git("for-each-ref", "--format=%(refname)", workspace.PRESERVED_REF_PREFIX
                    + workspace.merge_target.workspace_key(item.attempt)).splitlines()


@pytest.fixture
def stop_ready(repo, monkeypatch):
    monkeypatch.setattr(stop, "with_flow_guard", workspace.with_flow_guard)
    monkeypatch.setattr(stop.result_merge, "reclaim_integrating", lambda *_: None)
    monkeypatch.setattr(stop.result_merge, "abort_for_stop", lambda *_: [])
    monkeypatch.setattr(stop.result_merge, "session_pending", lambda *_: [])
    monkeypatch.setattr(stop.result_merge, "integrating", lambda *_: [])
    monkeypatch.setattr(stop, "_settle_gate_decisions", lambda *_: (set(), {}))
    from flowgate.gate_decisions import run_hold
    monkeypatch.setattr(run_hold, "gate_activity", lambda *_: {})
    return repo


@pytest.mark.parametrize("dirty", [True, False])
def test_stop_saves_commits_index_and_working_files_then_unlocks(stop_ready, dirty):
    repo = stop_ready
    item = _attempt(repo, status="active")
    (item.root / "value.txt").write_text("committed", encoding="utf-8")
    item.at("add", ".")
    item.at(*COMMIT, "-m", "unique attempt commit")
    committed = item.at("rev-parse", "HEAD")
    group_head = repo.git("rev-parse", "HEAD")
    if dirty:
        (item.root / "value.txt").write_text("staged", encoding="utf-8")
        item.at("add", ".")
        (item.root / "value.txt").write_text("working", encoding="utf-8")
        (item.root / "new.bin").write_bytes(b"\x00\x01\xff")
    repo.db._execute("UPDATE graph_runs SET status='stopping'")
    assert stop.advance(GROUP, "run") == ([], [])
    run = run_store.run_by_id("run")
    assert (run["status"], run["idle_reason"]) == ("idle", "stopped")
    assert _gone(repo, item)
    refs = _refs(repo, item)
    assert len(refs) == 1
    snapshot = refs[0]
    assert repo.git("show", snapshot + ":value.txt") == ("working" if dirty else "committed")
    assert repo.git("show", snapshot + "^:value.txt") == ("staged" if dirty else "committed")
    assert workspace.git._run_git(["merge-base", "--is-ancestor", committed, snapshot], cwd=repo.group).returncode == 0
    if dirty:
        assert repo.git("cat-file", "-s", snapshot + ":new.bin") == "3"
    assert repo.git("rev-parse", "HEAD") == group_head
    assert workspace.cleanup_for_stop(item.attempt)
    assert _refs(repo, item) == refs
    # New execution uses a fresh attempt, not the retained/removed invalidated workspace.
    successor = run_store.open_attempt("run", "a", 1)
    assert successor != item.attempt
    assert not repo.held


@pytest.mark.parametrize("failure", ["add", "commit-tree", "update-ref", "status", "remove", "owner", "locks"])
def test_snapshot_or_removal_failure_keeps_original_but_finishes_stop(stop_ready, monkeypatch, failure):
    repo = stop_ready
    item = _stopped(repo)
    (item.root / "value.txt").write_text("staged", encoding="utf-8")
    item.at("add", ".")
    (item.root / "value.txt").write_text("working", encoding="utf-8")
    head, index = item.at("rev-parse", "HEAD"), item.at("write-tree")
    if failure == "owner":
        path = item.directory / workspace.merge_target.OWNER_MARKER
        marker = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**marker, "attempt_id": "foreign"}), encoding="utf-8")
    elif failure == "locks":
        monkeypatch.setattr(workspace.locks, "acquire", lambda *a, **kw: SimpleNamespace(ok=False, lock_key=None))
    else:
        _fail_git(monkeypatch, failure)
    repo.db._execute("UPDATE graph_runs SET status='stopping'")
    assert stop.advance(GROUP, "run") == ([], [])
    assert run_store.run_by_id("run")["status"] == "idle"
    assert _kept(repo, item)
    assert (item.root / "value.txt").read_text(encoding="utf-8") == "working"
    assert item.at("rev-parse", "HEAD") == head
    assert item.at("write-tree") == index
    assert any(json.loads(e["payload_json"]).get("mode") == workspace.PRESERVED for e in run_store.events("run"))


def test_active_process_still_blocks_stop_and_snapshot(stop_ready, monkeypatch):
    repo = stop_ready
    item = _stopped(repo)
    (item.root / "value.txt").write_text("in flight", encoding="utf-8")
    repo.db._execute("UPDATE graph_attempt_runs SET status='running' WHERE exec_id=?", [item.execution])
    monkeypatch.setattr(stop, "_process_ended", lambda _: False)
    repo.db._execute("UPDATE graph_runs SET status='stopping'")
    assert stop.advance(GROUP, "run")[0] == [item.execution]
    assert run_store.run_by_id("run")["status"] == "stopping"
    assert not workspace.cleanup_for_stop(item.attempt)
    assert _kept(repo, item) and _refs(repo, item) == []


def test_startup_retries_failed_snapshot_and_preserves_deletions(repo, monkeypatch):
    item = _stopped(repo)
    (item.root / "value.txt").unlink()
    (item.root / "new.txt").write_text("new work", encoding="utf-8")
    with monkeypatch.context() as failing:
        _fail_git(failing, "update-ref")
        assert workspace.cleanup_for_stop(item.attempt)
        assert _kept(repo, item)
    workspace.cleanup_finished()
    assert _gone(repo, item)
    ref, = _refs(repo, item)
    assert repo.git("ls-tree", "--name-only", ref) == "new.txt"
    assert repo.git("show", ref + ":new.txt") == "new work"
    workspace.cleanup_finished()
    assert _refs(repo, item) == [ref]
