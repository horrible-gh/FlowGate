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

def test_public_conflict_redacts_every_spelling_and_keeps_visible_lines():
    """Rework 2: a Git-quoted hidden line is counted and every spelling of it (the line, its
    body, the real path, ``\\`` separators) is gone from nested fields; a visible quoted
    line keeps finalize's form in ``conflict_files``."""
    quoted = git_quoted(SECRET_KO)
    visible = git_quoted("docs/\ud55c.txt")
    assert _mm().policy.hidden(quoted) is False and _mm().policy.hidden(SECRET_KO) is True
    out = _mm()._public_conflict({
        "status": "conflict", "conflict_files": [visible, quoted, "shared.txt"],
        "message": f"conflict in {quoted}",
        "checkpoint_recovery": {"paths": [quoted.strip('"'), SECRET_KO.replace("/", "\\")],
                                SECRET_KO: True},
    })
    assert out["conflict_files"] == [visible, "shared.txt"] and out["hidden_conflict_count"] == 1
    no_hidden(out, SECRET_KO)
    assert out["message"] == "conflict in <hidden>"
@needs_git
def test_restart_after_a_landed_manual_merge_completes_it_and_keeps_the_group(proj, monkeypatch):
    from test_git_merge_target_0594 import _recover_job_after_crash
    mt = _mm().merge_target
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    real_finish = mt.finish_manual_attempt
    armed = {"on": True}

    def crash(*a, **k):
        if armed["on"]:
            armed["on"] = False
            raise _Crash()
        return real_finish(*a, **k)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mt, "fail_attempt", lambda *a, **k: None)
        patch.setattr(mt, "finish_manual_attempt", crash)
        with pytest.raises(_Crash):
            manual(gid, push=False)
    session = merge_sessions(gid)[0]
    assert session["status"] == "open" and state(gid)["status"] == "merging"
    landed = rev(proj.base, "main")
    job = _recover_job_after_crash(gid)
    assert job["status"] == "succeeded", job
    ctx = _ctx(session["merge_id"])
    assert ctx["attempt_state"] == "completed" and ctx["attempt_result"]["recovered"] is True
    assert rev(proj.base, "main") == landed
    assert state(gid)["status"] == "none" and state(gid)["worktree_registered"] and proj.wt(gid).is_dir()
    assert held_locks(proj.pid) == []
@needs_git
@pytest.mark.parametrize("case", ["local_lost", "no_push", "pushed", "not_pushed", "remote_unreadable",
                                  "remote_deleted"])
def test_recovery_row_restart_table(proj, monkeypatch, case):
    from modules.flow_gate.db import git_integration as db_git
    svc = _svc()
    mt = _mm().merge_target
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    m, attempt = _recovery_row(proj, gid, push=case != "no_push")
    if case == "local_lost":
        _git(["reset", "--hard", "main^1"], cwd=proj.base)
    if case == "pushed":
        _git(["push", str(proj.bare), f"{m}:refs/heads/main"], cwd=proj.base)
    if case == "remote_deleted":
        # Rework P2: the tracking ref still holds M (fetch.prune=false) but origin deleted
        # the branch; the recovery must not close the push=true row as completed.
        _git(["config", "fetch.prune", "false"], cwd=proj.base)
        _git(["push", str(proj.bare), f"{m}:refs/heads/main"], cwd=proj.base)
        _git(["fetch", "origin"], cwd=proj.base)
        assert rev(proj.base, "refs/remotes/origin/main") == m
        _git(["update-ref", "-d", "refs/heads/main"], cwd=proj.bare)
        assert _git(["ls-remote", str(proj.bare), "refs/heads/main"], cwd=proj.base).strip() == ""
    if case == "remote_unreadable":
        def broken(*a, **k):
            raise GitServiceError(500, "git_error", "origin unreachable")
        monkeypatch.setattr(svc, "ensure_origin_matches_config", broken)
    calls = spy_git(monkeypatch)
    verdict = mt.recover_interrupted_attempt(db_git.get_session(attempt.merge_id), "test_restart")
    ctx = _ctx(attempt.merge_id)
    assert not any(a[:1] == ["push"] for a in calls)
    if case in ("no_push", "pushed"):
        assert verdict == mt.ATTEMPT_COMPLETED and ctx["attempt_state"] == "completed"
        assert ctx["attempt_result"] == {"merge_commit": m[:len(ctx["attempt_result"]["merge_commit"])],
                                         "merge_commit_sha": m, "pushed": case == "pushed",
                                         "recovered": True}
        assert state(gid)["status"] == "none" and state(gid)["worktree_registered"]
    elif case == "remote_unreadable":
        assert verdict is None and _session(attempt.merge_id)["status"] == "open"
    else:
        assert verdict == mt.ATTEMPT_INTERRUPTED and ctx["attempt_state"] == "interrupted"
        if case == "not_pushed":
            assert rev(proj.base, "main") == m              # never rewound
            assert manual(gid, push=True)["recovered"] is True
        if case == "remote_deleted":
            assert rev(proj.base, "main") == m              # never rewound
            assert try_rev(proj.base, "refs/remotes/origin/main") is None   # pruned
