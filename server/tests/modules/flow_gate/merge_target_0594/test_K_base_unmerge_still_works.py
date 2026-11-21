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
def test_K_base_unmerge_still_works(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    (proj.wt(gid) / "k.txt").write_text("k\n", encoding="utf-8")
    proj.ready(gid)
    before = proj.base_sha()
    out = svc.finalize(gid, "merge_only")
    assert out["result"]["status"] == "merged"
    assert db_git.get_state(gid)["merge_id"] is not None       # ledger points at attempt
    status = svc.project_git_status(proj.pid)["status"]
    merges = status["unpushed"]["merges"]
    assert merges and merges[0]["group_id"] == gid and merges[0]["can_unmerge"] is True
    result = svc.unmerge(gid, out["result"]["merge_commit"])
    assert result["result"]["unmerged"] is True
    assert proj.base_sha() == before
    assert db_git.get_state(gid)["status"] == "awaiting_choice"

