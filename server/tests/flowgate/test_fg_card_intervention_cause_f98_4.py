"""flowgate.v02test.F98-4: a card in intervention carries the closed review loop's failure cause (``stop_detail``).

The intervention keeps ``kind`` / ``reason``; ``stop_detail`` is added only when the latest stopped loop of the card's
stage document stopped with the very stop reason recorded (``review_not_passed:<stop reason>``) and told a cause.
"""
import pytest

from flowgate.card_intervention import judge, reasons
from flowgate.card_settings import action_guard, legacy_adapter

ROW = {"node_key": "gn_a", "run_id": "gr_1", "status": "review", "result_doc_id": "doc-TR"}


@pytest.fixture
def card(monkeypatch):
    state = {"reason": None, "loops": [], "reads": []}

    def loops(doc_id):
        state["reads"].append(doc_id)
        if isinstance(state["loops"], Exception):
            raise state["loops"]
        return state["loops"]

    monkeypatch.setattr(action_guard, "node_reason", lambda _flow, _row: None)
    monkeypatch.setattr(legacy_adapter, "approval_wait_reason", lambda _flow, _row: state["reason"])
    monkeypatch.setattr(legacy_adapter, "review_loops", loops)
    return state


def _loop(stop_reason, detail, stage="stopped"):
    return {"current_stage": stage, "stop_reason": stop_reason, "stop_detail": detail}


def test_the_closed_loop_cause_is_carried_as_an_added_field(card):
    card["reason"] = "review_not_passed:retry_exhausted"
    card["loops"] = [_loop("retry_exhausted", "  model_not_found  ")]
    assert judge.intervention("f", ROW) == {"kind": "needs_intervention", "reason": "review_not_passed:retry_exhausted",
                                            "stop_detail": "model_not_found"}
    assert card["reads"] == ["doc-TR"]


@pytest.mark.parametrize("loops", [
    [],                                                        # no loop row
    [_loop("retry_exhausted", None)],                          # the loop told no cause
    [_loop("retry_exhausted", "   ")],
    [_loop("total_timeout", "model_not_found")],               # the latest stop is not the recorded one
    [_loop("total_timeout", "x"), _loop("retry_exhausted", "model_not_found")],   # an older loop's cause is not shown
])
def test_no_cause_keeps_the_two_fields(card, loops):
    card["reason"] = "review_not_passed:retry_exhausted"
    card["loops"] = loops
    assert judge.intervention("f", ROW) == {"kind": "needs_intervention", "reason": "review_not_passed:retry_exhausted"}


def test_a_running_loop_before_the_stopped_one_is_skipped(card):
    card["reason"] = "review_not_passed:review_count_exhausted"
    card["loops"] = [_loop(None, None, stage="review"), _loop("review_count_exhausted", "spent")]
    assert judge.intervention("f", ROW)["stop_detail"] == "spent"


@pytest.mark.parametrize("reason,kind", [(None, "needs_intervention"), ("run_not_clean:timeout", "needs_intervention"),
                                         ("approval_refused", "system_error")])
def test_other_reasons_read_no_loop(card, reason, kind):
    card["reason"] = reason
    card["loops"] = [_loop("retry_exhausted", "model_not_found")]
    assert judge.intervention("f", ROW) == {"kind": kind, "reason": reason or "completion_missing"}
    assert card["reads"] == []


def test_a_failed_loop_read_still_answers_the_intervention(card):
    card["reason"] = "review_not_passed:retry_exhausted"
    card["loops"] = RuntimeError("db down")
    assert judge.intervention("f", ROW) == {"kind": "needs_intervention", "reason": "review_not_passed:retry_exhausted"}


def test_a_held_card_carries_no_cause(card, monkeypatch):
    monkeypatch.setattr(action_guard, "node_reason", lambda _flow, _row: "action_required:new_attempt")
    card["reason"] = "review_not_passed:retry_exhausted"
    card["loops"] = [_loop("retry_exhausted", "model_not_found")]
    assert judge.intervention("f", ROW) == {"kind": "needs_intervention", "reason": "action_required:new_attempt"}


def test_the_reason_helpers():
    assert reasons.loop_stop_reason("review_not_passed:retry_exhausted") == "retry_exhausted"
    assert [reasons.loop_stop_reason(r) for r in (None, "", "review_not_passed:", "run_not_clean:x", 3)] == [None] * 5
    assert reasons.stop_detail_text(" model_not_found ") == "model_not_found"
    assert [reasons.stop_detail_text(v) for v in (None, "", "  ", 7)] == [None] * 4
    assert len(reasons.stop_detail_text("x" * 1000)) == reasons.STOP_DETAIL_MAX_CHARS


def test_the_ai_graph_read_keeps_the_cause():
    from flowgate.graph_read import service

    row = {"node_key": "gn_a", "status": "review",
           "intervention": {"kind": "needs_intervention", "reason": "review_not_passed:retry_exhausted",
                            "stop_detail": "model_not_found"}}
    assert service.node_run(row, set())["intervention"] == row["intervention"]
    plain = {**row, "intervention": {"kind": "needs_intervention", "reason": "completion_missing"}}
    assert service.node_run(plain, set())["intervention"] == {"kind": "needs_intervention", "reason": "completion_missing"}


def test_the_node_judgement_passes_the_cause_through(monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_settings_ports, node_approval

    found = {"kind": "needs_intervention", "reason": "review_not_passed:retry_exhausted", "stop_detail": "model_not_found"}
    monkeypatch.setattr(card_settings_ports, "intervention", lambda _flow, _row: dict(found))
    assert node_approval._state("f", ROW) == found
    monkeypatch.setattr(card_settings_ports, "intervention", lambda _flow, _row: {**found, "stop_detail": None})
    assert node_approval._state("f", ROW) == {"kind": "needs_intervention", "reason": "review_not_passed:retry_exhausted"}
