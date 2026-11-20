"""Independently selectable scenario; shared harness stays in test_fg_gate_push_sync_0132."""
from __future__ import annotations

from test_fg_gate_push_sync_0132 import (
    FA,
    NON_BASE,
    PROJECT,
    Path,
    _accepted,
    _act,
    _advance,
    _ancestor,
    _checkpoint,
    _confirm,
    _confirmed,
    _decision,
    _due,
    _effects,
    _ev,
    _evidence,
    _expire_and_reconcile,
    _gate,
    _gate_events,
    _gate_merge,
    _git,
    _merges,
    _on_push,
    _parents,
    _push_rejected_once,
    _rev,
    _run_job,
    _side_commit,
    _sql,
    _start,
    _start_linear,
    _states,
    _stop_holds,
    _target,
    _version,
    _work,
    db_path,
    edge,
    env,
    git_env,
    json,
    node,
    product,
    pushes,
    pytest,
    pytestmark,
    shutil,
    subprocess,
    sys,
)

def test_a_sync_conflict_hand_off_cut_off_before_its_record_is_the_active_conflict_after_the_resume(
        git_env, pushes, monkeypatch):
    from flowgate.gate_decisions import git_adapter, publish, recovery
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import conflict, job_store, job_sweeper, merge_target
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False, name="shared.txt",
                                                         text="group side\n", contained=True)
    h1 = _advance(r, "main", "shared.txt", "remote side\n")
    real_hand_off, real_fail = git_adapter._hand_off_conflict, merge_target.fail_attempt

    def cut(*args, **kwargs):
        real_hand_off(*args, **kwargs)
        raise RuntimeError("process died right after the conflict hand-off")

    monkeypatch.setattr(git_adapter, "_hand_off_conflict", cut)
    monkeypatch.setattr(merge_target, "fail_attempt", lambda *a, **k: None)   # nothing ran after the kill
    job, _ = _run_job(jid)
    assert job["status"] == "running"
    effects = _effects(env, did)
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    mid = int(sev["merge_id"])
    assert sync["status"] == "unknown" and svc.db_git.get_session(mid)["status"] == "open"
    assert (effects["merge"]["status"], _ev(effects["merge"])["changed"]) == ("succeeded", False)   # contained
    merge_mid = _ev(effects["merge"]).get("merge_id")
    assert mid != merge_mid
    # Already before the resume records it, the one lookup reads the sync's attempt (not the approved merge's).
    assert publish._conflict_merge_id(job_store.get_job(jid)) == mid
    monkeypatch.setattr(git_adapter, "_hand_off_conflict", real_hand_off)
    monkeypatch.setattr(merge_target, "fail_attempt", real_fail)
    _expire_and_reconcile(jid)
    job, _ = _run_job(jid)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_conflict",
                                                                          "merge_conflict")
    sync, sev = _effects(env, did)["remote_sync"], _ev(_effects(env, did)["remote_sync"])
    assert (sync["status"], sev["conflict"], int(sev["merge_id"]), sev["hand_off_recovered"]) == (
        "pending", True, mid, True)
    assert (sev["pre_head"], sev["remote_head"]) == (frozen, h1)
    decision = repo.get(did)
    assert decision["status"] == "failed" and repo.effects_state(did) == repo.EFFECTS_NONE   # nothing changed outside
    assert publish._conflict_merge_id(job) == mid and recovery._conflict_open(decision) is True
    refused = _act(env, run_id, g, did, "retry", {"request_id": "cut-retry", "expected_decision_version":
                                                  _version(env, did)}, 409)
    assert refused["error"]["code"] == "gate_decision_not_retryable" and "merge_conflict_open" in json.dumps(refused)
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "cut-cancel", "expected_decision_version":
                                                   _version(env, did)}, 409)
    assert refused["error"]["code"] == "gate_decision_not_cancelable" and "merge_conflict_open" in json.dumps(refused)
    _due(jid)
    job_sweeper.step_recovery_required_retry()                               # UNDECIDABLE while the review is open
    assert job_store.get_job(jid)["status"] == "recovery_required"
    _stop_holds(env, run_id, g, did)                                         # the Stop waits for the sync's review
    assert _rev(base, "refs/heads/main") == frozen and _rev(r["bare"], "refs/heads/main") == h1
    conflict.abort_merge(FA, mid)                                             # the review's [abort]
    _due(jid)
    job_sweeper.step_recovery_required_retry()
    assert job_store.get_job(jid)["status"] == "pending"
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_sync_conflict_aborted")
    assert _effects(env, did)["remote_sync"]["status"] == "failed"
    assert publish._conflict_merge_id(job) == merge_mid
    assert _merges(base) == 0 and len(pushes) == 1 and _effects(env, did)["merge"]["attempt_count"] == 1
