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
def test_0086_open_attempt_keeps_its_pinned_target_off_branch(proj):
    """§5.3-3: the open attempt's pinned target (fixed path) is returned before the check."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.local_branch("release")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    _git(["checkout", "release"], cwd=proj.base)
    cfg = db_git.get_config(proj.pid)
    fixed = mt.plan_finalize_target(gid, proj.pid, cfg, None)
    assert fixed.target_branch == "develop" and fixed.merge_id == merge_id

