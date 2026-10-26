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


def test_ai_direct_t2_authoring_guidance_is_read_only():
    from modules.flow_gate.services import mention_service
    assert "T2" in mention_service._NT_AUTHORING_TYPES
    guidance = mention_service._nt_authoring_section("T2", "en")
    assert "TR2 edit-spec" in guidance
    assert "do not modify" in guidance


def test_auto_approved_http_path_accepts_t2(monkeypatch, tmp_path):
    from test_next_approved_document import _FakeRequest, _wire_common
    from modules.flow_gate.documents.routers import documents as routes

    group_id = "proj-main-0001"
    prev_doc_id = "proj-main-0001-R0001"
    doc_id = "proj-main-0001.0005-T2"
    created = {"doc_id": doc_id, "project_id": "proj", "group_id": group_id,
               "type_code": "T2", "doc_review_status": None}
    refreshed = {**created, "doc_review_status": "approved", "title": "Proposal"}
    _wire_common(
        monkeypatch, head={"id": 10, "type": "T2", "result_doc_id": None},
        group_id=group_id, prev_doc_id=prev_doc_id, tmp_path=tmp_path,
        created_doc=created, refreshed_doc=refreshed,
        perms={"document.approve", "document.update", "perm_document_create"},
        locale_label="Proposal",
    )
    monkeypatch.setattr(routes.numbering_service, "reserve_document",
                        lambda **_kwargs: "0005-T2")
    result = routes.create_next_approved_document(
        routes.NextApprovedDocumentCreate(
            project_id="proj", group_id=group_id, prev_doc_id=prev_doc_id,
            type_code="T2"),
        request=_FakeRequest({"X-Locale": "en"}),
        current_user={"user_id": "tester"})
    assert result["data"]["doc_id"] == doc_id
    assert result["data"]["doc_review_status"] == "approved"
