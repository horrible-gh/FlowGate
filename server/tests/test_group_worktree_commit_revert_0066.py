"""0066 E4--E6: real Git contents, real TR2 mutation guard and HTTP contracts."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from copy import deepcopy

import pytest

from test_group_worktree_stage_0066 import (
    repo as stage_repo, git, put, index, write, status,
    G, URL, SECRET, m, gs, git_routes, tr2_file_policy as tr2,
    REAL_ACQUIRE, REAL_RELEASE, REAL_STILL_HELD, lock_store, db_path,
)
from modules.flow_gate.services.git.credentials import GitServiceError
from modules.flow_gate.storage.safe_path import MutationPathAliasError

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary unavailable")


@pytest.fixture
def repo(stage_repo, monkeypatch):
    monkeypatch.setattr(tr2.tr2_service, "resolve_source_root", lambda *args: stage_repo.root)
    monkeypatch.setattr(tr2.db_recovery, "has_unresolved", lambda _: False)
    return stage_repo


def prepare(repo, operation):
    if operation == "remove":
        path = "new.py"
    else:
        path = "a.py"
    put(repo.root, path, "staged\n")
    git(repo.root, "add", path)
    return {"message": "fix: staged"} if operation == "commit" else {"paths": [path]}


def snapshot(repo):
    return (git(repo.root, "rev-parse", "HEAD"), index(repo),
            {p.relative_to(repo.root).as_posix(): p.read_bytes()
             for p in repo.root.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo.root).parts})


def test_commit_only_index_preserves_working_and_untracked_author_state(repo, monkeypatch):
    repo.cfg.update(author_name="Configured Author", author_email="configured@example.test")
    before_state = deepcopy(repo.state)
    put(repo.root, "a.py", "index version\n")
    git(repo.root, "add", "a.py")
    put(repo.root, "a.py", "working version\n")
    put(repo.root, "new.py", "new untracked\n")
    put(repo.root, "b.py", "unstaged\n")
    expected = snapshot(repo)[2]
    observed = []
    run = gs._run_git
    def spy(args, **kwargs):
        observed.append(args)
        return run(args, **kwargs)
    monkeypatch.setattr(gs, "_run_git", spy)
    result = write(repo, "commit", {"message": " \tfix: staged\n only  "})
    assert result["subject"] == "fix: staged only"
    assert result["commit_sha"] == git(repo.root, "rev-parse", "HEAD").strip()
    assert result["commit"] == result["commit_sha"][:7]
    assert result["committed_paths"] == ["a.py"] and result["committed_count"] == 1
    assert git(repo.root, "show", "HEAD:a.py") == "index version\n"
    assert git(repo.root, "show", "HEAD:b.py") == "original\n"
    assert snapshot(repo)[2] == expected
    assert git(repo.root, "log", "-1", "--format=%an <%ae>").strip() == "Configured Author <configured@example.test>"
    assert repo.state == before_state
    assert result["status"]["counts"]["staged"] == 0
    assert result["status"]["counts"]["unstaged"] == 2
    assert result["status"]["counts"]["untracked"] == 1
    assert result["status"]["committed"]["commit_count"] == 1
    assert not any(set(args) & {"add", "merge", "push", "fetch", "reset"} for args in observed)
    assert repo.calls[0][1]["holder_kind"] == "source_mutation"
    assert repo.calls[-1][0] == "release"


@pytest.mark.parametrize("message,code", [
    ("", "commit_message_required"), (" \n\t ", "commit_message_required"),
    ("x" * (gs.COMMIT_SUBJECT_MAX + 1), "invalid_request"), ("a\0b", "invalid_request"),
])
def test_bad_message_before_lock_without_side_effects(repo, message, code):
    prepare(repo, "commit")
    before = snapshot(repo)
    assert write(repo, "commit", {"message": message}, 422)["code"] == code
    assert not repo.calls and snapshot(repo) == before


def test_message_exact_limit_and_unicode(repo):
    prepare(repo, "commit")
    message = "가" * gs.COMMIT_SUBJECT_MAX
    assert write(repo, "commit", {"message": message})["subject"] == message


def test_nothing_staged_has_no_absorption(repo):
    put(repo.root, "a.py", "working\n")
    put(repo.root, "new.py", "new\n")
    before = snapshot(repo)
    assert write(repo, "commit", {"message": "fix: none"}, 422)["code"] == "nothing_staged"
    assert snapshot(repo) == before


@pytest.mark.parametrize("path,kind", [(SECRET, "hidden"), ("managed.py", "managed"), ("new.log", "excluded_new")])
def test_external_protected_stage_refuses_entire_commit(repo, path, kind):
    prepare(repo, "commit")
    put(repo.root, path, "protected\n")
    git(repo.root, "add", path)
    before = snapshot(repo)
    error = write(repo, "commit", {"message": "fix: protected"}, 409)
    assert error["code"] == "staged_protected_paths"
    assert error["details"]["counts"][kind] == 1
    assert SECRET not in json.dumps(error)
    if path == SECRET:
        assert error["details"]["counts"]["managed"] == 0 and not error["details"]["paths"]
    assert snapshot(repo) == before


@pytest.mark.parametrize("path", ["managed.py", SECRET])
def test_protected_rename_source_deletion_blocks_commit(repo, path):
    git(repo.root, "mv", path, "renamed.py")
    before = snapshot(repo)
    error = write(repo, "commit", {"message": "fix: rename"}, 409)
    assert error["code"] == "staged_protected_paths" and SECRET not in json.dumps(error)
    assert snapshot(repo) == before


@pytest.mark.parametrize("delete", [False, True])
def test_existing_excluded_changes_are_committable(repo, delete):
    if delete:
        (repo.root / "old.log").unlink()
    else:
        put(repo.root, "old.log", "cleanup\n")
    git(repo.root, "add", "old.log")
    result = write(repo, "commit", {"message": "fix: existing artifact"})
    assert result["committed_paths"] == ["old.log"]
    assert ("old.log" in git(repo.root, "ls-tree", "--name-only", "HEAD").splitlines()) is not delete


def make_unsupported(repo, kind):
    oid = git(repo.root, "rev-parse", "HEAD:a.py").strip()
    if kind == "type":
        git(repo.root, "update-index", "--cacheinfo", "120000", oid, "a.py")
    else:
        payload = f"0 {'0' * 40}\ta.py\0" + "".join(
            f"100644 {oid} {stage}\ta.py\0" for stage in (1, 2, 3))
        result = subprocess.run(["git", "update-index", "-z", "--index-info"], cwd=repo.root,
                                input=payload.encode(), capture_output=True)
        assert result.returncode == 0


@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
@pytest.mark.parametrize("kind", ["type", "unmerged"])
def test_unsupported_entries_never_mutate(repo, operation, kind):
    make_unsupported(repo, kind)
    before = snapshot(repo)
    body = {"message": "fix: unsupported"} if operation == "commit" else {"paths": ["a.py"]}
    error = write(repo, operation, body, 409)
    assert error["code"] == ("index_entry_unsupported" if operation == "commit" else "path_protected")
    assert snapshot(repo) == before


@pytest.mark.parametrize("token", ["index_token", "worktree_token"])
def test_commit_stale_token_is_side_effect_free(repo, token):
    prepare(repo, "commit")
    view = status(repo)
    put(repo.root, "b.py", "later\n")
    git(repo.root, "add", "b.py")
    before = snapshot(repo)
    assert write(repo, "commit", {"message": "fix: stale", token: view[token]}, 409)["code"] == "worktree_changed"
    assert snapshot(repo) == before


def test_index_token_allows_later_working_edit_and_matching_tokens_commit(repo):
    prepare(repo, "commit")
    view = status(repo)
    put(repo.root, "b.py", "later\n")
    result = write(repo, "commit", {"message": "fix: same index", "index_token": view["index_token"]})
    assert result["committed_paths"] == ["a.py"]
    assert (repo.root / "b.py").read_text() == "later\n"
    prepare(repo, "commit")
    put(repo.root, "a.py", "second\n")
    git(repo.root, "add", "a.py")
    view = status(repo)
    write(repo, "commit", {"message": "fix: second", "index_token": view["index_token"],
                           "worktree_token": view["worktree_token"]})


def test_revert_restores_index_deleted_files_and_existing_excluded(repo):
    for path in ("a.py", "b.py", "old.log"):
        put(repo.root, path, "staged\n")
        git(repo.root, "add", path)
    put(repo.root, "a.py", "working\n")
    (repo.root / "b.py").unlink()
    put(repo.root, "untouched.py", "keep\n")
    result = write(repo, "revert", {"paths": ["a.py", "b.py", "old.log"]})
    assert [row["result"] for row in result["results"]] == ["reverted"] * 3
    for path in ("a.py", "b.py", "old.log"):
        assert (repo.root / path).read_text() == "original\n"
    assert not git(repo.root, "diff", "--cached", "--name-only")
    assert (repo.root / "untouched.py").read_text() == "keep\n"
    assert git(repo.root, "rev-parse", "HEAD").strip() == repo.floor
    assert repo.state["status"] == "none"
    assert write(repo, "revert", {"paths": ["a.py"]})["results"] == [{"path": "a.py", "result": "not_changed"}]


@pytest.mark.parametrize("staged", [False, True])
@pytest.mark.parametrize("new_path", ["new.py", "new.log"])
def test_revert_rejects_new_path_before_restoring_anything(repo, staged, new_path):
    put(repo.root, "a.py", "keep\n")
    put(repo.root, new_path, "new\n")
    if staged:
        git(repo.root, "add", new_path)
    before = snapshot(repo)
    assert write(repo, "revert", {"paths": ["a.py", new_path]}, 422)["code"] == "new_path_use_remove"
    assert snapshot(repo) == before


@pytest.mark.parametrize("staged", [False, True])
def test_remove_new_file_from_index_and_disk_is_idempotent(repo, staged):
    put(repo.root, "new.py", "new\n")
    if staged:
        git(repo.root, "add", "new.py")
        put(repo.root, "new.py", "later\n")
    put(repo.root, "a.py", "keep\n")
    result = write(repo, "remove", {"paths": ["new.py"]})
    assert result["results"] == [{"path": "new.py", "result": "removed"}]
    assert not (repo.root / "new.py").exists()
    assert "new.py" not in git(repo.root, "ls-files").splitlines()
    assert (repo.root / "a.py").read_text() == "keep\n"
    assert write(repo, "remove", {"paths": ["new.py"]})["results"][0]["result"] == "not_changed"


def test_remove_staged_file_missing_on_disk(repo):
    prepare(repo, "remove")
    (repo.root / "new.py").unlink()
    assert write(repo, "remove", {"paths": ["new.py"]})["results"][0]["result"] == "removed"
    assert "new.py" not in git(repo.root, "ls-files").splitlines()


def test_remove_named_ignored_new_file(repo):
    (repo.root / ".git" / "info" / "exclude").write_text("ignored.tmp\n", encoding="utf-8")
    put(repo.root, "ignored.tmp", "ignored\n")
    assert "ignored.tmp" not in {row["path"] for row in status(repo)["files"]}
    assert write(repo, "remove", {"paths": ["ignored.tmp"]})["results"] == [
        {"path": "ignored.tmp", "result": "removed"}]
    assert not (repo.root / "ignored.tmp").exists()
    assert write(repo, "remove", {"paths": ["ignored.tmp"]})["results"][0]["result"] == "not_changed"


def test_remove_rejects_mixed_existing_request_without_partial_deletion(repo):
    prepare(repo, "remove")
    before = snapshot(repo)
    assert write(repo, "remove", {"paths": ["new.py", "a.py"]}, 422)["code"] == "existing_path_use_revert"
    assert snapshot(repo) == before


@pytest.mark.parametrize("staged", [False, True])
def test_new_excluded_remove_is_protected_even_when_absent(repo, staged):
    put(repo.root, "new.log", "keep\n")
    if staged:
        git(repo.root, "add", "new.log")
    before = snapshot(repo)
    error = write(repo, "remove", {"paths": ["new.log"]}, 409)
    assert error["code"] == "path_protected"
    assert snapshot(repo) == before
    (repo.root / "new.log").unlink()
    assert write(repo, "remove", {"paths": ["new.log"]}, 409)["code"] == "path_protected"


@pytest.mark.parametrize("operation", ["revert", "remove"])
@pytest.mark.parametrize("path", [SECRET, "not-present/.env"])
def test_hidden_precedes_lock_existence_and_ownership(repo, operation, path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Hidden requests must not read the slot or ownership or acquire G")
    monkeypatch.setattr(m, "_context", forbidden)
    error = write(repo, operation, {"paths": [path]}, 409)
    assert error["code"] == "path_protected" and error["details"] == {"reason": "hidden"}
    assert path not in json.dumps(error) and "TR2_MANAGED_FILE" not in json.dumps(error)
    assert not repo.calls


@pytest.mark.parametrize("operation", ["revert", "remove"])
def test_real_general_source_guard_rejects_managed_paths(repo, operation):
    body = prepare(repo, operation)
    before = snapshot(repo)
    error = write(repo, operation, {"paths": [*body["paths"], "managed.py"]}, 409)
    assert error["code"] == "path_protected"
    assert error["details"]["cause_code"] == "TR2_MANAGED_FILE"
    assert snapshot(repo) == before and repo.calls[-1][0] == "release"


@pytest.mark.parametrize("operation", ["revert", "remove"])
def test_alias_refusal_mapping_does_not_expose_upstream_details(repo, operation, monkeypatch):
    body = prepare(repo, operation)
    before = snapshot(repo)
    def alias(*args, **kwargs):
        raise MutationPathAliasError(SECRET)
    monkeypatch.setattr(tr2, "resolve_mutation_target_no_alias", alias)
    error = write(repo, operation, body, 409)
    assert error["code"] == "path_protected"
    assert error["details"]["cause_code"] == "SOURCE_PATH_ALIAS_NOT_ALLOWED"
    assert SECRET not in json.dumps(error) and snapshot(repo) == before


def test_real_directory_alias_cannot_remove_target(repo):
    original = repo.root / "original"
    original.mkdir()
    put(repo.root, "original/new.py", "keep\n")
    alias = repo.root / "alias"
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(original)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(original, target_is_directory=True)
    try:
        error = write(repo, "remove", {"paths": ["alias/new.py"]}, 409)
        assert error["details"]["cause_code"] == "SOURCE_PATH_ALIAS_NOT_ALLOWED"
        assert (original / "new.py").read_text() == "keep\n"
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
@pytest.mark.parametrize("failure", ["recovery", "recovery_read", "invariant", "ownership_read"])
def test_ownership_failures_preserve_all_state_and_hide_details(repo, operation, failure, monkeypatch):
    body = prepare(repo, operation)
    before = snapshot(repo)
    def broken(_):
        if failure == "invariant":
            raise tr2.Tr2OwnershipInvariantError("bad ledger", details={"ledger_row_id": 999, "path": SECRET})
        raise RuntimeError("ledger_row_id=999 " + SECRET)
    if failure == "recovery":
        monkeypatch.setattr(tr2.db_recovery, "has_unresolved", lambda _: True)
    elif failure == "recovery_read":
        monkeypatch.setattr(tr2.db_recovery, "has_unresolved", broken)
    else:
        monkeypatch.setattr(tr2, "managed_paths", broken)
    error = write(repo, operation, body, 409)
    assert error["code"] == ("tr_history_recovery_required" if failure.startswith("recovery") else "ownership_unavailable")
    if not failure.startswith("recovery"):
        assert error["details"]["reason"] == ("ownership_invariant" if failure == "invariant" else "ownership_read_failed")
    assert SECRET not in json.dumps(error) and "999" not in json.dumps(error)
    assert snapshot(repo) == before and repo.calls[-1][0] == "release"


@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
@pytest.mark.parametrize("code", ["branch_merge_claim_active", "run_already_active", "group_lease_active"])
def test_technical_guard_rechecked_under_g(repo, operation, code, monkeypatch):
    body = prepare(repo, operation)
    before = snapshot(repo)
    calls = 0
    def guard(_):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise GitServiceError(409, code, "busy")
    module, attr = ((m.lock, "guard_group_branch_merge_free") if code == "branch_merge_claim_active"
                    else (m.finalize, "_guard_group_update_ai_idle"))
    monkeypatch.setattr(module, attr, guard)
    assert write(repo, operation, body, 409)["code"] == code
    assert snapshot(repo) == before and repo.calls[-1][0] == "release"


@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
def test_slot_ownership_revalidated_under_g(repo, operation, monkeypatch):
    body = prepare(repo, operation)
    before = snapshot(repo)
    acquire = m.lock_manager.acquire_group
    def race(*args, **kwargs):
        held = acquire(*args, **kwargs)
        repo.state["branch"] = "other"
        return held
    monkeypatch.setattr(m.lock_manager, "acquire_group", race)
    assert write(repo, operation, body, 409)["code"] == "invalid_state"
    assert snapshot(repo) == before and repo.calls[-1][0] == "release"


@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
def test_ownership_revalidated_under_g(repo, operation, monkeypatch):
    body = prepare(repo, operation)
    path = "new.py" if operation == "remove" else "a.py"
    before = snapshot(repo)
    acquire = m.lock_manager.acquire_group
    def race(*args, **kwargs):
        held = acquire(*args, **kwargs)
        repo.owner.add(path)
        return held
    monkeypatch.setattr(m.lock_manager, "acquire_group", race)
    error = write(repo, operation, body, 409)
    assert error["code"] == ("staged_protected_paths" if operation == "commit" else "path_protected")
    assert snapshot(repo) == before


@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
@pytest.mark.parametrize("frozen", [False, True])
def test_real_mutex_and_freeze_preserve_head_index_files(lock_store, repo, monkeypatch, operation, frozen):
    body = prepare(repo, operation)
    before = snapshot(repo)
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
    try:
        error = write(repo, operation, body, 409)
        assert error["code"] == "git_busy"
        assert error["details"]["reason_code"] == ("group_frozen_for_approval" if frozen else "lock_busy")
        assert snapshot(repo) == before
    finally:
        if held:
            REAL_RELEASE(held[1], held[0].lock_key)


@pytest.mark.parametrize("operation", ["commit", "revert", "remove"])
def test_pending_approval_is_not_a_gate(repo, operation, monkeypatch):
    body = prepare(repo, operation)
    repo.state["status"] = "awaiting_choice"
    monkeypatch.setattr(m.approval_intent, "group_is_final_approval_bound", lambda _: True)
    result = write(repo, operation, body)
    assert repo.state["status"] == "awaiting_choice"
    assert result["status"]["git_state"]["final_approval_bound"]


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
    assert permissions == [("project.settings.edit", "p_0669b")]
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
