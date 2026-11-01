"""flowgate.v02.0113 T#3 — L-2 §2.10 / §2.11 the run read's reconciliation (RC-1 ~ RC-8) and the explicit command's
retry of an assigned attempt: what was lost is done once, a wait or a failure is never restarted by a read, and only an
explicit command opens a new attempt (``REOPEN_NEW_ATTEMPT``) or writes again on the same one (``RETRY_SAME_ATTEMPT``).

The harness of ``test_fg_ai_roles_l2_assignment``; a lost contact is a step the test does not deliver.
"""
from __future__ import annotations

import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    B, FA, STAGE_ASSIGNMENTS, WORK_RUN, X, Y, _executions_ended, _failing_issue, _review_status, _sql, _write_written,
    assignments, command, db_path, decided, env, flow, ledger, line_doc, node_run, one, roles, save_instruction, start,
    starts, stored, tokens, wired, write_line,
)
from test_fg_ai_roles_l2_review import _loop_ended  # noqa: E402


def _read(env):
    """A run read: its reconciliation (``reconcile_stage``) runs before the answer."""
    res = env["client"].get(f"/api/v1/flows/{FA}/runs/current", headers={"Authorization": "Bearer user:u_kim"})
    assert res.status_code == 200, res.text
    return res.json()["run"]


def _wire(env, key):
    return next(n for n in _read(env)["node_runs"] if n["node_key"] == key)


def _settle():
    from flowgate.card_settings import legacy_adapter

    legacy_adapter.settle_graph_run(FA)


def _attempts(env, key):
    return _sql(env["db"], "SELECT attempt_id, status, invalidated_reason FROM graph_node_attempts WHERE node_key = ? "
                           "ORDER BY created_at", (key,))


# ── RC-4 / K-4: a passed instruction review whose end was lost ───────────────

def test_k4_rc4_a_lost_end_of_a_passed_instruction_review_approves_and_starts_the_result_stage_once(
        env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", B)))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = save_instruction(env, key)
    i1 = one(env, key, "instruction_review", 1)
    _loop_ended(env, i1, "review_passed")                                     # its _after_review contact is lost
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    _read(env)
    assert one(env, key, "instruction_review", 1)["verdict"] == "passed"
    assert _review_status(instruction) == "approved"
    assert one(env, key, "result")["actor"] == "person" and node_run(env, key)["stage"] == "result"
    before = assignments(env, key)
    save_instruction(env, key)                                                # the same save again: EXISTING
    _read(env)
    assert assignments(env, key) == before


# ── RC-1 / K-10: an AI review assignment without its intent, a lost automatic approval ──

@pytest.mark.parametrize("ledger_kept", [True, False])
def test_k10_rc1_a_review_assignment_left_without_its_intent_gets_one_request(env, decided, starts, ledger_kept):
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used", review=B)
    start(env)
    write_line(env, key, "report", "## 결과\n\n본문\n")
    rr = one(env, key, "result_review", 1)
    exec_id = rr["intent_exec_id"]
    _sql(env["db"], "UPDATE graph_stage_assignments SET intent_kind = NULL, intent_exec_id = NULL")
    if ledger_kept:                                   # its request was made (and its process is over): E-43 reuses it
        _sql(env["db"], "UPDATE graph_attempt_runs SET status = 'ended', ended_at = 't' WHERE exec_id = ?", (exec_id,))
    else:
        _sql(env["db"], "DELETE FROM graph_attempt_runs WHERE exec_id = ?", (exec_id,))
    _read(env)
    _read(env)
    reviews = [l for l in ledger(env) if l["kind"] in ("review", "rework")]
    assert len(reviews) == 1
    again = one(env, key, "result_review", 1)
    assert again["intent_kind"] == "execution" and again["intent_exec_id"] == reviews[0]["exec_id"]
    assert (reviews[0]["exec_id"] == exec_id) is ledger_kept


def test_k10_a_lost_automatic_approval_of_no_review_is_made_once_by_the_next_read(env, decided, monkeypatch):
    from flowgate.card_settings import assigned_review

    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution="not_used", review="not_used")
    start(env)
    real = assigned_review.auto_approve
    monkeypatch.setattr(assigned_review, "auto_approve", lambda *a, **k: None)       # the step after the commit lost
    report = write_line(env, key, "report", "## 결과\n\n본문\n")["doc_id"]
    rr = one(env, key, "result_review", 1)
    assert (rr["actor"], rr["intent_kind"], rr["verdict"]) == ("no_review", "approval", None)
    monkeypatch.setattr(assigned_review, "auto_approve", real)
    _read(env)
    _read(env)
    assert _review_status(report) == "approved" and one(env, key, "result_review", 1)["verdict"] == "auto_approved"
    assert len(_sql(env["db"], "SELECT 1 FROM graph_run_events WHERE cause = 'auto_approval_authority'")) == 1


