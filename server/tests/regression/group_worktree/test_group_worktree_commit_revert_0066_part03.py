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
