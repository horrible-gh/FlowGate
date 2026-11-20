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

@pytest.mark.parametrize('landed', [False], ids=['before_sync_commit'])
def test_a_sync_cut_off_is_judged_from_its_attempt_without_a_second_merge(git_env, pushes, monkeypatch, landed):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", archive=False)
    h1 = _advance(r, "main", "theirs.txt")
    real_finish, real_git, real_fail = merge_target.finish_manual_attempt, svc._run_git, merge_target.fail_attempt
    calls = []
    if landed:
        def crash(ctx, **kwargs):
            calls.append(ctx.merge_id)
            if len(calls) == 2:                                              # 1: the approved merge, 2: the sync
                raise RuntimeError("process died after the sync commit")
            return real_finish(ctx, **kwargs)
        monkeypatch.setattr(merge_target, "finish_manual_attempt", crash)
    else:
        def killed(args, **kwargs):
            if "merge" in args and "--no-ff" in args:
                raise RuntimeError("process died before the sync commit")
            return real_git(args, **kwargs)
        monkeypatch.setattr(svc, "_run_git", killed)
        monkeypatch.setattr(merge_target, "fail_attempt", lambda *a, **k: None)   # nothing ran after the kill
    job, _ = _run_job(jid)
    assert job["status"] == "running"
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    sync = effects["remote_sync"]
    mid = _ev(sync)["merge_id"]
    assert sync["status"] == "unknown" and svc.db_git.get_session(mid)["status"] == "open"
    if landed:
        cut = _rev(base, "refs/heads/main")
        assert _parents(base, cut) == [a0, h1]
    else:
        assert _rev(base, "refs/heads/main") == a0
    monkeypatch.setattr(merge_target, "finish_manual_attempt", real_finish)
    monkeypatch.setattr(merge_target, "fail_attempt", real_fail)
    monkeypatch.setattr(svc, "_run_git", real_git)
    _expire_and_reconcile(jid)
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    effects = _effects(env, did)
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    s = sev["sync_commit"]
    assert sync["status"] == "succeeded" and len(sev["rounds"]) == 1
    assert svc.db_git.get_session(mid)["status"] != "open"
    if landed:
        assert s == cut and sync["attempt_count"] == 1                     # the landed commit is the result
        assert len(pushes) == 2
    else:
        assert sync["attempt_count"] == 2 and _parents(base, s) == [a0, h1]   # a new round, one sync commit
        assert len(pushes) == 3
    assert _merges(base) == 1 and effects["merge"]["attempt_count"] == 1
    assert _rev(r["bare"], "refs/heads/main") == s and _ancestor(r["bare"], h1, s)
    assert _decision(env, did)["target_sha"] == a0
