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

















# ── The job under its locks ──────────────────────────────────────────────────



def _queued_push(proj, gid, target):
    from group_lock_stub import hold_group_lock
    row = _row(results(gid), target)
    with hold_group_lock(proj.pid, gid, holder_kind="source_mutation"):
        error = refused(push_result, gid, row["merge_id"], row["merge_commit"])
    assert error.code == "git_busy" and error.details["queued"] is True
    assert error.details["phase"] == "start"
    return row, error.details["job_id"]












# ── Resume: fixed h1 ─────────────────────────────────────────────────────────
