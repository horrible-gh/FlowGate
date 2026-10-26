"""The one AI title of a new chat -- POST /api/v1/conversations/{doc_id}/auto-title
(flowgate.v02.0047 R0001 §2.2).

Whether a title is owed is the server's record (``documents.meta.chat_auto_title``, written by
``POST /conversations`` with ``auto_title: true``), never the caller's word: a second request,
a chat created with a typed title and a chat renamed by hand are all refused without an AI
call. The first user message is the only input, the project's effective AI connection is used
through the run-less prompt path, and a failure leaves the chat as it is. The legacy adapter is
replaced in one place, so no store or provider is needed; ``rename_document`` behaves like
``document_service.update_document`` (a title write ends an open debt).
"""
from __future__ import annotations

import json
from contextlib import nullcontext

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flowgate.conversation import auto_title
from flowgate.conversation import chats
from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter
from flowgate.conversation import routes
from modules.flow_gate.documents import document_service

DOC = "fgp.v02.0001.0012-CH"
USER = {"user_id": "usr_fg", "is_admin": False}


def _state_of(doc):
    return (json.loads(doc.get("meta") or "null") or {}).get("chat_auto_title", {}).get("state")


def _title_write(doc, title):
    # What document_service.update_document does with a title write: an open debt ends.
    updates = {"title": title}
    ended = document_service._chat_auto_title_ended(doc, updates)
    if ended is not None:
        updates["meta"] = ended
    doc.update(updates)


@pytest.fixture()
def env(monkeypatch):
    state = {
        "doc": {"doc_id": DOC, "project_id": "fgp", "group_id": "fgp.v02.0001", "type_code": "CH",
                "title": "새 대화", "updated_at": "2026-10-08T10:00:00+09:00",
                "meta": auto_title.owed_meta("새 대화")},
        "turns": [{"seq": 1, "speaker": "user", "body": "로그인 오류 원인을 찾아 줘"},
                  {"seq": 2, "speaker": "ai", "body": "확인하겠습니다"}],
        "answer": ("로그인 오류 원인 조사", None),
        "perms": {"perm_document_read", "perm_document_update"},
        "disposed": False,
        "prompts": [],
        "renames": [],
        "during_prompt": None,
    }

    def get_document(doc_id):
        return dict(state["doc"]) if doc_id == DOC else None

    def complete_prompt(project_id, provider_id, prompt, *, timeout, max_tokens):
        state["prompts"].append({"project_id": project_id, "provider_id": provider_id, "prompt": prompt})
        hook, state["during_prompt"] = state["during_prompt"], None
        if hook:
            hook(state)
        return state["answer"]

    def update_meta_cas(doc_id, expected, meta):
        if state["doc"].get("meta") != expected:
            return False
        state["doc"]["meta"] = meta
        return True

    def rename_document(doc_id, title, actor_user_id):
        state["renames"].append((doc_id, title, actor_user_id))
        _title_write(state["doc"], title)
        return dict(state["doc"])

    monkeypatch.setattr(legacy_adapter, "get_document", get_document)
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda user, perm, project: perm in state["perms"])
    monkeypatch.setattr(legacy_adapter, "is_group_disposed", lambda group_id: state["disposed"])
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda doc_id, limit: [dict(t) for t in state["turns"]])
    monkeypatch.setattr(legacy_adapter, "complete_prompt", complete_prompt)
    monkeypatch.setattr(legacy_adapter, "update_meta_cas", update_meta_cas)
    monkeypatch.setattr(legacy_adapter, "rename_document", rename_document)
    monkeypatch.setattr(legacy_adapter, "store_transaction", nullcontext)
    monkeypatch.setattr(legacy_adapter, "title_max_len", lambda: 100)
    return state


def _post(body, doc_id=DOC):
    app = FastAPI()
    app.include_router(routes.router, prefix="/flowgate/api/v1")
    app.dependency_overrides[routes.get_current_user] = lambda: USER
    return TestClient(app).post(f"/flowgate/api/v1/conversations/{doc_id}/auto-title", json=body)


BODY = {"provider_id": "opus"}


def _code(res):
    return res.json()["error"]["code"]


def test_titles_the_chat_from_its_first_user_message(env):
    res = _post(BODY)
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "doc_id": DOC, "title": "로그인 오류 원인 조사",
                          "updated_at": "2026-10-08T10:00:00+09:00"}
    assert env["renames"] == [(DOC, "로그인 오류 원인 조사", "usr_fg")]
    assert _state_of(env["doc"]) == "done"
    [call] = env["prompts"]
    assert call["project_id"] == "fgp" and call["provider_id"] == "opus"
    assert "로그인 오류 원인을 찾아 줘" in call["prompt"] and "확인하겠습니다" not in call["prompt"]


@pytest.mark.parametrize("body", [BODY, {"provider_id": "opus", "expected_title": "로그인 오류 원인 조사"}])
def test_a_second_request_after_success_is_refused_without_an_ai_call(env, body):
    assert _post(BODY).status_code == 200
    res = _post(body)  # even one that names the new title as the title to replace
    assert res.status_code == 409 and _code(res) == "conversation_auto_title_not_owed"
    assert len(env["prompts"]) == 1 and len(env["renames"]) == 1
    assert env["doc"]["title"] == "로그인 오류 원인 조사"


