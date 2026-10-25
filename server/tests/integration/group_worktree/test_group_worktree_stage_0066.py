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


@pytest.mark.parametrize("error,reason", [
    (tr2_file_policy.Tr2FilePolicyError(tr2_file_policy.TR_HISTORY_RECOVERY_REQUIRED),
     "tr_history_recovery_required"),
    (tr2_file_policy.Tr2OwnershipInvariantError("private ledger"), "ownership_invariant"),
    (RuntimeError("private ledger"), "ownership_read_failed"),
])
def test_ownership_unavailable_blocks_stage_but_never_unstage(repo, monkeypatch, error, reason):
    put(repo.root, "a.py", "changed\n")
    put(repo.root, SECRET, "changed\n")
    git(repo.root, "add", "a.py", SECRET)
    def unavailable(_):
        raise error
    monkeypatch.setattr(tr2_file_policy, "managed_paths", unavailable)
    view = status(repo)
    assert view["ownership"] == {"available": False, "reason": reason}
    assert files(view)["a.py"]["class"] == "unverified"
    assert files(view)["a.py"]["can_unstage"] and not files(view)["a.py"]["can_stage"]
    assert SECRET not in json.dumps(view)
    assert "unstage" not in view["blockers"][-1]["operations"]
    before = index(repo)
    for body in ({"all": True}, {"paths": ["a.py"]}):
        err = write(repo, "stage", body, 409)
        assert err["code"] == ("tr_history_recovery_required" if reason == "tr_history_recovery_required"
                               else "ownership_unavailable")
        assert index(repo) == before
    selected = write(repo, "unstage", {"paths": ["a.py"]})
    assert not selected["status"]["ownership"]["available"]
    result = write(repo, "unstage", {"all": True})
    assert result["applied_hidden_count"] == 1 and result["status"]["counts"]["staged"] == 0


def test_post_success_refresh_failure_is_still_success(repo, monkeypatch):
    put(repo.root, "a.py", "changed\n")
    def fail(_):
        raise GitServiceError(500, "git_error", "cannot refresh")
    monkeypatch.setattr(m, "worktree_status", fail)
    result = write(repo, "stage", {"paths": ["a.py"]})
    assert result["applied"] == ["a.py"] and result["status"] is None
    assert result["status_error"] == "git_error"
    assert git(repo.root, "diff", "--cached", "--name-only").strip() == "a.py"


@pytest.mark.parametrize("code", ["run_already_active", "group_lease_active", "branch_merge_claim_active"])
def test_technical_guards_and_no_partial_mutation(repo, monkeypatch, code):
    put(repo.root, "a.py", "changed\n")
    def block(_):
        raise GitServiceError(409, code, "busy")
    module, attr = ((m.lock, "guard_group_branch_merge_free") if code == "branch_merge_claim_active"
                    else (m.finalize, "_guard_group_update_ai_idle"))
    monkeypatch.setattr(module, attr, block)
    before = index(repo)
    assert status(repo)["blockers"][0]["code"] == code
    assert write(repo, "stage", {"all": True}, 409)["code"] == code
    assert write(repo, "unstage", {"all": True}, 409)["code"] == code
    assert index(repo) == before


def test_guard_is_rechecked_after_lock(repo, monkeypatch):
    put(repo.root, "a.py", "changed\n")
    count = 0
    def race(_):
        nonlocal count
        count += 1
        if count > 1:
            raise GitServiceError(409, "group_lease_active", "new lease")
    monkeypatch.setattr(m.finalize, "_guard_group_update_ai_idle", race)
    before = index(repo)
    assert write(repo, "stage", {"all": True}, 409)["code"] == "group_lease_active"
    assert index(repo) == before and repo.calls[-1][0] == "release"


def test_slot_branch_owner_checked_again_after_lock(repo, monkeypatch):
    put(repo.root, "a.py", "changed\n")
    original = m.lock_manager.acquire_group
    def changed_owner(*args, **kwargs):
        acquired = original(*args, **kwargs)
        repo.state["branch"] = "some-other-group"
        return acquired
    monkeypatch.setattr(m.lock_manager, "acquire_group", changed_owner)
    before = index(repo)
    assert write(repo, "stage", {"all": True}, 409)["code"] == "invalid_state"
    assert index(repo) == before


