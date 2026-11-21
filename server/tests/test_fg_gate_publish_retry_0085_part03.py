"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_retry_0085.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_retry_0085 import (
    FA,
    PROJECT,
    Path,
    _act,
    _attempt,
    _cleanup_jobs,
    _cleanups,
    _confirm,
    _current,
    _decide,
    _decision,
    _decisions,
    _effects,
    _evidence,
    _finish,
    _flow,
    _gate,
    _gate_events,
    _git,
    _h,
    _job_row,
    _jobs,
    _merges,
    _nodes,
    _parked_after_merge,
    _parked_without_effect,
    _requests,
    _rev,
    _run_cleanup,
    _run_job,
    _run_status,
    _sql,
    _start,
    _start_linear,
    _states,
    _version,
    _work,
    allowed,
    approval_retry_policy,
    base64,
    db_path,
    env,
    git_env,
    json,
    logging,
    product,
    pytest,
    pytestmark,
    setting,
    shutil,
    subprocess,
    sys,
)

def test_approval_deadline_is_durable_caps_next_wait_and_blocks_new_git(approval_retry_policy, monkeypatch):
    f = approval_retry_policy
    f.fail(cause="network")
    deadline = f.evidence()["publish_retry"]["deadline_at"]
    f.clock["now"] = "2026-11-05T00:09:59+00:00"
    # Configuration changes and different errors never start a fresh budget.
    monkeypatch.setenv("FLOWGATE_APPROVAL_RETRY_DEADLINE_SEC", "3600")
    f.ctx.job = json.loads(json.dumps(f.ctx.job))
    f.fail(stage="merge", cause="timeout")
    assert f.ctx.job["available_at"] == deadline == "2026-11-05T00:10:00+00:00"
    before = f.evidence()["attempt_error"]
    f.clock["now"] = deadline
    monkeypatch.setattr(f.ap, "_admit", lambda *args: None)
    monkeypatch.setattr(f.ap, "_resume", lambda *args: None)
    monkeypatch.setattr(f.ap, "_run_git", lambda *args: pytest.fail("new Git work after deadline"))
    f.ctx.job.update(status="running", phase="fetched")
    assert f.ap.publish(f.ctx, f.ap.REQUEST).state == f.ap.FAILED
    assert f.releases == [("failed", "git_error")]
    assert f.evidence()["attempt_error"] == before
    assert f.evidence()["publish_retry"]["stop_reason"] == "deadline_exceeded"
@pytest.mark.parametrize("phase,resumed", [("fetched", True), ("db_finalize", False)])
def test_approval_deadline_preserves_landed_git_and_db_finalize(approval_retry_policy, monkeypatch, phase, resumed):
    f = approval_retry_policy
    f.fail(cause="network")
    f.clock["now"] = "2026-11-05T00:11:00+00:00"
    f.ctx.job.update(status="running", phase=phase)
    terminal = {"ok": True, "terminal": True, "result": {"status": "merged"}}
    monkeypatch.setattr(f.ap.store, "ensure_lease", lambda ctx: None)
    monkeypatch.setattr(f.ap, "_admit", lambda *args: None)
    monkeypatch.setattr(f.ap, "_resume", lambda *args: terminal)
    monkeypatch.setattr(f.ap, "_run_git", lambda *args: pytest.fail("terminal Git must not be repeated"))
    monkeypatch.setattr(f.ap, "_db_finalize", lambda *args: f.ap.Outcome(f.ap.EXECUTED, job=f.ctx.job))
    assert f.ap.publish(f.ctx, f.ap.REQUEST).state == f.ap.EXECUTED
    assert f.releases == []
def test_approval_general_retry_cap_still_applies_to_transients(approval_retry_policy, monkeypatch):
    f = approval_retry_policy
    monkeypatch.setattr(f.ap.store, "retry_max_attempts", lambda: 1)
    assert f.fail(cause="network").state == f.ap.QUEUED
    assert f.fail(cause="network").state == f.ap.FAILED
    assert f.evidence()["publish_retry"]["stop_reason"] == "retry_exhausted"
@pytest.mark.parametrize("returncode,stderr,cause", [
    (-1, "timeout_expired", "timeout"),
    (128, "fatal: Could not resolve host: git.example.invalid", "network"),
    (128, "fatal: Authentication failed for 'https://example.invalid/repo'", "auth"),
    (128, "Permission denied (publickey).", "auth"),
    (128, "fatal: cannot lock ref 'refs/remotes/origin/main'", "ref_lock"),
    (128, "fatal: Unable to create '/repo/.git/index.lock': File exists.", "ref_lock"),
    (128, "fatal: unable to access 'https://example.invalid': unspecified error", "unknown"),
    (128, "unexpected 401 bytes received", "unknown"),
    (128, "permission denied", "unknown"),
    (1, "timeout_expired", "unknown"),
])
def test_approval_command_errors_are_conservatively_classified(returncode, stderr, cause):
    from modules.flow_gate.services.git import command, finalize
    proc = subprocess.CompletedProcess(["git", "fetch"], returncode, "", stderr)
    error = finalize._command_error(proc, "fetch")
    record = finalize._attempt_error(error)
    assert record["code"] == "git_error"
    assert (record["stage"], record["cause"], record["diagnostic"]) == ("fetch", cause, stderr)
    assert command.git_failure_details(proc, "fetch") == {"stage": "fetch", "cause": cause}
@pytest.mark.parametrize("budget_expired", [False, True])
def test_approval_run_git_timeout_has_a_cause_for_subprocess_and_budget_timeout(monkeypatch, budget_expired):
    from modules.flow_gate.services.git import command

    def timeout(*args, **kwargs):
        if budget_expired:
            pytest.fail("expired budget must not start subprocess")
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(command.subprocess, "run", timeout)
    with command.git_budget(0 if budget_expired else 30):
        proc = command._run_git(["fetch", "origin"])
    assert (proc.returncode, proc.stderr, proc.cause) == (-1, "timeout_expired", "timeout")
