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
def test_0098_the_candidate_budget_ends_git_and_discards_a_partial_list(proj, monkeypatch):
    import time as _time

    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt
    from modules.flow_gate.services.git.command import git_budget

    gid = proj.group(9)
    proj.local_branch("develop")
    mt.reset_check_targets()
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.01)
    # The computation has a budget of its own; here it runs out too (a host where even that
    # one is not enough), so nothing finishes behind the timed-out view
    monkeypatch.setattr(mt, "CHECK_TARGET_COMPUTE_SEC", 0.01)
    view = _check_view(proj, gid)
    assert view["merge_target_options"] == []
    assert (view["merge_target_state"]["status"], view["merge_target_state"]["code"]) == (
        "timeout", mt.CHECK_OPTIONS_TIMEOUT)
    assert view["merge_target_state"]["current"]["checked"] is False
    assert mt.settle_check_targets(10)
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 60.0)
    monkeypatch.setattr(mt, "CHECK_TARGET_COMPUTE_SEC", 60.0)
    # 0148: a timeout is now negative-cached for 5 s; explicit refresh must bypass and replace it.
    assert _check_view(proj, gid, refresh=False)["merge_target_state"]["status"] == "timeout"
    assert _check_view(proj, gid, refresh=True)["merge_target_options"] == ["develop", "main"]
    # one name's judgement failing for a reason of its own basis drops the whole list, never a partial one
    real = mt.inspect_workspace
    monkeypatch.setattr(mt, "inspect_workspace", lambda project_id, branch: (
        (_ for _ in ()).throw(RuntimeError("disk")) if branch == "develop" else real(project_id, branch)))
    broken = _check_view(proj, gid)
    assert broken["merge_target_options"] == []
    assert (broken["merge_target_state"]["status"], broken["merge_target_state"]["code"]) == (
        "failed", mt.CHECK_OPTIONS_UNAVAILABLE)
    # each Git call gets at most the time left (``subprocess.run`` kills the call it outlives); once spent, no
    # further Git call starts
    from modules.flow_gate.services.git import command
    timeouts = []

    def outlived(args, **kw):
        timeouts.append(kw["timeout"])
        _time.sleep(kw["timeout"])
        raise command.subprocess.TimeoutExpired(args, kw["timeout"])

    with monkeypatch.context() as m:
        m.setattr(command.subprocess, "run", outlived)
        with git_budget(0.3) as budget:
            assert svc._run_git(["status"], cwd=proj.base).returncode == -1
            assert len(timeouts) == 1 and 0 < timeouts[0] <= 0.3 and budget.expired
            assert svc._run_git(["status"], cwd=proj.base).stderr == "timeout_expired" and len(timeouts) == 1
    with monkeypatch.context() as m:                     # outside a budget: the call's own limit, as before
        m.setattr(command.subprocess, "run", lambda args, **kw: timeouts.append(kw["timeout"]) or
                  command.subprocess.CompletedProcess(args, 0, "", ""))
        assert svc._run_git(["status"], cwd=proj.base).returncode == 0
    assert timeouts[-1] == command.GIT_LOCAL_TIMEOUT_SEC

