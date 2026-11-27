"""Independently selectable scenarios; shared fixtures remain in test_group_worktree_commit_revert_0066.py."""
from __future__ import annotations

from tests.regression.group_worktree.test_group_worktree_commit_revert_0066 import (
    G,
    GitServiceError,
    MutationPathAliasError,
    REAL_ACQUIRE,
    REAL_RELEASE,
    REAL_STILL_HELD,
    SECRET,
    URL,
    db_path,
    deepcopy,
    git,
    git_routes,
    gs,
    index,
    json,
    lock_store,
    m,
    make_unsupported,
    os,
    prepare,
    put,
    pytest,
    pytestmark,
    repo,
    shutil,
    snapshot,
    stage_repo,
    status,
    subprocess,
    tr2,
    write,
)

@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
@pytest.mark.parametrize("state", ["merging", "conflict", "merged", "pushed"])
def test_bad_state_and_open_session_block_mutation(repo, operation, state, monkeypatch):
    body = prepare(repo, operation)
    before = snapshot(repo)
    repo.state["status"] = state
    assert write(repo, operation, body, 409)["code"] == "invalid_state"
    repo.state["status"] = "none"
    monkeypatch.setattr(gs.db_git, "get_open_session_by_group", lambda _: {"session_kind": "group_update"})
    assert write(repo, operation, body, 409)["code"] == "invalid_state"
    assert snapshot(repo) == before
@pytest.mark.parametrize("operation", ["revert", "remove"])
def test_token_and_path_validation_and_directory_safety(repo, operation):
    body = prepare(repo, operation)
    before = snapshot(repo)
    assert write(repo, operation, {**body, "worktree_token": "old"}, 409)["code"] == "worktree_changed"
    for path in ("../a.py", "/a.py", "C:/a.py", "a/../a.py", "a\0.py"):
        assert write(repo, operation, {"paths": [path]}, 422)["code"] == "invalid_request"
    assert snapshot(repo) == before
    error = write(repo, operation, {"paths": ["server"]}, 422)
    assert error["code"] == "invalid_request" and (repo.root / SECRET).exists()
    # TR2 rejects the colon in pathspec magic; wildcard-only paths stay literal.
    error = write(repo, operation, {"paths": ["*.py", ":(glob)*"]}, 422)
    assert error["code"] == "invalid_request" and snapshot(repo) == before
    if operation == "remove":
        assert write(repo, operation, {"paths": ["*.py"]})["results"][0]["result"] == "not_changed"
    else:
        assert write(repo, operation, {"paths": ["*.py"]}, 422)["code"] == "new_path_use_remove"
    assert snapshot(repo) == before
@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
def test_rbac_strict_body_and_request_size(repo, operation, monkeypatch):
    body = prepare(repo, operation)
    before = snapshot(repo)
    permissions = []
    monkeypatch.setattr(git_routes, "_has_permission",
                        lambda user, perm, pid: permissions.append((perm, pid)) or False)
    assert repo.client.post(URL + "/" + operation, json=body).status_code == 403
    assert permissions == [("project.git.write", "p_0669b")]
    monkeypatch.setattr(git_routes, "_has_permission", lambda *args: True)
    invalid = [{}, {**body, "all": True}, {**body, "force": True}]
    invalid += [{"message": 3}] if operation == "commit" else [{"paths": []}, {"paths": [3]}]
    for payload in invalid:
        assert repo.client.post(URL + "/" + operation, json=payload).status_code == 422
    response = repo.client.post(URL + "/" + operation, content=b" " * (1024 * 1024 + 1),
                                headers={"content-type": "application/json"})
    assert response.status_code == 413 and response.json()["error"]["code"] == "request_too_large"
    assert snapshot(repo) == before
@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
def test_status_refresh_failure_does_not_erase_mutation_success(repo, operation, monkeypatch):
    body = prepare(repo, operation)
    def unavailable(_):
        raise GitServiceError(500, "git_error", "hidden " + SECRET)
    monkeypatch.setattr(m, "worktree_status", unavailable)
    result = write(repo, operation, body)
    assert result["status"] is None and result["status_error"] == "git_error"
    assert SECRET not in json.dumps(result)
    if operation == "commit":
        assert result["committed_count"] == 1 and git(repo.root, "rev-parse", "HEAD").strip() != repo.floor
    else:
        assert result["results"][0]["result"] == ("removed" if operation == "remove" else "reverted")
def test_git_commit_failure_does_not_echo_secret_or_unstage(repo, monkeypatch):
    body = prepare(repo, "commit")
    before = snapshot(repo)
    run = gs._run_git
    def fail(args, **kwargs):
        if "commit" in args:
            return subprocess.CompletedProcess(args, 1, "", "failure: " + SECRET)
        return run(args, **kwargs)
    monkeypatch.setattr(gs, "_run_git", fail)
    error = write(repo, "commit", body, 500)
    assert error["code"] == "git_error" and SECRET not in json.dumps(error)
    assert snapshot(repo) == before
@pytest.mark.skipif(os.name != "nt", reason="Windows case aliases")
@pytest.mark.parametrize("operation", ["revert", "remove"])
@pytest.mark.parametrize("path", ["MANAGED.py", "A.py"])
def test_windows_case_alias_cannot_bypass_head_or_managed_protection(repo, operation, path):
    prepare(repo, "remove")
    put(repo.root, "a.py", "keep\n")
    before = snapshot(repo)
    error = write(repo, operation, {"paths": [path]}, 409)
    assert error["code"] == "path_protected"
    assert error["details"]["cause_code"] == "SOURCE_PATH_ALIAS_NOT_ALLOWED"
    assert snapshot(repo) == before
def test_tr_approval_still_absorbs_all_edits_after_partial_stage(repo, monkeypatch):
    from modules.flow_gate.services.git import commit as automatic
    monkeypatch.setattr(gs, "get_branch_merge_group_claim", lambda _: None)
    monkeypatch.setattr(gs, "_project_name", lambda _: "test")
    monkeypatch.setattr(gs, "src_root", lambda *args: repo.root)
    monkeypatch.setattr(automatic, "_acquire_group_lock", lambda *args, **kwargs: "test")
    monkeypatch.setattr(automatic, "_release_group_lock", lambda *_: None)
    put(repo.root, "a.py", "staged\n")
    git(repo.root, "add", "a.py")
    for path in ("a.py", "b.py", SECRET, "new.py"):
        put(repo.root, path, "whole worktree\n")
    result = automatic.create_tr_commit(G, "chore: approved TR")
    assert result["committed"] is True
    for path in ("a.py", "b.py", SECRET, "new.py"):
        assert git(repo.root, "show", "HEAD:" + path) == "whole worktree\n"
    assert not git(repo.root, "status", "--porcelain")
@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
def test_slot_removed_while_waiting_for_g_cannot_mutate(repo, operation, monkeypatch):
    body = prepare(repo, operation)
    before = snapshot(repo)
    acquire = m.lock_manager.acquire_group
    def race(*args, **kwargs):
        held = acquire(*args, **kwargs)
        repo.state["worktree_registered"] = 0
        return held
    monkeypatch.setattr(m.lock_manager, "acquire_group", race)
    assert write(repo, operation, body, 409)["code"] == "invalid_state"
    assert snapshot(repo) == before
@pytest.mark.skipif(os.name != "nt", reason="Windows case aliases")
def test_missing_tracked_file_still_cannot_be_removed_via_case_alias(repo):
    (repo.root / "a.py").unlink()
    before = snapshot(repo)
    assert write(repo, "remove", {"paths": ["A.py"]}, 409)["code"] == "path_protected"
    assert snapshot(repo) == before
