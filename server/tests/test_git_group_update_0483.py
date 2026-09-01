import subprocess
from pathlib import Path

import pytest

from modules.flow_gate.api.v1.git_routes import router
from modules.flow_gate.services import git_service
from modules.flow_gate.services.git_service import GitServiceError


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True,
    )


def _repo(tmp_path: Path, *, tracked: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "FlowGate Test")
    _git(repo, "config", "user.email", "flowgate@example.invalid")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    if tracked:
        (repo / "blocked.txt").write_text("original\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "seed")
    _git(repo, "branch", "group/test")
    if tracked:
        (repo / "blocked.txt").write_text("base\n", encoding="utf-8")
    else:
        (repo / "blocked.txt").write_text("base\n", encoding="utf-8")
        _git(repo, "add", "blocked.txt")
    _git(repo, "commit", "-am", "base change")
    _git(repo, "checkout", "group/test")
    return repo


def _patch_recovery(monkeypatch, repo: Path) -> None:
    monkeypatch.setattr(
        git_service, "_finalize_context",
        lambda _gid: (
            {"base_branch": "main"}, {"branch": "group/test", "status": "none"},
            "demo", repo, repo,
        ),
    )
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda _project, _holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda _project, _holder: None)
    monkeypatch.setattr(git_service.db_git, "get_open_session_by_group", lambda _gid: None)


def test_group_update_session_does_not_hold_base(monkeypatch):
    group_update = {"group_id": "demo.default.0001", "merge_id": 7, "kind": "group_update"}
    normal_merge = {"group_id": "demo.default.0002", "merge_id": 8, "kind": "merge"}
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [group_update])
    monkeypatch.setattr(git_service.db_git, "session_kind", lambda row: row["kind"])
    monkeypatch.setattr(git_service, "_project_of_group", lambda _gid: "demo")
    assert git_service.open_merge_session_of_project("demo") is None

    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [group_update, normal_merge])
    assert git_service.open_merge_session_of_project("demo") == normal_merge


def test_group_untracked_recovery_routes_are_registered():
    paths = {route.path for route in router.routes}
    assert "/api/v1/groups/{group_id}/git/untracked-commit" in paths
    assert "/api/v1/groups/{group_id}/git/untracked-revert" in paths
    assert "/api/v1/groups/{group_id}/git/untracked-remove" in paths


@pytest.mark.parametrize("path", ["../outside", "/absolute", "C:/absolute"])
def test_group_untracked_recovery_rejects_unsafe_paths_before_git(monkeypatch, path):
    monkeypatch.setattr(
        git_service, "_finalize_context",
        lambda _gid: ({"base_branch": "main"}, {"branch": "group/test"}, "demo", Path("base"), Path("group")),
    )
    with pytest.raises(GitServiceError) as caught:
        git_service.group_update_untracked_recover("demo.default.0001", [path], "remove")
    assert caught.value.status == 422
    assert caught.value.code == "invalid_request"


def test_group_untracked_commit_success_in_real_repository(tmp_path, monkeypatch):
    repo = _repo(tmp_path, tracked=False)
    (repo / "blocked.txt").write_text("group copy\n", encoding="utf-8")
    _patch_recovery(monkeypatch, repo)

    result = git_service.group_update_untracked_recover(
        "demo.default.0001", ["blocked.txt"], "commit", "keep group copy",
    )

    assert result["result"]["action"] == "commit"
    assert _git(repo, "show", "HEAD:blocked.txt").stdout == "group copy\n"
    assert _git(repo, "log", "-1", "--pretty=%s").stdout.strip() == "keep group copy"


def test_group_untracked_remove_success_in_real_repository(tmp_path, monkeypatch):
    repo = _repo(tmp_path, tracked=False)
    (repo / "blocked.txt").write_text("discard me\n", encoding="utf-8")
    _patch_recovery(monkeypatch, repo)

    result = git_service.group_update_untracked_recover(
        "demo.default.0001", ["blocked.txt"], "remove",
    )

    assert result["result"]["action"] == "remove"
    assert not (repo / "blocked.txt").exists()


def test_group_tracked_revert_success_in_real_repository(tmp_path, monkeypatch):
    repo = _repo(tmp_path, tracked=True)
    (repo / "blocked.txt").write_text("local edit\n", encoding="utf-8")
    _patch_recovery(monkeypatch, repo)

    result = git_service.group_update_untracked_recover(
        "demo.default.0001", ["blocked.txt"], "revert",
    )

    assert result["result"]["action"] == "revert"
    assert (repo / "blocked.txt").read_text(encoding="utf-8") == "original\n"
    assert _git(repo, "status", "--porcelain").stdout == ""


def test_finalize_state_exposes_open_group_update_for_resume(monkeypatch):
    monkeypatch.setattr(git_service, "_project_of_group", lambda _gid: "demo")
    monkeypatch.setattr(git_service.db_git, "get_config", lambda _pid: {"enabled": True, "base_branch": "main"})
    monkeypatch.setattr(git_service.db_git, "get_state", lambda _gid: {
        "worktree_registered": 1, "status": "waiting", "branch": "group/test",
        "merge_id": None, "merge_commit": None,
    })
    monkeypatch.setattr(git_service, "_project_name", lambda _pid: None)
    monkeypatch.setattr(git_service, "resolve_commit_message", lambda _gid: ("subject", "fallback"))
    monkeypatch.setattr(git_service.db_git, "get_open_session_by_group", lambda _gid: {
        "merge_id": 41, "kind": git_service.db_git.SESSION_KIND_GROUP_UPDATE,
    })
    monkeypatch.setattr(git_service.db_git, "session_kind", lambda row: row["kind"])

    response = git_service.get_finalize_state("demo.default.0001")

    assert response["state"]["merge_id"] == 41


def test_tracked_merge_blockers_parsing():
    stderr = """error: Your local changes to the following files would be overwritten by merge:\n\tblocked.txt\nPlease commit your changes or stash them before you merge.\nAborting\n"""
    assert git_service._tracked_merge_blockers(stderr) == ["blocked.txt"]
    assert git_service._tracked_merge_blockers("fatal: unrelated") is None