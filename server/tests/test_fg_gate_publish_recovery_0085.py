"""flowgate.v02.0085 T#3 (D0007 §7.2 / §7.3; §8-7): the end cleanup of the confirm gate's ``gate_publish`` job on a
real Git repository.

flowgate.v02.0098 T#2 (TR0009): moved unchanged from ``test_fg_gate_publish_0085``, whose harness, fixtures
and shared helpers it imports. ``--dist loadfile`` gives one file to one worker, and the single file's serial
time alone exceeded the 1800 s Self-check limit.
The retry / cancel / permission / credential / recovery close cases (§8-12 to §8-18) are in
``test_fg_gate_publish_retry_0085`` for the same reason.
"""
from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fg_card_settings import FA, _h, _sql, db_path, env, setting  # noqa: E402,F401
from test_fg_gate_intake_0085 import (  # noqa: E402,F401
    _attempt, _current, _decide, _decisions, _finish, _gate_events, _jobs, _nodes, _start, _states, product, allowed)
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    PROJECT, _git, _rev, _work, _gate, _run_job, _decision, _effects, _job_row, _merges, _confirm, _act, _version,
    _requests, _start_linear, _evidence, _cleanup_jobs, _run_cleanup, _flow, _cleanups, _run_status,
    _parked_after_merge, _parked_without_effect, git_env)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


# ── §8-7: the end cleanup judges the real HEAD ───────────────────────────────

@pytest.mark.parametrize("case", ["gate2_hold", "tail_card"])
def test_an_old_archive_never_lets_a_newer_head_go(git_env, case):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env = r["env"]
    keys = _flow(env, ("G", "T", "G") if case == "gate2_hold" else ("G", "T"))
    run_id = _start(env)["run_id"]
    sha_a = _work(r, "a.txt")
    _gate(env, keys[0], merge_mode="hold", archive=True)
    body = _confirm(env, run_id, keys[0], "g1")
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"
    sha_next = _work(r, "b.txt")                                             # SHA-B / the tail card's SHA-C
    _finish(env, keys[1], 90)
    if case == "gate2_hold":
        if _states(env)[keys[2]] == "pending":                               # an idle run's propagation waits
            resume = env["client"].post(f"/api/v1/runs/{run_id}/resume", json={}, headers=_h())
            assert resume.status_code == 200, resume.text
        assert _states(env)[keys[2]] == "open"
        _confirm(env, run_id, keys[2], "g2", expect=200)                     # hold, archive OFF: done at once
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "completed"}
    assert _cleanups(env) == [] and _run_cleanup(env)["status"] == "succeeded"     # its own job (D0007 §7.1)
    records = _cleanups(env)
    assert len(records) == 1
    rec = records[0]
    assert (rec["result"], rec["end_head_sha"]) == ("retained_uncovered_head", sha_next)
    bases = json.loads(rec["evidence_json"])["bases"]
    assert any(b["kind"] == "archive_checkpoint" and b["observed"] == sha_a and b["reachable"] is False for b in bases)
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 1 and r["wt"].is_dir()
    assert _rev(r["base"], f"refs/heads/{r['branch']}") == sha_next


@pytest.mark.parametrize("keep", [False, True], ids=["no_keep", "keep_branch"])
def test_a_head_held_by_its_merge_target_may_go_and_comes_back_at_the_recorded_head(git_env, keep):
    from flowgate.gate_decisions import cleanup
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env = r["env"]
    (g,) = _flow(env, ("G",))
    run_id = _start(env)["run_id"]
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", keep_branch=keep)
    body = _confirm(env, run_id, g, "k1")
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "completed"}
    assert _run_cleanup(env)["status"] == "succeeded"
    (rec,) = _cleanups(env)
    if keep:
        assert (rec["result"], rec["result_reason"], rec["keep_requested"]) == ("retained", "keep_branch_requested", 1)
        assert r["wt"].is_dir() and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
        return
    assert (rec["result"], rec["basis_kind"], rec["basis_ref"], rec["end_head_sha"], rec["reachable"]) == (
        "deleted", "merged_target", "refs/heads/main", sha, 1)
    assert not r["wt"].exists() and _rev(r["base"], f"refs/heads/{r['branch']}") is None
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 0
    assert _rev(r["base"], f"refs/flowgate/gate-pin/{body['decision']['decision_id']}") == sha   # pins stay
    start, refused = cleanup.restore_point(PROJECT, FA)
    assert (start, refused) == (sha, None)
    assert _rev(r["base"], f"refs/heads/{r['branch']}") == sha
    assert svc.ensure_worktree(PROJECT, "v02", FA, trigger="gate_restore", start_point=start) == "ok"
    assert _rev(r["wt"], "HEAD") == sha and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    assert cleanup.restore_point(PROJECT, FA) == (None, None)                # restored: nothing more to do


