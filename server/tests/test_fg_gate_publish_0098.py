"""flowgate.v02.0098 T#2 (D0004 §3.4, §3.6 to §3.6.3; T0008): the recorded merge target of a gate decision,
its execution, push and unmerge limits on real local targets and a temporary bare origin.

Uses the harness of ``test_fg_gate_publish_0085``. It is its own file so that ``--dist loadfile`` can run it
beside that file; one file is one worker, and together they exceeded the 1800 s Self-check limit (TR0009).
The resume, unmerge, lock slot and ref-movement cases are in ``test_fg_gate_publish_0098_resume``: this
file's 20 cases on one worker took about as long as that limit on a loaded host (TR0009).
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

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


# flowgate.v02.0098 T#2 -- real local targets and a temporary bare origin.
def _0098_target(r, name="review/selected"):
    from modules.flow_gate.services.git import merge_target as mt
    _git(["branch", name, "main"], cwd=r["base"])
    mt.invalidate_check_targets()
    return name


@pytest.mark.parametrize("push", [False, True])
def test_0098_selected_local_target_uses_its_snapshot_and_creates_origin_only_when_on(git_env, push):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    before = _rev(r["base"], branch)
    work_base = _sql(env["db"], "SELECT work_base_ref FROM groups WHERE group_id = ?", (FA,))[0]
    frozen = _work(r, "selected.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch, push=push)
    body = _confirm(env, run_id, g, "selected-0098")
    assert _sql(env["db"], "SELECT work_base_ref FROM groups WHERE group_id = ?", (FA,))[0] == work_base
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    saved = json.loads(_decision(env, did)["settings_json"])
    assert saved["shown"]["merge_target_branch"] == saved["effective"]["merge_target_branch"] == branch
    assert saved["target_basis"]["accepted_target_sha"] == before
    assert saved["target_basis"]["scope_floor_sha"]
    assert saved["target_basis"]["unmerge_supported"] is False
    # Alter inheritance after acceptance. The recorded work base/floor still govern execution.
    _sql(env["db"], "UPDATE groups SET work_base_ref = ? WHERE group_id = ?", ("missing-now", FA))
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    applied = _rev(r["base"], branch)
    assert applied != before and _rev(r["base"], applied + "^2") == frozen
    assert _rev(r["base"], "main") == before
    assert _rev(r["bare"], "refs/heads/" + branch) == (applied if push else None)
    effects = _effects(env, did)
    assert effects["merge"]["sha_before"] == before
    assert json.loads(_decision(env, did)["settings_json"])["target_basis"]["accepted_target_sha"] == before
    mid = json.loads(effects["merge"]["evidence_json"])["merge_id"]
    session = svc.db_git.get_session(mid)
    from modules.flow_gate.services.git import merge_target as mt
    rec = mt.target_record(session)
    assert rec["project_base_branch"] == "main" and rec["unmerge_supported"] is False
    assert "unmerge_unsupported_for_non_base_target" in rec["warnings"]
    if push:
        evidence = json.loads(effects["push"]["evidence_json"])
        assert evidence["remote_head_before"] is None and evidence["attempt_kind"] == "create"
        assert evidence["sent_sha"] == applied
    assert _sql(env["db"], "SELECT work_base_ref FROM groups WHERE group_id = ?", (FA,))[0]["work_base_ref"] == "missing-now"


@pytest.mark.parametrize("change,code", [("delete", "merge_target_not_found"), ("move", "target_moved")])
def test_0098_changed_target_parks_without_adopting_a_new_sha_and_retry_requires_the_original(git_env, change, code):
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    before = _rev(r["base"], branch)
    frozen = _work(r, "changed.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch)
    body = _confirm(env, run_id, g, "changed-0098")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    if change == "delete":
        _git(["update-ref", "refs/remotes/origin/" + branch, before], cwd=r["base"])
        _git(["update-ref", "-d", "refs/heads/" + branch], cwd=r["base"])
    else:
        _git(["update-ref", "refs/heads/" + branch, frozen], cwd=r["base"])
    job, _ = _run_job(jid)
    assert (job["status"], job["last_error_code"], job["retryable"], job["recovery_since"]) == (
        "recovery_required", code, 1, None)
    assert _decision(env, did)["status"] == "failed"
    effect = _effects(env, did)["merge"]
    assert effect["error_code"] == code and effect["status"] == "failed"
    ev = json.loads(effect["evidence_json"])
    assert ev["accepted_target_sha"] == before and ev["stage"] == "target_check"
    assert ev["target_deleted"] == (change == "delete")
    assert _decision(env, did)["target_sha"] == before
    _git(["update-ref", "refs/heads/" + branch, before], cwd=r["base"])
    _act(env, run_id, g, did, "retry", {"request_id": "restore-0098", "expected_decision_version": _version(env, did)}, 202)
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    assert _decision(env, did)["frozen_sha"] == frozen


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


def test_0098_rejected_push_keeps_local_merge_and_retries_only_the_same_applied_sha(git_env):
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    _work(r, "ours.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch, push=True)
    body = _confirm(env, run_id, g, "rejected-0098")
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    _git(["checkout", "-b", branch], cwd=r["seed"])
    (r["seed"] / "theirs.txt").write_text("theirs\n", encoding="utf-8")
    _git(["add", "."], cwd=r["seed"])
    _git(["commit", "-m", "remote advance"], cwd=r["seed"])
    _git(["push", "origin", branch], cwd=r["seed"])
    job, _ = _run_job(jid)
    assert job["status"] == "recovery_required" and job["last_error_code"] == "push_rejected"
    assert _decision(env, did)["status"] == "recovery_required" and job["recovery_since"] is None
    applied = _rev(r["base"], branch)
    assert _effects(env, did)["push"]["status"] == "failed"
    _act(env, run_id, g, did, "retry", {"request_id": "push-retry-0098", "expected_decision_version": _version(env, did)}, 202)
    job, _ = _run_job(jid)
    assert job["last_error_code"] == "push_rejected" and _rev(r["base"], branch) == applied
    assert _merges(r["base"], branch) == 1
    assert json.loads(_effects(env, did)["push"]["evidence_json"])["sent_sha"] == applied
