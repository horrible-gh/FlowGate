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

def test_a_pin_left_by_a_cut_off_snapshot_is_taken_back_although_the_head_moved(git_env, monkeypatch):
    """A pin written whose SHA was not stored yet is the snapshot: the resume takes it back instead of the HEAD of now
    (never a permanent pin_ref_mismatch)."""
    from flowgate.gate_decisions import git_adapter
    r = git_env
    env, g, t, run_id = _start_linear(r)
    sha_a = _work(r, "feature.txt")
    _gate(env, g, merge_mode="hold", archive=True)
    body = _confirm(env, run_id, g, "q1")
    did, job_id = body["decision"]["decision_id"], body["job"]["job_id"]
    real, calls = git_adapter.create_ref, []

    def crash(repo_root, ref, target):
        out = real(repo_root, ref, target)
        if ref == f"refs/flowgate/gate-pin/{did}" and not calls:
            calls.append(1)
            raise RuntimeError("process died after the pin")
        return out

    monkeypatch.setattr(git_adapter, "create_ref", crash)
    job, _ = _run_job(job_id)
    assert job["status"] == "running" and _decision(env, did)["frozen_sha"] is None
    assert _rev(r["base"], f"refs/flowgate/gate-pin/{did}") == sha_a
    sha_b = _work(r, "later.txt")                                              # the group HEAD moved meanwhile
    assert _expire_and_reconcile(job_id)["status"] == "pending"
    job, _ = _run_job(job_id)
    assert job["status"] == "succeeded"
    assert _decision(env, did)["frozen_sha"] == sha_a != sha_b
    assert _rev(r["base"], f"refs/flowgate/gate-archive/{did}") == sha_a
    snapshot = json.loads(_effects(env, did)["snapshot"]["evidence_json"])
    assert snapshot["pin_taken_back"] is True
@pytest.mark.parametrize("cut", ["worktree", "branch", "ledger", "record"])
def test_a_cleanup_cut_off_at_any_step_keeps_its_plan_and_restores_the_real_end_head(git_env, monkeypatch, cut):
    """The plan (the real end HEAD, the basis) is on the job before the first delete; every step is resumed; the
    slot comes back at that HEAD whatever step the cleanup ended at."""
    from flowgate.gate_decisions import cleanup, repository
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, run_id, sha, cj = _completed_merge_flow(r)
    calls = []
    if cut == "record":
        real_insert = repository.insert_cleanup

        def failing(row):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("process died before the record")
            return real_insert(row)
        monkeypatch.setattr(repository, "insert_cleanup", failing)
    else:
        name = {"worktree": "_step_worktree", "branch": "_step_branch", "ledger": "_step_ledger"}[cut]
        real_step = getattr(cleanup, name)

        def cut_after(*args):
            out = real_step(*args)
            if not calls:
                calls.append(1)
                raise RuntimeError(f"process died after the {cut} step")
            return out
        monkeypatch.setattr(cleanup, name, cut_after)
    job, _ = _run_job(cj)
    assert job["status"] == "running" and job["phase"] == cut and calls
    plan = json.loads(job["evidence"])["plan"]
    assert (plan["end_head"], plan["branch"], plan["record"]["basis_kind"]) == (sha, r["branch"], "merged_target")
    assert cleanup.restore_point(PROJECT, FA) == (None, "git_busy")           # never "nothing to restore"
    assert _expire_and_reconcile(cj)["status"] == "pending"
    job, _ = _run_job(cj)
    assert job["status"] == "succeeded"
    (rec,) = _cleanups(env)
    assert (rec["result"], rec["end_head_sha"], rec["job_id"]) == ("deleted", sha, cj)
    assert not r["wt"].exists() and _rev(r["base"], f"refs/heads/{r['branch']}") is None
    assert int(svc.db_git.get_state(FA)["worktree_registered"]) == 0
    start, refused = cleanup.restore_point(PROJECT, FA)
    assert (start, refused) == (sha, None) and _rev(r["base"], f"refs/heads/{r['branch']}") == sha
    assert svc.ensure_worktree(PROJECT, "v02", FA, trigger="gate_restore", start_point=start) == "ok"
    assert _rev(r["wt"], "HEAD") == sha
