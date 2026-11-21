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
def test_D_reject_and_re_review_keep_the_target_root(proj):
    from modules.flow_gate.services import git_service as svc

    gid = proj.group(1)
    proj.local_branch("develop")
    base_before = proj.base_sha()
    merge_id = _conflict_on(proj, gid, "develop")["merge_id"]
    ws = proj.workspace("develop") / "tree"
    original = svc.list_conflicts(gid, merge_id)["files"][0]["content"]
    svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\ndevelop version\n",
    }], True)
    rejected = svc.reject_merge_review(
        gid, merge_id, reason="다시 확인해 주세요 — 충분히 긴 사유입니다.",
        provider_id="prov", provider_pinned=True, start_run=lambda message: "aiv_fake",
    )
    assert rejected["result"]["status"] == "returned_to_resolver"
    assert svc.resolve_conflict_src_root(gid, merge_id) == ws
    assert svc.list_conflicts(gid, merge_id)["files"][0]["content"] == original
    assert (ws / "shared.txt").read_text(encoding="utf-8") == original
    assert proj.base_sha() == base_before and proj.base_clean()
    assert _ctx(merge_id)["merge_target"]["branch"] == "develop"

    second = f"develop version\n{gid} version\n"
    out = svc.resolve_conflicts(gid, merge_id, [{"path": "shared.txt", "content": second}], True)
    review = svc.get_merge_review(gid, merge_id)["result"]
    assert review["review_state"] == "resolved_pending_review"
    approved = svc.approve_merge_review(
        gid, merge_id, attempt_id=str(uuid.uuid4()),
        review_fingerprint=out["result"]["review_fingerprint"], authority="human",
    )
    assert approved["result"]["status"] == "merged"
    assert proj.origin_show("develop", "shared.txt") == second
    assert proj.origin_show("main", "shared.txt") == "line1\n"

