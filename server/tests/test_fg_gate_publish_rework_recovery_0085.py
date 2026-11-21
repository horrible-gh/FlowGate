"""flowgate.v02.0085 T#3 rework (review rej_01M5NT4F9CPN06M6): one focused regression test per defect of the
review, on the ``gate_publish`` harness and a real Git repository.

flowgate.v02.0098 T#2 (TR0009): moved unchanged from ``test_fg_gate_publish_rework_0085`` (before that
``test_fg_gate_publish_0085``), whose harness, fixtures and shared helpers it imports. ``--dist loadfile``
gives one file to one worker, and the 19 cases of the one file took longer than the 1800 s Self-check limit
allows on a loaded host.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fg_card_settings import FA, _h, _sql, db_path, env  # noqa: E402,F401
from test_fg_gate_intake_0085 import _finish, _gate_events, _start, _states, product  # noqa: E402,F401
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    PROJECT, _git, _rev, _work, _gate, _run_job, _expire_and_reconcile, _decision, _effects, _job_row, _merges,
    _confirm, _act, _version, _requests, _start_linear, _evidence, _run_cleanup, _flow, _cleanups, _run_status,
    _parked_after_merge, _parked_without_effect, _side_commit, _second_publish, _completed_merge_flow, git_env)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


# ── T#3 rework (continued): the end cleanup, the checkpoints and the recovery requests ──

def test_the_end_cleanup_waits_for_its_locks_checks_again_under_them_and_holds_the_basis_target(git_env, monkeypatch):
    """A busy G leaves the job blocked (a later claim goes on); a run entered again after the locks were taken is seen
    by the check under them (nothing deleted); while the cleanup deletes, the basis target (B) and G are its own."""
    from types import SimpleNamespace
    from flowgate.gate_decisions import cleanup
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import lock_manager as locks
    r = git_env
    env, g, run_id, sha, cj = _completed_merge_flow(r)
    other = locks.new_context("req")
    held = locks.acquire("G", PROJECT, group_id=FA, holder_kind="source_mutation", ctx=other)
    assert held.ok
    job, _ = _run_job(cj)
    assert (job["status"], job["blocked_domain"]) == ("blocked", "G") and _cleanups(env) == []
    locks.release(other, held.lock_key)
    real_conditions = cleanup._conditions

    def entered(flow_id, run):
        with get_store().transaction():                                       # a new command took the run meanwhile
            get_store()._execute("UPDATE graph_runs SET status = 'running', idle_reason = NULL WHERE run_id = ?",
                                 [run])
        return real_conditions(flow_id, run)

    monkeypatch.setattr(cleanup, "_conditions", entered)
    job, _ = _run_job(cj)
    assert job["status"] == "succeeded" and json.loads(job["result"]) == {"skipped": "run_not_completed"}
    assert _cleanups(env) == [] and r["wt"].is_dir() and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    monkeypatch.setattr(cleanup, "_conditions", real_conditions)
    _sql(env["db"], "UPDATE graph_runs SET status = 'idle', idle_reason = 'completed' WHERE run_id = ?", (run_id,))
    with get_store().transaction():
        job2 = cleanup.request(SimpleNamespace(flow_id=FA, run_id=run_id, jobs=[], project_id=PROJECT))
    seen = {}
    real_step = cleanup._step_worktree

    def contended(plan):
        for domain, extra, kind in (("B", {}, "base_mutation"), ("G", {"group_id": FA}, "source_mutation")):
            ctx = locks.new_context("req")
            o = locks.acquire(domain, PROJECT, holder_kind=kind, ctx=ctx, **extra)
            seen[domain] = o.kind
            if o.ok:
                locks.release(ctx, o.lock_key)
        return real_step(plan)

    monkeypatch.setattr(cleanup, "_step_worktree", contended)
    job, _ = _run_job(job2["job_id"])
    assert job["status"] == "succeeded" and seen == {"B": locks.BUSY, "G": locks.BUSY}
    (rec,) = _cleanups(env)
    assert (rec["result"], rec["basis_kind"], rec["basis_ref"]) == ("deleted", "merged_target", "refs/heads/main")


def test_gate_checkpoints_are_listed_beside_the_group_archives_and_restored_without_being_consumed(git_env):
    """D0007 §7.1: the existing archive list shows every round's checkpoint (told apart from the v0.1 archives); a
    checkpoint restore keeps the checkpoint and its refs and may run again; the v0.1 restore path is unchanged."""
    from flowgate.gate_decisions import checkpoints
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git.credentials import GitServiceError
    r = git_env
    env = r["env"]
    keys = _flow(env, ("G", "T", "G"))
    run_id = _start(env)["run_id"]
    sha_a = _work(r, "a.txt")
    _gate(env, keys[0], merge_mode="hold", archive=True)
    first = _confirm(env, run_id, keys[0], "ck1")
    assert _run_job(first["job"]["job_id"])[0]["status"] == "succeeded"
    sha_b = _work(r, "b.txt")
    _finish(env, keys[1], 90)
    if _states(env)[keys[2]] == "pending":
        assert env["client"].post(f"/api/v1/runs/{run_id}/resume", json={}, headers=_h()).status_code == 200
    _gate(env, keys[2], merge_mode="hold", archive=True)
    second = _confirm(env, run_id, keys[2], "ck2")
    assert _run_job(second["job"]["job_id"])[0]["status"] == "succeeded"
    assert _run_cleanup(env)["status"] == "succeeded" and _cleanups(env)[-1]["result"] == "deleted"
    items = [i for i in inbox_routes._project_git_archives(PROJECT) if i.get("kind") == checkpoints.KIND_GATE]
    by_sha = {i["source_sha"]: i for i in items}
    assert set(by_sha) == {sha_a, sha_b} and all(i["consumable"] is False for i in items)
    assert {by_sha[sha_a]["decision_id"], by_sha[sha_b]["decision_id"]} == {
        first["decision"]["decision_id"], second["decision"]["decision_id"]}
    cp_a = by_sha[sha_a]["archive_id"]
    out = inbox_routes._restore_group_git_archive(FA, "u_kim", archive_id=cp_a)["result"]
    assert (out["sha"], out["consumed"], out["outcome"]) == (sha_a, False, "restored")
    assert _rev(r["wt"], "HEAD") == sha_a and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    again = inbox_routes._restore_group_git_archive(FA, "u_kim", archive_id=cp_a)["result"]
    assert again["outcome"] == "already_at_checkpoint" and again["last_restored_at"]
    for i in items:                                                           # nothing consumed
        assert _rev(r["base"], i["head_ref"]) == i["source_sha"]
    assert len(_sql(env["db"], "SELECT * FROM gate_archive_checkpoints")) == 2
    with pytest.raises(GitServiceError) as conflict:                          # the slot is never moved
        inbox_routes._restore_group_git_archive(FA, "u_kim", archive_id=by_sha[sha_b]["archive_id"])
    assert conflict.value.code == "restore_conflict" and _rev(r["wt"], "HEAD") == sha_a
    with pytest.raises(GitServiceError) as legacy:                            # the v0.1 restore: its own record only
        inbox_routes._restore_group_git_archive(FA, "u_kim")
    assert legacy.value.code == "archive_not_found"
