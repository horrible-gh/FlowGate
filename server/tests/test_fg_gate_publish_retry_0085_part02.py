"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_retry_0085.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_retry_0085 import (
    FA,
    PROJECT,
    Path,
    _act,
    _attempt,
    _cleanup_jobs,
    _cleanups,
    _confirm,
    _current,
    _decide,
    _decision,
    _decisions,
    _effects,
    _evidence,
    _finish,
    _flow,
    _gate,
    _gate_events,
    _git,
    _h,
    _job_row,
    _jobs,
    _merges,
    _nodes,
    _parked_after_merge,
    _parked_without_effect,
    _requests,
    _rev,
    _run_cleanup,
    _run_job,
    _run_status,
    _sql,
    _start,
    _start_linear,
    _states,
    _version,
    _work,
    allowed,
    approval_retry_policy,
    base64,
    db_path,
    env,
    git_env,
    json,
    logging,
    product,
    pytest,
    pytestmark,
    setting,
    shutil,
    subprocess,
    sys,
)

def test_effects_confirmed_closes_with_one_confirm_and_keeps_a_paused_run_paused(git_env, monkeypatch):
    r = git_env
    env, g, t, run_id, did, job_id, sha, claim = _parked_after_merge(r, monkeypatch, request_id="e1")
    pause = env["client"].post(f"/api/v1/runs/{run_id}/pause", json={}, headers=_h())
    assert pause.status_code == 200, pause.text
    _git(["push", str(r["bare"]), "main"], cwd=r["base"])                    # the operator pushed it
    v = _version(env, did)
    body = {"request_id": "e2", "expected_decision_version": v, "resolution": "effects_confirmed",
            "reason": "pushed by the operator", **_evidence(env, g)}
    out = _act(env, run_id, g, did, "resolve", body, 200)
    d = _decision(env, did)
    assert (d["status"], d["close_kind"], d["closed_by"], d["close_reason"]) == (
        "succeeded", "effects_confirmed", "u_kim", "pushed by the operator")
    assert out["decision"]["close"]["kind"] == "effects_confirmed"
    assert _job_row(env, job_id)["status"] == "succeeded" and _attempt(env, run_id, g)["status"] == "ended"
    causes = [e["cause"] for e in _sql(env["db"], "SELECT cause FROM graph_run_events WHERE node_key = ? "
                                                    "ORDER BY event_seq", (g,))]
    assert causes.count("gate_confirmed") == 1 and causes[-1] == "gate_recovery_resolved"
    assert _states(env)[g] == "done" and _states(env)[t] == "pending"        # paused: what follows waits
    push = _effects(env, did)["push"]
    assert (push["status"], push["recovery_state"]) == ("succeeded", "confirmed")
    assert _act(env, run_id, g, did, "resolve", body, 200)["decision"]["status"] == "succeeded"   # replay
    conflict = _act(env, run_id, g, did, "resolve", {**body, "reason": "other"}, 409)
    assert conflict["error"]["code"] == "gate_request_conflict"
    retry = _act(env, run_id, g, did, "retry", {"request_id": "e3", "expected_decision_version": _version(env, did)},
                 409)
    assert retry["error"]["code"] == "gate_decision_not_retryable"
    from modules.flow_gate.services.git import job_store
    with pytest.raises(job_store.LeaseLost):
        job_store.job_write(claim.ctx, status="failed")                     # the discarded lease writes nothing
    resume = env["client"].post(f"/api/v1/runs/{run_id}/resume", json={}, headers=_h())
    assert resume.status_code == 200, resume.text
    assert _states(env)[t] == "open"
