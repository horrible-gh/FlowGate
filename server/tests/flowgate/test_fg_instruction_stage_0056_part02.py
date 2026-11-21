"""Independently selectable scenarios; shared fixtures remain in test_fg_instruction_stage_0056.py."""
from __future__ import annotations

from test_fg_instruction_stage_0056 import (
    AI,
    FA,
    INSTRUCTION_RUN,
    Path,
    ROOT,
    WORK_RUN,
    _SERVER_DIR,
    _admin_and_reviewer,
    _before_migration_148,
    _blank,
    _card,
    _count,
    _current,
    _doc,
    _events,
    _h,
    _line,
    _new_instruction_start,
    _node,
    _node_run,
    _one_job,
    _record_start,
    _recorded_start_run,
    _register,
    _register_as_the_inbox,
    _result,
    _resume,
    _review_status,
    _row,
    _run_ended,
    _run_id,
    _run_one_card,
    _source_scopes,
    _sql,
    _start,
    _start_ai,
    _stop,
    _through_instruction,
    _token,
    _token_stage,
    _work_run_ended,
    _write_written,
    card_outputs_port,
    db_path,
    decided,
    edge,
    env,
    get_settings,
    json,
    legacy_cards,
    legacy_setting,
    node,
    pre_feature_attempts,
    put_draft,
    pytest,
    pytestmark,
    setting,
    sqlite3,
)

@pytest.mark.parametrize("contact", [True, False])
def test_with_no_instruction_document_the_instruction_ai_writes_a_new_one(env, decided, monkeypatch, contact):
    """No document on the instruction line: the instruction AI writes the instruction type as a new document
    (registered with its token); it takes the line, binds nothing and moves no node — also when the
    registration's contact was lost (found by the node's tokens when the run ends)."""
    from modules.flow_gate.db import documents as db_documents

    _admin_and_reviewer(env)
    key = _card(env, "T")
    old = _line(env, key, "instruction")
    db_documents.update(old, {"status": "cancelled"})                    # the line keeps no live document
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["action_scope"], job["target_doc_id"], job["ref_doc_ids"]) == (
        "instruction", "new", None, None)
    mention, start, issue = _record_start(monkeypatch, job, INSTRUCTION_RUN)
    assert (start["action_scope"], start["doc_ref"], mention["head_type"]) == ("new", ROOT, "T")
    stray = _doc(FA, 70, "TR")
    _register(env, stray, "TR", issue["token_id"])                       # a report in the instruction stage
    assert _node(env, key) == ("open", None)                             # is no result
    written = _doc(FA, 71, "T")
    _write_written(written)
    _register(env, written, "T", issue["token_id"], contact=contact)
    row = _row(env, key)
    assert (row["status"], row["result_doc_id"], row["stage"]) == ("open", None, "instruction")
    assert row["instruction_doc_id"] == (written if contact else None)
    assert _events(env, key) == ["opened"]
    _run_ended(INSTRUCTION_RUN)
    assert _review_status(written) == "approved" and _line(env, key, "instruction") == written
    row = _row(env, key)
    assert (row["stage"], row["instruction_doc_id"]) == ("result", written)
    assert _one_job(env)["ref_doc_ids"] == [written]
def test_instruction_omitted_runs_only_the_result_stage(env, decided, monkeypatch):
    _admin_and_reviewer(env)
    key = _card(env, "T", (("instruction", "omit"), ("instruction_ai", "aip_card")))
    instruction = _line(env, key, "instruction")
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["result_doc_type"], job["ref_doc_ids"], job["provider_id"]) == (
        "result", "TR", None, "aip_assignee")
    row = _row(env, key)
    assert (row["stage"], row["instruction_doc_id"]) == ("result", None)
    _mention, _start_kw, issue = _record_start(monkeypatch, job, WORK_RUN)
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"])
    _work_run_ended()
    assert _node(env, key) == ("done", result)
    assert _review_status(instruction) is None                           # the instruction is never written
@pytest.mark.parametrize("doc_type, node_type, instruction_type, report", [
    ("N", "document", "N", "NR"), ("D", "document", "DS", "D"), ("DB", "document", "DS", "DB"),
    (None, "custom", "T", "TR"),
])
def test_each_run_card_writes_its_instruction_then_its_report(
        env, decided, monkeypatch, doc_type, node_type, instruction_type, report):
    """A design card now writes its DS before its design document, a research card its N before its NR, a
    Custom card its T before its TR."""
    _admin_and_reviewer(env)
    key = _card(env, doc_type, node_type=node_type)
    _start(env)
    job = _one_job(env)
    assert (job["stage"], job["result_doc_type"], job["action_scope"]) == ("instruction", instruction_type, "edit")
    _record_start(monkeypatch, job, INSTRUCTION_RUN)
    _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    result_job = _one_job(env)
    assert (result_job["stage"], result_job["result_doc_type"], result_job["ref_doc_ids"]) == (
        "result", report, [job["target_doc_id"]])
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    result = _result(81, report)
    _register(env, result, report, issue["token_id"])
    assert _node(env, key) == ("review", result)
    _work_run_ended()
    assert _node(env, key) == ("done", result)
    assert (_review_status(job["target_doc_id"]), _review_status(result)) == ("approved", "approved")
