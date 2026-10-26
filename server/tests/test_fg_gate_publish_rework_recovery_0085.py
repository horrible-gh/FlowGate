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


@pytest.mark.parametrize("action", ["retry", "cancel", "resolve"])
def test_two_copies_of_one_recovery_request_answer_the_first_ones_result(git_env, monkeypatch, action):
    """Both copies found no stored key before the guard: the guard serializes them and the second answers the
    first's stored result (not gate_context_changed, not a second row); other content is gate_request_conflict."""
    from flowgate.gate_decisions import recovery
    r = git_env
    if action == "cancel":
        env, g, t, run_id, did, job_id = _parked_without_effect(r)
    else:
        env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    body = {"request_id": "dup1", "expected_decision_version": _version(env, did)}
    if action == "resolve":
        body.update({"resolution": "abandon", "reason": "remote gone", **_evidence(env, g)})
    real, seen = recovery._replay, {"n": 0}

    def racing(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] in (1, 3):                                               # each copy's read before the guard
            return None
        return real(*args, **kwargs)

    monkeypatch.setattr(recovery, "_replay", racing)
    expect = 202 if action == "retry" else 200
    first = _act(env, run_id, g, did, action, body, expect)
    second = _act(env, run_id, g, did, action, body, expect)
    assert second["replayed"] is True and second["decision"]["decision_id"] == did and "replayed" not in first
    assert len([q for q in _requests(env) if q["action"] == action]) == 1
    monkeypatch.setattr(recovery, "_replay", real)
    other = _act(env, run_id, g, did, action, {**body, "expected_decision_version": body[
        "expected_decision_version"] + 7}, 409)
    assert other["error"]["code"] == "gate_request_conflict"


def test_resolve_takes_the_reviewed_evidence_as_required_and_refuses_a_stale_one(git_env, monkeypatch):
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    v = _version(env, did)
    reviewed = _evidence(env, g)
    assert reviewed["evidence_refs"] and all(ref.startswith(did + "/") for ref in reviewed["evidence_refs"])
    base = {"request_id": "ev1", "expected_decision_version": v, "resolution": "abandon", "reason": "gone"}
    missing = _act(env, run_id, g, did, "resolve", base, 422)
    assert {e["field"] for e in missing["error"]["params"]["errors"]} == {"evidence_version", "evidence_refs"}
    bad = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": ["not a ref"]}, 422)
    assert {e["field"] for e in bad["error"]["params"]["errors"]} == {"evidence_refs"}
    stale = _act(env, run_id, g, did, "resolve", {**base, **reviewed,
                                                  "evidence_version": reviewed["evidence_version"] - 1}, 409)
    assert (stale["error"]["code"], stale["error"]["params"]["reason"]) == ("gate_context_changed", "evidence_version")
    moved = [ref.rsplit("@", 1)[0] + "@99" if "/push@" in ref else ref for ref in reviewed["evidence_refs"]]
    assert moved != reviewed["evidence_refs"]
    stale_ref = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": moved}, 409)
    assert stale_ref["error"]["params"]["reason"] == "evidence_refs"
    foreign = [ref.replace(did, "gdc_other", 1) for ref in reviewed["evidence_refs"]]
    assert _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": foreign}, 409)[
        "error"]["params"]["reason"] == "evidence_refs"
    assert _requests(env) == [] and _version(env, did) == v and _job_row(env, job_id)["status"] == "recovery_required"
    out = _act(env, run_id, g, did, "resolve", {**base, **reviewed}, 200)
    assert out["decision"]["status"] == "abandoned"
    (stored,) = _requests(env)
    assert json.loads(stored["response_json"])["reviewed_evidence"] == {
        "evidence_version": reviewed["evidence_version"], "evidence_refs": sorted(reviewed["evidence_refs"])}
    event = _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE node_key = ? AND cause = "
                            "'gate_recovery_abandoned'", (g,))
    assert json.loads(event[0]["payload_json"])["reviewed_evidence"]["evidence_refs"] == sorted(
        reviewed["evidence_refs"])
    conflict = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": []}, 409)
    assert conflict["error"]["code"] == "gate_request_conflict"


