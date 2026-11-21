"""Group 0061 card 3 -- AI-facing responses are slimmer, person-facing responses are not touched.

What this file pins (design 0004-D section 5.2, requirement R4, instruction 0009-T):

  * the generic rule (``drop_empty``): only empty values go, ``0`` / ``False`` stay, a ``keep`` key stays
    when its emptiness is the answer;
  * the turn list: a worker token gets the page without ``participants`` / ``me`` / null and empty
    fields (and a few copies and bookkeeping fields), a *session* reader gets the page it always got,
    and the part of the first page that is not turn text shrinks by at least 60 % against the size
    card 1 measured;
  * ``GET /help/tools``: an AI token gets name + one line + path per tool in at most 1,000 characters
    in every locale, the per-tool contract is still one call deeper, and a console user's answer is
    the full block it was;
  * who counts as "an AI" and who does not (``for_ai``), and the lists of the chat-control and
    card-question routes keep the empty answers that mean something.

Sizes are measured with the same helpers card 1 built (``test_prompt_size_baseline_0061``); the limits
that keep them from growing back live in ``prompt_size_baseline_0061.json`` (measured + 10 %).
Run with ``-s`` to see one ``SIZE|<key>|<chars>|<limit>`` line per measurement.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from modules.flow_gate.services import ai_response_slim as slim
from modules.flow_gate.services import conversation_query_service as query
from modules.flow_gate.services import conversation_turn_service as append_service
from modules.flow_gate.services import tool_registry
from test_conversation_query_0351 import DOC_ID as TURN_DOC_ID, _seed as _seed_turns, doc, store  # noqa: F401
from test_prompt_size_baseline_0061 import HELP_PERSONAS, _help_client, _wire, _worker_token

SESSION = {"kind": "session", "user_id": "u1", "user_name": "sjm", "locale": "ko"}
TURN_BODY = "hello, this is a sample turn body"

# Card 1 measured the first worker page of this fixture at 1,647 characters without turn text
# (limit 1,812 = 1.10 x 1,647 in prompt_size_baseline_0061.json before this card).
FIRST_PAGE_BODY_EXCLUDED_BEFORE = 1647
REQUIRED_REDUCTION = 0.60
HELP_TOOLS_SUMMARY_MAX = 1000

FULL_PAGE_KEYS = {"ok", "doc_id", "after_seq", "before_seq", "limit", "head_seq", "next_after_seq",
                  "prev_before_seq", "has_more", "truncated_by", "head", "turns", "participants", "me"}
FULL_HEAD_KEYS = {"doc_id", "type", "title", "status", "group_id", "target_id", "intro",
                  "carried_over_from", "total_turns", "head_seq"}
FULL_TURN_KEYS = {"seq", "speaker", "participant_key", "display_name", "locale", "body", "message_version",
                  "edited_at", "deleted_at", "images", "based_on_seq", "stale_since_seq", "source_run_id",
                  "created_at"}
SLIM_TURN_KEYS = {"seq", "speaker", "display_name", "body", "created_at"}


# ── the generic rule ─────────────────────────────────────────────────────────

def test_drop_empty_removes_only_empty_values_and_keeps_zero_and_false():
    source = {"a": None, "b": "", "c": [], "d": {}, "e": 0, "f": False, "g": {"h": None},
              "i": [{"j": None, "k": 1}, {"l": ""}], "keep": [], "deep": {"x": {"y": None}, "z": 2}}
    assert slim.drop_empty(source, keep=("keep",)) == {
        "e": 0, "f": False, "i": [{"k": 1}, {}], "keep": [], "deep": {"z": 2}}


def test_drop_empty_never_changes_the_input_and_never_renames():
    source = {"a": None, "b": {"c": None, "d": 1}}
    snapshot = {"a": None, "b": {"c": None, "d": 1}}
    out = slim.drop_empty(source)
    assert source == snapshot
    assert out == {"b": {"d": 1}} and set(out["b"]) <= set(source["b"])


def _request(auth):
    state = SimpleNamespace(**({"fg_auth": auth} if auth is not None else {}))
    return SimpleNamespace(state=state)


def test_for_ai_slims_an_ai_token_and_leaves_a_person_or_an_unknown_caller_alone():
    payload = {"a": None, "b": 1}
    assert slim.for_ai(_request({"token_id": "t", "action_scope": "chat"}), payload) == {"b": 1}
    assert slim.for_ai(_request({"_is_user_jwt": True, "issued_to": "u"}), payload) is payload
    assert slim.for_ai(_request(None), payload) is payload
    assert slim.for_ai(object(), payload) is payload


def test_remember_auth_is_what_request_is_ai_reads():
    request = _request(None)
    assert slim.request_is_ai(request) is False
    slim.remember_auth(request, {"token_id": "t"})
    assert slim.request_is_ai(request) is True
    slim.remember_auth(request, {"_is_user_jwt": True})
    assert slim.request_is_ai(request) is False


# ── turn list ────────────────────────────────────────────────────────────────

def _first_page(actor, **over):
    kwargs = dict(doc_id=TURN_DOC_ID, actor=actor, after_seq=0, include_head=True, limit=3)
    kwargs.update(over)
    return query.list_turns(**kwargs)


def _worker_actor():
    return {"kind": "worker", "token": _worker_token()}


def _body_excluded(page: dict) -> int:
    return len(_wire(page)) - sum(len(turn.get("body") or "") for turn in page["turns"])


def test_a_session_reader_still_gets_the_full_turn_page(store, doc):  # noqa: F811
    _seed_turns(6, body=TURN_BODY)
    page = _first_page(SESSION)
    assert set(page) == FULL_PAGE_KEYS
    assert set(page["head"]) == FULL_HEAD_KEYS
    assert all(set(turn) == FULL_TURN_KEYS for turn in page["turns"])
    assert page["limit"] == 3 and page["before_seq"] is None and page["prev_before_seq"] is None
    assert page["me"]["participant_key"] == "user:u1"


def test_a_worker_gets_the_same_turns_without_the_noise(store, doc):  # noqa: F811
    _seed_turns(6, body=TURN_BODY)
    with patch.object(append_service, "_provider_row", return_value={"name": "Claude Opus 5"}):
        worker = _first_page(_worker_actor())
    session = _first_page(SESSION)

    assert set(worker) == {"ok", "doc_id", "after_seq", "head_seq", "next_after_seq", "has_more",
                           "truncated_by", "head", "turns"}
    assert set(worker["head"]) == {"type", "title", "status", "group_id", "target_id", "intro", "total_turns"}
    assert all(set(turn) == SLIM_TURN_KEYS for turn in worker["turns"])

    # Taking away, never renaming: every kept field has the value the session page has.
    for key in ("ok", "doc_id", "after_seq", "head_seq", "next_after_seq", "has_more", "truncated_by"):
        assert worker[key] == session[key], key
    for key, value in worker["head"].items():
        assert session["head"][key] == value, key
    for slim_turn, full_turn in zip(worker["turns"], session["turns"]):
        assert all(full_turn[key] == value for key, value in slim_turn.items())
    assert [turn["seq"] for turn in worker["turns"]] == [1, 2, 3]
    assert worker["after_seq"] == 0 and worker["has_more"] is True and worker["next_after_seq"] == 3


def test_the_first_worker_page_loses_at_least_60_percent_outside_the_turn_text(store, doc):  # noqa: F811
    _seed_turns(6, body=TURN_BODY)
    with patch.object(append_service, "_provider_row", return_value={"name": "Claude Opus 5"}):
        page = _first_page(_worker_actor())
    after = _body_excluded(page)
    reduction = 1 - after / FIRST_PAGE_BODY_EXCLUDED_BEFORE
    print(f"SIZE|turns:worker_first_page:body_excluded:before_after|{FIRST_PAGE_BODY_EXCLUDED_BEFORE}|{after}")
    print(f"SIZE|turns:worker_first_page:reduction|{reduction:.3f}|{REQUIRED_REDUCTION}")
    assert reduction >= REQUIRED_REDUCTION, f"{after} chars left of {FIRST_PAGE_BODY_EXCLUDED_BEFORE}"


def test_a_worker_page_with_no_more_turns_drops_the_cursor_fields_but_keeps_turns(store, doc):  # noqa: F811
    _seed_turns(6, body=TURN_BODY)
    with patch.object(append_service, "_provider_row", return_value={"name": "Claude Opus 5"}):
        page = _first_page(_worker_actor(), after_seq=3, include_head=False)
    assert set(page) == {"ok", "doc_id", "after_seq", "head_seq", "has_more", "turns"}
    assert page["has_more"] is False and [turn["seq"] for turn in page["turns"]] == [4, 5, 6]


def test_an_empty_worker_page_still_says_turns_is_empty():
    page = {"ok": True, "doc_id": "d", "after_seq": 0, "before_seq": None, "limit": 3, "head_seq": 0,
            "next_after_seq": None, "prev_before_seq": None, "has_more": False, "truncated_by": None,
            "head": None, "turns": [], "participants": [], "me": None}
    assert slim.slim_turn_page(page) == {
        "ok": True, "doc_id": "d", "after_seq": 0, "head_seq": 0, "has_more": False, "turns": []}


def test_a_turn_that_is_edited_or_stale_keeps_the_marks_that_say_so():
    page = {"ok": True, "doc_id": "d", "turns": [{
        "seq": 2, "speaker": "user", "participant_key": "user:u1", "display_name": "sjm", "locale": "ko",
        "body": "fixed", "message_version": 2, "edited_at": "2026-07-29T10:05:00+09:00", "deleted_at": None,
        "images": [{"image_id": "i1"}], "based_on_seq": 1, "stale_since_seq": 1, "source_run_id": None,
        "created_at": "2026-07-29T10:00:00+09:00"}]}
    turn = slim.slim_turn_page(page)["turns"][0]
    assert turn == {"seq": 2, "speaker": "user", "display_name": "sjm", "body": "fixed",
                    "edited_at": "2026-07-29T10:05:00+09:00", "images": [{"image_id": "i1"}],
                    "stale_since_seq": 1, "created_at": "2026-07-29T10:00:00+09:00"}


# ── /help/tools ──────────────────────────────────────────────────────────────

def _persona(monkeypatch, persona):
    fields, step_type = HELP_PERSONAS[persona]
    token = {"project": "p", "group_id": "p.m.0001", "doc_ref": "p.m.0001.0004-TR", "scratch_dir": "scratch",
             **fields}
    return _help_client(monkeypatch, token, step_type), token


@pytest.mark.parametrize("persona", sorted(HELP_PERSONAS))
def test_help_tools_summary_is_short_and_points_one_call_deeper(monkeypatch, persona):
    client, token = _persona(monkeypatch, persona)
    response = client.get("/api/v1/help/tools")
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    registry = tool_registry.resolve_registry(token, "p", "ko")
    print(f"SIZE|help_tools_summary:{persona}|{len(response.text)}|{HELP_TOOLS_SUMMARY_MAX}")

    assert len(response.text) <= HELP_TOOLS_SUMMARY_MAX
    assert set(body) <= {"ok", "kind", "reason", "detail_url", "tools", "attachments"}
    assert body["ok"] is True and body["kind"] == registry["kind"]
    assert body["detail_url"].endswith("/help/tools/{name}")
    assert [(tool["name"], tool["path"]) for tool in body["tools"]] == [
        (tool["name"], tool["path"]) for tool in registry["tools"]]
    assert all(set(tool) == {"name", "path", "summary"} and 0 < len(tool["summary"]) <= 20
               for tool in body["tools"])
    assert body.get("attachments", []) == tool_registry.attachment_names(registry["kind"])
    for gone in ("document_attachments", "notes", "version", "base_url", "locale", "source_mode"):
        assert gone not in body, gone


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
def test_help_tools_summary_fits_in_every_locale(monkeypatch, locale):
    client, _token = _persona(monkeypatch, "chat_edit")
    response = client.get(f"/api/v1/help/tools?locale={locale}")
    assert len(response.text) <= HELP_TOOLS_SUMMARY_MAX
    assert [tool["name"] for tool in response.json()["tools"]][:3] == ["read", "grep", "glob"]


def test_every_tool_has_a_short_line_in_every_locale():
    names = [name for name in tool_registry.DISPLAY_ORDER if name != "resolve_base_dirty"]
    for locale in ("ko", "ja", "en"):
        assert all(slim.TOOL_BRIEF[locale].get(name) for name in names), locale


def test_a_tool_without_a_line_falls_back_to_the_first_clause_of_its_summary():
    tool = {"name": "resolve_base_dirty", "summary": "작업 기준선이 더러울 때 정리한다. 그 다음은 두 번째 문장."}
    assert slim._brief(tool, "ko") == "작업 기준선이 더러울 때 정리한다"
    assert slim._brief({"name": "x", "summary": "가" * 100}, "ko") == "가" * 40
    assert slim._brief({"name": "read"}, "ko") == "파일 읽기"


def test_help_tools_detail_is_still_the_full_contract(monkeypatch):
    client, _token = _persona(monkeypatch, "chat_edit")
    detail = client.get("/api/v1/help/tools/read?locale=en")
    assert detail.status_code == 200
    body = detail.json()
    tool = body["tool"]
    assert tool["name"] == "read" and tool["method"] == "POST" and tool["path"] == "/remote/read"
    assert tool["request_fields"] and tool["errors"] and tool["cautions"] and tool["example_request"]
    assert body["notes"]
    attachment = client.get("/api/v1/help/tools/attachment_list")
    assert attachment.status_code == 200 and attachment.json()["tool"]["name"] == "attachment_list"


def test_a_console_user_still_gets_the_full_help_tools_block(monkeypatch):
    client = _help_client(monkeypatch, {"issued_to": "console-user", "_is_user_jwt": True}, "TR")
    response = client.get("/api/v1/help/tools?locale=en")
    assert response.status_code == 200
    body = response.json()
    assert {"version", "base_url", "locale", "kind", "source_mode", "reason", "detail_url", "tools",
            "document_attachments", "notes"} <= set(body)
    assert body["locale"] == "en" and body["kind"] == "none" and body["tools"] == []
    assert body["document_attachments"]["denied"] or body["document_attachments"]["absent"]


# ── chat-control lists and card questions ────────────────────────────────────

def test_card_roles_keep_an_unset_selection_and_an_empty_options_list():
    answer = {
        "flow_id": "f", "node_key": "n", "options": [],
        "context": {"run_id": None, "graph_revision_no": 3, "draft_version": 2},
        "roles": {"review": {"state": "unset", "outcome": None, "source": None, "provider_id": None,
                             "display_name": None, "cause": None, "selected": None},
                  "execution": {"state": "set", "outcome": "ai", "source": "chat", "provider_id": "p1",
                                "display_name": None, "cause": None,
                                "selected": {"kind": "assigned", "provider_id": "p1"}}},
        "settings": {"time_limit": {"value": None, "stored": False, "default": None, "choices": []}},
    }
    assert slim.slim_card_roles(answer) == {
        "flow_id": "f", "node_key": "n", "options": [],
        "context": {"run_id": None, "graph_revision_no": 3, "draft_version": 2},
        "roles": {"review": {"state": "unset", "selected": None},
                  "execution": {"state": "set", "outcome": "ai", "source": "chat", "provider_id": "p1",
                                "selected": {"kind": "assigned", "provider_id": "p1"}}},
        "settings": {"time_limit": {"value": None, "stored": False, "default": None}},
    }


def test_a_value_the_ai_sends_back_is_copied_whole():
    """``context`` is sent back as ``expected_context`` and a locator chains the next read: their nulls
    are part of the value, so ``whole`` keeps the inside untouched while an empty one still goes."""
    source = {"context": {"run_id": None, "n": 1}, "locator": {"section_id": None, "ref": "d@r1#L1-2"},
              "next_locator": None, "other": {"x": None}}
    assert slim.drop_empty(source, whole=("context", "locator", "next_locator")) == {
        "context": {"run_id": None, "n": 1}, "locator": {"section_id": None, "ref": "d@r1#L1-2"}}


def test_ai_options_keep_empty_lists_because_they_mean_nothing_to_pick():
    answer = {"flow_id": "f", "options_state": "ok", "options": [], "next": "x",
              "role_groups": [{"ai_group_id": "g", "name": None, "role_group": False, "type_keys": []}]}
    assert slim.slim_ai_options(answer) == {
        "flow_id": "f", "options_state": "ok", "options": [], "next": "x",
        "role_groups": [{"ai_group_id": "g", "role_group": False, "type_keys": []}]}


def test_a_card_question_view_drops_its_empty_fields_only():
    from flowgate.card_document_edit import chat_questions

    item = {"id": 7, "seq": 1, "title": "Which?", "body": "A or B", "options": "[]", "asker_kind": "ai",
            "answer_count": 0, "responder_state": None, "responder_error_code": None}
    assert chat_questions._item_view(item, False) == {
        "item_id": 7, "seq": 1, "title": "Which?", "body": "A or B", "asker_kind": "ai",
        "answered": False, "answer_count": 0}
    with_options = dict(item, options='[{"id": "o1", "label": "A"}]', responder_state="failed",
                        responder_error_code="no_enabled_provider")
    view = chat_questions._item_view(with_options, False)
    assert view["options"] == [{"id": "o1", "label": "A"}]
    assert view["responder_state"] == "failed" and view["responder_error_code"] == "no_enabled_provider"


def test_a_listed_document_keeps_an_empty_questions_list():
    view = slim.slim_question_view(
        {"doc_id": "d", "title": None, "cards": [], "open_count": 0, "questions": []}, keep=("questions",))
    assert view == {"doc_id": "d", "open_count": 0, "questions": []}


# ── document reads: an AI token against a console user, document layer stubbed ───

ROUTE_DOC = "flowgate.v02test.0061.0003-D"
ROUTE_BODY = "---\ntitle: t\n---\n\n## One\n\ntext one\n\n### One.1\n\nnested text\n\n## Two\n\ntext two\n"
WORKER_AUTH = {"token_id": "tok", "project": "flowgate", "group_id": "flowgate.v02test.0061",
               "action_scope": "edit"}
CONSOLE_AUTH = {"_is_user_jwt": True, "issued_to": "u"}


@pytest.fixture
def document_client(monkeypatch):
    """``client(auth)``: the outbound document routes with the storage and the card-edit layer stubbed,
    so the only thing that differs between two calls is who is reading."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from modules.flow_gate.api.v1 import document_routes
    from modules.flow_gate.graph_workflow.services import card_document_ports

    row = {"doc_id": ROUTE_DOC, "project_id": "flowgate", "type_code": "D", "title": "Design",
           "status": "open", "group_id": "flowgate.v02test.0061", "module": "v02test", "owner_id": "u",
           "revision_no": 2, "file_path": "documents/x.md", "branch": "main", "rejection_history": None,
           "_snapshot_content_state": "readable"}
    state = {"body": ROUTE_BODY}
    monkeypatch.setattr(document_routes.db_docs, "get_by_id", lambda doc_id: dict(row))
    monkeypatch.setattr(card_document_ports, "card_edit", lambda op, doc: (doc, state["body"], "readable"))
    monkeypatch.setattr(document_routes, "_load_reviews", lambda doc_id: (None, []))
    monkeypatch.setattr(document_routes, "_load_test_runs", lambda doc_id: (None, []))
    monkeypatch.setattr(document_routes, "get_answers_for_document", lambda doc_id: [])
    app = FastAPI()
    app.include_router(document_routes.router)

    def client(auth, body=None):
        if body is not None:
            state["body"] = body
        monkeypatch.setattr(document_routes, "verify_bearer", lambda request: auth)
        return TestClient(app)

    return client


