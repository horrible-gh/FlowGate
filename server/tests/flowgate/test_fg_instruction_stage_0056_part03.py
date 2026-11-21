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

def test_the_inbox_contacts_the_graph_before_it_consumes_the_token():
    """The order ``_register_as_the_inbox`` follows: a new registration reaches the result binder before its token is
    consumed (its ``token_consumed`` event comes after the contact), so a stage can end between the two."""
    src = (_SERVER_DIR / "modules" / "flow_gate" / "api" / "inbox_routes.py").read_text(encoding="utf-8")
    start = src.index("def _handle_new(")
    body = src[start:src.index("\ndef ", start + 1)]
    contact = body.index("_graph_contact.after_inbox_registered(")
    assert body.count("token_service.consume(") == 1 and contact < body.index("token_service.consume(")
@pytest.mark.parametrize("result_contact", [True, False])
@pytest.mark.parametrize("arrival", ["contact_then_end", "end_then_contact", "committed_after_end"])
@pytest.mark.parametrize("end", ["result", "timeout", "cancelled"])
def test_a_report_the_instruction_ai_registered_as_its_stage_ended_never_becomes_the_result(
        env, decided, monkeypatch, end, arrival, result_contact):
    """The instruction AI's report is still on its way when its stage ends — the move to the result stage, or a
    timeout / cancel that fails it — in the inbox's order: its contact before the end and its consume after
    (``contact_then_end``), the whole request after the end (``end_then_contact``), or a registration whose contact
    saw nothing and whose consume lands after the end (``committed_after_end``: a PostgreSQL commit after the
    boundary). Its token is the instruction stage's, so no contact, run read or resume binds it; the node's real
    result — the result AI's report, or after a failure a person's with the dispatch token (L0009 E-23) — binds on
    its contact or by the reconciliation after a lost one."""
    key, job, token, _none = _new_instruction_start(env, monkeypatch, stray=False)
    if end == "result":
        written = _doc(FA, 71, "T")
        _write_written(written)
        _register_as_the_inbox(env, written, "T", token)
    ending = {} if end == "result" else {"end_reason": end, "stop_code": end}

    def stage_end():
        _run_ended(INSTRUCTION_RUN, **ending)

    stray = _result(70, "TR")
    if arrival == "contact_then_end":
        _register_as_the_inbox(env, stray, "TR", token, before_consume=stage_end)
    else:
        stage_end()
        _register_as_the_inbox(env, stray, "TR", token, contact=arrival == "end_then_contact")
    row = _row(env, key)
    if end == "result":
        assert (row["stage"], row["dispatch_error_code"]) == ("result", None)
        result_job = _one_job(env)
    else:
        assert (row["stage"], row["dispatch_error_code"]) == ("instruction", "ai_invoke_failed")
        assert env["jobs"] == []
    shown = _node_run(env, key)                                         # a run read reconciles first
    assert (shown["status"], shown["result_doc_id"]) == ("open", None)
    _stop(env)
    assert _resume(env)["status"] == "running"                           # a resume reconciles too
    assert _node(env, key) == ("open", None) and "result_submitted" not in _events(env, key)
    assert _token_stage(env, token) == "instruction"
    if end == "result":
        _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
        real = issue["token_id"]
        assert _token_stage(env, real) == "result"
    else:
        real = job["token"]["token_id"]                                  # a person, the dispatch token
        assert _token_stage(env, real) is None
    result = _result(81, "TR")
    _register_as_the_inbox(env, result, "TR", real, contact=result_contact)
    if not result_contact:
        assert _node(env, key) == ("open", None)
        assert _node_run(env, key)["result_doc_id"] == result           # the reconciliation finds the real one
    assert _node(env, key) == ("review", result) and _review_status(stray) == "pending_review"
def test_a_report_a_person_registered_as_the_instruction_stage_failed_stays_behind_it(env, decided, monkeypatch):
    """A person registers a report with the node's dispatch token while the instruction AI still works; the stage
    times out between the registration's contact and its consume. The contact marked the dispatch token as the
    instruction stage's, so the late consume binds nothing — on a run read or a resume — as the contact judged."""
    key, job, _token, _none = _new_instruction_start(env, monkeypatch, stray=False)
    dispatch = job["token"]["token_id"]
    early = _result(72, "TR")
    _register_as_the_inbox(env, early, "TR", dispatch, before_consume=lambda: _run_ended(
        INSTRUCTION_RUN, end_reason="timeout", stop_code="timeout"))
    assert _row(env, key)["dispatch_error_code"] == "ai_invoke_failed"
    assert _token_stage(env, dispatch) == "instruction"
    shown = _node_run(env, key)
    assert (shown["status"], shown["result_doc_id"]) == ("open", None)
    _stop(env)
    assert _resume(env)["status"] == "running"
    assert _node(env, key) == ("open", None) and "result_submitted" not in _events(env, key)
