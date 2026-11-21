"""(to; the earlier implementation): the recorded merge target of a gate decision,
its execution, push and unmerge limits on real local targets and a temporary bare origin.

Uses the harness of ``test_fg_gate_publish_0085``. It is its own file so that ``--dist loadfile`` can run it
beside that file; one file is one worker, and together they exceeded the 1800 s Self-check limit.
The resume, unmerge, lock slot and ref-movement cases are in ``test_fg_gate_publish_0098_resume``: this
file's 20 cases on one worker took about as long as that limit on a loaded host.
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


# real local targets and a temporary bare origin.
def _0098_target(r, name="review/selected"):
    from modules.flow_gate.services.git import merge_target as mt
    _git(["branch", name, "main"], cwd=r["base"])
    mt.reset_check_targets()        # a branch made outside FlowGate: forget the list from before
    return name


@pytest.mark.parametrize("diverged", [False, True])
@pytest.mark.parametrize("push", [False, True])
def test_0098_selected_local_target_uses_its_snapshot_and_creates_origin_only_when_on(git_env, push, diverged):
    from modules.flow_gate.services import git_service as svc
    r = git_env
    env, g, _, run_id = _start_linear(r)
    branch = _0098_target(r)
    if diverged:
        side = _git(["commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", "independent target history"], cwd=r["base"])
        _git(["update-ref", "refs/heads/" + branch, side], cwd=r["base"])
    before = _rev(r["base"], branch)
    work_base = _sql(env["db"], "SELECT work_base_ref FROM groups WHERE group_id = ?", (FA,))[0]
    frozen = _work(r, "selected.txt")
    _gate(env, g, merge_mode="merge", merge_target_branch=branch, push=push)
    from test_fg_gate_intake_0085 import _current, _jobs, _nodes
    waiting = _current(env)
    assert (waiting["status"], waiting["idle_reason"]) == ("idle", "waiting_confirm")
    assert _jobs(env) == [] and _rev(r["base"], branch) == before
    assert _rev(r["bare"], "refs/heads/" + branch) is None  # no early merge or push
    body = _confirm(env, run_id, g, "selected-0098")
    assert _rev(r["base"], branch) == before and _rev(r["bare"], "refs/heads/" + branch) is None
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
    assert applied != before
    assert _git(["merge-base", "--is-ancestor", frozen, applied], cwd=r["base"]) == ""
    assert _merges(r["base"], branch) == (1 if diverged else 0)
    assert (_rev(r["base"], applied + "^2") == frozen) if diverged else applied == frozen
    assert _rev(r["base"], "main") == saved["target_basis"]["scope_floor_sha"]
    assert _rev(r["bare"], "refs/heads/" + branch) == (applied if push else None)
    effects = _effects(env, did)
    assert "archive" not in effects
    assert _sql(env["db"], "SELECT * FROM gate_archive_checkpoints WHERE decision_id = ?", (did,)) == []
    assert _nodes(_current(env))[g]["gate"]["job"] is None
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
