"""flowgate.v02.0105 T#2 — an AI card's automatic approval: its authority, why it was not made, and a lost run end
(NR flowgate.v02.0101.0003 §3.9 B-1 ~ B-4 / B-6, §3.9.1, §3.9.2 rev4; regression cases §3.10).

On the real SQLite DB, routes and fixtures of ``test_fg_card_settings_review`` (the AI invoke admission replaced by
recorders): the run-end stage approves an AI card's result under the server's automatic approval authority (a non-admin
issuer included) and records why it did not (``auto_approval_skipped`` run events, shown as the node run's
``approval_wait_reason``); a run read reconciles a lost run end once — an approval only for review 'none' or the result's
own passed review, a review loop started once and never an approval for a card that needs one, nothing but one recorded
reason otherwise — and every existing record is kept. The durable row of the lost AI run (``ai_run_record``) and, where a
case names it, the verdict of the current revision (``submission_passed``) are mocks.
"""
from __future__ import annotations

import json

import pytest

from test_fg_card_approval_0056 import _approve, _current, _node_run
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, _sql, db_path, decided, env,
)
from test_fg_card_settings_review import (
    REVIEW_RUN, WORK_RUN, _in_review, _loop_row, _node, _recorded_start_run, _review_status, _work_run_ended,
)


@pytest.fixture(autouse=True)
def card_approval_port():
    """The card approval's Graph ports, registered as ``startup`` registers them."""
    from flowgate.card_approval import integration

    integration.register()
    yield
    integration.unregister()


# ── helpers ──────────────────────────────────────────────────────────────────

REVIEWED = (("review_count", 1), ("review_ai", "aip_review"), ("instruction", "omit"))
NO_REVIEW = (("instruction", "omit"),)


def _non_admin(monkeypatch):
    """Nobody holds ``document.approve`` (the approve button's stub as for a non-admin issuer)."""
    from modules.flow_gate.workflow.routers import workflow

    monkeypatch.setattr(workflow, "_get_user_permissions", lambda u: {"document.read"})


def _waits(env):
    rows = _sql(env["db"], "SELECT reason, payload_json FROM graph_run_events WHERE cause = 'auto_approval_skipped' "
                           "ORDER BY event_seq")
    return [(json.loads(r["payload_json"])["doc_id"], r["reason"]) for r in rows]


def _authorities(env):
    rows = _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE cause = 'auto_approval_authority' "
                           "ORDER BY event_seq")
    return [(p["doc_id"], p["auto_approval_authority"], p["issuer"]) for p in (json.loads(r["payload_json"]) for r in rows)]


def _events(env):
    return [dict(r) for r in _sql(env["db"], "SELECT * FROM graph_run_events ORDER BY run_id, event_seq")]


def _loops(env):
    return [dict(r) for r in _sql(env["db"], "SELECT run_id, round_no, attempts_used, current_stage, stop_reason, "
                                             "stop_detail FROM ai_invoke_document_review_loops ORDER BY run_id")]


def _active_exec(env, key):
    rows = _sql(env["db"], "SELECT e.exec_id FROM graph_attempt_runs e JOIN graph_node_attempts a "
                           "ON a.attempt_id = e.attempt_id WHERE a.node_key = ? AND a.status = 'active' "
                           "ORDER BY e.created_at DESC", (key,))
    return rows[0]["exec_id"] if rows else None


def _lost_run_end(monkeypatch, env, key, *, review_exec=True, **ending):
    """The node's AI run ended and its run-end stage never followed it (``on_run_ended`` lost): its durable rows say how
    it ended; the review run's row works for the node's current attempt unless ``review_exec`` is False."""
    from flowgate.card_settings import legacy_adapter

    real = legacy_adapter.ai_run_record
    rows = {WORK_RUN: {"run_id": WORK_RUN, "group_id": FA, "doc_ref": f"{FA}.0001-R", "action_scope": "new",
                       "mode": "single", "issued_to": "u_kim", "provider_id": "aip_assignee", "end_reason": "exited",
                       "stop_code": None, "graph_exec_id": None, "document_review_loop": None, **ending},
            REVIEW_RUN: {"run_id": REVIEW_RUN, "group_id": FA, "action_scope": "review", "mode": "single",
                         "issued_to": "u_kim", "graph_exec_id": _active_exec(env, key) if review_exec else None,
                         "document_review_loop": {"current_stage": "stopped"}}}
    monkeypatch.setattr(legacy_adapter, "ai_run_record", lambda rid: dict(rows[rid]) if rid in rows else real(rid))


