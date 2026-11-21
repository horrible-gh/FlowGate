"""Independently selectable scenarios; shared fixtures remain in test_fg_next_step_wait_0113.py."""
from __future__ import annotations

from test_fg_next_step_wait_0113 import (
    A,
    AI,
    B,
    BODY,
    FA,
    INSTRUCTION_RUN,
    NS,
    QUESTION,
    REVIEW_MODEL,
    REVIEW_RUN,
    ROOT,
    SAVED_REPORT,
    X,
    _admin_and_reviewer,
    _again,
    _ai_instruction,
    _ai_run_finished,
    _current,
    _ended,
    _executions,
    _h,
    _lost_instruction,
    _lost_result,
    _review_status,
    _run_one_card,
    _run_row,
    _sql,
    _start,
    _start_ai,
    _step,
    _submitted,
    _wire,
    _write_written,
    assignments,
    b1,
    db_path,
    decided,
    env,
    events,
    flow,
    launches,
    legacy_cards,
    legacy_setting,
    line_doc,
    node_run,
    one,
    pre_feature_attempts,
    pytest,
    questions,
    roles,
    save_instruction,
    start,
    starts,
    stored,
    threading,
    time,
    tokens,
    traceback,
    wired,
    write_line,
)

def test_an_unreadable_instruction_review_setting_waits_with_its_cause_and_approves_nothing(env, decided, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    stored(key, "instruction_review_count", "two")               # a value that cannot be told
    start(env)
    instruction = save_instruction(env, key)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["actor_reason"]) == ("wait_setup", "setting_unreadable")
    # flowgate.v02.0119 T#3 (T-2 rework): its ``review_result_unknown`` is a system error — the error family (never
    # "nothing to run", never a person's wait), the setting read again by the reads up to the limit
    assert _current(env)["idle_reason"] == "error"
    wire = _wire(_current(env), key)
    wait = wire["wait_reason"]
    assert (wait["code"], wait["role"]) == ("ai_setup_required", "review")
    assert wire["intervention"] == {"kind": "system_error", "reason": "review_result_unknown"}

    # [다음 단계]: 0126 T#5 — the explicit command resolves the waiting instruction review again as the NEXT review seq
    # of the same attempt (the old seq 1 stays as history, the attempt is not invalidated); the written instruction is
    # kept, the same setting keeps that review waiting — and once that post-commit step is over the run is judged again
    # (it stayed ``no_targets`` before).
    run = _step(env)
    [active] = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'",
                    (key,))
    assert active["attempt_id"] == ir["attempt_id"]
    agains = [a for a in assignments(env, key)
              if a["attempt_id"] == active["attempt_id"] and a["stage"] == "instruction_review"]
    again = max(agains, key=lambda a: a["seq"])
    assert all((a["actor"], a["actor_reason"], a["basis_doc_id"]) == ("wait_setup", "setting_unreadable", instruction)
               for a in agains)
    # the command's answer itself (the client puts it in its run state at once): the wait of the new attempt, its
    # review assignment and its cause — never the ``no_targets`` it told before its post-commit step had run; its
    # ``review_result_unknown`` is the error family (0119 T-2 rework)
    assert (run["status"], run["idle_reason"]) == ("idle", "error")
    wire = _wire(run, key)
    assert wire["attempt_id"] == active["attempt_id"]
    shown = max((x for x in wire["stage_assignments"] if x["stage"] == "instruction_review"), key=lambda x: x["seq"])
    assert (shown["actor"], shown["actor_reason"]) == ("wait_setup", "setting_unreadable")
    assert (wire["wait_reason"]["code"], wire["wait_reason"]["role"]) == ("ai_setup_required", "review")
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "error")
    assert _review_status(instruction) != "approved" and starts == [] and env["jobs"] == []
def test_a_card_waiting_for_its_ai_setup_is_no_no_targets(env, decided, starts):
    """pending_init / wait_setup (T#1's first setup): the account has no role defaults and AIs are selectable — the card
    waits for its setup (the client's [다음 단계] asks for it first, ``beforeRunPrepare``); the run never says
    "nothing to run", and nothing is written to the account."""
    key = flow(env)
    roles(key, init="pending_init")
    run = _step(env)
    ia = one(env, key, "instruction")
    assert ia["actor"] == "wait_setup"
    assert run["idle_reason"] == "needs_intervention"
    assert _wire(_current(env), key)["wait_reason"]["code"] == "ai_setup_required"
    assert starts == [] and env["jobs"] == []
    assert _sql(env["db"], "SELECT * FROM user_ai_role_defaults") == []
def test_a_content_save_judges_the_person_s_instruction_once_its_flow_guard_is_left(env, decided, starts, monkeypatch):
    """The content PATCH saves under the Flow guard and its contact (CONTENT_SAVED) takes that guard to judge the person's
    instruction stage. Run from the save's commit callbacks — the guard's lock still held — it waited the lock out
    (``FlowLockTimeout``, found on flowgate.test 0090) and judged nothing until a run read; now it runs once the guard is
    left: a blank save keeps the wait, a written one is approved and its result stage starts at the save."""
    import time

    from modules.flow_gate.documents.routers import documents
    from modules.flow_gate.graph_workflow.services import flow_guard

    monkeypatch.setattr(flow_guard, "FLOW_LOCK_WAIT_SEC", 2)            # a contact inside the guard fails fast
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = line_doc(env, key, "instruction")
    user = {"user_id": "u_kim", "is_admin": True}

    documents.update_document_content(instruction, documents.DocumentContentUpdate(
        content="---\ntitle: 지시서\n---\n   \n  "), current_user=user)
    assert node_run(env, key)["stage"] == "instruction"
    assert _wire(_current(env), key)["wait_reason"]["code"] == "manual_write_wait"

    began = time.monotonic()
    documents.update_document_content(instruction, documents.DocumentContentUpdate(
        content="---\ntitle: 지시서\n---\n## 작업\n\n화면에서 저장한 지시서\n"), current_user=user)
    assert time.monotonic() - began < 2                                  # no lock wait
    assert _review_status(instruction) == "approved" and node_run(env, key)["stage"] == "result"   # at the save
    assert one(env, key, "instruction_review", 1)["verdict"] == "auto_approved"
    assert starts == [] and env["jobs"] == []
