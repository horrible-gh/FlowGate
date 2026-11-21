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

def test_commit_mode_parks_a_remote_ahead_push_without_a_sync(git_env, pushes):
    r = git_env
    env, g, _, run_id = _start_linear(r)
    frozen = _work(r, "local.txt")
    _gate(env, g, merge_mode="commit", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "commit-ahead-0132")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    branch = r["branch"]
    remote = _advance(r, branch, "remote.txt")
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "push_remote_ahead")
    assert _decision(env, did)["status"] == "failed"
    effects = _effects(env, did)
    assert "remote_sync" not in effects and effects["push"]["error_code"] == "push_remote_ahead"
    assert _rev(r["base"], "refs/heads/" + branch) == frozen == _rev(r["wt"], "HEAD")
    assert _rev(r["bare"], "refs/heads/" + branch) == remote and len(pushes) == 1
