"""flowgate.v02.0143 T#1 (W6, R12~R14; NR flowgate.v02.0143.0003 §3, §4.1~4.2) — the command result of a card whose AI asked
a question answered by an automatic Q&A responder.

The counterexample checked (T §2 W6-1): a responder's role invocation record (``origin_kind=card_qa``,
``purpose=inv_role=qa_response``, ``graph_exec_id`` NULL) carries the asker's ``target_graph_run_id`` / attempt / stage / seq
(``ai_roles.qa_adapter.origin``), so ``record_store.for_graph_run`` returns it and it is the asker's command's (by the
stage assignment event its target names). A responder launch that failed (``call_failed`` …) then counted as the card's
failure (``_invocation_failure`` judges any role) and failed the command — while the question went to a person
(``responder_state`` failed / user_decision) and the card only waits for that person.

On the real Graph engine, question wait, run read and command result route of ``test_fg_question_pending_0111`` (an AI
card whose instruction AI asked and ended): the records are written as the launch journal writes them (no AI is called);
the item's responder state is the stand-in ``legacy_adapter.question_responder_state``.

- R14: a failed responder launch whose question went to a person (failed / user_decision, or answered since) is no failure
  of the command: the card waits, the message (with its error) stays;
- the fix is that branch only: the same failed launch while the item is still being answered (``dispatched``) or whose
  item cannot be read still fails the command; next to a handed-over responder, the command is still failed (R13, cause,
  card and message kept) by the card's own failed launch, by a real review failure after the Q&A (case 2 of NR §4.1:
  the review stage's own end records ``review_not_passed:review_count_exhausted``) and by an independent branch's
  ``node_failed`` (its result writer ended with no result);
- R12: a responder that answered and the card that went on (case 1 of NR §4.1) is no failure.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from test_fg_card_intervention_0119 import fork_join  # noqa: F401 — the production branch / join rules (R13 branches)
from test_fg_card_settings import FA, _h, _sql  # noqa: F401
from test_fg_question_pending_0111 import (  # noqa: F401 — fixtures used by name
    INSTRUCTION_RUN, RESPONDER_RUN, _answer, _asked_instruction, _run_id, card_outputs_port, db_path, decided, env,
    legacy_cards, questions,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")

ITEM = 901                                                  # test_fg_question_pending_0111.QUESTION


@pytest.fixture(autouse=True)
def document_write_port_0138(monkeypatch):
    """This older harness seeds documents before any app lifespan registers the document write gate (flowgate.v02.0127):
    the current real gate is registered explicitly, as test_fg_provider_failure_retry_0121 does, and the review harness's
    fixture writes of earlier documents bypass it (not an agent's card edit)."""
    import test_fg_card_settings_review as review_harness
    from flowgate.card_document_edit import write_gate
    from modules.flow_gate.db import documents
    from modules.flow_gate.graph_workflow.services import card_document_ports

    monkeypatch.setitem(card_document_ports._handlers, card_document_ports.DOCUMENT_WRITE_GATE, write_gate.dispatch)
    real_update = documents.update

    def seed_update(doc_id, updates):
        if set(updates) <= {"file_path", "doc_review_status"} and updates.get("file_path") == f"docs/{doc_id}.md":
            return documents._update_document(doc_id, updates)
        return real_update(doc_id, updates)

    def seeded(function):
        def call(*args, **kwargs):
            with monkeypatch.context() as m:
                m.setattr(documents, "update", seed_update)
                return function(*args, **kwargs)
        return call

    import test_fg_question_pending_0111 as pending_harness

    written = seeded(review_harness._write_written)
    monkeypatch.setattr(review_harness, "_write_written", written)
    monkeypatch.setattr(pending_harness, "_write_written", written)         # the name ``_asked_instruction`` calls


# ── helpers ──────────────────────────────────────────────────────────────────

def result(env, run_id):
    res = env["client"].get(f"/api/v1/runs/{run_id}/command-result", headers=_h())
    assert res.status_code == 200, res.text
    return res.json()["command_result"]


def _target(env, key):
    """The asker's stage as its stage assignment event names it (attempt, stage, seq) — the target its record carries and
    a responder's record copies (``qa_adapter.origin``)."""
    rows = _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE cause = 'stage_assigned' ORDER BY event_seq")
    found = [json.loads(r["payload_json"] or "{}") for r in rows]
    found = [d for d in found if d.get("node_key") == key and d.get("attempt_id")]
    assert found, rows
    last = found[-1]
    return last["attempt_id"], last["stage"], last.get("seq")


def _record(env, key, ai_run_id, launch_no, result_value, *, exec_id=None, qa=False, detail=None, target=None):
    """One role invocation record as the launch journal writes it (DB-1 §2.6): the card's own launch
    (``card_assigned``) or an automatic responder's (``card_qa`` / ``qa_response``, no Graph execution, the asker's target)."""
    from flowgate.ai_roles import record_store

    attempt, stage, seq = target or _target(env, key)
    now = record_store._now()
    inv_id = f"{ai_run_id}#{launch_no}"
    rec = {
        "inv_id": inv_id, "create_seq": record_store.issue_create_seq(), "ai_run_id": ai_run_id, "launch_no": launch_no,
        "hop_no": None, "graph_exec_id": exec_id, "origin_kind": "card_qa" if qa else "card_assigned",
        "inv_role": "qa_response" if qa else "instruction", "purpose": "qa_response" if qa else "instruction_write",
        "target_group_id": FA, "target_graph_run_id": _run_id(env), "target_node_key": key,
        "target_attempt_id": attempt, "target_stage": stage, "target_seq": seq, "target_doc_id": None,
        "target_doc_revision_no": None, "requested_ai_id": "aip_review" if qa else "aip_assignee",
        "role_source": "card_review_role" if qa else "card_explicit", "actual_ai_id": "aip_review" if qa else "aip_assignee",
        "actual_ai_name": "AI", "provider_exec_type": "cli", "provider_kind": "claude",
        "requested_model_state": "value", "requested_model_value": "claude-opus-5-5", "requested_model_reason": None,
        "confirmed_model_state": "confirmed", "confirmed_model_values_json": json.dumps(["claude-opus-5-5"]),
        "confirmed_coverage": "full", "result": result_value, "result_detail": detail,
        "exit_code": 0 if result_value == "succeeded" else 1, "link_kind": "answers_question_of" if qa else "first",
        "unit_kind": "qa" if qa else "assignment", "unit_key_hash": hashlib.sha256(inv_id.encode()).hexdigest(),
        "chain_root_id": inv_id, "started_at": now, "ended_at": now, "created_at": now, "updated_at": now,
        **({"qa_item_id": ITEM, "qa_asker_ai_run_id": INSTRUCTION_RUN, "qa_card_class": "qa_new"} if qa else {}),
    }
    assert record_store.write_record(rec)
    return rec


@pytest.fixture()
def items(monkeypatch):
    """The Q&A items' responder state as stored (``legacy_adapter.question_responder_state``): ``{item id: state}``; an
    item set to an exception cannot be read."""
    from flowgate.graph_run import legacy_adapter as engine

    states: dict = {}

    def read(item_id):
        value = states.get(int(item_id))
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(engine, "question_responder_state", read, raising=False)
    return states


def _asked_with_records(env, monkeypatch, questions, responder="call_failed"):
    """The card's instruction AI asked and ended (its own launch recorded, succeeded); its responder's launch ended
    ``responder``. -> (node key, run id)."""
    key, _doc, exec_id = _asked_instruction(env, monkeypatch, questions)
    _record(env, key, INSTRUCTION_RUN, 1, "succeeded", exec_id=exec_id)
    _record(env, key, RESPONDER_RUN, 1, responder, qa=True, detail=None if responder == "succeeded" else "exit_nonzero")
    return key, _run_id(env)


def _responder_message(r):
    return next(m for m in r["messages"] if m["kind"] == "invocation" and m["inv_id"] == f"{RESPONDER_RUN}#1")


# ── R14: the responder's failure handed to a person is the card's wait, not the command's failure ─────────────

@pytest.mark.parametrize("state", ["failed", "user_decision", "answered"])
def test_a_failed_responder_whose_question_went_to_a_person_does_not_fail_the_command(env, decided, monkeypatch,
                                                                                     questions, items, state):
    key, run_id = _asked_with_records(env, monkeypatch, questions)
    items[ITEM] = state
    r = result(env, run_id)
    assert r["ended"] is True
    assert r["state"] != "failed" and r["failure"] is None
    assert [f for f in r["failures"] if f["code"] == "call_failed"] == []
    assert {n["node_key"]: n["outcome"] for n in r["nodes"]}[key] == "waiting"           # the card waits
    message = _responder_message(r)                                                      # the message stays
    assert (message["result"], message["error"], message["purpose"]) == ("call_failed", True, "qa_response")
    assert "exit_nonzero" in message["error_text"]


def test_the_responder_record_is_the_asker_s_command_s(env, decided, monkeypatch, questions, items):
    """The counterexample's premise: the responder record of no Graph execution reaches this command's records."""
    key, run_id = _asked_with_records(env, monkeypatch, questions)
    items[ITEM] = "failed"
    message = _responder_message(result(env, run_id))
    assert (message["node_key"], message["attribution"]) == (key, "event")


# ── the fix is that branch only ─────────────────────────────────────────────────────────────────────────────

def test_a_failed_responder_still_being_answered_or_unreadable_still_fails_the_command(env, decided, monkeypatch,
                                                                                    questions, items):
    key, run_id = _asked_with_records(env, monkeypatch, questions)
    for state in ("dispatched", None, RuntimeError("db down")):
        items[ITEM] = state
        r = result(env, run_id)
        assert r["state"] == "failed", state
        assert (r["failure"]["node_key"], r["failure"]["code"]) == (key, "call_failed")


def test_r13_the_card_s_own_failed_launch_next_to_a_handed_over_responder_still_fails_the_command(
        env, decided, monkeypatch, questions, items):
    key, run_id = _asked_with_records(env, monkeypatch, questions)
    items[ITEM] = "user_decision"
    _record(env, key, INSTRUCTION_RUN, 2, "call_failed", detail="timeout")               # the asker's own launch
    r = result(env, run_id)
    assert r["state"] == "failed"
    assert (r["failure"]["node_key"], r["failure"]["code"], r["failure"]["detail"]) == (key, "call_failed", "timeout")
    assert r["failure"]["role"] == "instruction"


NOT_PASSED = "review_not_passed:review_count_exhausted"   # NR §4.1 case 2 (#41): the review failure after the Q&A
INSTRUCTION_REVIEW_RUN = "air_card_instruction_review"


def _failure_events(r):
    return [(m["code"], m["detail"], m["node_key"]) for m in r["messages"]
            if m["kind"] == "event" and m["event"] == "failure"]


@pytest.mark.parametrize("state", ["user_decision", "answered"])
def test_r13_a_real_review_failure_after_the_q_and_a_next_to_a_handed_over_responder_still_fails_the_command(
        env, decided, monkeypatch, questions, items, state):
    """R13 (T §3), case 2 of NR §4.1: the card's written instruction asked; its responder's launch failed and the question
    went to a person (``state``) — then the card's instruction review loop ended without passing (its count spent). The
    review stage's own end (``review_stage.on_run_ended`` -> ``_after_review`` -> ``record_approval_wait``, the B-1
    ``auto_approval_skipped`` run event) records it; only the loop's AI is a stand-in. The command is failed by that
    review failure — its cause, card and message kept — and never by the handed-over responder."""
    from flowgate.card_settings import review_stage

    key, line_doc, exec_id = _asked_instruction(env, monkeypatch, questions, stop_code=None,
                                                body="## 작업\n\n지시 AI가 쓴 지시서\n")
    _record(env, key, INSTRUCTION_RUN, 1, "succeeded", exec_id=exec_id)
    _record(env, key, RESPONDER_RUN, 1, "call_failed", qa=True, detail="exit_nonzero")
    items[ITEM] = state
    run_id = _run_id(env)
    before = result(env, run_id)
    assert before["state"] != "failed" and before["failure"] is None     # the handed-over responder alone: a wait

    review_stage.on_run_ended({"run_id": INSTRUCTION_REVIEW_RUN, "group_id": FA, "doc_ref": line_doc, "mode": "loop",
                               "action_scope": "review", "issued_to": "u_kim", "graph_exec_id": None,
                               "document_review_loop": {"stop_reason": "review_count_exhausted"}})

    r = result(env, run_id)
    assert r["command_seq"] == before["command_seq"]                     # the same command
    assert r["state"] == "failed"
    assert (r["failure"]["node_key"], r["failure"]["code"], r["failure"]["detail"]) == (key, "auto_approval_failed",
                                                                                        NOT_PASSED)
    assert [(f["node_key"], f["code"], f["detail"]) for f in r["failures"]] == [(key, "auto_approval_failed", NOT_PASSED)]
    assert _failure_events(r) == [("auto_approval_failed", NOT_PASSED, key)]                # its message
    card = next(n for n in r["nodes"] if n["node_key"] == key)
    assert (card["outcome"], card["reason"], card["detail"]) == ("failed", "auto_approval_failed", NOT_PASSED)
    message = _responder_message(r)                                      # the responder's message stays, not counted
    assert (message["result"], message["error"]) == ("call_failed", True)


def test_r13_an_independent_branch_s_node_failed_next_to_a_handed_over_responder_still_fails_the_command(
        env, decided, fork_join, monkeypatch, questions, items):
    """R13 (T §3): R -> a, R -> b (two AI cards, one command). a's instruction AI asked and its responder's launch failed —
    the question went to a person (user_decision); b's instruction was written and approved, then its result writer
    ended with no result (``write_failure`` ``no_result``: the run's error family, no failure event of its own — the
    command result's ``node_failed``). The command is failed by b — its cause, card and message kept — and a only
    waits. Every step is the real engine's (run end hooks, settle, run read); the AI runs are the harness's stand-ins."""
    import test_fg_card_settings_review as review_harness
    from test_fg_card_settings import AI, _start, edge, node, put_draft
    from test_fg_card_settings_review import _admin_and_reviewer, _start_ai
    from test_fg_question_pending_0111 import QUESTION, _ai_run_finished, _ended

    _admin_and_reviewer(env)
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T", assignee=AI), node("tmp_b", "T", assignee=AI)],
                      [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_r", "tmp_b")])
    a, b = first["key_map"]["tmp_a"], first["key_map"]["tmp_b"]
    confirmed = env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert confirmed.status_code == 201, confirmed.text
    _start(env)
    jobs = {job["node_key"]: job for job in env["jobs"]}
    env["jobs"].clear()
    assert set(jobs) == {a, b} and {j["stage"] for j in jobs.values()} == {"instruction"}
    other_run, b_work_run = "air_card_instruction_b", "air_card_work_b"

    _start_ai(monkeypatch, jobs[a], INSTRUCTION_RUN)                     # a asks and ends asking
    questions["asked"][INSTRUCTION_RUN] = [dict(QUESTION)]
    _ai_run_finished(env, INSTRUCTION_RUN, jobs[a]["graph_exec_id"], stop_code="question_pending",
                     doc_ref=jobs[a]["target_doc_id"])
    _ended(env, INSTRUCTION_RUN, jobs[a]["graph_exec_id"], action_scope="edit", doc_ref=jobs[a]["target_doc_id"],
           stop_code="question_pending", outcome="none")
    _start_ai(monkeypatch, jobs[b], other_run)                           # b's instruction written and approved
    review_harness._write_written(jobs[b]["target_doc_id"])
    _ai_run_finished(env, other_run, jobs[b]["graph_exec_id"], stop_code=None, doc_ref=jobs[b]["target_doc_id"])
    _ended(env, other_run, jobs[b]["graph_exec_id"], action_scope="edit", doc_ref=jobs[b]["target_doc_id"],
           stop_code=None, outcome="complete")
    [work] = [j for j in env["jobs"] if j["node_key"] == b and j["stage"] == "result"]
    env["jobs"].clear()
    _start_ai(monkeypatch, work, b_work_run)                             # b's result writer ends with no result
    _ai_run_finished(env, b_work_run, work["graph_exec_id"], stop_code=None)
    _ended(env, b_work_run, work["graph_exec_id"], stop_code=None, outcome="none")
    _record(env, a, INSTRUCTION_RUN, 1, "succeeded", exec_id=jobs[a]["graph_exec_id"])
    _record(env, a, RESPONDER_RUN, 1, "call_failed", qa=True, detail="exit_nonzero")
    items[ITEM] = "user_decision"

    r = result(env, _run_id(env))
    assert r["state"] == "failed"
    assert (r["failure"]["node_key"], r["failure"]["code"]) == (b, "node_failed")
    assert [(f["node_key"], f["code"]) for f in r["failures"]] == [(b, "node_failed")]
    assert [(code, key) for code, _detail, key in _failure_events(r)] == [("node_failed", b)]
    shown = {n["node_key"]: (n["outcome"], n["reason"]) for n in r["nodes"]}
    assert shown[b] == ("failed", "node_failed") and shown[a][0] == "waiting"
    assert r["counts"]["failed"] == 1
    assert _responder_message(r)["error"] is True


# ── R12: a responder that answered and the card that went on ──────────────────────────────────────────────────

def test_r12_a_responder_that_answered_and_the_card_that_went_on_is_no_failure(env, decided, monkeypatch, questions,
                                                                               items):
    key, run_id = _asked_with_records(env, monkeypatch, questions, responder="succeeded")
    items[ITEM] = "answered"
    _answer(questions, INSTRUCTION_RUN, "ai")                                            # the work resumed (NR §4.1 #18)
    r = result(env, run_id)
    assert r["state"] != "failed" and r["failures"] == []
    assert _responder_message(r)["error"] is False
