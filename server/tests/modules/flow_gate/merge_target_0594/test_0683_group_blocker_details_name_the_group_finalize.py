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
def test_0683_group_blocker_details_name_the_group_finalize(proj):
    """(9) a group finalize blocker is named as one: owner type, source, state, route."""
    from modules.flow_gate.services import git_service as svc

    gid_a = proj.group(1)
    gid_b = proj.group(2)
    proj.local_branch("develop")
    merge_a = _conflict_on(proj, gid_a, "develop")["merge_id"]
    (proj.wt(gid_b) / "b.txt").write_text("b\n", encoding="utf-8")
    proj.ready(gid_b)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid_b, "merge", target_branch="develop")
    exc = caught.value
    assert exc.code == "merge_target_busy"
    d = exc.details
    assert d["merge_id"] == merge_a and d["blocking_group_id"] == gid_a
    assert d["blocking_owner_type"] == "group"
    assert d["blocking_state"] == "conflict" and d["attempt_state"] == "conflict"
    assert d["route"] == f"/api/v1/groups/{gid_a}/git/merge/{merge_a}"
    assert d["source_branch"] == svc.db_git.get_state(gid_a)["branch"]
    assert gid_a in exc.message and "branch merge" not in exc.message

