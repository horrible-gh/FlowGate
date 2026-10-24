from __future__ import annotations

 
from unittest.mock import MagicMock


def test_create_bug_root_uses_b_numbering_and_frontmatter(monkeypatch, tmp_path):
    from modules.flow_gate import process_service

    storage_file = tmp_path / "documents" / "0001-bug.md"
    inserted: dict = {}

    monkeypatch.setattr(
        process_service.db,
        "get_allowed_projects",
        lambda: [{"project": "flowgate", "module": "default"}],
    )
    monkeypatch.setattr(
        process_service.db,
        "get_group",
        lambda _group_id: {
            "group_id": "flowgate.default.0001",
            "project_id": "flowgate",
            "module": "default",
        },
    )
    monkeypatch.setattr(process_service.db, "get_documents_by_group_id", lambda _group_id: [])
    monkeypatch.setattr(process_service.db, "outbox_dir", lambda _project: str(tmp_path / "outbox"))
    monkeypatch.setattr(
        process_service.numbering_service,
        "reserve_document",
        lambda group_id, doc_type, module: "0001-B",
    )
    monkeypatch.setattr(
        process_service.storage_paths,
        "document_path",
        lambda **_kwargs: storage_file,
    )
    monkeypatch.setattr(
        process_service.db,
        "insert_document",
        lambda **kwargs: inserted.update(kwargs),
    )
    monkeypatch.setattr(process_service.db, "insert_event", lambda *_args, **_kwargs: None)

    result = process_service.create_requirement(
        project="flowgate",
        module="default",
        title="Login failure",
        slug="",
        priority="medium",
        body="",
        group_id="flowgate.default.0001",
        doc_type="B",
        template="default",
        locale="ko",
    )

    assert result["status"] == "success"
    assert result["doc_id"] == "flowgate.default.0001.0001-B"
    assert inserted["doc_type"] == "B"
    content = storage_file.read_text(encoding="utf-8")
    assert "type: B" in content
    assert "## 재현 절차" in content
    assert "## 기대 동작" in content
    assert "## 실제 동작" in content


def test_create_bug_root_localizes_template_body(monkeypatch, tmp_path):
    """The default B template body must follow the request locale, not always Korean."""
    from modules.flow_gate import process_service

    def _run(locale: str) -> str:
        storage_file = tmp_path / "documents" / f"0001-bug-{locale}.md"
        monkeypatch.setattr(
            process_service.db,
            "get_allowed_projects",
            lambda: [{"project": "flowgate", "module": "default"}],
        )
        monkeypatch.setattr(
            process_service.db,
            "get_group",
            lambda _group_id: {
                "group_id": "flowgate.default.0001",
                "project_id": "flowgate",
                "module": "default",
            },
        )
        monkeypatch.setattr(process_service.db, "get_documents_by_group_id", lambda _group_id: [])
        monkeypatch.setattr(process_service.db, "outbox_dir", lambda _project: str(tmp_path / "outbox"))
        monkeypatch.setattr(
            process_service.numbering_service,
            "reserve_document",
            lambda group_id, doc_type, module: "0001-B",
        )
        monkeypatch.setattr(
            process_service.storage_paths,
            "document_path",
            lambda **_kwargs: storage_file,
        )
        monkeypatch.setattr(process_service.db, "insert_document", lambda **kwargs: None)
        monkeypatch.setattr(process_service.db, "insert_event", lambda *_args, **_kwargs: None)

        result = process_service.create_requirement(
            project="flowgate",
            module="default",
            title="Login failure",
            slug="",
            priority="medium",
            body="",
            group_id="flowgate.default.0001",
            doc_type="B",
            template="default",
            locale=locale,
        )
        assert result["status"] == "success"
        return storage_file.read_text(encoding="utf-8")

    en_content = _run("en")
    assert "## Steps to Reproduce" in en_content
    assert "## Expected Behavior" in en_content
    assert "재현 절차" not in en_content

    ja_content = _run("ja")
    assert "## 再現手順" in ja_content
    assert "## 期待する動作" in ja_content
    assert "재현 절차" not in ja_content

    # Unknown locale falls back to English, never silently Korean.
    other_content = _run("fr")
    assert "## Steps to Reproduce" in other_content
    assert "재현 절차" not in other_content


def test_create_bug_root_rejects_group_with_requirement(monkeypatch):
    from modules.flow_gate import process_service

    monkeypatch.setattr(
        process_service.db,
        "get_allowed_projects",
        lambda: [{"project": "flowgate", "module": "default"}],
    )
    monkeypatch.setattr(
        process_service.db,
        "get_group",
        lambda _group_id: {
            "project_id": "flowgate",
            "module": "default",
        },
    )
    monkeypatch.setattr(
        process_service.db,
        "get_documents_by_group_id",
        lambda _group_id: [{"type_code": "R"}],
    )

    result = process_service.create_requirement(
        project="flowgate",
        module="default",
        title="Login failure",
        slug="",
        priority="medium",
        body="",
        group_id="flowgate.default.0001",
        doc_type="B",
    )

    assert result["status"] == "error"
    assert result["errors"][0]["code"] == "group_root_already_exists"