def _both(document_client, path, **kwargs):
    ai = document_client(WORKER_AUTH, **kwargs).get(path)
    human = document_client(CONSOLE_AUTH, **kwargs).get(path)
    assert ai.status_code == human.status_code == 200, (ai.text[:300], human.text[:300])
    return ai, human


def test_meta_for_an_ai_token_has_no_empty_field_and_for_a_console_user_has_them_all(document_client):
    ai, human = _both(document_client, f"/api/v1/document/{ROUTE_DOC}/meta")
    full = human.json()
    for key in ("ai_review", "ai_review_history", "test_run", "test_run_history", "rejection_history",
                "rejection_reason", "doc_review_status"):
        assert key in full, key
    assert ai.json() == slim.drop_empty(full)
    assert not {"ai_review", "ai_review_history", "test_run", "test_run_history", "rejection_history"} & set(ai.json())
    assert ai.json()["answers_count"] == 0 and ai.json()["body"]["present"] is True
    print(f"SIZE|meta:ai_vs_console|{len(ai.text)}|{len(human.text)}")
    assert len(ai.text) < len(human.text)


def test_outline_for_an_ai_token_keeps_every_field_that_has_a_value(document_client):
    ai, human = _both(document_client, f"/api/v1/document/{ROUTE_DOC}/outline")
    assert ai.json() == slim.drop_empty(human.json(), keep=slim.OUTLINE_KEEP, whole=slim.OUTLINE_WHOLE)
    assert ai.json()["items"] == human.json()["items"] and ai.json()["section_total"] == 3
    print(f"SIZE|outline:ai_vs_console|{len(ai.text)}|{len(human.text)}")


