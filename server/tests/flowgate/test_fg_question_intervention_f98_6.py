"""F98-6: questions outrank stale loop failures only in their current scope."""
from types import SimpleNamespace
import pytest
from flowgate.card_intervention import judge
from flowgate.card_settings import legacy_adapter as legacy, question_wait, action_guard
from flowgate.graph_run import settle
from modules.flow_gate.graph_workflow.services import node_approval, card_settings_ports

ROW = {"run_id": "graph", "node_key": "card", "status": "review", "result_doc_id": "result"}

@pytest.fixture
def waiting(monkeypatch):
    state = {"attempt": "a2", "loop_attempt": "a2", "questions": [{"id": 1}],
             "loops": [{"run_id": "loop2", "group_id": "flow", "current_stage": "stopped"}], "docs": []}
    monkeypatch.setattr(legacy.run_store, "active_attempt_id", lambda *a: state["attempt"])
    monkeypatch.setattr(legacy.run_store, "current_attempts", lambda *a: {})
    monkeypatch.setattr(legacy, "_loop_attempt", lambda *a: state["loop_attempt"])
    def loops(doc):
        state["docs"].append(doc)
        return state["loops"]
    monkeypatch.setattr(legacy, "review_loops", loops)
    monkeypatch.setattr(question_wait, "open_questions", lambda *a: state["questions"])
    monkeypatch.setattr(action_guard, "node_reason", lambda *a: "action_required:new_attempt")
    monkeypatch.setattr(legacy, "approval_wait_reason", lambda *a: "review_not_passed:retry_exhausted")
    monkeypatch.setattr(card_settings_ports, "intervention", judge.intervention)
    monkeypatch.setattr(node_approval, "_settled_refusal", lambda *a: None)
    return state

@pytest.mark.parametrize("status,stage,doc", [("review", "result_review", "result"), ("open", "instruction", "instruction")])
def test_question_precedes_action_guard_and_recorded_failure(waiting, status, stage, doc):
    row = {**ROW, "status": status, "stage": stage, "instruction_doc_id": "instruction"}
    assert judge.intervention("flow", row) == {"kind": "question_pending", "reason": "question_pending"}
    assert node_approval.intervention("flow", row, "document") is None
    assert set(waiting["docs"]) == {doc}

@pytest.mark.parametrize("change", [{"questions": []}, {"questions": None}, {"loop_attempt": "a1"},
                                    {"loop_attempt": None}, {"attempt": None}, {"loops": []},
                                    {"loops": [{"run_id": "loop2", "group_id": "other"}]}])
def test_answered_other_attempt_and_unknown_identity_keep_action_guard(waiting, change):
    waiting.update(change)
    assert judge.intervention("flow", ROW) == {"kind": "needs_intervention", "reason": "action_required:new_attempt"}

def test_old_loop_questions_are_not_consulted(waiting, monkeypatch):
    waiting["loops"].append({"run_id": "loop1", "group_id": "flow"})
    monkeypatch.setattr(question_wait, "open_questions", lambda f, r: [] if r == "loop2" else [{"id": 1}])
    assert not judge.question_pending("flow", ROW)

def test_unreadable_loop_does_not_hide_failure(waiting, monkeypatch):
    def fail(*a):
        raise RuntimeError("unreadable")
    monkeypatch.setattr(legacy, "review_loops", fail)
    assert not judge.question_pending("flow", ROW)

@pytest.mark.parametrize("stage", ["stopped", "rework", "review"])
def test_question_is_neither_assignment_intervention_nor_orphan(waiting, monkeypatch, stage):
    waiting["loops"][0]["current_stage"] = stage
    ctx = SimpleNamespace(flow_id="flow", run_id="graph", node_rows={"card": {"node_type": "document"}})
    monkeypatch.setattr(card_settings_ports, "stage_waits", lambda *a: True)
    assert not settle._needs_intervention(ctx, ROW)
    assert not settle._orphaned_review_loop(ctx, ROW)
