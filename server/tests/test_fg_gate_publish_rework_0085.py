"""flowgate.v02.0085 T#3 rework (review rej_01M5NT4F9CPN06M6): one focused regression test per defect of the
review, on the ``gate_publish`` harness and a real Git repository.

flowgate.v02.0098 T#2 (TR0009): moved unchanged from ``test_fg_gate_publish_0085``, whose harness, fixtures
and shared helpers it imports. ``--dist loadfile`` gives one file to one worker, and the single file's serial
time alone exceeded the 1800 s Self-check limit.
From the end cleanup under its locks on, the cases are in ``test_fg_gate_publish_rework_recovery_0085``
for the same reason.
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


# ── T#3 rework: the review's defects, each with its focused regression test ──

def test_a_groups_gate_jobs_run_in_acceptance_order_and_hold_the_integration_until_their_pin(git_env):
    """D0007 §7.3: one group's publishes run in their acceptance order, and a result integrated after an acceptance
    never enters that decision's snapshot (the integration waits for the pin, then becomes the next one's)."""
    from flowgate.graph_run import result_merge
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_runner, job_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "o1")
    did1, job1 = body["decision"]["decision_id"], body["job"]["job_id"]
    did2, job2 = _second_publish(env, run_id)
    seqs = {j["job_id"]: json.loads(j["payload"])["group_seq"] for j in _sql(
        env["db"], "SELECT job_id, payload FROM operation_job WHERE kind = 'gate_publish'")}
    assert seqs == {job1: 1, job2: 2}
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job2])
    assert job_runner.try_claim(job2) is None                                 # the earlier job is unfinished
    assert job_store.gate_publish_predecessor(job_store.get_job(job2))["job_id"] == job1
    side = _side_commit(r, sha, "late.txt")
    row = {"group_id": FA, "run_id": run_id, "node_key": t, "attempt_id": "att_side_1", "result_commit": side}
    assert result_merge._merge(row) == (result_merge.PENDING, result_merge.GATE_SNAPSHOT_RESERVED)
    assert _rev(r["wt"], "HEAD") == sha                                       # nothing integrated
    job, _ = _run_job(job1)
    assert job["status"] == "succeeded" and _decision(env, did1)["frozen_sha"] == sha
    # the second decision still holds the group branch until its own pin
    assert result_merge._merge(row) == (result_merge.PENDING, result_merge.GATE_SNAPSHOT_RESERVED)
    job, _ = _run_job(job2)                                                   # now its turn: it pins HEAD
    assert _decision(env, did2)["frozen_sha"] == sha
    assert job["status"] == "recovery_required" and job["last_error_code"] == "gate_not_open"   # a stand-in gate
    status, merged = result_merge._merge(row)                                 # the pins are taken: it goes on
    assert status == result_merge.INTEGRATED and _rev(r["wt"], "HEAD") == merged != sha
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did1}") == sha       # the snapshots stay as they were
    assert _decision(env, did1)["frozen_sha"] == _decision(env, did2)["frozen_sha"] == sha


@pytest.mark.parametrize("phase", ["merge", "push", "archive", "push_after_close"])
def test_a_lease_lost_during_a_git_step_writes_no_evidence_and_the_next_owner_judges_it(git_env, monkeypatch, phase):
    """Every worker write is fenced by the job's lease and the decision's state: a worker that lost its lease (or whose
    decision was closed) during a Git command writes nothing; the phase stays ``unknown`` for the next owner."""
    from flowgate.gate_decisions import git_adapter
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True, archive=True)
    body = _confirm(env, run_id, g, "f1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    target_at_accept = _decision(env, did)["target_sha"]
    calls = []

    def lose():
        with get_store().transaction():
            if phase == "push_after_close":                                   # what a resolve's close leaves
                get_store()._execute("UPDATE operation_job SET status = 'cancelled', lease_token = NULL, "
                                     "lease_until = NULL WHERE job_id = ?", [job_id])
                get_store()._execute("UPDATE gate_decisions SET status = 'abandoned', recovery_reset_required = 1, "
                                     "decision_version = decision_version + 1 WHERE decision_id = ?", [did])
            else:
                get_store()._execute("UPDATE operation_job SET lease_token = 'lt_other_owner' WHERE job_id = ?",
                                     [job_id])

    owner, name = (git_adapter, {"merge": "merge", "archive": "create_ref"}.get(phase, "push"))
    real = getattr(owner, name)

    def step(*args, **kwargs):
        out = real(*args, **kwargs)
        if not calls and (phase != "archive" or args[1] == f"refs/flowgate/gate-archive/{did}"):
            calls.append(1)
            lose()
        return out

    monkeypatch.setattr(owner, name, step)
    job, _ = _run_job(job_id)
    effect_phase = "push" if phase == "push_after_close" else phase
    assert calls and _effects(env, did)[effect_phase]["status"] == "unknown"   # the evidence before the command
    assert _states(env)[g] == "open" and _gate_events(env, g) == []
    if phase == "push_after_close":
        d = _decision(env, did)
        assert (job["status"], d["status"]) == ("cancelled", "abandoned")       # the close stands; nothing more
        assert d["target_sha"] == _rev(r["base"], "refs/heads/main")             # written before the close only
        return
    assert job["status"] == "running" and job["lease_token"] == "lt_other_owner"
    d = _decision(env, did)
    assert d["status"] == "accepted"
    if phase == "merge":
        assert d["target_sha"] == target_at_accept and _effects(env, did)["merge"]["sha_after"] is None
    if phase == "archive":
        assert _sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,)) == []
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    merged = _rev(r["base"], "refs/heads/main")
    assert _merges(r["base"]) == 1 and _rev(r["bare"], "refs/heads/main") == merged and merged != sha
    assert len(_sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,))) == 1
    assert {p: e["status"] for p, e in _effects(env, did).items()} == {
        "snapshot": "succeeded", "merge": "succeeded", "push": "succeeded", "archive": "succeeded"}
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]


def test_an_archive_cut_off_after_its_ref_is_never_canceled_as_without_effect(git_env, monkeypatch):
    """hold + archive cut off between the ref and its checkpoint: the intent was stored first, so neither the common
    cancel nor a Stop releases it as "no effect" - also when no phase row tells of the ref (the ref itself is read)."""
    from flowgate.gate_decisions import repository
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "x1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = repository.insert_checkpoint, []

    def crash(row):
        calls.append(row)
        if len(calls) == 1:
            raise RuntimeError("process died after the archive ref")
        return real(row)

    monkeypatch.setattr(repository, "insert_checkpoint", crash)
    job, _ = _run_job(job_id)
    assert job["status"] == "running" and _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _effects(env, did)["archive"]["status"] == "unknown" and repository.effects_state(did) == "unknown"
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "x2", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_effects_unknown"
    _sql(env["db"], "DELETE FROM gate_decision_effects WHERE decision_id = ? AND phase = 'archive'", (did,))
    assert repository.effects_state(did) == "none"                            # only the ref tells it now
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "x3", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_effects_unknown" and _requests(env) == []
    stop = env["client"].post(f"/api/v1/runs/{run_id}/stop", json={}, headers=_h())
    assert stop.status_code in (200, 202), stop.text
    d = _decision(env, did)
    assert (d["status"], d["last_error_code"]) == ("recovery_required", "stop_requested")      # never canceled
    assert _job_row(env, job_id)["status"] == "recovery_required"
    assert _run_status(env, run_id)["status"] == "stopping" and _states(env)[g] == "open"
    # The Stop ends only through the explicit close of this intent (a retry would be asked to cancel again).
    out = _act(env, run_id, g, did, "resolve", {"request_id": "x4", "expected_decision_version": _version(env, did),
                                                "resolution": "abandon", "reason": "stopped, archive cut off",
                                                **_evidence(env, g)}, 200)
    assert out["decision"]["status"] == "abandoned" and _job_row(env, job_id)["phase_note"] == "recovery_abandoned"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "stopped"}
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha     # nothing removed, nothing reverted
    assert _effects(env, did)["snapshot"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}"


def test_a_pin_left_by_a_cut_off_snapshot_is_taken_back_although_the_head_moved(git_env, monkeypatch):
    """A pin written whose SHA was not stored yet is the snapshot: the resume takes it back instead of the HEAD of now
    (never a permanent pin_ref_mismatch)."""
    from flowgate.gate_decisions import git_adapter
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha_a = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "q1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = git_adapter.create_ref, []

    def crash(repo_root, ref, target):
        out = real(repo_root, ref, target)
        if ref == f"refs/flowgate/gate-pin/{did}" and not calls:
            calls.append(1)
            raise RuntimeError("process died after the pin")
        return out

    monkeypatch.setattr(git_adapter, "create_ref", crash)
    job, _ = _run_job(job_id)
    assert job["status"] == "running" and _decision(env, did)["frozen_sha"] is None
    assert _rev(r["base"], f"refs/flowgate/gate-pin/{did}") == sha_a
    sha_b = _work(r, "later.txt")                                              # the group HEAD moved meanwhile
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _decision(env, did)["frozen_sha"] == sha_a != sha_b
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha_a
    snapshot = json.loads(_effects(env, did)["snapshot"]["evidence_json"])
    assert snapshot["pin_taken_back"] is True


@pytest.mark.parametrize("cut", ["worktree", "branch", "ledger", "record"])
def test_a_cleanup_cut_off_at_any_step_keeps_its_plan_and_restores_the_real_end_head(git_env, monkeypatch, cut):
    """The plan (the real end HEAD, the basis) is on the job before the first delete; every step is resumed; the
    slot comes back at that HEAD whatever step the cleanup ended at."""
    from flowgate.gate_decisions import cleanup, repository
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, run_id, sha, cj = _completed_merge_flow(r)
    calls = []
    if cut == "record":
        real_insert = repository.insert_cleanup

        def failing(row):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("process died before the record")
            return real_insert(row)
        monkeypatch.setattr(repository, "insert_cleanup", failing)
    else:
        name = {"worktree": "_step_worktree", "branch": "_step_branch", "ledger": "_step_ledger"}[cut]
        real_step = getattr(cleanup, name)

        def cut_after(*args):
            out = real_step(*args)
            if not calls:
                calls.append(1)
                raise RuntimeError(f"process died after the {cut} step")
            return out
        monkeypatch.setattr(cleanup, name, cut_after)
    job, _ = _run_job(cj)
    assert job["status"] == "running" and job["phase"] == cut and calls
    plan = json.loads(job["evidence"])["plan"]
    assert (plan["end_head"], plan["branch"], plan["record"]["basis_kind"]) == (sha, r["branch"], "merged_target")
    assert cleanup.restore_point(PROJECT, FA) == (None, "git_busy")           # never "nothing to restore"
    assert _expire_and_reconcile(cj)["status"] == "pending"
    job, _ = _run_job(cj)
    assert job["status"] == "succeeded"
    (rec,) = _cleanups(env)
    assert (rec["result"], rec["end_head_sha"], rec["job_id"]) == ("deleted", sha, cj)
    assert not r["wt"].exists() and _rev(r["base"], f"refs/heads/{r['branch']}") is None
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 0
    start, refused = cleanup.restore_point(PROJECT, FA)
    assert (start, refused) == (sha, None) and _rev(r["base"], f"refs/heads/{r['branch']}") == sha
    assert svc.ensure_worktree(PROJECT, "v02", FA, trigger="gate_restore", start_point=start) == "ok"
    assert _rev(r["wt"], "HEAD") == sha
