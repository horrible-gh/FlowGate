"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_rework_recovery_0085.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_rework_recovery_0085 import (
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

@pytest.mark.parametrize("action", ["retry", "cancel", "resolve"])
def test_two_copies_of_one_recovery_request_answer_the_first_ones_result(git_env, monkeypatch, action):
    """Both copies found no stored key before the guard: the guard serializes them and the second answers the
    first's stored result (not gate_context_changed, not a second row); other content is gate_request_conflict."""
    from flowgate.gate_decisions import recovery
    r = git_env
    if action == "cancel":
        env, g, t, run_id, did, job_id = _parked_without_effect(r)
    else:
        env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    body = {"request_id": "dup1", "expected_decision_version": _version(env, did)}
    if action == "resolve":
        body.update({"resolution": "abandon", "reason": "remote gone", **_evidence(env, g)})
    real, seen = recovery._replay, {"n": 0}

    def racing(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] in (1, 3):                                               # each copy's read before the guard
            return None
        return real(*args, **kwargs)

    monkeypatch.setattr(recovery, "_replay", racing)
    expect = 202 if action == "retry" else 200
    first = _act(env, run_id, g, did, action, body, expect)
    second = _act(env, run_id, g, did, action, body, expect)
    assert second["replayed"] is True and second["decision"]["decision_id"] == did and "replayed" not in first
    assert len([q for q in _requests(env) if q["action"] == action]) == 1
    monkeypatch.setattr(recovery, "_replay", real)
    other = _act(env, run_id, g, did, action, {**body, "expected_decision_version": body[
        "expected_decision_version"] + 7}, 409)
    assert other["error"]["code"] == "gate_request_conflict"
def test_resolve_takes_the_reviewed_evidence_as_required_and_refuses_a_stale_one(git_env, monkeypatch):
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    v = _version(env, did)
    reviewed = _evidence(env, g)
    assert reviewed["evidence_refs"] and all(ref.startswith(did + "/") for ref in reviewed["evidence_refs"])
    base = {"request_id": "ev1", "expected_decision_version": v, "resolution": "abandon", "reason": "gone"}
    missing = _act(env, run_id, g, did, "resolve", base, 422)
    assert {e["field"] for e in missing["error"]["params"]["errors"]} == {"evidence_version", "evidence_refs"}
    bad = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": ["not a ref"]}, 422)
    assert {e["field"] for e in bad["error"]["params"]["errors"]} == {"evidence_refs"}
    stale = _act(env, run_id, g, did, "resolve", {**base, **reviewed,
                                                  "evidence_version": reviewed["evidence_version"] - 1}, 409)
    assert (stale["error"]["code"], stale["error"]["params"]["reason"]) == ("gate_context_changed", "evidence_version")
    moved = [ref.rsplit("@", 1)[0] + "@99" if "/push@" in ref else ref for ref in reviewed["evidence_refs"]]
    assert moved != reviewed["evidence_refs"]
    stale_ref = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": moved}, 409)
    assert stale_ref["error"]["params"]["reason"] == "evidence_refs"
    foreign = [ref.replace(did, "gdc_other", 1) for ref in reviewed["evidence_refs"]]
    assert _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": foreign}, 409)[
        "error"]["params"]["reason"] == "evidence_refs"
    assert _requests(env) == [] and _version(env, did) == v and _job_row(env, job_id)["status"] == "recovery_required"
    out = _act(env, run_id, g, did, "resolve", {**base, **reviewed}, 200)
    assert out["decision"]["status"] == "abandoned"
    (stored,) = _requests(env)
    assert json.loads(stored["response_json"])["reviewed_evidence"] == {
        "evidence_version": reviewed["evidence_version"], "evidence_refs": sorted(reviewed["evidence_refs"])}
    event = _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE node_key = ? AND cause = "
                            "'gate_recovery_abandoned'", (g,))
    assert json.loads(event[0]["payload_json"])["reviewed_evidence"]["evidence_refs"] == sorted(
        reviewed["evidence_refs"])
    conflict = _act(env, run_id, g, did, "resolve", {**base, **reviewed, "evidence_refs": []}, 409)
    assert conflict["error"]["code"] == "gate_request_conflict"
