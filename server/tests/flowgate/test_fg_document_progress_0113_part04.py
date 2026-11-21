"""Independently selectable scenarios; shared fixtures remain in test_fg_document_progress_0113.py."""
from __future__ import annotations

from test_fg_document_progress_0113 import (
    A,
    B,
    EDITED_REPORT,
    FA,
    INSTRUCTION_BODY,
    NEW_REPORT,
    REPORT_BODY,
    ROWS,
    WORK_RUN,
    X,
    Y,
    _BrokenBaselineStore,
    _active_attempt,
    _ai_writes_instruction,
    _approved_report_card,
    _break_baseline,
    _events,
    _h,
    _review_end,
    _review_status,
    _row6_person_card,
    _row_card,
    _run_ended,
    _saved,
    _sql,
    _token_state,
    _waits_for_its_report,
    _write_wait,
    _write_written,
    _written,
    ai_writes_result,
    all_roles,
    assignments,
    baseline_rows,
    command,
    current,
    db_path,
    decided,
    doc_row,
    edge,
    env,
    fork_join,
    graph,
    line_doc,
    loops,
    node,
    node_run,
    one,
    prewrite,
    put_draft,
    pytest,
    record_starts,
    roles,
    run_row,
    save_instruction,
    start,
    starts,
    status,
    stored,
    taken_judged_events,
    to_role,
    tokens,
    wire,
    wired,
    write_jobs,
    write_line,
)

