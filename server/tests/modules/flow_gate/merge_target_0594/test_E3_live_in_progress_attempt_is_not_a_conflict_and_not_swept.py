"""Selected finalize target: isolated scenario from the shared 0594 harness."""
from __future__ import annotations

from test_git_merge_target_0594 import (
    Path,
    Proj,
    _COUNTER,
    _Crash,
    _GIT,
    _MockDB,
    _MockTxn,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _arm_crash,
    _assert_off_branch,
    _check_view,
    _conflict_on,
    _crash_finalize,
    _ctx,
    _end_job,
    _feature_group,
    _git,
    _judge,
    _legacy_group,
    _live_job,
    _merge_head,
    _merge_rows,
    _open_attempt,
    _recover_job_after_crash,
    _refused,
    _seed_wf_done_root,
    _session,
    base64,
    check_budget,
    contextmanager,
    held_locks,
    itertools,
    json,
    needs_git,
    os,
    patch_store,
    proj,
    pytest,
    shutil,
    sqlite3,
    storage_dir,
    subprocess,
    sys,
    uuid,
)


@needs_git
def test_E3_live_in_progress_attempt_is_not_a_conflict_and_not_swept(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    gid2 = proj.group(2)
    proj.local_branch("develop")
    proj.ready(gid)
    proj.ready(gid2)
    # 0669: the live runner is the attempt job; while it is not terminal it owns the attempt
    job_id = _live_job(proj.pid, "live")
    holder = f"job:{job_id}"
    attempt = _open_attempt(proj, gid, "develop", holder)
    base_attempt = _open_attempt(proj, gid2, "main", holder)
    db_git.set_status(gid, "merging")
    session = _session(attempt.merge_id)
    assert mt.attempt_phase(session) == mt.PHASE_IN_PROGRESS
    svc.merge_session_sweep()
    assert _session(attempt.merge_id)["status"] == "open"
    assert proj.workspace("develop").exists()
    assert svc.get_finalize_state(gid)["state"]["status"] == "merging"
    assert svc.get_finalize_state(gid)["state"]["merge_id"] is None

    # a BASE attempt in flight does not report the base checkout as conflicted either
    assert mt.attempt_phase(_session(base_attempt.merge_id)) == mt.PHASE_IN_PROGRESS
    assert _session(base_attempt.merge_id)["status"] == "open"
    assert svc.open_merge_session_of_project(proj.pid) is None
    svc.guard_base_free(proj.pid)
    svc.startup_recovery()                      # a restart leaves a live job attempt alone too
    assert _session(attempt.merge_id)["status"] == "open"

    # runner gone (the job ended) -> the next sweep closes both as interrupted
    _end_job(job_id)
    svc.merge_session_sweep()
    assert _ctx(attempt.merge_id)["attempt_state"] == "interrupted"
    assert _ctx(base_attempt.merge_id)["attempt_state"] == "interrupted"
    assert not proj.workspace("develop").exists()
    assert db_git.get_state(gid)["status"] == "waiting"

