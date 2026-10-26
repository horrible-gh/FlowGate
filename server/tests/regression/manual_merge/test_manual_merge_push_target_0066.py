"""0066 T#3 — per-target merge results and their follow-up push (D#1 §3-8, E10/E11).

The evidence is the latest completed manual merge attempt of each target branch. A push
names one by ``merge_id``; its ``merge_result_push`` job holds G -> B/W -> R from the
re-validation to its terminal write and pushes exactly the tip it pinned (h1).
"""
from __future__ import annotations

import threading

import pytest

from manual_merge_0066_support import (
    GitServiceError, _ctx, _git, branch, drive_jobs, group, held_locks, manual, needs_git,
    push_result, refused, reject_push, results, rev, spy_git, spy_locks, state, try_rev, work,
)
from test_git_merge_target_0594 import patch_store, proj, storage_dir  # noqa: F401 — fixtures


def _mm():
    from modules.flow_gate.services.git import manual_merge
    return manual_merge


def _origin(p, ref):
    return _git(["rev-parse", ref], cwd=p.bare).strip()


def _row(view, target):
    return next(r for r in view["results"] if r["target"]["target_branch"] == target)


def _two_targets(p):
    p.local_branch("develop")
    p.local_branch("release")


def _commit_on(p, target: str, name: str) -> str:
    """A commit made directly on a local branch of the base repository (not the checkout)."""
    tree = _git(["rev-parse", f"{target}^{{tree}}"], cwd=p.base).strip()
    sha = _git(["commit-tree", tree, "-p", target, "-m", name], cwd=p.base).strip()
    _git(["update-ref", f"refs/heads/{target}", sha], cwd=p.base)
    return sha


class _Crash(BaseException):
    pass


def _recover(gid: str) -> dict:
    """The running push job's lease lapses; the Sweeper reconciles it; the next claim runs."""
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, job_sweeper
    job = [j for j in db_jobs.jobs_of_group(gid, "base_publish", ("running",))][-1]
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET lease_until = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job["job_id"]])
    assert job_sweeper.step_expired_leases(full=True) >= 1
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job["job_id"]])
    claim = job_runner.try_claim(job["job_id"])
    assert claim is not None, db_jobs.get_job(job["job_id"])
    job_runner.execute(claim)
    return db_jobs.get_job(job["job_id"])


# ── Per-target evidence (rev3) ───────────────────────────────────────────────

@needs_git
def test_each_target_keeps_its_own_unpushed_result_and_pushes_alone(proj):
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    a = manual(gid, target="develop")
    work(proj, gid, "b.txt", "b\n")
    b = manual(gid, target="release")
    view = results(gid)
    assert [r["merge_id"] for r in view["results"]] == [b["merge_id"], a["merge_id"]]
    assert view["push_candidates"] == 2 and view["group_blocker"] is None
    row_a = _row(view, "develop")
    assert row_a["target"]["is_project_base"] is False and row_a["pushed"] is False
    assert row_a["remote"] == {"measured": True, "remote_branch_missing": False,
                               "local_ahead_count": 2, "contains_merge_commit": False}
    release_before = _origin(proj, "release")
    out = push_result(gid, row_a["merge_id"], row_a["merge_commit"][:9])
    assert out["target_branch"] == "develop" and out["is_project_base"] is False
    assert out["pushed_ref"] == "refs/heads/develop" and out["already_published"] is False
    assert out["pushed_head"] == rev(proj.base, "develop") == _origin(proj, "develop")
    assert _origin(proj, "release") == release_before and "a.txt" not in proj.origin_files("main")
    view = results(gid)
    assert _row(view, "develop")["push_available"] is False
    assert _row(view, "develop")["push_blocked_reason"] == "already_published"
    assert _row(view, "release")["push_available"] is True and view["push_candidates"] == 1
    row_b = _row(view, "release")
    push_result(gid, row_b["merge_id"], row_b["merge_commit"])
    assert "b.txt" in proj.origin_files("release")
    assert results(gid)["push_candidates"] == 0
    assert held_locks(proj.pid) == []


