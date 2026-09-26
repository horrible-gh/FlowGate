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
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=root, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=root, check=True, capture_output=True)

    storage = tmp_path / "storage"
    storage.mkdir()
    monkeypatch.setattr(git_service, "get_storage_root", lambda: storage)
    states = [{"project_id": "flowgate", "group_id": "flowgate.default.1", "branch": "slot-live"}]
    monkeypatch.setattr(branch_service, "_branch_context", lambda project_id: ({"enabled": 1}, root, "main"))
    monkeypatch.setattr(git_service, "_base_root_of", lambda project_id: root)
    monkeypatch.setattr(git_service, "_load_secret_for", lambda cfg: "")
    monkeypatch.setattr(git_service, "_acquire_lock", lambda project_id, holder: True)
    monkeypatch.setattr(git_service.db_git, "release_lock", lambda project_id, holder: None)
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", lambda project_id: list(states))
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])
    # 0613 T0013: the delete guard also asks which live groups pin a branch as their
    # Base Branch; none do in these catalog/delete scenarios.
    from modules.flow_gate.db import groups as db_groups
    monkeypatch.setattr(db_groups, "list_open_groups_by_work_base", lambda project_id, ref: [])
    subprocess.run(["git", "branch", "slot-live"], cwd=root, check=True)
    return root


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)


def test_catalog_classifies_local_remote_only_and_registered_slot(repo):
    _git(repo, "branch", "local-only")
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(repo, "update-ref", "refs/remotes/origin/remote-only", main_sha)

    result = git_service.list_branches("flowgate")
    rows = {row["name"]: row for row in result["branches"]}

    assert rows["main"]["kind"] == "base"
    assert rows["main"]["has_remote_counterpart"] is True
    assert rows["local-only"]["kind"] == "local"
    assert rows["slot-live"]["kind"] == "internal_slot"
    assert rows["slot-live"]["connected_group_id"] == "flowgate.default.1"
    assert rows["slot-live"]["can_be_create_source"] is False
    assert rows["remote-only"]["kind"] == "remote_only"
    assert rows["remote-only"]["can_delete"] is False
    assert rows["remote-only"]["can_be_create_source"] is False
    assert rows["remote-only"]["ahead_of_base"] is None


def test_name_validation_is_git_check_ref_format_based(repo):
    with pytest.raises(GitServiceError) as caught:
        git_service.validate_new_branch_name("flowgate", repo, "main", "bad..name")
    assert caught.value.status == 422
    assert caught.value.code == "branch_ref_format_invalid"
    assert "git_stderr" in caught.value.details

    with pytest.raises(GitServiceError) as caught:
        git_service.validate_new_branch_name("flowgate", repo, "main", "main")
    assert caught.value.code == "branch_reserved_name"


def test_create_rejects_remote_only_and_actual_slot_then_creates_local_only(repo, monkeypatch):
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/remote-only", main_sha)

    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-a", "remote-only")
    assert caught.value.code == "branch_source_remote_only"

    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-b", "slot-live")
    assert caught.value.code == "branch_source_internal_slot"

    result = git_service.create_branch("flowgate", "feature/ok", "main")
    assert result["published"] is False
    assert _git(repo, "show-ref", "--verify", "refs/heads/feature/ok").returncode == 0
    assert _git(repo, "show-ref", "--verify", "refs/remotes/origin/feature/ok").returncode != 0


