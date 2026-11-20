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

@pytest.mark.parametrize('rounds', [1], ids=['first_round'])
def test_an_abandon_keeps_the_landed_sync_commit_its_round_never_recorded(git_env, pushes, monkeypatch, rounds):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False)
    tips = [_advance(r, "main", "theirs.txt")]
    real = _on_push(monkeypatch, {2: lambda: tips.append(_advance(r, "main", "race.txt"))} if rounds == 2 else {})
    real_finish, calls = merge_target.finish_manual_attempt, []

    def crash(ctx, **kwargs):
        calls.append(ctx.merge_id)
        if len(calls) == rounds + 1:                                         # 1: the approved merge, then the syncs
            raise RuntimeError("process died after the sync commit")
        return real_finish(ctx, **kwargs)

    monkeypatch.setattr(merge_target, "finish_manual_attempt", crash)
    job, _ = _run_job(jid)
    assert job["status"] == "running"
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    done = [x["sync_commit"] for x in sev["rounds"]]
    landed = _rev(base, "refs/heads/main")
    assert sync["status"] == "unknown" and len(done) == rounds - 1 and landed not in done
    assert _parents(base, landed) == [done[-1] if done else a0, tips[-1]]
    monkeypatch.setattr(merge_target, "finish_manual_attempt", real_finish)
    monkeypatch.setattr(svc, "_run_git", real)
    (base / "README.md").write_text("tracked change\n", encoding="utf-8")    # the resume cannot settle the round
    _expire_and_reconcile(jid)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "sync_unsettled")
    sync = _effects(env, did)["remote_sync"]
    assert sync["status"] == "unknown" and [x["sync_commit"] for x in _ev(sync)["rounds"]] == done
    assert _decision(env, did)["status"] == "recovery_required"
    _act(env, run_id, g, did, "resolve", {"request_id": f"landed-abandon-{rounds}", "expected_decision_version":
                                          _version(env, did), "resolution": "abandon",
                                          "reason": "the sync could not be settled", **_evidence(env, g)}, 200)
    ref = f"refs/flowgate/gate-recovery/{did}-sync"
    assert _rev(base, ref) == landed                                         # the newest landed sync commit
    row = _effects(env, did)["remote_sync"]
    assert _ev(row)["retention"] == {"sha": landed, "ref": ref, "kept": True}
    assert (row["recovery_state"], row["recovery_ref"]) == ("unknown", ref)
    assert _rev(base, "refs/heads/main") == landed and len(pushes) == rounds  # nothing reset, nothing more pushed
    assert _effects(env, did)["merge"]["attempt_count"] == 1
