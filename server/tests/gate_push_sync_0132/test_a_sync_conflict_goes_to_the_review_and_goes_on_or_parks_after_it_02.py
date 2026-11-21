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

@pytest.mark.parametrize('review', ['aborted'])
def test_a_sync_conflict_goes_to_the_review_and_goes_on_or_parks_after_it(git_env, pushes, review):
    from flowgate.gate_decisions import publish, recovery
    from flowgate.gate_decisions import repository as repo
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import conflict, job_store, job_sweeper
    r = git_env
    base = r["base"]
    env, g, run_id, branch, frozen, did, jid = _accepted(r, "base", name="shared.txt", text="group side\n")
    h1 = _advance(r, "main", "shared.txt", "remote side\n")
    job, _ = _run_job(jid)
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_conflict",
                                                                          "merge_conflict")
    assert job["recovery_since"] is not None                                 # goes on by itself after the review
    decision = repo.get(did)
    assert decision["status"] == "recovery_required" and repo.effects_state(did) == repo.EFFECTS_PRESENT
    effects = _effects(env, did)
    a0 = effects["merge"]["sha_after"]
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    assert (sync["status"], sev["conflict"], sev["pre_head"], sev["remote_head"]) == ("pending", True, a0, h1)
    mid = int(sev["merge_id"])
    assert mid != _ev(effects["merge"]).get("merge_id")
    session = svc.db_git.get_session(mid)
    context = svc.db_git.session_context(session)
    assert session["status"] == "open" and context["gate_sync_decision_id"] == did
    assert "gate_decision_id" not in context                                 # never read as the decision's merge
    # The one active-conflict lookup: the sync's review, for the Sweeper, retry / cancel and the Stop alike.
    assert publish._conflict_merge_id(job) == mid and recovery._conflict_open(decision) is True
    refused = _act(env, run_id, g, did, "retry", {"request_id": "sync-retry", "expected_decision_version":
                                                  _version(env, did)}, 409)
    assert refused["error"]["code"] == "gate_decision_not_retryable" and "merge_conflict_open" in json.dumps(refused)
    _due(jid)
    job_sweeper.step_recovery_required_retry()                               # UNDECIDABLE while the review is open
    assert job_store.get_job(jid)["status"] == "recovery_required"
    assert _rev(r["bare"], "refs/heads/main") == h1 and _rev(base, "refs/heads/main") == a0
    if review == "completed":
        (base / "shared.txt").write_text("resolved\n", encoding="utf-8")
        _git(["add", "shared.txt"], cwd=base)
        _git(["commit", "--no-edit"], cwd=base)
        resolved = _rev(base, "HEAD")
        svc._finish_reviewed_attempt(FA, mid, PROJECT, resolved[:7], False)
    else:
        conflict.abort_merge(FA, mid)                                         # the review's [abort]
        assert _rev(base, "HEAD") == a0 and _git(["status", "--porcelain", "--untracked-files=no"], cwd=base) == ""
    _due(jid)
    job_sweeper.step_recovery_required_retry()
    assert job_store.get_job(jid)["status"] == "pending"
    job, _ = _run_job(jid)
    effects = _effects(env, did)
    sync, sev = effects["remote_sync"], _ev(effects["remote_sync"])
    if review == "completed":
        assert job["status"] == "succeeded", job
        assert (sync["status"], sev["sync_commit"], sev["reviewed"]) == ("succeeded", resolved, True)
        assert _rev(r["bare"], "refs/heads/main") == resolved == _ev(effects["push"])["sent_sha"]
        d = _decision(env, did)
        assert d["status"] == "succeeded" and d["target_sha"] == a0
        assert _checkpoint(env, did)["applied_sha"] == a0
        assert len(pushes) == 2 and effects["merge"]["attempt_count"] == 1
        return
    assert (job["status"], job["phase_note"], job["last_error_code"]) == ("recovery_required", "gate_recovery",
                                                                          "push_sync_conflict_aborted")
    assert (sync["status"], sync["error_code"], sev["changed"]) == ("failed", "push_sync_conflict_aborted", False)
    assert _rev(base, "refs/heads/main") == a0 and _rev(r["bare"], "refs/heads/main") == h1   # target unchanged
    assert _decision(env, did)["status"] == "recovery_required" and len(pushes) == 1
    assert publish._conflict_merge_id(job_store.get_job(jid)) == _ev(effects["merge"]).get("merge_id")
