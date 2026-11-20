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
def test_0086_off_branch_base_refuses_a_non_base_target_before_any_attempt(proj, monkeypatch):
    """§5.3-1: base checkout on develop, target develop — named and omitted (work base)."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    (proj.wt(gid) / "x.txt").write_text("x\n", encoding="utf-8")
    proj.ready(gid)
    _git(["checkout", "develop"], cwd=proj.base)

    calls = (
        lambda: svc.finalize(gid, "merge", target_branch="develop"),
        lambda: svc.precheck_approve_git_target(gid, "merge", "develop"),
        lambda: svc.finalize(gid, "merge"),       # omitted → work base; never re-wrapped
    )
    monkeypatch.setattr(svc, "resolve_group_work_base_ref", lambda *_a, **_k: "develop")
    for call in calls:
        with pytest.raises(svc.GitServiceError) as caught:
            call()
        _assert_off_branch(caught.value)
    assert _merge_rows(gid) == []
    assert not proj.workspace("develop").exists()
    assert db_git.get_state(gid)["status"] == "awaiting_choice"

