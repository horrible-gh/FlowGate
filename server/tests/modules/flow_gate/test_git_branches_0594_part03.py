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

def test_branch_merge_push_false_base_target_updates_local_ref_without_push(repo):
    # T5 — merging into the project base branch itself with push=False must
    # land the ref and the shared base checkout's working tree together.
    # the merge now runs in a detached managed workspace (so a
    # conflict never sits in the shared checkout) and a clean result is applied
    # to the base checkout by fast-forward before that workspace is cleaned up —
    # the merge commit is never left unreachable.
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
    # T7, the earlier implementation — a base-target conflict is kept as a persistent attempt, but
    # never in the shared base checkout: it lives in a detached managed workspace,
    # and the base checkout stays exactly as it was (clean, same ref, same branch).
    (repo / "same.txt").write_text("main\n", encoding="utf-8")
    _git(repo, "add", "same.txt")
    _git(repo, "commit", "-m", "main change")
    main_before = _git(repo, "rev-parse", "main").stdout.strip()
    _git(repo, "checkout", "-b", "source", "HEAD~1")
    (repo / "same.txt").write_text("source\n", encoding="utf-8")
    _git(repo, "add", "same.txt")
    _git(repo, "commit", "-m", "source change")
    _git(repo, "checkout", "main")

    result = git_service.merge_branches("flowgate", "source", "main", push=False)

    assert result["status"] == "conflict"
    assert result["conflict_files"] == ["same.txt"]
    assert _git(repo, "rev-parse", "main").stdout.strip() == main_before
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert _git(repo, "branch", "--show-current").stdout.strip() == "main"
    assert not (repo / ".git" / "MERGE_HEAD").exists()
    session = git_service.db_git.get_session(result["merge_id"])
    record = git_service.db_git.session_context(session)["merge_target"]
    assert record["detached"] is True and record["is_project_base"] is False
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
    # fix #2: a direct base-checkout merge (push=False, target=base)
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
    # fix #1: the pre-merge dirty guard is include_untracked=False
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
    # fix #1 (second stage): the same untracked collision can hit
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
    # BranchMergeBody.push must reach merge_branches verbatim,
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
