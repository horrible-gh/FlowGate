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
def test_E4_crash_before_the_push_rolls_the_local_merge_back(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    (proj.wt(gid) / "feature.txt").write_text("feature\n", encoding="utf-8")
    proj.ready(gid)
    _crash_finalize(proj, monkeypatch, gid, "merge", where="before_push")
    session = db_git.get_open_session_by_group(gid)
    merge_id = int(session["merge_id"])
    inputs = _ctx(merge_id)["merge_inputs"]
    assert _git(["rev-parse", "develop"], cwd=proj.base).strip() != inputs["pre_head"]
    assert db_git.get_state(gid)["status"] == "merging"
    svc.startup_recovery()                                   # the live job keeps its attempt
    assert _session(merge_id)["status"] == "open"
    # the job's recovery claim: the unpushed merge commit is undone (never reported as a
    # result), the attempt is closed as interrupted, and the same job runs the finalize again
    job = _recover_job_after_crash(gid)
    assert job["status"] == "succeeded", job
    assert _ctx(merge_id)["attempt_state"] == "interrupted"
    assert _git(["rev-parse", "develop^1"], cwd=proj.base).strip() == inputs["pre_head"]
    assert db_git.get_state(gid)["status"] == "merged"
    assert "feature.txt" in proj.origin_files("develop")
    assert not proj.workspace("develop").exists()
    assert held_locks(proj.pid) == []

