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
def test_E5_completed_review_left_open_is_finished_on_restart(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    tree = proj.workspace("develop") / "tree"
    _git(["checkout", "--theirs", "shared.txt"], cwd=tree)
    _git(["add", "-A"], cwd=tree)
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "commit", "--no-edit"], cwd=tree)
    commit = _git(["rev-parse", "HEAD"], cwd=tree).strip()
    ctx = _ctx(merge_id)
    ctx.update({"review_state": "completed", "apply_phase": "completed", "merge_commit": commit})
    db_git.set_session_context(merge_id, ctx)
    # the crash hit after the ledger write, before the row was closed
    db_git.set_status(gid, "merged", merge_id=merge_id, merge_commit=commit[:7])
    svc.startup_recovery()
    assert _session(merge_id)["status"] == "done"
    assert _ctx(merge_id)["attempt_state"] == "completed"
    state = db_git.get_state(gid)
    assert state["status"] == "merged" and state["merge_id"] == merge_id
    assert not proj.workspace("develop").exists()