def _stored(env, key, field, value_json):
    """The card's setting row as stored (the settings API refuses a card in review: its node is protected)."""
    _sql(env["db"], "DELETE FROM graph_node_settings WHERE group_id = ? AND node_key = ? AND field = ?", (FA, key, field))
    _sql(env["db"], "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at) "
                    "VALUES (?, ?, ?, ?, 't')", (FA, key, field, value_json))


def _passed_loop(env, result, stop_reason="review_passed"):
    _loop_row(result, "review")
    detail = None if stop_reason == "review_passed" else "ended"
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', stop_reason = ?, "
                    "stop_detail = ?, round_no = 2, attempts_used = 3 WHERE run_id = ?", (stop_reason, detail, REVIEW_RUN))


def _verdict(monkeypatch, passed=True):
    from flowgate.card_settings import legacy_adapter

    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: passed)


def _review_starts(monkeypatch, env, result):
    """The review loop's admission is a recorder; a start writes the loop row as the engine's start does. The recorder
    never acquires the loop's Graph execution, so the engine's scheduler replays that same requested execution (harness):
    only the starts that are no such replay are recorded."""
    import traceback

    from modules.flow_gate.services import ai_invoke_service

    calls = []

    def start_run(**kw):
        if not any(frame.name == "replay" for frame in traceback.extract_stack()):
            calls.append(kw)
        if not _loops(env):
            _loop_row(result, "review")
        return {"run_id": REVIEW_RUN, "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)
    return calls


# ── the normal automatic approval (kept) ─────────────────────────────────────

@pytest.mark.parametrize("admin", [True, False])
def test_review_none_is_approved_automatically_for_an_admin_or_a_non_admin_issuer(env, decided, monkeypatch, admin):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    if not admin:
        _non_admin(monkeypatch)
    _work_run_ended()
    assert (_node(env, key), _review_status(result)) == (("done", result), "approved")
    shown = _node_run(env, key)
    assert (shown["awaiting_approval"], shown["approval_wait_reason"]) == (False, None)
    assert _waits(env) == [] and _authorities(env) == [(result, "system", "u_kim")]


@pytest.mark.parametrize("admin", [True, False])
def test_a_passed_review_is_approved_automatically_for_an_admin_or_a_non_admin_issuer(env, decided, monkeypatch, admin):
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    if not admin:
        _non_admin(monkeypatch)
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped", "stop_reason": "review_passed"}})
    assert (_node(env, key)[0], _review_status(result)) == ("done", "approved")
    assert _waits(env) == [] and _authorities(env) == [(result, "system", "u_kim")]


# ── a person's judgement (kept, told apart) ──────────────────────────────────

@pytest.mark.parametrize("stop_reason", ["review_count_exhausted", "review_verdict_hold", "review_stalled"])
def test_a_review_that_did_not_pass_waits_for_a_person_and_tells_why(env, decided, monkeypatch, stop_reason):
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped", "stop_reason": stop_reason}})
    shown = _node_run(env, key)
    assert (shown["awaiting_approval"], shown["approval_wait_reason"]) == (True, f"review_not_passed:{stop_reason}")
    assert _waits(env) == [(result, f"review_not_passed:{stop_reason}")] and _authorities(env) == []


def test_an_ai_run_that_timed_out_waits_for_a_person_and_tells_why(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    _work_run_ended(end_reason="timeout", stop_code="timeout")
    shown = _node_run(env, key)
    assert (shown["awaiting_approval"], shown["approval_wait_reason"]) == (True, "run_not_clean:timeout")
    assert _review_status(result) == "pending_review"


# ── a missed or failed automatic approval ────────────────────────────────────

def test_a_refused_transition_is_recorded_and_the_card_waits(env, decided, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service

    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)

    def refuse(**_kw):
        raise pipeline_service.TransitionError("expected_revision")

    monkeypatch.setattr(pipeline_service, "transition_document_review", refuse)
    _work_run_ended()
    shown = _node_run(env, key)
    assert (shown["awaiting_approval"], shown["approval_wait_reason"]) == (True, "approval_refused")
    _current(env)                                                        # read again: nothing more is tried
    assert _waits(env) == [(result, "approval_refused")] and _authorities(env) == []


def test_no_issuer_approves_nothing(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    _work_run_ended(issued_to="")
    assert _node_run(env, key)["approval_wait_reason"] == "issuer_missing"
    assert _review_status(result) == "pending_review"


def test_a_review_that_cannot_start_is_recorded_and_never_approved(env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 1), ("instruction", "omit")))
    monkeypatch.setattr(legacy_adapter, "default_reviewer", lambda project_id: None)   # no reviewer can be chosen
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    _lost_run_end(monkeypatch, env, key)
    for _ in range(2):                                                   # ④-a: read again and again
        shown = _node_run(env, key)
        assert (shown["awaiting_approval"], shown["approval_wait_reason"]) == (True, "review_not_started")
    _stored(env, key, "review_count", "0")                               # set to 'none' afterwards: still nothing
    _current(env)
    assert calls == [] and _review_status(result) == "pending_review"
    assert _waits(env) == [(result, "review_not_started")] and _loops(env) == []


# ── B-3: a lost run end, reconciled once ─────────────────────────────────────

@pytest.mark.parametrize("stored", [None, 0])
@pytest.mark.parametrize("admin", [True, False])
def test_a_lost_run_end_of_a_review_none_card_is_approved_once_by_a_run_read(env, decided, monkeypatch, stored, admin):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    if stored is not None:
        _stored(env, key, "review_count", json.dumps(stored))
    if not admin:
        _non_admin(monkeypatch)
    _lost_run_end(monkeypatch, env, key)
    assert _node_run(env, key)["status"] == "done"
    assert (_node(env, key), _review_status(result)) == (("done", result), "approved")
    before = _events(env)
    _current(env)
    assert _events(env) == before                                        # read again: no approval, no event
    assert _authorities(env) == [(result, "system", "u_kim")] and _waits(env) == []


def test_a_lost_approval_of_a_passed_review_is_made_once_and_the_loop_is_kept(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result)
    _verdict(monkeypatch, True)
    loops = _loops(env)
    _lost_run_end(monkeypatch, env, key)
    assert _node_run(env, key)["status"] == "done" and _review_status(result) == "approved"
    before = _events(env)
    _current(env)
    assert _events(env) == before and _loops(env) == loops               # round_no / attempts_used unchanged
    assert _authorities(env) == [(result, "system", "u_kim")]


def test_a_lost_run_end_before_the_review_starts_the_review_once_and_approves_nothing(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    calls = _review_starts(monkeypatch, env, result)
    _lost_run_end(monkeypatch, env, key)
    for _ in range(3):
        shown = _node_run(env, key)
        assert shown["status"] == "review" and _review_status(result) == "pending_review"
    assert len(calls) == 1 and calls[0]["doc_ref"] == result and calls[0]["action_scope"] == "review"
    assert _authorities(env) == [] and _waits(env) == []


def test_a_lost_run_end_whose_review_cannot_start_is_recorded_once(env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 1), ("instruction", "omit")))
    monkeypatch.setattr(legacy_adapter, "default_reviewer", lambda project_id: None)
    calls = _recorded_start_run(monkeypatch)
    _lost_run_end(monkeypatch, env, key)
    for _ in range(3):
        assert _node_run(env, key)["approval_wait_reason"] == "review_not_started"
    assert calls == [] and _waits(env) == [(result, "review_not_started")] and _review_status(result) == "pending_review"