@needs_git
def test_a_later_merge_with_push_elsewhere_leaves_the_first_target_pushable(proj):
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    a = manual(gid, target="develop")
    manual(gid, target="release", push=True)
    row = _row(results(gid), "develop")
    assert row["merge_id"] == a["merge_id"] and row["push_available"] is True
    assert _row(results(gid), "release")["push_blocked_reason"] == "already_published"


@needs_git
def test_same_target_twice_only_the_latest_is_the_evidence(proj):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    m1 = manual(gid, target="develop")
    work(proj, gid, "b.txt", "b\n")
    m2 = manual(gid, target="develop")
    view = results(gid)
    assert [r["merge_id"] for r in view["results"]] == [m2["merge_id"]]
    error = refused(push_result, gid, m1["merge_id"], _ctx(m1["merge_id"])["attempt_result"]["merge_commit_sha"])
    assert error.code == "stale_target"
    assert error.details["reason"] == "superseded" and error.details["latest_merge_id"] == m2["merge_id"]
    row = view["results"][0]
    push_result(gid, row["merge_id"], row["merge_commit"])
    m1_sha = _ctx(m1["merge_id"])["attempt_result"]["merge_commit_sha"]
    assert _git(["merge-base", "--is-ancestor", m1_sha, "develop"], cwd=proj.bare, check=False) == ""
    assert {"a.txt", "b.txt"} <= set(proj.origin_files("develop"))


@needs_git
def test_history_a_person_dropped_is_not_pushed_again(proj):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    m1 = manual(gid, target="develop")
    m1_sha = _ctx(m1["merge_id"])["attempt_result"]["merge_commit_sha"]
    _git(["update-ref", "refs/heads/develop", f"{m1_sha}^1"], cwd=proj.base)   # person rewinds
    work(proj, gid, "b.txt", "b\n")
    m2 = manual(gid, target="develop")
    row = _row(results(gid), "develop")
    assert row["merge_id"] == m2["merge_id"]
    push_result(gid, row["merge_id"], row["merge_commit"])
    assert _origin(proj, "develop") == row["merge_commit"]
    assert m1_sha not in _git(["rev-list", "develop"], cwd=proj.bare).split()


# ── Request-time refusals ────────────────────────────────────────────────────

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


# ── The job under its locks ──────────────────────────────────────────────────

@needs_git
@pytest.mark.parametrize("target", ["main", "develop"])
def test_push_job_holds_g_target_r_until_its_end(proj, monkeypatch, target):
    from modules.flow_gate.services import git_service as svc
    gid = group(proj, 1)
    if target != "main":
        proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target=None if target == "main" else target)
    row = _row(results(gid), target)
    seen = spy_locks(monkeypatch)
    during_push = []
    real = svc._run_git

    def run(args, **kwargs):
        if args and args[0] == "push":
            during_push.append(sorted((r["domain"], r.get("target_key") or "") for r in held_locks(proj.pid)))
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", run)
    push_result(gid, row["merge_id"], row["merge_commit"])
    taken = [(d, t, k) for op, d, t, k, ok in (x for x in seen if x[0] == "acquire") if ok]
    expected_target = ("B", None, "publish") if target == "main" else ("W", "develop", "publish")
    assert taken[:3] == [("G", None, "publish"), expected_target, ("R", None, "publish")]
    domains = {d for d, _ in during_push[0]}
    assert {"G", "R", "B" if target == "main" else "W"} <= domains
    assert held_locks(proj.pid) == []


def _queued_push(proj, gid, target):
    from group_lock_stub import hold_group_lock
    row = _row(results(gid), target)
    with hold_group_lock(proj.pid, gid, holder_kind="source_mutation"):
        error = refused(push_result, gid, row["merge_id"], row["merge_commit"])
    assert error.code == "git_busy" and error.details["queued"] is True
    assert error.details["phase"] == "start"
    return row, error.details["job_id"]


