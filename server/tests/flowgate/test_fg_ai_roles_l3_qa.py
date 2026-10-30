"""L-3 Q/N truth tables and immutable asker targets."""
from types import SimpleNamespace as NS
import pytest
from flowgate.ai_roles import qa_adapter as qa, resolve, role_store
from modules.flow_gate.services.ai_invoke import review, finalize

NODE = {"node_key": "n", "node_type": "document", "doc_type": "T"}


@pytest.fixture
def routing(monkeypatch):
    state = {"slots": {}, "init": None, "stored": {"review_ai": "B"}, "chain": [{"id": "A"}, {"id": "B"}, {"id": "C"}]}
    monkeypatch.setattr(role_store, "available", lambda: True)
    monkeypatch.setattr(resolve, "read_card_role_inputs", lambda *a: resolve.CardRoleInputs(
        slots=state["slots"], init=state["init"], stored=state["stored"], store_error=state.get("error", False)))
    monkeypatch.setattr(qa.repository, "values", lambda *a: state["stored"])
    monkeypatch.setattr(qa.legacy, "project_of", lambda *a: "p")
    monkeypatch.setattr(qa.legacy, "default_reviewer", lambda *a: "A")
    monkeypatch.setattr(review.ai_settings_service, "resolve_effective", lambda *a: {"providers": state["chain"]})
    return state


@pytest.mark.parametrize("stored,asker,pid,source", [("B", "A", "B", "card_review_ai"),
    ("A", "A", "B", "default"), ("gone", "B", "A", "default"), (None, "A", "B", "default"),
    ("  ", "A", "B", "default"), ("B", None, None, "default")])
def test_q_legacy_matches_existing_candidates(routing, stored, asker, pid, source):
    routing["stored"]["review_ai"] = stored
    result = qa.choose("f", NODE, asker)
    expected = qa.legacy.independent_question_responder("p", asker, [(stored, "card_review_ai"), ("A", "default")])
    assert result[:2] == expected and result[0] == pid and result[3:] == (True, "qa_legacy")
    routing["slots"]["execution"] = object()  # Q-9: only review changes the contract.
    assert qa.choose("f", NODE, asker) == result


@pytest.mark.parametrize("value,asker,reason", [("B", "A", None), ("B", "B", "designated_is_asker"),
    ("gone", "A", "designated_not_enabled"), (None, "A", "responder_not_designated"),
    ("B", None, "asker_unknown")])
def test_n_new_has_one_designated_candidate(routing, value, asker, reason):
    routing["slots"]["review"] = NS(source="card_explicit", value=role_store.RoleValue(
        kind="assigned" if value else "not_used", provider_id=value, provider_name=None))
    result = qa.choose("f", NODE, asker)
    assert result == ("B", "card_review_role", None, False, "qa_new") if reason is None else result == (None, None, reason, False, "qa_new")


@pytest.mark.parametrize("state", ["initialized", "pending_init"])
def test_n_new_unset_never_initializes_or_uses_chain(routing, state):
    routing["init"] = NS(state=state)
    assert qa.choose("f", NODE, "A")[2:4] == ("responder_not_designated", False)


def test_n_unreadable_hands_to_person(routing):
    routing["error"] = True
    assert qa.choose("f", NODE, "A")[2:4] == ("card_settings_unreadable", False)
    routing["error"] = False
    routing["slots"]["review"] = NS(source="bad", value=role_store.RoleValue(kind="assigned", provider_id="B", provider_name=None))
    assert qa.choose("f", NODE, "A")[2] == "card_settings_unreadable"


def test_k_qa_uses_asker_history_never_the_current_attempt(monkeypatch):
    r = {"origin_kind": "card_assigned", "launch_no": 1, "inv_id": "ask#1", "actual_ai_id": "A",
         "target_group_id": "f", "target_node_key": "n", "target_graph_run_id": "old-run",
         "target_attempt_id": "old-attempt", "target_stage": "result", "target_seq": 0,
         "target_doc_id": "doc", "target_doc_revision_no": 2}
    item = {"id": 1, "question_id": 7, "anchor_doc_id": "doc", "asker_ai_run_id": "ask", "asker_actual_provider_id": "A"}
    run = {"run_id": "ask", "invocations": [r]}
    origin = qa.origin("f", NODE, item, run, "card_review_role", "qa_new", "B")
    assert origin["target"]["attempt_id"] == "old-attempt" and origin["qa"]["asker_inv_id"] == "ask#1"
    assert origin["target"]["doc_revision_no"] == 2
    assert qa.origin("f", NODE, item, run, "default", "qa_legacy", "B")["role_source"] == "default_chain"
    r["target_node_key"] = "different"
    origin = qa.origin("f", NODE, item, run, "card_review_role", "qa_new", "B")
    assert origin["target_unresolved_reason"] == "asker_target_mismatch" and origin["target"]["attempt_id"] is None
    run["invocations"] = []
    assert qa.origin("f", NODE, item, run, "card_review_role", "qa_new", "B")["target_unresolved_reason"] == "asker_record_missing"


def test_q15_graph_finalize_and_handoff_cannot_take_card_question(monkeypatch):
    called = []
    monkeypatch.setattr(finalize, "_dispatch_responder_for_chain", lambda *a, **kw: called.append(kw))
    finalize._dispatch_question_responder({"graph_exec_id": "e", "doc_ref": "d"})
    assert called == []
    # Test the actual second entry point independently, before it touches Q&A state.
    monkeypatch.undo()
    assert finalize._dispatch_responder_for_chain({"requester_run": {"graph_exec_id": "e"},
        "requester_run_id": "r", "doc_ref": "d"}, trigger="question_pending_handoff") is None


@pytest.mark.parametrize("kind", ["card_assigned", "card_legacy_attempt"])
def test_k11_review_hop_hands_its_ledger_origin_to_admission(monkeypatch, kind):
    from flowgate.graph_run import execution
    frozen = {"origin_kind": kind, "requested_ai_id": "old-provider", "purpose": "result_review_round",
              "target": {"attempt_id": "frozen-attempt"}}
    monkeypatch.setattr(execution, "request_origin", lambda e: {"invocation_origin": frozen})
    called = []
    monkeypatch.setattr(review._svc(), "start_run", lambda **kw: called.append(kw) or {"run_id": "fake"})
    assert review._start_graph_hop(graph_exec_id="e", provider_id="chosen") == {"run_id": "fake"}
    origin = called[0]["invocation_origin"]
    assert origin["target"]["attempt_id"] == "frozen-attempt"
    assert origin["requested_ai_id"] == ("chosen" if kind == "card_legacy_attempt" else "old-provider")
    assert frozen["requested_ai_id"] == "old-provider"
