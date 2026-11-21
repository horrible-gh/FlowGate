"""Independently selectable scenarios; shared fixtures remain in test_fg_ai_roles_l2_fixes.py."""
from __future__ import annotations

from test_fg_ai_roles_l2_fixes import (
    A,
    B,
    FA,
    LIMIT,
    REVIEW_RUN,
    STAGE_ASSIGNMENTS,
    WORK_RUN,
    X,
    Y,
    _ai_token,
    _attempts,
    _break_reads,
    _console_save,
    _doc,
    _edit_between,
    _evidence,
    _failing_issue,
    _loop_ended,
    _person_card,
    _read,
    _refusing_loops,
    _review_end,
    _review_status,
    _run_ended,
    _run_row,
    _set_revision,
    _sql,
    _unreadable,
    _wire,
    _write_written,
    ai_writes_result,
    assignments,
    b1,
    command,
    db_path,
    decided,
    env,
    events,
    flow,
    json,
    line_doc,
    node_run,
    one,
    pytest,
    record_starts,
    roles,
    save_instruction,
    start,
    starts,
    stored,
    threading,
    time,
    tokens,
    wired,
    write_line,
)

def test_t46_an_instruction_contact_with_an_unreadable_assignment_records_it_once_and_starts_nothing(env, decided,
                                                                                                   monkeypatch):
    key = _person_card(env)
    attempt = one(env, key, "instruction")["attempt_id"]
    repair = _break_reads(monkeypatch, "read")
    instruction = save_instruction(env, key)
    save_instruction(env, key)                                       # the same submission's contact again
    assert _unreadable(env) == [(instruction, "instruction", attempt,
                                 {"doc_id": instruction, "revision_no": 0, "rejection_count": 0})]
    _read(env)                                                       # the reconciliation only skips the node
    assert len(_unreadable(env)) == 1
    repair()
    _read(env)                       # readable again: that submission has its B-1 (L-2 §2.7.2 C5: the card's [승인])
    assert one(env, key, "instruction_review", 1) is None and _review_status(instruction) != "approved"
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (instruction,))
    save_instruction(env, key, "## 작업\n\n고쳐 낸 지시서\n")       # a new submission is judged
    assert _review_status(instruction) == "approved" and len(_unreadable(env)) == 1
def test_t46_a_result_contact_with_an_unreadable_assignment_records_it_once_and_approves_nothing(env, decided,
                                                                                               monkeypatch):
    from flowgate.card_settings import manual_stage

    key = _person_card(env, instruction="omit")
    real = manual_stage.person_result
    monkeypatch.setattr(manual_stage, "person_result", lambda *a, **k: None)     # the bound result's contact lost
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    monkeypatch.setattr(manual_stage, "person_result", real)
    run_id, attempt = _run_row(env)["run_id"], one(env, key, "result")["attempt_id"]
    repair = _break_reads(monkeypatch, "read", "latest")
    manual_stage.follow_person_result(FA, run_id, key, report, attempt)
    manual_stage.follow_person_result(FA, run_id, key, report, attempt)
    assert [(d, s, a) for d, s, a, _m in _unreadable(env)] == [(report, "result", attempt)]
    _read(env)
    assert len(_unreadable(env)) == 1 and _review_status(report) != "approved"
    repair()
    _read(env)                       # readable again: that submission has its B-1 (the card's [승인] takes it)
    assert _review_status(report) != "approved" and [r for _d, r, _m in b1(env)] == ["assignment_unreadable"]
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (report,))
    _read(env)                                                       # RC-3: a new submission is judged
    assert _review_status(report) == "approved" and node_run(env, key)["status"] == "done"
def test_t46_a_loop_end_contact_with_an_unreadable_assignment_records_it_once_and_approves_nothing(env, decided,
                                                                                                 monkeypatch, starts):
    from flowgate.card_settings import assigned_review, legacy_adapter

    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1)))
    roles(key, execution="not_used", review=B)
    start(env)
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    r1 = one(env, key, "result_review", 1)
    _loop_ended(env, r1, "review_passed")
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    run, ref = {"run_id": REVIEW_RUN}, (r1["attempt_id"], "result_review", 1)
    repair = _break_reads(monkeypatch, "read")
    assigned_review.after_loop_end(FA, run, ref, False)
    assigned_review.after_loop_end(FA, run, ref, False)
    assert [(d, s) for d, s, _a, _m in _unreadable(env)] == [(report, "result")]
    assert _review_status(report) != "approved"
    repair()
    assert one(env, key, "result_review", 1)["verdict"] is None
    _read(env)                                                       # RC-1 once readable: consumed once
    assert one(env, key, "result_review", 1)["verdict"] == "passed" and _review_status(report) == "approved"
    assert len(_unreadable(env)) == 1