@needs_git
def test_job_rejects_a_target_moved_while_it_waited(proj):
    from modules.flow_gate.db import operation_job as db_jobs
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    out = manual(gid, target="develop")
    sha = _ctx(out["merge_id"])["attempt_result"]["merge_commit_sha"]
    row, job_id = _queued_push(proj, gid, "develop")
    _git(["update-ref", "refs/heads/develop", f"{sha}^1"], cwd=proj.base)
    drive_jobs(gid)
    job = db_jobs.get_job(job_id)
    assert job["status"] == "failed" and job["last_error_code"] == "merge_target_moved"
    assert "a.txt" not in proj.origin_files("develop")
    assert held_locks(proj.pid) == []


@needs_git
def test_job_refuses_superseded_base_changed_and_conflict_after_waiting(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import operation_job as db_jobs
    from modules.flow_gate.services.git import merge_target
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row, job_id = _queued_push(proj, gid, "develop")
    work(proj, gid, "b.txt", "b\n")
    newer = manual(gid, target="develop")                   # completes while the push waits
    drive_jobs(gid)
    job = db_jobs.get_job(job_id)
    assert job["status"] == "failed" and job["last_error_code"] == "stale_target"
    assert "a.txt" not in proj.origin_files("develop")
    row, job_id = _queued_push(proj, gid, "develop")
    assert row["merge_id"] == newer["merge_id"]
    monkeypatch.setattr(merge_target, "project_base_branch", lambda cfg: "develop")
    drive_jobs(gid)
    assert db_jobs.get_job(job_id)["last_error_code"] == "stale_target"
    monkeypatch.undo()
    row, job_id = _queued_push(proj, gid, "develop")
    db_git.set_status(gid, "conflict")
    drive_jobs(gid)
    assert db_jobs.get_job(job_id)["last_error_code"] == "invalid_state"
    db_git.set_status(gid, "none")
    assert "a.txt" not in proj.origin_files("develop")


@needs_git
def test_another_target_completing_meanwhile_does_not_stop_the_push(proj):
    from modules.flow_gate.db import operation_job as db_jobs
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row, job_id = _queued_push(proj, gid, "develop")
    release_before = _origin(proj, "release")
    work(proj, gid, "b.txt", "b\n")
    manual(gid, target="release")                           # B completes while A waits
    drive_jobs(gid)
    assert db_jobs.get_job(job_id)["status"] == "succeeded"
    assert _origin(proj, "develop") == row["merge_commit"]
    assert _origin(proj, "release") == release_before
    assert _row(results(gid), "release")["push_available"] is True


@needs_git
def test_a_merge_of_another_target_waits_for_the_running_push(proj, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    gid = group(proj, 1)
    _two_targets(proj)
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    work(proj, gid, "b.txt", "b\n")
    row = _row(results(gid), "develop")
    outcome = {}
    real = svc._run_git

    def run(args, **kwargs):
        if args and args[0] == "push" and "develop" in args[-1] and "merge" not in outcome:
            def other():
                try:
                    outcome["merge"] = manual(gid, target="release")
                except GitServiceError as exc:
                    outcome["merge"] = exc
            t = threading.Thread(target=other)
            t.start()
            t.join(60)
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", run)
    push_result(gid, row["merge_id"], row["merge_commit"])
    assert isinstance(outcome["merge"], GitServiceError) and outcome["merge"].code == "git_busy"
    assert "b.txt" not in _git(["ls-tree", "-r", "--name-only", "release"], cwd=proj.base).split()
    monkeypatch.undo()
    drive_jobs(gid)
    assert "b.txt" in _git(["ls-tree", "-r", "--name-only", "release"], cwd=proj.base).split()
    assert _origin(proj, "develop") == row["merge_commit"]


@needs_git
def test_frozen_group_blocks_the_push_job_until_released(proj, monkeypatch):
    from modules.flow_gate.db import operation_job as db_jobs
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row = _row(results(gid), "develop")
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim",
                        lambda g: {"job_id": "approval", "state": "frozen", "at": "now"})
    error = refused(push_result, gid, row["merge_id"], row["merge_commit"])
    assert error.code == "git_busy" and error.details["reason_code"] == "group_frozen_for_approval"
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim", lambda g: None)
    drive_jobs(gid)
    assert _origin(proj, "develop") == row["merge_commit"]


# ── Resume: fixed h1 ─────────────────────────────────────────────────────────

@needs_git
@pytest.mark.parametrize("case", ["keeps_h1", "moved_off_h1", "pushed"])
def test_resume_pushes_only_the_pinned_head(proj, monkeypatch, case):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import finalize_publish
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row = _row(results(gid), "develop")
    h1 = rev(proj.base, "develop")
    with pytest.MonkeyPatch.context() as patch:
        if case == "pushed":
            def finish(ctx, result):
                raise _Crash()
            patch.setattr(finalize_publish, "_finish", finish)
        else:
            real = svc._run_git

            def run(args, **kwargs):
                if args and args[0] == "push":
                    raise _Crash()
                return real(args, **kwargs)
            patch.setattr(svc, "_run_git", run)
        with pytest.raises(_Crash):
            push_result(gid, row["merge_id"], row["merge_commit"])
    if case == "keeps_h1":
        extra = _commit_on(proj, "develop", "later work")    # the target moves on, above h1
    elif case == "moved_off_h1":
        _git(["update-ref", "refs/heads/develop", f"{h1}^1"], cwd=proj.base)
        _commit_on(proj, "develop", "other history")
    pushes = []
    real = svc._run_git

    def count(args, **kwargs):
        if args and args[0] == "push":
            pushes.append(list(args))
        return real(args, **kwargs)
    monkeypatch.setattr(svc, "_run_git", count)
    job = _recover(gid)
    if case == "keeps_h1":
        assert job["status"] == "succeeded", job
        assert _origin(proj, "develop") == h1 != extra
        assert pushes == [["push", "origin", f"{h1}:refs/heads/develop"]]
    elif case == "moved_off_h1":
        assert job["status"] == "failed" and job["last_error_code"] == "merge_target_moved"
        assert pushes == [] and "a.txt" not in proj.origin_files("develop")
    else:
        assert job["status"] == "succeeded" and job["phase"] == "done" and pushes == []
        assert _origin(proj, "develop") == h1
    assert held_locks(proj.pid) == []


@needs_git
def test_push_rejection_is_a_500_and_changes_nothing(proj, monkeypatch):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    manual(gid, target="develop")
    row = _row(results(gid), "develop")
    reject_push(monkeypatch)
    error = refused(push_result, gid, row["merge_id"], row["merge_commit"])
    assert error.status == 500 and error.code == "push_rejected"
    assert "a.txt" not in proj.origin_files("develop")
    monkeypatch.undo()
    assert push_result(gid, row["merge_id"], row["merge_commit"])["pushed_head"]   # a retry is a new job


@needs_git
def test_routes_validate_and_answer(proj, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "admin", "is_admin": True}
    allowed = {"ok": True}
    monkeypatch.setattr(git_routes, "_has_permission", lambda *a: allowed["ok"])
    client = TestClient(app, raise_server_exceptions=False)
    base = f"/api/v1/groups/{gid}/git"
    assert client.get(base + "/manual-merge").json()["result"]["can_merge"] is True
    assert client.post(base + "/manual-merge", json={"push": "yes"}).status_code == 422
    assert client.post(base + "/manual-merge", json={"bogus": 1}).status_code == 422
    merged = client.post(base + "/manual-merge", json={"push": True}).json()["result"]
    assert merged["status"] == "merged" and merged["group_status"] == "none"
    view = client.get(base + "/merge-result").json()["result"]
    assert view["results"][0]["merge_id"] == merged["merge_id"]
    missing = client.post(base + "/merge-result/push", json={})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "invalid_request"
    again = client.post(base + "/manual-merge", json={})
    assert again.status_code == 409 and again.json()["error"]["code"] == "nothing_to_merge"
    allowed["ok"] = False
    assert client.post(base + "/manual-merge", json={}).status_code == 403
    assert client.post(base + "/merge-result/push", json={"merge_id": 1, "merge_commit": "a" * 7}).status_code == 403
    assert client.get(base + "/merge-result").status_code == 403
