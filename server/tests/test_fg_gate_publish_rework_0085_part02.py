"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_rework_0085.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_rework_0085 import (
    FA,
    PROJECT,
    Path,
    _act,
    _cleanups,
    _completed_merge_flow,
    _confirm,
    _decision,
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
    _merges,
    _parked_after_merge,
    _parked_without_effect,
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

@pytest.mark.parametrize("phase", ["merge", "push", "archive", "push_after_close"])
def test_a_lease_lost_during_a_git_step_writes_no_evidence_and_the_next_owner_judges_it(git_env, monkeypatch, phase):
    """Every worker write is fenced by the job's lease and the decision's state: a worker that lost its lease (or whose
    decision was closed) during a Git command writes nothing; the phase stays ``unknown`` for the next owner."""
    from flowgate.gate_decisions import git_adapter
    from modules.flow_gate.db.connection import get_store
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="merge", merge_target="origin", push=True, archive=True)
    body = _confirm(env, run_id, g, "f1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    target_at_accept = _decision(env, did)["target_sha"]
    calls = []

    def lose():
        with get_store().transaction():
            if phase == "push_after_close":                                   # what a resolve's close leaves
                get_store()._execute("UPDATE operation_job SET status = 'cancelled', lease_token = NULL, "
                                     "lease_until = NULL WHERE job_id = ?", [job_id])
                get_store()._execute("UPDATE gate_decisions SET status = 'abandoned', recovery_reset_required = 1, "
                                     "decision_version = decision_version + 1 WHERE decision_id = ?", [did])
            else:
                get_store()._execute("UPDATE operation_job SET lease_token = 'lt_other_owner' WHERE job_id = ?",
                                     [job_id])

    owner, name = (git_adapter, {"merge": "merge", "archive": "create_ref"}.get(phase, "push"))
    real = getattr(owner, name)

    def step(*args, **kwargs):
        out = real(*args, **kwargs)
        if not calls and (phase != "archive" or args[1] == f"refs/flowgate/gate-archive/{did}"):
            calls.append(1)
            lose()
        return out

    monkeypatch.setattr(owner, name, step)
    job, _ = _run_job(job_id)
    effect_phase = "push" if phase == "push_after_close" else phase
    assert calls and _effects(env, did)[effect_phase]["status"] == "unknown"   # the evidence before the command
    assert _states(env)[g] == "open" and _gate_events(env, g) == []
    if phase == "push_after_close":
        d = _decision(env, did)
        assert (job["status"], d["status"]) == ("cancelled", "abandoned")       # the close stands; nothing more
        assert d["target_sha"] == _rev(r["base"], "refs/heads/main")             # written before the close only
        return
    assert job["status"] == "running" and job["lease_token"] == "lt_other_owner"
    d = _decision(env, did)
    assert d["status"] == "accepted"
    if phase == "merge":
        assert d["target_sha"] == target_at_accept and _effects(env, did)["merge"]["sha_after"] is None
    if phase == "archive":
        assert _sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,)) == []
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    merged = _rev(r["base"], "refs/heads/main")
    assert _merges(r["base"]) == 0 and _rev(r["bare"], "refs/heads/main") == merged and merged == sha
    assert len(_sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,))) == 1
    assert {p: e["status"] for p, e in _effects(env, did).items()} == {
        "snapshot": "succeeded", "merge": "succeeded", "push": "succeeded", "archive": "succeeded"}
    assert [e["cause"] for e in _gate_events(env, g)] == ["gate_confirmed"]
