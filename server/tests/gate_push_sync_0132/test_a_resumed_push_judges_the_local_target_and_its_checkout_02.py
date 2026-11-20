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

@pytest.mark.parametrize('case', ['dirty'])
def test_a_resumed_push_judges_the_local_target_and_its_checkout(git_env, pushes, monkeypatch, case):
    from flowgate.gate_decisions import git_adapter
    r = git_env
    base = r["base"]
    _push_rejected_once(monkeypatch)
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", diverge_base=True)
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_rejected")
    a0 = _effects(env, did)["merge"]["sha_after"]
    assert a0 != frozen and _rev(base, "refs/heads/main") == a0
    # B: another Flow's gate merges its own result into the same local target while A is parked.
    side = _side_commit(r, a0, "b_result.txt")
    _git(["merge", "--no-ff", "-m", "gate B merge", side], cwd=base)
    m_b = _rev(base, "HEAD")
    h1 = _advance(r, "main", "theirs.txt")
    expected = {"dirty": "target_checkout_mismatch", "detached": "target_checkout_mismatch",
                "diverged": "target_diverged"}.get(case)
    if case == "dirty":
        (base / "README.md").write_text("tracked change\n", encoding="utf-8")
    elif case == "detached":
        _git(["checkout", "--detach", "HEAD"], cwd=base)
    elif case == "diverged":
        root = _git(["rev-list", "--max-parents=0", "HEAD"], cwd=base).splitlines()[0]
        elsewhere = _side_commit(r, root, "elsewhere.txt")
        _git(["checkout", "--detach", "HEAD"], cwd=base)
        _git(["update-ref", "refs/heads/main", elsewhere], cwd=base)
        _git(["checkout", "main"], cwd=base)
    local_before = _rev(base, "refs/heads/main")
    _act(env, run_id, g, did, "retry", {"request_id": f"resume-{case}", "expected_decision_version":
                                        _version(env, did)}, 202)
    job, _ = _run_job(jid)
    effects = _effects(env, did)
    if expected:
        assert (job["status"], job["last_error_code"]) == ("recovery_required", expected)
        assert "remote_sync" not in effects and len(pushes) == 0
        assert _rev(base, "refs/heads/main") == local_before and _rev(r["bare"], "refs/heads/main") == h1
        assert effects["merge"]["attempt_count"] == 1
        return
    assert job["status"] == "succeeded", job
    pev, sev = _ev(effects["push"]), _ev(effects["remote_sync"])
    assert set(pev["foreign_included"]) == {m_b, side} and pev["applied_sha"] == a0
    assert (sev["pre_head"], sev["remote_head"], sev["local_applied"]) == (m_b, h1, a0)   # L measured, not A0
    s = sev["sync_commit"]
    assert _parents(base, s) == [m_b, h1] and _rev(r["bare"], "refs/heads/main") == s == pev["sent_sha"]
    d = _decision(env, did)
    assert d["status"] == "succeeded" and d["target_sha"] == a0
    assert _rev(base, f"refs/flowgate/gate-archive/{did}-applied") == a0 and _checkpoint(env, did)["applied_sha"] == a0
    # B's own push afterwards reads the remote first: it holds B's result, so B reconciles (no second push).
    assert git_adapter.remote_contains(git_adapter.slot_of(FA), "main", m_b) is True
    assert effects["merge"]["attempt_count"] == 1 and _merges(base) == 3   # A0, B, one sync: no duplicate merge
    assert len(pushes) == 2
