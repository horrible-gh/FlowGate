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
def test_open_session_blocker_names_the_session(repo, monkeypatch):
    # flowgate.v02test.0068: the Git tab names the session and offers its abort even with no conflict card.
    monkeypatch.setattr(gs.db_git, "get_open_session_by_group",
                        lambda _: {"merge_id": 41, "kind": "branch_merge", "status": "open"})
    blocker = status(repo)["blockers"][0]
    assert blocker["code"] == "session_open"
    assert blocker["session_kind"] == "branch_merge" and blocker["merge_id"] == 41
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
