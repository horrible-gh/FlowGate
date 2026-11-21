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

def test_a_stolen_lease_writes_no_completion_and_the_new_owner_completes_once(git_env, monkeypatch):
    from flowgate.gate_decisions import publish
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "ls1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = publish._complete, []

    def stolen(ctx, plan):
        calls.append(1)
        if len(calls) == 1:
            with get_store().transaction():
                get_store()._execute("UPDATE operation_job SET lease_token = 'lt_other_owner' WHERE job_id = ?",
                                     [job_id])
        return real(ctx, plan)

    monkeypatch.setattr(publish, "_complete", stolen)
    job, first = _run_job(job_id)
    assert job["status"] == "running" and job["lease_token"] == "lt_other_owner"
    assert _states(env)[g] == "open" and _gate_events(env, g) == [] and _states(env)[t] == "pending"
    assert _decision(env, did)["status"] == "accepted"
    job = _expire_and_reconcile(job_id)
    assert job["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded" and _merges(r["base"]) == 0
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]
    from modules.flow_gate.services.git import job_store
    with pytest.raises(job_store.LeaseLost):                                 # the first claim's late write vanishes
        job_store.job_write(first.ctx, status="failed")
    assert _job_row(env, job_id)["status"] == "succeeded"
def test_a_conflict_goes_to_the_merge_review_with_the_decision_and_goes_on_once_it_is_resolved(git_env):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import job_store, job_sweeper
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "shared.txt", "group side\n")
    (r["base"] / "shared.txt").write_text("base side\n", encoding="utf-8")
    _git(["commit", "-am", "base change"], cwd=r["base"])
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "cf1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    job, _ = _run_job(job_id)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_conflict",
                                                                          "merge_conflict")
    d = _decision(env, did)
    assert d["status"] == "failed" and _states(env)[g] == "open" and _gate_events(env, g) == []
    session = svc.db_git.get_open_session_by_group(FA)
    context = svc.db_git.session_context(session)
    assert context["gate_decision_id"] == did and svc.db_git.get_state(FA)["status"] == "conflict"
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "cx", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_decision_not_cancelable"
    job_sweeper.step_recovery_required_retry()                               # the review is still open: it waits
    assert job_store.get_job(job_id)["status"] == "recovery_required"
    # The review resolves the conflict (as the existing review completion does for a manual-merge attempt).
    (r["base"] / "shared.txt").write_text("resolved\n", encoding="utf-8")
    _git(["add", "shared.txt"], cwd=r["base"])
    _git(["commit", "--no-edit"], cwd=r["base"])
    merge_commit = _rev(r["base"], "HEAD")
    svc._finish_reviewed_attempt(FA, int(session["merge_id"]), PROJECT, merge_commit[:7], False)
    from modules.flow_gate.db.connection import get_store
    with get_store().transaction():                                          # the Sweeper's next round is due
        get_store()._execute("UPDATE operation_job SET available_at = ? WHERE job_id = ?",
                             ["2000-01-01T00:00:00+00:00", job_id])
    job_sweeper.step_recovery_required_retry()
    assert job_store.get_job(job_id)["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _decision(env, did)["status"] == "succeeded" and _effects(env, did)["merge"]["sha_after"] == merge_commit
    assert _states(env)[g] == "done" and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
@pytest.mark.parametrize("after", ["snapshot", "merge"])
def test_a_stop_during_the_job_cancels_without_effects_and_parks_with_them(git_env, monkeypatch, after):
    from flowgate.gate_decisions import git_adapter, publish
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "st1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]

    def stop_now():
        # What Stop's first transaction does to a run whose gate job is being run: stopping + the cancel request.
        with get_store().transaction():
            get_store()._execute("UPDATE graph_runs SET status = 'stopping' WHERE run_id = ?", [run_id])
            get_store()._execute("UPDATE operation_job SET cancel_requested = 1 WHERE job_id = ?", [job_id])

    target = publish._snapshot if after == "snapshot" else git_adapter.merge
    owner = publish if after == "snapshot" else git_adapter
    name = "_snapshot" if after == "snapshot" else "merge"

    def wrapped(*args, **kwargs):
        out = target(*args, **kwargs)
        stop_now()
        return out

    monkeypatch.setattr(owner, name, wrapped)
    job, _ = _run_job(job_id)
    run = _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE run_id = ?", (run_id,))[0]
    if after == "snapshot":
        assert (job["status"], job["phase_note"]) == ("cancelled", "no_effect_canceled")
        assert _decision(env, did)["status"] == "canceled"
        assert (run["status"], run["idle_reason"]) == ("idle", "stopped")    # the Stop went on to its end
        assert _states(env)[g] == "pending" and _merges(r["base"]) == 0
        return
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "stop_requested")
    assert _decision(env, did)["status"] == "recovery_required"
    # parked effects retain the gate while Stop itself finishes.
    assert (run["status"], run["idle_reason"]) == ("idle", "stopped")
    assert _states(env)[g] == "open"
    _act(env, run_id, g, did, "resolve", {"request_id": "ab1", "expected_decision_version": _version(env, did),
                                         "resolution": "abandon", "reason": "stopped after the merge",
                                         **_evidence(env, g)}, 200)
    run = _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE run_id = ?", (run_id,))[0]
    assert (run["status"], run["idle_reason"]) == ("idle", "stopped")
    assert _decision(env, did)["recovery_reset_required"] == 1 and _states(env)[g] == "pending"