def test_effects_confirmed_never_closes_an_intent_whose_snapshot_was_not_pinned(git_env):
    """commit mode, push and archive OFF, stopped before its snapshot (another commit sits in its pin): nothing is
    proven - no frozen SHA, no pin at it - so effects_confirmed is refused and nothing follows."""
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    (r["base"] / "README.md").write_text("main moved\n", encoding="utf-8")
    _git(["commit", "-am", "main only"], cwd=r["base"])
    foreign = _rev(r["base"], "HEAD")                                        # not a commit of the group branch
    _gate(env, g, merge_mode="commit")
    body = _confirm(env, run_id, g, "u1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    _git(["update-ref", f"refs/flowgate/gate-pin/{did}", foreign], cwd=r["base"])
    job, _ = _run_job(job_id)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "pin_ref_mismatch")
    d = _decision(env, did)
    assert (d["status"], d["frozen_sha"]) == ("recovery_required", None)
    refused = _act(env, run_id, g, did, "resolve", {"request_id": "u2", "expected_decision_version": d[
        "decision_version"], "resolution": "effects_confirmed", "reason": "looks fine", **_evidence(env, g)}, 409)
    assert refused["error"]["code"] == "gate_effects_unverified"
    assert refused["error"]["params"]["missing"] == ["snapshot", "commit"]
    assert _decision(env, did)["status"] == "recovery_required" and _job_row(env, job_id)["status"] == \
        "recovery_required"
    assert _states(env)[g] == "open" and _gate_events(env, g) == [] and _states(env)[t] == "pending"


def test_an_unwritable_recovery_ref_stays_an_obligation_through_the_round_split(git_env, monkeypatch):
    """abandon goes on when the merge result's recovery ref cannot be written, but that SHA is never recorded as
    kept by another ref (not the pin): it stays an obligation, the end cleanup keeps the slot for it after the round
    split, and writes the ref itself once it can."""
    from types import SimpleNamespace
    from flowgate.gate_decisions import cleanup, git_adapter, repository as repo
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    merged = _rev(r["base"], "refs/heads/main")
    real, broken = git_adapter.create_ref, {"on": True}

    def create_ref(repo_root, ref, target):
        if broken["on"] and ref == f"refs/flowgate/gate-recovery/{did}-merge":
            return "error"
        return real(repo_root, ref, target)

    monkeypatch.setattr(git_adapter, "create_ref", create_ref)
    out = _act(env, run_id, g, did, "resolve", {"request_id": "ob1", "expected_decision_version": _version(env, did),
                                                "resolution": "abandon", "reason": "remote gone",
                                                **_evidence(env, g)}, 200)
    assert out["decision"]["status"] == "abandoned"
    effects = _effects(env, did)
    merge_keep = json.loads(effects["merge"]["evidence_json"])["retention"]
    assert effects["merge"]["recovery_ref"] is None and merge_keep == {
        "sha": merged, "ref": f"refs/flowgate/gate-recovery/{did}-merge", "kept": False}
    assert effects["snapshot"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}"      # its own SHA, kept
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-merge") is None
    assert [o["sha"] for o in repo.retention_obligations(FA)] == [merged]
    # The Time Machine's round split (card 4) clears the flag only; the obligation stays.
    d = _decision(env, did)
    assert repo.move(did, d["decision_version"], recovery_reset_required=False)
    _sql(env["db"], "UPDATE graph_runs SET status = 'idle', idle_reason = 'completed' WHERE run_id = ?", (run_id,))
    with get_store().transaction():
        job = cleanup.request(SimpleNamespace(flow_id=FA, run_id=run_id, jobs=[], project_id=PROJECT))
    assert _run_job(job["job_id"])[0]["status"] == "succeeded"
    rec = _cleanups(env)[-1]
    assert (rec["result"], rec["result_reason"]) == ("retained", "recovery_ref_unpreserved")
    assert r["wt"].is_dir() and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    broken["on"] = False                                                      # the ref can be written again
    with get_store().transaction():
        job = cleanup.request(SimpleNamespace(flow_id=FA, run_id=run_id, jobs=[], project_id=PROJECT))
    assert _run_job(job["job_id"])[0]["status"] == "succeeded"
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-merge") == merged
    assert repo.retention_obligations(FA) == []
    assert _effects(env, did)["merge"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}-merge"
    assert _cleanups(env)[-1]["result_reason"] != "recovery_ref_unpreserved"
