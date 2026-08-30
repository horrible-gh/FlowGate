"""수동수정 저장 재시도 회귀 — flowgate.default.0484 T0005/TR0006.

TR 편집 PATCH 경로(``update_document_content`` / RPC 별칭)는 예전엔
``step_verification_service.enforce_on_save`` 를 불러 구버전 TR(``## 단계별 확인``
절이 없는 문서)의 헤더 한 줄만 고쳐도 본문을 고쳐도 422 로 저장을 막았다. TR0006 이
이 호출을 지웠다 — 게이트 함수 자체(``enforce_on_save``)는 inbox new/edit 두
호출자를 위해 그대로 남아 있다.

이 파일은 "수정 전 실패 / 수정 후 성공"을 재실행 가능한 형태로 굳힌다:

* before — 편집한 내용을 ``enforce_on_save`` 에 직접 넣는다. PATCH 경로가 지금도
  이 함수를 부른다면(게이트를 되붙이는 회귀) 무슨 판정이 나올지의 증거이며, TR 은
  여전히 REJECT 다(함수 자체는 손대지 않았으므로).
* after — 같은 편집 내용을 실제 PATCH 라우트로 저장한다. T0005 §5-1 이 요구한 대로
  REST(``PATCH /documents/{doc_id}/content``)와 RPC(``PATCH /documents/content``)
  양쪽 모두 실제 HTTP 클라이언트로 요청을 보내 라우팅/요청 바인딩을 통과시키고
  HTTP 200 을 확인한다(``inbox_client.post_inbox`` 와 같은 "문으로 들어가기" 방식 —
  라우트 함수를 직접 호출하면 엔드포인트 자체가 깨져도 시험이 통과할 수 있다).

헤더만 고친 경우와 본문을 고친 경우를 나누어 각각 확인하고, TR 이 아닌 문서타입
(N, NR)은 이 게이트가 원래 걸린 적이 없었다는 대조군으로 같은 두 변형에서 함께
확인한다(NR0009 — TR 전용 판정).
"""
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

from modules.flow_gate.services import step_verification_service as svs  # noqa: E402

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
def test_tr_manual_save_retry_was_422_before_the_gate_removal_now_saves(monkeypatch, tmp_path, edit_kind):
    from modules.flow_gate.documents.routers import documents as documents_router

    edited_content = _EDITS[edit_kind]

    before = svs.enforce_on_save("TR", edited_content, locale="ko")
    assert before is not None
    assert before["verdict"] == svs.VERDICT_REJECT

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
def test_tr_manual_save_retry_rpc_alias_was_422_before_now_saves(monkeypatch, tmp_path, edit_kind):
    from modules.flow_gate.documents.routers import documents as documents_router

    edited_content = _EDITS[edit_kind]

    before = svs.enforce_on_save("TR", edited_content, locale="ko")
    assert before is not None
    assert before["verdict"] == svs.VERDICT_REJECT

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


@pytest.mark.parametrize("doc_type", ["N", "NR"])
@pytest.mark.parametrize("edit_kind", ["header", "body"])
def test_non_tr_manual_save_retry_was_never_gated_and_still_saves(monkeypatch, tmp_path, doc_type, edit_kind):
    from modules.flow_gate.documents.routers import documents as documents_router

    edited_content = _EDITS[edit_kind]

    before = svs.enforce_on_save(doc_type, edited_content, locale="ko")
    assert before is None

    doc = _doc(tmp_path, doc_type, f"flowgate.default.0484.9103-{edit_kind}-{doc_type}")
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
