"""Independently selectable scenarios; shared fixtures remain in test_manual_merge_0066.py."""
from __future__ import annotations

from tests.regression.manual_merge.test_manual_merge_0066 import (
    GitServiceError,
    SECRET,
    SECRET_KO,
    _Crash,
    _ctx,
    _dirty,
    _git,
    _hidden_conflict,
    _job_of,
    _local_unrecorded_merge,
    _mm,
    _recovery_row,
    _session,
    _svc,
    branch,
    drive_jobs,
    files_at,
    git_quoted,
    group,
    held_locks,
    manual,
    merge_sessions,
    needs_git,
    no_hidden,
    patch_store,
    proj,
    pytest,
    refused,
    reject_push,
    results,
    rev,
    spy_git,
    spy_locks,
    state,
    storage_dir,
    try_rev,
    uuid,
    work,
)

@needs_git
def test_unpushed_merge_recovery_push_rejection_never_rewinds_and_retries_the_same_merge(proj, monkeypatch):
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    m = _local_unrecorded_merge(proj, gid)
    with pytest.MonkeyPatch.context() as rejected:
        reject_push(rejected)
        error = refused(manual, gid, push=True)
    assert error.code == "push_rejected"
    assert rev(proj.base, "main") == m and state(gid)["status"] == "waiting"
    first = merge_sessions(gid)[0]
    assert _ctx(first["merge_id"])["attempt_state"] == "failed"
    assert results(gid)["results"] == []
    out = manual(gid, push=True)
    assert out["recovered"] is True and out["merge_id"] != first["merge_id"]
    assert rev(proj.base, "main") == m and "a.txt" in proj.origin_files("main")
@needs_git
def test_push_rejection_rewinds_the_fresh_merge_keeps_the_slot_and_heals_to_none(proj, monkeypatch):
    svc = _svc()
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    before = rev(proj.base, "main")
    with pytest.MonkeyPatch.context() as rejected:
        reject_push(rejected)
        error = refused(manual, gid, push=True)
    assert error.code == "push_rejected"
    assert rev(proj.base, "main") == before                 # E6 rewind of this merge only
    assert state(gid)["status"] == "waiting" and state(gid)["worktree_registered"]
    assert _ctx(merge_sessions(gid)[0]["merge_id"])["attempt_state"] == "failed"
    assert results(gid)["results"] == []
    svc.project_git_status(proj.pid)                        # self-heal (root not wf_done)
    assert state(gid)["status"] == "none"
    assert manual(gid, push=True)["pushed"] is True         # the next try just works
    assert held_locks(proj.pid) == []
@needs_git
def test_conflict_uses_the_normal_review_and_keeps_the_group(proj):
    svc = _svc()
    gid = group(proj, 1)
    (proj.wt(gid) / "shared.txt").write_text(f"{gid} version\n", encoding="utf-8")
    _git(["commit", "-am", "group edit"], cwd=proj.wt(gid))
    proj.origin_commit("main", "shared.txt", "main version\n")
    out = manual(gid, push=True)
    assert out["status"] == "conflict" and out["group_status"] == "conflict"
    assert out["worktree_kept"] is True and out["conflict_files"] == ["shared.txt"]
    merge_id = out["merge_id"]
    assert state(gid)["status"] == "conflict"
    resolved = svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\nmain version\n"}], True)
    assert resolved["result"]["status"] == "resolved_pending_review"
    review = svc.get_merge_review(gid, merge_id)["result"]
    approved = svc.approve_merge_review(gid, merge_id, attempt_id=str(uuid.uuid4()),
                                        review_fingerprint=review["review_fingerprint"],
                                        authority="human")
    assert approved["result"]["status"] == "merged" and approved["result"]["group_status"] == "none"
    assert proj.origin_show("main", "shared.txt") == f"{gid} version\nmain version\n"
    assert state(gid)["status"] == "none" and state(gid)["worktree_registered"] and proj.wt(gid).is_dir()
    ctx = _ctx(merge_id)
    assert ctx["attempt_state"] == "completed" and ctx["attempt_result"]["merge_commit_sha"]
    assert [r["merge_id"] for r in results(gid)["results"]] == [merge_id]
