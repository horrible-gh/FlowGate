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

    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(git_service, "get_storage_root", lambda: storage)
    monkeypatch.setattr(branch_service, "_branch_context", lambda project_id: ({"enabled": 1}, root, "main"))
    monkeypatch.setattr(git_service, "_base_root_of", lambda project_id: root)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda project_id: [])
    return root


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)


def test_tree_reads_ordinary_local_branch_without_touching_base_checkout(repo):
    _git(repo, "checkout", "-b", "test-branch")
    (repo / "feature.txt").write_text("feature content\n", encoding="utf-8")
    (repo / "sub").mkdir()
    (repo / "sub" / "nested.txt").write_text("nested\n", encoding="utf-8")
    _git(repo, "add", "feature.txt", "sub/nested.txt")
    _git(repo, "commit", "-m", "add feature")
    _git(repo, "checkout", "main")
    base_head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result = git_service.read_local_branch_tree("flowgate", "test-branch")

    assert result["ok"] is True
    data = result["data"]
    assert data["branch"] == "test-branch"
    assert data["read_only"] is True
    paths = {n["path"] for n in data["nodes"]}
    assert "feature.txt" in paths
    assert "sub/nested.txt" in paths
    assert "README.md" in paths
    # checkout-free: the base checkout's HEAD/branch and its working tree files
    # must be completely untouched by reading another branch's tree.
    assert _git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == base_head
    assert not (repo / "feature.txt").exists()


