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
def test_E1_startup_recovery_refinds_the_deterministic_workspace(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    svc.startup_recovery()
    assert _session(merge_id)["status"] == "open"
    state = db_git.get_state(gid)
    assert state["status"] == "conflict" and state["merge_id"] == merge_id
    ws = proj.workspace("develop") / "tree"
    assert svc.resolve_conflict_src_root(gid, merge_id) == ws
    assert "<<<<<<<" in svc.list_conflicts(gid, merge_id)["files"][0]["content"]
    # periodic sweep inside the TTL also leaves it alone
    svc.merge_session_sweep()
    assert _session(merge_id)["status"] == "open"
    svc.abort_merge(gid, merge_id)
    assert _ctx(merge_id)["attempt_state"] == "aborted"
    assert db_git.get_state(gid)["status"] == "waiting"
    assert not proj.workspace("develop").exists()

