from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_document_query_api_0370 import (
    PROJECT, MODULE, GROUP, DOC_R,
    storage_root, tmp_db, patch_store, seed_data,
)
from modules.flow_gate.api import inbox_routes
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.services import token_service

_BODIES = (
    "# Report\n\n## Changed Files\n\nNone\n",
    "# Report\n\n## Changed Files\n\nNone\n\n"
    "## \ub2e8\uacc4\ubcc4 \ud655\uc778\n\n### Legacy\n- Step: unfinished\n",
    "# Report\n\n## Changed Files\n\nNone\n\n"
    "## Step Verification\n\n### Legacy\n- Expected: orphan\n",
    "# Report\n\n## Changed Files\n\nNone\n\n## Step Verification\n\nNone\n",
    "# Report\n\n## Changed Files\n\nNone\n\n## Step Verification\n\n"
    "### Existing checklist\n- Summary: unchanged\n- Step: read\n  - Expected: readable\n",
)


@pytest.fixture
def submission(monkeypatch, tmp_path, seed_data):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    token = {
        "token_id": "tok-0512", "project": PROJECT, "group_id": GROUP,
        "action_scope": "new", "doc_ref": DOC_R,
        "issued_to": "usr_test_001", "scratch_dir": str(scratch),
        "dry_run_count": 0,
    }
    monkeypatch.setattr(token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(token_service, "consume", lambda **_kwargs: None)
    monkeypatch.setattr(token_service, "increment_dry_run", lambda _id: None)
    monkeypatch.setattr(inbox_routes, "has_permission", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(inbox_routes.git_service, "worktree_untracked_summary", lambda *_args: {})
    monkeypatch.setattr(inbox_routes.tr_scope_service, "evaluate", lambda **_kwargs: None)
    app = FastAPI()
    app.include_router(inbox_routes.router)
    return TestClient(app), token


@pytest.mark.parametrize("body_case", range(len(_BODIES)))
@pytest.mark.parametrize("dry_run", [True, False])
def test_new_accepts_ordinary_markdown_when_section_is_absent_or_legacy(submission, storage_root, body_case, dry_run):
    client, _token = submission
    content = _BODIES[body_case]
    response = client.post("/api/v1/inbox", headers={"Authorization": "Bearer test"}, json={
        "action": "new", "project": PROJECT, "module": MODULE, "group_name": GROUP,
        "prev_doc_id": DOC_R, "doc_type": "TR", "title": "Retirement regression",
        "content": content, "dry_run": dry_run,
    })
    assert response.status_code == (200 if dry_run else 201), response.text
    payload = response.json()
    assert payload["ok"] is True
    if dry_run:
        assert "step_verification" not in payload["would_register"]
        assert "step_verification" not in payload["would_register"]["checks_passed"]
    else:
        row = db_docs.get_by_id(payload["doc_id"])
        assert (storage_root / row["file_path"]).read_text(encoding="utf-8") == content


@pytest.mark.parametrize("body_case", range(len(_BODIES)))
@pytest.mark.parametrize("dry_run", [True, False])
def test_edit_accepts_ordinary_markdown_when_section_is_absent_or_legacy(submission, storage_root, body_case, dry_run):
    client, token = submission
    seq = 9100 + body_case * 2 + int(dry_run)
    doc_id = f"{GROUP}-TR{seq}"
    rel = f"documents/{PROJECT}/main/0370/{seq}-TR.md"
    stored = storage_root / rel
    stored.parent.mkdir(parents=True, exist_ok=True)
    original = "# Original report\n"
    stored.write_text(original, encoding="utf-8")
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT, "group_id": GROUP, "module": MODULE,
        "type_code": "TR", "seq": seq, "title": "Legacy report", "status": "open",
        "owner_id": "usr_test_001", "file_path": rel, "revision_no": 0,
    })
    token.update(action_scope="edit", doc_ref=doc_id)
    content = _BODIES[body_case]
    response = client.post("/api/v1/inbox", headers={"Authorization": "Bearer test"}, json={
        "action": "edit", "project": PROJECT, "module": MODULE, "group_name": GROUP,
        "doc_id": doc_id, "edit_reason": "worker_self", "content": content, "dry_run": dry_run,
    })
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    if dry_run:
        assert "step_verification" not in payload["would_register"]
        assert "step_verification" not in payload["would_register"]["checks_passed"]
        assert stored.read_text(encoding="utf-8") == original
        assert db_docs.get_by_id(doc_id)["revision_no"] == 0
    else:
        assert stored.read_text(encoding="utf-8") == content
        assert payload["revision_no"] == 1


@pytest.mark.parametrize("action", ["new", "edit"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_scope_rejection_remains_when_verification_section_is_absent(submission, monkeypatch, storage_root, action, dry_run):
    client, token = submission
    seq = 9200 + int(dry_run)
    doc_id = f"{GROUP}-TR{seq}"
    rel = f"documents/{PROJECT}/main/0370/{seq}-TR.md"
    stored = storage_root / rel
    stored.parent.mkdir(parents=True, exist_ok=True)
    original = "# Original report\n"
    stored.write_text(original, encoding="utf-8")
    if action == "edit":
        db_docs.create({
            "doc_id": doc_id, "project_id": PROJECT, "group_id": GROUP, "module": MODULE,
            "type_code": "TR", "seq": seq, "title": "Scope rejection", "status": "open",
            "owner_id": "usr_test_001", "file_path": rel, "revision_no": 0,
        })
        token.update(action_scope="edit", doc_ref=doc_id)
    reserve = MagicMock()
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.numbering_service, "reserve_document", reserve)
    monkeypatch.setattr(token_service, "consume", consume)
    monkeypatch.setattr(inbox_routes.tr_scope_service, "evaluate", lambda **_kwargs: {
        "verdict": "reject", "notice": "scope rejected", "codes": ["TRV-001"],
    })
    response = client.post("/api/v1/inbox", headers={"Authorization": "Bearer test"}, json={
        "action": action, "project": PROJECT, "module": MODULE, "group_name": GROUP,
        "prev_doc_id": DOC_R, "doc_type": "TR", "title": "Scope rejection",
        "doc_id": doc_id, "edit_reason": "worker_self",
        "content": _BODIES[0], "dry_run": dry_run,
    })
    assert response.status_code == 422, response.text
    assert response.json()["error_message"] == "scope rejected"
    reserve.assert_not_called()
    consume.assert_not_called()
    assert stored.read_text(encoding="utf-8") == original


def test_retired_get_is_absent_when_documents_router_is_registered():
    from modules.flow_gate.documents.routers import documents

    app = FastAPI()
    app.include_router(documents.router, prefix="/api/v1")
    assert not any(route.path.endswith("/step-verification") for route in app.routes)
    response = TestClient(app).get(f"/api/v1/documents/{GROUP}-TR9100/step-verification")
    assert response.status_code == 404
