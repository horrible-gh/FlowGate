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

@needs_git
@pytest.mark.parametrize("case,code", [
    ("ai_run", "run_already_active"), ("lease", "group_lease_active"),
    ("retry", "approval_retry_pending"), ("branch_merge", "branch_merge_claim_active"),
    ("merging", "git_busy"), ("merged", "invalid_state"), ("conflict", "invalid_state"),
    ("tr_session", "invalid_state"), ("group_update", "invalid_state"),
])
def test_technical_guards_refuse_without_side_effects(proj, monkeypatch, case, code):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import group_ai_leases
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.services.git import approval_intent
    svc = _svc()
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    if case == "ai_run":
        monkeypatch.setattr(ai_invoke_service, "has_active_run", lambda _: True)
    elif case == "lease":
        monkeypatch.setattr(group_ai_leases, "get_active", lambda _: {"lease": 1})
    elif case == "retry":
        monkeypatch.setattr(approval_intent, "clean_retry_of_state", lambda _: {"intent": {}})
    elif case == "branch_merge":
        def claimed(_):
            raise GitServiceError(409, "branch_merge_claim_active", "claimed")
        monkeypatch.setattr(svc, "guard_group_branch_merge_free", claimed)
    elif case in ("merging", "merged", "conflict"):
        db_git.set_status(gid, case)
    else:
        kind = db_git.SESSION_KIND_TR_REVERT if case == "tr_session" else db_git.SESSION_KIND_GROUP_UPDATE
        db_git.create_session(gid, [], kind=kind, context={})
    main_before = rev(proj.base, "main")
    error = refused(manual, gid)
    assert error.code == code
    if case == "tr_session":
        assert error.details["session_kind"] == "tr_revert"
    assert rev(proj.base, "main") == main_before
    assert not [s for s in merge_sessions(gid) if (s.get("context") or "").find("manual_merge") >= 0]
    assert held_locks(proj.pid) == []


@needs_git
def test_approval_freeze_claim_leaves_the_job_queued_until_it_is_released(proj, monkeypatch):
    from modules.flow_gate.db import operation_job as db_jobs
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim",
                        lambda g: {"job_id": "approval-job", "state": "frozen", "at": "now"})
    error = refused(manual, gid)
    assert error.code == "git_busy" and error.details["queued"] is True
    assert error.details["reason_code"] == "group_frozen_for_approval"
    assert "a.txt" not in files_at(proj.base, "main")
    monkeypatch.setattr(db_jobs, "get_group_freeze_claim", lambda g: None)
    assert drive_jobs(gid) >= 1
    assert "a.txt" in files_at(proj.base, "main") and state(gid)["status"] == "none"
    job = db_jobs.get_job(error.details["job_id"])
    assert job["status"] == "succeeded"


@needs_git
def test_job_takes_g_then_target_then_r_and_releases_everything(proj, monkeypatch):
    gid = group(proj, 1)
    proj.local_branch("develop")
    work(proj, gid, "a.txt", "a\n")
    seen = spy_locks(monkeypatch)
    manual(gid, push=True)
    taken = [(d, t, k) for op, d, t, k, ok in (x for x in seen if x[0] == "acquire") if ok]
    assert taken[:3] == [("G", None, "finalize"), ("B", None, "publish"), ("R", None, "publish")]
    work(proj, gid, "b.txt", "b\n")
    seen.clear()
    manual(gid, target="develop", push=False)
    taken = [(d, t, k) for op, d, t, k, ok in (x for x in seen if x[0] == "acquire") if ok]
    assert taken[:3] == [("G", None, "finalize"), ("W", "develop", "publish"), ("R", None, "publish")]
    assert held_locks(proj.pid) == []


# ── Graph execution (§3-6(3)) ────────────────────────────────────────────────

@needs_git
@pytest.mark.parametrize("run,hook,expected", [
    (None, "unused", None),
    ({"run_id": "r1"}, None, None),
    ({"run_id": "r1"}, "graph_execution_active", "graph_execution_active"),
    ({"run_id": "r1"}, "graph_execution_handoff", "graph_execution_handoff"),
    ({"run_id": "r1"}, "graph_integration_active", "graph_integration_active"),
    ({"run_id": "r1"}, "unregistered", "graph_state_unavailable"),
])
def test_graph_blocker_table(proj, monkeypatch, run, hook, expected):
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services import graph_execution_hooks
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    monkeypatch.setattr(run_store, "active_run", lambda _: run)
    monkeypatch.setattr(run_store, "has_active_attempts", lambda _: True)
    if hook == "unregistered":
        monkeypatch.setattr(graph_execution_hooks, "_handler", None)
    else:
        monkeypatch.setattr(graph_execution_hooks, "_handler",
                            lambda action, *a, **k: hook if action == "manual_git_blocker" else None)
    if expected is None:
        out = manual(gid)
        assert out["status"] == "merged"
        return
    error = refused(manual, gid)
    assert error.code == "graph_execution_active" and error.details == {"reason": expected}
    assert "a.txt" not in files_at(proj.base, "main")


