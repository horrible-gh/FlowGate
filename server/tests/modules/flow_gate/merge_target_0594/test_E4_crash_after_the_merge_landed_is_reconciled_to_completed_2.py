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
@pytest.mark.parametrize('action,where,recover', [('merge', 'before_close', 'startup')])
def test_E4_crash_after_the_merge_landed_is_reconciled_to_completed(
    proj, monkeypatch, action, where, recover,
):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    (proj.wt(gid) / "feature.txt").write_text("feature\n", encoding="utf-8")
    proj.ready(gid)
    _crash_finalize(proj, monkeypatch, gid, action, where=where)
    session = db_git.get_open_session_by_group(gid)
    assert session is not None                               # the crash left it open
    merge_id = int(session["merge_id"])
    landed = _git(["rev-parse", "develop"], cwd=proj.base).strip()
    assert "feature.txt" in _git(["ls-tree", "-r", "--name-only", "develop"], cwd=proj.base).split()
    # 0669 unit 9a: the finalize job owns the attempt while its lease is valid, so a restart or
    # a periodic sweep must not take it away ...
    if recover == "startup":
        svc.startup_recovery()
    else:
        svc.merge_session_sweep()
    assert _session(merge_id)["status"] == "open"
    # ... the Sweeper reconciles the job once its lease lapsed, and its next claim settles it
    job = _recover_job_after_crash(gid)
    assert job["status"] == "succeeded", job
    ctx = _ctx(merge_id)
    assert _session(merge_id)["status"] == "done"
    assert ctx["attempt_state"] == "completed"               # never "interrupted"
    assert ctx["attempt_result"]["pushed"] is (action == "merge")
    assert ctx["attempt_result"]["recovered"] is True
    state = db_git.get_state(gid)
    assert state["status"] == "merged" and state["merge_id"] == merge_id
    assert _git(["rev-parse", "develop"], cwd=proj.base).strip() == landed   # not undone
    assert ("feature.txt" in proj.origin_files("develop")) is (action == "merge")
    assert not proj.workspace("develop").exists()
    assert proj.base_head_branch() == "main"
    assert held_locks(proj.pid) == []

