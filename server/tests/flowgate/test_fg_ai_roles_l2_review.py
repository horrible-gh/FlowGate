"""flowgate.v02.0113 T#3 — L-2 §2.8 the review stages of an assigned attempt: who reviews, how many rounds, who reworks,
and how a verdict is consumed once (passed / issues / not passed, a newer submission taken over).

The harness of ``test_fg_ai_roles_l2_assignment``. A review loop's run is a recorder; its end is the loop row and the
ledger request it ran as (``_loop_ended``), then the run-end contact (``_run_ended`` with ``document_review_loop``) as
the engine sends it. ``submission_passed`` (the verdict row of the current revision) is a mock where a case names it.
"""
from __future__ import annotations

import json

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    A, B, FA, INSTRUCTION_RUN, REVIEW_RUN, STAGE_ASSIGNMENTS, WORK_RUN, X, Y, _executions_ended, _review_status,
    _run_ended, _sql,
    _start_ai, _write_written, ai_writes_result, b1, db_path, decided, env, flow, ledger, line_doc, node_run, one,
    record_starts, roles, save_instruction, start, starts, stored, wired, write_line,
)


def _diag(env, key, doc_id):
    """Diagnostics of a missing assignment: the run's events, the node run, the attempts and the ledger."""
    run = _sql(env["db"], "SELECT run_id, status, idle_reason FROM graph_runs")
    evs = _sql(env["db"], "SELECT event_seq, cause, reason, payload_json FROM graph_run_events ORDER BY event_seq")
    row = node_run(env, key)
    return json.dumps({"run": run, "events": [(e["event_seq"], e["cause"], e["reason"], e["payload_json"][-160:])
                                   for e in evs[-10:]],
            "row": {k: row[k] for k in ("status", "stage", "result_doc_id", "instruction_doc_id",
                                        "dispatch_error_code")},
            "assignments": [(a["stage"], a["seq"], a["actor"], a["stage_state"], a["verdict"]) for a in
                            _sql(env["db"], "SELECT * FROM graph_stage_assignments")],
            "ledger": [(l["kind"], l["status"]) for l in ledger(env)]}, ensure_ascii=False)


def _loop_ended(env, a, stop_reason, run_id=REVIEW_RUN, doc_id=None):
    """The assignment's review loop ran as ``run_id`` and stopped with ``stop_reason`` (its ledger request ended)."""
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops

    exec_id = a["intent_exec_id"]
    _sql(env["db"], "UPDATE graph_attempt_runs SET ai_run_id = ?, status = 'ended', ended_at = 't' WHERE exec_id = ?",
         (run_id, exec_id))
    db_loops.insert({"run_id": run_id, "group_id": FA, "doc_ref": doc_id or a["basis_doc_id"], "review_count": 1,
                     "reviewer_provider_id": a["provider_id"], "review_criteria": "document_type_default",
                     "rework_provider_id": a.get("rework_provider_id") or a["provider_id"], "rework_timeout_sec": 3600,
                     "failure_restart_max_attempts": 0, "total_timeout_sec": 14400, "review_baseline_id": 0,
                     "baseline_revision_no": 0, "starts_with_rework": 0, "started_at": "t", "deadline_at": "t",
                     "current_stage": "review"})
    detail = None
    if stop_reason == "awaiting_author_revision":           # the engine's encoding of a no-rework issues end
        stop_reason, detail = "review_count_exhausted", "awaiting_author_revision"
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', stop_reason = ?, "
                    "stop_detail = ? WHERE run_id = ?", (stop_reason, detail, run_id))
    return exec_id


def _review_end(env, a, stop_reason, run_id=REVIEW_RUN):
    exec_id = _loop_ended(env, a, stop_reason, run_id)
    _run_ended(run_id, action_scope="review", doc_ref=a["basis_doc_id"], graph_exec_id=exec_id,
               document_review_loop={"current_stage": "stopped"})


def _ai_writes_instruction(env, monkeypatch, provider=A, body="## 작업\n\nAI가 쓴 지시서\n"):
    """The instruction AI's run as the engine runs it: its edit token writes the line's document (the inbox edit's
    records: a new revision and the token's consume), then its run ends on its own."""
    from modules.flow_gate.services import token_service

    [job] = env["jobs"]
    assert (job["stage"], job["action_scope"], job["provider_id"]) == ("instruction", "edit", provider)
    _heads, token = _start_ai(monkeypatch, job, INSTRUCTION_RUN)
    target = job["target_doc_id"]
    _write_written(target, body)
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (target,))
    _sql(env["db"], "INSERT INTO workflow_events (event_type, project_id, group_id, actor_user_id, metadata, created_at) "
                    "VALUES ('doc_edited', 'flowgate', ?, 'u_kim', ?, 't')",
         (FA, json.dumps({"doc_id": target, "revision_no": 1})))
    token_service.consume(token, "flowgate", target)
    env["jobs"].clear()
    # its execution's run-end hand-off keeps the run running through the hooks; it ends after them (harness)
    _run_ended(INSTRUCTION_RUN, action_scope="edit", doc_ref=target, provider_id=provider)
    _end_written_instruction(env)
    return target