def test_create_rechecks_after_lock_and_fails_closed(repo, monkeypatch):
    calls = 0

    def changing_states(project_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return [{"group_id": "flowgate.default.2", "branch": "source"}]

    _git(repo, "branch", "source")
    monkeypatch.setattr(git_service.db_git, "list_states_of_project", changing_states)
    with pytest.raises(GitServiceError) as caught:
        git_service.create_branch("flowgate", "new-branch", "source")
    assert caught.value.code == "branch_source_internal_slot"
    assert _git(repo, "show-ref", "--verify", "refs/heads/new-branch").returncode != 0


def test_delete_guards_and_local_only_remote_preservation(repo, monkeypatch):
    assert git_service.check_branch_delete("flowgate", "missing") == "branch_not_found"
    assert git_service.check_branch_delete("flowgate", "main") == "branch_is_base"
    assert git_service.check_branch_delete("flowgate", "slot-live") == "branch_is_internal_slot"

    _git(repo, "branch", "in-use")
    monkeypatch.setattr(
        git_service.db_git,
        "list_open_sessions",
        lambda: [{"merge_id": 7, "group_id": "flowgate.default.9", "context": '{"target_branch":"in-use"}'}],
    )
    monkeypatch.setattr(git_service.db_git, "get_state", lambda group_id: None)
    assert git_service.check_branch_delete("flowgate", "in-use") == "branch_in_use"
    monkeypatch.setattr(git_service.db_git, "list_open_sessions", lambda: [])

    _git(repo, "branch", "merged")
    main_sha = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/merged", main_sha)
    result = git_service.delete_branch("flowgate", "merged")
    assert result["remote_counterpart_remains"] is True
    assert _git(repo, "show-ref", "--verify", "refs/heads/merged").returncode != 0
    assert _git(repo, "show-ref", "--verify", "refs/remotes/origin/merged").returncode == 0


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


def test_branch_merge_conflict_aborts_cleans_and_creates_no_session(repo, monkeypatch):
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
    created = []
    monkeypatch.setattr(git_service.db_git, "create_session", lambda *a, **k: created.append((a, k)))

    with pytest.raises(GitServiceError) as caught:
        git_service.merge_branches("flowgate", "source", "target")

    assert caught.value.code == "branch_merge_conflict"
    assert caught.value.details["conflict_files"] == ["same.txt"]
    assert _git(repo, "rev-parse", "target").stdout.strip() == target_before
    assert created == []
    storage = git_service.get_storage_root()
    assert not (storage / "git_merge_targets").exists() or not any(
        (storage / "git_merge_targets").rglob("tree")
    )


# flowgate.default.0612 T0006 §10 — merge/push separation. The existing
# test_branch_merge_clean_pushes_target_without_switching_base above already
# covers T1 (push omitted defaults to the old always-push behavior).


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


def test_branch_merge_push_false_base_target_updates_local_ref_without_push(repo):
    # T5 — merging into the project base branch itself with push=False must
    # not go through the detached managed-workspace path (that would make the
    # merge commit unreachable once the workspace is cleaned up); it runs
    # directly in the shared base checkout instead, so both the ref and the
    # working tree land together.
    _git(repo, "checkout", "-b", "feature")
    (repo / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-m", "feature")
    source_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "main")
    main_before = _git(repo, "rev-parse", "main").stdout.strip()

    result = git_service.merge_branches("flowgate", "feature", "main", push=False)

    assert result["pushed"] is False
    assert result["target_before"] == main_before
    # origin's main is untouched
    remote_main = _git(repo, "ls-remote", "origin", "refs/heads/main").stdout.split()[0]
    assert remote_main == main_before
    # the local base branch ref itself advanced to the merge commit...
    assert _git(repo, "rev-parse", "main").stdout.strip() == result["target_head"]
    assert _git(repo, "rev-parse", "main").stdout.strip() != main_before
    # ...and so did the shared base checkout's actual working tree (not just the ref)
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert _git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    # no leftover managed workspace was ever created for this combination
    storage = git_service.get_storage_root()
    assert not (storage / "git_merge_targets").exists() or not any(
        (storage / "git_merge_targets").rglob("tree")
    )


def test_branch_merge_push_false_base_target_conflict_leaves_base_clean(repo):
    # T7 — conflict handling is unchanged by push=False: still terminal, still
    # cleaned up, and (for a base target) the abort happens directly in the
    # shared checkout, which must come back exactly as it was.
    (repo / "same.txt").write_text("main\n", encoding="utf-8")
    _git(repo, "add", "same.txt")
    _git(repo, "commit", "-m", "main change")
    main_before = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "checkout", "-b", "source", "HEAD~1")
    (repo / "same.txt").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "same.txt")
    _git(repo, "commit", "-m", "source change")
    _git(repo, "checkout", "main")

    with pytest.raises(GitServiceError) as caught:
        git_service.merge_branches("flowgate", "source", "main", push=False)

    assert caught.value.code == "branch_merge_conflict"
    assert caught.value.details["conflict_files"] == ["same.txt"]
    assert _git(repo, "rev-parse", "main").stdout.strip() == main_before
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert _git(repo, "branch", "--show-current").stdout.strip() == "main"


