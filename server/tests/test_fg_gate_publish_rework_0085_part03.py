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

def test_an_archive_cut_off_after_its_ref_is_never_canceled_as_without_effect(git_env, monkeypatch):
    """hold + archive cut off between the ref and its checkpoint: the intent was stored first, so neither the common
    cancel nor a Stop releases it as "no effect" - also when no phase row tells of the ref (the ref itself is read)."""
    from flowgate.gate_decisions import repository
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "x1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = repository.insert_checkpoint, []

    def crash(row):
        calls.append(row)
        if len(calls) == 1:
            raise RuntimeError("process died after the archive ref")
        return real(row)

    monkeypatch.setattr(repository, "insert_checkpoint", crash)
    job, _ = _run_job(job_id)
    assert job["status"] == "running" and _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha
    assert _effects(env, did)["archive"]["status"] == "unknown" and repository.effects_state(did) == "unknown"
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "x2", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_effects_unknown"
    _sql(env["db"], "DELETE FROM gate_decision_effects WHERE decision_id = ? AND phase = 'archive'", (did,))
    assert repository.effects_state(did) == "none"                            # only the ref tells it now
    refused = _act(env, run_id, g, did, "cancel", {"request_id": "x3", "expected_decision_version": _version(env, did)},
                   409)
    assert refused["error"]["code"] == "gate_effects_unknown" and _requests(env) == []
    stop = env["client"].post(f"/api/v1/runs/{run_id}/stop", json={}, headers=_h())
    assert stop.status_code in (200, 202), stop.text
    d = _decision(env, did)
    assert (d["status"], d["last_error_code"]) == ("recovery_required", "stop_requested")      # never canceled
    assert _job_row(env, job_id)["status"] == "recovery_required"
    # an unknown parked effect preserves the reservation, but no longer holds Stop in flight.
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "stopped"}
    assert _states(env)[g] == "open"
    # Explicit resolution still preserves the archive ref and records the recovery obligation.
    out = _act(env, run_id, g, did, "resolve", {"request_id": "x4", "expected_decision_version": _version(env, did),
                                                "resolution": "abandon", "reason": "stopped, archive cut off",
                                                **_evidence(env, g)}, 200)
    assert out["decision"]["status"] == "abandoned" and _job_row(env, job_id)["phase_note"] == "recovery_abandoned"
    assert _run_status(env, run_id) == {"status": "idle", "idle_reason": "stopped"}
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha     # nothing removed, nothing reverted
    assert _effects(env, did)["snapshot"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}"
