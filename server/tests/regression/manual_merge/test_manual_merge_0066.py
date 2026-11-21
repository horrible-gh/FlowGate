"""0066 T#3 — manual merge (D#1 §3-6, E8/E9) on real Git, real jobs and real locks.

The approval gate is replaced by technical guards only; nothing uncommitted is absorbed;
the merge need is counted against the target three times (request, under the job's locks,
after the fetch + ff-only); success keeps the Group slot with the ledger at ``none``; an
unpushed local merge is completed through its own attempt row; finalize() keeps its old
behavior for every one of these cases.
"""
from __future__ import annotations

import uuid

import pytest

from manual_merge_0066_support import (
    GitServiceError, _ctx, _git, _session, branch, drive_jobs, files_at, git_quoted, group,
    held_locks, manual, merge_sessions, needs_git, no_hidden, refused, reject_push, results, rev,
    spy_git, spy_locks, state, try_rev, work,
)
from test_git_merge_target_0594 import patch_store, proj, storage_dir  # noqa: F401 — fixtures

SECRET = "server/.env.local"
# Rework 2 (rej_01M50Z5C5VDKCGM3): a non-ASCII hidden name Git prints C-quoted
# (``"server/.env-\\355\\225\\234\\352\\270\\200"``) in finalize's conflict list.
SECRET_KO = "server/.env-\ud55c\uae00"


def _svc():
    from modules.flow_gate.services import git_service as svc
    return svc


def _mm():
    from modules.flow_gate.services.git import manual_merge
    return manual_merge


# ── Approval separation, Group kept ──────────────────────────────────────────

@needs_git
def test_manual_merge_without_wf_done_keeps_the_group_and_merges_only_new_commits(proj, monkeypatch):
    from modules.flow_gate.db import git_integration as db_git
    svc = _svc()
    gid = group(proj, 1)
    tip1 = work(proj, gid, "one.txt", "one\n")
    before = state(gid)
    # finalize() still refuses a Group whose root is not wf_done
    db_git.set_status(gid, "waiting")
    assert refused(svc.finalize, gid, "merge").message == "final workflow approval is required before Git finalize"
    snapshots = []
    from modules.flow_gate.services import conversation_markdown_service
    monkeypatch.setattr(conversation_markdown_service, "snapshot_group_conversations",
                        lambda *a: snapshots.append(a))

    first = manual(gid, push=False)
    assert first["status"] == "merged" and first["group_status"] == "none"
    assert first["worktree_kept"] is True and first["pushed"] is False and first["recovered"] is False
    assert first["action"] == "merge_only" and first["target_branch"] == "main"
    assert first["unmerge_supported"] is False and first["job_id"]
    m1 = rev(proj.base, "main")
    assert first["merge_id"] and first["merge_commit"] == m1[:len(first["merge_commit"])]
    assert rev(proj.base, "main^2") == tip1 and "one.txt" in files_at(proj.base, "main")
    assert "one.txt" not in proj.origin_files("main")
    after = state(gid)
    assert after["status"] == "none" and after["merge_id"] is None and after["merge_commit"] is None
    assert after["worktree_registered"] and proj.wt(gid).is_dir() and try_rev(proj.base, branch(gid))
    for key in ("work_base_ref", "work_base_sha", "work_base_sync_sha", "work_base_state"):
        assert after.get(key) == before.get(key)            # scope floor untouched
    ctx = _ctx(first["merge_id"])
    assert _session(first["merge_id"])["status"] == "done"
    assert ctx["attempt_state"] == "completed" and ctx["merge_target"]["origin"] == "manual_merge"
    assert ctx["attempt_result"]["merge_commit_sha"] == m1 and ctx["attempt_result"]["pushed"] is False
    assert snapshots == []                                  # no conversation snapshot
    assert svc.cleanup_terminal_slots(proj.pid) is not None
    assert state(gid)["worktree_registered"] and proj.wt(gid).is_dir()

    # the Group goes on: commit, merge again — only the new commit lands, now pushed
    tip2 = work(proj, gid, "two.txt", "two\n")
    second = manual(gid, push=True)
    assert second["status"] == "merged" and second["pushed"] is True
    assert rev(proj.base, "main^1") == m1 and rev(proj.base, "main^2") == tip2
    assert {"one.txt", "two.txt"} <= set(proj.origin_files("main"))
    assert state(gid)["status"] == "none" and state(gid)["worktree_registered"]
    assert held_locks(proj.pid) == []


@needs_git
def test_non_base_target_merge_and_push_uses_its_workspace_and_keeps_the_group(proj):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "feature.txt", "feature\n")
    base_before = proj.base_sha()
    out = manual(gid, target="develop", push=True)
    assert out["status"] == "merged" and out["target_branch"] == "develop" and out["pushed"] is True
    assert "feature.txt" in proj.origin_files("develop") and "feature.txt" not in proj.origin_files("main")
    assert proj.base_sha() == base_before and proj.base_head_branch() == "main"
    assert not proj.workspace("develop").exists()           # its own workspace released
    assert state(gid)["status"] == "none" and state(gid)["worktree_registered"]
    assert _ctx(out["merge_id"])["merge_target"]["is_project_base"] is False


