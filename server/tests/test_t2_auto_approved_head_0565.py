from modules.flow_gate.services import workflow_decision_service as decisions

def test_auto_and_ai_direct_head_selection():
    assert decisions.is_auto_handled_step(
        head_type="T2", item_seq=1, instruction_mode="auto_approved",
        auto_approve_item_seqs=[]) is True
    assert decisions.is_auto_handled_step(
        head_type="T2", item_seq=1, instruction_mode="ai_direct",
        auto_approve_item_seqs=[]) is False
    assert decisions.is_auto_handled_step(
        head_type="T2", item_seq=1, instruction_mode="ai_direct",
        auto_approve_item_seqs=[1]) is True
    assert decisions.AUTO_REPORT_MAP["T2"] == "TR2"

def test_auto_approved_core_accepts_t2_before_previous_doc_lookup(monkeypatch):
    from modules.flow_gate.documents.routers import documents
    from modules.flow_gate.documents import document_service
    monkeypatch.setattr(document_service, "get_document", lambda _: None)
    try:
        documents.create_next_approved_core(
            project_id="flowgate", group_id="flowgate.default.0565",
            module="default", prev_doc_id="missing", type_code="T2",
            actor_user_id="tester", approver_perms={"document.approve"})
    except documents.NextApprovedError as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("missing previous document must be rejected")