def test_request_workflow_decision_accepts_bug_root(monkeypatch):
    from modules.flow_gate.services import workflow_decision_service as service

    doc_id = "flowgate.default.0001.0001-B"
    monkeypatch.setattr(
        service.db_documents,
        "get_by_id",
        lambda _doc_id: {
            "doc_id": doc_id,
            "project_id": "flowgate",
            "group_id": "flowgate.default.0001",
            "type_code": "B",
            "seq": 1,
            "title": "Login failure",
        },
    )
    monkeypatch.setattr(service.db_documents, "get_group_max_seq", lambda _group_id: 1)
    monkeypatch.setattr(service.db_documents, "fetch_recent_group_docs", lambda **_kwargs: [])
    monkeypatch.setattr(service.db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: None)
    issue = MagicMock(return_value={
        "raw_token": "worker-token",
        "token_id": "tok-1",
        "expires_at": "2026-06-13T00:00:00+00:00",
        "scratch_dir": "C:/scratch/tok-1",
    })
    monkeypatch.setattr(service.token_service, "issue", issue)

    result = service.request_workflow_decision(
        doc_id=doc_id,
        issued_to="user-1",
        api_base_url="http://localhost/flowgate/api/v1",
    )

    assert result["doc_ref"] == doc_id
    assert doc_id in result["mention"]


def test_parse_child_document_uses_bug_root_sequence(monkeypatch):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import workflow_sequences as db_sequences
    from modules.flow_gate.documents.routers import documents as routes

    bug_id = "flowgate.default.0001.0001-B"
    child_id = "flowgate.default.0001.0002-T"
    docs = [
        {
            "doc_id": bug_id,
            "type_code": "B",
            "doc_review_status": "wf_in_progress",
            "workflow_steps": '["T", "TR"]',
            "seq": 1,
        },
        {
            "doc_id": child_id,
            "type_code": "T",
            "doc_review_status": "pending_review",
            "seq": 2,
        },
    ]

    def list_documents(project_id, group_id=None, type_code=None, limit=100, **_kwargs):
        result = docs
        if type_code:
            result = [item for item in result if item["type_code"] == type_code]
        return result[:limit]

    monkeypatch.setattr(db_documents, "list_documents", list_documents)
    monkeypatch.setattr(
        db_sequences,
        "get_sequence_by_doc_id",
        lambda doc_id: {"id": 1} if doc_id == bug_id else None,
    )
    monkeypatch.setattr(
        db_sequences,
        "get_sequence_items",
        lambda _seq_id: [
            {"id": 1, "type": "T", "result_doc_id": child_id},
            {"id": 2, "type": "TR", "result_doc_id": None},
        ],
    )

    parsed = routes._parse_doc_workflow({
        "doc_id": child_id,
        "type_code": "T",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0001",
        "workflow_steps": None,
        "rejection_history": None,
    })

    assert parsed["parent_root_doc_id"] == bug_id
    assert parsed["parent_r_doc_id"] == bug_id
    assert parsed["workflow_root_type"] == "B"
    assert parsed["workflow_steps"] == ["T", "TR"]


def test_final_approval_marks_bug_root_done(monkeypatch):
    from modules.flow_gate.workflow.routers import workflow as routes

    bug_id = "flowgate.default.0001.0001-B"
    docs = {
        bug_id: {
            "doc_id": bug_id,
            "project_id": "flowgate",
            "group_id": "flowgate.default.0001",
            "type_code": "B",
            "doc_review_status": "wf_in_progress",
            "seq": 1,
        },
    }
    updated: dict = {}

    monkeypatch.setattr(routes, "_get_user_permissions", lambda _user: {"document.approve"})
    monkeypatch.setattr(routes.db_docs, "get_by_id", lambda doc_id: docs.get(doc_id))
    monkeypatch.setattr(
        routes.db_docs,
        "list_documents",
        lambda **_kwargs: [docs[bug_id]],
    )
    monkeypatch.setattr(
        routes.db_docs,
        "update",
        lambda doc_id, values: updated.update({"doc_id": doc_id, **values}) or {
            **docs[bug_id],
            **values,
        },
    )

    result = routes.finalize_workflow_endpoint(
        routes.DocumentBodyRequest(doc_id=bug_id),
        {"user_id": "user-1"},
    )

    assert updated == {"doc_id": bug_id, "doc_review_status": "wf_done"}
    assert result["document"]["doc_review_status"] == "wf_done"