@pytest.mark.parametrize("contact", [True, False])
@pytest.mark.parametrize("instruction, written, other", [("use", "T", "TR"), ("omit", "TR", "T")])
def test_a_card_run_opened_before_migration_148_finishes_with_the_type_its_ai_was_told(pre_feature_attempts, 
        env, decided, monkeypatch, instruction, written, other, contact):
    """A card run whose AI started under the one-stage contract before migration 148 (instruction use: the node's
    own T; omitted: its TR): after 148 its node run has no stage, and the document its AI was told to write is
    still its result — on the contact, or by a run read's reconciliation after a lost one — and the AI run's end
    approves it; the other type binds nothing."""
    _admin_and_reviewer(env)
    key = _card(env, "T", (("instruction", instruction),))
    _start(env)
    job = _one_job(env)
    told = {**job, "stage": "result", "instruction": None, "action_scope": "new", "target_doc_id": None,
            "ref_doc_ids": None, "result_doc_type": written}            # what the dispatch before 148 queued
    mention, _start_kw, issue = _record_start(monkeypatch, told, WORK_RUN)
    assert mention["head_type"] == written
    _before_migration_148(env)
    row = _row(env, key)
    assert (row["status"], row["stage"], row["instruction_doc_id"]) == ("open", None, None)
    assert _token_stage(env, issue["token_id"]) is None
    _register(env, _result(80, other), other, issue["token_id"])
    assert _node_run(env, key)["result_doc_id"] is None                  # the other type is no result
    result = _result(81, written)
    _register(env, result, written, issue["token_id"], contact=contact)
    if not contact:
        assert _node_run(env, key)["result_doc_id"] == result
    assert _node(env, key) == ("review", result)
    _work_run_ended()                                                    # the run end approves it
    assert _node(env, key) == ("done", result) and _review_status(result) == "approved"
def test_a_stopped_run_finishes_both_stages_of_a_card_it_opened(env, decided, monkeypatch):
    _admin_and_reviewer(env)
    key = _card(env, "T")
    _start(env)
    _stop(env)                                                           # 다음 단계: the card is open
    job = _one_job(env)
    _record_start(monkeypatch, job, INSTRUCTION_RUN)
    _write_written(job["target_doc_id"])
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=job["target_doc_id"])
    result_job = _one_job(env)                                           # the result stage starts while stopped
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"])
    _work_run_ended()
    run = _current(env)
    node_run = next(n for n in run["node_runs"] if n["node_key"] == key)
    assert (run["status"], node_run["status"], node_run["stage"], node_run["instruction_doc_id"]) == (
        "stopped", "done", "result", job["target_doc_id"])
    assert (run["pending_propagation_count"], run["pending_node_keys"]) == (1, [key])
    assert _resume(env)["status"] == "completed"
def test_an_issuer_without_document_approve_still_has_the_instruction_and_result_approved_automatically(
        env, decided, monkeypatch):
    """flowgate.v02.0105 T#2 — expectation revised (NR flowgate.v02.0101.0003 §3.9.1, Q #511 o3: 0083 T#4 개정). Before,
    an issuer without ``document.approve`` left the instruction unapproved and the card waiting for a person's approval.
    An AI card's automatic approvals now take the server's automatic approval authority: the instruction and the result
    are approved, the card never awaits approval, the issuer stays the approval's actor and the system's authority is
    recorded beside each; a person's card approval keeps its own check."""
    from modules.flow_gate.workflow.routers import workflow

    _admin_and_reviewer(env)
    monkeypatch.setattr(workflow, "_get_user_permissions", lambda u: set())   # the run's issuer (and everyone)
    key = _card(env, "T")
    _start(env)
    job = _one_job(env)
    _record_start(monkeypatch, job, INSTRUCTION_RUN)
    instruction = job["target_doc_id"]
    _write_written(instruction)
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=instruction)
    assert _review_status(instruction) == "approved"
    result_job = _one_job(env)
    assert result_job["ref_doc_ids"] == [instruction]
    _mention, _start_kw, issue = _record_start(monkeypatch, result_job, WORK_RUN)
    result = _result(81, "TR")
    _register(env, result, "TR", issue["token_id"])
    _work_run_ended()
    assert _node(env, key) == ("done", result) and _review_status(result) == "approved"
    assert _node_run(env, key)["awaiting_approval"] is False
    rows = _sql(env["db"], "SELECT reason, payload_json FROM graph_run_events WHERE cause = 'auto_approval_authority' "
                           "ORDER BY event_seq")
    told = [json.loads(r["payload_json"]) for r in rows]
    assert [(r["reason"], t["doc_id"], t["stage"], t["issuer"]) for r, t in zip(rows, told)] == [
        ("system", instruction, "instruction", "u_kim"), ("system", result, "result", "u_kim")]   # the issuer: the actor
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_run_events WHERE cause = 'auto_approval_skipped'")[0]["n"] == 0
