"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_publish_0098.py."""
from __future__ import annotations

from tests.test_fg_gate_publish_0098 import (
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

@pytest.mark.parametrize("code", ["merge_target_incoming_work_base", "merge_target_preview_unavailable",
                                 "merge_target_busy", "merge_target_owner_mismatch", "gate_git_unavailable"])
def test_0098_execution_uses_common_judgement_and_parks_specific_safety_errors(git_env, monkeypatch, code):
    from modules.flow_gate.services.git import merge_target as mt
    from modules.flow_gate.services.git.credentials import GitServiceError
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    _work(r, "unfit.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch)
    body = _confirm(env, run_id, g, "unfit-run-0098")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    saved = json.loads(_decision(env, did)["settings_json"])["target_basis"]
    def fail(pid, gid, target, **kw):
        assert target == branch and kw["work_base_ref"] == saved["work_base_ref"]
        assert kw["scope_floor_sha"] == saved["scope_floor_sha"]
        raise GitServiceError(409, code, "private/path", details={"diagnostic": "secret"})
    monkeypatch.setattr(mt, "judge_check_target", fail)
    job, _ = _run_job(jid)
    assert job["status"] == "recovery_required" and job["last_error_code"] == code
    assert _decision(env, did)["status"] == "failed"
    assert _effects(env, did)["merge"]["error_code"] == code
    assert "private/path" not in json.dumps(body)
