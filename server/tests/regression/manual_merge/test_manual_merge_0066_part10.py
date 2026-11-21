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
def test_finalize_default_path_still_cleans_up_and_records_merged(proj):
    svc = _svc()
    gid = group(proj, 1, wf_done=True)
    work(proj, gid, "a.txt", "a\n")
    proj.ready(gid)
    out = svc.finalize(gid, "merge")
    assert out["result"]["status"] == "merged" and out["result"]["merge_id"] is None
    st = state(gid)
    assert st["status"] == "merged" and st["merge_id"] is not None
    assert "origin" not in _ctx(st["merge_id"])["merge_target"]
    assert not proj.wt(gid).exists()
@needs_git
def test_finalize_default_no_work_still_discards_the_slot(proj):
    svc = _svc()
    gid = group(proj, 1, wf_done=True)
    proj.ready(gid)
    out = svc.finalize(gid, "merge")
    assert out["result"]["status"] == "discarded" and not proj.wt(gid).exists()
    assert not merge_sessions(gid)
@needs_git
def test_finalize_default_origin_only_containment_is_unchanged(proj, monkeypatch):
    """Without ``recheck_after_sync`` finalize merges after its fetch as it always did."""
    svc = _svc()
    gid = group(proj, 1, wf_done=True)
    work(proj, gid, "a.txt", "a\n")
    _git(["push", str(proj.bare), f"{branch(gid)}:main"], cwd=proj.base)
    proj.ready(gid)
    out = svc.finalize(gid, "merge")
    assert out["result"]["status"] == "merged"
    assert state(gid)["status"] == "merged"
@needs_git
def test_manual_merge_view_lists_targets_need_and_blockers_without_changing_anything(proj):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    before = (state(gid), rev(proj.base, "main"))
    view = _mm().merge_view(gid)
    assert view["can_merge"] is True and view["blocked"] == []
    assert view["default_target"] == "main" and view["ahead_of_target"] == 1
    assert view["merge_needed"] is True and view["recovery"] is False
    names = {t["name"]: t for t in view["targets"]}
    assert names["main"]["kind"] == "base" and names["main"]["eligible"] is True
    assert names["develop"]["kind"] == "local" and names["develop"]["eligible"] is True
    assert names[branch(gid)]["blocked_reason"] == "internal_slot"
    assert view["graph"]["blocker"] is None and view["commit_count"] == 1
    assert view["preview"]["target_branch"] == "main"
    developed = _mm().merge_view(gid, "develop")
    assert developed["preview"]["target_branch"] == "develop" and developed["ahead_of_target"] == 1
    assert (state(gid), rev(proj.base, "main")) == before
    manual(gid)
    after = _mm().merge_view(gid)
    assert after["can_merge"] is False and after["blocked"][0]["code"] == "nothing_to_merge"
    assert after["blocked"][0]["details"]["reason"] == "already_recorded"
