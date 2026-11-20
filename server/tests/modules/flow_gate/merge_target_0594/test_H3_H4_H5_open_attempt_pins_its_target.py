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
def test_H3_H4_H5_open_attempt_pins_its_target(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    proj.local_branch("develop")
    proj.local_branch("release")
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    started = _ctx(merge_id)["merge_target"]["started_at"]

    # H3: open conflict + a different explicit target → refused, target unchanged
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="release")
    assert caught.value.code == "merge_target_locked"
    assert caught.value.details["target_branch"] == "develop"
    assert caught.value.details["started_at"] == started
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="main")
    assert caught.value.code == "merge_target_locked"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.precheck_approve_git_target(gid, "merge", "release")
    assert caught.value.code == "merge_target_locked"

    # H5: omitted target is not a retarget — it continues with the pinned one
    cfg = db_git.get_config(proj.pid)
    assert mt.plan_finalize_target(gid, proj.pid, cfg, None).target_branch == "develop"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge")
    assert caught.value.code == "invalid_state"
    assert caught.value.details["target_branch"] == "develop"

    # H4: resolved → open review: still pinned
    resolved = f"{gid} version\ndevelop version\n"
    out = svc.resolve_conflicts(gid, merge_id, [{"path": "shared.txt", "content": resolved}], True)
    assert out["result"]["status"] == "resolved_pending_review"
    with pytest.raises(svc.GitServiceError) as caught:
        svc.finalize(gid, "merge", target_branch="release")
    assert caught.value.code == "merge_target_locked"
    assert _ctx(merge_id)["merge_target"]["branch"] == "develop"
    approved = svc.approve_merge_review(
        gid, merge_id, attempt_id=str(uuid.uuid4()),
        review_fingerprint=out["result"]["review_fingerprint"], authority="human",
    )
    assert approved["result"]["status"] == "merged"
    assert proj.origin_show("develop", "shared.txt") == resolved
    assert proj.origin_show("release", "shared.txt") == "line1\n"