def test_abandon_closes_without_the_remote_and_blocks_the_gate_until_a_round_split(git_env, monkeypatch):
    from flowgate.gate_decisions import cleanup, repository as repo
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "a1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    shutil.move(str(r["bare"]), str(r["bare"]) + ".gone")                    # the remote was deleted
    job, claim = _run_job(job_id)
    assert job["status"] in ("recovery_required", "retry_wait")
    if job["status"] == "retry_wait":                                        # an unreadable remote retries first
        monkeypatch.setenv("FLOWGATE_RETRY_MAX_ATTEMPTS", "1")
        job, claim = _run_job(job_id)
    assert job["status"] == "recovery_required" and _decision(env, did)["status"] == "recovery_required"
    merged = _rev(r["base"], "refs/heads/main")
    # A writer holds the job: the close waits for it.
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET lease_token = 'lt_x', lease_until = '2999-01-01T00:00:00+00:00' "
                             "WHERE job_id = ?", [job_id])
    v = _version(env, did)
    reviewed = _evidence(env, g)
    busy = _act(env, run_id, g, did, "resolve", {"request_id": "a2", "expected_decision_version": v,
                                                 "resolution": "abandon", "reason": "remote deleted", **reviewed}, 409)
    assert busy["error"]["code"] == "gate_job_active" and _version(env, did) == v
    with get_store().transaction():
        get_store()._execute("UPDATE operation_job SET lease_token = NULL, lease_until = NULL WHERE job_id = ?",
                             [job_id])
    events_before = len(_gate_events(env, g))
    out = _act(env, run_id, g, did, "resolve", {"request_id": "a3", "expected_decision_version": v,
                                                "resolution": "abandon", "reason": "remote deleted", **reviewed}, 200)
    assert out["recovery_reset_required"] is True
    d = _decision(env, did)
    assert (d["status"], d["recovery_reset_required"], d["close_kind"], d["closed_by"]) == (
        "abandoned", 1, "abandon", "u_kim")
    job = _job_row(env, job_id)
    assert (job["status"], job["phase_note"]) == ("cancelled", "recovery_abandoned")
    assert _attempt(env, run_id, g)["status"] == "ended" and _states(env)[g] == "pending"
    assert len(_gate_events(env, g)) == events_before                       # no confirm, no reject
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}") == sha
    assert merged == sha  # fast-forward: the snapshot recovery ref already retains the applied SHA
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-merge") is None
    effects = _effects(env, did)
    assert (effects["merge"]["recovery_state"], effects["merge"]["recovery_ref"]) == (
        "confirmed", None)
    assert effects["snapshot"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}"
    assert all(e["reviewed_evidence_version"] is not None for e in effects.values())
    assert _rev(r["base"], "refs/heads/main") == merged                      # nothing reverted
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "blocked_recovery"}
    wire = _nodes(_current(env))[g]["gate"]
    assert (wire["recovery_reset_required"], wire["can_decide"], wire["archived"]) == (True, False, False)
    refused = _confirm(env, run_id, g, "a4", expect=409)
    assert refused["error"]["code"] == "node_not_open" and refused["error"]["params"]["reason"] == \
        "recovery_reset_required"
    assert setting(env, FA, g, "merge_mode", "hold", expect=409)["error"]["code"] == "gate_settings_locked"
    resumed = env["client"].post(f"/api/v1/runs/{run_id}/resume", json={"mode": "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text
    assert _states(env)[g] == "pending"                                      # a command does not reopen it
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "blocked_recovery"}
    assert _decision(env, did)["recovery_reset_required"] == 1
    with pytest.raises(repo.DecisionConflict):                                # abandoned keeps holding its attempt
        repo.insert_decision({"group_id": FA, "run_id": run_id, "node_key": g, "attempt_id": d["attempt_id"],
                              "revision_no": 1, "actor_user_id": "u_kim", "request_id": "a5",
                              "request_fingerprint": "f" * 64, "decision": "confirm", "status": "succeeded"})
    assert cleanup.blocker(FA, run_id) in ("run_not_completed", "recovery_reset_required")
    from modules.flow_gate.services.git import job_store
    with pytest.raises(job_store.LeaseLost):
        job_store.job_write(claim.ctx, status="succeeded")                  # the discarded lease's late result
    assert _job_row(env, job_id)["status"] == "cancelled"
@pytest.mark.parametrize("stage,cause,code", [
    ("fetch", "timeout", "git_error"), ("fetch", "network", "git_error"),
    ("fetch", "ref_lock", "git_error"), ("merge", "timeout", "git_error"),
    ("merge", "network", "git_error"), ("merge", "ref_lock", "git_error"),
    ("push", "unknown", "push_rejected"), ("unknown", "unknown", "git_busy"),
])
def test_approval_stage_cause_transients_keep_retrying_without_release(approval_retry_policy, stage, cause, code):
    f = approval_retry_policy
    for _ in range(4):
        assert f.fail(code, stage, cause).state == f.ap.QUEUED
        assert f.ctx.job["status"] == "retry_wait"
        assert f.evidence()["attempt_error"] == {
            "code": code, "stage": stage, "cause": cause, "diagnostic": "unexplained failure"}
    assert f.releases == []
@pytest.mark.parametrize("code,stage,cause", [
    ("target_diverged", "merge", "conflict_other"),
    ("base_diverged", "merge", "conflict_other"),
    ("git_error", "fetch", "auth"), ("push_rejected", "push", "auth"),
])
def test_approval_persistent_candidates_release_on_second_observation(approval_retry_policy, code, stage, cause):
    f = approval_retry_policy
    assert f.fail(code, stage, cause).state == f.ap.QUEUED
    # Job evidence, not process-local memory, carries the count across claims.
    f.ctx.job = json.loads(json.dumps(f.ctx.job))
    assert f.fail(code, stage, cause).state == f.ap.FAILED
    assert f.releases == [("failed", code)]
    assert f.evidence()["publish_retry"]["stop_reason"] == "persistent_limit"
def test_approval_unknown_streak_resets_on_diagnostic_stage_and_cause_changes(approval_retry_policy):
    f = approval_retry_policy
    for _ in range(2):
        f.fail(diagnostic="first failure")
    f.fail(diagnostic="different failure")
    assert f.evidence()["publish_retry"]["consecutive"] == 1
    f.fail(stage="merge", diagnostic="different failure")
    assert f.evidence()["publish_retry"]["consecutive"] == 1
    f.fail(stage="merge", cause="timeout", diagnostic="different failure")
    f.fail(stage="merge", diagnostic="different failure")
    assert f.evidence()["publish_retry"]["consecutive"] == 1
    assert f.fail(stage="merge", diagnostic="different   failure").state == f.ap.QUEUED
    assert f.fail(stage="merge", diagnostic="different failure").state == f.ap.FAILED
    assert len(f.releases) == 1
    assert f.evidence()["publish_retry"]["stop_reason"] == "unknown_limit"
