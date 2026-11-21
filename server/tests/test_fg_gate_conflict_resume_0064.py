"""Real conflict review completion immediately resumes the original gate job."""
import uuid
import pytest

from test_fg_card_settings import FA, _h, _sql, db_path, env
from test_fg_gate_intake_0085 import product, _states, _gate_events
from test_fg_gate_publish_0085 import (
    PROJECT, git_env, _start_linear, _work, _git, _gate, _confirm,
    _run_job, _decision, _effects, _rev,
)
from test_fg_gate_push_sync_0132 import _accepted, _advance

pytestmark = pytest.mark.area("gate")


@pytest.mark.parametrize("remote_sync", [False, True])
@pytest.mark.parametrize("paused", [False, True])
def test_review_completion_resumes_same_job_without_retry_or_sweeper(git_env, monkeypatch, remote_sync, paused):
    from flowgate.gate_decisions import recovery, legacy_adapter, publish
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import job_store, merge_target
    r = git_env
    if remote_sync:
        e, g, run_id, branch, frozen, did, jid = _accepted(r, "base", name="shared.txt", text="group side\n")
        _advance(r, "main", "shared.txt", "remote side\n")
    else:
        e, g, _, run_id = _start_linear(r)
        frozen = _work(r, "shared.txt", "group side\n")
        (r["base"] / "shared.txt").write_text("base side\n", encoding="utf-8")
        _git(["commit", "-am", "base change"], cwd=r["base"])
        _gate(e, g, merge_mode="merge")
        answer = _confirm(e, run_id, g, "immediate-39")
        did, jid = answer["decision"]["decision_id"], answer["job"]["job_id"]
    job, _ = _run_job(jid)
    assert job["status"] == "recovery_required" and job["phase_note"] == "gate_conflict"
    mid = publish._conflict_merge_id(job)
    assert recovery.resume_after_merge(mid) is False  # open review is never resumed
    before = _decision(e, did)
    if paused:
        out = e["client"].post(f"/api/v1/runs/{run_id}/pause", json={}, headers=_h())
        assert out.status_code == 200, out.text
    session = svc.db_git.get_session(mid)
    target = merge_target.resolve_session_target(session)
    root = target.root
    (root / "shared.txt").write_text("resolved\n", encoding="utf-8")
    _git(["add", "shared.txt"], cwd=root)
    # Freeze the actual reviewed index; approval itself runs the production lock/commit/completion path.
    context = svc.db_git.session_context(session)
    snapshot = svc._freeze_commit_candidate(root, target.target_branch)
    context.update(snapshot)
    context["review_state"] = svc.REVIEW_STATE_PENDING
    svc.db_git.set_session_context(mid, context)
    wakes = []
    real_notify = legacy_adapter.notify_job
    def notify(job):
        # Acquiring the review's locks here proves the Flow callback runs after release.
        from modules.flow_gate.services.git import branch_merge_publish as bmp
        lock, held = bmp.session_lock(svc.db_git.get_session(mid), PROJECT, holder_kind="review_action", publish=True)
        bmp.session_unlock(lock, held)
        wakes.append(job["job_id"])
        real_notify(job)
    monkeypatch.setattr(legacy_adapter, "notify_job", notify)
    result = svc.approve_merge_review(FA, mid, attempt_id=str(uuid.uuid4()),
                                     review_fingerprint=snapshot["review_fingerprint"], authority="human")
    assert result["result"]["status"] == "merged", result
    assert job_store.get_job(jid)["status"] == "pending"
    assert wakes == [jid]
    assert recovery.resume_after_merge(mid) is False  # duplicate completion cannot reopen twice
    assert _decision(e, did)["decision_version"] == before["decision_version"] + 1
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    after = _decision(e, did)
    assert after["frozen_sha"] == frozen and after["settings_json"] == before["settings_json"]
    assert _states(e)[g] == "done"
    assert len(_sql(e["db"], "SELECT * FROM gate_decisions")) == 1
    assert len(_sql(e["db"], "SELECT * FROM operation_job WHERE kind = 'gate_publish'")) == 1
    assert [event["cause"] for event in _gate_events(e, g)] == ["gate_confirmed"]
    if paused:
        assert _sql(e["db"], "SELECT status FROM graph_runs WHERE run_id = ?", (run_id,))[0]["status"] == "paused"
    if remote_sync:
        assert _effects(e, did)["remote_sync"]["status"] == "succeeded"
        assert _rev(r["bare"], "refs/heads/main") == _rev(r["base"], "refs/heads/main")