@pytest.mark.parametrize("ending, written", [
    ({"end_reason": "cancelled", "stop_code": "cancelled"}, True),
    ({"end_reason": "timeout", "stop_code": "timeout"}, False),
    ({}, False),                                                         # ended on its own, nothing written
])
def test_a_failed_instruction_stage_records_the_ai_failure_and_never_starts_the_result(
        env, decided, monkeypatch, ending, written):
    _admin_and_reviewer(env)
    key = _card(env, "T")
    _start(env)
    job = _one_job(env)
    _record_start(monkeypatch, job, INSTRUCTION_RUN)
    if written:
        _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"], **ending)
    assert env["jobs"] == []                                              # no result stage
    row = _row(env, key)
    assert (row["status"], row["stage"], row["dispatch_error_code"]) == ("open", "instruction", "ai_invoke_failed")
    assert _events(env, key) == ["opened", "dispatch_failed"]             # the AI start failure path
    assert _review_status(job["target_doc_id"]) is None                  # nothing approved
    assert _node_run(env, key)["dispatch"]["error_code"] == "ai_invoke_failed"
    # L0009 E-23: the node's token is a person's again; their result binds
    token = _sql(env["db"], "SELECT token_id FROM graph_run_token_map WHERE node_key = ? ORDER BY rowid",
                 (key,))[0]["token_id"]
    result = _result(82, "TR")
    _register(env, result, "TR", token)
    assert _node(env, key) == ("review", result)
@pytest.mark.parametrize("contact", [True, False])
def test_a_report_the_instruction_stage_registered_never_becomes_the_result(env, decided, monkeypatch, contact):
    """After the move to the result stage the instruction stage's report stays behind: a run read's reconciliation
    and a resume bind nothing, and the result AI's own report is the node's result — on its contact, or found by
    the reconciliation after a lost one."""
    key, _job, token, stray = _new_instruction_start(env, monkeypatch)
    written = _doc(FA, 71, "T")
    _write_written(written)
    _register(env, written, "T", token)
    _run_ended(INSTRUCTION_RUN)
    result_job = _one_job(env)
    assert _row(env, key)["stage"] == "result" and _review_status(written) == "approved"
    shown = _node_run(env, key)                                         # a run read reconciles first
    assert (shown["status"], shown["result_doc_id"], shown["stage"]) == ("open", None, "result")
    _stop(env)
    assert _resume(env)["status"] == "running"                           # a resume reconciles too
    assert _node(env, key) == ("open", None) and "result_submitted" not in _events(env, key)
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"], contact=contact)
    if not contact:
        assert _node(env, key) == ("open", None)
        assert _node_run(env, key)["result_doc_id"] == result           # the reconciliation finds the real one
    assert _node(env, key) == ("review", result) and _line(env, key, "report") == result
    _work_run_ended()
    assert _node(env, key) == ("done", result)
    assert (_review_status(result), _review_status(stray)) == ("approved", "pending_review")
@pytest.mark.parametrize("contact", [True, False])
def test_a_report_a_failed_instruction_stage_registered_never_becomes_the_result(env, decided, monkeypatch, contact):
    """The instruction stage failed after its AI registered a report: the failure keeps that report behind the
    stage — a run read or a resume does not put the node in review — and the report a person registers with the
    node's token (L0009 E-23) is the result, on its contact or by the reconciliation after a lost one."""
    key, job, _token, stray = _new_instruction_start(env, monkeypatch)
    _run_ended(INSTRUCTION_RUN, end_reason="timeout", stop_code="timeout")
    assert env["jobs"] == []
    shown = _node_run(env, key)
    assert (shown["status"], shown["result_doc_id"], shown["dispatch"]["error_code"]) == (
        "open", None, "ai_invoke_failed")
    _stop(env)
    assert _resume(env)["status"] == "running"
    assert _node(env, key) == ("open", None) and "result_submitted" not in _events(env, key)
    result = _result(82, "TR")
    _register(env, result, "TR", job["token"]["token_id"], contact=contact)      # a person, the dispatch token
    if not contact:
        assert _node_run(env, key)["result_doc_id"] == result
    assert _node(env, key) == ("review", result) and _review_status(stray) == "pending_review"
