"""flowgate.v02.0098 T#2 (D0004 §3.4, §3.6 to §3.6.3; T0008): the recorded merge target of a gate decision,
its resume, unmerge limit, lock slot and ref movement on real local targets and a temporary bare origin.

Moved unchanged from ``test_fg_gate_publish_0098`` (TR0009), whose ``_0098_target`` it imports, and uses the
harness of ``test_fg_gate_publish_0085``. ``--dist loadfile`` gives one file to one worker, and the 20 cases of
the one file took about as long as the 1800 s Self-check limit on a loaded host.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fg_card_settings import FA, _sql, db_path, env  # noqa: E402,F401
from test_fg_gate_intake_0085 import product  # noqa: E402,F401
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    _git, _rev, _work, _gate, _run_job, _expire_and_reconcile, _decision, _effects, _merges, _confirm, _act, _version,
    _start_linear, git_env)
from test_fg_gate_publish_0098 import _0098_target  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


def test_0098_legacy_decision_resumes_its_recorded_target_without_a_new_field(git_env):
    r = git_env
    env, g, _, run_id = _start_linear(r)
    _work(r, "legacy.txt")
    _gate(env, g, merge_mode="merge")
    body = _confirm(env, run_id, g, "legacy-0098")
    did = body["decision"]["decision_id"]
    saved = json.loads(_decision(env, did)["settings_json"])
    saved["shown"].pop("merge_target_branch")
    saved["effective"].pop("merge_target_branch")
    saved.pop("target_basis")
    _sql(env["db"], "UPDATE gate_decisions SET settings_json = ? WHERE decision_id = ?", (json.dumps(saved), did))
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"


def test_0098_new_nonbase_unmerge_is_rejected_before_any_job_or_ref_mutation(git_env):
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import merge_target as mt
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    _work(r, "no-unmerge.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch)
    body = _confirm(env, run_id, g, "unmerge-0098")
    did = body["decision"]["decision_id"]
    assert _run_job(body["job"]["job_id"])[0]["status"] == "succeeded"
    applied = _rev(r["base"], branch)
    state = svc.db_git.get_state(FA)
    jobs = _sql(env["db"], "SELECT * FROM operation_job")
    with pytest.raises(svc.GitServiceError) as caught:
        svc.unmerge(FA, applied)
    assert caught.value.code == "unmerge_unsupported_target"
    assert caught.value.details["target_branch"] == branch
    assert caught.value.details["recovery_options"]
    mid = json.loads(_effects(env, did)["merge"]["evidence_json"])["merge_id"]
    context = svc.db_git.session_context(svc.db_git.get_session(mid))
    context.pop(mt.TARGET_RECORD_KEY)
    svc.db_git.set_session_context(mid, context)
    with pytest.raises(svc.GitServiceError) as caught:
        svc.unmerge(FA, applied)
    assert caught.value.code == "unmerge_target_unknown"
    assert svc.db_git.get_state(FA) == state and _rev(r["base"], branch) == applied
    assert _sql(env["db"], "SELECT * FROM operation_job") == jobs


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
    assert _rev(r["base"], branch) == applied and _merges(r["base"], branch) == 1


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