@pytest.mark.parametrize("state", ["merging", "conflict", "merged", "pushed"])
def test_state_gate(repo, state):
    repo.state["status"] = state
    write(repo, "unstage", {"all": True}, 409)


def test_pending_approval_does_not_block(repo, monkeypatch):
    put(repo.root, "a.py", "changed\n")
    repo.state["status"] = "awaiting_choice"
    monkeypatch.setattr(m.approval_intent, "group_is_final_approval_bound", lambda _: True)
    result = write(repo, "stage", {"all": True})
    assert result["status"]["git_state"]["final_approval_bound"] is True


def test_missing_slot_and_git_disabled_are_readable(repo):
    repo.cfg["enabled"] = False
    assert status(repo)["worktree_available"] is False
    write(repo, "stage", {"all": True}, 409)
    repo.cfg["enabled"] = True
    repo.state["worktree_registered"] = 0
    assert status(repo)["worktree_available"] is False


def test_rbac_and_body_contract(repo, monkeypatch):
    calls = []
    monkeypatch.setattr(git_routes, "_has_permission", lambda user, perm, pid: calls.append((perm, pid)) or False)
    assert repo.client.get(URL + "/status").status_code == 403
    assert repo.client.post(URL + "/stage", json={"all": True}).status_code == 403
    assert calls == [("project.settings.read", "p_0669b"), ("project.settings.edit", "p_0669b")]
    monkeypatch.setattr(git_routes, "_has_permission", lambda *args: True)
    for payload in ({}, {"paths": []}, {"all": True, "paths": []},
                    {"all": "true"}, {"all": True, "force": True}):
        assert repo.client.post(URL + "/stage", json=payload).status_code == 422


def test_snapshot_remains_immutable_after_index_and_worktree_change(repo):
    put(repo.root, "a.py", "snapshot\n")
    git(repo.root, "add", "a.py")
    snapshot = m.read_index_snapshot(G)
    put(repo.root, "a.py", "later\n")
    git(repo.root, "add", "a.py")
    head, changes = m.snapshot_changes(snapshot)
    assert changes == {"a.py": "M"}
    oid = next(oid for path, mode, oid, stage in snapshot.entries if path == "a.py")
    assert snapshot.object_git(["cat-file", "blob", oid]) == "snapshot\n"
    assert snapshot.index_token != m.read_index_snapshot(G).index_token


def test_snapshot_retries_and_reports_continuous_race(repo, monkeypatch):
    original = m._read_index_once
    count = 0
    def race(root):
        nonlocal count
        snapshot = original(root)
        count += 1
        put(root, "a.py", f"changed {count}\n")
        git(root, "add", "a.py")
        return snapshot
    monkeypatch.setattr(m, "_read_index_once", race)
    with pytest.raises(GitServiceError) as caught:
        m.read_index_snapshot(G, attempts=2)
    assert caught.value.code == "worktree_changed" and count == 4


def test_unmerged_entries_can_be_unstaged_without_touching_files(repo):
    oid = git(repo.root, "rev-parse", "HEAD:a.py").strip()
    payload = f"0 {'0'*40}\ta.py\0" + "".join(f"100644 {oid} {stage}\ta.py\0" for stage in (1, 2, 3))
    proc = subprocess.run(["git", "update-index", "-z", "--index-info"], cwd=repo.root,
                          input=payload.encode("utf-8"), capture_output=True)
    assert proc.returncode == 0, proc.stderr
    view = status(repo)
    assert files(view)["a.py"]["staged"] == "U"
    assert files(view)["a.py"]["unsupported"] == "unmerged"
    before = (repo.root / "a.py").read_bytes()
    write(repo, "stage", {"paths": ["a.py"]}, 409)
    write(repo, "unstage", {"paths": ["a.py"]})
    assert not git(repo.root, "ls-files", "--unmerged") and (repo.root / "a.py").read_bytes() == before


