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

def test_defers_keeps_every_wait_but_the_wakeable_idle_run_revised_0061_t2():
    from modules.flow_gate.graph_workflow.domain.frontier import Decision, NodeRun, RunState, defers, virtual

    def state(status, wakes=False):
        return RunState(status, {"a": NodeRun("a", "review", "d")}, approval_wakes=wakes)

    assert defers(state("running")) is False
    assert defers(state("idle", wakes=True)) is False                   # revised
    assert defers(state("idle")) is True                                # stopped / Time Machine / a command
    assert defers(state("paused")) is True and defers(state("paused", wakes=True)) is True
    assert virtual(state("idle", wakes=True), Decision()).approval_wakes is True
def test_r_ai_cards_still_approve_by_themselves_with_no_person_s_approval(env, decided, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service

    # T-3: the card approval is removed; no v0.1 approval (a person's) is taken either
    approvals = []
    real = pipeline_service.transition_document_review
    monkeypatch.setattr(pipeline_service, "transition_document_review",
                        lambda **kw: (approvals.append(kw) if kw.get("action") == "approve" else None) or real(**kw))
    commands = _no_command(monkeypatch)
    _admin_and_reviewer(env)
    t1, t2 = _cards(env, [("T", AI), ("T", AI)], {0: (("instruction", "omit"),)})   # t1 writes its result at once
    started = env["client"].post(f"/api/v1/flows/{FA}/runs", json={"revision_no": 1}, headers=_h())
    assert started.status_code == 201, started.text
    [job] = env["jobs"]
    _heads, _call, [issued] = _start_ai_run(monkeypatch, job, run_id="air_t1")
    result = _result(81, "TR")
    _register(issued["token_id"], result, "TR")
    _executions_ended()
    _run_ended("air_t1")
    assert (_node(env, t1), _review_status(result)) == (("done", result), "approved")
    assert _node(env, t2)[0] == "open" and [j["node_key"] for j in env["jobs"]] == [t1, t2]
    assert approvals == [] and commands == []                           # no person, no resume
    assert _run_status(env)[0] == "running"
@WAITS
@pytest.mark.parametrize("approve", ["card", "v01"])
def test_a_person_s_approval_held_for_its_merge_opens_the_next_ai_card_once_merged_revised_0061_t2(pre_feature_attempts, 
        env, decided, monkeypatch, group_tree, wait, approve):
    commands = _no_command(monkeypatch)
    (t1, t2), result, attempt_id, commit = _person_waits_on_code(env, group_tree, [("T", LEE), ("T", AI)], wait=wait)
    seq = _seq(env)
    if approve == "card":
        _approve(env, t1, body={"result_doc_id": result})
    else:
        _v01_approve(result)
    assert group_tree.merges == [("review", "pending", "running")]     # nothing opened before the merge; run woken
    assert _merged(group_tree, commit)
    status, integration, merged_as = _integration(attempt_id)
    assert (status, integration) == ("ended", "integrated") and merged_as
    assert group_tree.cleaned == [attempt_id]
    assert (_node(env, t1), _node(env, t2)[0]) == (("done", result), "open")   # once merged, with no resume
    assert _review_status(result) == "approved" and _propagations(env, t1) == ["done"]
    assert [j["node_key"] for j in env["jobs"]] == [t2]                 # the AI card's job is created
    assert _run_status(env) == ("running", None)
    events = _run_events_after(env, seq)
    assert events.count(("idle", "running", "work_resumed", "approval")) == 1
    assert not [e for e in events if e[2] in HOLD]                      # no idle run's integration hold
    assert ("running", "idle", "run_idle", None) not in events
    assert commands == []                                              # no resume, step or full
@WAITS
def test_the_last_card_s_approval_held_for_its_merge_completes_the_run_once_merged_revised_0061_t2(pre_feature_attempts, 
        env, decided, monkeypatch, group_tree, wait):
    commands = _no_command(monkeypatch)
    (t1,), result, attempt_id, commit = _person_waits_on_code(env, group_tree, [("T", LEE)], wait=wait)
    _approve(env, t1, body={"result_doc_id": result})
    assert _merged(group_tree, commit) and _integration(attempt_id)[1] == "integrated"
    assert _node(env, t1)[0] == "done" and _propagations(env, t1) == ["done"]
    assert _run_status(env) == ("idle", "completed") and commands == []
@WAITS
@pytest.mark.parametrize("state", ["step", "paused", "stopped", "time_machine"])
def test_a_step_paused_stopped_or_time_machine_run_still_waits_after_an_approval_held_for_its_merge(pre_feature_attempts, 
        env, decided, monkeypatch, group_tree, state, wait):
    from flowgate.graph_time_machine import operation

    mode = "step" if state == "step" else None
    (t1, t2), result, attempt_id, commit = _person_waits_on_code(env, group_tree, [("T", LEE), ("T", AI)],
                                                                  mode=mode, wait=wait)
    if state == "paused":
        _sql(env["db"], "UPDATE graph_runs SET status = 'paused', idle_reason = NULL WHERE group_id = ?", (FA,))
    elif state == "stopped":
        _sql(env["db"], "UPDATE graph_runs SET idle_reason = 'stopped' WHERE group_id = ?", (FA,))
    elif state == "time_machine":
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [{"operation_id": "tm_in_progress"}])
    seq = _seq(env)
    _v01_approve(result)
    assert _review_status(result) == "approved"
    assert _node(env, t2)[0] == "pending" and env["jobs"] == []         # the next card waits
    assert _propagations(env, t1) in ([], ["pending"])                  # (none yet while the merge waits)
    if state != "step":         # (a step's proven command is woken by ``resume_approval``, still bounded to its targets)
        assert ("idle", "running", "work_resumed", "approval") not in _run_events_after(env, seq)
    if state == "time_machine":
        assert group_tree.merges == [] and _node(env, t1)[0] == "review"   # no integration under the operation
        monkeypatch.setattr(operation, "_unfinished", lambda flow: [])
    else:
        assert _merged(group_tree, commit) and _node(env, t1)[0] == "done"
    resumed = env["client"].post(f"/api/v1/runs/{_run_id(env)}/resume", json={"mode": mode or "full"}, headers=_h())
    assert resumed.status_code == 200, resumed.text                     # the next command goes on
    assert _merged(group_tree, commit) and (_node(env, t1)[0], _node(env, t2)[0]) == ("done", "open")
    assert _propagations(env, t1) == ["done"] and [j["node_key"] for j in env["jobs"]] == [t2]