def test_a_review_still_working_is_never_approved_even_when_no_ai_is_told_working(env, decided, monkeypatch):
    from flowgate.card_settings import review_stage

    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _loop_row(result, "rework")
    monkeypatch.setattr(review_stage, "ai_working", lambda *_a: False)  # ④-b: the two judgements disagree
    _lost_run_end(monkeypatch, env, key)
    _current(env)
    _current(env)
    assert _review_status(result) == "pending_review" and _waits(env) == [] and _authorities(env) == []


@pytest.mark.parametrize("stop_reason", ["review_count_exhausted", "review_verdict_hold", "retry_exhausted",
                                         "total_timeout", "restart_orphaned", "review_stalled"])
def test_a_lost_run_end_of_a_review_that_did_not_pass_records_it_once(env, decided, monkeypatch, stop_reason):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result, stop_reason)
    loops = _loops(env)
    calls = _recorded_start_run(monkeypatch)
    _lost_run_end(monkeypatch, env, key)
    _current(env)
    shown = _node_run(env, key)
    assert (shown["awaiting_approval"], shown["approval_wait_reason"]) == (True, f"review_not_passed:{stop_reason}")
    assert _waits(env) == [(result, f"review_not_passed:{stop_reason}")]
    assert calls == [] and _loops(env) == loops and _review_status(result) == "pending_review"