def test_tree_hides_dotfiles_and_db_files_like_the_group_explorer(repo):
    _git(repo, "checkout", "-b", "test-branch")
    (repo / ".secret").write_text("x\n", encoding="utf-8")
    (repo / "data.db").write_text("x\n", encoding="utf-8")
    (repo / "visible.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "add hidden and visible files")
    _git(repo, "checkout", "main")

    result = git_service.read_local_branch_tree("flowgate", "test-branch")
    paths = {n["path"] for n in result["data"]["nodes"]}
    assert "visible.txt" in paths
    assert ".secret" not in paths
    assert "data.db" not in paths


def test_tree_rejects_remote_only_and_missing_branch(repo):
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/remote-only", main_sha)

    with pytest.raises(GitServiceError) as caught:
        git_service.read_local_branch_tree("flowgate", "remote-only")
    assert caught.value.status == 409
    assert caught.value.code == "branch_read_remote_only"

    with pytest.raises(GitServiceError) as caught:
        git_service.read_local_branch_tree("flowgate", "does-not-exist")
    assert caught.value.status == 404
    assert caught.value.code == "branch_not_found"


def test_blob_reads_committed_content_and_rejects_path_traversal(repo):
    _git(repo, "checkout", "-b", "test-branch")
    (repo / "feature.txt").write_text("feature content\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "add feature")
    _git(repo, "checkout", "main")

    result = git_service.read_local_branch_blob("flowgate", "test-branch", "feature.txt")
    assert result["data"]["content"] == "feature content\n"
    assert result["data"]["binary"] is False
    assert result["data"]["branch"] == "test-branch"

    with pytest.raises(GitServiceError) as caught:
        git_service.read_local_branch_blob("flowgate", "test-branch", "../outside.txt")
    assert caught.value.status == 400
    assert caught.value.code == "invalid_path"

    with pytest.raises(GitServiceError) as caught:
        git_service.read_local_branch_blob("flowgate", "test-branch", "does-not-exist.txt")
    assert caught.value.status == 404


def test_blob_has_no_working_tree_fallback_for_an_uncommitted_file(repo):
    # A local branch has no live worktree behind it (unlike a group branch), so an
    # uncommitted change made directly to the shared base checkout while HEAD points
    # at another branch must never leak through the blob reader as that branch's
    # content — there is no untracked channel here at all (T0004 §3.2).
    _git(repo, "branch", "test-branch")
    (repo / "untracked.txt").write_text("not committed\n", encoding="utf-8")

    with pytest.raises(GitServiceError) as caught:
        git_service.read_local_branch_blob("flowgate", "test-branch", "untracked.txt")
    assert caught.value.status == 404


def test_blob_ref_pin_requires_a_real_commit(repo):
    _git(repo, "checkout", "-b", "test-branch")
    (repo / "feature.txt").write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "v1")
    commit1 = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")

    result = git_service.read_local_branch_blob("flowgate", "test-branch", "feature.txt", ref=commit1)
    assert result["data"]["commit"] == commit1

    with pytest.raises(GitServiceError) as caught:
        git_service.read_local_branch_blob("flowgate", "test-branch", "feature.txt", ref="not-a-sha")
    assert caught.value.status == 400
    assert caught.value.code == "invalid_ref"


def _client(*, is_admin=True):
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": is_admin}
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def full_repo(tmp_path, monkeypatch):
    """Same shape as test_git_branches_0594.py's `repo` fixture (create/delete need
    the lock/session/work-base guards those routes also go through), kept local to
    this file so the connected regression below does not depend on import order
    with a sibling test module."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    (root / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=root, check=True, capture_output=True)

    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(git_service, "get_storage_root", lambda: storage)
    monkeypatch.setattr(branch_service, "_branch_context", lambda project_id: ({"enabled": 1}, root, "main"))
    monkeypatch.setattr(git_service, "_base_root_of", lambda project_id: root)
    monkeypatch.setattr(git_service, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(git_service, "_acquire_lock", lambda project_id, holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda project_id, holder: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda project_id: [])
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])
    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "list_open_groups_by_work_base", lambda project_id, ref: [])
    return root


def test_connected_regression_branch_manager_create_to_explorer_tree_read(full_repo):
    """T0004 §7 "branch catalog -> explorer option -> real branch tree read 경계를
    연결" -- exercised through the REAL FastAPI routes (no service-function
    monkeypatching past the repo/lock/session plumbing above), against a REAL git
    repo on disk. Reproduces the exact boundary the review flagged as missing:
    Branch Manager create API -> list_branches(kind=local) -> the client's local
    branch selector option -> the actual checkout-free tree/blob read."""
    client = _client()

    # 1. Branch Manager's create call (File Explorer would be listening for
    #    fg:git_branches_changed at this same moment in the browser).
    create_res = client.post(
        "/api/v1/projects/flowgate/git/branches",
        json={"name": "test-branch-0615", "source_branch": "main"},
    )
    assert create_res.status_code == 200, create_res.text
    assert create_res.json()["ok"] is True

    # 2. The catalog GET File Explorer's selector is built from: the new branch
    #    must appear with kind=local (never internal_slot/remote_only), which is
    #    the exact condition FileExplorer.vue's localBranchOptions filter checks.
    catalog_res = client.get("/api/v1/projects/flowgate/git/branches")
    assert catalog_res.status_code == 200, catalog_res.text
    rows = {row["name"]: row for row in catalog_res.json()["branches"]}
    assert rows["test-branch-0615"]["kind"] == "local"

    # 3. Selecting that option in File Explorer calls exactly this tree route.
    tree_res = client.get(
        "/api/v1/projects/flowgate/git/branches/tree",
        params={"branch": "test-branch-0615"},
    )
    assert tree_res.status_code == 200, tree_res.text
    tree_data = tree_res.json()["data"]
    assert tree_data["read_only"] is True
    paths = {n["path"] for n in tree_data["nodes"]}
    assert "README.md" in paths

    # 4. Opening the file (double-click in File Explorer) calls the blob route,
    #    pinned to the tree's own commit -- the same ref MdViewer/TextViewer now
    #    forward explicitly (client/src/main/stores/explorer.ts fetchLocalBranchBlob).
    blob_res = client.get(
        "/api/v1/projects/flowgate/git/branches/blob",
        params={"branch": "test-branch-0615", "path": "README.md", "ref": tree_data["commit"]},
    )
    assert blob_res.status_code == 200, blob_res.text
    assert blob_res.json()["data"]["content"] == "base\n"

    # 5. Branch Manager's delete call -- File Explorer would fall back to base and
    #    the catalog GET below must no longer list it (selector removal).
    delete_res = client.delete("/api/v1/projects/flowgate/git/branches/test-branch-0615")
    assert delete_res.status_code == 200, delete_res.text
    assert delete_res.json()["deleted"] is True

    catalog_after = client.get("/api/v1/projects/flowgate/git/branches")
    names_after = {row["name"] for row in catalog_after.json()["branches"]}
    assert "test-branch-0615" not in names_after

    # 6. And the now-deleted branch's tree read 404s -- the exact response a stale
    #    File Explorer selection (if invalidation ever failed to fire) would hit.
    stale_tree_res = client.get(
        "/api/v1/projects/flowgate/git/branches/tree",
        params={"branch": "test-branch-0615"},
    )
    assert stale_tree_res.status_code == 404
    assert stale_tree_res.json()["error"]["code"] == "branch_not_found"


def test_local_branch_routes_rbac_and_error_envelope(monkeypatch):
    monkeypatch.setattr(
        git_routes.git_service, "read_local_branch_tree",
        lambda project_id, branch: {"ok": True, "data": {"branch": branch, "commit": "abc", "nodes": []}},
    )
    monkeypatch.setattr(
        git_routes.git_service, "read_local_branch_blob",
        lambda project_id, branch, path, ref=None: {
            "ok": True, "data": {"branch": branch, "path": path, "content": "x"},
        },
    )
    client = _client()
    tree_res = client.get("/api/v1/projects/flowgate/git/branches/tree", params={"branch": "test-branch"})
    assert tree_res.status_code == 200
    assert tree_res.json()["data"]["branch"] == "test-branch"

    blob_res = client.get(
        "/api/v1/projects/flowgate/git/branches/blob",
        params={"branch": "test-branch", "path": "a.txt"},
    )
    assert blob_res.status_code == 200
    assert blob_res.json()["data"]["path"] == "a.txt"

    def boom(project_id, branch):
        raise GitServiceError(404, "branch_not_found", "local branch was not found")

    monkeypatch.setattr(git_routes.git_service, "read_local_branch_tree", boom)
    response = client.get("/api/v1/projects/flowgate/git/branches/tree", params={"branch": "gone"})
    assert response.status_code == 404
    assert response.json() == {"ok": False, "error": {"code": "branch_not_found", "message": "local branch was not found"}}

    monkeypatch.setattr(
        "modules.flow_gate.rbac.decorators._permission_service.has_permission",
        lambda *args, **kwargs: False,
    )
    assert _client(is_admin=False).get(
        "/api/v1/projects/flowgate/git/branches/tree", params={"branch": "x"},
    ).status_code == 403
    assert _client(is_admin=False).get(
        "/api/v1/projects/flowgate/git/branches/blob", params={"branch": "x", "path": "a.txt"},
    ).status_code == 403


def test_tree_and_blob_work_for_a_branch_name_containing_a_slash(repo):
    _git(repo, "checkout", "-b", "feature/nested")
    (repo / "x.txt").write_text("nested branch content\n", encoding="utf-8")
    _git(repo, "add", "x.txt")
    _git(repo, "commit", "-m", "nested branch commit")
    _git(repo, "checkout", "main")

    tree = git_service.read_local_branch_tree("flowgate", "feature/nested")
    assert tree["data"]["branch"] == "feature/nested"
    assert {n["path"] for n in tree["data"]["nodes"]} >= {"x.txt", "README.md"}

    blob = git_service.read_local_branch_blob("flowgate", "feature/nested", "x.txt")
    assert blob["data"]["content"] == "nested branch content\n"


def test_tree_and_blob_reject_a_branch_name_that_is_not_a_real_ref(repo):
    # Argv-based subprocess calls (never a shell string), so an arbitrary attacker
    # string cannot inject a command — it simply matches no real ref and 404s.
    hostile = "main; rm -rf /tmp/x"
    with pytest.raises(GitServiceError) as caught:
        git_service.read_local_branch_tree("flowgate", hostile)
    assert caught.value.status == 404
    assert caught.value.code == "branch_not_found"

    with pytest.raises(GitServiceError) as caught:
        git_service.read_local_branch_blob("flowgate", hostile, "README.md")
    assert caught.value.status == 404
    assert caught.value.code == "branch_not_found"


def test_route_accepts_a_slash_containing_branch_via_query_param(monkeypatch):
    seen = {}

    def fake_tree(project_id, branch):
        seen["tree_branch"] = branch
        return {"ok": True, "data": {"branch": branch, "commit": "abc", "nodes": []}}

    def fake_blob(project_id, branch, path, ref=None):
        seen["blob_branch"] = branch
        return {"ok": True, "data": {"branch": branch, "path": path, "content": "x"}}

    monkeypatch.setattr(git_routes.git_service, "read_local_branch_tree", fake_tree)
    monkeypatch.setattr(git_routes.git_service, "read_local_branch_blob", fake_blob)
    client = _client()

    res = client.get("/api/v1/projects/flowgate/git/branches/tree", params={"branch": "feature/nested"})
    assert res.status_code == 200
    assert seen["tree_branch"] == "feature/nested"

    res = client.get(
        "/api/v1/projects/flowgate/git/branches/blob",
        params={"branch": "feature/nested", "path": "x.txt"},
    )
    assert res.status_code == 200
    assert seen["blob_branch"] == "feature/nested"
