"""T0007 current Markdown download regressions."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import document_routes  # noqa: E402

DOC_ID = "flowgate.default.0534.0006-TR"
CH_ID = "flowgate.default.0534.0012-CH"


def _row(doc_id=DOC_ID, file_path=None, type_code="TR", group_id="flowgate.default.0534"):
    return {
        "doc_id": doc_id, "type_code": type_code, "title": "title", "status": "open",
        "group_id": group_id, "project_id": "flowgate", "module": "default",
        "branch": "main", "file_path": file_path,
    }


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(document_routes.router)
    return TestClient(app)


def _base(monkeypatch, row, auth):
    monkeypatch.setattr(document_routes, "verify_bearer", lambda _request: dict(auth))
    monkeypatch.setattr(document_routes.db_docs, "get_by_id", lambda doc_id: dict(row) if doc_id == row["doc_id"] else None)
    monkeypatch.setattr(document_routes, "get_answers_for_document", lambda _doc_id: [])
    monkeypatch.setattr(document_routes, "_load_reviews", lambda _doc_id: (None, []))
    monkeypatch.setattr(document_routes, "_load_test_runs", lambda _doc_id: (None, []))
    monkeypatch.setattr(document_routes, "_attachment_briefs", lambda _doc_id: [])


def test_file_backed_download_has_exact_utf8_bytes_and_filename(client, monkeypatch, tmp_path):
    artifact = tmp_path / "source.md"
    content = "# 현재 문서\n\n원문 그대로\n"
    artifact.write_text(content, encoding="utf-8")
    row = _row(file_path="documents/source.md")
    _base(monkeypatch, row, {"doc_ref": DOC_ID, "project": "flowgate"})
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_a, **_k: artifact)

    detail = client.get(f"/api/v1/document/{DOC_ID}")
    response = client.get(f"/api/v1/document/{DOC_ID}/download")

    assert detail.status_code == 200 and detail.json()["download_available"] is True
    assert response.status_code == 200
    assert response.content == content.encode("utf-8")
    assert response.headers["content-type"] == "text/markdown; charset=utf-8"
    assert response.headers["content-disposition"] == f'attachment; filename="{DOC_ID}.md"'


def test_migrated_ch_download_uses_canonical_render_not_stale_snapshot(client, monkeypatch, tmp_path):
    artifact = tmp_path / "stale.md"
    artifact.write_text("# stale snapshot\n", encoding="utf-8")
    row = _row(CH_ID, "documents/stale.md", "CH")
    _base(monkeypatch, row, {"group_id": row["group_id"], "project": "flowgate"})
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_a, **_k: artifact)
    monkeypatch.setattr(document_routes, "_uses_live_conversation_content", lambda _doc: True)
    monkeypatch.setattr(document_routes.conversation_markdown_service, "render_markdown", lambda _id: {"content": "# canonical live\n"})

    response = client.get(f"/api/v1/document/{CH_ID}/download")

    assert response.status_code == 200
    assert response.content == b"# canonical live\n"
    assert b"stale snapshot" not in response.content
    assert response.headers["content-disposition"] == f'attachment; filename="{CH_ID}.md"'


@pytest.mark.parametrize("type_code", ["AC", "DC"])
def test_fileless_documents_are_not_downloadable(client, monkeypatch, type_code):
    doc_id = f"flowgate.default.0534.0099-{type_code}"
    row = _row(doc_id, None, type_code)
    _base(monkeypatch, row, {"doc_ref": doc_id, "project": "flowgate"})

    detail = client.get(f"/api/v1/document/{doc_id}")
    response = client.get(f"/api/v1/document/{doc_id}/download")

    assert detail.status_code == 200 and detail.json()["download_available"] is False
    assert response.status_code == 404
    assert response.json()["error_message"] == "This document has no readable Markdown artifact"


def test_read_only_user_is_allowed_but_user_without_read_is_forbidden(client, monkeypatch, tmp_path):
    artifact = tmp_path / "doc.md"
    artifact.write_text("read only\n", encoding="utf-8")
    row = _row(file_path="documents/doc.md")
    _base(monkeypatch, row, {"_is_user_jwt": True, "issued_to": "viewer"})
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_a, **_k: artifact)
    monkeypatch.setattr(document_routes, "has_permission", lambda *_a: True)
    assert client.get(f"/api/v1/document/{DOC_ID}/download").status_code == 200
    monkeypatch.setattr(document_routes, "has_permission", lambda *_a: False)
    assert client.get(f"/api/v1/document/{DOC_ID}/download").status_code == 403


def test_worker_scope_missing_file_and_bad_ids_are_rejected(client, monkeypatch, tmp_path):
    artifact = tmp_path / "doc.md"
    artifact.write_text("body", encoding="utf-8")
    row = _row(file_path="documents/doc.md")
    _base(monkeypatch, row, {"group_id": "flowgate.default.9999", "project": "flowgate"})
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_a, **_k: artifact)

    assert client.get(f"/api/v1/document/{DOC_ID}/download").status_code == 403
    assert client.get("/api/v1/document/not-a-doc/download").status_code == 422
    assert client.get("/api/v1/document/flowgate.default.0534.9998-TR/download").status_code == 404

    _base(monkeypatch, row, {"doc_ref": DOC_ID, "project": "flowgate"})
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_a, **_k: tmp_path / "missing.md")
    assert client.get(f"/api/v1/document/{DOC_ID}/download").status_code == 404
