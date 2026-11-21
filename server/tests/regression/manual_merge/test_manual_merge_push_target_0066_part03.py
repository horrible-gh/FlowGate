"""Independently selectable scenarios; shared fixtures remain in test_manual_merge_push_target_0066.py."""
from __future__ import annotations

from tests.regression.manual_merge.test_manual_merge_push_target_0066 import (
    GitServiceError,
    _Crash,
    _commit_on,
    _ctx,
    _git,
    _mm,
    _origin,
    _queued_push,
    _recover,
    _row,
    _two_targets,
    branch,
    drive_jobs,
    group,
    held_locks,
    manual,
    needs_git,
    patch_store,
    proj,
    push_result,
    pytest,
    refused,
    reject_push,
    results,
    rev,
    spy_git,
    spy_locks,
    state,
    storage_dir,
    threading,
    try_rev,
    work,
)

@needs_git
def test_a_completed_push_does_not_pin_a_later_request_after_the_remote_rewinds(proj, monkeypatch):
    """Rework P2: merge_only -> E11 succeeds -> origin rewound below M -> E12 -> E10 pushable
    -> the same merge_id is a new job that fetches, re-validates and pushes again."""
    from modules.flow_gate.services.git import remote_refresh
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row = _row(results(gid), "develop")
    sha = row["merge_commit"]
    first = push_result(gid, row["merge_id"], sha)
    assert first["already_published"] is False and _origin(proj, "develop") == sha
    _git(["update-ref", "refs/heads/develop", f"{sha}^1"], cwd=proj.bare)    # origin rewound
    fetched = _row(remote_refresh.fetch_refs(proj.pid, gid)["group"], "develop")
    assert fetched["push_available"] is True and fetched["remote"]["contains_merge_commit"] is False
    assert _row(results(gid), "develop")["push_available"] is True
    seen = spy_git(monkeypatch)
    again = push_result(gid, row["merge_id"], sha)
    assert again["job_id"] != first["job_id"] and again["already_published"] is False
    assert _origin(proj, "develop") == sha
    assert ["push", "origin", f"{sha}:refs/heads/develop"] in seen
    assert any(a[:3] == ["fetch", "--prune", "origin"] for a in seen)
    # with origin intact, a repeat is again a new run that ends without a push
    seen.clear()
    third = push_result(gid, row["merge_id"], sha)
    assert third["job_id"] not in (first["job_id"], again["job_id"])
    assert third["already_published"] is True and not any(a[:1] == ["push"] for a in seen)
    assert held_locks(proj.pid) == []
@needs_git
def test_duplicate_requests_while_a_push_waits_share_one_job(proj):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row, job_id = _queued_push(proj, gid, "develop")
    _, again = _queued_push(proj, gid, "develop")
    assert again == job_id
    drive_jobs(gid)
    assert _origin(proj, "develop") == row["merge_commit"]
@needs_git
@pytest.mark.parametrize("fetch_first", [True, False])
def test_a_remote_branch_deleted_after_the_push_is_missing_and_recreated(proj, fetch_first):
    """Rework P2: fetch.prune=false, E9 push=true, then origin deletes the branch. E12/E10
    show remote_branch_missing and a pushable row; E11 (with or without that Fetch first)
    re-creates the branch instead of answering already_published from a stale ref."""
    from modules.flow_gate.services.git import remote_refresh
    gid = group(proj, 1)
    proj.local_branch("develop")
    _git(["config", "fetch.prune", "false"], cwd=proj.base)
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop", push=True)
    row = _row(results(gid), "develop")
    assert row["push_blocked_reason"] == "already_published"
    _git(["update-ref", "-d", "refs/heads/develop"], cwd=proj.bare)
    assert _git(["ls-remote", str(proj.bare), "refs/heads/develop"], cwd=proj.base).strip() == ""
    if fetch_first:
        fetched = _row(remote_refresh.fetch_refs(proj.pid, gid)["group"], "develop")
        assert fetched["remote"]["remote_branch_missing"] is True
        assert fetched["push_available"] is True and fetched["push_blocked_reason"] is None
        assert try_rev(proj.base, "refs/remotes/origin/develop") is None
        view = _row(results(gid), "develop")
        assert view["remote"]["remote_branch_missing"] is True and view["push_available"] is True
    pushed = push_result(gid, row["merge_id"], row["merge_commit"])
    assert pushed["already_published"] is False
    assert _origin(proj, "develop") == row["merge_commit"]
    assert held_locks(proj.pid) == []
@needs_git
def test_no_record_means_an_empty_result(proj):
    gid = group(proj, 1)
    view = results(gid)
    assert view == {"group_status": "none", "results": [], "push_candidates": 0, "group_blocker": None}