def test_branch_merge_push_false_base_target_rejects_dirty_checkout(repo):
    # New guard: merging locally into the shared base checkout must not run
    # over uncommitted changes already sitting there (e.g. a pending
    # base-commit edit).
    _git(repo, "branch", "feature")
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(GitServiceError) as caught:
        git_service.merge_branches("flowgate", "feature", "main", push=False)

    assert caught.value.code == "branch_merge_target_dirty"
    assert _git(repo, "status", "--porcelain").stdout.strip() != ""


def test_branch_merge_push_false_base_target_rejects_open_merge_session(repo, monkeypatch):
    # TR0007 rev1 fix #2: a direct base-checkout merge (push=False, target=base)
    # mutates base like any other base-mutating op — guard_base_free() must
    # reject it while an unresolved merge session elsewhere still holds the
    # base checkout, exactly like the existing base-mutating entry points.
    _git(repo, "branch", "feature")
    main_before = _git(repo, "rev-parse", "main").stdout.strip()
    monkeypatch.setattr(
        git_service.db_git, "list_open_sessions",
        lambda: [{"merge_id": 99, "group_id": "flowgate.default.9", "context": "{}"}],
    )

    with pytest.raises(GitServiceError) as caught:
        git_service.merge_branches("flowgate", "feature", "main", push=False)

    assert caught.value.code == "merge_conflict_open"
    assert caught.value.details["blocking_group_id"] == "flowgate.default.9"
    assert _git(repo, "rev-parse", "main").stdout.strip() == main_before
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_branch_merge_push_false_base_target_untracked_collision_is_409_not_abort_failure(repo):
    # TR0007 rev1 fix #1: the pre-merge dirty guard is include_untracked=False
    # by design, so an untracked file at a path the source branch also adds
    # sails through it. Git then refuses the merge itself with "untracked
    # working tree files would be overwritten" BEFORE creating MERGE_HEAD —
    # unconditionally escalating that to `merge --abort` (a no-op) failing
    # used to misreport this as branch_merge_abort_failed 500.
    _git(repo, "checkout", "-b", "source")
    (repo / "new.txt").write_text("from source\n", encoding="utf-8")
    _git(repo, "add", "new.txt")
    _git(repo, "commit", "-m", "add new.txt")
    _git(repo, "checkout", "main")
    main_before = _git(repo, "rev-parse", "main").stdout.strip()
    (repo / "new.txt").write_text("untracked local copy\n", encoding="utf-8")

    with pytest.raises(GitServiceError) as caught:
        git_service.merge_branches("flowgate", "source", "main", push=False)

    assert caught.value.code == "branch_merge_untracked_conflict"
    assert caught.value.details["files"] == ["new.txt"]
    # the local checkout was never mutated: no merge started, ref unchanged,
    # and the untracked file itself survives untouched.
    assert _git(repo, "rev-parse", "main").stdout.strip() == main_before
    assert (repo / "new.txt").read_text(encoding="utf-8") == "untracked local copy\n"
    assert _git(repo, "status", "--porcelain").stdout == "?? new.txt\n"


