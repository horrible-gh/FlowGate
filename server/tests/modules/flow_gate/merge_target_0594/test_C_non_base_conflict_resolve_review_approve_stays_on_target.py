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
def test_C_non_base_conflict_resolve_review_approve_stays_on_target(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    base_before = proj.base_sha()
    base_shared = (proj.base / "shared.txt").read_text(encoding="utf-8")
    result = _conflict_on(proj, gid, "develop")
    merge_id = result["merge_id"]
    assert result["conflict_files"] == ["shared.txt"]
    assert result["target_branch"] == "develop"
    ws = proj.workspace("develop") / "tree"
    assert svc.resolve_conflict_src_root(gid, merge_id) == ws
    listed = svc.list_conflicts(gid, merge_id)
    assert listed["target_branch"] == "develop"
    assert "<<<<<<<" in listed["files"][0]["content"]
    assert "<<<<<<<" in (ws / "shared.txt").read_text(encoding="utf-8")
    # the base checkout is untouched and not held
    assert proj.base_head_branch() == "main" and proj.base_sha() == base_before
    assert (proj.base / "shared.txt").read_text(encoding="utf-8") == base_shared
    assert proj.base_clean() and svc.base_merge_in_progress(proj.pid) is None
    svc.guard_base_free(proj.pid)                             # does not raise
    assert svc.open_merge_session_of_project(proj.pid) is None
    view = svc.get_finalize_state(gid)["state"]
    assert view["status"] == "conflict" and view["merge_id"] == merge_id
    assert view["finalize_target"]["target_branch"] == "develop"

    out = svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\ndevelop version\n",
    }], True)
    assert out["result"]["status"] == "resolved_pending_review"
    review = svc.get_merge_review(gid, merge_id)["result"]
    assert (proj.base / "shared.txt").read_text(encoding="utf-8") == base_shared
    approved = svc.approve_merge_review(
        gid, merge_id, attempt_id=str(uuid.uuid4()),
        review_fingerprint=review["review_fingerprint"], authority="human",
    )
    assert approved["result"]["status"] == "merged"
    assert approved["result"]["pushed"] is True
    assert proj.origin_show("develop", "shared.txt") == f"{gid} version\ndevelop version\n"
    assert proj.origin_show("main", "shared.txt") == "line1\n"
    assert proj.base_head_branch() == "main" and proj.base_sha() == base_before
    session = _session(merge_id)
    assert session["status"] == "done"
    assert _ctx(merge_id)["attempt_state"] == "completed"
    state = db_git.get_state(gid)
    assert state["status"] == "merged" and state["merge_id"] == merge_id
    assert not proj.workspace("develop").exists()

