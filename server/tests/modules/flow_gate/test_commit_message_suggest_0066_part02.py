"""Independently selectable scenarios; shared fixtures remain in test_commit_message_suggest_0066.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_commit_message_suggest_0066 import (
    ENDPOINT,
    G,
    GitServiceError,
    REAL_EXECUTE,
    SECRET,
    SimpleNamespace,
    URL,
    call,
    git,
    git_routes,
    group_ai_leases,
    gs,
    index,
    json,
    m,
    policy,
    provider,
    provider_cli,
    put,
    pytest,
    records,
    replace,
    repo,
    runtime,
    s,
    stage,
    sys,
    threading,
)

def test_snapshot_retries_and_uses_new_objects(repo, provider, monkeypatch):
    stage(repo, text="first\n")
    read = m._read_index_once
    calls = []
    def race(root):
        snap = read(root)
        calls.append(snap)
        if len(calls) == 1:
            stage(repo, text="second-snapshot\n")
        return snap
    monkeypatch.setattr(m, "_read_index_once", race)
    result = call(repo)
    assert len(calls) == 4
    assert result["index_token"] == calls[-1].index_token
    assert "second-snapshot" in provider.captures[0] and 'first' not in provider.captures[0]
def test_snapshot_exhaustion(repo, provider, monkeypatch):
    stage(repo)
    read = m._read_index_once
    calls = []
    def race(root):
        snap = read(root)
        calls.append(snap)
        stage(repo, text=str(len(calls)) + "\n")
        return snap
    monkeypatch.setattr(m, "_read_index_once", race)
    assert call(repo, 409)["code"] == "worktree_changed"
    assert len(calls) == 2 * s.SNAPSHOT_ATTEMPTS and not provider.captures
@pytest.mark.parametrize("change", ["protect", "unstage", "commit", "merge", "cleanup"])
def test_after_snapshot_changes_never_reopen_index_or_worktree(repo, provider, monkeypatch, tmp_path, change):
    # A real linked worktree proves the common objects survive slot removal.
    common = repo.root
    linked = tmp_path / "linked"
    git(common, "branch", "base")
    git(common, "checkout", "base")
    git(common, "worktree", "add", str(linked), "group")
    repo.root = linked
    monkeypatch.setattr(gs, "effective_src_root_ex", lambda *a: (linked, "worktree"))
    stage(repo)
    snap = m.read_index_snapshot(G)
    expected = s._prompt(snap, *s._staged(snap, policy.Ownership(frozenset(repo.owner))))
    read = m.read_index_snapshot
    def freeze(*a, **kw):
        frozen = read(*a, **kw)
        if change == "protect":
            stage(repo, SECRET, "LATE_SECRET\n")
        elif change == "unstage":
            git(linked, "reset", "HEAD", "--", "a.py")
        elif change == "commit":
            git(linked, "commit", "-m", "concurrent commit")
        elif change == "merge":
            put(common, "base-new.py", "base\n")
            git(common, "add", ".")
            git(common, "commit", "-m", "base change")
            git(linked, "commit", "-m", "staged commit")
            git(common, "merge", "--no-ff", "group", "-m", "manual merge")
        else:
            git(common, "worktree", "remove", "--force", str(linked))
            repo.state["worktree_registered"] = 0
        return frozen
    monkeypatch.setattr(m, "read_index_snapshot", freeze)
    result = call(repo)
    assert result["index_token"] == snap.index_token and result["head"] == snap.head
    assert (provider.captures[0], result["input_truncated"]) == expected
    assert "LATE_SECRET" not in provider.captures[0]
def test_slot_lost_during_snapshot(repo, provider, monkeypatch):
    stage(repo)
    read = m._read_index_once
    def disappear(root):
        snap = read(root)
        repo.state["worktree_registered"] = 0
        return snap
    monkeypatch.setattr(m, "_read_index_once", disappear)
    err = call(repo, 409)
    assert err["code"] == "invalid_state" and err["details"]["reason"] == "worktree_unavailable"
@pytest.mark.parametrize("changed", [False, True])
def test_suggested_token_commits_only_same_index(repo, provider, monkeypatch, changed):
    stage(repo)
    result = call(repo)
    if changed:
        stage(repo, "b.py", "later\n")
    monkeypatch.setattr(m.tr2.db_recovery, "has_unresolved", lambda _: False)
    r = repo.client.post(URL + "/commit", json={"message": result["suggested"], "index_token": result["index_token"]})
    assert r.status_code == (409 if changed else 200), r.text
    if changed:
        assert r.json()["error"]["code"] == "worktree_changed"
    else:
        assert r.json()["result"]["committed_paths"] == ["a.py"]
def test_active_lease_is_untouched_no_run_token_or_git_lock(repo, provider, monkeypatch):
    stage(repo)
    lease = {"run_id": "existing", "group_id": G}
    monkeypatch.setattr(group_ai_leases, "get_active", lambda _: lease)
    def forbidden(*a, **kw):
        pytest.fail("suggestion attempted a mutation or AI admission")
    monkeypatch.setattr(m.finalize, "_guard_group_update_ai_idle", forbidden)
    monkeypatch.setattr(m.lock_manager, "acquire_group", forbidden)
    monkeypatch.setattr(git_routes.token_service, "issue", forbidden, raising=False)
    before = dict(runtime._svc()._runs)
    call(repo)
    assert group_ai_leases.get_active(G) is lease and runtime._svc()._runs == before
    assert not repo.calls
@pytest.mark.parametrize("state", ["conflict", "merging", "merged", "pushed", "discarded"])
def test_state_gate(repo, provider, state):
    stage(repo)
    repo.state["status"] = state
    assert call(repo, 409)["code"] == "invalid_state"
    assert not provider.captures
@pytest.mark.parametrize("body", [{}, {"provider_id": None}, {"provider_id": "  "}])
def test_provider_required(repo, provider, body):
    assert call(repo, 422, body)["code"] == "provider_required"
