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

def test_effects_confirmed_never_closes_an_intent_whose_snapshot_was_not_pinned(git_env):
    """commit mode, push and archive OFF, stopped before its snapshot (another commit sits in its pin): nothing is
    proven - no frozen SHA, no pin at it - so effects_confirmed is refused and nothing follows."""
    r = git_env
    env, g, t, run_id = _start_linear(r)
    _work(r, "feature.txt")
    (r["base"] / "README.md").write_text("main moved\n", encoding="utf-8")
    _git(["commit", "-am", "main only"], cwd=r["base"])
    foreign = _rev(r["base"], "HEAD")                                        # not a commit of the group branch
    _gate(env, g, merge_mode="commit")
    body = _confirm(env, run_id, g, "u1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    _git(["update-ref", f"refs/flowgate/gate-pin/{did}", foreign], cwd=r["base"])
    job, _ = _run_job(job_id)
    assert (job["status"], job["last_error_code"]) == ("recovery_required", "pin_ref_mismatch")
    d = _decision(env, did)
    assert (d["status"], d["frozen_sha"]) == ("recovery_required", None)
    refused = _act(env, run_id, g, did, "resolve", {"request_id": "u2", "expected_decision_version": d[
        "decision_version"], "resolution": "effects_confirmed", "reason": "looks fine", **_evidence(env, g)}, 409)
    assert refused["error"]["code"] == "gate_effects_unverified"
    assert refused["error"]["params"]["missing"] == ["snapshot", "commit"]
    assert _decision(env, did)["status"] == "recovery_required" and _job_row(env, job_id)["status"] == \
        "recovery_required"
    assert _states(env)[g] == "open" and _gate_events(env, g) == [] and _states(env)[t] == "pending"
def test_an_unwritable_recovery_ref_stays_an_obligation_through_the_round_split(git_env, monkeypatch):
    """abandon goes on when the merge result's recovery ref cannot be written, but that SHA is never recorded as
    kept by another ref (not the pin): it stays an obligation, the end cleanup keeps the slot for it after the round
    split, and writes the ref itself once it can."""
    from types import SimpleNamespace
    from flowgate.gate_decisions import cleanup, git_adapter, repository as repo
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    merged = _rev(r["base"], "refs/heads/main")
    real, broken = git_adapter.create_ref, {"on": True}

    def create_ref(repo_root, ref, target):
        if broken["on"] and ref == f"refs/flowgate/gate-recovery/{did}-merge":
            return "error"
        return real(repo_root, ref, target)

    monkeypatch.setattr(git_adapter, "create_ref", create_ref)
    out = _act(env, run_id, g, did, "resolve", {"request_id": "ob1", "expected_decision_version": _version(env, did),
                                                "resolution": "abandon", "reason": "remote gone",
                                                **_evidence(env, g)}, 200)
    assert out["decision"]["status"] == "abandoned"
    effects = _effects(env, did)
    merge_keep = json.loads(effects["merge"]["evidence_json"])["retention"]
    assert effects["merge"]["recovery_ref"] is None and merge_keep == {
        "sha": merged, "ref": f"refs/flowgate/gate-recovery/{did}-merge", "kept": False}
    assert effects["snapshot"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}"      # its own SHA, kept
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-merge") is None
    assert [o["sha"] for o in repo.retention_obligations(FA)] == [merged]
    # The Time Machine's round split (card 4) clears the flag only; the obligation stays.
    d = _decision(env, did)
    assert repo.move(did, d["decision_version"], recovery_reset_required=False)
    _sql(env["db"], "UPDATE graph_runs SET status = 'idle', idle_reason = 'completed' WHERE run_id = ?", (run_id,))
    with get_store().transaction():
        job = cleanup.request(SimpleNamespace(flow_id=FA, run_id=run_id, jobs=[], project_id=PROJECT))
    assert _run_job(job["job_id"])[0]["status"] == "succeeded"
    rec = _cleanups(env)[-1]
    assert (rec["result"], rec["result_reason"]) == ("retained", "recovery_ref_unpreserved")
    assert r["wt"].is_dir() and int(svc.db_git.get_state(FA)["worktree_registered"]) == 1
    broken["on"] = False                                                      # the ref can be written again
    with get_store().transaction():
        job = cleanup.request(SimpleNamespace(flow_id=FA, run_id=run_id, jobs=[], project_id=PROJECT))
    assert _run_job(job["job_id"])[0]["status"] == "succeeded"
    assert _rev(r["base"], f"refs/flowgate/gate-recovery/{did}-merge") == merged
    assert repo.retention_obligations(FA) == []
    assert _effects(env, did)["merge"]["recovery_ref"] == f"refs/flowgate/gate-recovery/{did}-merge"
    assert _cleanups(env)[-1]["result_reason"] != "recovery_ref_unpreserved"