# ── E-40: a wait or a failure is never restarted by a read ───────────────────

def test_e40_a_failed_or_waiting_assignment_is_not_restarted_by_reads(env, decided, monkeypatch, starts):
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review="not_used")
    _failing_issue(monkeypatch, {1})
    start(env)
    _read(env)
    _read(env)
    assert tokens(env, key) == [] and env["jobs"] == [] and starts == []
    assert one(env, key, "result")["stage_state"] == "failed"


# ── K-3 / K-15: a review wait reopened only by an explicit command ───────────

def test_k3_an_unavailable_instruction_reviewer_waits_and_only_an_explicit_command_reopens_a_new_attempt(
        env, decided, monkeypatch, starts):
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", "aip_gone")))
    roles(key, "not_used", "not_used", "not_used")
    start(env)
    instruction = save_instruction(env, key)
    ir = one(env, key, "instruction_review", 1)
    assert (ir["actor"], ir["cause"]) == ("wait_unavailable", "deleted")
    wire = _wire(env, key)
    assert wire["wait_reason"] == {"code": "ai_unavailable", "role": "review", "stage": "instruction_review", "seq": 1,
                                   "detail": "deleted"}
    assert node_run(env, key)["dispatch_error_code"] == "ai_unavailable:deleted"
    assert _sql(env["db"], "SELECT status, idle_reason FROM graph_runs")[0] == {"status": "idle", "idle_reason": "error"}
    stored(key, "instruction_review_ai", Y)
    _read(env)
    assert len(_attempts(env, key)) == 1                                       # a read never reopens it
    old = _attempts(env, key)[0]["attempt_id"]
    command(env)
    attempts = _attempts(env, key)
    assert [a["status"] for a in attempts] == ["invalidated", "active"]
    new = attempts[1]["attempt_id"]
    ia = one_of(env, new, "instruction")
    assert ia["reused_doc_id"] == instruction                                  # a person's saved draft is reused
    ir2 = one_of(env, new, "instruction_review", 1)
    assert (ir2["actor"], ir2["provider_id"]) == ("ai", Y)
    assert one_of(env, old, "instruction_review", 1)["actor"] == "wait_unavailable"   # the old record is kept


def one_of(env, attempt_id, stage, seq=0):
    rows = _sql(env["db"], "SELECT * FROM graph_stage_assignments WHERE attempt_id = ? AND stage = ? AND seq = ?",
                (attempt_id, stage, seq))
    return rows[0] if rows else None


