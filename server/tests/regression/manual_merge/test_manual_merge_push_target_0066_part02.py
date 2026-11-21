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
def test_evidence_must_be_a_completed_manual_merge_of_this_group(proj):
    from modules.flow_gate.services import git_service as svc
    gid = group(proj, 1)
    other = group(proj, 2)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    work(proj, other, "o.txt", "o\n")
    mine = manual(gid, target="develop")
    theirs = manual(other, target="develop")
    sha = _ctx(mine["merge_id"])["attempt_result"]["merge_commit_sha"]
    assert refused(push_result, gid, 999999, sha).code == "merge_result_missing"
    assert refused(push_result, gid, theirs["merge_id"], sha).code == "merge_result_missing"
    # a finalize attempt (no origin) and a failed manual attempt are not evidence either
    fin = group(proj, 3, wf_done=True)
    work(proj, fin, "f.txt", "f\n")
    proj.ready(fin)
    svc.finalize(fin, "merge_only", target_branch="develop")
    fin_id = state(fin)["merge_id"]
    assert refused(push_result, fin, fin_id, "0" * 40).code == "merge_result_missing"
    assert refused(push_result, gid, None, sha).code == "invalid_request"
    assert refused(push_result, gid, mine["merge_id"], None).code == "invalid_request"
    assert refused(push_result, gid, mine["merge_id"], "xyz").code == "invalid_request"
    assert refused(push_result, gid, True, sha).code == "invalid_request"
    error = refused(push_result, gid, mine["merge_id"], "abcdef1234")
    assert error.code == "stale_target" and error.details["reason"] == "merge_commit_mismatch"
@needs_git
def test_failed_manual_attempt_is_not_evidence(proj, monkeypatch):
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    with pytest.MonkeyPatch.context() as rejected:
        reject_push(rejected)
        refused(manual, gid, push=True)
    from manual_merge_0066_support import merge_sessions
    failed = merge_sessions(gid)[0]
    assert _ctx(failed["merge_id"])["attempt_state"] == "failed"
    assert results(gid)["results"] == []
    assert refused(push_result, gid, failed["merge_id"], "a" * 40).code == "merge_result_missing"
@needs_git
def test_moved_target_open_session_and_busy_workspace_refuse_the_push(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import merge_target
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    out = manual(gid, target="develop")
    row = _row(results(gid), "develop")
    monkeypatch.setattr(merge_target, "inspect_workspace", lambda *a: ("busy", {"merge_id": None}))
    assert refused(push_result, gid, row["merge_id"], row["merge_commit"]).code == "merge_target_busy"
    monkeypatch.undo()
    session = db_git.create_session(gid, [], kind=db_git.SESSION_KIND_TR_REVERT, context={})
    view = results(gid)
    assert view["group_blocker"] == "tr_revert" and view["push_candidates"] == 0
    assert _row(view, "develop")["push_blocked_reason"] == "group_blocked"
    error = refused(push_result, gid, row["merge_id"], row["merge_commit"])
    assert error.code == "invalid_state" and error.details["session_kind"] == "tr_revert"
    db_git.close_session(session, "aborted")
    sha = _ctx(out["merge_id"])["attempt_result"]["merge_commit_sha"]
    _git(["update-ref", "refs/heads/develop", f"{sha}^1"], cwd=proj.base)
    assert _row(results(gid), "develop")["push_blocked_reason"] == "target_moved"
    assert refused(push_result, gid, row["merge_id"], row["merge_commit"]).code == "merge_target_moved"
    assert "a.txt" not in proj.origin_files("develop")
@needs_git
def test_already_published_and_rewound_remote(proj):
    from modules.flow_gate.services.git import remote_refresh
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    out = manual(gid, target="develop", push=True)
    row = _row(results(gid), "develop")
    assert row["pushed"] is True and row["push_available"] is False
    sha = row["merge_commit"]
    # someone rewinds origin: the pushed result becomes pushable again (after a fetch)
    _git(["update-ref", "refs/heads/develop", f"{sha}^1"], cwd=proj.bare)
    remote_refresh.fetch_refs(proj.pid)
    again = _row(results(gid), "develop")
    assert again["push_available"] is True and again["pushed"] is True
    pushed = push_result(gid, out["merge_id"], sha)
    assert pushed["already_published"] is False and _origin(proj, "develop") == sha
    # origin already has it (pushed from elsewhere): no push, already_published
    work(proj, gid, "b.txt", "b\n")
    nxt = manual(gid, target="develop")
    nsha = _ctx(nxt["merge_id"])["attempt_result"]["merge_commit_sha"]
    _git(["push", str(proj.bare), f"{nsha}:refs/heads/develop"], cwd=proj.base)
    done = push_result(gid, nxt["merge_id"], nsha)
    assert done["already_published"] is True