@pytest.mark.parametrize("case", ["uncommitted", "moved", "basis_lost", "remove_fails"])
def test_the_end_cleanup_keeps_the_slot_on_doubt(git_env, monkeypatch, case):
    from flowgate.gate_decisions import cleanup, git_adapter
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env = r["env"]
    (g,) = _flow(env, ("G",))
    run_id = _start(env)["run_id"]
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)                           # the archive holds the final HEAD
    body = _confirm(env, run_id, g, "d1")
    did = body["decision"]["decision_id"]
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"
    assert len(_cleanup_jobs(env)) == 1 and _cleanups(env) == []          # the completion's job, not run yet
    real_rev, real_git = git_adapter.rev, svc._run_git
    if case == "uncommitted":
        (r["wt"] / "left.tmp").write_text("not committed\n", encoding="utf-8")
    elif case == "moved":
        reads = {"n": 0}

        def moving(repo, ref):
            if ref == f"refs/heads/{r['branch']}":
                reads["n"] += 1
                if reads["n"] >= 2:
                    return "f" * 40                                          # the HEAD moved before the delete
            return real_rev(repo, ref)

        monkeypatch.setattr(git_adapter, "rev", moving)
    elif case == "basis_lost":
        _git(["update-ref", "-d", f"refs/flowgate/gate-archive/{did}"], cwd=r["base"])
    else:
        def failing(args, **kwargs):
            if args[:2] == ["worktree", "remove"]:
                return subprocess.CompletedProcess(["git", *args], 1, "", "locked")
            return real_git(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", failing)
    job = _run_cleanup(env)
    assert job["status"] == ("failed" if case == "remove_fails" else "succeeded")
    (rec,) = _cleanups(env)
    assert rec["job_id"] == job["job_id"]
    expected = {"uncommitted": ("retained", "uncommitted_files"), "moved": ("retained_uncovered_head",
                                                                           "moved_before_delete"),
                "basis_lost": ("retained_uncovered_head", "no_basis_holds_head"),
                "remove_fails": ("failed", "worktree_remove_failed")}[case]
    assert (rec["result"], rec["result_reason"]) == expected
    assert rec["end_head_sha"] == sha
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 1         # never shown as free
    assert _rev(r["base"], f"refs/heads/{r['branch']}") == sha


def test_the_keep_requests_are_ored_over_every_round_and_skip_what_does_not_count(product):
    from flowgate.gate_decisions import cleanup, repository as repo

    def decision(status, mode, keep, n, decision="confirm"):
        row = repo.insert_decision({
            "group_id": FA, "run_id": "run_k", "node_key": f"g{n}", "attempt_id": f"att_k{n}", "revision_no": 1,
            "actor_user_id": "u_kim", "request_id": f"rk{n}", "request_fingerprint": "f" * 64, "decision": decision,
            "status": repo.REJECTED if decision == "reject" else (repo.SUCCEEDED if status == "succeeded"
                                                                  else repo.ACCEPTED),
            "settings": {"effective": {"merge_mode": mode, "keep_branch": keep}}})
        if status in ("canceled", "recovery_required"):
            assert repo.move(row["decision_id"], 1, status)
        if status == "abandoned":
            assert repo.move(row["decision_id"], 1, repo.RECOVERY_REQUIRED)
            assert repo.move(row["decision_id"], 2, repo.ABANDONED)
        return row["decision_id"]

    counted = [decision("succeeded", "merge", True, 1), decision("abandoned", "commit", True, 2)]
    decision("canceled", "merge", True, 3)
    decision("succeeded", "hold", True, 4)                                   # hold never carries it (preflight)
    decision("succeeded", None, True, 5)
    decision("rejected", "merge", True, 6, decision="reject")
    decision("succeeded", "merge", False, 7)                                  # a later false cancels nothing
    assert sorted(cleanup.keep_requests(FA)) == sorted(counted)