def test_manual_git_blocker_reads_only_execution_handoff_and_integration(monkeypatch):
    from flowgate.graph_run import execution, execution_store, result_merge
    from modules.flow_gate.graph_workflow.store import run_store
    facts = {"working": False, "handoff": False, "integrating": False, "status": "idle"}
    monkeypatch.setattr(execution_store, "working", lambda _: facts["working"])
    monkeypatch.setattr(execution_store, "handing_off", lambda _: facts["handoff"])
    monkeypatch.setattr(result_merge, "in_progress", lambda _: facts["integrating"])
    monkeypatch.setattr(run_store, "run_by_id", lambda _: {"status": facts["status"]})
    assert execution.manual_git_blocker("r") is None                       # idle run
    facts.update(status="running")
    assert execution.manual_git_blocker("r") is None                       # open manual/gate node only
    facts.update(handoff=True)
    assert execution.manual_git_blocker("r") == "graph_execution_handoff"
    facts.update(status="idle")
    assert execution.manual_git_blocker("r") is None                       # hand-off of an idle run
    facts.update(integrating=True)
    assert execution.manual_git_blocker("r") == "graph_integration_active"
    facts.update(working=True)
    assert execution.manual_git_blocker("r") == "graph_execution_active"
    assert execution._ACTIONS["manual_git_blocker"] is execution.manual_git_blocker


def test_unregistered_hook_raises_for_manual_git_blocker(monkeypatch):
    from modules.flow_gate.services import graph_execution_hooks
    monkeypatch.setattr(graph_execution_hooks, "_handler", None)
    with pytest.raises(RuntimeError):
        graph_execution_hooks.call("manual_git_blocker", "r1")


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


# ── Merge need, three times (§3-6(5)) ────────────────────────────────────────

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


# ── Unpushed merge recovery (§3-6(7-1)) ──────────────────────────────────────

def _local_unrecorded_merge(p, gid) -> str:
    _git(["-c", "user.name=T", "-c", "user.email=t@t", "merge", "--no-ff", "-m", "lost merge",
          branch(gid)], cwd=p.base)
    return rev(p.base, "main")


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


@needs_git
def test_unpushed_merge_recovery_push_rejection_never_rewinds_and_retries_the_same_merge(proj, monkeypatch):
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    m = _local_unrecorded_merge(proj, gid)
    with pytest.MonkeyPatch.context() as rejected:
        reject_push(rejected)
        error = refused(manual, gid, push=True)
    assert error.code == "push_rejected"
    assert rev(proj.base, "main") == m and state(gid)["status"] == "waiting"
    first = merge_sessions(gid)[0]
    assert _ctx(first["merge_id"])["attempt_state"] == "failed"
    assert results(gid)["results"] == []
    out = manual(gid, push=True)
    assert out["recovered"] is True and out["merge_id"] != first["merge_id"]
    assert rev(proj.base, "main") == m and "a.txt" in proj.origin_files("main")


# ── Push rejection of a fresh merge, conflict, restart ───────────────────────

@needs_git
def test_push_rejection_rewinds_the_fresh_merge_keeps_the_slot_and_heals_to_none(proj, monkeypatch):
    svc = _svc()
    gid = group(proj, 1)
    work(proj, gid, "a.txt", "a\n")
    before = rev(proj.base, "main")
    with pytest.MonkeyPatch.context() as rejected:
        reject_push(rejected)
        error = refused(manual, gid, push=True)
    assert error.code == "push_rejected"
    assert rev(proj.base, "main") == before                 # E6 rewind of this merge only
    assert state(gid)["status"] == "waiting" and state(gid)["worktree_registered"]
    assert _ctx(merge_sessions(gid)[0]["merge_id"])["attempt_state"] == "failed"
    assert results(gid)["results"] == []
    svc.project_git_status(proj.pid)                        # T0009 self-heal (root not wf_done)
    assert state(gid)["status"] == "none"
    assert manual(gid, push=True)["pushed"] is True         # the next try just works
    assert held_locks(proj.pid) == []