def test_a_manual_instruction_wait_whose_instruction_ai_changed_is_the_ai_s_at_the_next_full(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    assert one(env, key, "instruction")["actor"] == "person"
    to_role(key, "execution", Y)                                         # another role than the waiting stage's
    command(env, "full")
    assert write_jobs(env) == [] and one(env, key, "instruction")["actor"] == "person"
    to_role(key, "instruction", X)
    command(env, "full")
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"]) == ("instruction", X)
def test_a_person_stage_holding_its_document_keeps_its_assignment(env, decided, starts):
    """A person's stage whose document is registered keeps its assignment until that document is judged: the report
    bound and rejected by its review (the person's revision awaited), the execution role changed — the command
    reinterprets nothing and asks no AI."""
    from modules.flow_gate.db import documents as db_documents

    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", "not_used", B)
    start(env)
    first = one(env, key, "result")
    report = write_line(env, key, "report", NEW_REPORT)["doc_id"]       # registered: the review takes it
    rr = one(env, key, "result_review", 1)
    db_documents.update(report, {"doc_review_status": "rejected"})
    _review_end(env, rr, "awaiting_author_revision")
    assert current(env)["status"] == "idle"
    to_role(key, "execution", X)
    command(env, "step")
    ra = one(env, key, "result")
    assert (ra["attempt_id"], ra["actor"], ra["stage_state"]) == (first["attempt_id"], "person", "done")
    assert write_jobs(env) == []
    assert status(env, key) == "review" and node_run(env, key)["result_doc_id"] == report
    assert [a["attempt_id"] for a in assignments(env, key)] == [first["attempt_id"]] * len(assignments(env, key))
def test_a_result_wait_reinterpreted_keeps_the_instruction_an_ai_wrote_and_the_report_baseline(env, decided, starts,
                                                                                              monkeypatch):
    keys = graph(env, {"c": "T"}, [("r", "c")])
    key = keys["c"]
    roles(key, X, "not_used", "not_used")
    report = prewrite(env, key, "report", REPORT_BODY)                   # row 6: the report of before
    start(env)
    instruction = _ai_writes_instruction(env, monkeypatch, provider=X)
    assert _review_status(instruction) == "approved"
    first = one(env, key, "result")
    assert first["actor"] == "person" and write_jobs(env) == []
    assert current(env)["status"] == "idle"                              # the manual write wait

    to_role(key, "execution", Y)
    command(env, "step")
    jobs = [(j["stage"], j["provider_id"]) for j in write_jobs(env)]
    assert jobs == [("result", Y)]                                       # no instruction AI again, the report not taken
    active = _sql(env["db"], "SELECT attempt_id FROM graph_node_attempts WHERE node_key = ? AND status = 'active'",
                  (key,))[0]["attempt_id"]
    ia = [a for a in assignments(env, key) if a["attempt_id"] == active and a["stage"] == "instruction"][0]
    assert ia["reused_doc_id"] == instruction
    rows = baseline_rows(env, key)
    assert [(r["doc_id"], r["carried_from"]) for r in rows] == [(report, None), (report, first["attempt_id"])]
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
@pytest.mark.parametrize("execution", ["not_used", X])
def test_an_approved_report_taken_by_a_new_attempt_is_reviewed_by_its_review_ai_and_goes_on_only_once_passed(
        env, decided, starts, monkeypatch, execution):
    """A-3 / A-4: the old approval is no review of this attempt — the review AI B reviews it (one loop, no write AI),
    run reads never complete the node on the old approval, and only B's pass opens the next card. Both the person's
    result stage (execution 'not used') and the AI's (execution X) take it."""
    from flowgate.card_settings import legacy_adapter

    keys, report = _approved_report_card(env, execution, B)
    start(env)
    assert write_jobs(env) == []                                         # written: no write AI
    rr = one(env, keys["c1"], "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["basis_doc_id"], rr["verdict"]) == ("ai", B, report, None)
    [loop] = loops(starts)
    assert (loop["document_review_loop"]["reviewer_provider_id"], loop["doc_ref"]) == (B, report)
    assert [p["doc_id"] for p in taken_judged_events(env, keys["c1"])] == [report]
    for _ in range(2):                                                   # run reads: the old approval completes nothing
        current(env)
        assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("review", "pending")
    assert len(loops(starts)) == 1 and one(env, keys["c1"], "result_review", 2) is None   # no second review

    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, rr, "review_passed")
    assert one(env, keys["c1"], "result_review", 1)["verdict"] == "passed"
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("done", "open")
    assert write_jobs(env) == [] and len(loops(starts)) == 1
def test_an_approved_report_taken_whose_review_does_not_pass_goes_no_further(env, decided, starts):
    keys, report = _approved_report_card(env, "not_used", B)
    start(env)
    rr = one(env, keys["c1"], "result_review", 1)
    _review_end(env, rr, "awaiting_author_revision")
    assert one(env, keys["c1"], "result_review", 1)["verdict"] == "issues"
    current(env)
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("review", "pending")
    assert write_jobs(env) == [] and len(loops(starts)) == 1
@pytest.mark.parametrize("wait", ["wait_setup", "wait_unavailable"])
@pytest.mark.parametrize("execution", ["not_used", X])
def test_an_approved_report_taken_under_a_review_wait_waits_and_never_completes(env, decided, starts, wait, execution):
    """A-4: the review assignment is made and waits (setup needed / AI unavailable): no review, no automatic approval,
    the node stays in review and the next card is not opened, read after read."""
    review = "unset" if wait == "wait_setup" else B
    keys, report = _approved_report_card(env, execution, review)
    if wait == "wait_unavailable":
        _sql(env["db"], "UPDATE ai_providers SET enabled = 0 WHERE provider_id = ?", (B,))
    start(env)
    rr = one(env, keys["c1"], "result_review", 1)
    assert (rr["actor"], rr["basis_doc_id"], rr["verdict"]) == (wait, report, None)
    for _ in range(2):
        run = current(env)
        assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("review", "pending")
    assert run["idle_reason"] != "completed"
    assert write_jobs(env) == [] and starts == []
def test_an_approved_report_taken_with_review_not_used_is_approved_by_this_attempt_then_goes_on(env, decided, starts):
    """A-4: review 'not used' (count 2): this attempt's automatic approval — recorded on its own review assignment —
    is what moves the node, not the report's old approval."""
    keys, report = _approved_report_card(env, "not_used", "not_used", count=2)
    start(env)
    current(env)
    rr = one(env, keys["c1"], "result_review", 1)
    assert (rr["actor"], rr["verdict"], rr["basis_doc_id"]) == ("no_review", "auto_approved", report)
    assert (status(env, keys["c1"]), status(env, keys["c2"])) == ("done", "open")
    assert write_jobs(env) == [] and starts == []
