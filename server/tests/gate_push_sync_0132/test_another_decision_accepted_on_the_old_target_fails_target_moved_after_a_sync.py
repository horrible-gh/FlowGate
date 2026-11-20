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

def test_another_decision_accepted_on_the_old_target_fails_target_moved_after_a_sync(git_env, pushes):
    r = git_env
    env = r["env"]
    keys = _confirmed(env, [node("tmp_r", "R"), node("tmp_g1", None, "gate"), node("tmp_g2", None, "gate")],
                      [edge("tmp_e1", "tmp_r", "tmp_g1"), edge("tmp_e2", "tmp_r", "tmp_g2")])
    g1, g2 = keys["tmp_g1"], keys["tmp_g2"]
    run_id = _start(env)["run_id"]
    assert (_states(env)[g1], _states(env)[g2]) == ("open", "open")
    branch = _target(r, "nonbase")
    before = _rev(r["base"], branch)
    _work(r, "ours.txt")
    for g in (g1, g2):
        _gate_merge(env, g, branch)
    first, second = _confirm(env, run_id, g1, "first-0132"), _confirm(env, run_id, g2, "second-0132")
    a_did, a_job = first["decision"]["decision_id"], first["job"]["job_id"]
    c_did, c_job = second["decision"]["decision_id"], second["job"]["job_id"]
    for did in (a_did, c_did):
        assert json.loads(_decision(env, did)["settings_json"])["target_basis"]["accepted_target_sha"] == before
    h1 = _advance(r, branch, "theirs.txt")
    job, _ = _run_job(a_job)
    assert job["status"] == "succeeded", job
    s = _ev(_effects(env, a_did)["remote_sync"])["sync_commit"]
    assert _rev(r["base"], branch) == s == _rev(r["bare"], "refs/heads/" + branch) and _ancestor(r["bare"], h1, s)
    job, _ = _run_job(c_job)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_failed",
                                                                          "target_moved")
    assert _decision(env, c_did)["status"] == "failed"                       # no effect outside
    merge = _effects(env, c_did)["merge"]
    assert (merge["status"], merge["error_code"]) == ("failed", "target_moved")
    assert (_ev(merge)["accepted_target_sha"], _ev(merge)["observed_target_sha"]) == (before, s)
    assert "remote_sync" not in _effects(env, c_did) and "push" not in _effects(env, c_did)
    assert _rev(r["base"], branch) == s == _rev(r["bare"], "refs/heads/" + branch) and len(pushes) == 2