@pytest.mark.parametrize("raw", ["{bad", '"1"', "5"])
def test_a_review_count_that_cannot_be_read_approves_nothing(env, decided, monkeypatch, raw):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _stored(env, key, "review_count", raw)
    _lost_run_end(monkeypatch, env, key)
    _current(env)
    assert _node_run(env, key)["approval_wait_reason"] == "review_result_unknown"
    assert _waits(env) == [(result, "review_result_unknown")] and _review_status(result) == "pending_review"


@pytest.mark.parametrize("case", ["earlier_attempt", "earlier_revision"])
def test_a_pass_that_is_not_the_current_one_approves_nothing(env, decided, monkeypatch, case):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result)
    _verdict(monkeypatch, case != "earlier_revision")
    _lost_run_end(monkeypatch, env, key, review_exec=case != "earlier_attempt")
    _current(env)
    _current(env)
    assert _waits(env) == [(result, "review_result_unknown")] and _review_status(result) == "pending_review"


def test_a_review_none_card_whose_result_has_a_loop_approves_nothing(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result)
    _stored(env, key, "review_count", "0")                               # set to 'none' after its review started
    _lost_run_end(monkeypatch, env, key)
    _current(env)
    assert _waits(env) == [(result, "review_result_unknown")] and _review_status(result) == "pending_review"


def test_a_read_that_fails_approves_nothing_and_is_not_tried_again(env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result)

    def broken(doc_id):
        raise RuntimeError("loop read failed")

    monkeypatch.setattr(legacy_adapter, "review_loops", broken)
    _lost_run_end(monkeypatch, env, key)
    _current(env)
    _current(env)
    assert _waits(env) == [(result, "review_result_unknown")] and _review_status(result) == "pending_review"


@pytest.mark.parametrize("ending", [{"end_reason": "timeout", "stop_code": "timeout"}, {"end_reason": "cancelled"}])
def test_a_result_whose_ai_run_did_not_end_on_its_own_is_never_reconciled(env, decided, monkeypatch, ending):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    _lost_run_end(monkeypatch, env, key, **ending)
    _current(env)
    assert _review_status(result) == "pending_review" and _waits(env) == [] and _authorities(env) == []