def test_outline_of_a_document_without_headings_keeps_its_empty_items_for_an_ai_token(document_client):
    ai, human = _both(document_client, f"/api/v1/document/{ROUTE_DOC}/outline", body="short prose\n")
    assert ai.json()["items"] == [] and human.json()["items"] == []
    assert ai.json()["section_total"] == 0


def test_section_for_an_ai_token_loses_its_null_and_empty_fields_only(document_client):
    ai, human = _both(document_client, f"/api/v1/document/{ROUTE_DOC}/section?section=Two")
    full = human.json()
    assert full["next_locator"] is None and full["candidates"] == []
    assert ai.json() == slim.drop_empty(full, keep=slim.SECTION_KEEP, whole=slim.SECTION_WHOLE)
    assert "next_locator" not in ai.json() and "candidates" not in ai.json()
    assert ai.json()["locator"] == full["locator"]
    assert ai.json()["text"] == full["text"] and ai.json()["truncated"] is False
    print(f"SIZE|section:ai_vs_console|{len(ai.text)}|{len(human.text)}")


def test_an_error_answer_of_a_document_read_is_not_slimmed_for_anyone(document_client):
    ai = document_client(WORKER_AUTH).get(f"/api/v1/document/{ROUTE_DOC}/section?section=Nope")
    human = document_client(CONSOLE_AUTH).get(f"/api/v1/document/{ROUTE_DOC}/section?section=Nope")
    assert ai.status_code == human.status_code == 404
    assert ai.json() == human.json()
