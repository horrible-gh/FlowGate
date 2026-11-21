"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_0098_resume.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_0098_resume import (
    FA,
    Path,
    _0098_target,
    _act,
    _confirm,
    _decision,
    _effects,
    _expire_and_reconcile,
    _gate,
    _git,
    _merges,
    _rev,
    _run_job,
    _sql,
    _start_linear,
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

def test_0098_ref_moving_between_the_judgement_and_adapter_entry_is_still_refused(git_env, monkeypatch):
    from flowgate.gate_decisions import git_adapter
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    frozen = _work(r, "raced.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch)
    body = _confirm(env, run_id, g, "raced-0098")
    real = git_adapter.validate_merge_target
    def race(*a, **kw):
        real(*a, **kw)
        _git(["update-ref", "refs/heads/" + branch, frozen], cwd=r["base"])
    monkeypatch.setattr(git_adapter, "validate_merge_target", race)
    job, _ = _run_job(body["job"]["job_id"])
    assert job["last_error_code"] == "target_moved"
    assert _merges(r["base"], branch) == 0
    ev = json.loads(_effects(env, body["decision"]["decision_id"])["merge"]["evidence_json"])
    assert ev["stage"] == "merge_entry"
def test_0098_legacy_transient_git_failure_keeps_its_existing_retry_contract(git_env, monkeypatch):
    from flowgate.gate_decisions import git_adapter
    r = git_env
    env, g, _, run_id = _start_linear(r)
    _work(r, "legacy-transient.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "legacy-transient-0098")
    did = body["decision"]["decision_id"]
    saved = json.loads(_decision(env, did)["settings_json"])
    saved["shown"].pop("merge_target_branch")
    saved["effective"].pop("merge_target_branch")
    saved.pop("target_basis")
    _sql(env["db"], "UPDATE gate_decisions SET settings_json = ? WHERE decision_id = ?", (json.dumps(saved), did))
    def unavailable(*a, **kw):
        raise git_adapter.GitServiceError(503, "legacy_probe_unavailable", "local probe failed")
    monkeypatch.setattr(git_adapter, "merge", unavailable)
    job, _ = _run_job(body["job"]["job_id"])
    assert job["status"] == "retry_wait" and job["last_error_code"] == "legacy_probe_unavailable"
    assert _decision(env, did)["status"] == "accepted"
@pytest.mark.parametrize("deleted", [False, True])
def test_0098_target_moving_after_workspace_preparation_records_the_observed_sha(git_env, monkeypatch, deleted):
    from modules.flow_gate.services.git import merge_target as mt
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    accepted = _rev(r["base"], branch)
    frozen = _work(r, "workspace-race.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch)
    body = _confirm(env, run_id, g, "workspace-race-0098")
    real = mt.prepare_workspace
    def race(ctx, **kw):
        real(ctx, **kw)
        args = ["update-ref", "-d", "refs/heads/" + branch] if deleted else ["update-ref", "refs/heads/" + branch, frozen]
        _git(args, cwd=r["base"])
    monkeypatch.setattr(mt, "prepare_workspace", race)
    job, _ = _run_job(body["job"]["job_id"])
    did = body["decision"]["decision_id"]
    assert job["last_error_code"] == ("merge_target_not_found" if deleted else "target_moved")
    assert _decision(env, did)["status"] == "failed"
    effect = _effects(env, did)["merge"]
    ev = json.loads(effect["evidence_json"])
    assert effect["status"] == "failed" and ev["accepted_target_sha"] == accepted
    assert ev["observed_target_sha"] == (None if deleted else frozen)
    assert ev["target_deleted"] == deleted and ev["stage"] == "target_checkout" and ev["merge_id"]
    assert _rev(r["base"], "refs/heads/" + branch) == (None if deleted else frozen)
    if not deleted:
        assert _merges(r["base"], branch) == 0
