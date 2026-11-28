"""TS has two document slots; zero cases cannot become an approved scenario."""
import pytest
from test_fg_card_settings import env, db_path, legacy_cards, decided, FA, _sql, node, put_draft
from test_fg_document_decisions_0126 import decisions_env, _card, _get, _request, _post, _body, _rows
from test_chat_control_v02test_0023 import _group, gate_policy
from test_recovery_v02test_0045 import HEAD, OLD, restore_agent_pepper

pytestmark = pytest.mark.usefixtures("legacy_cards")


@pytest.mark.parametrize("body", ["Legacy instruction only", "## 시험 사양\n", "---\ntest_contract_version: 2\n---\n## 시험 사양\n"])
def test_no_cases_refused_by_human_and_automatic_approval(decisions_env, body):
    from flowgate.card_completion import instruction, gates
    from modules.flow_gate.db import documents
    from modules.flow_gate.workflow import pipeline_service
    env = decisions_env
    key = _card(env, "TS")
    doc_id = _get(env, key)["documents"]["scenario"]["doc_id"]
    _body(doc_id, body)
    response = _post(env, key, _request(env, key, "scenario"), "scenario", status=409)
    assert response["error"]["code"] == "scenario_no_cases"
    assert not _rows(env)
    with pytest.raises(instruction.InstructionRefused, match="scenario_no_cases"):
        instruction.approve(doc_id, requested_by="u_kim")
    with pytest.raises(gates.GateRefused, match="scenario_no_cases"):
        gates.require_gates(documents.get_by_id(doc_id))
    with pytest.raises(pipeline_service.TransitionError, match="scenario_no_cases"):
        pipeline_service._require_test_gate_for_approval(documents.get_by_id(doc_id))
    assert documents.get_by_id(doc_id)["doc_review_status"] is None


def test_legacy_instruction_row_hidden_and_document_preserved(decisions_env):
    from flowgate.card_documents import lines, repository
    from flowgate.card_settings import stage_assignment
    from modules.flow_gate.db import documents
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    env = decisions_env
    key = _card(env, "TS")
    doc_id = _get(env, key)["documents"]["scenario"]["doc_id"]
    _body(doc_id, "Historical brief, keep this text.")
    original = documents.get_by_id(doc_id)
    from modules.flow_gate.services import test_run_service
    text = test_run_service._read_doc_content_or_empty(original)
    before = lines.legacy.flow_documents(FA)
    with_flow_guard(FA, lambda: repository.insert(FA, key, "instruction", "TS", doc_id, True, 0, "u_kim"))
    docs = {d["doc_id"]: d for d in before}
    assert [r["slot"] for r in lines.lines_by_node(FA, docs, [key])[key]] == ["scenario", "report"]
    view = lines.CardView(FA, docs)
    assert [r["slot"] for r in view.lines_for({"node_key": key, "node_type": "document", "doc_type": "TS"})] == ["scenario", "report"]
    assert lines.line_document(FA, key, "instruction", "document", "TS") is None
    assert lines.line_document(FA, key, "scenario", "document", "TS")["doc_id"] == doc_id
    found = stage_assignment.instruction_line_document(FA, {"node_key": key, "node_type": "document", "doc_type": "TS"}, {"instruction_type": "TS"})
    assert found == {"doc_id": doc_id, "written": True}
    assert documents.get_by_id(doc_id) == original
    assert test_run_service._read_doc_content_or_empty(original) == text
    assert "Historical brief, keep this text." in text


def test_empty_ts_starts_and_restarts(env, decided, gate_policy, monkeypatch):
    from flowgate.chat_control import service
    from flowgate.graph_run import recovery, recovery_basis as basis, workspace
    from modules.flow_gate.graph_workflow.store import run_store
    from flowgate.card_documents import lines
    put_draft(env, FA, 0, [node("tmp_r", "R")])
    card = service.place(_group(), "u_kim", {"cards": [{"doc_type": "TS"}], "gate": False})["cards"][0]
    key = card["node_key"]
    assert not lines.has_body(lines.line_document(FA, key, "scenario", "document", "TS")["document"])
    result = service.start(_group(), "u_kim")
    rid = result["run"]["run_id"]
    assert rid
    monkeypatch.setattr(basis, "git_facts", lambda group: {"available": True, "ref": "refs/heads/group", "commit": HEAD, "work_base_ref": "feature", "work_base_commit": OLD})
    monkeypatch.setattr(workspace, "cleanup_for_stop", lambda aid: True)
    request = {"request_id": "restart-empty-ts", "expected_context": basis.view(_group())["context"]}
    restarted = recovery.restart(_group(), "u_kim", request, lambda: None)
    assert restarted["new_run_id"] != rid and restarted["new_run_id"]
    assert run_store.run_by_id(rid)["idle_reason"] == "stopped"
