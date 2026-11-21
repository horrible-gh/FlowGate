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

@pytest.mark.parametrize("row", sorted(ROWS))
def test_an_ai_s_card_goes_by_its_documents(env, decided, starts, monkeypatch, row):
    """Instruction and execution AI X: an unwritten stage is X's work (one request), a written one asks nothing."""
    key, docs = _row_card(env, row, ai=True)
    start(env)
    jobs = [(j["stage"], j["provider_id"]) for j in write_jobs(env)]
    expected = {"1_omit_none": [("result", X)], "2_omit_report": [], "3_none_none": [("instruction", X)],
                "4_ins_none": [("result", X)], "5_ins_report": [], "6_none_report": [("instruction", X)]}[row]
    assert jobs == expected
    assert loops(starts) == []
    if not expected:                                                     # Satisfied: no writer, reviewed (not used) → done
        assert status(env, key) == "done" and _review_status(docs["report"]) == "approved"
        ra = one(env, key, "result")
        assert (ra["actor"], ra["provider_id"], ra["stage_state"], ra["intent_kind"]) == ("ai", X, "done", None)
        assert [l for l in _sql(env["db"], "SELECT kind FROM graph_attempt_runs")] == []   # no execution request
        return
    assert status(env, key) == "open"
    if row == "4_ins_none":
        assert write_jobs(env)[0]["ref_doc_ids"] == [docs["instruction"]]
def test_an_ai_s_report_written_before_the_instruction_is_written_anew_by_its_execution_ai(env, decided, starts,
                                                                                          monkeypatch):
    """Row 6 (AI): X writes the instruction, then X writes a new report — the report of before is not taken."""
    key, docs = _row_card(env, "6_none_report", ai=True)
    start(env)
    before = doc_row(env, docs["report"])
    instruction = _ai_writes_instruction(env, monkeypatch, provider=X)
    assert _review_status(instruction) == "approved"
    [job] = write_jobs(env)
    assert (job["stage"], job["provider_id"], job["ref_doc_ids"]) == ("result", X, [instruction])
    assert status(env, key) == "open" and node_run(env, key)["result_doc_id"] is None
    assert doc_row(env, docs["report"]) == before
    result = ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    assert status(env, key) == "done" and _review_status(result) == "approved" and result != docs["report"]
def test_an_approved_report_satisfies_its_card_as_a_draft_does(env, decided, starts):
    """§3.6-0-3: a report registered or approved already is written — not refused for being no draft."""
    from modules.flow_gate.db import documents as db_documents

    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", X, "not_used")
    report = prewrite(env, key, "report", REPORT_BODY)
    db_documents.update(report, {"status": "open", "doc_review_status": "approved"})
    start(env)
    assert write_jobs(env) == [] and starts == []
    current(env)
    assert status(env, key) == "done" and node_run(env, key)["result_doc_id"] == report
def test_a_blank_report_is_no_report(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")], omit=("c",))
    key = keys["c"]
    roles(key, "not_used", X, "not_used")
    prewrite(env, key, "report", "   \n\t\n")
    start(env)
    assert [(j["stage"], j["provider_id"]) for j in write_jobs(env)] == [("result", X)]
def test_a_satisfied_card_with_a_review_ai_is_reviewed_once_and_a_rejection_goes_no_further(env, decided, starts,
                                                                                            monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")], settings=(("c1", "review_count", 2),))
    roles(keys["c1"], X, X, B)
    roles(keys["c2"], "not_used", "not_used", "not_used")
    prewrite(env, keys["c1"], "instruction", INSTRUCTION_BODY)
    report = prewrite(env, keys["c1"], "report", REPORT_BODY)
    start(env)
    assert write_jobs(env) == []                                         # no write AI: the documents are written
    rr = one(env, keys["c1"], "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["author"], rr["review_rounds"]) == ("ai", B, "person", 1)  # §3.7
    [loop] = loops(starts)
    assert (loop["document_review_loop"]["reviewer_provider_id"], loop["doc_ref"]) == (B, report)
    assert loop["document_review_loop"]["rework_provider_id"] is None
    assert status(env, keys["c1"]) == "review" and status(env, keys["c2"]) == "pending"

    db_documents.update(report, {"doc_review_status": "rejected"})
    _review_end(env, rr, "awaiting_author_revision")
    assert one(env, keys["c1"], "result_review", 1)["verdict"] == "issues"
    current(env)
    assert status(env, keys["c1"]) == "review" and status(env, keys["c2"]) == "pending"     # no further
    assert len(loops(starts)) == 1 and write_jobs(env) == []
def test_a_satisfied_card_with_a_passing_review_ai_goes_on(env, decided, starts, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    keys = graph(env, {"c1": "T", "c2": "T"}, [("r", "c1"), ("c1", "c2")], omit=("c1",))
    roles(keys["c1"], "not_used", X, B)
    roles(keys["c2"], "not_used", "not_used", "not_used")
    report = prewrite(env, keys["c1"], "report", REPORT_BODY)
    start(env)
    rr = one(env, keys["c1"], "result_review", 1)
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, rr, "review_passed")
    assert _review_status(report) == "approved" and status(env, keys["c1"]) == "done"
    assert status(env, keys["c2"]) == "open" and write_jobs(env) == []
def test_review_not_used_approves_without_a_review_whatever_its_count(env, decided, starts):
    keys = graph(env, {"c": "T"}, [("r", "c")], settings=(("c", "review_count", 2),))
    key = keys["c"]
    roles(key, "not_used", "not_used", "not_used")
    prewrite(env, key, "instruction", INSTRUCTION_BODY)
    report = prewrite(env, key, "report", REPORT_BODY)
    start(env)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["verdict"], rr["review_rounds"]) == ("no_review", "auto_approved", None)
    assert _review_status(report) == "approved" and status(env, key) == "done"
    assert loops(starts) == [] and write_jobs(env) == []
    assert _sql(env["db"], "SELECT value_json FROM graph_node_settings WHERE node_key = ? AND field = 'review_count'",
                (key,))[0]["value_json"] == "2"                         # the stored count stays
