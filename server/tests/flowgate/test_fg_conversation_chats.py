"""Standalone CH creation -- POST /api/v1/conversations (flowgate.v02.0032 T#1).

D0006 §3.10.2 (decision order), §3.10.3 (no workflow/lease coupling), §3.10.4 (no
lineage), §5.4 (input/output/errors) and completion conditions 17 and 19.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    ADMIN, CHAT_DOC, GROUP, MODULE, PROJECT, USER, fg_store, seed,  # noqa: F401
)

from flowgate.conversation import chats  # noqa: E402
from flowgate.conversation import legacy_adapter  # noqa: E402
from flowgate.conversation import routes  # noqa: E402

ADMIN_USER = {"user_id": ADMIN, "is_admin": True}


@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch):
    seed(fg_store)
    files = tmp_path / "docs"
    monkeypatch.setattr(
        legacy_adapter, "document_file_path",
        lambda project_id, group_id, doc_code, module: files / group_id / doc_code / "document.md",
    )
    return {"files": files, "store": fg_store}


def _client(user=ADMIN_USER):
    app = FastAPI()
    app.include_router(routes.router, prefix="/flowgate/api/v1")
    if user is not None:
        app.dependency_overrides[routes.get_current_user] = lambda: user
    return TestClient(app)


def _body(**over):
    body = {"project_id": PROJECT, "group_id": GROUP, "title": "  New chat  "}
    body.update(over)
    return body


def test_creates_an_approved_standalone_ch(env):
    res = _client().post("/flowgate/api/v1/conversations", json=_body())
    assert res.status_code == 201, res.text
    body = res.json()
    assert set(body) == {"ok", "doc_id", "project_id", "group_id", "type_code", "title", "status",
                         "created_at", "updated_at"}
    assert body["ok"] is True and body["type_code"] == "CH" and body["status"] == "approved"
    assert body["title"] == "New chat" and body["project_id"] == PROJECT and body["group_id"] == GROUP
    assert body["doc_id"].startswith(f"{GROUP}.") and body["doc_id"].endswith("-CH")
    assert body["doc_id"] != CHAT_DOC

    doc = legacy_adapter.get_document(body["doc_id"])
    assert doc["type_code"] == "CH" and doc["doc_review_status"] == "approved"
    assert doc["target_id"] is None and doc["triggered_by"] is None
    assert doc["module"] == MODULE
    text = next(env["files"].rglob("document.md")).read_text(encoding="utf-8")
    assert "type: CH" in text and "title: New chat" in text and "target_id:\n" in text


def test_workflow_sequence_head_and_parent_are_untouched(env):
    from modules.flow_gate.db import documents as db_documents

    db_documents.create({"doc_id": f"{GROUP}.0002-R", "project_id": PROJECT, "module": MODULE,
                         "group_id": GROUP, "type_code": "R", "seq": 2, "title": "root", "status": "open"})
    store = env["store"]
    tables = ("workflow_sequences", "workflow_sequence_items", "events")

    def counts():
        return {t: store._fetch_one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in tables[:2]}

    before = counts()
    res = _client().post("/flowgate/api/v1/conversations", json=_body())
    assert res.status_code == 201
    assert counts() == before
    assert legacy_adapter.get_document(f"{GROUP}.0002-R")["status"] == "open"


def test_concurrent_creates_get_distinct_numbers(env):
    results = []

    def create():
        results.append(chats.create_chat(ADMIN_USER, _body(title="parallel")))

    threads = [threading.Thread(target=create) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ids = [r["doc_id"] for r in results]
    assert len(ids) == 5 and len(set(ids)) == 5


@pytest.mark.parametrize("body", [
    {"group_id": GROUP, "title": "x"},
    {"project_id": PROJECT, "title": "x"},
    {"project_id": PROJECT, "group_id": GROUP},
    {"project_id": PROJECT, "group_id": GROUP, "title": "   "},
    {"project_id": PROJECT, "group_id": GROUP, "title": "x" * 101},
    {"project_id": PROJECT, "group_id": GROUP, "title": "line\nbreak"},
    {"project_id": PROJECT, "group_id": GROUP, "title": 5},
])
def test_invalid_input_is_422(env, body):
    res = _client().post("/flowgate/api/v1/conversations", json=body)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "conversation_invalid_request"


def test_not_found_and_mismatch(env):
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects as db_projects

    client = _client()
    res = client.post("/flowgate/api/v1/conversations", json=_body(project_id="nope"))
    assert (res.status_code, res.json()["error"]["code"]) == (404, "conversation_project_not_found")
    res = client.post("/flowgate/api/v1/conversations", json=_body(group_id="fgp.v02.9999"))
    assert (res.status_code, res.json()["error"]["code"]) == (404, "conversation_group_not_found")
    db_projects.create({"project_id": "fgq", "project_name": "fgq"})
    db_groups.create({"group_id": "fgq.v02.0001", "project_id": "fgq", "module": "v02", "title": "g", "status": "OPEN"})
    res = client.post("/flowgate/api/v1/conversations", json=_body(group_id="fgq.v02.0001"))
    assert (res.status_code, res.json()["error"]["code"]) == (422, "conversation_invalid_request")


def test_disposed_group_is_409(env, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "is_group_disposed", lambda group_id: True)
    res = _client().post("/flowgate/api/v1/conversations", json=_body())
    assert (res.status_code, res.json()["error"]["code"]) == (409, "conversation_group_disposed")


def test_number_reservation_failure_is_409_and_leaves_nothing(env, monkeypatch):
    def exhausted(group_id, type_code, module):
        raise RuntimeError("numbering lock timeout")

    monkeypatch.setattr(legacy_adapter, "reserve_document", exhausted)
    res = _client().post("/flowgate/api/v1/conversations", json=_body())
    assert (res.status_code, res.json()["error"]["code"]) == (409, "conversation_number_reservation_failed")
    assert not env["files"].exists()


def test_without_create_permission_the_existing_rbac_403(env):
    res = _client(user={"user_id": USER, "is_admin": False}).post("/flowgate/api/v1/conversations", json=_body())
    assert res.status_code == 403 and res.json() == {"detail": "Forbidden"}


def test_unauthenticated_is_rejected_by_the_existing_auth(env):
    res = _client(user=None).post("/flowgate/api/v1/conversations", json=_body())
    assert res.status_code == 401


def test_a_failed_row_insert_removes_the_file(env, monkeypatch):
    def broken(data, actor_user_id):
        raise RuntimeError("insert failed")

    monkeypatch.setattr(legacy_adapter, "create_document", broken)
    with pytest.raises(RuntimeError):
        chats.create_chat(ADMIN_USER, _body())
    assert not list(env["files"].rglob("document.md"))
