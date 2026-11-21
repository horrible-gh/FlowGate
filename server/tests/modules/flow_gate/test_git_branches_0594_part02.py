"""Independently selectable scenarios; shared fixtures remain in test_git_branches_0594.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_branches_0594 import (
    FastAPI,
    GitServiceError,
    Path,
    TestClient,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _client,
    _git,
    _pytest_locks,
    attempt_db,
    branch_service,
    contextmanager,
    get_current_user,
    git_routes,
    git_service,
    group_store,
    os,
    pytest,
    pytestmark,
    repo,
    shutil,
    sqlite3,
    subprocess,
    sys,
)

def test_delete_fails_closed_when_worktree_registry_cannot_be_read(repo, monkeypatch):
    _git(repo, "branch", "probe-fail")
    monkeypatch.setattr(
        branch_service, "_registered_worktrees_for_delete", lambda base_root: None
    )

    assert git_service.check_branch_delete("flowgate", "probe-fail") == (
        "branch_worktree_state_unknown"
    )
    with pytest.raises(GitServiceError) as caught:
        git_service.delete_branch("flowgate", "probe-fail")
    assert caught.value.status == 409
    assert caught.value.code == "branch_worktree_state_unknown"
def test_delete_race_rechecks_worktree_registry_after_git_refusal(repo, monkeypatch):
    _git(repo, "branch", "race-linked")
    original_run_git = git_service._run_git
    worktree_snapshots = iter([
        [],
        [],
        [{"path": str(repo.parent / "race-wt"), "branch": "race-linked", "prunable": False}],
    ])
    monkeypatch.setattr(
        branch_service,
        "_registered_worktrees_for_delete",
        lambda base_root: next(worktree_snapshots),
    )

    def run_git(args, **kwargs):
        if args == ["branch", "-d", "race-linked"]:
            return subprocess.CompletedProcess(
                args,
                1,
                stdout="",
                stderr=(
                    "error: cannot delete branch 'race-linked' used by worktree "
                    f"at '{repo.parent / 'race-wt'}'\n"
                ),
            )
        return original_run_git(args, **kwargs)

    monkeypatch.setattr(git_service, "_run_git", run_git)
    with pytest.raises(GitServiceError) as caught:
        git_service.delete_branch("flowgate", "race-linked")
    assert caught.value.status == 409
    assert caught.value.code == "branch_checked_out_in_worktree"
def test_delete_unmerged_reports_commit_preview(repo):
    _git(repo, "checkout", "-b", "unmerged")
    (repo / "work.txt").write_text("work\n", encoding="utf-8")
    _git(repo, "add", "work.txt")
    _git(repo, "commit", "-m", "unmerged work")
    _git(repo, "checkout", "main")

    with pytest.raises(GitServiceError) as caught:
        git_service.delete_branch("flowgate", "unmerged")
    assert caught.value.code == "branch_unmerged_commits"
    assert caught.value.details["commits"][0]["subject"] == "unmerged work"
    assert _git(repo, "show-ref", "--verify", "refs/heads/unmerged").returncode == 0
def test_branch_merge_clean_pushes_target_without_switching_base(repo):
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature")
    source_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")
    base_head = _git(repo, "rev-parse", "HEAD").stdout.strip()

    result = git_service.merge_branches("flowgate", "feature", "develop")

    assert result["pushed"] is True
    assert result["workspace_cleaned"] is True
    assert _git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == base_head
    assert _git(repo, "rev-parse", "feature").stdout.strip() == source_head
    assert _git(repo, "show", "develop:feature.txt").stdout == "feature\n"
    assert _git(repo, "ls-remote", "--exit-code", "origin", "refs/heads/develop").returncode == 0
def test_branch_merge_rejects_self_remote_and_actual_slots(repo):
    sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/remote-only", sha)
    for source, target, code in [
        ("main", "main", "branch_merge_same_branch"),
        ("remote-only", "main", "branch_merge_source_remote_only"),
        ("main", "remote-only", "branch_merge_target_remote_only"),
        ("slot-live", "main", "branch_merge_source_internal_slot"),
        ("main", "slot-live", "branch_merge_target_internal_slot"),
    ]:
        with pytest.raises(GitServiceError) as caught:
            git_service.merge_branches("flowgate", source, target)
        assert caught.value.code == code
def test_branch_merge_conflict_keeps_a_persistent_attempt_and_its_workspace(repo, monkeypatch):
    # flowgate.default.0630 T0005 §15 — replaces
    # test_branch_merge_conflict_aborts_cleans_and_creates_no_session, whose contract
    # (abort + clean + no session) is exactly what T0005 removes. A conflict is now a
    # live, addressable attempt: no `merge --abort`, the managed workspace keeps
    # MERGE_HEAD and the markers, and nothing about the target moved.
    _git(repo, "checkout", "-b", "target")
    (repo / "same.txt").write_text("target\n", encoding="utf-8")
    _git(repo, "add", "same.txt")
    _git(repo, "commit", "-m", "target")
    target_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")
    _git(repo, "checkout", "-b", "source")
    (repo / "same.txt").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "same.txt")
    _git(repo, "commit", "-m", "source")
    _git(repo, "checkout", "main")
    group_sessions = []
    monkeypatch.setattr(git_service.db_git, "create_session", lambda *a, **k: group_sessions.append((a, k)))

    result = git_service.merge_branches("flowgate", "source", "target")

    assert result["status"] == "conflict" and result["ok"] is True
    assert result["conflict_files"] == ["same.txt"]
    assert result["push"] is True and result["pushed"] is False
    assert group_sessions == []                       # no fake group session was opened
    session = git_service.db_git.get_session(result["merge_id"])
    assert session["kind"] == "branch_merge" and session["owner_type"] == "branch_merge"
    assert session["group_id"] is None and session["project_id"] == "flowgate"
    assert session["status"] == "open"
    assert _git(repo, "rev-parse", "target").stdout.strip() == target_before
    storage = git_service.get_storage_root()
    trees = list((storage / "git_merge_targets").rglob("tree"))
    assert len(trees) == 1
    assert _git(trees[0], "rev-parse", "-q", "--verify", "MERGE_HEAD").stdout.strip()
    assert "<<<<<<<" in (trees[0] / "same.txt").read_text(encoding="utf-8")
def test_branch_merge_push_explicit_true_pushes(repo):
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature")
    _git(repo, "checkout", "main")

    result = git_service.merge_branches("flowgate", "feature", "develop", push=True)

    assert result["pushed"] is True
    assert _git(repo, "ls-remote", "--exit-code", "origin", "refs/heads/develop").returncode == 0
    remote_sha = _git(repo, "ls-remote", "origin", "refs/heads/develop").stdout.split()[0]
    assert remote_sha == _git(repo, "rev-parse", "develop").stdout.strip()
def test_branch_merge_push_false_skips_push_and_preserves_local_ref(repo):
    # T3/T4/T6 — an ordinary (non-base) target's managed worktree already has
    # the branch checked out, so its ref must advance even though nothing
    # reaches origin.
    _git(repo, "branch", "develop")
    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature")
    source_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")

    result = git_service.merge_branches("flowgate", "feature", "develop", push=False)

    assert result["pushed"] is False
    assert result["workspace_cleaned"] is True
    # nothing was published: origin never learned about "develop" at all
    assert _git(repo, "ls-remote", "--exit-code", "origin", "refs/heads/develop").returncode != 0
    # but the local branch ref itself really did advance to the merge commit
    assert _git(repo, "rev-parse", "develop").stdout.strip() == result["target_head"]
    assert _git(repo, "rev-parse", "feature").stdout.strip() == source_head
    assert _git(repo, "show", "develop:feature.txt").stdout == "feature\n"
    # the shared base checkout was not touched
    assert _git(repo, "branch", "--show-current").stdout.strip() == "main"