@needs_git
def test_conflict_uses_the_normal_review_and_keeps_the_group(proj):
    svc = _svc()
    gid = group(proj, 1)
    (proj.wt(gid) / "shared.txt").write_text(f"{gid} version\n", encoding="utf-8")
    _git(["commit", "-am", "group edit"], cwd=proj.wt(gid))
    proj.origin_commit("main", "shared.txt", "main version\n")
    out = manual(gid, push=True)
    assert out["status"] == "conflict" and out["group_status"] == "conflict"
    assert out["worktree_kept"] is True and out["conflict_files"] == ["shared.txt"]
    merge_id = out["merge_id"]
    assert state(gid)["status"] == "conflict"
    resolved = svc.resolve_conflicts(gid, merge_id, [{
        "path": "shared.txt", "content": f"{gid} version\nmain version\n"}], True)
    assert resolved["result"]["status"] == "resolved_pending_review"
    review = svc.get_merge_review(gid, merge_id)["result"]
    approved = svc.approve_merge_review(gid, merge_id, attempt_id=str(uuid.uuid4()),
                                        review_fingerprint=review["review_fingerprint"],
                                        authority="human")
    assert approved["result"]["status"] == "merged" and approved["result"]["group_status"] == "none"
    assert proj.origin_show("main", "shared.txt") == f"{gid} version\nmain version\n"
    assert state(gid)["status"] == "none" and state(gid)["worktree_registered"] and proj.wt(gid).is_dir()
    ctx = _ctx(merge_id)
    assert ctx["attempt_state"] == "completed" and ctx["attempt_result"]["merge_commit_sha"]
    assert [r["merge_id"] for r in results(gid)["results"]] == [merge_id]


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


@needs_git
@pytest.mark.parametrize("secret", [SECRET, SECRET_KO], ids=["ascii", "non_ascii"])
@pytest.mark.parametrize("managed", [False, True])
def test_conflict_answer_and_stored_result_never_name_a_hidden_path(proj, monkeypatch, managed, secret):
    """Rework P1 (D#1 §3-1(3)): a committed hidden file in conflict is counted, never named,
    in the E9 answer and the job result; the conflict session keeps finalize's own line.
    Rework 2: a non-ASCII hidden name arrives Git-quoted and is judged by its real path."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import tr2_file_policy
    if managed:
        monkeypatch.setattr(tr2_file_policy, "managed_paths", lambda _: {secret})
    gid = group(proj, 1)
    _hidden_conflict(proj, gid, secret)
    out = manual(gid, push=True)
    assert out["status"] == "conflict" and out["group_status"] == "conflict"
    assert out["conflict_files"] == ["shared.txt"] and out["hidden_conflict_count"] == 1
    no_hidden(out, secret)
    no_hidden(_job_of(out["job_id"]), secret)
    files = sorted(row["path"] for row in db_git.session_files(out["merge_id"]))
    # The session keeps the line finalize recorded (quoted for the non-ASCII name).
    assert files == sorted(["shared.txt", git_quoted(secret)])
    assert (git_quoted(secret) != secret) is (secret == SECRET_KO)
    assert state(gid)["status"] == "conflict"


class _Crash(BaseException):
    pass


@needs_git
@pytest.mark.parametrize("secret", [SECRET, SECRET_KO], ids=["ascii", "non_ascii"])
@pytest.mark.parametrize("managed", [False, True])
def test_resumed_conflict_answer_never_names_a_hidden_path(proj, monkeypatch, managed, secret):
    """The resume path (_resume_merge) answers through the same filter, from the session's
    (Git-quoted for a non-ASCII name) lines."""
    from test_git_merge_target_0594 import _recover_job_after_crash
    from modules.flow_gate.services import tr2_file_policy
    from modules.flow_gate.services.git import finalize_publish
    if managed:
        monkeypatch.setattr(tr2_file_policy, "managed_paths", lambda _: {secret})
    gid = group(proj, 1)
    _hidden_conflict(proj, gid, secret)
    with pytest.MonkeyPatch.context() as patch:
        def crash(ctx, result):
            raise _Crash()
        patch.setattr(finalize_publish, "_finish", crash)
        with pytest.raises(_Crash):
            manual(gid, push=True)
    assert state(gid)["status"] == "conflict"
    job = _recover_job_after_crash(gid)
    assert job["status"] == "succeeded", job
    no_hidden(job, secret)
    import json as _json
    result = job["result"] if isinstance(job["result"], dict) else _json.loads(job["result"])
    answer = result["result"]
    assert answer["resumed"] is True and answer["status"] == "conflict"
    assert answer["conflict_files"] == ["shared.txt"] and answer["hidden_conflict_count"] == 1


@pytest.mark.parametrize("line, real", [
    ("shared.txt", "shared.txt"),
    ("server/.env.local", "server/.env.local"),
    (git_quoted(SECRET_KO), SECRET_KO),
    (git_quoted("docs/\ud55c.txt"), "docs/\ud55c.txt"),
    ('"a\\tb\\"c\\\\d"', 'a\tb"c\\d'),
    ('"bad\\x"', None),
    ('"a"b"', None),
    ('"trail\\"', None),
])
def test_git_unquote_reads_finalize_conflict_lines(line, real):
    """Rework 2: Git's C quoting (octal UTF-8 bytes, C escapes) is undone exactly; a quoted
    line that is not valid Git quoting is None and counts as hidden (fail closed)."""
    mm = _mm()
    assert mm._git_unquote(line) == real
    if real is None:
        assert mm._hidden_conflict_path(line) is True


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


# ── finalize() defaults are untouched ────────────────────────────────────────

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


# ── E8 view ──────────────────────────────────────────────────────────────────

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
