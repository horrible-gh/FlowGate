"""B1: actual document entry handlers forward precheck fields and keep success."""
from contextlib import contextmanager, nullcontext
from unittest.mock import patch
import pytest
from tests.modules.flow_gate.test_inbox_origin_write_0410 import tmp_db, patch_store, _issue_token, _post_new  # noqa: F401
from tests.modules.flow_gate.test_inbox_origin_write_0410 import seed_data as original_seed


@pytest.fixture(scope="module")
def seed_data(tmp_db, patch_store):
    from modules.flow_gate.graph_workflow.services import card_document_ports
    def gate(operation, *args, **kwargs):
        return nullcontext() if operation == "paths_scope" else args[-1]()
    with patch.object(card_document_ports, "write_gate", side_effect=gate):
        yield from original_seed.__wrapped__(tmp_db)


def test_inbox_new_document_returns_reuse_and_keeps_run_provenance(seed_data, tmp_path, monkeypatch):
    from modules.flow_gate.services import q_service, ai_invoke_service
    from modules.flow_gate.db import documents
    from starlette.testclient import TestClient
    seen = {}
    precheck = {"status": "reused_answer", "added_item_ids": [], "precheck_id": "qpc_fixture"}
    def common(**kwargs):
        seen.update(kwargs)
        return precheck
    monkeypatch.setattr(q_service, "add_questions", common)
    original_post = TestClient.post
    def with_questions(client, *args, **kwargs):
        kwargs["json"]["questions"] = [{"body": "테스트 서버 커밋 확인", "rejected_answer_id": 17, "reuse_rejection_reason": "현재 R과 다름"}]
        return original_post(client, *args, **kwargs)
    monkeypatch.setattr(TestClient, "post", with_questions)
    from modules.flow_gate.services import token_service
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path.resolve()))
    raw = token_service.issue(project="owproj", group_id="owproj-__ALL__-0001", action_scope="new",
        doc_ref="owproj-__ALL__-0001-R0001", issued_to="usr_ow_001", ai_run_id="aiv_b1_fixture")["raw_token"]
    with patch.object(ai_invoke_service, "get_run_record", return_value={"provider": {"id": "prov_1", "name": "Fixture AI"}}):
        response = _post_new(raw, tmp_path, seq_label="NR9301", content="# B1 document with reused question")
    assert response.status_code == 201, response.text
    assert response.json()["question_precheck"] == precheck
    assert documents.get_by_id("owproj-__ALL__-0001-NR9301") is not None
    assert seen["document_accompanied"] is True
    assert seen["questions"][0]["reuse_rejection_reason"] == "현재 R과 다름"
    assert seen["asker_provenance"] == {"ai_run_id": "aiv_b1_fixture", "actual_provider_name": "Fixture AI"}


def test_next_empty_forwards_reason_options_and_returns_precheck(monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.services import q_service
    from modules.flow_gate.db import connection, documents, workflow_sequences as wf
    from modules.flow_gate.workflow import pipeline_service
    from starlette.requests import Request
    seen = {}
    expected = {"status": "existing_answer_requires_reason", "added_item_ids": [], "precheck_id": "qpc_fixture"}
    payload = {"body": "테스트 서버 커밋 확인", "options": ["확인"], "rejected_answer_id": 17, "reuse_rejection_reason": "새 R의 다른 조건"}
    prev = {"doc_id": "p.none.0001.0001-R", "project_id": "p", "group_id": "p.none.0001"}
    doc = {"doc_id": "p.none.0001.0002-DS", "project_id": "p", "group_id": "p.none.0001", "type_code": "DS", "doc_review_status": "pending_review"}
    class Store:
        @contextmanager
        def transaction(self):
            yield self
    monkeypatch.setattr(connection, "get_store", lambda: Store())
    monkeypatch.setattr(routes, "_canonical_doc_module", lambda *a: "none")
    monkeypatch.setattr(routes, "_reject_if_group_ai_running", lambda *a: None)
    monkeypatch.setattr(routes, "_reject_if_revert_pending", lambda *a: None)
    monkeypatch.setattr(routes, "_next_workflow_type", lambda *a: None)
    monkeypatch.setattr(routes, "_get_project_branch", lambda *a: "main")
    monkeypatch.setattr(routes.document_service, "get_document", lambda *a: prev)
    monkeypatch.setattr(routes.document_service, "create_document", lambda *a, **kw: dict(doc))
    monkeypatch.setattr(documents, "get_by_id", lambda *a: dict(doc))
    monkeypatch.setattr(wf, "get_sequence_for_member_doc", lambda *a: {"id": 1})
    monkeypatch.setattr(wf, "get_effective_head", lambda *a: {"id": 2, "type": "DS"})
    monkeypatch.setattr(routes.numbering_service, "reserve_document", lambda **kw: "0002-DS")
    monkeypatch.setattr(routes.storage_paths, "document_path", lambda **kw: tmp_path / "document.md")
    monkeypatch.setattr(routes.storage_paths, "to_storage_relative", lambda p, *a: str(p))
    monkeypatch.setattr(routes.document_write_ports, "write_gate", lambda op, paths, action: action())
    monkeypatch.setattr(pipeline_service, "register_workflow_result", lambda **kw: None)
    monkeypatch.setattr(pipeline_service, "transition_document_review", lambda **kw: None)
    def common(**kwargs):
        seen.update(kwargs)
        return expected
    monkeypatch.setattr(q_service, "add_questions", common)
    body = routes.NextEmptyDocumentCreate(project_id="p", group_id=prev["group_id"], prev_doc_id=prev["doc_id"],
        type_code="DS", title="B1 next document", questions=[payload])
    result = routes.create_next_empty_document(Request({"type": "http", "headers": []}), body, current_user={"user_id": "u1", "is_admin": True})
    assert seen["questions"] == [{"title": None, **payload}]
    assert seen["document_accompanied"] is True
    assert result["data"]["question_precheck"] == expected
    assert (tmp_path / "document.md").is_file()