@pytest.mark.parametrize("stage", ["instruction", "result", "instruction_review"])
def test_t46_an_open_node_whose_assignment_cannot_be_read_is_an_error_and_the_read_goes_on(env, decided, monkeypatch,
                                                                                          stage):
    from flowgate.card_settings import legacy_adapter

    if stage == "instruction":
        key = _person_card(env)
    elif stage == "result":
        key = _person_card(env, instruction="omit")
    else:
        key = flow(env, extra_settings=(("instruction_review_count", 1),))
        roles(key, "not_used", "not_used")                          # no reviewer of any kind: it waits for setup
        start(env)
        save_instruction(env, key)
        assert one(env, key, "instruction_review", 1)["actor"] == "wait_setup"
    legacy_adapter.settle_graph_run(FA)
    assert _run_row(env)["idle_reason"] == "needs_intervention"
    repair = _break_reads(monkeypatch)
    legacy_adapter.settle_graph_run(FA)
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "error")      # never no_targets
    wire = _wire(env, key)
    assert (wire["wait_reason"], wire["stage_assignments"]) == (None, None)
    repair()
    legacy_adapter.settle_graph_run(FA)
    assert _run_row(env)["idle_reason"] == "needs_intervention"
def test_e13_a_refused_result_review_loop_fails_with_its_own_code_and_no_other_ai(env, decided, monkeypatch):
    """(T-2 rework): the refused start is a system error. Each run read tries the review again with
    the same AI (a new review assignment, the next seq) until ``SYSTEM_ERROR_RETRY_LIMIT`` tries, then no more; its
    B-1 is recorded once (the tries are the assignments), no other AI is asked and nothing is approved."""
    from flowgate.card_intervention import reasons

    assert reasons.SYSTEM_ERROR_RETRY_LIMIT == LIMIT
    calls: list = []
    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1)))
    roles(key, execution="not_used", review=B)
    start(env)
    _refusing_loops(monkeypatch, calls)
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    rr = one(env, key, "result_review", 1)
    assert (rr["stage_state"], rr["fail_cause"]) == ("failed", "start_refused:no_enabled_provider")
    loops = [c for c in calls if c.get("document_review_loop")]
    assert len(loops) == 1 and loops[0]["document_review_loop"]["reviewer_provider_id"] == B
    assert [(d, r) for d, r, _m in b1(env)] == [(report, "review_not_started")]
    for _ in range(LIMIT + 2):
        _read(env)                                                   # tried again up to the limit, then never
    loops = [c for c in calls if c.get("document_review_loop")]
    assert len(loops) == LIMIT and {c["document_review_loop"]["reviewer_provider_id"] for c in loops} == {B}
    reviews = [a for a in assignments(env, key) if a["stage"] == "result_review"]
    assert [(a["seq"], a["actor"], a["stage_state"], a["fail_cause"], a["basis_doc_id"]) for a in reviews] == \
        [(n, "ai", "failed", "start_refused:no_enabled_provider", report) for n in range(1, LIMIT + 1)]
    assert len(b1(env)) == 1 and _review_status(report) != "approved"
    assert _wire(env, key)["intervention"] == {"kind": "system_error", "reason": "review_not_started"}
    assert _read(env)["idle_reason"] == "error"                      # the error family, never a person's wait
def test_e13_a_result_review_whose_start_failed_for_a_moment_starts_on_the_next_read_and_completes(
        env, decided, monkeypatch):
    """(T-2 rework): once the admission takes it again, the next run read starts the review (the
    next assignment, the same AI and submission), and its pass completes the card automatically."""
    from flowgate.card_settings import legacy_adapter

    calls: list = []
    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1)))
    roles(key, execution="not_used", review=B)
    start(env)
    _refusing_loops(monkeypatch, calls)
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    assert one(env, key, "result_review", 1)["stage_state"] == "failed"
    record_starts(monkeypatch, calls)                                # the admission takes it now
    _read(env)
    rr = one(env, key, "result_review", 2)
    assert (rr["actor"], rr["provider_id"], rr["stage_state"], rr["intent_kind"], rr["basis_doc_id"]) == \
        ("ai", B, "active", "execution", report)
    assert len([c for c in calls if c.get("document_review_loop")]) == 2
    _read(env)
    assert one(env, key, "result_review", 3) is None                 # a review working: nothing tried again
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, rr, "review_passed")
    assert _review_status(report) == "approved" and node_run(env, key)["status"] == "done"
    assert one(env, key, "result_review", 2)["verdict"] == "passed"
