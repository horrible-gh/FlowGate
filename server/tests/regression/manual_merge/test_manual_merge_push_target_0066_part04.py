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
@pytest.mark.parametrize("target", ["main", "develop"])
def test_push_job_holds_g_target_r_until_its_end(proj, monkeypatch, target):
    from modules.flow_gate.services import git_service as svc
    gid = group(proj, 1)
    if target != "main":
        proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target=None if target == "main" else target)
    row = _row(results(gid), target)
    seen = spy_locks(monkeypatch)
    during_push = []
    real = svc._run_git

    def run(args, **kwargs):
        if args and args[0] == "push":
            during_push.append(sorted((r["domain"], r.get("target_key") or "") for r in held_locks(proj.pid)))
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", run)
    push_result(gid, row["merge_id"], row["merge_commit"])
    taken = [(d, t, k) for op, d, t, k, ok in (x for x in seen if x[0] == "acquire") if ok]
    expected_target = ("B", None, "publish") if target == "main" else ("W", "develop", "publish")
    assert taken[:3] == [("G", None, "publish"), expected_target, ("R", None, "publish")]
    domains = {d for d, _ in during_push[0]}
    assert {"G", "R", "B" if target == "main" else "W"} <= domains
    assert held_locks(proj.pid) == []
@needs_git
def test_job_rejects_a_target_moved_while_it_waited(proj):
    from modules.flow_gate.db import operation_job as db_jobs
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    out = manual(gid, target="develop")
    sha = _ctx(out["merge_id"])["attempt_result"]["merge_commit_sha"]
    row, job_id = _queued_push(proj, gid, "develop")
    _git(["update-ref", "refs/heads/develop", f"{sha}^1"], cwd=proj.base)
    drive_jobs(gid)
    job = db_jobs.get_job(job_id)
    assert job["status"] == "failed" and job["last_error_code"] == "merge_target_moved"
    assert "a.txt" not in proj.origin_files("develop")
    assert held_locks(proj.pid) == []
@needs_git
def test_job_refuses_superseded_base_changed_and_conflict_after_waiting(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.services.git import merge_target
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row, job_id = _queued_push(proj, gid, "develop")
    work(proj, gid, "b.txt", "b\n")
    newer = manual(gid, target="develop")                   # completes while the push waits
    drive_jobs(gid)
    job = db_jobs.get_job(job_id)
    assert job["status"] == "failed" and job["last_error_code"] == "stale_target"
    assert "a.txt" not in proj.origin_files("develop")
    row, job_id = _queued_push(proj, gid, "develop")
    assert row["merge_id"] == newer["merge_id"]
    monkeypatch.setattr(merge_target, "project_base_branch", lambda cfg: "develop")
    drive_jobs(gid)
    assert db_jobs.get_job(job_id)["last_error_code"] == "stale_target"
    monkeypatch.undo()
    row, job_id = _queued_push(proj, gid, "develop")
    db_git.set_status(gid, "conflict")
    drive_jobs(gid)
    assert db_jobs.get_job(job_id)["last_error_code"] == "invalid_state"
    db_git.set_status(gid, "none")
    assert "a.txt" not in proj.origin_files("develop")
@needs_git
def test_another_target_completing_meanwhile_does_not_stop_the_push(proj):
    from modules.flow_gate.db import operation_job as db_jobs
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row, job_id = _queued_push(proj, gid, "develop")
    release_before = _origin(proj, "release")
    work(proj, gid, "b.txt", "b\n")
    manual(gid, target="release")                           # B completes while A waits
    drive_jobs(gid)
    assert db_jobs.get_job(job_id)["status"] == "succeeded"
    assert _origin(proj, "develop") == row["merge_commit"]
    assert _origin(proj, "release") == release_before
    assert _row(results(gid), "release")["push_available"] is True