def _end_written_instruction(env):
    """The instruction stage's own execution ends once its run-end stage is over (a result AI's request has already
    taken it over: ``request_write`` hands a requested instruction writer off)."""
    rows = _sql(env["db"], "SELECT exec_id, request_json FROM graph_attempt_runs WHERE status IN ('requested', 'running')")
    for row in rows:
        if json.loads(row["request_json"]).get("job", {}).get("stage") == "instruction":
            _sql(env["db"], "UPDATE graph_attempt_runs SET status = 'cancelled', ended_at = 't' WHERE exec_id = ?",
                 (row["exec_id"],))


def _resubmit(env, doc_id):
    """The author's resubmission of a rejected document in place (``mark_revised``): back in review, one rejection."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.api import v01_contact

    db_documents.update(doc_id, {"doc_review_status": "pending_review",
                                 "rejection_history": json.dumps([{"reason": "issues", "review_id": 1}])})
    v01_contact.after_review_decided(FA, doc_id, "mark_revised")


# ── G-5: AI instruction, person result, B reviews once with no rework ────────

def test_g5_k14_a_person_result_is_reviewed_once_with_no_rework_and_a_resubmission_is_seq_2(env, decided, monkeypatch,
                                                                                            starts):
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.db import documents as db_documents

    key = flow(env)
    roles(key, A, "not_used", B)
    start(env)
    instruction = _ai_writes_instruction(env, monkeypatch)
    record_starts(monkeypatch, starts)
    assert _review_status(instruction) == "approved"
    assert one(env, key, "instruction_review", 1)["author"] == "ai"
    assert one(env, key, "result") is not None, _diag(env, key, None)
    assert one(env, key, "result")["actor"] == "person"
    report = write_line(env, key, "report", "## 결과\n\n사람이 쓴 레포트\n")["doc_id"]
    r1 = one(env, key, "result_review", 1)
    assert (r1["actor"], r1["provider_id"], r1["review_rounds"], r1["rework_provider_id"], r1["author"]) == \
        ("ai", B, 1, None, "person")                                        # K-14: no rework hop, never B
    [loop] = [c for c in starts if c.get("document_review_loop")]
    assert loop["document_review_loop"]["rework_provider_id"] is None
    db_documents.update(report, {"doc_review_status": "rejected"})           # the engine's rejection of the issues
    _review_end(env, r1, "awaiting_author_revision")
    r1 = one(env, key, "result_review", 1)
    assert (r1["verdict"], r1["stage_state"]) == ("issues", "done")
    assert b1(env) == []                                                     # a person's issues: no B-1
    _resubmit(env, report)
    r2 = one(env, key, "result_review", 2)
    assert (r2["actor"], r2["basis_rejection_count"], r2["author"]) == ("ai", 1, "person")
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, r2, "review_passed", "air_card_review2")
    assert one(env, key, "result_review", 2)["verdict"] == "passed"
    assert _review_status(report) == "approved" and node_run(env, key)["status"] == "done"


def test_g6_a_person_result_with_an_unset_review_and_ais_to_choose_waits_for_setup(env, decided, starts):
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used")
    start(env)
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["source"], rr["stage_state"]) == ("wait_setup", "none", "waiting")
    assert _review_status(report) != "approved" and node_run(env, key)["status"] == "review"
    assert starts == []


def test_g8_a_former_person_card_is_a_person_s_twice_and_its_result_has_no_review(env, decided, starts):
    """G-8: a person assignee with instruction_ai A, review_ai B and review_count 0 kept: I-2 / E-2 person, R-3 no review —
    the system approves (the 'person approval wait' expectation of the pre-feature path is revised here)."""
    key = flow(env, assignee={"kind": "human", "user_id": "u_lee", "provider_id": None},
               extra_settings=(("instruction_ai", A), ("review_ai", B), ("review_count", 0)))
    roles(key, init="legacy")
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["actor"], ia["actor_reason"], ia["source"]) == ("person", "legacy_person", "legacy_fields")
    instruction = save_instruction(env, key)
    assert _review_status(instruction) == "approved"
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["actor_reason"]) == ("no_review", "legacy_no_review")
    assert _review_status(report) == "approved" and node_run(env, key)["status"] == "done"
    assert starts == [] and env["jobs"] == []


def test_g9_a_former_run_ai_card_reviewing_twice_with_no_reviewer_waits_and_never_asks_the_project_default(
        env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "default_reviewer",
                        lambda p: (_ for _ in ()).throw(AssertionError("project default reviewer asked")))
    key = flow(env, extra_settings=(("run_ai", X), ("review_count", 2)))
    roles(key, init="legacy")
    start(env)
    assert (one(env, key, "instruction")["actor"], one(env, key, "instruction")["provider_id"]) == ("ai", X)
    _ai_writes_instruction(env, monkeypatch, provider=X)
    ra = one(env, key, "result")
    assert ra is not None, _diag(env, key, None)
    assert (ra["actor"], ra["provider_id"]) == ("ai", X)
    [job] = env["jobs"]
    ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["source"], rr["author"]) == ("wait_setup", "legacy_fields", "ai")
    assert [c for c in starts if c.get("document_review_loop")] == []


# ── K-8: the author is the document's evidence, never this attempt's assignment ──

@pytest.mark.parametrize("case", ["person_wrote_it", "unknown"])
def test_k8_a_reused_instruction_s_author_decides_one_round_and_no_rework(env, decided, monkeypatch, starts, case):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction_review_count", 3), ("instruction_review_ai", B)))
    roles(key, A, "not_used", "not_used")
    _write_written(line_doc(env, key, "instruction"), "## 작업\n\n미리 써 둔 지시서\n")
    if case == "unknown":
        monkeypatch.setattr(legacy_adapter, "_written_by", lambda d, t: (_ for _ in ()).throw(RuntimeError("read")))
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["actor"], ia["provider_id"], ia["reused_doc_id"], ia["stage_state"]) == \
        ("ai", A, line_doc(env, key, "instruction"), "done")
    assert env["jobs"] == []                                                 # A is never asked to write it
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["provider_id"], ir["review_rounds"], ir["rework_provider_id"], ir["author"]) == \
        ("ai", B, 1, None, "person" if case == "person_wrote_it" else "unknown")
    assert node_run(env, key)["instruction_doc_id"] == ia["reused_doc_id"]    # K-7: linked before its review


def test_k8_an_ai_written_instruction_with_a_person_instruction_role_has_no_rework(env, decided, monkeypatch, starts):
    """An earlier AI's instruction (its evidence: an AI token wrote it) reused while this attempt's instruction role is
    a person's: one round, no rework (the rework AI is only this attempt's instruction AI)."""
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction_review_count", 2), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    _write_written(line_doc(env, key, "instruction"), "## 작업\n\n이전 AI의 지시서\n")
    monkeypatch.setattr(legacy_adapter, "_written_by", lambda d, t: {"author": "ai", "provider_id": X})
    start(env)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["author"], ir["author_provider_id"], ir["review_rounds"], ir["rework_provider_id"]) == ("ai", X, 1, None)


def test_k8_an_ai_instruction_of_this_attempt_reviewed_by_b_is_reworked_by_its_writer(env, decided, monkeypatch, starts):
    key = flow(env, extra_settings=(("instruction_review_count", 2), ("instruction_review_ai", B)))
    roles(key, A, "not_used", "not_used")
    start(env)
    _ai_writes_instruction(env, monkeypatch)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["provider_id"], ir["review_rounds"], ir["rework_provider_id"], ir["author"],
            ir["author_provider_id"]) == ("ai", B, 2, A, "ai", A)


# ── K-6 / K-11: a submission during a loop, a rework within one assignment ───

def test_k6_an_instruction_revised_while_its_review_works_is_taken_over_after_it(env, decided, monkeypatch, starts):
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = save_instruction(env, key)
    i1 = one(env, key, "instruction_review", 1)
    assert (i1["actor"], i1["author"], i1["intent_kind"]) == ("ai", "person", "execution")
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (instruction,))
    save_instruction(env, key, "## 작업\n\n고친 지시서\n")                     # TAKE_OVER_LATER: no seq 2 yet
    assert one(env, key, "instruction_review", 2) is None
    _review_end(env, i1, "review_passed")                                    # the pass answers revision 0
    assert one(env, key, "instruction_review", 1)["verdict"] == "passed"
    assert _review_status(instruction) != "approved"                         # never approved by an older pass
    i2 = one(env, key, "instruction_review", 2)
    assert (i2["basis_revision_no"], i2["actor"]) == (1, "ai")


def test_k11_an_ai_rework_keeps_the_same_assignment_and_a_later_role_change_is_not_applied(env, decided, monkeypatch,
                                                                                          starts):
    from flowgate.ai_roles import role_store
    from modules.flow_gate.graph_workflow.services import card_settings_ports
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review=B)
    start(env)
    [job] = env["jobs"]
    result = ai_writes_result(env, monkeypatch, key, job)
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    r1 = one(env, key, "result_review", 1)
    _sql(env["db"], "UPDATE documents SET revision_no = 2, rejection_history = ? WHERE doc_id = ?",
         (json.dumps([{"reason": "issues"}]), result))                       # the rework's new revision
    with_flow_guard(FA, lambda: role_store.write_slot(FA, key, role_store.RoleSlot(
        "review", role_store.RoleValue("assigned", Y), "card_explicit", "u_kim", role_store._now())))
    card_settings_ports.reconcile_stage(FA)
    card_settings_ports.reconcile_stage(FA)
    assert one(env, key, "result_review", 2) is None and one(env, key, "result_review", 1) == r1
    assert len([c for c in starts if c.get("document_review_loop")]) == 1


def test_k14_the_rework_target_of_each_purpose_is_never_the_reviewer():
    from flowgate.card_settings import stage_assignment as sa

    ai_result_review = {"actor": "ai", "provider_id": B, "rework_provider_id": A}
    person_result_review = {"actor": "ai", "provider_id": B, "rework_provider_id": None}
    assert sa.target_provider(ai_result_review, sa.RESULT_REVIEW_ROUND) == B
    assert sa.target_provider(ai_result_review, sa.RESULT_REWORK) == A
    assert sa.target_provider(person_result_review, sa.RESULT_REWORK) == sa.NONE
    assert sa.target_provider(person_result_review, sa.INSTRUCTION_REWORK) == sa.NONE
    assert sa.target_provider({"actor": "wait_setup", "provider_id": None}, sa.RESULT_WRITE) is None


# ── G-11 / K-1: a TEST card of AIs — A writes the TS, X the TSR, B reviews, X reworks ──

def test_g11_k1_an_ai_test_card_writes_ts_then_tsr_reviewed_by_b_and_reworked_by_x(env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, "TS")
    roles(key, A, X, B)
    start(env)
    ia = one(env, key, "instruction")
    assert (ia["actor"], ia["provider_id"], ia["plan_instruction_type"], ia["plan_result_type"]) == ("ai", A, "TS", "TSR")
    [job] = env["jobs"]
    assert (job["result_doc_type"], job["provider_id"]) == ("TS", A)        # K-1: A writes the TS in the stage
    ts = _ai_writes_instruction(env, monkeypatch, provider=A, body="## 시험 시나리오\n\nA가 쓴 TS\n")
    assert _review_status(ts) == "approved"                                  # the system's approval of the TS
    [rjob] = env["jobs"]
    assert (rjob["provider_id"], rjob["result_doc_type"], rjob["ref_doc_ids"]) == (X, "TSR", [ts])
    result = ai_writes_result(env, monkeypatch, key, rjob, typ="TSR")
    assert node_run(env, key)["result_doc_id"] == result                     # K-2: the binder takes the TSR
    record_starts(monkeypatch, starts)
    _run_ended(WORK_RUN, provider_id=X)
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["provider_id"], rr["rework_provider_id"], rr["author"]) == ("ai", B, X, "ai")
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _review_end(env, rr, "review_passed")
    assert _review_status(result) == "approved" and node_run(env, key)["status"] == "done"


# ── K-5: a person's instruction rejected by its review, revised, reviewed as a new submission ──

def test_k5_a_person_instruction_s_issues_reject_it_without_b1_and_its_revision_is_seq_2(env, decided, starts):
    from flowgate.card_settings import stage_view
    from modules.flow_gate.db import documents as db_documents

    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = save_instruction(env, key)
    i1 = one(env, key, "instruction_review", 1)
    assert (i1["actor"], i1["author"], i1["review_rounds"], i1["rework_provider_id"]) == ("ai", "person", 1, None)
    db_documents.update(instruction, {"doc_review_status": "rejected"})
    _review_end(env, i1, "awaiting_author_revision")
    assert one(env, key, "instruction_review", 1)["verdict"] == "issues" and b1(env) == []
    row = node_run(env, key)
    shown = stage_view.wire(FA, row, i1["attempt_id"], None)
    assert shown["wait_reason"] == {"code": "manual_write_wait", "role": "instruction", "stage": "instruction_review",
                                    "seq": 1, "detail": None}
    _sql(env["db"], "UPDATE documents SET revision_no = 1 WHERE doc_id = ?", (instruction,))
    save_instruction(env, key, "## 작업\n\n지적을 반영한 지시서\n")
    i2 = one(env, key, "instruction_review", 2)
    assert (i2["actor"], i2["basis_revision_no"], i2["author"]) == ("ai", 1, "person")
