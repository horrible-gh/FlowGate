"""Independently selectable scenarios; shared fixtures remain in test_group_worktree_stage_0066.py."""
from __future__ import annotations

from tests.integration.group_worktree.test_group_worktree_stage_0066 import (
    FastAPI,
    G,
    GitServiceError,
    Path,
    REAL_ACQUIRE,
    REAL_FLOOR,
    REAL_RELEASE,
    REAL_STILL_HELD,
    SECRET,
    SimpleNamespace,
    TestClient,
    URL,
    db_path,
    files,
    get_current_user,
    git,
    git_routes,
    gs,
    index,
    json,
    lock_store,
    m,
    os,
    policy,
    put,
    pytest,
    pytestmark,
    repo,
    shutil,
    status,
    subprocess,
    tr2_file_policy,
    write,
)

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
