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
def test_L_internal_slot_remote_only_and_missing_targets_are_refused(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    other = proj.group(2)
    other_branch = db_git.get_state(other)["branch"]
    own_branch = db_git.get_state(gid)["branch"]
    (proj.wt(gid) / "l.txt").write_text("l\n", encoding="utf-8")
    proj.ready(gid)

    for branch, owner in ((other_branch, other), (own_branch, gid)):
        with pytest.raises(svc.GitServiceError) as caught:
            svc.finalize(gid, "merge", target_branch=branch)
        assert caught.value.code == "merge_target_internal_slot"
        assert caught.value.details["connected_group_id"] == owner

    _git(["push", "origin", "main:remote-only"], cwd=proj.seed)
    _git(["fetch", "origin"], cwd=proj.base)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="remote-only")
    assert caught.value.code == "merge_target_remote_only"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.precheck_approve_git_target(gid, "merge", "remote-only")
    assert caught.value.code == "merge_target_remote_only"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="nope")
    assert caught.value.code == "merge_target_not_found"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "push", target_branch="develop")
    assert caught.value.code == "merge_target_requires_merge"
    assert db_git.get_open_session_by_group(gid) is None
    assert db_git.get_state(gid)["status"] == "awaiting_choice"

    # the slot decision is the ledger, not the name: an UNREGISTERED branch whose
    # name looks like a slot is an ordinary local branch
    lookalike = f"{proj.pid}_default_0999"
    proj.local_branch(lookalike)
    cfg = db_git.get_config(proj.pid)
    assert mt.plan_finalize_target(gid, proj.pid, cfg, lookalike).target_branch == lookalike