def test_a_request_that_finds_the_debt_claimed_is_refused(env):
    # Two requests at once: the second one arrives while the first one's AI answers.
    def second_request(state):
        res = _post(BODY)
        state["second"] = (res.status_code, _code(res))

    env["during_prompt"] = second_request
    assert _post(BODY).status_code == 200
    assert env["second"] == (409, "conversation_auto_title_not_owed")
    assert len(env["prompts"]) == 1 and len(env["renames"]) == 1


@pytest.mark.parametrize("meta", [None, "", "{}", "not json",
                                  json.dumps({"chat_auto_title": {"state": "done", "title": "새 대화"}})])
def test_a_chat_that_owes_no_title_is_never_asked_for(env, meta):
    # e.g. a chat created with the title the user typed on the draft (no auto_title flag).
    env["doc"]["meta"] = meta
    res = _post(BODY)
    assert res.status_code == 409 and _code(res) == "conversation_auto_title_not_owed"
    assert env["prompts"] == [] and env["renames"] == []


def test_a_manual_title_sent_as_the_current_one_is_never_overwritten(env):
    _title_write(env["doc"], "내가 정한 제목")
    res = _post({"provider_id": "opus", "expected_title": "내가 정한 제목"})
    assert res.status_code == 409 and _code(res) == "conversation_auto_title_not_owed"
    assert env["prompts"] == [] and env["renames"] == []
    assert env["doc"]["title"] == "내가 정한 제목"


def test_a_manual_rename_back_to_the_provisional_title_still_ends_the_debt(env):
    _title_write(env["doc"], "잠깐 다른 제목")
    _title_write(env["doc"], "새 대화")
    res = _post(BODY)
    assert res.status_code == 409 and _code(res) == "conversation_auto_title_not_owed"
    assert env["prompts"] == [] and env["doc"]["title"] == "새 대화"


def test_a_title_changed_some_other_way_ends_the_debt_here(env):
    env["doc"]["title"] = "다른 경로로 바뀐 제목"
    res = _post(BODY)
    assert res.status_code == 409 and _code(res) == "conversation_title_changed"
    assert env["prompts"] == [] and _state_of(env["doc"]) == "manual"
    env["doc"]["title"] = "새 대화"
    assert _code(_post(BODY)) == "conversation_auto_title_not_owed"


def test_a_rename_that_lands_while_the_ai_answers_wins(env):
    env["during_prompt"] = lambda state: _title_write(state["doc"], "사용자 제목")
    res = _post(BODY)
    assert res.status_code == 409 and _code(res) == "conversation_title_changed"
    assert len(env["prompts"]) == 1 and env["renames"] == []
    assert env["doc"]["title"] == "사용자 제목" and _state_of(env["doc"]) == "manual"


def test_a_rename_to_the_same_title_while_the_ai_answers_also_wins(env):
    env["during_prompt"] = lambda state: _title_write(state["doc"], "새 대화")
    res = _post(BODY)
    assert res.status_code == 409 and _code(res) == "conversation_title_changed"
    assert env["renames"] == [] and env["doc"]["title"] == "새 대화"


def test_no_user_message_yet_keeps_the_debt(env):
    env["turns"] = []
    res = _post(BODY)
    assert res.status_code == 409 and _code(res) == "conversation_auto_title_no_message"
    assert env["prompts"] == [] and _state_of(env["doc"]) == "owed"


@pytest.mark.parametrize("answer,status,code,after", [
    ((None, "provider_unavailable"), 409, "conversation_auto_title_unavailable", "owed"),
    ((None, "provider_not_found"), 409, "conversation_auto_title_unavailable", "owed"),
    ((None, "timeout"), 502, "conversation_auto_title_failed", "failed"),
    (("  \n ", None), 502, "conversation_auto_title_failed", "failed"),
])
def test_an_ai_that_cannot_answer_leaves_the_chat_alone(env, answer, status, code, after):
    env["answer"] = answer
    res = _post(BODY)
    assert res.status_code == status and _code(res) == code
    assert env["renames"] == [] and env["doc"]["title"] == "새 대화"
    # Only an unusable provider (nothing was asked) hands the debt back.
    assert _state_of(env["doc"]) == after


def test_an_ai_that_was_asked_is_not_asked_again(env):
    env["answer"] = (None, "timeout")
    assert _post(BODY).status_code == 502
    env["answer"] = ("제목", None)
    res = _post(BODY)
    assert res.status_code == 409 and _code(res) == "conversation_auto_title_not_owed"
    assert len(env["prompts"]) == 1 and env["doc"]["title"] == "새 대화"


@pytest.mark.parametrize("body", [None, {}, {"expected_title": "새 대화"}, {"provider_id": " "},
                                  {"provider_id": 3}, [1]])
def test_invalid_input(env, body):
    res = _post(body)
    assert res.status_code == 422 and _code(res) == "conversation_invalid_request"


