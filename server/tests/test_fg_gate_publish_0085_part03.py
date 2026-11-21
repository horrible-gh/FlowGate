"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_0085.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_0085 import (
    FA,
    PROJECT,
    Path,
    _SERVER_DIR,
    _act,
    _alive,
    _attempt,
    _cleanup_jobs,
    _cleanups,
    _completed_merge_flow,
    _confirm,
    _confirmed,
    _current,
    _decide,
    _decision,
    _decisions,
    _effects,
    _evidence,
    _expire_and_reconcile,
    _finish,
    _flow,
    _gate,
    _gate_events,
    _git,
    _h,
    _job_row,
    _jobs,
    _linear,
    _merges,
    _nodes,
    _parked_after_merge,
    _parked_without_effect,
    _push_rejected_once,
    _requests,
    _rev,
    _run_cleanup,
    _run_job,
    _run_status,
    _second_publish,
    _side_commit,
    _sql,
    _start,
    _start_linear,
    _states,
    _version,
    _work,
    allowed,
    base64,
    db_path,
    edge,
    env,
    git_env,
    json,
    node,
    os,
    product,
    put_draft,
    pytest,
    pytestmark,
    setting,
    shutil,
    subprocess,
    sys,
)

@pytest.mark.parametrize("landed", [True, False], ids=["timeout_landed", "timeout_unknown"])
def test_a_push_timeout_is_judged_by_the_remote(git_env, monkeypatch, landed):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True)
    body = _confirm(env, run_id, g, "pt1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real = svc._run_git
    seen = {"ls": 0, "push": 0}

    def flaky(args, **kwargs):
        if args and args[0] == "push":
            seen["push"] += 1
            if seen["push"] == 1:
                if landed:
                    real(args, **kwargs)                                     # it really landed, then timed out
                return subprocess.CompletedProcess(["git", *args], -1, "", "timeout_expired")
        if args and args[0] == "ls-remote":
            seen["ls"] += 1
            # 1: the remote head before, 2: does it hold our SHA already, 3: the check after the timeout
            if not landed and seen["ls"] == 3:
                return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: unable to access")
        return real(args, **kwargs)

    monkeypatch.setattr(svc, "_run_git", flaky)
    job, _ = _run_job(job_id)
    if landed:
        assert job["status"] == "succeeded"
        push = _effects(env, did)["push"]
        assert push["status"] == "succeeded" and json.loads(push["evidence_json"])["result"] == "push_landed_after_error"
        assert seen["push"] == 1
        return
    assert job["status"] == "retry_wait" and job["last_error_code"] == "push_result_unknown"
    assert _effects(env, did)["push"]["status"] == "unknown"
    evidence = json.loads(_effects(env, did)["push"]["evidence_json"])
    assert evidence["exit_code"] == -1 and evidence["diagnostic_tail"] == "timeout_expired"
    assert evidence["remote"] == "origin" and evidence["branch"] == "main"
    assert evidence["sent_sha"] == _rev(r["base"], "refs/heads/main")
    assert evidence["remote_head_before"] == _rev(r["bare"], "refs/heads/main")
    assert evidence["attempt_kind"] == "update"
    assert _decision(env, did)["status"] == "accepted" and _states(env)[g] == "open"
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "x1", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_effects_unknown"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _rev(r["bare"], "refs/heads/main") == _rev(r["base"], "refs/heads/main")
    assert _merges(r["base"]) == 0 and [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]
def test_a_crash_after_the_archive_ref_resumes_from_the_ref_after_the_reconcile(git_env, monkeypatch):
    from flowgate.gate_decisions import repository
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", archive=True)
    body = _confirm(env, run_id, g, "ar1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = repository.insert_checkpoint, []

    def crash(row):
        calls.append(row)
        if len(calls) == 1:
            raise RuntimeError("process died")
        return real(row)

    monkeypatch.setattr(repository, "insert_checkpoint", crash)
    job, _ = _run_job(job_id)
    assert job["status"] == "running" and job["phase_note"] == "result_unknown"     # the Runner's unknown result
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _states(env)[g] == "open" and _decision(env, did)["status"] == "accepted"
    job = _expire_and_reconcile(job_id)
    assert job["status"] == "pending" and job["phase"] == "archive"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert len(_sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,))) == 1
    assert _merges(r["base"]) == 0 and _states(env)[g] == "done"
def test_a_crash_right_after_the_merge_commit_is_settled_by_the_existing_attempt_recovery(git_env, monkeypatch):
    from modules.flow_gate.services.git import merge_target
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "mc1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = merge_target.finish_manual_attempt, []

    def crash(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("process died after the merge commit")
        return real(*args, **kwargs)

    monkeypatch.setattr(merge_target, "finish_manual_attempt", crash)
    job, _ = _run_job(job_id)
    landed = _rev(r["base"], "refs/heads/main")
    assert landed == sha and job["status"] == "running"
    assert _effects(env, did)["merge"]["status"] == "unknown" and _states(env)[g] == "open"
    _expire_and_reconcile(job_id)
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _rev(r["base"], "refs/heads/main") == landed and _merges(r["base"]) == 0   # the same merge commit
    merge = _effects(env, did)["merge"]
    assert merge["status"] == "succeeded" and merge["sha_after"] == landed
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]
