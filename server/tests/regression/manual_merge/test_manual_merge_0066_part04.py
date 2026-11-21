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
@pytest.mark.parametrize("case,counts", [
    ("staged", {"staged": 1}), ("unstaged", {"unstaged": 1}), ("new_normal", {"new": 1}),
    ("staged_excluded_new", {"staged": 1, "new": 1}),
    ("hidden_new", {"hidden": 1, "new": 1}),
    ("managed_hidden", {"hidden": 1, "managed": 0}),
    ("managed", {"managed": 1}),
])
def test_uncommitted_changes_block_the_merge_and_are_not_absorbed(proj, monkeypatch, case, counts):
    from modules.flow_gate.services import tr2_file_policy
    gid = group(proj, 1)
    tip = work(proj, gid, "a.txt", "a\n")
    owned = {"managed_hidden": {SECRET}, "managed": {"managed.txt"}}.get(case, set())
    monkeypatch.setattr(tr2_file_policy, "managed_paths", lambda _: owned)
    _dirty(proj, gid, case)
    error = refused(manual, gid)
    assert error.code == "uncommitted_changes"
    for key, value in counts.items():
        assert error.details["counts"][key] == value, error.details
    if case in ("hidden_new", "managed_hidden", "managed"):
        assert error.details["protected_uncommitted"] == 1
    no_hidden({"details": error.details, "message": error.message}, SECRET)
    view = _mm().merge_view(gid)
    assert view["can_merge"] is False and view["blocked"][0]["code"] == "uncommitted_changes"
    no_hidden(view, SECRET)
    assert rev(proj.wt(gid), "HEAD") == tip                 # no absorb commit
    assert "a.txt" not in files_at(proj.base, "main") and not merge_sessions(gid)
@needs_git
def test_new_excluded_artifact_outside_the_index_does_not_block_and_stays_untracked(proj):
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    (proj.wt(gid) / "debug.log").write_text("log\n", encoding="utf-8")
    out = manual(gid)
    assert out["status"] == "merged" and out["excluded_artifact_count"] == 1
    assert "debug.log" not in files_at(proj.base, "main")
    assert (proj.wt(gid) / "debug.log").exists()
    assert "debug.log" in _git(["status", "--porcelain"], cwd=proj.wt(gid))
@needs_git
def test_unreadable_ownership_removes_the_excluded_exemption_only_when_it_matters(proj, monkeypatch):
    from modules.flow_gate.services import tr2_file_policy
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")

    def unreadable(_):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(tr2_file_policy, "managed_paths", unreadable)
    (proj.wt(gid) / "debug.log").write_text("log\n", encoding="utf-8")
    error = refused(manual, gid)
    assert error.code == "uncommitted_changes" and error.details["reason"] == "ownership_unavailable"
    (proj.wt(gid) / "debug.log").unlink()
    assert manual(gid)["status"] == "merged"                # nothing uncommitted: goes on
