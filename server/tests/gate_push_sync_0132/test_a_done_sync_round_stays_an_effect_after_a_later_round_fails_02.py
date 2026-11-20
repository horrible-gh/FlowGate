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

@pytest.mark.parametrize('later', ['failed'])
def test_a_done_sync_round_stays_an_effect_after_a_later_round_fails(git_env, pushes, monkeypatch, later):
    from flowgate.gate_decisions import publish
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import conflict, job_store, job_sweeper
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False, contained=True)
    h1 = _advance(r, "main", "theirs.txt")
    tips = []

    def move():
        # Before the second push the remote moves again: on our own file (a conflict) or not (the merge then fails).
        if later == "aborted":
            tips.append(_advance(r, "main", "ours.txt", "remote ours\n"))
        else:
            tips.append(_advance(r, "main", "race.txt"))

    real = _on_push(monkeypatch, {2: move})
    if later == "failed":
        from_push, merges = svc._run_git, []

        def refused_merge(args, **kwargs):
            if args and "merge" in args and "--no-ff" in args:
                merges.append(1)
                if len(merges) == 2:                                         # the second sync's merge cannot run
                    return subprocess.CompletedProcess(args, 1, "", "fatal: simulated merge failure")
            return from_push(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", refused_merge)
    job, _ = _run_job(jid)
    effects = _effects(env, did)
    assert (effects["merge"]["status"], _ev(effects["merge"])["changed"]) == ("succeeded", False)   # contained
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    s1 = sev["rounds"][0]["sync_commit"]
    assert len(sev["rounds"]) == 1 and _parents(base, s1) == [frozen, h1]
    assert _rev(base, "refs/heads/main") == s1                              # the local target moved to S1
    assert effects["push"]["status"] == "failed"
    if later == "aborted":
        assert (job["status"], job["phase_note"]) == ("recovery_required", "gate_conflict")
        assert (sync["status"], sev["conflict"], sev["pre_head"], sev["remote_head"]) == ("pending", True, s1, tips[0])
        assert repo.effects_state(did) == repo.EFFECTS_PRESENT
        assert _decision(env, did)["status"] == "recovery_required"
        conflict.abort_merge(FA, int(sev["merge_id"]))
        _due(jid)
        job_sweeper.step_recovery_required_retry()
        assert job_store.get_job(jid)["status"] == "pending"
        monkeypatch.setattr(svc, "_run_git", real)
        job, _ = _run_job(jid)
        code = "push_sync_conflict_aborted"
    else:
        code = "sync_merge_failed"
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_recovery", code)
    sync, sev = _effects(env, did)["remote_sync"], _ev(_effects(env, did)["remote_sync"])
    assert (sync["status"], sev["changed"], [x["sync_commit"] for x in sev["rounds"]]) == ("failed", False, [s1])
    decision = repo.get(did)
    assert repo.effects_state(did) == repo.EFFECTS_PRESENT == publish.effects_now(decision)
    assert decision["status"] == "recovery_required"                        # never "failed" (no effect)
    refused = _act(env, run_id, g, did, "cancel", {"request_id": f"later-{later}", "expected_decision_version":
                                                   _version(env, did)}, 409)
    assert refused["error"]["code"] == "gate_effects_present"
    _stop_holds(env, run_id, g, did)                                         # nor does a Stop cancel it
    assert _rev(base, "refs/heads/main") == s1 and _rev(r["bare"], "refs/heads/main") == tips[0]
    monkeypatch.setattr(svc, "_run_git", real)
    _act(env, run_id, g, did, "resolve", {"request_id": f"later-abandon-{later}", "expected_decision_version":
                                          _version(env, did), "resolution": "abandon",
                                          "reason": "the later sync failed", **_evidence(env, g)}, 200)
    assert _rev(base, f"refs/flowgate/gate-recovery/{did}-sync") == s1        # the last done round is kept
    assert _merges(base) == 1 and _effects(env, did)["merge"]["attempt_count"] == 1
