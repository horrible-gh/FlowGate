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
def test_0098_the_judgement_reads_a_legacy_group_without_recording_anything(proj, check_budget):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services import git_service as svc

    gid = _legacy_group(proj, 58)
    proj.local_branch("develop")

    def rows():
        return db_groups.get_by_id(gid), db_git.get_state(gid), db_git.list_work_base_log(gid)

    before = rows()
    assert before[0]["work_base_ref"] is None and before[1]["work_base_state"] is None
    view = _check_view(proj, gid, "develop", refresh=False)
    # flowgate.v02test.0020 rev2: the list reads no floor; develop is listed and refused when saved (below)
    assert view["merge_target_options"] == ["develop", "main"]
    assert view["default_target_branch"] == "main"
    assert view["merge_target_state"]["current"]["error"] is None
    assert _judge(proj, gid, "main")["is_work_base"] is True
    assert _refused(proj, gid, "develop").code == "merge_target_preview_unavailable"   # no floor: cannot be told
    assert rows() == before                                  # no floor, no audit row, no work base pin
    # an ordinary scope reader's legacy recovery is what records it (a floor, an audit row, the work base pin) ...
    svc.resolve_scope_floor(proj.pid, gid, proj.wt(gid))
    assert db_groups.get_by_id(gid)["work_base_ref"] == "main"
    assert len(db_git.list_work_base_log(gid)) == len(before[2]) + 1
    # ... and only with a verified floor is a target other than the work base told
    assert _judge(proj, gid, "develop")["incoming_work_base_commits"] == 0
    assert _check_view(proj, gid)["merge_target_options"] == ["develop", "main"]