def test_k15_a_reused_instruction_s_unavailable_review_keeps_waiting_after_its_count_is_set_to_0(env, decided, starts):
    key = flow(env, extra_settings=(("instruction_review_count", 1), ("instruction_review_ai", "aip_gone")))
    roles(key, "not_used", "not_used", "not_used")
    instruction = line_doc(env, key, "instruction")
    _write_written(instruction, "## 작업\n\n미리 쓴 지시서\n")
    start(env)
    assert one(env, key, "instruction_review", 1)["actor"] == "wait_unavailable"
    stored(key, "instruction_review_count", 0)
    _read(env)
    _read(env)
    assert _review_status(instruction) != "approved" and node_run(env, key)["stage"] == "instruction"
    assert one(env, key, "result") is None
    command(env)
    new = [a for a in _attempts(env, key) if a["status"] == "active"][0]["attempt_id"]
    ir = one_of(env, new, "instruction_review", 1)
    assert (ir["actor"], ir["actor_reason"]) == ("no_review", "no_review_setting")
    assert _review_status(instruction) == "approved"


# ── RETRY_SAME_ATTEMPT: a result writer that ended with no result ────────────

def test_an_explicit_command_retries_a_no_result_writer_on_the_same_attempt_with_the_same_ai(env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review="not_used")
    start(env)
    [first] = env["jobs"]
    env["jobs"].clear()
    _executions_ended()
    monkeypatch.setattr(legacy_adapter, "write_failure", lambda row: "no_result")
    _settle()
    attempt = one(env, key, "result")["attempt_id"]
    stored(key, "run_ai", Y)                                                    # a value change is not the assignment
    command(env)
    [again] = env["jobs"]
    assert (again["provider_id"], again["assignment_ref"]["attempt_id"]) == (X, attempt)
    assert again["invocation_origin"]["purpose"] == "result_write_retry"
    assert len(_attempts(env, key)) == 1 and first["provider_id"] == X


# ── RC-8: a person's saved report with no registration ───────────────────────

def test_rc8_a_saved_plan_with_no_registration_is_registered_once_by_the_next_read(env, decided, monkeypatch):
    from flowgate.card_documents import legacy_adapter as documents
    from flowgate.card_settings import legacy_adapter

    key = flow(env, "WP")
    roles(key, execution="not_used", review="not_used")
    start(env)
    monkeypatch.setattr(documents, "work_sets", lambda doc: 1)
    monkeypatch.setattr(legacy_adapter, "work_sets", lambda doc: 1)
    plan = line_doc(env, key, "plan")
    _write_written(plan, "{\"steps\": [1]}")
    _read(env)
    _read(env)
    row = node_run(env, key)
    assert row["result_doc_id"] == plan
    assert len([t for t in tokens(env, key) if t["consumed_at"]]) == 1
    assert one(env, key, "result_review", 1)["actor"] == "no_review"


# ── 폴백 금지: a start failure fails the assignment; only an explicit command reopens it, with the same AI ──

def test_a_start_failure_fails_the_assignment_and_an_explicit_command_reopens_it_with_the_same_ai(env, decided,
                                                                                                   monkeypatch):
    from modules.flow_gate.graph_workflow.services import node_dispatch

    called, seen = [], set()

    def failing(job):
        # recovery may redeliver a durable execution until it starts: each execution counted once (``record_job``)
        if job.get("graph_exec_id") not in seen:
            seen.add(job.get("graph_exec_id"))
            called.append(job["provider_id"])
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(node_dispatch, "_ai_starter", failing)
    key = flow(env, extra_settings=(("instruction", "omit"),))
    roles(key, execution=X, review="not_used")
    start(env)
    ra = one(env, key, "result")
    assert (ra["stage_state"], ra["fail_cause"]) == ("failed", "start_failed")
    assert node_run(env, key)["dispatch_error_code"] == "ai_unavailable:start_failed"
    assert _wire(env, key)["wait_reason"] == {"code": "ai_unavailable", "role": "execution", "stage": "result",
                                              "seq": 0, "detail": "start_failed"}
    _read(env)
    assert called == [X]                                                       # a read never restarts it
    command(env)
    assert called == [X, X]                                                    # a new attempt, the same assigned AI
    assert [a["status"] for a in _attempts(env, key)] == ["invalidated", "active"]
