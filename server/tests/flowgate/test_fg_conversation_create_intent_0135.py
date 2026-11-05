"""One chat per create intent -- POST /api/v1/conversations ``client_request_id`` (flowgate.v02.0135 T#3).

On stg the create answered only after ~2 minutes; the GUI gave up at 30 s, showed ``create_failed``
and a retry made a second CH while the first one was still being written (NR 3.2: two CH with no
turn). A create now names its intent with ``client_request_id``; the same intent sent again gets
the chat it already made (200, ``replayed``), also when the retry arrives while the first create
is still running. Without the field the create is unchanged (a new CH every time).
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    ADMIN, CHAT_DOC, GROUP, PROJECT, fg_store, seed,  # noqa: F401
)

from flowgate.conversation import auto_title  # noqa: E402
from flowgate.conversation import chats  # noqa: E402
from flowgate.conversation import legacy_adapter  # noqa: E402
from flowgate.conversation import routes  # noqa: E402

ADMIN_USER = {"user_id": ADMIN, "is_admin": True}
URL = "/flowgate/api/v1/conversations"
KEY = "draft-7f3a9c2e-0001"


@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch):
    # The document write gate is installed at startup (startup.py); this file does it itself so it
    # does not depend on another test module having registered it first.
    from flowgate.card_document_edit import integration

    integration.register()
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
    app.dependency_overrides[routes.get_current_user] = lambda: user
    return TestClient(app)


def _body(**over):
    body = {"project_id": PROJECT, "group_id": GROUP, "title": "새 대화", "auto_title": True,
            "client_request_id": KEY}
    body.update(over)
    return body


def _new_chats(store):
    rows = store._fetch_all("SELECT doc_id, meta FROM documents WHERE group_id = ? AND type_code = 'CH' "
                            "AND doc_id <> ? ORDER BY doc_id", [GROUP, CHAT_DOC])
    return rows


def test_the_same_intent_sent_again_gets_the_chat_it_made(env):
    """Committed, answer lost, retried: one CH, the retry answers it (200 replayed)."""
    client = _client()
    first = client.post(URL, json=_body())
    assert first.status_code == 201, first.text
    again = client.post(URL, json=_body())
    assert again.status_code == 200, again.text
    assert again.json()["doc_id"] == first.json()["doc_id"]
    assert again.json()["replayed"] is True and "replayed" not in first.json()
    assert [r["doc_id"] for r in _new_chats(env["store"])] == [first.json()["doc_id"]]
    assert len(list(env["files"].rglob("document.md"))) == 1


def test_a_retry_that_arrives_while_the_first_create_still_runs_waits_for_it(env, monkeypatch):
    """The stg case: the first create is still inside the slow write when the retry comes in."""
    entered, release = threading.Event(), threading.Event()
    real_gate = chats.document_write_ports.write_gate

    def slow_gate(kind, paths, call):
        if not entered.is_set():
            entered.set()
            assert release.wait(10)
        return real_gate(kind, paths, call)

    monkeypatch.setattr(chats.document_write_ports, "write_gate", slow_gate)
    results = {}

    def first():
        results["first"] = chats.create_chat(ADMIN_USER, _body())

    def retry():
        results["retry"] = chats.create_chat(ADMIN_USER, _body())

    t1 = threading.Thread(target=first)
    t1.start()
    assert entered.wait(10)
    t2 = threading.Thread(target=retry)
    t2.start()
    t2.join(0.3)
    assert t2.is_alive(), "the retry must wait for the running create, not make its own chat"
    release.set()
    t1.join(10)
    t2.join(10)
    assert results["retry"]["doc_id"] == results["first"]["doc_id"]
    assert results["retry"]["replayed"] is True
    assert len(_new_chats(env["store"])) == 1


def test_a_retry_after_a_late_commit_gets_that_chat(env, monkeypatch):
    """Timeout -> the list read shows nothing -> the server commits -> the user tries again."""
    client = _client()
    gate_open = threading.Event()
    real_gate = chats.document_write_ports.write_gate

    def late_gate(kind, paths, call):
        assert gate_open.wait(10)
        return real_gate(kind, paths, call)

    monkeypatch.setattr(chats.document_write_ports, "write_gate", late_gate)
    late = threading.Thread(target=lambda: chats.create_chat(ADMIN_USER, _body()))
    late.start()
    # the GUI's re-read after its timeout: nothing yet
    assert _new_chats(env["store"]) == []
    gate_open.set()
    late.join(10)
    assert len(_new_chats(env["store"])) == 1
    monkeypatch.setattr(chats.document_write_ports, "write_gate", real_gate)
    retried = client.post(URL, json=_body())
    assert retried.status_code == 200 and retried.json()["replayed"] is True
    assert [r["doc_id"] for r in _new_chats(env["store"])] == [retried.json()["doc_id"]]


def test_other_intents_users_and_plain_creates_still_make_new_chats(env):
    client = _client()
    a = client.post(URL, json=_body()).json()["doc_id"]
    b = client.post(URL, json=_body(client_request_id="draft-7f3a9c2e-0002")).json()["doc_id"]
    other_id = "usr_fg_admin2"
    env["store"]._execute(
        "INSERT INTO users (user_id, username, email, password, is_active, is_admin, first_login_required, "
        "created_at, updated_at) VALUES (?, ?, ?, 'x', 1, 1, 0, '2026-11-05', '2026-11-05')",
        [other_id, other_id, f"{other_id}@example.test"])
    other = {"user_id": other_id, "is_admin": True}
    c = _client(other).post(URL, json=_body()).json()["doc_id"]
    plain1 = client.post(URL, json={"project_id": PROJECT, "group_id": GROUP, "title": "x"})
    plain2 = client.post(URL, json={"project_id": PROJECT, "group_id": GROUP, "title": "x"})
    assert plain1.status_code == plain2.status_code == 201
    ids = [a, b, c, plain1.json()["doc_id"], plain2.json()["doc_id"]]
    assert len(set(ids)) == 5
    assert legacy_adapter.get_document(plain1.json()["doc_id"]).get("meta") is None


def test_the_intent_is_kept_next_to_the_owed_title(env):
    created = _client().post(URL, json=_body()).json()
    doc = legacy_adapter.get_document(created["doc_id"])
    meta = json.loads(doc["meta"])
    assert meta["chat_auto_title"] == {"state": "owed", "title": "새 대화"}
    assert set(meta) == {"chat_auto_title", "chat_create_request"}
    assert KEY not in doc["meta"]  # only its digest is stored
    assert auto_title._owed(doc) is not None  # the AI title debt still reads as owed


@pytest.mark.parametrize("value", [5, "short", "has space inside", "x" * 201, ""])
def test_an_invalid_intent_is_422(env, value):
    res = _client().post(URL, json=_body(client_request_id=value))
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "conversation_invalid_request"
    assert _new_chats(env["store"]) == []
