"""Independently selectable scenarios; shared fixtures remain in test_manual_merge_0066.py."""
from __future__ import annotations

from tests.regression.manual_merge.test_manual_merge_0066 import (
    GitServiceError,
    SECRET,
    SECRET_KO,
    _Crash,
    _ctx,
    _dirty,
    _git,
    _hidden_conflict,
    _job_of,
    _local_unrecorded_merge,
    _mm,
    _recovery_row,
    _session,
    _svc,
    branch,
    drive_jobs,
    files_at,
    git_quoted,
    group,
    held_locks,
    manual,
    merge_sessions,
    needs_git,
    no_hidden,
    patch_store,
    proj,
    pytest,
    refused,
    reject_push,
    results,
    rev,
    spy_git,
    spy_locks,
    state,
    storage_dir,
    try_rev,
    uuid,
    work,
)

@needs_git
@pytest.mark.parametrize("case,code", [
    ("ai_run", "run_already_active"), ("lease", "group_lease_active"),
    ("retry", "approval_retry_pending"), ("branch_merge", "branch_merge_claim_active"),
    ("merging", "git_busy"), ("merged", "invalid_state"), ("conflict", "invalid_state"),
    ("tr_session", "invalid_state"), ("group_update", "invalid_state"),
])
def test_technical_guards_refuse_without_side_effects(proj, monkeypatch, case, code):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import group_ai_leases
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.services.git import approval_intent
    svc = _svc()
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    if case == "ai_run":
        monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda _: True)
    elif case == "lease":
        monkeypatch.setattr(group_ai_leases, "get_active", lambda _: {"lease": 1})
    elif case == "retry":
        monkeypatch.setattr(approval_intent, "clean_retry_of_state", lambda _: {"intent": {}})
    elif case == "branch_merge":
        def claimed(_):
            raise GitServiceError(409, "branch_merge_claim_active", "claimed")
        monkeypatch.setattr(svc, "guard_group_branch_merge_free", claimed)
    elif case in ("merging", "merged", "conflict"):
        db_git.set_status(gid, case)
    else:
        kind = db_git.SESSION_KIND_TR_REVERT if case == "tr_session" else db_git.SESSION_KIND_GROUP_UPDATE
        db_git.create_session(gid, [], kind=kind, context={})
    main_before = rev(proj.base, "main")
    error = refused(manual, gid)
    assert error.code == code
    if case == "tr_session":
        assert error.details["session_kind"] == "tr_revert"
    assert rev(proj.base, "main") == main_before
    assert not [s for s in merge_sessions(gid) if (s.get("context") or "").find("manual_merge") >= 0]
    assert held_locks(proj.pid) == []
@needs_git
def test_approval_freeze_claim_leaves_the_job_queued_until_it_is_released(proj, monkeypatch):
    from modules.flow_gate.db import operation_job as db_jobs
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim",
                        lambda g: {"job_id": "approval-job", "state": "frozen", "at": "now"})
    error = refused(manual, gid)
    assert error.code == "git_busy" and error.details["queued"] is True
    assert error.details["reason_code"] == "group_frozen_for_approval"
    assert "a.txt" not in files_at(proj.base, "main")
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim", lambda g: None)
    assert drive_jobs(gid) >= 1
    assert "a.txt" in files_at(proj.base, "main") and state(gid)["status"] == "none"
    job = db_jobs.get_job(error.details["job_id"])
    assert job["status"] == "succeeded"
@needs_git
def test_job_takes_g_then_target_then_r_and_releases_everything(proj, monkeypatch):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    seen = spy_locks(monkeypatch)
    manual(gid, push=True)
    taken = [(d, t, k) for op, d, t, k, ok in (x for x in seen if x[0] == "acquire") if ok]
    assert taken[:3] == [("G", None, "finalize"), ("B", None, "publish"), ("R", None, "publish")]
    work(proj, gid, "b.txt", "b\n")
    seen.clear()
    manual(gid, target="develop", push=False)
    taken = [(d, t, k) for op, d, t, k, ok in (x for x in seen if x[0] == "acquire") if ok]
    assert taken[:3] == [("G", None, "finalize"), ("W", "develop", "publish"), ("R", None, "publish")]
    assert held_locks(proj.pid) == []