def test_staged_type_change_rejected_but_unstage_keeps_working_file(repo):
    # Insert a symlink blob directly; no host symlink privilege is required.
    oid = git(repo.root, "rev-parse", "HEAD:a.py").strip()
    git(repo.root, "update-index", "--cacheinfo", "120000", oid, "a.py")
    row = files(status(repo))["a.py"]
    assert row["staged"] == "T" and row["unsupported"] == "type_change"
    before = (repo.root / "a.py").read_bytes()
    write(repo, "stage", {"paths": ["a.py"]}, 409)
    write(repo, "unstage", {"paths": ["a.py"]})
    assert (repo.root / "a.py").read_bytes() == before


@pytest.mark.parametrize("frozen", [False, True])
def test_real_mutex_and_freeze_reject_without_index_changes(lock_store, repo, monkeypatch, frozen):
    put(repo.root, "a.py", "changed\n")
    monkeypatch.setattr(m.lock_manager, "acquire_group", REAL_ACQUIRE)
    monkeypatch.setattr(m.lock_manager, "release", REAL_RELEASE)
    monkeypatch.setattr(m.lock_manager, "still_held", REAL_STILL_HELD)
    monkeypatch.setattr(m.lock_manager, "wait_budget", lambda *args: 0)
    held = None
    if frozen:
        monkeypatch.setattr(m.operation_job, "get_group_freeze_claim",
                            lambda _: {"job_id": "approval", "state": "freezing", "at": "now"})
    else:
        held = REAL_ACQUIRE("p_0669b", G, holder_kind="source_mutation", mode="job")
        assert held[0].ok
    before = index(repo)
    try:
        err = write(repo, "stage", {"all": True}, 409)
        assert err["code"] == "git_busy"
        assert err["details"]["reason_code"] == ("group_frozen_for_approval" if frozen else "lock_busy")
        assert index(repo) == before
    finally:
        if held:
            REAL_RELEASE(held[1], held[0].lock_key)


def test_real_scope_floor_does_not_follow_base_tip(repo, monkeypatch):
    monkeypatch.setattr(m.scope_base, "resolve_scope_floor", REAL_FLOOR)
    monkeypatch.setattr(gs, "resolve_group_work_base_ref", lambda *args, **kwargs: "base")
    monkeypatch.setattr(m.scope_base, "_ledger_commits", lambda _: [])
    put(repo.root, "a.py", "first\n")
    git(repo.root, "commit", "-am", "first")
    git(repo.root, "branch", "base")
    put(repo.root, "b.py", "second\n")
    git(repo.root, "commit", "-am", "second")
    view = status(repo)
    assert view["committed"]["commit_count"] == 2 and view["floor"]["scope_base_sha"] == repo.floor
    repo.state["work_base_state"] = "unverified"
    assert status(repo)["floor"]["error"]["code"] == "group_work_base_unverified"
    repo.state["work_base_state"] = "verified"
    repo.state["work_base_sha"] = "0" * 40
    assert status(repo)["floor"]["error"]["code"] == "group_work_base_diverged"


def test_snapshot_objects_survive_linked_worktree_removal(repo, monkeypatch):
    linked = repo.root.parent / "linked"
    git(repo.root, "worktree", "add", "-b", "linked", str(linked), "HEAD")
    repo.state["branch"] = "linked"
    monkeypatch.setattr(gs, "effective_src_root_ex", lambda *args: (linked, "worktree"))
    put(linked, "a.py", "frozen object\n")
    git(linked, "add", "a.py")
    snapshot = m.read_index_snapshot(G)
    git(repo.root, "worktree", "remove", "--force", str(linked))
    _, changed = m.snapshot_changes(snapshot)
    assert changed == {"a.py": "M"}
    oid = next(oid for path, mode, oid, stage in snapshot.entries if path == "a.py")
    assert snapshot.object_git(["cat-file", "blob", oid]) == "frozen object\n"


def test_git_diagnostic_never_echoes_hidden_path(repo, monkeypatch):
    original = gs._run_git
    def failing(args, **kwargs):
        if "add" in args:
            return subprocess.CompletedProcess(args, 1, "", "failure: " + SECRET)
        return original(args, **kwargs)
    put(repo.root, "a.py", "changed\n")
    monkeypatch.setattr(gs, "_run_git", failing)
    error = write(repo, "stage", {"all": True}, 500)
    assert error["code"] == "git_error" and SECRET not in json.dumps(error)


