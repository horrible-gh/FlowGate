"""flowgate.v02test.0002 T-b (0006-T §4, R0001 requirement 3): a merge target changed **before** the confirmation,
while the candidate lookup times out, is the branch the decision records, merges into and pushes.

The client keeps the last confirmed candidate list (``useGateContext``) and sends a kept name with the exact body
``{field: "merge_target_branch", value, expected_settings_version}``, then confirms with the saved settings version
(``checkTargets.0098.spec.ts``). This file sends the same requests to the real routes, on the real repository harness
of ``test_fg_gate_publish_0085`` (a bare ``origin``, the base checkout and the group worktree), with the lookup timing
out from the change on: the PATCH, the confirm preflight and the publish judge the one name themselves. It checks the
saved name, the decision's ``target_branch``, the actual local and pushed refs, and that the first choice and ``main``
did not move. The facts are printed on one ``TB0002_EVIDENCE`` line for the work report.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fg_card_settings import FA, _sql, db_path, env, get_settings, patch  # noqa: E402,F401
from test_fg_gate_intake_0085 import _attempt, _current, _jobs, product  # noqa: E402,F401
from test_fg_gate_publish_0085 import (  # noqa: E402,F401
    _confirm, _decision, _effects, _git, _merges, _rev, _run_job, _start_linear, _work, git_env)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git binary required")


def _lookup_times_out(monkeypatch):
    """The settings view's candidate lookup spends its budget (as ``test_git_merge_target_trace_0119``): the server
    answers ``timeout`` with no candidates. A single-name judgement (PATCH / preflight / publish) has no such budget."""
    from modules.flow_gate.services.git import merge_target as mt
    real = mt.inspect_workspace

    def slow(project_id, name):
        time.sleep(0.4)
        return real(project_id, name)

    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 0.3)
    # flowgate.v02test.0020: the background computation's own budget runs out too, so the lookup keeps timing out
    monkeypatch.setattr(mt, "CHECK_TARGET_COMPUTE_SEC", 0.3)
    monkeypatch.setattr(mt, "inspect_workspace", slow)
    mt.reset_check_targets()        # no list from before is served (an invalidation would keep it, stale)


def test_0002_tb_a_target_changed_before_the_confirmation_during_a_lookup_timeout_is_merged_and_pushed(
        git_env, monkeypatch):
    from modules.flow_gate.services.git import merge_target as mt
    r = git_env
    env, g, _, run_id = _start_linear(r)
    first, chosen = "review/first-0002", "review/kept-0002"
    for name in (first, chosen):
        _git(["branch", name, "main"], cwd=r["base"])
    mt.reset_check_targets()
    frozen = _work(r, "tb-0002.txt")

    # 1. The lookup works: the Designer reads the candidates and saves the first choice (versioned, as the client).
    # On a loaded Self-check host the real 3 s budget alone was spent in the shared reads (TR0013 rev 1, the R0001 #3
    # condition itself); this phase needs one confirmed list, so only here the test gives the lookup more time.
    monkeypatch.setattr(mt, "CHECK_TARGET_BUDGET_SEC", 60.0)
    mt.reset_check_targets()
    view = get_settings(env, FA, g)
    assert view["merge_target_state"]["status"] == "ok", view["merge_target_state"]
    assert {first, chosen} <= set(view["merge_target_options"])
    version = view["settings_version"]
    for field, value in (("merge_mode", "merge"), ("merge_target_branch", first), ("push", True)):
        saved = patch(env, FA, g, {"field": field, "value": value, "expected_settings_version": version})
        assert saved["settings_version"] > version
        version = saved["settings_version"]
    waiting = _current(env)
    assert (waiting["status"], waiting["idle_reason"]) == ("idle", "waiting_confirm")
    before = {name: _rev(r["base"], name) for name in (first, chosen, "main")}
    assert _rev(r["bare"], "refs/heads/" + chosen) is None

    # 2. The lookup times out: no candidates in the answer (the client offers its kept list), the stored name stays.
    _lookup_times_out(monkeypatch)
    failed = get_settings(env, FA, g)
    state = failed["merge_target_state"]
    assert (state["status"], state["code"]) == ("timeout", "merge_target_options_timeout"), state
    assert failed["merge_target_options"] == []
    assert failed["fields"]["merge_target_branch"]["value"] == first and failed["settings_version"] == version

    # 3. Before the confirmation, a kept name is saved with the client's body; the server judges it again.
    saved = patch(env, FA, g, {"field": "merge_target_branch", "value": chosen, "expected_settings_version": version})
    assert saved["fields"]["merge_target_branch"]["value"] == chosen and saved["settings_version"] > version
    stale_version, version = version, saved["settings_version"]
    again = get_settings(env, FA, g)                                       # a GET / F5 shows the server's choice
    assert again["merge_target_state"]["status"] == "timeout"
    assert again["fields"]["merge_target_branch"]["value"] == chosen and again["settings_version"] == version
    assert {name: _rev(r["base"], name) for name in (first, chosen, "main")} == before   # nothing applied yet

    # 4. A confirmation from the screen before the change is refused and writes nothing.
    attempt = _attempt(env, run_id, g)["attempt_id"]
    stale = _confirm(env, run_id, g, "tb-0002-stale", expect=409, expected_attempt_id=attempt,
                     expected_settings_version=stale_version)
    assert stale["error"]["code"] == "gate_context_changed"
    assert stale["error"]["params"]["reason"] == "settings_version"
    assert _jobs(env) == []

    # 5. The confirmation of the shown choice (the saved version), then Merge & Push.
    body = _confirm(env, run_id, g, "tb-0002", expected_attempt_id=attempt, expected_settings_version=version)
    did, jid = body["decision"]["decision_id"], body["job"]["job_id"]
    row = _decision(env, did)
    recorded = json.loads(row["settings_json"])
    assert row["target_branch"] == chosen
    assert recorded["shown"]["merge_target_branch"] == recorded["effective"]["merge_target_branch"] == chosen
    assert recorded["target_basis"]["accepted_target_sha"] == before[chosen]
    job, _ = _run_job(jid)
    assert job["status"] == "succeeded", job
    applied = _rev(r["base"], chosen)
    assert applied != before[chosen]
    assert _git(["merge-base", "--is-ancestor", frozen, applied], cwd=r["base"]) == ""
    assert _rev(r["bare"], "refs/heads/" + chosen) == applied               # pushed to the chosen name
    assert _rev(r["base"], first) == before[first] and _rev(r["base"], "main") == before["main"]
    assert _rev(r["bare"], "refs/heads/" + first) is None
    effects = _effects(env, did)
    assert effects["merge"]["sha_before"] == before[chosen]
    pushed = json.loads(effects["push"]["evidence_json"])
    assert pushed["sent_sha"] == applied
    print("TB0002_EVIDENCE " + json.dumps({
        "lookup_after_change": again["merge_target_state"]["status"], "options_after_change": failed["merge_target_options"],
        "first_choice": first, "saved_choice": saved["fields"]["merge_target_branch"]["value"],
        "settings_version": {"stale": stale_version, "confirmed": version},
        "decision_target_branch": row["target_branch"], "decision_shown": recorded["shown"]["merge_target_branch"],
        "decision_effective": recorded["effective"]["merge_target_branch"],
        "target_sha_before": before[chosen], "frozen_work_sha": frozen,
        "local_ref": f"refs/heads/{chosen}", "local_sha_after": applied,
        "origin_ref": f"refs/heads/{chosen}", "origin_sha_after": _rev(r["bare"], "refs/heads/" + chosen),
        "first_choice_sha_unchanged": _rev(r["base"], first) == before[first],
        "main_sha_unchanged": _rev(r["base"], "main") == before["main"],
        "merge_commits_on_target": _merges(r["base"], chosen), "job_status": job["status"]}, sort_keys=True))
