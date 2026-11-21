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
@pytest.mark.parametrize("target", ["main", "develop"])
def test_origin_only_containment_is_caught_after_the_fetch(proj, monkeypatch, target):
    gid = group(proj, 1)
    if target != "main":
        proj.local_branch("develop")
    tip = work(proj, gid, "a.txt", "a\n")
    # origin's target already has the group's commit; the local target does not
    _git(["push", str(proj.bare), f"{branch(gid)}:{target}"], cwd=proj.base)
    local_before = rev(proj.base, target)
    calls = spy_git(monkeypatch)
    finished = []
    monkeypatch.setattr(_mm().merge_target, "finish_manual_attempt",
                        lambda *a, **k: finished.append(a))
    error = refused(manual, gid, target=None if target == "main" else target)
    assert error.code == "nothing_to_merge"
    assert error.details == {"reason": "target_synced_contains", "target_fast_forwarded": True}
    assert not any(a[:1] == ["merge"] and "--no-ff" in a for a in calls)
    assert finished == []
    rows = merge_sessions(gid)
    assert len(rows) == 1 and _ctx(rows[0]["merge_id"])["attempt_state"] == "failed"
    assert _ctx(rows[0]["merge_id"])["attempt_error"]["code"] == "nothing_to_merge"
    assert results(gid)["results"] == []
    assert state(gid)["status"] == "none" and state(gid)["worktree_registered"]
    assert rev(proj.base, target) == tip != local_before   # fast-forwarded, never rewound
    assert not proj.workspace(target).exists()
@needs_git
def test_unpushed_merge_recovery_with_push_records_its_own_row(proj, monkeypatch):
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    m = _local_unrecorded_merge(proj, gid)
    calls = spy_git(monkeypatch)
    out = manual(gid, push=True)
    assert out["status"] == "merged" and out["recovered"] is True and out["pushed"] is True
    assert rev(proj.base, "main") == m                      # no new merge commit
    assert not any(a[:1] == ["merge"] and "--no-ff" in a for a in calls)
    assert "a.txt" in proj.origin_files("main")
    ctx = _ctx(out["merge_id"])
    assert ctx["merge_target"]["recovery_merge_commit"] == m
    assert ctx["attempt_state"] == "completed" and ctx["attempt_result"]["recovered"] is True
    assert state(gid)["status"] == "none" and state(gid)["worktree_registered"]
    rows = results(gid)["results"]
    assert [r["merge_id"] for r in rows] == [out["merge_id"]] and rows[0]["merge_commit"] == m
@needs_git
def test_unpushed_merge_recovery_without_push_then_already_recorded_then_e11(proj):
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    m = _local_unrecorded_merge(proj, gid)
    out = manual(gid, push=False)
    assert out["recovered"] is True and out["pushed"] is False
    row = results(gid)["results"][0]
    assert row["merge_id"] == out["merge_id"] and row["push_available"] is True
    error = refused(manual, gid, push=True)
    assert error.code == "nothing_to_merge"
    assert error.details == {"reason": "already_recorded", "merge_id": out["merge_id"],
                             "push_via": "merge_result_push"}
    assert len(merge_sessions(gid)) == 1
    pushed = _mm().request_result_push(gid, row["merge_id"], m[:12])
    assert pushed["pushed_head"] == m and "a.txt" in proj.origin_files("main")
