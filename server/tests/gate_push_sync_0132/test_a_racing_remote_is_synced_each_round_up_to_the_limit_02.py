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

@pytest.mark.parametrize('advances,then', [(None, 'retry')], ids=['over_limit_retry'])
def test_a_racing_remote_is_synced_each_round_up_to_the_limit(git_env, pushes, monkeypatch, advances, then):
    from flowgate.gate_decisions import publish
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False)
    assert publish.sync_round_limit() == 3
    real, tips = svc._run_git, []

    def racing(args, **kwargs):
        if args and args[0] == "push" and (advances is None or len(tips) < advances):
            tips.append(_advance(r, branch, f"race{len(tips)}.txt"))   # just before each push
        return real(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", racing)
    job, _ = _run_job(jid)
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    sev = _ev(effects["remote_sync"])
    syncs = [x["sync_commit"] for x in sev["rounds"]]
    for older, newer, tip in zip([a0, *syncs], syncs, tips):                 # one sync per round, chained
        assert _parents(r["base"], newer) == [older, tip]
    remote_now = _rev(r["bare"], "refs/heads/main")
    assert all(_ancestor(r["bare"], tip, remote_now) for tip in tips)       # nothing on the remote was overwritten
    if then is None:
        assert job["status"] == "succeeded", job
        assert len(syncs) == 2 and len(pushes) == 3 and remote_now == syncs[-1]
        assert _decision(env, did)["target_sha"] == a0 and _merges(r["base"]) == 2
        return
    assert (job["status"], job["phase_note"], job["last_error_code"], job["retryable"]) == (
        "recovery_required", "gate_recovery", "push_remote_race", 1)
    assert _decision(env, did)["status"] == "recovery_required"
    assert len(syncs) == 3 and len(pushes) == 4 and len(tips) == 4
    assert _rev(r["base"], "refs/heads/main") == syncs[-1] and remote_now == tips[-1]
    push = effects["push"]
    assert (push["status"], push["error_code"]) == ("failed", "push_remote_race")
    assert _ev(push)["sync_round_limit"] == 3 and _ev(push)["rejected_rounds"] == 4
    assert _merges(r["base"]) == 3
    monkeypatch.setattr(svc, "_run_git", real)                               # the remote stops moving
    if then == "retry":
        _act(env, run_id, g, did, "retry", {"request_id": "race-retry", "expected_decision_version":
                                            _version(env, did)}, 202)
        job, _ = _run_job(jid)
        assert job["status"] == "succeeded", job
        sev = _ev(_effects(env, did)["remote_sync"])
        assert len(sev["rounds"]) == 4 and sev["rounds"][-1]["pre_head"] == syncs[-1]
        assert _rev(r["bare"], "refs/heads/main") == sev["sync_commit"]
        assert _effects(env, did)["merge"]["attempt_count"] == 1 and _decision(env, did)["target_sha"] == a0
        return
    _act(env, run_id, g, did, "resolve", {"request_id": "race-abandon", "expected_decision_version":
                                          _version(env, did), "resolution": "abandon",
                                          "reason": "the remote keeps moving", **_evidence(env, g)}, 200)
    kept = _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-sync")
    assert kept == syncs[-1]
    row = _effects(env, did)["remote_sync"]
    assert row["recovery_state"] == "confirmed" and row["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}-sync"
    assert _ev(row)["retention"] == {"sha": syncs[-1], "ref": f"refs/flowgate/gate-recovery/{did}-sync",
                                     "kept": True}
