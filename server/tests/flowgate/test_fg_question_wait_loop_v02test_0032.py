"""flowgate.v02test.0032: a question a review loop's rework AI asked is answered even when the loop ran out of reviews.

0029 Q #628: the rework AI asked inside an assigned result review loop, the loop went on and stopped with
``review_count_exhausted``; ``assigned_review.consume_loop_verdict`` held the verdict for the answer, but
``question_wait._waiting`` only took a passed loop, so no responder was dispatched and an answer moved nothing.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

from flowgate.card_settings import question_wait as wait
from flowgate.card_settings import stage_assignment as sa

FLOW = "flow"
NODE = {"node_key": "card", "node_type": "document", "doc_type": "T"}
REF = ("attempt-1", sa.RESULT_REVIEW, 1)
INVOCATIONS = [
    {"inv_role": "review", "actual_ai_id": "reviewer", "launch_no": 1},
    {"inv_role": "execution", "actual_ai_id": "worker", "launch_no": 2},
    {"inv_role": "review", "actual_ai_id": "reviewer", "launch_no": 3},
]


def _loop_run(stop_reason="review_count_exhausted"):
    return {"run_id": "loop-run", "group_id": FLOW, "doc_ref": "doc-T", "graph_exec_id": "exec-1",
            "provider_id": "reviewer", "issued_to": "owner", "invocations": list(INVOCATIONS),
            "document_review_loop": {"current_stage": "stopped", "stop_reason": stop_reason}}


@pytest.fixture
def held(monkeypatch):
    state = {"assignment": {"actor": sa.AI, "verdict": None, "seq": 1}, "latest": {"seq": 1}, "legacy": False,
             "active": False, "ref": REF}
    monkeypatch.setattr(wait.legacy, "instruction_node_of_document", lambda fid, doc: None)
    monkeypatch.setattr(wait.legacy, "node_of_result", lambda fid, doc: NODE)
    monkeypatch.setattr(wait.legacy, "review_loop_active", lambda doc: state["active"])
    monkeypatch.setattr(wait.legacy, "assignment_of_exec", lambda exec_id: state["ref"])
    monkeypatch.setattr(sa, "is_legacy_attempt", lambda attempt_id: state["legacy"])
    monkeypatch.setattr(sa.store, "read", lambda *a: state["assignment"])
    monkeypatch.setattr(sa, "current_review_assignment", lambda attempt_id, stage: state["latest"])
    # flowgate.v02test.0051 T0005: no parked mid-loop wait unless a test stores one
    from flowgate.card_settings import question_loop_wait
    state["wait"] = None
    monkeypatch.setattr(question_loop_wait, "wait_of_run", lambda run_id: state["wait"])
    monkeypatch.setattr(question_loop_wait, "resume_due", lambda wait: wait["status"] == "waiting")
    return state


@pytest.mark.parametrize("stop", ["review_count_exhausted", "review_stalled", "review_verdict_hold"])
def test_exhausted_loop_with_held_verdict_waits_as_review(held, stop):
    assert wait._waiting(FLOW, _loop_run(stop)) == ("review", NODE, None)


def test_passed_loop_still_waits_without_reading_the_assignment(held, monkeypatch):
    monkeypatch.setattr(wait.legacy, "assignment_of_exec", lambda exec_id: pytest.fail("not read"))
    assert wait._waiting(FLOW, _loop_run("review_passed")) == ("review", NODE, None)


@pytest.mark.parametrize("change", [
    {"assignment": {"actor": sa.AI, "verdict": "not_passed:review_count_exhausted", "seq": 1}},   # consumed already
    {"latest": {"seq": 2}},                                                  # a newer review assignment took over
    {"assignment": {"actor": sa.PERSON, "verdict": None, "seq": 1}},         # not an AI loop's assignment
    {"assignment": None},
    {"legacy": True},                                                        # pre-feature attempt: no verdict hold
    {"ref": None},
    {"ref": ("attempt-1", sa.RESULT, 0)},
])
def test_not_waiting_when_the_verdict_is_not_held(held, change):
    held.update(change)
    assert wait._waiting(FLOW, _loop_run()) is None


# flowgate.v02test.0051 T0005 (A1): the former ``{"active": True}`` case above. An unstopped loop row alone no longer
# decides: a loop working on the document (no parked wait) is still not a waiting step, but a loop parked at its hop
# boundary for its questions — its row unstopped, the card locked — is the ``loop_wait`` step, never the loop-end
# ``review`` step (whatever its verdict hold says).
def test_an_active_loop_without_a_parked_wait_is_not_waiting(held):
    held.update(active=True)
    assert wait._waiting(FLOW, _loop_run()) is None


@pytest.mark.parametrize("stop", ["review_count_exhausted", "review_passed"])
def test_an_active_loop_parked_for_its_questions_waits_as_loop_wait(held, stop):
    held.update(active=True)
    run = {**_loop_run(stop), "document_review_loop": {"current_stage": "rework", "stop_reason": None}}
    held["wait"] = {"wait_id": "qlw_1", "group_id": FLOW, "status": "waiting", "loop_run_id": "loop-run"}
    assert wait._waiting(FLOW, run) == ("loop_wait", NODE, held["wait"])
    for status in ("resuming", "resumed", "cancelled"):        # resumed once / gone: neither step any more
        held["wait"] = {**held["wait"], "status": status}
        assert wait._waiting(FLOW, run) is None
        assert wait._waiting(FLOW, _loop_run(stop)) is None


def test_unreadable_assignment_is_not_waiting(held, monkeypatch):
    def unreadable(*a):
        raise sa.AssignmentUnreadable("down")
    monkeypatch.setattr(sa.store, "read", unreadable)
    assert wait._waiting(FLOW, _loop_run()) is None


def test_dispatch_waiting_puts_the_rework_question_to_a_responder(held, monkeypatch):
    item = {"id": 628, "asker_ai_run_id": "loop-run", "anchor_doc_id": "doc-T", "asker_actual_provider_id": "worker"}
    dispatched = []
    monkeypatch.setattr(wait.legacy, "flow_ai_busy", lambda fid: False)
    monkeypatch.setattr(wait.legacy, "active_run_id", lambda fid: "graph-run")
    monkeypatch.setattr(wait, "open_questions_in_flow", lambda fid: [item])
    monkeypatch.setattr(wait.legacy, "ai_run_record", lambda run_id: _loop_run())
    monkeypatch.setattr(wait, "_dispatch", lambda fid, node, run, items, by_instr: dispatched.append(
        (node["node_key"], [i["id"] for i in items], by_instr)) or True)
    wait._dispatch_waiting(FLOW)
    assert dispatched == [("card", [628], False)]


def test_answers_let_the_held_verdict_be_consumed(held, monkeypatch):
    from flowgate.card_settings import review_stage
    calls = []
    monkeypatch.setattr(wait.legacy, "ai_run_record", lambda run_id: _loop_run())
    monkeypatch.setattr(wait, "open_questions", lambda fid, run_id: [])
    monkeypatch.setattr(wait.legacy, "wake_graph_run", lambda fid: True)
    monkeypatch.setattr(wait.legacy, "settle_graph_run", lambda fid: calls.append("settle"))
    monkeypatch.setattr(review_stage, "_after_review", lambda fid, run, answered=False: calls.append(
        ("after_review", run["run_id"], answered)))
    wait._answered(FLOW, "loop-run")
    assert calls == [("after_review", "loop-run", True), "settle"]


def test_loop_question_role_follows_the_asker_not_the_last_hop():
    run = _loop_run()
    assert wait._asking_role(run, {"asker_actual_provider_id": "worker"}, False) == "execution"
    assert wait._asking_role(run, {"asker_actual_provider_id": "reviewer"}, False) == "review"


def test_loop_question_role_falls_back_when_the_asker_is_ambiguous_or_unknown():
    run = {**_loop_run(), "invocations": [{"inv_role": "review", "actual_ai_id": "same", "launch_no": 1},
                                          {"inv_role": "execution", "actual_ai_id": "same", "launch_no": 2},
                                          {"inv_role": "review", "actual_ai_id": "same", "launch_no": 3}]}
    assert wait._asking_role(run, {"asker_actual_provider_id": "same"}, False) == "review"   # the last hop, as before
    assert wait._asking_role(_loop_run(), {}, False) == "review"


def test_dispatch_hands_the_rework_role_to_the_candidates(monkeypatch):
    from flowgate.ai_roles import qa_adapter
    seen = []
    monkeypatch.setattr(wait.legacy, "flow_ai_busy", lambda fid: False)
    monkeypatch.setattr(qa_adapter, "candidates", lambda fid, node, asker, role: seen.append((asker, role)) or (
        [], "responder_not_designated", False, "qa_new"))
    monkeypatch.setattr(wait.legacy, "mark_question_responder_failed", lambda *a: True)
    item = {"id": 628, "anchor_doc_id": "doc-T", "asker_actual_provider_id": "worker"}
    assert wait._dispatch(FLOW, NODE, _loop_run(), [item], False) is False
    assert seen == [("worker", "execution")]
