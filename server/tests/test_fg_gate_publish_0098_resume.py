"""(to; the earlier implementation): the recorded merge target of a gate decision,
its resume, unmerge limit, lock slot and ref movement on real local targets and a temporary bare origin.

Moved unchanged from ``test_fg_gate_publish_0098``, whose ``_0098_target`` it imports, and uses the
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
