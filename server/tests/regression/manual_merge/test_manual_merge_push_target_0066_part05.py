"""Independently selectable scenarios; shared fixtures remain in test_manual_merge_push_target_0066.py."""
from __future__ import annotations

from tests.regression.manual_merge.test_manual_merge_push_target_0066 import (
    GitServiceError,
    _Crash,
    _commit_on,
    _ctx,
    _git,
    _mm,
    _origin,
    _queued_push,
    _recover,
    _row,
    _two_targets,
    branch,
    drive_jobs,
    group,
    held_locks,
    manual,
    needs_git,
    patch_store,
    proj,
    push_result,
    pytest,
    refused,
    reject_push,
    results,
    rev,
    spy_git,
    spy_locks,
    state,
    storage_dir,
    threading,
    try_rev,
    work,
)

@needs_git
def test_a_merge_of_another_target_waits_for_the_running_push(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    work(proj, gid, "b.txt", "b\n")
    row = _row(results(gid), "develop")
    outcome = {}
    real = svc._run_git

    def run(args, **kwargs):
        if args and args[0] == "push" and "develop" in args[-1] and "merge" not in outcome:
            def other():
                try:
                    outcome["merge"] = manual(gid, target="release")
                except GitServiceError as exc:
                    outcome["merge"] = exc
            t = threading.Thread(target=other)
            t.start()
            t.join(60)
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", run)
    push_result(gid, row["merge_id"], row["merge_commit"])
    assert isinstance(outcome["merge"], GitServiceError) and outcome["merge"].code == "git_busy"
    assert "b.txt" not in _git(["ls-tree", "-r", "--name-only", "release"], cwd=proj.base).split()
    monkeypatch.undo()
    drive_jobs(gid)
    assert "b.txt" in _git(["ls-tree", "-r", "--name-only", "release"], cwd=proj.base).split()
    assert _origin(proj, "develop") == row["merge_commit"]
@needs_git
def test_frozen_group_blocks_the_push_job_until_released(proj, monkeypatch):
    from modules.flow_gate.db import operation_job as db_jobs
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row = _row(results(gid), "develop")
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim",
                        lambda g: {"job_id": "approval", "state": "frozen", "at": "now"})
    error = refused(push_result, gid, row["merge_id"], row["merge_commit"])
    assert error.code == "git_busy" and error.details["reason_code"] == "group_frozen_for_approval"
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim", lambda g: None)
    drive_jobs(gid)
    assert _origin(proj, "develop") == row["merge_commit"]
@needs_git
@pytest.mark.parametrize("case", ["keeps_h1", "moved_off_h1", "pushed"])
def test_resume_pushes_only_the_pinned_head(proj, monkeypatch, case):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import finalize_publish
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row = _row(results(gid), "develop")
    h1 = rev(proj.base, "develop")
    with pytest.MonkeyPatch.context() as patch:
        if case == "pushed":
            def finish(ctx, result):
                raise _Crash()
            patch.setattr(finalize_publish, "_finish", finish)
        else:
            real = svc._run_git

            def run(args, **kwargs):
                if args and args[0] == "push":
                    raise _Crash()
                return real(args, **kwargs)
            patch.setattr(svc, "_run_git", run)
        with pytest.raises(_Crash):
            push_result(gid, row["merge_id"], row["merge_commit"])
    if case == "keeps_h1":
        extra = _commit_on(proj, "develop", "later work")    # the target moves on, above h1
    elif case == "moved_off_h1":
        _git(["update-ref", "refs/heads/develop", f"{h1}^1"], cwd=proj.base)
        _commit_on(proj, "develop", "other history")
    pushes = []
    real = svc._run_git

    def count(args, **kwargs):
        if args and args[0] == "push":
            pushes.append(list(args))
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", count)
    job = _recover(gid)
    if case == "keeps_h1":
        assert job["status"] == "succeeded", job
        assert _origin(proj, "develop") == h1 != extra
        assert pushes == [["push", "origin", f"{h1}:refs/heads/develop"]]
    elif case == "moved_off_h1":
        assert job["status"] == "failed" and job["last_error_code"] == "merge_target_moved"
        assert pushes == [] and "a.txt" not in proj.origin_files("develop")
    else:
        assert job["status"] == "succeeded" and job["phase"] == "done" and pushes == []
        assert _origin(proj, "develop") == h1
    assert held_locks(proj.pid) == []
@needs_git
def test_push_rejection_is_a_500_and_changes_nothing(proj, monkeypatch):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row = _row(results(gid), "develop")
    reject_push(monkeypatch)
    error = refused(push_result, gid, row["merge_id"], row["merge_commit"])
    assert error.status == 500 and error.code == "push_rejected"
    assert "a.txt" not in proj.origin_files("develop")
    monkeypatch.undo()
    assert push_result(gid, row["merge_id"], row["merge_commit"])["pushed_head"]   # a retry is a new job
@needs_git
def test_routes_validate_and_answer(proj, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    allowed = {"ok": True}
    monkeypatch.setattr(git_routes, "_has_permission", lambda *a: allowed["ok"])
    client = TestClient(app, raise_server_exceptions=False)
    base = f"/api/v1/groups/{gid}/git"
    assert client.get(base + "/manual-merge").json()["result"]["can_merge"] is True
    assert client.post(base + "/manual-merge", json={"push": "yes"}).status_code == 422
    assert client.post(base + "/manual-merge", json={"bogus": 1}).status_code == 422
    merged = client.post(base + "/manual-merge", json={"push": True}).json()["result"]
    assert merged["status"] == "merged" and merged["group_status"] == "none"
    view = client.get(base + "/merge-result").json()["result"]
    assert view["results"][0]["merge_id"] == merged["merge_id"]
    missing = client.post(base + "/merge-result/push", json={})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "invalid_request"
    again = client.post(base + "/manual-merge", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "nothing_to_merge"
    allowed["ok"] = False
    assert client.post(base + "/manual-merge", json={}).status_code == 403
    assert client.post(base + "/merge-result/push", json={"merge_id": 1, "merge_commit": "a" * 7}).status_code == 403
    assert client.get(base + "/merge-result").status_code == 403
