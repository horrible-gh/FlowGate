from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")
_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import git_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git import branches as branch_service
from modules.flow_gate.services.git.credentials import GitServiceError


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)

    states = [{"project_id": "flowgate", "group_id": "flowgate.default.1", "branch": "slot-live"}]
    monkeypatch.setattr(branch_service, "_branch_context", lambda project_id: ({"enabled": 1}, root, "main"))
    monkeypatch.setattr(git_service, "_acquire_lock", lambda project_id, holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda project_id, holder: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda project_id: list(states))
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])
    subprocess.run(["git", "branch", "slot-live"], cwd=root, check=True)
    return root


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)


def test_catalog_classifies_local_remote_only_and_registered_slot(repo):
    _git(repo, "branch", "local-only")
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "update-ref", "refs/remotes/origin/remote-only", main_sha)

    result = git_service.list_branches("flowgate")
    rows = {row["name"]: row for row in result["branches"]}

    assert rows["main"]["kind"] == "base"
    assert rows["main"]["has_remote_counterpart"] is True
    assert rows["local-only"]["kind"] == "local"
    assert rows["slot-live"]["kind"] == "internal_slot"
    assert rows["slot-live"]["connected_group_id"] == "flowgate.default.1"
    assert rows["slot-live"]["can_be_create_source"] is False
    assert rows["remote-only"]["kind"] == "remote_only"
    assert rows["remote-only"]["can_delete"] is False
    assert rows["remote-only"]["can_be_create_source"] is False
    assert rows["remote-only"]["ahead_of_base"] is None


def test_name_validation_is_git_check_ref_format_based(repo):
    with pytest.raises(GitServiceError) as caught:
        git_service.validate_new_branch_name("flowgate", repo, "main", "bad..name")
    assert caught.value.status == 422
    assert caught.value.code == "branch_ref_format_invalid"
    assert "git_stderr" in caught.value.details

    with pytest.raises(GitServiceError) as caught:
        git_service.validate_new_branch_name("flowgate", repo, "main", "main")
    assert caught.value.code == "branch_reserved_name"


def test_create_rejects_remote_only_and_actual_slot_then_creates_local_only(repo, monkeypatch):
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/remote-only", main_sha)

    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-a", "remote-only")
    assert caught.value.code == "branch_source_remote_only"

    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-b", "slot-live")
    assert caught.value.code == "branch_source_internal_slot"

    result = git_service.create_branch("flowgate", "feature/ok", "main")
    assert result["published"] is False
    assert _git(repo, "show-ref", "--verify", "refs/heads/feature/ok").returncode == 0
    assert _git(repo, "show-ref", "--verify", "refs/remotes/origin/feature/ok").returncode != 0


def test_create_rechecks_after_lock_and_fails_closed(repo, monkeypatch):
    calls = 0

    def changing_states(project_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return [{"group_id": "flowgate.default.2", "branch": "source"}]

    _git(repo, "branch", "source")
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", changing_states)
    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-branch", "source")
    assert caught.value.code == "branch_source_internal_slot"
    assert _git(repo, "show-ref", "--verify", "refs/heads/new-branch").returncode != 0


def test_delete_guards_and_local_only_remote_preservation(repo, monkeypatch):
    assert git_service.check_branch_delete("flowgate", "missing") == "branch_not_found"
    assert git_service.check_branch_delete("flowgate", "main") == "branch_is_base"
    assert git_service.check_branch_delete("flowgate", "slot-live") == "branch_is_internal_slot"

    _git(repo, "branch", "in-use")
    monkeypatch.setattr(
        git_service.db_git,
        "list_open_sessions",
        lambda: [{"merge_id": 7, "group_id": "flowgate.default.9", "context": '{"target_branch":"in-use"}'}],
    )
    monkeypatch.setattr(git_service.db_git, "get_state", lambda group_id: None)
    assert git_service.check_branch_delete("flowgate", "in-use") == "branch_in_use"
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])

    _git(repo, "branch", "merged")
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/merged", main_sha)
    result = git_service.delete_branch("flowgate", "merged")
    assert result["remote_counterpart_remains"] is True
    assert _git(repo, "show-ref", "--verify", "refs/heads/merged").returncode != 0
    assert _git(repo, "show-ref", "--verify", "refs/remotes/origin/merged").returncode == 0


def test_delete_unmerged_reports_commit_preview(repo):
    _git(repo, "checkout", "-b", "unmerged")
    (repo / "work.txt").write_text("work\n", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-m", "unmerged work")
    _git(repo, "checkout", "main")

    with pytest.raises(GitServiceError) as caught:
        git_service.delete_branch("flowgate", "unmerged")
    assert caught.value.code == "branch_unmerged_commits"
    assert caught.value.details["commits"][0]["subject"] == "unmerged work"
    assert _git(repo, "show-ref", "--verify", "refs/heads/unmerged").returncode == 0


def _client(*, is_admin=True):
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": is_admin}
    return TestClient(app, raise_server_exceptions=False)


def test_branch_routes_rbac_delegation_and_error_envelope(monkeypatch):
    monkeypatch.setattr(git_routes.git_service, "list_branches", lambda project_id: {"ok": True, "branches": []})
    monkeypatch.setattr(git_routes.git_service, "create_branch", lambda project_id, name, source: {"ok": True, "branch": name})
    monkeypatch.setattr(git_routes.git_service, "delete_branch", lambda project_id, name: {"ok": True, "branch": name})
    client = _client()
    assert client.get("/api/v1/projects/flowgate/git/branches").status_code == 200
    assert client.post("/api/v1/projects/flowgate/git/branches", json={"name": "x/y", "source_branch": "main"}).json()["branch"] == "x/y"
    assert client.delete("/api/v1/projects/flowgate/git/branches/x/y").json()["branch"] == "x/y"

    def boom(project_id, name):
        raise GitServiceError(409, "branch_is_base", "base branch cannot be deleted")

    monkeypatch.setattr(git_routes.git_service, "delete_branch", boom)
    response = client.delete("/api/v1/projects/flowgate/git/branches/main")
    assert response.status_code == 409
    assert response.json() == {"ok": False, "error": {"code": "branch_is_base", "message": "base branch cannot be deleted"}}

    monkeypatch.setattr(
        "modules.flow_gate.rbac.decorators._permission_service.has_permission",
        lambda *args, **kwargs: False,
    )
    assert _client(is_admin=False).get("/api/v1/projects/flowgate/git/branches").status_code == 403
    assert _client(is_admin=False).post("/api/v1/projects/flowgate/git/branches", json={"name": "x", "source_branch": "main"}).status_code == 403
    assert _client(is_admin=False).delete("/api/v1/projects/flowgate/git/branches/x").status_code == 403