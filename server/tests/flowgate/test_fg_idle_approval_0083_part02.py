"""Independently selectable scenarios; shared fixtures remain in test_fg_idle_approval_0083.py."""
from __future__ import annotations

from test_fg_idle_approval_0083 import (
    AI,
    FA,
    HOLD,
    LEE,
    SimpleNamespace,
    WAITS,
    _admin_and_reviewer,
    _approve,
    _begin,
    _cards,
    _chain,
    _current,
    _executions_ended,
    _git,
    _h,
    _human_result,
    _integration,
    _merged,
    _no_command,
    _node,
    _person_registers,
    _person_waits,
    _person_waits_on_code,
    _prepared_worktree,
    _propagations,
    _register,
    _reported,
    _result,
    _review_status,
    _run_ended,
    _run_events_after,
    _run_id,
    _run_status,
    _seq,
    _sql,
    _start_ai_run,
    _v01_approve,
    card_outputs_port,
    db_path,
    decided,
    env,
    group_tree,
    legacy_cards,
    no_document_gates,
    passive_person_results,
    pre_feature_attempts,
    pytest,
    pytestmark,
    shutil,
    subprocess,
)

@pytest.mark.parametrize("wait, before_resume", [("command_chain", "pending"), ("reported", "pending")])
def test_with_the_0061_t2_rule_the_reported_wait_needed_a_resume(pre_feature_attempts, env, decided, monkeypatch, wait, before_resume):
    """What is revised: with the 0061 T#2 ``defers`` (every run not running waits) only a wait whose command its events
    prove went on (``command.resume_approval``, 0083 T#2); the reported wait left the next card pending after the
    approval, and only the next resume opened it — the reported "a person approves, then resumes".
    flowgate.v02.0119 T#3 (T-2): ``command.resume_approval`` wakes a confirm wait only (``waiting_confirm``), so a card's
    ``needs_intervention`` wait is never that command chain's either: under the old rule both waits need the resume; the
    revised ``defers`` (the cases above) moves both on at once."""
    from modules.flow_gate.graph_workflow.domain import frontier

    monkeypatch.setattr(frontier, "defers", lambda s: s.status != "running")      # the rule before the revision
    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], wait=wait)
    _approve(env, t1, body={"result_doc_id": result})
    assert (_node(env, t1)[0], _node(env, t2)[0]) == ("done", before_resume)
    assert [j["node_key"] for j in env["jobs"]] == ([t2] if before_resume == "open" else [])
    if before_resume == "pending":
        assert _propagations(env, t1) == ["pending"]
        resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": "full"}, headers=_h())
        assert resumed.status_code == 200, resumed.text
        assert (_node(env, t2)[0], [j["node_key"] for j in env["jobs"]]) == ("open", [t2])
@WAITS
def test_a_step_still_keeps_the_next_card_pending_after_a_person_s_approval(pre_feature_attempts, env, decided, monkeypatch, wait):
    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], mode="step", wait=wait)
    _approve(env, t1, body={"result_doc_id": result})
    assert (_node(env, t1)[0], _node(env, t2)[0], _propagations(env, t1)) == ("done", "pending", ["pending"])
    assert env["jobs"] == [] and _run_status(env)[0] == "idle"         # the step is over: the next one waits
    resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": "step"}, headers=_h())
    assert resumed.status_code == 200, resumed.text                     # the next step opens it
    assert (_node(env, t2)[0], [j["node_key"] for j in env["jobs"]]) == ("open", [t2])
@WAITS
@pytest.mark.parametrize("state", ["paused", "stopped", "time_machine"])
def test_a_paused_stopped_or_time_machine_run_does_not_go_on_after_a_person_s_approval(pre_feature_attempts, 
        env, decided, monkeypatch, state, wait):
    from flowgate.graph_time_machine import operation

    (t1, t2), result = _person_waits(env, [("T", LEE), ("T", AI)], wait=wait)
    if state == "paused":
        _sql(env["db"], "UPDATE graph_runs SET status = 'paused', idle_reason = NULL WHERE group_id = ?", (FA,))
    elif state == "stopped":
        _sql(env["db"], "UPDATE graph_runs SET idle_reason = 'stopped' WHERE group_id = ?", (FA,))
    else:
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm_in_progress"}])
    before = _run_status(env)
    seq = _seq(env)
    _v01_approve(result)                                                # a person's approval reaches the Graph
    assert _node(env, t1) == ("done", result) and _review_status(result) == "approved"
    assert (_node(env, t2)[0], _propagations(env, t1)) == ("pending", ["pending"])
    assert env["jobs"] == [] and _run_status(env)[0] == before[0]       # the run does not go on
    if state != "time_machine":                                          # (a TM wait's idle reason is re-judged)
        assert _run_status(env) == before
    assert ("idle", "running", "work_resumed", "approval") not in _run_events_after(env, seq)
    if state == "time_machine":
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [])  # the operation ends
    resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text                     # the next command drains what waited
    assert (_node(env, t2)[0], _propagations(env, t1)) == ("open", ["done"])
    assert [j["node_key"] for j in env["jobs"]] == [t2]
def test_the_wake_judgement_follows_wake_graph_run_s_conditions(env, monkeypatch):
    from types import SimpleNamespace

    from flowgate.graph_run import command
    from flowgate.graph_time_machine import operation

    monkeypatch.setattr(operation, "_unfinished", lambda flow: [])
    idle = {"status": "idle", "idle_reason": "waiting_approval", "active_group_id": FA, "run_mode": "full",
            "step_node_keys": None}

    def wakes(command_pending=False, **run):
        return command.approval_wakes(SimpleNamespace(run={**idle, **run}, command_pending=command_pending, flow_id=FA))

    assert wakes() is True
    assert wakes(idle_reason="no_targets") is True and wakes(idle_reason="error") is True
    assert wakes(run_mode="step", step_node_keys=["gn_a"]) is True     # the step's own bound is ``in_command``
    assert [wakes(status=s) for s in ("running", "paused", "stopping")] == [False, False, False]
    assert wakes(idle_reason="stopped") is False
    assert wakes(active_group_id=None) is False                         # not the Flow's active run
    assert wakes(command_pending=True) is False                         # an explicit command drains it itself
    assert wakes(run_mode="step", step_node_keys=None) is False
    monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm"}])
    assert wakes() is False
