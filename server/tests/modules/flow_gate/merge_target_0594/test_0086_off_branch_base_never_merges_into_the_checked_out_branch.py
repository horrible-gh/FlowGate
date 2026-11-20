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
def test_0086_off_branch_base_never_merges_into_the_checked_out_branch(proj, monkeypatch):
    """§5.3-2: target main is refused at plan time and, if the checkout is switched after
    planning, again right before the merge (attempt error keeps the details)."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    proj.local_branch("develop")
    (proj.wt(gid) / "y.txt").write_text("y\n", encoding="utf-8")
    proj.ready(gid)
    _git(["checkout", "develop"], cwd=proj.base)

    def refs() -> list[str]:
        return (_git(["rev-parse", "main", "develop"], cwd=proj.base).split()
                + _git(["rev-parse", "main", "develop"], cwd=proj.bare).split())

    before = refs()
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="main")
    _assert_off_branch(caught.value)
    assert _merge_rows(gid) == []
    assert refs() == before

    # final defense: let every plan-time check pass, as if the switch came after planning
    real_check, real_plan = mt.raise_if_base_checkout_off_branch, mt.plan_finalize_target
    planning = {"on": False}

    def plan(*args, **kwargs):
        planning["on"] = True
        try:
            return real_plan(*args, **kwargs)
        finally:
            planning["on"] = False

    def check(project_id, base_branch):
        if not planning["on"]:
            real_check(project_id, base_branch)

    monkeypatch.setattr(mt, "plan_finalize_target", plan)
    monkeypatch.setattr(mt, "raise_if_base_checkout_off_branch", check)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="main")
    _assert_off_branch(caught.value)
    rows = _merge_rows(gid)
    assert len(rows) == 1 and rows[0]["status"] == "aborted"
    error = db_git.session_context(rows[0])["attempt_error"]
    assert error["code"] == "base_checkout_branch_mismatch"
    assert error["details"]["checked_out_branch"] == "develop"
    assert refs() == before
    assert proj.base_head_branch() == "develop"           # never switched by the server

