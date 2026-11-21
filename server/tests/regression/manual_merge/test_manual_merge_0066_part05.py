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
def test_managed_path_under_an_exclusion_rule_still_blocks_and_pure_excluded_does_not(proj, monkeypatch):
    """Rework P1: the excluded exemption follows the final policy class. A new untracked
    ``*.log`` the TR2 ledger owns is managed and blocks (E8 shows it, E9 is 409); a plain
    excluded ``*.log`` next to it does not."""
    from modules.flow_gate.services import tr2_file_policy
    gid = group(proj, 1)
    tip = work(proj, gid, "a.txt", "a\n")
    monkeypatch.setattr(tr2_file_policy, "managed_paths", lambda _: {"generated.log"})
    wt = proj.wt(gid)
    (wt / "generated.log").write_text("owned\n", encoding="utf-8")     # managed ∩ excluded
    (wt / "debug.log").write_text("log\n", encoding="utf-8")           # pure excluded
    summary = _mm().uncommitted_summary(gid)
    assert summary["blocking"] == 1 and summary["protected_uncommitted"] == 1
    assert summary["counts"]["managed"] == 1 and summary["counts"]["excluded_new_untracked"] == 1
    view = _mm().merge_view(gid)
    assert view["can_merge"] is False
    assert [b["code"] for b in view["blocked"]] == ["uncommitted_changes"]
    assert view["uncommitted"]["counts"]["managed"] == 1
    assert view["uncommitted"]["protected_uncommitted"] == 1
    error = refused(manual, gid)
    assert error.status == 409 and error.code == "uncommitted_changes"
    assert error.details["counts"]["managed"] == 1 and error.details["protected_uncommitted"] == 1
    assert error.details["count"] == 1
    assert rev(wt, "HEAD") == tip and not merge_sessions(gid)
    (wt / "generated.log").unlink()
    out = manual(gid)                                       # only the pure excluded one is left
    assert out["status"] == "merged" and out["excluded_artifact_count"] == 1
    assert "debug.log" not in files_at(proj.base, "main") and (wt / "debug.log").exists()
@needs_git
def test_nothing_to_merge_changes_nothing(proj, monkeypatch):
    svc = _svc()
    gid = group(proj, 1)
    cleaned = []
    monkeypatch.setattr(svc, "_cleanup_group_slot", lambda *a, **k: cleaned.append(a))
    before = (state(gid), rev(proj.base, "main"))
    error = refused(manual, gid)
    assert error.code == "nothing_to_merge" and "reason" not in error.details
    assert (state(gid), rev(proj.base, "main")) == before and cleaned == [] and not merge_sessions(gid)
    assert proj.wt(gid).is_dir()
@needs_git
def test_work_base_already_containing_the_commits_is_nothing_to_merge(proj):
    gid = group(proj, 1)
    tip = work(proj, gid, "a.txt", "a\n")
    _git(["merge", "--ff-only", tip], cwd=proj.base)        # base moved onto the group's work
    error = refused(manual, gid)
    assert error.code == "nothing_to_merge" and not merge_sessions(gid)
@needs_git
def test_job_rejudges_under_its_locks_and_never_cleans_the_slot(proj, monkeypatch):
    from modules.flow_gate.services.git import finalize_publish
    svc = _svc()
    gid = group(proj, 1)
    tip = work(proj, gid, "a.txt", "a\n")
    cleaned = []
    monkeypatch.setattr(svc, "_cleanup_group_slot", lambda *a, **k: cleaned.append(a))
    real_admit = finalize_publish._admit

    def admit(ctx, p, held, wanted=None):
        real_admit(ctx, p, held, wanted)
        _git(["merge", "--ff-only", tip], cwd=proj.base)    # landed while the job waited
    monkeypatch.setattr(finalize_publish, "_admit", admit)
    error = refused(manual, gid)
    assert error.code == "nothing_to_merge"
    assert not merge_sessions(gid) and cleaned == [] and state(gid)["status"] == "none"
    assert held_locks(proj.pid) == []
