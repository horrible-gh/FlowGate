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

def test_a_retried_push_after_a_sync_round_keeps_the_foreign_results_it_sends(git_env, pushes, monkeypatch):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    base = r["base"]
    _push_rejected_once(monkeypatch)
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", diverge_base=True)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_rejected")
    a0 = _effects(env, did)["merge"]["sha_after"]
    side = _side_commit(r, a0, "b_result.txt")                               # B merges into the same local target
    _git(["merge", "--no-ff", "-m", "gate B merge", side], cwd=base)
    m_b = _rev(base, "HEAD")
    h1 = _advance(r, "main", "theirs.txt")
    monkeypatch.setenv("FLOWGATE_GATE_SYNC_ROUNDS", "1")
    tips = []
    real = _on_push(monkeypatch, {2: lambda: tips.append(_advance(r, "main", "race.txt"))})
    _act(env, run_id, g, did, "retry", {"request_id": "foreign-1", "expected_decision_version": _version(env, did)},
         202)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_remote_race")
    effects = _effects(env, did)
    s1 = _ev(effects["remote_sync"])["sync_commit"]
    assert _parents(base, s1) == [m_b, h1] and _rev(base, "refs/heads/main") == s1
    assert set(_ev(effects["push"])["foreign_included"]) == {m_b, side}
    monkeypatch.setattr(svc, "_run_git", real)                               # the remote stops moving
    _act(env, run_id, g, did, "retry", {"request_id": "foreign-2", "expected_decision_version": _version(env, did)},
         202)
    job, _ = _run_job(jid)                                                   # A = S1 = L now: still counted from A0
    assert job["status"] == "succeeded", job
    effects = _effects(env, did)
    pev, sev = _ev(effects["push"]), _ev(effects["remote_sync"])
    s2 = sev["sync_commit"]
    assert [x["sync_commit"] for x in sev["rounds"]] == [s1, s2] and _parents(base, s2) == [s1, tips[0]]
    assert set(pev["foreign_included"]) == {m_b, side}                       # B's results, nothing of the remote
    assert pev["sent_sha"] == s2 == _rev(r["bare"], "refs/heads/main") and pev["applied_sha"] == a0
    assert [x["sent_sha"] for x in pev["rejections"]] == [m_b, s1, s1]      # every refusal of every claim
    d = _decision(env, did)
    assert d["status"] == "succeeded" and d["target_sha"] == a0 and _checkpoint(env, did)["applied_sha"] == a0
    assert effects["merge"]["attempt_count"] == 1 and _merges(base) == 4    # A0, B, S1, S2: no duplicate merge
    assert len(pushes) == 4                                                 # 2 + 2 real pushes, never forced
