"""0066 E1--E3: real Git index/files and HTTP contract, mocked application metadata."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import git_routes
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.services import git_service as gs, tr2_file_policy
from modules.flow_gate.services.git import worktree_manual as m, manual_path_policy as policy
from modules.flow_gate.services.git.credentials import GitServiceError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary unavailable")
from tests.modules.flow_gate.test_git_lock_manager_0669 import store as lock_store, db_path  # shared real-lock fixture

REAL_ACQUIRE = m.lock_manager.acquire_group
REAL_RELEASE = m.lock_manager.release
REAL_STILL_HELD = m.lock_manager.still_held
REAL_FLOOR = m.scope_base.resolve_scope_floor

G = "p_0669b.test.0001"
URL = "/api/v1/groups/" + G + "/git/worktree"
SECRET = "server/.env.local"


def git(root, *args):
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stderr
    return result.stdout


def put(root, path, text):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "group")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.test")
    git(root, "config", "core.autocrlf", "false")
    for path in ("a.py", "b.py", "managed.py", SECRET, "old.log"):
        put(root, path, "original\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    floor = git(root, "rev-parse", "HEAD").strip()
    state = {"project_id": "p_0669b", "branch": "group", "worktree_registered": 1,
             "status": "none", "work_base_ref": "base", "work_base_sha": floor,
             "work_base_state": "verified"}
    cfg = {"enabled": True}
    owner = {"managed.py", SECRET}
    monkeypatch.setenv("FLOWGATE_TEST_SCRATCH", str(tmp_path / "scratch"))
    monkeypatch.setattr(gs.db_git, "get_config", lambda _: cfg)
    monkeypatch.setattr(gs.db_git, "get_state", lambda _: state)
    monkeypatch.setattr(gs.db_git, "get_open_session_by_group", lambda _: None)
    monkeypatch.setattr(gs, "effective_src_root_ex", lambda *args: (root, "worktree"))
    monkeypatch.setattr(gs, "_group_root_wf_done", lambda _: False)
    monkeypatch.setattr(m.approval_intent, "group_is_final_approval_bound", lambda _: False)
    monkeypatch.setattr(tr2_file_policy, "managed_paths", lambda _: owner)
    monkeypatch.setattr(m.lock, "guard_group_branch_merge_free", lambda _: None)
    monkeypatch.setattr(m.finalize, "_guard_group_update_ai_idle", lambda _: None)
    monkeypatch.setattr(m.operation_job, "get_group_freeze_claim", lambda _: None)
    monkeypatch.setattr(m.scope_base, "resolve_scope_floor", lambda *args, **kwargs: {
        "floor_sha": floor, "work_base_ref": "base", "work_base_sha": floor,
        "work_base_sync_sha": None, "work_base_state": "verified"})
    calls = []
    def acquire(*args, **kwargs):
        calls.append(("acquire", kwargs))
        return SimpleNamespace(ok=True, lock_key="G:test"), object()
    monkeypatch.setattr(m.lock_manager, "acquire_group", acquire)
    monkeypatch.setattr(m.lock_manager, "still_held", lambda *args: True)
    monkeypatch.setattr(m.lock_manager, "release", lambda *args: calls.append(("release", {})))
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    monkeypatch.setattr(git_routes, "_has_permission", lambda *args: True)
    return SimpleNamespace(root=root, state=state, cfg=cfg, owner=owner, floor=floor,
                           calls=calls, client=TestClient(app, raise_server_exceptions=False))


def status(repo):
    response = repo.client.get(URL + "/status")
    assert response.status_code == 200, response.text
    return response.json()["result"]


def write(repo, endpoint, body, expected=200):
    response = repo.client.post(URL + "/" + endpoint, json=body)
    assert response.status_code == expected, response.text
    return response.json()["result" if expected == 200 else "error"]


def index(repo):
    return git(repo.root, "ls-files", "--stage", "-z")


def files(view):
    return {r["path"]: r for r in view["files"]}


def test_partial_stage_delete_and_untracked(repo):
    put(repo.root, "a.py", "staged\n")
    git(repo.root, "add", "a.py")
    put(repo.root, "a.py", "working\n")
    (repo.root / "b.py").unlink()
    put(repo.root, "new.py", "new\n")
    rows = files(status(repo))
    assert (rows["a.py"]["staged"], rows["a.py"]["unstaged"]) == ("M", "M")
    assert rows["b.py"]["unstaged"] == "D" and rows["new.py"]["untracked"]
    before = (repo.root / "a.py").read_bytes()
    result = write(repo, "unstage", {"paths": ["a.py"]})
    assert result["applied"] == ["a.py"] and (repo.root / "a.py").read_bytes() == before
    assert repo.state["status"] == "none"
    assert repo.calls[0][1]["holder_kind"] == "source_mutation"
    assert repo.calls[-1][0] == "release"


@pytest.mark.parametrize("code", m.FLOOR_ERRORS)
def test_floor_error_keeps_index_operations_available(repo, monkeypatch, code):
    def bad(*args, **kwargs):
        raise GitServiceError(409, code, "Floor unavailable", details={"reason": "unverified"})
    monkeypatch.setattr(m.scope_base, "resolve_scope_floor", bad)
    put(repo.root, "a.py", "changed\n")
    view = status(repo)
    assert view["committed"] is None and view["floor"]["error"]["code"] == code
    assert files(view)["a.py"]["can_stage"]
    assert write(repo, "stage", {"paths": ["a.py"]})["status"]["floor"]["error"]["code"] == code


def test_commit_interval_uses_recorded_floor(repo):
    put(repo.root, "a.py", "one\n")
    git(repo.root, "commit", "-am", "one")
    git(repo.root, "branch", "base")  # branch tip already contains first group commit
    put(repo.root, "b.py", "two\n")
    git(repo.root, "commit", "-am", "two")
    view = status(repo)
    assert view["committed"] == {"commit_count": 2, "changed_file_count": 2}
    assert view["floor"]["scope_base_sha"] == repo.floor


def test_selected_stage_is_all_or_nothing_and_all_counts_protection(repo):
    for path in ("a.py", "managed.py", SECRET, "new.log", "old.log"):
        put(repo.root, path, "changed\n")
    before = index(repo)
    err = write(repo, "stage", {"paths": ["a.py", "managed.py"]}, 409)
    assert err["code"] == "path_protected" and index(repo) == before
    result = write(repo, "stage", {"all": True})
    assert set(result["applied"]) == {"a.py", "old.log"}
    assert result["skipped"] == {"managed": 1, "hidden": 1, "excluded_new": 1, "unsupported": 0}
    assert SECRET not in json.dumps(result)
    assert result["status"]["counts"]["managed_dirty"] == 1


def test_external_new_excluded_is_not_existing(repo):
    put(repo.root, "new.log", "new\n")
    git(repo.root, "add", "new.log")
    row = files(status(repo))["new.log"]
    assert row["in_head"] is False and row["protected_reason"] == "excluded_new"
    assert row["can_unstage"] and not row["can_stage"]
    write(repo, "stage", {"paths": ["new.log"]}, 409)
    result = write(repo, "unstage", {"paths": ["new.log"]})
    assert "new.log" not in files(result["status"])
    assert result["status"]["counts"]["excluded_new_untracked"] == 1


def test_hidden_owned_and_unowned_never_leak_and_all_unstage_recovers(repo):
    put(repo.root, SECRET, "changed\n")
    git(repo.root, "add", SECRET)
    view = status(repo)
    assert view["counts"]["staged"] == 1 and view["counts"]["hidden"] == 1
    assert view["counts"]["managed_dirty"] == 0 and view["files"] == []
    for path in (SECRET, "server/.not-existing"):
        err = write(repo, "unstage", {"paths": [path]}, 409)
        assert err == {"code": "path_protected", "message": "Protected file path",
                       "details": {"reason": "hidden_use_unstage_all"}}
    result = write(repo, "unstage", {"all": True})
    assert result["applied"] == [] and result["applied_hidden_count"] == 1
    assert SECRET not in json.dumps(result)
    assert (repo.root / SECRET).read_text() == "changed\n"


@pytest.mark.parametrize("source,target,protected", [
    ("a.py", "renamed.py", None), ("managed.py", "renamed.py", "managed.py"),
    (SECRET, "renamed.py", SECRET), ("a.py", "server/.renamed", "server/.renamed"),
    ("a.py", "managed.py", "managed.py"),
])
def test_rename_is_two_independent_paths(repo, source, target, protected):
    (repo.root / target).parent.mkdir(parents=True, exist_ok=True)
    if (repo.root / target).exists():
        (repo.root / target).unlink()
        git(repo.root, "rm", "--cached", target)
        git(repo.root, "commit", "-m", "remove destination")
    (repo.root / source).rename(repo.root / target)
    git(repo.root, "add", "-A")
    view = status(repo)
    rows = files(view)
    for path, letter in ((source, "D"), (target, "A")):
        if policy.hidden(path):
            assert path not in json.dumps(view)
        else:
            assert rows[path]["staged"] == letter
            assert "old_path" not in rows[path]
    if protected:
        before = index(repo)
        write(repo, "stage", {"paths": [source, target]}, 409)
        assert index(repo) == before


def test_copy_is_only_addition(repo):
    shutil.copyfile(repo.root / "a.py", repo.root / "copy.py")
    git(repo.root, "add", "copy.py")
    assert [(r["path"], r["staged"]) for r in status(repo)["files"]] == [("copy.py", "A")]


@pytest.mark.parametrize("path", ["*", ":(glob)**", "server", "server/*", "../a", "/a", "C:/a"])
def test_pattern_folder_and_traversal_cannot_stage_secret(repo, path):
    put(repo.root, SECRET, "secret changed\n")
    put(repo.root, "a.py", "changed\n")
    before = index(repo)
    write(repo, "stage", {"paths": [path]}, 422 if path in ("../a", "/a", "C:/a") else 409)
    assert index(repo) == before


def test_literal_brackets_and_option_filename(repo):
    for path in ("[a].py", "-option.py"):
        put(repo.root, path, "new\n")
    put(repo.root, "a.py", "must stay unstaged\n")
    write(repo, "stage", {"paths": ["[a].py", "-option.py"]})
    rows = files(status(repo))
    assert rows["[a].py"]["staged"] == "A" and rows["-option.py"]["staged"] == "A"
    assert rows["a.py"]["staged"] is None


def test_large_path_list_has_no_truncation_or_argv_limit(repo):
    for n in range(137):
        put(repo.root, ("folder_" + "x" * 60) + f"/file_{n:03d}.py", "new\n")
    put(repo.root, SECRET, "protected\n")
    result = write(repo, "stage", {"all": True})
    assert len(result["applied"]) == 137 and result["status"]["counts"]["staged"] == 137
    assert len(result["status"]["files"]) == 137 and result["skipped"]["hidden"] == 1
    unstage = write(repo, "unstage", {"paths": result["applied"]})
    assert len(unstage["applied"]) == 137 and unstage["status"]["counts"]["staged"] == 0


def test_content_change_invalidates_worktree_token_without_changing_index(repo):
    put(repo.root, "a.py", "one\n")
    view = status(repo)
    put(repo.root, "a.py", "two\n")
    changed = status(repo)
    assert view["index_token"] == changed["index_token"]
    assert view["worktree_token"] != changed["worktree_token"]
    before = index(repo)
    err = write(repo, "stage", {"paths": ["a.py"], "worktree_token": view["worktree_token"]}, 409)
    assert err["code"] == "worktree_changed" and index(repo) == before
