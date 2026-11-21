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
def test_A_base_finalize_without_target_is_unchanged_and_records_attempt(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    (proj.wt(gid) / "work.txt").write_text("work\n", encoding="utf-8")
    proj.ready(gid)
    out = svc.finalize(gid, "merge")
    assert out["result"]["status"] == "merged"
    assert out["result"]["pushed"] is True
    assert out["result"]["merge_id"] is None                 # response contract unchanged
    assert out["result"]["target_branch"] == "main"
    assert "work.txt" in proj.origin_files("main")
    state = db_git.get_state(gid)
    assert state["status"] == "merged" and state["merge_id"] is not None
    session = _session(state["merge_id"])
    ctx = db_git.session_context(session)
    assert session["status"] == "done"
    assert ctx["attempt_state"] == "completed"
    assert ctx["merge_target"]["branch"] == "main"
    assert ctx["merge_target"]["is_project_base"] is True
    view = svc.get_finalize_state(gid)["state"]
    assert view["merge_id"] is None                            # still only for conflicts
    assert view["finalize_target"]["target_branch"] == "main"
    assert not proj.workspace("main").exists()