def test_branch_merge_push_false_base_target_untracked_ff_only_is_409_not_diverged(repo):
    # TR0007 rev1 fix #1 (second stage): the same untracked collision can hit
    # the earlier `merge --ff-only origin/{target}` sync — a fast-forward that
    # really could land, just blocked by a local untracked file — and must not
    # be misclassified as branch_merge_target_diverged.
    clone = repo.parent / "clone"
    _git(repo.parent, "clone", str(repo.parent / "remote.git"), str(clone))
    _git(clone, "checkout", "-B", "main", "origin/main")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "config", "user.email", "test@example.com")
    (clone / "collide.txt").write_text("from origin\n", encoding="utf-8")
    _git(clone, "add", "collide.txt")
    _git(clone, "commit", "-m", "origin-only change")
    push = _git(clone, "push", "origin", "main")
    assert push.returncode == 0

    _git(repo, "branch", "feature")
    main_before = _git(repo, "rev-parse", "main").stdout.strip()
    (repo / "collide.txt").write_text("untracked local copy\n", encoding="utf-8")

    with pytest.raises(GitServiceError) as caught:
        git_service.merge_branches("flowgate", "feature", "main", push=False)

    assert caught.value.code == "branch_merge_untracked_conflict"
    assert caught.value.details["files"] == ["collide.txt"]
    assert _git(repo, "rev-parse", "main").stdout.strip() == main_before
    assert (repo / "collide.txt").read_text(encoding="utf-8") == "untracked local copy\n"


def _client(*, is_admin=True):
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": is_admin}
    return TestClient(app, raise_server_exceptions=False)


def test_branch_routes_rbac_delegation_and_error_envelope(monkeypatch):
    monkeypatch.setattr(git_routes.git_service, "list_branches", lambda project_id: {"ok": True, "branches": []})
    monkeypatch.setattr(git_routes.git_service, "create_branch", lambda project_id, name, source: {"ok": True, "branch": name})
    monkeypatch.setattr(git_routes.git_service, "delete_branch", lambda project_id, name: {"ok": True, "branch": name})
    client = _client()
    assert client.get("/api/v1/projects/flowgate/git/branches").status_code == 200
    assert client.post("/api/v1/projects/flowgate/git/branches", json={"name": "x/y", "source_branch": "main"}).json()["branch"] == "x/y"
    assert client.delete("/api/v1/projects/flowgate/git/branches/x/y").json()["branch"] == "x/y"

    def boom(project_id, name):
        raise GitServiceError(409, "branch_is_base", "base branch cannot be deleted")

    monkeypatch.setattr(git_routes.git_service, "delete_branch", boom)
    response = client.delete("/api/v1/projects/flowgate/git/branches/main")
    assert response.status_code == 409
    assert response.json() == {"ok": False, "error": {"code": "branch_is_base", "message": "base branch cannot be deleted"}}

    monkeypatch.setattr(
        "modules.flow_gate.rbac.decorators._permission_service.has_permission",
        lambda *args, **kwargs: False,
    )
    assert _client(is_admin=False).get("/api/v1/projects/flowgate/git/branches").status_code == 403
    assert _client(is_admin=False).post("/api/v1/projects/flowgate/git/branches", json={"name": "x", "source_branch": "main"}).status_code == 403
    assert _client(is_admin=False).delete("/api/v1/projects/flowgate/git/branches/x").status_code == 403


def test_branch_merge_route_forwards_push_and_defaults_to_true(monkeypatch):
    # T0006 §4/§8 — BranchMergeBody.push must reach merge_branches() verbatim,
    # and an omitted field must keep the pre-existing always-push behavior.
    calls = []

    def fake_merge(project_id, source_branch, target_branch, push=True):
        calls.append({"project_id": project_id, "source_branch": source_branch,
                       "target_branch": target_branch, "push": push})
        return {"ok": True, "pushed": push}

    monkeypatch.setattr(git_routes.git_service, "merge_branches", fake_merge)
    client = _client()

    resp = client.post(
        "/api/v1/projects/flowgate/git/branches/merge",
        json={"source_branch": "a", "target_branch": "b"},
    )
    assert resp.status_code == 200 and resp.json()["pushed"] is True
    assert calls[-1]["push"] is True

    resp = client.post(
        "/api/v1/projects/flowgate/git/branches/merge",
        json={"source_branch": "a", "target_branch": "b", "push": False},
    )
    assert resp.status_code == 200 and resp.json()["pushed"] is False
    assert calls[-1]["push"] is False