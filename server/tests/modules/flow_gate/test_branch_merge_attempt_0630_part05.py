"""Independently selectable scenarios; shared fixtures remain in test_branch_merge_attempt_0630.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_branch_merge_attempt_0630 import (
    Path,
    Proj,
    _COUNTER,
    _Crash,
    _GIT,
    _MockDB,
    _MockTxn,
    _OTHER_GROUP,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _approve,
    _busy,
    _client,
    _crashed_branch_merge,
    _ctx,
    _end_attempt_job,
    _git,
    _plan_finalize,
    _prepare_route_conflict,
    _resolve_all,
    _review_pending,
    _session,
    _view,
    base64,
    contextmanager,
    copy,
    itertools,
    mock_db,
    os,
    proj,
    pytest,
    pytestmark,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    tempfile,
    uuid,
)

def test_14_stale_remote_is_not_overwritten(proj):
    p = proj
    out, _before = p.conflict(push=True, publish=True)
    merge_id = out["merge_id"]
    _resolve_all(p, merge_id)
    # someone pushes to origin/develop while the review waits
    _git(["fetch", "origin"], cwd=p.seed)
    _git(["checkout", "-B", "develop", "origin/develop"], cwd=p.seed)
    (p.seed / "remote-only.txt").write_text("remote\n", encoding="utf-8")
    _git(["add", "-A"], cwd=p.seed)
    _git(["commit", "-m", "remote change"], cwd=p.seed)
    _git(["push", "origin", "develop"], cwd=p.seed)
    remote_head = p.remote_sha("develop")
    result = _approve(p, merge_id)
    assert result["status"] in ("re_review", "reconciling"), result
    ctx = _ctx(merge_id)
    assert ctx["review_state"] in ("re_review", "reconciling")
    # the remote still carries the other person's commit (or a descendant of it)
    now = p.remote_sha("develop")
    assert now == remote_head or _git(["merge-base", "--is-ancestor", remote_head, now],
                                      cwd=p.bare, check=False) == ""
def test_15_abort_restores_and_cleans(proj):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, before = p.conflict()
    merge_id = out["merge_id"]
    result = svc.abort_merge(None, merge_id, project_id=p.pid)["result"]
    assert result["status"] == "aborted" and result["workspace_cleaned"] is True
    assert _session(merge_id)["status"] == "aborted"
    assert _ctx(merge_id)["attempt_state"] == "aborted"
    assert p.sha("refs/heads/develop") == before
    assert not (p.workspace("develop") / "tree").exists()
    assert _view(p, merge_id)["state"] == "aborted"
    # the same target can be merged again right away
    again = svc.merge_branches(p.pid, "feature", "develop", push=False)
    assert again["status"] == "conflict" and again["merge_id"] != merge_id
def test_16_restart_recovery_keeps_a_matching_attempt_and_flags_a_broken_one(proj):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import branch_merge
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    assert branch_merge.verify_open_attempt(_session(merge_id), at_boot=True) == "ok"
    # the periodic sweep never TTL-aborts a branch merge
    svc.merge_session_sweep(sessions=[_session(merge_id)])
    assert _session(merge_id)["status"] == "open"
    assert _view(p, merge_id)["state"] == "conflict"
    # the workspace loses its MERGE_HEAD behind FlowGate's back
    _git(["merge", "--abort"], cwd=p.workspace("develop") / "tree")
    assert branch_merge.verify_open_attempt(_session(merge_id), at_boot=True) == "interrupted"
    assert _session(merge_id)["status"] == "open"            # never read as success
    assert _view(p, merge_id)["state"] == "interrupted"
    # abort/retry path stays available
    assert svc.abort_merge(None, merge_id, project_id=p.pid)["result"]["status"] == "aborted"
def test_16b_startup_recovery_scan_handles_branch_merges(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    p = proj
    out, _before = p.conflict()
    merge_id = out["merge_id"]
    monkeypatch.setattr(svc, "_start_sweep_daemon", lambda: None)
    svc.startup_recovery()
    assert _session(merge_id)["status"] == "open"
    assert _view(p, merge_id)["state"] == "conflict"
def test_16c_crash_after_a_landed_base_merge_is_applied_on_recovery(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import branch_merge, merge_target
    p = proj
    p.branch("feature")
    p.commit_on("feature", {"feature.txt": "feature\n"})
    main_before = p.sha("refs/heads/main")
    real_run_git = svc._run_git

    def crashing_run_git(args, cwd=None, **kwargs):
        if args[:2] == ["merge", "--ff-only"] and not str(args[2]).startswith("origin/") \
                and Path(cwd) == p.base:
            raise _Crash()
        return real_run_git(args, cwd=cwd, **kwargs)

    monkeypatch.setattr(svc, "_run_git", crashing_run_git)
    monkeypatch.setattr(merge_target, "release_workspace", lambda ctx: False)
    with pytest.raises(_Crash):
        svc.merge_branches(p.pid, "feature", "main", push=False)
    monkeypatch.undo()
    from modules.flow_gate.db import git_integration as db_git
    session = db_git.list_branch_merge_sessions(p.pid)[0]
    assert _ctx(session["merge_id"])["attempt_state"] == "in_progress"
    assert p.sha("refs/heads/main") == main_before               # the crash left main behind

    # 0669 unit 8a: the attempt belongs to its branch_merge_publish job (holder ``job:{id}``),
    # which is still running while its lease is valid -- boot recovery leaves it alone.
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, job_sweeper
    assert merge_target.attempt_phase(session, at_boot=True) == merge_target.PHASE_IN_PROGRESS
    job_id = merge_target.target_record(session)["lock_holder"][len(merge_target.JOB_HOLDER_PREFIX):]
    assert db_jobs.get_job(job_id)["status"] == "running"
    # the process is gone: its lease expires, the Sweeper reconciles the job, and the job's
    # next claim settles the attempt (landed merge -> applied) under the target's locks.
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET lease_until = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job_id])
    assert job_sweeper.step_expired_leases(full=True) >= 1
    claim = job_runner.try_claim(job_id)
    assert claim is not None, db_jobs.get_job(job_id)
    job_runner.execute(claim)
    assert db_jobs.get_job(job_id)["status"] == "succeeded"
    merged = _session(session["merge_id"])
    assert merged["status"] == "done"
    result = _ctx(session["merge_id"])["attempt_result"]
    assert result["recovered"] is True and result["local_applied"] is True
    assert p.sha("refs/heads/main") != main_before
    assert (p.base / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert not (p.workspace("main") / "tree").exists()
