"""0045: immutable recovery inputs exercised through real Git worktree creation."""
from unittest.mock import Mock
import pytest
from test_graph_attempt_workspaces_0061 import harness, repo, prepare, PROJECT, GROUP
from test_graph_attempt_runs_0061 import MIGRATIONS
from flowgate.graph_run import recovery_store, workspace
from modules.flow_gate.graph_workflow.store import run_store


def test_workspace_harness_does_not_cache_stubs_in_run_service(harness):
    # First import under the harness used to retain stubs even after teardown.
    from modules.flow_gate.graph_workflow.services import run_apply, run_service

    for name in ("load_context", "emit_node_state", "emit_run_status"):
        assert getattr(run_service, name) is not getattr(run_apply, name), name


def test_retry_materializes_inspected_commit_without_reset_or_merge(repo, monkeypatch):
    repo.db._execute("INSERT INTO groups(group_id) VALUES (?)", [GROUP])
    repo.db.db.executescript((MIGRATIONS / "sqlite" / "195_graph_recovery.sql").read_text(encoding="utf-8"))
    old = repo.new("old")
    old_root = prepare(old)
    old_sha = workspace.attempt(old.attempt)["input_commit"]
    (repo.group / "value.txt").write_text("inspected new input", encoding="utf-8")
    repo.git("add", "value.txt")
    repo.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "new input")
    selected = repo.git("rev-parse", "HEAD")
    new = repo.new("new")
    recovery_store.pin(new.attempt, GROUP, {"commit": selected, "input_attempt_ids": [], "predecessors": []})
    # This window still has the old attempt's base. The explicit retry must use its pinned new input.
    forbid = Mock(side_effect=AssertionError("recovery must not merge or reset"))
    monkeypatch.setattr(workspace, "_join", forbid)
    real_git = workspace.git._run_git
    def safe_git(args, **kwargs):
        assert not (args and args[0] in ("reset", "merge")), args
        return real_git(args, **kwargs)
    monkeypatch.setattr(workspace.git, "_run_git", safe_git)
    root = prepare(new)
    assert workspace.git._rev_parse(root, "HEAD") == selected != old_sha
    assert workspace.attempt(new.attempt)["input_commit"] == selected
    assert (root / "value.txt").read_text(encoding="utf-8") == "inspected new input"
    assert (old_root / "value.txt").read_text(encoding="utf-8") == "initial"
    forbid.assert_not_called()


def test_recovery_join_is_refused_before_any_worktree_is_created(repo, monkeypatch):
    repo.db._execute("INSERT INTO groups(group_id) VALUES (?)", [GROUP])
    repo.db.db.executescript((MIGRATIONS / "sqlite" / "195_graph_recovery.sql").read_text(encoding="utf-8"))
    item = repo.new()
    sha = repo.git("rev-parse", "HEAD")
    recovery_store.pin(item.attempt, GROUP, {"commit": sha, "input_attempt_ids": [], "predecessors": []})
    monkeypatch.setattr(workspace, "_start", lambda *a: {"input_commit": sha, "join_inputs": ["other"]})
    with pytest.raises(Exception) as caught:
        prepare(item)
    assert caught.value.details["cause"] == "recovery_join_requires_merge"
    assert not workspace.location(workspace.attempt(item.attempt), PROJECT)[1].exists()



def test_interrupted_marker_at_another_input_is_not_reused(repo):
    repo.db._execute("INSERT INTO groups(group_id) VALUES (?)", [GROUP])
    repo.db.db.executescript((MIGRATIONS / "sqlite" / "195_graph_recovery.sql").read_text(encoding="utf-8"))
    item = repo.new()
    workspace._materialize(workspace.attempt(item.attempt), PROJECT)
    (repo.group / "value.txt").write_text("new inspected input", encoding="utf-8")
    repo.git("add", "value.txt")
    repo.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "advance")
    selected = repo.git("rev-parse", "HEAD")
    recovery_store.pin(item.attempt, GROUP, {"commit": selected, "input_attempt_ids": [], "predecessors": []})
    with pytest.raises(Exception) as caught:
        prepare(item)
    assert caught.value.details["cause"] == "recovery_context_changed"
