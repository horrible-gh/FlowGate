"""Regression tests for remote import, protected draft repair and live chat aggregation."""
from contextlib import nullcontext
import subprocess
import pytest

from flowgate.graph_run import editing
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import branches, base_slot, group_work_base
from modules.flow_gate.services.git.credentials import GitServiceError


@pytest.fixture
def protected(monkeypatch):
    a = {"node_key": "gn_a", "node_type": "document", "label": "A"}
    gate = {"node_key": "gn_gate", "node_type": "gate", "label": "Confirm"}
    edge = {"edge_id": "ge_a", "source": "gn_a", "target": "gn_gate", "condition": None}
    monkeypatch.setattr(editing.run_store, "current_run", lambda _: {"run_id": "r", "revision_no": 1})
    monkeypatch.setattr(editing.run_store, "node_runs", lambda _: [{"node_key": "gn_gate", "status": "open"}])
    monkeypatch.setattr(editing.definition_store, "revision", lambda *_: {"nodes": [a, gate], "edges": [edge]})
    monkeypatch.setattr(editing, "_unresolved_gates", lambda *_: set())
    monkeypatch.setattr(editing.layout_store, "retired_keys", lambda _: set())
    return a, gate, edge


def test_missing_open_gate_and_input_are_restored_without_losing_new_card(protected):
    a, gate, edge = protected
    new = {"node_key": "gn_new", "node_type": "document", "label": "new"}
    nodes, edges = [a, new], []
    editing.protect_definition("f", nodes, edges, restore_missing=True)
    assert nodes == [a, new, gate]
    assert edges == [edge]


def test_repair_does_not_allow_modifying_present_protected_card(protected):
    a, gate, edge = protected
    with pytest.raises(GraphApiError) as error:
        editing.protect_definition("f", [a, {**gate, "label": "changed"}], [edge], restore_missing=True)
    assert error.value.code == "graph_node_protected"
    with pytest.raises(GraphApiError):
        editing.protect_definition("f", [a, gate], [], restore_missing=True)


def test_confirm_still_refuses_missing_protected_card(protected):
    a, _, _ = protected
    with pytest.raises(GraphApiError):
        editing.protect_definition("f", [a], [])


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_remote_import_fetches_only_requested_branch_and_keeps_checkout(tmp_path, monkeypatch):
    origin, repo = tmp_path / "origin", tmp_path / "checkout"
    origin.mkdir()
    git(origin, "init", "-b", "main")
    git(origin, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "base")
    git(origin, "branch", "feature/test")
    git(origin, "branch", "other")
    git(tmp_path, "clone", "--single-branch", str(origin), str(repo))
    cfg = {"enabled": True, "base_branch": "main", "repo_url": str(origin)}
    monkeypatch.setattr(branches, "_branch_context", lambda _: (cfg, repo, "main"))
    monkeypatch.setattr(base_slot, "_domain_locks", lambda *_: nullcontext())
    monkeypatch.setattr(branches, "_meta_lock", lambda *_: nullcontext())
    monkeypatch.setattr(gs, "_load_secret_for", lambda _: "")
    monkeypatch.setattr(gs.db_git, "get_state_by_branch", lambda *_: None)
    monkeypatch.setattr(gs.db_git, "list_states_of_project_any", lambda _: [])
    monkeypatch.setattr(gs.db_git, "get_config", lambda _: cfg)
    monkeypatch.setattr(gs, "_base_root_of", lambda _: repo)
    before = git(repo, "rev-parse", "HEAD")
    # Standard clone remote refspec: required for tracking setup, no unrelated fetch.
    git(repo, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(repo, "update-ref", "refs/remotes/origin/feature/test", before)
    choices = group_work_base.list_group_work_base_options("p")["branches"]
    assert {"name": "feature/test", "is_project_base": False, "remote_only": True} in choices
    assert all(c["name"] != "HEAD" for c in choices)
    assert branches.import_remote_branch("p", "feature/test")["branch"] == "feature/test"
    assert git(repo, "symbolic-ref", "--short", "HEAD") == "main"
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "config", "branch.feature/test.remote") == "origin"
    assert git(repo, "config", "branch.feature/test.merge") == "refs/heads/feature/test"
    assert not gs._ref_exists(repo, "refs/remotes/origin/other")
    with pytest.raises(GitServiceError) as error:
        branches.import_remote_branch("p", "feature/test")
    assert error.value.code == "branch_name_exists"


def test_active_query_includes_chats_even_when_first_live_run_is_a_card(monkeypatch):
    from modules.flow_gate.services.ai_invoke import diagnostics
    monkeypatch.setattr(diagnostics, "list_live_runs", lambda **_: [
        {"run_id": "card", "doc_ref": "p.m.1.0001-T", "status": "running"},
        {"run_id": "chat", "doc_ref": "p.m.1.0002-CH", "status": "cancelling"}])
    class Service:
        def _active_run_for_group(self, _): return {"run_id": "card", "doc_ref": "p.m.1.0001-T"}
    monkeypatch.setattr(diagnostics, "_svc", lambda: Service())
    monkeypatch.setattr(diagnostics, "get_status", lambda _: {"run_id": "card"})
    assert diagnostics.get_active_status("p.m.1")["chat_runs"] == [
        {"run_id": "chat", "doc_ref": "p.m.1.0002-CH", "status": "cancelling"}]
