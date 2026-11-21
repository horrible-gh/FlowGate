"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_0098.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_0098 import (
    FA,
    Path,
    _0098_target,
    _act,
    _confirm,
    _decision,
    _effects,
    _expire_and_reconcile,
    _gate,
    _git,
    _merges,
    _rev,
    _run_job,
    _sql,
    _start_linear,
    _version,
    _work,
    db_path,
    env,
    git_env,
    json,
    product,
    pytest,
    pytestmark,
    shutil,
    sys,
)

@pytest.mark.parametrize("change,code", [("delete", "merge_target_not_found"), ("move", "target_moved")])
def test_0098_changed_target_parks_without_adopting_a_new_sha_and_retry_requires_the_original(git_env, change, code):
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    before = _rev(r["base"], branch)
    frozen = _work(r, "changed.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch)
    body = _confirm(env, run_id, g, "changed-0098")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    if change == "delete":
        _git(["update-ref", "refs/remotes/origin/" + branch, before], cwd=r["base"])
        _git(["update-ref", "-d", "refs/heads/" + branch], cwd=r["base"])
    else:
        _git(["update-ref", "refs/heads/" + branch, frozen], cwd=r["base"])
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"], job["retryable"], job["recovery_since"]) == (
        "recovery_required", code, 1, None)
    assert _decision(env, did)["status"] == "failed"
    effect = _effects(env, did)["merge"]
    assert effect["error_code"] == code and effect["status"] == "failed"
    ev = json.loads(effect["evidence_json"])
    assert ev["accepted_target_sha"] == before and ev["stage"] == "target_check"
    assert ev["target_deleted"] == (change == "delete")
    assert _decision(env, did)["target_sha"] == before
    _git(["update-ref", "refs/heads/" + branch, before], cwd=r["base"])
    _act(env, run_id, g, did, "retry", {"request_id": "restore-0098", "expected_decision_version": _version(env, did)}, 202)
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    assert _decision(env, did)["frozen_sha"] == frozen