def test_permissions_and_group_state(env):
    assert _post(BODY, doc_id="fgp.v02.0001.0099-CH").status_code == 404
    env["perms"] = {"perm_document_read"}
    assert _post(BODY).status_code == 403
    env["perms"] = {"perm_document_read", "perm_document_update"}
    env["disposed"] = True
    res = _post(BODY)
    assert res.status_code == 409 and _code(res) == "conversation_group_disposed"
    assert env["prompts"] == [] and _state_of(env["doc"]) == "owed"


# ── the debt: written by POST /conversations, ended by a title write ─────────────────

@pytest.fixture()
def create_env(monkeypatch, tmp_path):
    created = []
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda user, perm, project: True)
    monkeypatch.setattr(legacy_adapter, "get_project", lambda project_id: {"project_id": project_id})
    monkeypatch.setattr(legacy_adapter, "get_group", lambda group_id: {"project_id": "fgp", "module": "v02"})
    monkeypatch.setattr(legacy_adapter, "is_group_disposed", lambda group_id: False)
    monkeypatch.setattr(legacy_adapter, "title_max_len", lambda: 100)
    monkeypatch.setattr(legacy_adapter, "reserve_document", lambda group_id, type_code, module: "0012-CH")
    monkeypatch.setattr(legacy_adapter, "doc_code_seq", lambda code: 12)
    monkeypatch.setattr(legacy_adapter, "document_file_path", lambda *args: tmp_path / "document.md")
    monkeypatch.setattr(legacy_adapter, "to_storage_relative", lambda path, project_id: "document.md")
    monkeypatch.setattr(legacy_adapter, "store_transaction", nullcontext)
    monkeypatch.setattr(legacy_adapter, "auto_complete_types", lambda: frozenset())
    monkeypatch.setattr(legacy_adapter, "create_document", lambda data, actor_user_id: created.append(dict(data)))
    monkeypatch.setattr(legacy_adapter, "get_document", lambda doc_id: dict(created[-1]))
    return created


CREATE = {"project_id": "fgp", "group_id": "fgp.v02.0001", "title": "새 대화"}


def test_a_chat_created_with_auto_title_owes_it_on_its_creation_title(create_env):
    chats.create_chat(USER, {**CREATE, "auto_title": True})
    assert json.loads(create_env[-1]["meta"]) == {"chat_auto_title": {"state": "owed", "title": "새 대화"}}


@pytest.mark.parametrize("extra", [{}, {"auto_title": False}])
def test_a_chat_created_without_auto_title_owes_nothing(create_env, extra):
    chats.create_chat(USER, {**CREATE, "title": "빌드 정리", **extra})
    assert create_env[-1].get("meta") is None


@pytest.mark.parametrize("value", ["yes", 1, None])
def test_auto_title_must_be_a_boolean(create_env, value):
    with pytest.raises(c.ConversationError) as err:
        chats.create_chat(USER, {**CREATE, "auto_title": value})
    assert err.value.code == "conversation_invalid_request"
    assert create_env == []


@pytest.mark.parametrize("state,ends", [("owed", True), ("claimed", True), ("done", False),
                                        ("failed", False), ("manual", False)])
def test_a_title_write_ends_an_open_debt(state, ends):
    doc = {"type_code": "CH", "meta": json.dumps({"chat_auto_title": {"state": state, "title": "새 대화"}, "x": 1})}
    ended = document_service._chat_auto_title_ended(doc, {"title": "t"})
    if ends:
        assert json.loads(ended) == {"chat_auto_title": {"state": "manual", "title": "새 대화"}, "x": 1}
    else:
        assert ended is None


@pytest.mark.parametrize("doc,updates", [
    ({"type_code": "CH", "meta": auto_title.owed_meta("새 대화")}, {"content": "x"}),
    ({"type_code": "R", "meta": auto_title.owed_meta("새 대화")}, {"title": "t"}),
    ({"type_code": "CH", "meta": None}, {"title": "t"}),
    ({"type_code": "CH", "meta": "{broken"}, {"title": "t"}),
])
def test_other_writes_leave_meta_alone(doc, updates):
    assert document_service._chat_auto_title_ended(doc, updates) is None


@pytest.mark.parametrize("raw,expected", [
    ("로그인 오류 조사", "로그인 오류 조사"),
    ('"로그인 오류 조사."', "로그인 오류 조사"),
    ("Title: Login error\nsecond line", "Login error"),
    ("\n\n**제목: 빌드 확인**", "빌드 확인"),
    ("# 「設計レビュー」", "設計レビュー"),
    ("a\tb   c", "a b c"),
    ("x" * 60, "x" * 39 + "…"),
    ("", None),
    ('""', None),
])
def test_clean_title(raw, expected):
    assert auto_title.clean_title(raw, 40) == expected


def test_the_prompt_bounds_the_message():
    prompt = auto_title.build_prompt("가" * 5000)
    assert prompt.count("가") == auto_title.MESSAGE_MAX_CHARS
    assert "same language" in prompt and "title only" in prompt