def test_e13_a_refused_instruction_review_loop_records_its_code_on_the_node(env, decided, monkeypatch):
    calls: list = []
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    _refusing_loops(monkeypatch, calls, "provider_inactive")
    instruction = save_instruction(env, key)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["stage_state"], ir["fail_cause"]) == ("failed", "start_refused:provider_inactive")
    assert node_run(env, key)["dispatch_error_code"] == "review_not_started:start_refused:provider_inactive"
    assert [(d, r) for d, r, _m in b1(env)] == [(instruction, "review_not_started")]
    assert _wire(env, key)["wait_reason"]["detail"] == "start_refused:provider_inactive"
    # (T-2 rework): a system error of the instruction stage — shown so (the node run's
    # intervention, the run's error family) and tried again by the reads, with the same AI, up to the limit
    for _ in range(LIMIT + 1):
        wire = _wire(env, key)
    assert len([c for c in calls if c.get("document_review_loop")]) == LIMIT
    reviews = [a for a in assignments(env, key) if a["stage"] == "instruction_review"]
    assert [(a["seq"], a["stage_state"]) for a in reviews] == [(n, "failed") for n in range(1, LIMIT + 1)]
    assert wire["intervention"] == {"kind": "system_error", "reason": "review_not_started"}
    assert node_run(env, key)["dispatch_error_code"] == "review_not_started:start_refused:provider_inactive"
    assert _read(env)["idle_reason"] == "error"
    assert [(d, r) for d, r, _m in b1(env)] == [(instruction, "review_not_started")]
    assert _review_status(instruction) != "approved" and one(env, key, "result") is None
def test_e13_an_instruction_review_whose_start_failed_for_a_moment_starts_on_the_next_read(env, decided, monkeypatch):
    """(T-2 rework): the retry that starts the instruction review clears the node's start error
    (no error family any more) and its pass approves the instruction and starts the result stage."""
    from flowgate.card_settings import legacy_adapter

    calls: list = []
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    _refusing_loops(monkeypatch, calls, "provider_inactive")
    instruction = save_instruction(env, key)
    assert node_run(env, key)["dispatch_error_code"] == "review_not_started:start_refused:provider_inactive"
    assert _wire(env, key)["intervention"] == {"kind": "system_error", "reason": "review_not_started"}   # one retry
    assert one(env, key, "instruction_review", 2)["stage_state"] == "failed"                            # refused too
    record_starts(monkeypatch, calls)                                # the admission takes it now
    _read(env)
    ir = one(env, key, "instruction_review", 3)
    assert (ir["actor"], ir["provider_id"], ir["stage_state"], ir["intent_kind"]) == ("ai", B, "active", "execution")
    assert node_run(env, key)["dispatch_error_code"] is None         # the start error is over
    assert _wire(env, key)["intervention"] is None                   # its review works on it
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, ir, "review_passed")
    assert _review_status(instruction) == "approved"
    # the result stage's start: this harness ends the loop's execution before its run-end contact, so the run is idle
    # by then and the run-end step (AUTO) is refused; the next read takes it (RC-4, as test_k4_rc4_*)
    _read(env)
    assert node_run(env, key)["stage"] == "result" and node_run(env, key)["dispatch_error_code"] is None
@pytest.mark.parametrize("stop_reason", ["review_count_exhausted", "review_verdict_hold", "retry_exhausted"])
def test_rc1_a_lost_end_of_a_review_that_did_not_pass_records_its_b1_once(env, decided, monkeypatch, starts,
                                                                          stop_reason):
    """The AI result's review loop stopped without passing and its run-end contact was lost: the next reads consume the
    verdict once — B-1 ``review_not_passed:<stop>`` with the submission's mark, verdict ``not_passed:<stop>``, nothing
    approved (an AI author: no takeover)."""
    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", 1), ("after_review_exhausted", "stop")))
    roles(key, execution=X, review=B)
    start(env)
    [job] = env["jobs"]
    result = ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    r1 = one(env, key, "result_review", 1)
    _loop_ended(env, r1, "review_passed")                            # its run-end contact is lost
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET stop_reason = ?, stop_detail = 'spent' WHERE run_id = ?",
         (stop_reason, REVIEW_RUN))
    _read(env)
    _read(env)
    r1 = one(env, key, "result_review", 1)
    assert (r1["verdict"], r1["stage_state"]) == ("not_passed:" + stop_reason, "done")
    assert b1(env) == [(result, "review_not_passed:" + stop_reason,
                        {"doc_id": result, "revision_no": 0, "rejection_count": 0})]
    assert _review_status(result) != "approved" and node_run(env, key)["status"] == "review"
