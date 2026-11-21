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

@pytest.mark.parametrize('target', ['base'])
def test_remote_ahead_is_synced_once_and_pushed_keeping_the_applied_sha(git_env, pushes, target):
    r = git_env
    env, g, run_id, branch, frozen, did, jid = _accepted(r, target, diverge_base=target == "base")
    h1 = _advance(r, branch, "theirs.txt")
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    d = _decision(env, did)
    assert d["status"] == "succeeded" and d["target_sha"] == a0
    assert (a0 != frozen) == (target == "base")
    sync = effects["remote_sync"]
    sev = _ev(sync)
    s = sev["sync_commit"]
    assert (sync["status"], sync["sha_before"], sync["sha_after"], sync["attempt_count"]) == ("succeeded", a0, s, 1)
    assert (sev["pre_head"], sev["remote_head"], sev["local_applied"], sev["reviewed"]) == (a0, h1, a0, False)
    assert [x["sync_commit"] for x in sev["rounds"]] == [s]
    assert _parents(r["base"], s) == [a0, h1]                                 # --no-ff: L first, h1 second
    assert _rev(r["base"], "refs/heads/" + branch) == s == _rev(r["bare"], "refs/heads/" + branch)
    assert _ancestor(r["bare"], h1, s) and _ancestor(r["bare"], a0, s)        # the remote holds both sides
    push = effects["push"]
    pev = _ev(push)
    assert (push["status"], push["attempt_count"], pev["sent_sha"]) == ("succeeded", 2, s)
    assert pev["applied_sha"] == a0 and pev["rejected_rounds"] == 1 and pev["sync_commit"] == s
    first = pev["rejections"][0]
    assert (first["code"], first["sent_sha"], first["exit_code"]) == ("push_remote_ahead", a0, 1)
    assert "rejected" in first["diagnostic_tail"]
    # The applied SHA contract: A0 everywhere the decision is read back.
    checkpoint = _checkpoint(env, did)
    assert (checkpoint["source_sha"], checkpoint["applied_sha"]) == (frozen, a0)
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}-applied") == (a0 if a0 != frozen else None)
    assert effects["merge"]["attempt_count"] == 1                             # the approved merge was not redone
    assert _merges(r["base"], branch) == (2 if target == "base" else 1)
    assert len(pushes) == 2
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]
