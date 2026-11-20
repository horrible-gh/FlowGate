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
def test_E2_startup_recovery_closes_an_interrupted_attempt(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.ready(gid)
    # an attempt left by the removed project mutex (holder ``op:...``): nothing can be running
    # it any more (0669 unit 9c), so a restart closes it as interrupted
    attempt = _open_attempt(proj, gid, "develop", "op:crashed")
    db_git.set_status(gid, "merging")
    assert proj.workspace("develop").exists()
    svc.startup_recovery()
    session = _session(attempt.merge_id)
    assert session["status"] == "aborted"
    assert _ctx(attempt.merge_id)["attempt_state"] == "interrupted"
    assert db_git.get_state(gid)["status"] == "waiting"
    assert held_locks(proj.pid) == []
    assert not proj.workspace("develop").exists()

