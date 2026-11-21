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
def test_I_legacy_session_without_target_falls_back_to_base(proj):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt

    gid = proj.group(1)
    merge_id = _conflict_on(proj, gid, "main")["merge_id"]
    ctx = _ctx(merge_id)
    for key in ("merge_target", "target_branch", "attempt_state"):
        ctx.pop(key, None)
    db_git.set_session_context(merge_id, ctx)          # = a row written before session targets existed

    resolved = mt.resolve_session_target(_session(merge_id))
    assert resolved.legacy and resolved.is_project_base and resolved.target_branch == "main"
    assert svc.resolve_conflict_src_root(gid, merge_id) == proj.base
    assert svc.open_merge_session_of_project(proj.pid)["merge_id"] == merge_id
    legacy = f"{gid} version\nmain version\n"
    out = svc.resolve_conflicts(gid, merge_id, [{"path": "shared.txt", "content": legacy}], True)
    review = svc.get_merge_review(gid, merge_id)["result"]
    approved = svc.approve_merge_review(
        gid, merge_id, attempt_id=str(uuid.uuid4()),
        review_fingerprint=review["review_fingerprint"], authority="human",
    )
    assert approved["result"]["status"] == "merged"
    assert proj.origin_show("main", "shared.txt") == legacy
    assert out["result"]["status"] == "resolved_pending_review"

