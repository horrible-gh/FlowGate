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

@pytest.mark.parametrize("bad_owner", [False, True, "probe"])
def test_0098_nonbase_resume_uses_own_attempt_and_never_takes_an_external_marker(git_env, monkeypatch, bad_owner):
    from modules.flow_gate.services.git import merge_target as mt
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    frozen = _work(r, "resume.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch)
    body = _confirm(env, run_id, g, "resume-0098")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    real = mt.finish_manual_attempt
    def crash(*a, **kw):
        raise RuntimeError("cut after local merge")
    monkeypatch.setattr(mt, "finish_manual_attempt", crash)
    job, _ = _run_job(jid)
    assert job["status"] == "running"
    applied = _rev(r["base"], branch)
    effect = _effects(env, did)["merge"]
    mid = json.loads(effect["evidence_json"])["merge_id"]
    ctx = mt.resolve_session_target(svc.db_git.get_session(mid))
    marker_path = ctx.workspace_dir / mt.OWNER_MARKER
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if bad_owner is True:
        marker["owner"] = "external-owner"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(mt, "finish_manual_attempt", real)
    _expire_and_reconcile(jid)
    if bad_owner == "probe":
        from flowgate.gate_decisions import git_adapter
        read_ref = git_adapter.rev
        monkeypatch.setattr(git_adapter, "rev", lambda repo, ref: git_adapter.READ_ERROR if ref == "refs/heads/" + branch else read_ref(repo, ref))
    job, _ = _run_job(jid)
    if bad_owner:
        assert job["last_error_code"] == ("gate_git_unavailable" if bad_owner == "probe" else "merge_target_owner_mismatch")
        assert job["status"] == "recovery_required" and _decision(env, did)["status"] == "recovery_required"
        assert svc.db_git.get_session(mid)["status"] == "open"
        if bad_owner is True:
            assert json.loads(marker_path.read_text(encoding="utf-8"))["owner"] == "external-owner"
        assert _effects(env, did)["merge"]["status"] == "unknown"
    else:
        assert job["status"] == "succeeded", job
        assert _decision(env, did)["frozen_sha"] == frozen
    assert _rev(r["base"], branch) == applied == frozen and _merges(r["base"], branch) == 0
def test_0098_project_base_change_cannot_switch_the_recorded_lock_slot(git_env, monkeypatch):
    from flowgate.gate_decisions import git_adapter
    from dataclasses import replace
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    _work(r, "base-context.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch)
    body = _confirm(env, run_id, g, "context-0098")
    did = body["decision"]["decision_id"]
    real = git_adapter.slot_of
    monkeypatch.setattr(git_adapter, "slot_of", lambda gid: replace(real(gid), cfg={**real(gid).cfg, "base_branch": branch}))
    job, _ = _run_job(body["job"]["job_id"])
    assert job["last_error_code"] == "gate_git_unavailable" and _decision(env, did)["status"] == "failed"
    ev = json.loads(_effects(env, did)["merge"]["evidence_json"])
    assert ev["reason"] == "git_context_changed"
