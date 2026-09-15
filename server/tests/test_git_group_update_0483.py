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
    monkeypatch.setattr(git_service, "guard_base_free", lambda _project: None)
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


def _repo_pair(tmp_path: Path) -> tuple[Path, Path]:
    """A base checkout (with an 'origin' remote) and a SEPARATE group worktree of the
    same repository, seeded with a shared.txt both sides will edit differently.

    NR0025 §8 / T0028 §3.4 (1): update_from_base ff-only-merges base_root against
    origin/<base branch> before merging base_branch into wt_path, so the two paths
    must be distinct checkouts, not one repo playing both roles.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    )
    base_root = tmp_path / "base"
    subprocess.run(
        ["git", "clone", str(origin), str(base_root)],
        cwd=tmp_path, text=True, capture_output=True, check=True,
    )
    _git(base_root, "config", "user.name", "FlowGate Test")
    _git(base_root, "config", "user.email", "flowgate@example.invalid")
    (base_root / "shared.txt").write_text("seed\n", encoding="utf-8")
    _git(base_root, "add", "shared.txt")
    _git(base_root, "commit", "-m", "seed")
    _git(base_root, "push", "origin", "main")
    _git(base_root, "branch", "group/test")

    wt_path = tmp_path / "group"
    _git(base_root, "worktree", "add", str(wt_path), "group/test")

    (wt_path / "shared.txt").write_text("group side\n", encoding="utf-8")
    _git(wt_path, "commit", "-am", "group edits shared.txt")

    (base_root / "shared.txt").write_text("main side\n", encoding="utf-8")
    _git(base_root, "commit", "-am", "main edits shared.txt")
    _git(base_root, "push", "origin", "main")

    assert _git(base_root, "status", "--porcelain").stdout == ""
    assert _git(wt_path, "status", "--porcelain").stdout == ""
    return base_root, wt_path


def _patch_group_update(
    monkeypatch, cfg: dict, state: dict, project_id: str,
    base_root: Path, wt_path: Path, calls: list[dict],
) -> None:
    monkeypatch.setattr(
        git_service, "_finalize_context",
        lambda _gid: (cfg, state, project_id, base_root, wt_path),
    )
    monkeypatch.setattr(git_service.db_git, "get_open_session_by_group", lambda _gid: None)
    monkeypatch.setattr(git_service, "guard_base_free", lambda _pid: None)
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda _pid, _holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda _pid, _holder: None)

    def _fake_create_session(group_id, files, *, kind, context):
        calls.append({"group_id": group_id, "files": files, "kind": kind, "context": context})
        return 4242

    monkeypatch.setattr(git_service.db_git, "create_session", _fake_create_session)


def test_group_update_records_conflict_status_merge_id_and_files(tmp_path, monkeypatch):
    """T0028 §3.3 test A — a REAL content conflict, resolved by the real merge, must
    surface status/merge_id/conflict_files and open a real (unaborted) conflict."""
    base_root, wt_path = _repo_pair(tmp_path)
    cfg = {"base_branch": "main"}
    state = {"branch": "group/test", "status": "waiting"}
    calls: list[dict] = []
    _patch_group_update(monkeypatch, cfg, state, "demo", base_root, wt_path, calls)

    result = git_service.update_from_base("demo.default.0001")

    assert result["ok"] is True
    assert result["result"]["status"] == "conflict"
    assert result["result"]["merge_id"] == 4242
    assert result["result"]["conflict_files"] == ["shared.txt"]

    assert len(calls) == 1
    assert calls[0]["files"] == ["shared.txt"]
    assert calls[0]["kind"] == git_service.db_git.SESSION_KIND_GROUP_UPDATE
    assert calls[0]["context"] == {"prev_status": "waiting", "branch": "group/test"}

    still_unmerged = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=wt_path, text=True, capture_output=True, check=True,
    ).stdout.splitlines()
    assert still_unmerged == ["shared.txt"]

    subprocess.run(["git", "merge", "--abort"], cwd=wt_path, text=True, capture_output=True)


def test_update_from_base_creates_session_for_real_content_conflict(tmp_path, monkeypatch):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    base = tmp_path / "base"
    subprocess.run(["git", "clone", str(remote), str(base)], check=True, capture_output=True)
    _git(base, "config", "user.name", "FlowGate Test")
    _git(base, "config", "user.email", "flowgate@example.invalid")
    _git(base, "checkout", "-b", "main")
    (base / "conflict.txt").write_text("seed\n", encoding="utf-8")
    _git(base, "add", "conflict.txt")
    _git(base, "commit", "-m", "seed")
    _git(base, "push", "-u", "origin", "main")
    group = tmp_path / "group"
    _git(base, "worktree", "add", "-b", "group/test", str(group), "main")
    _git(group, "config", "user.name", "FlowGate Test")
    _git(group, "config", "user.email", "flowgate@example.invalid")
    (group / "conflict.txt").write_text("group\n", encoding="utf-8")
    _git(group, "commit", "-am", "group change")
    (base / "conflict.txt").write_text("base\n", encoding="utf-8")
    _git(base, "commit", "-am", "base change")
    _git(base, "push")

    group_id = "demo.default.0001"
    monkeypatch.setattr(git_service, "_finalize_context", lambda _gid: (
        {"base_branch": "main"}, {"branch": "group/test", "status": "waiting"},
        "demo", base, group,
    ))
    monkeypatch.setattr(git_service, "git_available", lambda: True)
    monkeypatch.setattr(git_service, "guard_base_free", lambda _project: None)
    monkeypatch.setattr(git_service, "_acquire_lock", lambda _project, _holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda _project, _holder: None)
    monkeypatch.setattr(git_service.db_git, "get_open_session_by_group", lambda _gid: None)
    created = {}

    def create_session(gid, files, *, kind, context):
        created.update(group_id=gid, files=files, kind=kind, context=context)
        return 73

    monkeypatch.setattr(git_service.db_git, "create_session", create_session)
    result = git_service.update_from_base(group_id)

    assert result["result"] == {
        "status": "conflict", "merge_id": 73, "conflict_files": ["conflict.txt"],
    }
    assert created["group_id"] == group_id
    assert created["files"] == result["result"]["conflict_files"]
    assert created["kind"] == git_service.db_git.SESSION_KIND_GROUP_UPDATE


def test_group_update_conflicts_go_through_facade_seam(tmp_path, monkeypatch):
    """T0028 §3.3 test B — update_from_base must read conflicts via the git_service
    facade attribute (`_gs._unmerged_paths`), not a name bound at finalize.py import
    time. Patching the facade attribute must change what the caller returns."""
    base_root, wt_path = _repo_pair(tmp_path)
    cfg = {"base_branch": "main"}
    state = {"branch": "group/test", "status": "none"}
    calls: list[dict] = []
    _patch_group_update(monkeypatch, cfg, state, "demo", base_root, wt_path, calls)
    monkeypatch.setattr(git_service, "_unmerged_paths", lambda _wt: ["sentinel.txt"])

    result = git_service.update_from_base("demo.default.0001")

    assert result["result"]["conflict_files"] == ["sentinel.txt"]
    assert calls[0]["files"] == ["sentinel.txt"]

    subprocess.run(["git", "merge", "--abort"], cwd=wt_path, text=True, capture_output=True)