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

@pytest.mark.parametrize("asked", [False, True])
def test_an_approved_instruction_whose_result_stage_was_lost_starts_it_once_never_while_its_questions_wait(
        legacy_cards, pre_feature_attempts, env, decided, monkeypatch, questions, asked):
    from modules.flow_gate.db import documents as db_documents

    if asked:
        questions["asked"][INSTRUCTION_RUN] = [dict(QUESTION, responder_state="failed")]
    key, instruction, _exec = _ai_instruction(env, monkeypatch, end=False)      # its run-end contact lost
    db_documents.update(instruction, {"doc_review_status": "approved"})           # approved, the stage not moved
    assert node_run(env, key)["stage"] == "instruction"

    for _ in range(3):                                           # every read: once
        _current(env)
    if asked:                                                    # its questions first (0111): nothing starts
        assert env["jobs"] == [] and node_run(env, key)["stage"] == "instruction"
        assert _current(env)["idle_reason"] == "needs_intervention"
        return
    [job] = env["jobs"]
    assert (job["stage"], job["ref_doc_ids"]) == ("result", [instruction])
    row = node_run(env, key)
    assert (row["stage"], row["instruction_doc_id"]) == ("result", instruction)
    assert len([e for e in _executions(env) if e["status"] == "requested"]) == 1
def test_an_assigned_instruction_saved_without_its_contact_is_reviewed_once_by_its_review_ai(env, decided, starts):
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = line_doc(env, key, "instruction")
    _write_written(instruction, BODY)                            # saved; its contact (CONTENT_SAVED) lost
    _submitted(instruction)
    for _ in range(3):
        run = _current(env)                                      # RC-6: judged once
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["provider_id"], ir["author"]) == ("ai", B, "person")
    loops = [c for c in starts if c.get("document_review_loop")]
    assert len(loops) == 1                                       # one request, to the assigned provider
    assert (loops[0]["document_review_loop"]["reviewer_provider_id"], loops[0]["doc_ref"]) == (B, instruction)
    assert [c for c in starts if not c.get("document_review_loop")] == []
    assert _review_status(instruction) != "approved" and run["idle_reason"] != "no_targets"
def test_not_used_roles_go_on_without_a_person_s_approval_once_the_content_is_saved(env, decided, starts):
    key = flow(env)
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = line_doc(env, key, "instruction")
    for blank in ("", "   \n\t\n"):                             # nothing written: the manual write wait stays
        _write_written(instruction, blank)
        run = _step(env)
        assert run["idle_reason"] == "needs_intervention" and node_run(env, key)["stage"] == "instruction"
        assert _wire(_current(env), key)["wait_reason"]["code"] == "manual_write_wait"
    _write_written(instruction, BODY)                            # saved, its contact lost: the read judges it
    _submitted(instruction)
    _current(env)
    assert _review_status(instruction) == "approved" and node_run(env, key)["stage"] == "result"
    assert one(env, key, "instruction_review", 1)["verdict"] == "auto_approved"
    assert _wire(_current(env), key)["wait_reason"]["code"] == "manual_write_wait"     # the report now
    report = write_line(env, key, "report", "## 결과\n\n사람이 쓴 레포트\n")["doc_id"]
    assert _review_status(report) == "approved" and node_run(env, key)["status"] == "done"
    assert one(env, key, "result_review", 1)["verdict"] == "auto_approved"
    assert starts == [] and env["jobs"] == []                    # no AI at all
    before = assignments(env, key)
    save_instruction(env, key)                                   # the same contact again, then reads: nothing more
    _current(env)
    assert assignments(env, key) == before