def test_every_existing_record_is_kept_and_only_added_to(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    before = _events(env)
    history = _sql(env["db"], "SELECT rejection_history FROM documents WHERE doc_id = ?", (result,))[0]["rejection_history"]
    _lost_run_end(monkeypatch, env, key)
    _current(env)
    after = _events(env)
    assert after[:len(before)] == before                                 # nothing changed, nothing removed
    assert _sql(env["db"], "SELECT rejection_history FROM documents WHERE doc_id = ?",
                (result,))[0]["rejection_history"] == history


# ── B-3: a lost instruction stage end on an idle run (0006-TR rev0 review) ──

def _run_status(env):
    row = _sql(env["db"], "SELECT status, idle_reason FROM graph_runs WHERE group_id = ? ORDER BY rowid DESC",
               (FA,))[0]
    return row["status"], row["idle_reason"]


def _stage(env, key):
    row = _sql(env["db"], "SELECT status, stage FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    return row["status"], row["stage"]


def _refusals(env):
    return [r["reason"] for r in _sql(env["db"], "SELECT reason FROM graph_run_events WHERE cause = 'attempt_inactive' "
                                                 "ORDER BY event_seq")]


def _stopped(env):
    """The run a Stop left idle (its stored state once its executions ended: ``idle_reason`` stopped). A Stop is taken
    only on a running run (``run_not_running`` otherwise), so the idle run here gets the state it leaves."""
    _sql(env["db"], "UPDATE graph_runs SET idle_reason = 'stopped' WHERE group_id = ? AND status = 'idle'", (FA,))
    assert _run_status(env) == ("idle", "stopped")


def _result_jobs(env):
    return [job for job in env["jobs"] if job.get("stage") == "result"]


def _instruction_lost(env, monkeypatch, settings=(), *, current_attempt=True):
    """An AI card with its instruction used and ``settings`` runs: its instruction AI writes the card's instruction into
    the line's empty document (an edit start) and ends on its own, but its run-end stage never follows it (``on_run_ended``
    lost); its execution is over and the run, with nothing working any more, goes idle. The durable row of that AI run
    (``ai_run_record``) is a mock: it ran for the node's current attempt unless ``current_attempt`` is False; a review
    run's row (``REVIEW_RUN``) runs for it too. -> (node key, the instruction)."""
    from flowgate.card_settings import legacy_adapter
    from test_fg_card_settings import AI, _run_one_card, _start, setting
    from test_fg_card_settings_review import (
        INSTRUCTION_RUN, _admin_and_reviewer, _executions_ended, _start_ai, _write_written,
    )

    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    for field, value in settings:
        setting(env, FA, key, field, value)
    _start(env)
    [job] = env["jobs"]
    assert (job["stage"], job["action_scope"]) == ("instruction", "edit")
    _start_ai(monkeypatch, job, INSTRUCTION_RUN)
    instruction = job["target_doc_id"]
    _write_written(instruction)
    env["jobs"].clear()
    exec_id = _active_exec(env, key)
    real = legacy_adapter.ai_run_record
    rows = {INSTRUCTION_RUN: {"run_id": INSTRUCTION_RUN, "group_id": FA, "doc_ref": instruction, "action_scope": "edit",
                              "mode": "single", "issued_to": "u_kim", "provider_id": "aip_assignee",
                              "end_reason": "exited", "stop_code": None,
                              "graph_exec_id": exec_id if current_attempt else None, "document_review_loop": None},
            REVIEW_RUN: {"run_id": REVIEW_RUN, "group_id": FA, "action_scope": "review", "mode": "single",
                         "issued_to": "u_kim", "graph_exec_id": exec_id,
                         "document_review_loop": {"current_stage": "stopped"}}}
    monkeypatch.setattr(legacy_adapter, "ai_run_record", lambda rid: dict(rows[rid]) if rid in rows else real(rid))
    _executions_ended()
    legacy_adapter.settle_graph_run(FA)
    assert _run_status(env)[0] == "idle" and _stage(env, key) == ("open", "instruction")
    assert _review_status(instruction) != "approved" and env["jobs"] == []
    return key, instruction


def _instruction_passed(env, instruction):
    """The instruction's review loop passed (its row; the stage that would approve it was lost too)."""
    _loop_row(instruction, "review")
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', stop_reason = 'review_passed', "
                    "round_no = 1, attempts_used = 1 WHERE run_id = ?", (REVIEW_RUN,))


def _takes_its_result_stage_once(env, key, instruction):
    """The node moved to its result stage once, its AI write requested with the instruction attached, the idle run woken
    to running; reading again and again changes nothing (no second start, no stall, no new event)."""
    assert _review_status(instruction) == "approved" and _stage(env, key) == ("open", "result")
    [job] = _result_jobs(env)
    assert job["ref_doc_ids"] == [instruction] and _run_status(env)[0] == "running"
    before = _events(env)
    for _ in range(2):
        _current(env)
    assert _result_jobs(env) == [job] and _stage(env, key) == ("open", "result") and _events(env) == before
    assert _refusals(env) == [] and _waits(env) == []


@pytest.mark.parametrize("stored", [None, 0])
def test_a_lost_instruction_end_on_an_idle_run_is_approved_and_takes_its_result_stage_once(env, decided, monkeypatch,
                                                                                            stored):
    settings = () if stored is None else (("instruction_review_count", stored),)
    key, instruction = _instruction_lost(env, monkeypatch, settings)
    _current(env)
    _takes_its_result_stage_once(env, key, instruction)
    assert _authorities(env) == [(instruction, "system", "u_kim")]


def test_a_lost_approval_of_a_passed_instruction_review_on_an_idle_run_takes_its_result_stage_once(env, decided,
                                                                                                   monkeypatch):
    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", 1),))
    _instruction_passed(env, instruction)
    _verdict(monkeypatch, True)
    loops = _loops(env)
    _current(env)
    _takes_its_result_stage_once(env, key, instruction)
    assert _authorities(env) == [(instruction, "system", "u_kim")] and _loops(env) == loops


def test_an_approved_instruction_whose_result_stage_was_lost_takes_it_once_on_an_idle_run(env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    key, instruction = _instruction_lost(env, monkeypatch)
    db_documents.update(instruction, {"doc_review_status": "approved"})   # approved; the result stage's start lost
    _current(env)
    _takes_its_result_stage_once(env, key, instruction)
    assert _authorities(env) == []                                       # approved once, not again


def test_a_lost_instruction_end_that_needs_its_review_starts_it_once_on_an_idle_run(env, decided, monkeypatch):
    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", 1),))
    calls = _review_starts(monkeypatch, env, instruction)
    for _ in range(3):
        _current(env)
    assert len(calls) == 1 and calls[0]["doc_ref"] == instruction and calls[0]["action_scope"] == "review"
    assert _run_status(env)[0] == "running"                              # the explicit request woke the idle run
    assert _review_status(instruction) != "approved" and _stage(env, key) == ("open", "instruction")
    assert _refusals(env) == [] and _waits(env) == [] and _authorities(env) == [] and _result_jobs(env) == []


@pytest.mark.parametrize("failure", ["no_reviewer", "start_refused"])
def test_a_lost_instruction_review_that_cannot_start_on_an_idle_run_is_recorded_once(env, decided, monkeypatch, failure):
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.services import ai_invoke_service

    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", 1),))
    starts = []
    if failure == "no_reviewer":
        monkeypatch.setattr(legacy_adapter, "default_reviewer", lambda project_id: None)

    def refuse(**kw):
        starts.append(kw)
        raise RuntimeError("admission refused")

    monkeypatch.setattr(ai_invoke_service, "start_run", refuse)
    for _ in range(3):
        _current(env)
    assert len(starts) == (0 if failure == "no_reviewer" else 1)
    assert _waits(env) == [(instruction, "review_not_started")] and _refusals(env) == []
    assert _review_status(instruction) != "approved" and _stage(env, key) == ("open", "instruction")
    assert _result_jobs(env) == []


@pytest.mark.parametrize("closed", ["stopped", "time_machine"])
@pytest.mark.parametrize("review", [0, 1])
def test_a_stopped_or_rewinding_idle_run_reconciles_no_instruction_stage(env, decided, monkeypatch, closed, review):
    from flowgate.graph_time_machine import operation

    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", review),))
    calls = _review_starts(monkeypatch, env, instruction)
    if closed == "stopped":
        _stopped(env)
    else:
        monkeypatch.setattr(operation, "busy", lambda flow_id: True)
    before = _events(env)
    for _ in range(2):
        _current(env)
    assert calls == [] and _result_jobs(env) == [] and _review_status(instruction) != "approved"
    assert _stage(env, key) == ("open", "instruction") and _events(env)[len(before):] == []


@pytest.mark.parametrize("review", [0, 1])
def test_a_stop_that_commits_before_the_hold_refuses_the_reconciled_instruction_stage(env, decided, monkeypatch, review):
    """The Stop commits between the read's own judgement and the Stop gate's hold: the hold judges the run again and
    refuses (recorded) — no approval, no result stage, no review."""
    from flowgate.card_settings import legacy_adapter

    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", review),))
    calls = _review_starts(monkeypatch, env, instruction)
    _stopped(env)
    monkeypatch.setattr(legacy_adapter, "person_stage_open", lambda flow_id, run_row: True)   # the read's stale view
    _current(env)
    assert calls == [] and _result_jobs(env) == [] and _review_status(instruction) != "approved"
    assert _stage(env, key) == ("open", "instruction") and _authorities(env) == []
    assert "run_stopped" in _refusals(env)


@pytest.mark.parametrize("review", [0, 1])
def test_an_instruction_run_of_an_earlier_attempt_is_never_reconciled(env, decided, monkeypatch, review):
    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", review),),
                                         current_attempt=False)
    calls = _review_starts(monkeypatch, env, instruction)
    before = _events(env)
    _current(env)
    assert calls == [] and _result_jobs(env) == [] and _review_status(instruction) != "approved"
    assert _stage(env, key) == ("open", "instruction") and _events(env)[len(before):] == []


# ── B-6: viewer.can_approve, the approval route's own judgement ──────────────

def test_the_run_tells_the_viewer_whether_the_approval_route_would_take_its_approval(env, decided, monkeypatch):
    from flowgate.card_approval import legacy_adapter as approvals

    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result, "review_count_exhausted")                  # H1: a person decides
    _lost_run_end(monkeypatch, env, key)
    assert _current(env)["viewer"] == {"can_approve": True}              # an admin
    real = approvals.permissions_of
    monkeypatch.setattr(approvals, "permissions_of", lambda user_id: {"document.read"})
    assert _current(env)["viewer"] == {"can_approve": False}
    assert _approve(env, key, expect=403)["error"]["code"] == "approval_forbidden"
    monkeypatch.setattr(approvals, "permissions_of", real)
    body = _approve(env, key)
    assert body["run"]["viewer"] == {"can_approve": True} and _node(env, key)[0] == "done"
