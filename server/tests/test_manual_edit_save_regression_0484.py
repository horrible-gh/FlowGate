from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

_ORIGINAL = "---\ntitle: old title\ntype: TR\n---\n\nLegacy report without the section.\n"
_HEADER_EDIT = _ORIGINAL.replace("title: old title", "title: corrected title")
_BODY_EDIT = _ORIGINAL.replace(
    "Legacy report without the section.", "Legacy report, body line rewritten."
)
_EDITS = {"header": _HEADER_EDIT, "body": _BODY_EDIT}


def _doc(tmp_path: Path, doc_type: str, doc_id: str) -> dict:
    return {
        "doc_id": doc_id,
        "project_id": "flowgate",
        "group_id": "flowgate.default.0484",
        "type_code": doc_type,
        "status": "closed",
        "doc_review_status": "wf_in_progress",
        "file_path": str(tmp_path / f"{doc_id}.md"),
    }


def _stub_route(monkeypatch, documents_router, doc: dict, existing_body: str):
    doc_file = Path(doc["file_path"])
    doc_file.parent.mkdir(parents=True, exist_ok=True)
    doc_file.write_text(existing_body, encoding="utf-8")
    update_mock = MagicMock(return_value=doc)
    monkeypatch.setattr(documents_router.document_service, "get_document", lambda _doc_id: doc)
    monkeypatch.setattr(documents_router.document_service, "update_document", update_mock)
    monkeypatch.setattr(documents_router.document_service, "is_final_approved", lambda _doc: False)
    monkeypatch.setattr(
        documents_router.document_service, "is_document_editable",
        lambda _doc, final_approved=None: True,
    )
    monkeypatch.setattr(documents_router, "_document_file_path", lambda _doc: doc_file)
    return doc_file, update_mock


def _patch_client(documents_router) -> TestClient:
    """Real HTTP client over the documents router (T0005 §5-1: through the door,
    not the function — mirrors ``tests/inbox_client.py``'s rationale)."""
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(documents_router.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u"}
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("edit_kind", ["header", "body"])
def test_tr_manual_save_retry_saves_without_required_sections(monkeypatch, tmp_path, edit_kind):
    from modules.flow_gate.documents.routers import documents as documents_router

    edited_content = _EDITS[edit_kind]

    doc = _doc(tmp_path, "TR", f"flowgate.default.0484.9101-{edit_kind}-TR")
    doc_file, update_mock = _stub_route(monkeypatch, documents_router, doc, _ORIGINAL)
    client = _patch_client(documents_router)

    response = client.patch(
        f"/api/v1/documents/{doc['doc_id']}/content",
        json={"content": edited_content},
    )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == edited_content
    assert doc_file.read_text(encoding="utf-8") == edited_content
    update_mock.assert_called_once()


@pytest.mark.parametrize("edit_kind", ["header", "body"])
def test_tr_manual_save_retry_rpc_alias_saves_without_required_sections(monkeypatch, tmp_path, edit_kind):
    from modules.flow_gate.documents.routers import documents as documents_router

    edited_content = _EDITS[edit_kind]

    doc = _doc(tmp_path, "TR", f"flowgate.default.0484.9102-{edit_kind}-TR")
    doc_file, update_mock = _stub_route(monkeypatch, documents_router, doc, _ORIGINAL)
    client = _patch_client(documents_router)

    response = client.patch(
        "/api/v1/documents/content",
        json={"doc_id": doc["doc_id"], "content": edited_content},
    )

    assert response.status_code == 200, response.text
    assert response.json()["content"] == edited_content
    assert doc_file.read_text(encoding="utf-8") == edited_content
    update_mock.assert_called_once()


@pytest.mark.parametrize("rpc", [False, True])
@pytest.mark.parametrize("doc_type", ["N", "NR"])
@pytest.mark.parametrize("edit_kind", ["header", "body"])
def test_non_tr_manual_save_retry_still_saves(monkeypatch, tmp_path, doc_type, edit_kind, rpc):
    from modules.flow_gate.documents.routers import documents as documents_router

    edited_content = _EDITS[edit_kind]

    doc = _doc(tmp_path, doc_type, f"flowgate.default.0484.9103-{edit_kind}-{doc_type}")
    doc_file, update_mock = _stub_route(monkeypatch, documents_router, doc, _ORIGINAL)
    client = _patch_client(documents_router)
    url = "/api/v1/documents/content" if rpc else f"/api/v1/documents/{doc['doc_id']}/content"
    payload = {"content": edited_content}
    if rpc:
        payload["doc_id"] = doc["doc_id"]
    response = client.patch(url, json=payload)

    assert response.status_code == 200, response.text
    assert response.json()["content"] == edited_content
    assert doc_file.read_text(encoding="utf-8") == edited_content
    update_mock.assert_called_once()


@pytest.mark.parametrize("rpc", [False, True])
def test_legacy_markdown_survives_read_and_header_edit(monkeypatch, tmp_path, rpc):
    from modules.flow_gate.documents.routers import documents as documents_router

    legacy = _ORIGINAL + "\n## \ub2e8\uacc4\ubcc4 \ud655\uc778\n\n### Old checklist\n- Step: incomplete\n"
    changed = legacy.replace("title: old title", "title: corrected title")
    doc = _doc(tmp_path, "TR", "flowgate.default.0484.9201-TR")
    doc_file, update_mock = _stub_route(monkeypatch, documents_router, doc, legacy)
    client = _patch_client(documents_router)
    before = client.get(f"/api/v1/documents/{doc['doc_id']}/content")
    assert before.status_code == 200, before.text
    assert before.json()["content"] == legacy
    url = "/api/v1/documents/content" if rpc else f"/api/v1/documents/{doc['doc_id']}/content"
    payload = {"content": changed}
    if rpc:
        payload["doc_id"] = doc["doc_id"]
    response = client.patch(url, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["content"] == changed
    assert doc_file.read_text(encoding="utf-8") == changed
    update_mock.assert_called_once()