@needs_git
def test_approval_wait_does_not_block_and_final_approval_bound_is_the_conflict_state(proj, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import approval_intent
    gid = group(proj, 1)
    db_docs.create({"doc_id": f"{gid}.0002-AC", "project_id": proj.pid, "module": "default",
                    "group_id": gid, "type_code": "AC", "seq": 2, "title": "final approval",
                    "target_id": f"{gid}.0001-R"})
    db_docs.update(f"{gid}.0002-AC", {"doc_review_status": "pending_review"})
    work(proj, gid, "a.txt", "a\n")
    view = _mm().merge_view(gid)
    assert view["can_merge"] is True and view["approval"]["status"] == "pending_review"
    assert view["admission_basis"] == "no_wf_done"
    # final_approval_bound means an approval-coupled conflict session: refused as conflict
    db_git.set_status(gid, "conflict")
    monkeypatch.setattr(approval_intent, "group_is_final_approval_bound", lambda _: True)
    error = refused(manual, gid)
    assert error.code == "invalid_state" and error.code != "final_approval_bound"
    db_git.set_status(gid, "awaiting_choice")
    monkeypatch.setattr(approval_intent, "group_is_final_approval_bound", lambda _: False)
    assert manual(gid)["status"] == "merged"


# ── Admission guards (§3-6(2)) ───────────────────────────────────────────────







# ── Graph execution (§3-6(3)) ────────────────────────────────────────────────







# ── Uncommitted files are never absorbed (§3-6(4)) ───────────────────────────

def _dirty(p, gid, case):
    wt = p.wt(gid)
    if case == "staged":
        (wt / "README.md").write_text("staged\n", encoding="utf-8")
        _git(["add", "README.md"], cwd=wt)
    elif case == "unstaged":
        (wt / "README.md").write_text("unstaged\n", encoding="utf-8")
    elif case == "new_normal":
        (wt / "new.txt").write_text("new\n", encoding="utf-8")
    elif case == "staged_excluded_new":
        (wt / "debug.log").write_text("log\n", encoding="utf-8")
        _git(["add", "-f", "debug.log"], cwd=wt)
    elif case in ("hidden_new", "managed_hidden"):
        (wt / SECRET).parent.mkdir(parents=True, exist_ok=True)
        (wt / SECRET).write_text("TOKEN=1\n", encoding="utf-8")
    elif case == "managed":
        (wt / "managed.txt").write_text("owned\n", encoding="utf-8")










# ── Merge need, three times (§3-6(5)) ────────────────────────────────────────









# ── Unpushed merge recovery (§3-6(7-1)) ──────────────────────────────────────

def _local_unrecorded_merge(p, gid) -> str:
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "merge", "--no-ff", "-m", "lost merge",
          branch(gid)], cwd=p.base)
    return rev(p.base, "main")








# ── Push rejection of a fresh merge, conflict, restart ───────────────────────





def _hidden_conflict(p, gid, secret: str = SECRET) -> None:
    """Both sides commit the hidden ``secret`` and ``shared.txt`` differently. core.quotePath
    is pinned on (Git's default) so a non-ASCII ``secret`` reaches finalize C-quoted."""
    wt, seed = p.wt(gid), p.seed
    for repo in (p.base, wt, seed):
        _git(["config", "core.quotePath", "true"], cwd=repo)
    for repo, text in ((wt, f"{gid} version\n"), (None, "main version\n")):
        if repo is None:
            _git(["fetch", "origin"], cwd=seed)
            _git(["checkout", "-B", "main", "origin/main"], cwd=seed)
            repo = seed
        (repo / secret).parent.mkdir(parents=True, exist_ok=True)
        (repo / secret).write_text(f"TOKEN={text}", encoding="utf-8")
        (repo / "shared.txt").write_text(text, encoding="utf-8")
        _git(["add", "-f", "--", secret, "shared.txt"], cwd=repo)
        _git(["commit", "-m", "edit shared and secret"], cwd=repo)
    _git(["push", "origin", "main"], cwd=seed)


def _job_of(job_id: str) -> dict:
    from modules.flow_gate.db import operation_job as db_jobs
    return db_jobs.get_job(job_id)




class _Crash(BaseException):
    pass










def _recovery_row(p, gid, *, push: bool, job_status="failed"):
    """An open recovery attempt whose job is gone (what a crash leaves)."""
    from modules.flow_gate.db import git_integration as db_git
    from test_git_merge_target_0594 import _end_job, _live_job
    mt = _mm().merge_target
    m = _local_unrecorded_merge(p, gid)
    job_id = _live_job(p.pid, uuid.uuid4().hex[:8])
    _end_job(job_id, job_status)
    target = mt.plan_finalize_target(gid, p.pid, db_git.get_config(p.pid), None)
    attempt = mt.open_attempt(gid, target, "merge" if push else "merge_only", f"job:{job_id}",
                              uuid.uuid4().hex, origin=mt.ORIGIN_MANUAL_MERGE, recovery_merge_commit=m)
    return m, attempt




# ── finalize() defaults are untouched ────────────────────────────────────────







# ── E8 view ──────────────────────────────────────────────────────────────────