def test_open_session_blocks_stage_and_unstage(repo, monkeypatch):
    monkeypatch.setattr(gs.db_git, "get_open_session_by_group", lambda _: {"session_kind": "tr_commit"})
    assert status(repo)["blockers"][0]["code"] == "session_open"
    for op in ("stage", "unstage"):
        assert write(repo, op, {"all": True}, 409)["code"] == "invalid_state"


def test_floor_metadata_is_filtered_for_hidden_paths(repo, monkeypatch):
    def bad(*args, **kwargs):
        raise GitServiceError(409, "group_work_base_unverified", "Floor unavailable",
                              details={"reason": "unverified", "recovery_details": {
                                  "path": SECRET, "diagnostic": "failed on server/.unknown-secret"}})
    monkeypatch.setattr(m.scope_base, "resolve_scope_floor", bad)
    view = status(repo)
    rendered = json.dumps(view)
    assert SECRET not in rendered and "server/.unknown-secret" not in rendered
    assert view["floor"]["error"]["details"]["recovery_details"]["path"] == "<hidden>"


@pytest.mark.parametrize("reason", ["lock_busy", "group_frozen_for_approval"])
def test_lock_outcome_is_reported_without_mutation(repo, monkeypatch, reason):
    put(repo.root, "a.py", "changed\n")
    outcome = SimpleNamespace(ok=False, lock_key="G:test", kind=(
        m.lock_manager.BUSY if reason == "lock_busy" else m.lock_manager.GROUP_FROZEN),
        cause=reason, blocker={"job_id": "approval"} if reason != "lock_busy" else None)
    monkeypatch.setattr(m.lock_manager, "acquire_group", lambda *args, **kwargs: (outcome, object()))
    before = index(repo)
    error = write(repo, "stage", {"all": True}, 409)
    assert error["code"] == "git_busy" and error["details"]["reason_code"] == reason
    assert index(repo) == before


def test_detached_slot_is_unavailable(repo):
    git(repo.root, "checkout", "--detach", "HEAD")
    assert status(repo)["worktree_available"] is False
    assert write(repo, "stage", {"all": True}, 409)["code"] == "invalid_state"


def test_hidden_request_rejected_before_lookup_or_lock(repo, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("hidden request reached worktree/ownership/lock lookup")
    monkeypatch.setattr(m, "_context", unexpected)
    for endpoint, reason in (("stage", "hidden"), ("unstage", "hidden_use_unstage_all")):
        error = write(repo, endpoint, {"paths": ["server/.absent"]}, 409)
        assert error["details"] == {"reason": reason}


@pytest.mark.parametrize("endpoint", ["stage", "unstage"])
@pytest.mark.parametrize("body", [
    {"paths": ["a.py"] * 4097},
    {"paths": ["a" * 4097]},
    {"paths": ["한" * 1366]},
])
def test_request_path_limits_preserve_index(repo, endpoint, body):
    before = index(repo)
    write(repo, endpoint, body, 422)
    assert index(repo) == before and not repo.calls


@pytest.mark.parametrize("endpoint", ["stage", "unstage"])
@pytest.mark.parametrize("chunked", [False, True])
def test_request_body_limit_before_json_and_git(repo, endpoint, chunked):
    before = index(repo)
    raw = b" " * (1024 * 1024 + 1)
    content = iter([raw[:700000], raw[700000:]]) if chunked else raw
    response = repo.client.post(URL + "/" + endpoint, content=content,
                                headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert index(repo) == before and not repo.calls


def test_request_body_exact_limit_is_accepted(repo):
    put(repo.root, "a.py", "changed\n")
    raw = b'{"paths":["a.py"]}'
    response = repo.client.post(URL + "/stage", content=raw + b" " * (1024 * 1024 - len(raw)),
                                headers={"Content-Type": "application/json"})
    assert response.status_code == 200, response.text
