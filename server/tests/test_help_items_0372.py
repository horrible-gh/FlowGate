"""HTTP contract tests for the help index, the permission filter and bulk reads.

Group 0372 set 1 — D-0003 §3-4 (one judgment for showing and for allowing),
P-0004 (addresses and response shapes), L-0005 (assembly order, reason codes).
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate import template_provision
from modules.flow_gate.api.v1 import help_routes
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import events as db_events
from modules.flow_gate.db import templates as db_templates
from modules.flow_gate.services import (
    help_catalog,
    remote_tool_service,
    test_command_service,
)
from modules.flow_gate.settings import source_mode_service

_DESIGN_TYPES = ("D", "P", "L", "DB")

_TYPE_ROWS = [
    {"type_code": "R", "type_name": "요건정의", "series": "requirement", "description": "무엇을 만들 것인가", "is_active": 1},
    {"type_code": "D", "type_name": "기본설계", "series": "design", "description": "모듈 역할", "is_active": 1},
    {"type_code": "P", "type_name": "프로토콜", "series": "design", "description": "요청·응답", "is_active": 1},
    {"type_code": "L", "type_name": "로직", "series": "design", "description": "처리 순서", "is_active": 1},
    {"type_code": "DB", "type_name": "DB설계", "series": "design", "description": "테이블", "is_active": 1},
    {"type_code": "TR", "type_name": "작업레포트", "series": "task", "description": "고친 결과", "is_active": 1},
]

_GROUP_DOCS = [
    {"doc_id": "flowgate.default.0372.0001-R", "type_code": "R", "title": "멘트 리팩터링", "status": "closed", "seq": 1},
    {"doc_id": "flowgate.default.0372.0003-D", "type_code": "D", "title": "경계 기본설계", "status": "open", "seq": 3},
    {"doc_id": "flowgate.default.0372.0002-CH", "type_code": "CH", "title": "대화", "status": "draft", "seq": 2},
]


def _token(action_scope="new", **overrides):
    rec = {
        "project": "flowgate",
        "group_id": "flowgate.default.0372",
        "doc_ref": "flowgate.default.0372.0004-P",
        "action_scope": action_scope,
        "scratch_dir": r"C:\work\tok_1",
    }
    rec.update(overrides)
    return rec


def _client(monkeypatch, token_rec, *, step_type="P", source_mode="remote", events=None,
            commands_block=""):
    """A help client whose whole world (auth, workflow head, storage) is stubbed."""
    monkeypatch.setattr(help_routes, "verify_bearer", lambda _request: token_rec)
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", lambda _p: source_mode)
    monkeypatch.setattr(
        remote_tool_service, "_worker_token_step_type_result", lambda _rec: (step_type, False)
    )
    monkeypatch.setattr(
        db_templates,
        "list_document_types",
        lambda project_id=None, series=None, locale="ko": [
            row for row in _TYPE_ROWS if series is None or row["series"] == series
        ],
    )
    monkeypatch.setattr(db_documents, "get_documents_by_group_id", lambda _gid: list(_GROUP_DOCS))
    monkeypatch.setattr(
        db_documents,
        "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "type_code": step_type,
                        "target_id": "flowgate.default.0372.0001-R"},
    )
    monkeypatch.setattr(template_provision, "is_design_type", lambda code: code in _DESIGN_TYPES)
    monkeypatch.setattr(
        test_command_service, "build_verified_commands_block", lambda _project: commands_block
    )
    sink = events if events is not None else []
    monkeypatch.setattr(db_events, "insert_event", lambda **kwargs: sink.append(kwargs))
    app = FastAPI()
    app.include_router(help_routes.router)
    # A bare /help without a bearer header is the public endpoint catalog, so the
    # personalized answers only appear when the request actually carries one.
    return TestClient(app, headers={"Authorization": "Bearer help-token-0372"})


def _names(body):
    return [item["name"] for item in body["items"]]


def _hidden(body):
    return {entry["name"]: entry["reason"] for entry in body["hidden"]}


# ── index ────────────────────────────────────────────────────────────────────

def test_index_for_a_design_token_lists_the_template_and_hides_the_task_items(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")
    body = client.get("/api/v1/help").json()

    assert body["ok"] is True
    assert body["form"] == "index"
    assert body["version"] == help_catalog.VERSION
    assert _names(body) == [
        "notices", "group_documents", "document_access", "document_attachments", "doc_type",
        "question", "submit", "source_tools", "design_template",
    ]
    assert _hidden(body) == {
        "authoring_guide": "no_guide_for_type",
        "test_commands": "not_ts_type",
        "changed_files_format": "not_mutating_type",
        "step_verification_format": "not_tr_type",
    }
    assert body["context"] == {
        "doc_id": "flowgate.default.0372.0004-P",
        "doc_type": "P",
        "action_scope": "new",
        "tool_kind": "read",
        "source_mode": "remote",
        "reason": None,
    }
    assert body["item_url"].endswith("/help/items/{name}")
    assert body["child_url"].endswith("/help/items/{name}/{child}")
    assert body["bulk_url"].endswith("/help?items={name1},{name2}")
    assert body["detail_url"].endswith("/help?detail=true")


def test_index_never_carries_an_item_body(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")
    body = client.get("/api/v1/help").json()
    for item in body["items"]:
        assert set(item) == {"name", "title", "summary", "form", "children_count", "url"}
        assert item["url"].endswith(f"/help/items/{item['name']}")


def test_index_children_count_counts_only_the_tools_this_token_may_call(monkeypatch):
    read_only = _client(monkeypatch, _token(), step_type="P").get("/api/v1/help").json()
    counts = {item["name"]: item["children_count"] for item in read_only["items"]}
    assert counts["source_tools"] == 6
    assert counts["design_template"] == 4
    assert counts["notices"] is None


def test_index_for_a_mutating_token_opens_write_tools_and_the_report_format(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get("/api/v1/help").json()

    assert "changed_files_format" in _names(body)
    assert "step_verification_format" in _names(body)
    assert "authoring_guide" in _names(body)
    assert _hidden(body) == {"design_template": "not_design_type", "test_commands": "not_ts_type"}
    assert body["context"]["tool_kind"] == "read_write"
    counts = {item["name"]: item["children_count"] for item in body["items"]}
    # Stays 9: flowgate.default.0482 T0011 registered `resolve_base_dirty` as a tenth
    # catalog tool, but tool_registry.SCOPE_BOUND_TOOLS binds it to the
    # `resolve_base_dirty` action_scope. A TR token is read_write yet would get 403 on
    # that op, so the catalog it sees keeps the nine kind-wide tools.
    assert counts["source_tools"] == 9
    assert counts["authoring_guide"] == 1


def test_index_for_a_ts_token_shows_the_verified_commands(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TS")
    body = client.get("/api/v1/help").json()
    assert "test_commands" in _names(body)
    assert "authoring_guide" in _names(body)
    # group 0390 R0001: TS is now a mutating step type, so it gets write tools and
    # must report `## 변경 파일` like T/TR/TSR do.
    assert "changed_files_format" in _names(body)
    assert body["context"]["tool_kind"] == "read_write"


def test_review_token_gets_read_tools_and_a_verdict_flavoured_submit(monkeypatch):
    client = _client(monkeypatch, _token("review"), step_type="D")
    body = client.get("/api/v1/help").json()

    assert body["context"]["tool_kind"] == "read"
    submit = next(item for item in body["items"] if item["name"] == "submit")
    assert submit["title"] == "판정 제출 방법"
    assert _hidden(body)["design_template"] == "not_design_type"


def test_local_source_project_drops_the_tools_item_entirely(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR", source_mode="local")
    body = client.get("/api/v1/help").json()

    assert "source_tools" not in _names(body)
    assert _hidden(body)["source_tools"] == "source_mode_local"
    assert body["context"]["reason"] == "source_mode_local"
    assert body["context"]["tool_kind"] == "none"
    # Source mode gates advertising only — the type-driven items keep their own answer.
    assert "changed_files_format" in _names(body)


def test_a_step_that_touches_no_source_hides_the_tools_for_its_own_reason(monkeypatch):
    client = _client(monkeypatch, _token("test_run"), step_type="TS")
    body = client.get("/api/v1/help").json()
    assert _hidden(body)["source_tools"] == "token_scope_none"


def test_console_user_jwt_sees_only_the_two_context_free_items(monkeypatch):
    client = _client(monkeypatch, {"issued_to": "console", "_is_user_jwt": True})
    response = client.get("/api/v1/help")

    assert response.status_code == 200
    body = response.json()
    assert _names(body) == ["document_access", "doc_type"]
    assert set(_hidden(body).values()) == {"user_session"}
    assert body["context"]["reason"] == "user_session"


# ── the one judgment: shown ⇔ allowed ────────────────────────────────────────

@pytest.mark.parametrize("step_type", ["P", "TR", "TS", "N"])
def test_every_listed_item_answers_and_every_hidden_item_403s(monkeypatch, step_type):
    client = _client(monkeypatch, _token(), step_type=step_type)
    body = client.get("/api/v1/help").json()

    for name in _names(body):
        response = client.get(f"/api/v1/help/items/{name}")
        assert response.status_code == 200, f"{name} was listed but answered {response.status_code}"
        assert response.json()["name"] == name

    for name, reason in _hidden(body).items():
        response = client.get(f"/api/v1/help/items/{name}")
        assert response.status_code == 403, f"{name} was hidden but answered {response.status_code}"
        assert response.json()["error_message"] == (
            f"Help item '{name}' is not available for this token"
        )
        assert reason


def test_unknown_name_is_404_before_any_permission_judgment(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    response = client.get("/api/v1/help/items/templates")
    assert response.status_code == 404
    assert response.json()["error_message"] == "Unknown help item: templates"


def test_item_names_are_case_sensitive(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    assert client.get("/api/v1/help/items/Notices").status_code == 404


# ── children ─────────────────────────────────────────────────────────────────

def test_tool_child_returns_the_same_detail_the_legacy_route_serves(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get("/api/v1/help/items/source_tools/grep").json()

    assert body["name"] == "source_tools"
    assert body["child"] == "grep"
    assert body["form"] == "content"
    assert body["content"]["path"] == "/remote/grep"
    assert body["content"]["example_request"]["url"].endswith("/remote/grep")


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
@pytest.mark.parametrize("name", ["diff", "log"])
def test_diff_log_item_child_matches_tools_surface(monkeypatch, locale, name):
    client = _client(monkeypatch, _token(), step_type="P")
    item = client.get(f"/api/v1/help/items/source_tools/{name}?locale={locale}").json()["content"]
    legacy = client.get(f"/api/v1/help/tools/{name}?locale={locale}").json()["tool"]
    assert item == legacy


def test_a_tool_this_token_cannot_call_is_not_a_known_child(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")  # read-only step
    response = client.get("/api/v1/help/items/source_tools/write")
    assert response.status_code == 404
    assert response.json()["error_message"] == (
        "Unknown child 'write' of help item 'source_tools'"
    )


def test_unknown_design_template_child_is_404(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")
    response = client.get("/api/v1/help/items/design_template/TR")
    assert response.status_code == 404


def test_design_template_list_points_at_the_type_being_written(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="L")
    body = client.get("/api/v1/help/items/design_template").json()
    assert body["form"] == "children"
    assert body["default_child"] == "L"
    assert [child["name"] for child in body["children"]] == list(_DESIGN_TYPES)


def test_source_tools_notes_point_at_the_items_path(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get("/api/v1/help/items/source_tools").json()
    assert any("/help/items/source_tools/{name}" in note for note in body["notes"])
    assert not any("/help/tools/{name}" in note for note in body["notes"])


# ── item bodies ──────────────────────────────────────────────────────────────

def test_notices_lines_follow_the_step_not_the_locale_file(monkeypatch):
    unmanned = _client(
        monkeypatch, _token(continuation_target_seq=9), step_type="TR"
    ).get("/api/v1/help/items/notices").json()
    lines = unmanned["content"]["lines"]
    assert lines[0].startswith("이 작업은 무인(UNMANNED)")
    assert any("배정된 그룹 작업 공간" in line for line in lines)

    attended = _client(monkeypatch, _token(), step_type="P") \
        .get("/api/v1/help/items/notices").json()
    assert attended["content"]["lines"] == [
        help_catalog.NOTICE_LINES["ko"]["interactive_query_without_choice"]
    ]


def test_group_documents_answers_in_sequence_order(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")
    content = client.get("/api/v1/help/items/group_documents").json()["content"]
    assert [doc["doc_id"] for doc in content["documents"]] == [
        "flowgate.default.0372.0001-R",
        "flowgate.default.0372.0002-CH",
        "flowgate.default.0372.0003-D",
    ]
    assert content["total"] == 3
    assert content["more_url"] is None


def test_document_access_advertises_the_bounded_reads(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")
    content = client.get("/api/v1/help/items/document_access").json()["content"]
    partial = content["partial"]
    assert partial["meta"]["url"].endswith("/document/{doc_id}/meta")
    assert partial["outline"]["url"].endswith("/document/{doc_id}/outline")
    assert partial["relations"]["url"].endswith("/document/{doc_id}/relations")
    assert "section_id=<section_id>" in partial["section"]["url"]
    assert "include_matches=true" in partial["content_search"]["url"]
    assert "/documents/" in content["note"]


def test_document_attachments_advertises_the_copy_request_body(monkeypatch):
    """Automated-review regression: the help item used to name the copy URL without
    saying what to send it. A worker relying on help alone must be able to build the
    request without guessing (T0004 s.11/s.17/completion condition 14)."""
    client = _client(monkeypatch, _token(action_scope="resolve_base_dirty"), step_type="TR")
    content = client.get("/api/v1/help/items/document_attachments").json()["content"]
    copy = content["copy"]
    assert copy["method"] == "POST"
    assert copy["url"].endswith("/attachments/{name}/copy")
    assert copy["body"] == {
        "target_path": "<path inside the source tree, e.g. assets/schema.json>",
    }
    assert copy["headers"]["Content-Type"] == "application/json"


def test_submit_carries_this_token_own_identity(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    content = client.get("/api/v1/help/items/submit").json()["content"]
    assert content["url"].endswith("/inbox")
    assert content["body"]["group_name"] == "flowgate.default.0372"
    assert content["body"]["module"] == "default"
    assert content["body"]["doc_type"] == "TR"
    assert content["body"]["prev_doc_id"] == "flowgate.default.0372.0001-R"
    assert "commit_message" in content["body"]
    # T0004 (0506): source_choice keeps the generic scratch-directory guidance but no
    # longer interpolates the actual server-local absolute path.
    assert r"C:\work\tok_1" not in content["source_choice"]
    assert "scratch directory" in content["source_choice"]


def test_changed_files_format_states_the_exact_heading(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    content = client.get("/api/v1/help/items/changed_files_format").json()["content"]
    assert content["required"] is True
    assert content["heading"] == "## 변경 파일"
    assert content["example"].startswith("## 변경 파일")


def test_test_commands_with_an_empty_registry_is_a_normal_answer(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TS", commands_block="")
    response = client.get("/api/v1/help/items/test_commands")
    assert response.status_code == 200
    body = response.json()
    assert body["content"]["has_commands"] is False
    assert body["notes"]


# ── bulk ─────────────────────────────────────────────────────────────────────

def test_bulk_returns_the_requested_items_in_the_requested_order(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get(
        "/api/v1/help?items=notices,submit,changed_files_format"
    ).json()

    assert body["form"] == "bulk"
    assert body["requested"] == ["notices", "submit", "changed_files_format"]
    assert body["returned"] == 3
    assert [item["name"] for item in body["items"]] == body["requested"]
    assert body["unavailable"] == []


def test_bulk_deduplicates_while_keeping_first_position(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get("/api/v1/help?items=submit,notices,submit").json()
    assert body["requested"] == ["submit", "notices"]
    assert body["returned"] == 2


def test_bulk_returns_what_it_can_and_names_what_it_cannot(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get("/api/v1/help?items=notices,design_template,templates").json()

    assert [item["name"] for item in body["items"]] == ["notices"]
    assert body["unavailable"] == [
        {"name": "design_template", "http_status": 403, "reason": "not_design_type"},
        {"name": "templates", "http_status": 404, "reason": "unknown_item"},
    ]


def test_bulk_with_nothing_available_is_403_and_still_explains_each_name(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    response = client.get("/api/v1/help?items=design_template,test_commands")
    assert response.status_code == 403
    body = response.json()
    assert body["ok"] is False
    assert [entry["reason"] for entry in body["unavailable"]] == [
        "not_design_type", "not_ts_type",
    ]


def test_bulk_of_pure_typos_is_404(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    response = client.get("/api/v1/help?items=templates,tools")
    assert response.status_code == 404
    assert all(entry["http_status"] == 404 for entry in response.json()["unavailable"])


def test_bulk_over_the_cap_is_422_and_points_at_detail(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    names = ",".join(help_catalog.CATALOG_ORDER)  # 12 > 10
    response = client.get(f"/api/v1/help?items={names}")
    assert response.status_code == 422
    assert "detail=true" in response.json()["error_message"]


def test_empty_items_is_a_bad_request_but_no_items_at_all_is_the_index(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    assert client.get("/api/v1/help?items=").status_code == 422
    assert client.get("/api/v1/help?items=%20,%20").status_code == 422
    assert client.get("/api/v1/help").json()["form"] == "index"


def test_bulk_children_items_stop_at_the_child_list(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get("/api/v1/help?items=source_tools").json()
    entry = body["items"][0]
    assert entry["form"] == "children"
    assert "content" not in entry
    assert all("content" not in child for child in entry["children"])


# ── detail=true ──────────────────────────────────────────────────────────────

def test_detail_true_expands_exactly_what_the_index_listed(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    index = client.get("/api/v1/help").json()
    detail = client.get("/api/v1/help?detail=true").json()

    assert detail["form"] == "bulk"
    assert detail["requested"] == _names(index)
    assert detail["returned"] == len(index["items"])
    assert detail["unavailable"] == []


def test_detail_never_expands_a_hidden_item(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    detail = client.get("/api/v1/help?detail=true").json()
    assert "design_template" not in [item["name"] for item in detail["items"]]


@pytest.mark.parametrize("value", ["false", "TRUE", "1", ""])
def test_detail_folds_to_the_index_instead_of_being_rejected(monkeypatch, value):
    client = _client(monkeypatch, _token(), step_type="TR")
    response = client.get(f"/api/v1/help?detail={value}")
    assert response.status_code == 200
    assert response.json()["form"] == "index"


def test_items_wins_over_detail(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get("/api/v1/help?items=notices&detail=true").json()
    assert body["requested"] == ["notices"]


# ── locale ───────────────────────────────────────────────────────────────────

def test_query_locale_beats_the_header(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")
    body = client.get("/api/v1/help?locale=en", headers={"x-locale": "ja"}).json()
    assert body["locale"] == "en"
    assert body["items"][0]["title"] == "Notices"


def test_unsupported_locale_folds_to_ko_without_falling_through(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")
    body = client.get("/api/v1/help?locale=zh", headers={"x-locale": "ja"}).json()
    assert body["locale"] == "ko"


def test_the_token_locale_is_used_when_the_request_names_none(monkeypatch):
    client = _client(monkeypatch, _token(continuation_locale="ja"), step_type="P")
    assert client.get("/api/v1/help").json()["locale"] == "ja"


def test_identifiers_never_translate(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")
    body = client.get("/api/v1/help?locale=ja").json()
    assert _names(body)[:2] == ["notices", "group_documents"]
    assert set(_hidden(body)) == {"design_template", "test_commands"}


# ── unauthenticated + audit ──────────────────────────────────────────────────

def test_bare_help_without_a_token_is_still_the_public_endpoint_catalog():
    app = FastAPI()
    app.include_router(help_routes.router)
    body = TestClient(app).get("/api/v1/help").json()
    assert body["form"] == "endpoints"
    assert any(ep["path"] == "/help/items/{name}" for ep in body["endpoints"])


@pytest.mark.parametrize("query", ["?detail=true", "?items=notices"])
def test_asking_for_bodies_without_a_token_is_401(query):
    app = FastAPI()
    app.include_router(help_routes.router)
    response = TestClient(app).get(f"/api/v1/help{query}")
    assert response.status_code == 401


def test_every_authenticated_call_records_exactly_one_help_viewed(monkeypatch):
    events = []
    client = _client(monkeypatch, _token(), step_type="TR", events=events)

    client.get("/api/v1/help")
    client.get("/api/v1/help/items/notices")
    client.get("/api/v1/help/items/source_tools/grep")
    client.get("/api/v1/help?items=notices,submit")
    client.get("/api/v1/help/items/design_template")

    assert [event["event_type"] for event in events] == ["help_viewed"] * 5
    notes = [json.loads(event["note"]) for event in events]
    assert [note["view"] for note in notes] == ["index", "item", "child", "bulk", "item"]
    assert [note["http_status"] for note in notes] == [200, 200, 200, 200, 403]
    assert notes[3]["names"] == ["notices", "submit"]
    assert notes[3]["count"] == 2
    assert notes[0]["doc_type"] == "TR"
    assert notes[0]["tool_kind"] == "read_write"


def test_a_failed_audit_insert_never_breaks_the_help_answer(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="TR")

    def boom(**_kwargs):
        raise RuntimeError("event store down")

    monkeypatch.setattr(db_events, "insert_event", boom)
    assert client.get("/api/v1/help").status_code == 200
    assert client.get("/api/v1/help/items/notices").status_code == 200


def test_the_legacy_question_route_and_the_question_item_share_one_supplier(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")
    legacy = client.get("/api/v1/help/question").json()
    item = client.get("/api/v1/help/items/question").json()
    assert item["content"] == legacy


# ── supplier failures stay supplier failures ─────────────────────────────────

def test_a_storage_failure_is_500_not_a_permission_error(monkeypatch):
    client = _client(monkeypatch, _token(), step_type="P")

    def boom(_gid):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_documents, "get_documents_by_group_id", boom)
    response = client.get("/api/v1/help/items/group_documents")
    assert response.status_code == 500
    assert response.json()["ok"] is False


# ── against the real schema ──────────────────────────────────────────────────
# Everything above stubs storage so the contract is readable. These two run the
# suppliers against a migrated database instead, because a stub cannot catch a
# supplier calling a real query with the wrong argument shape.

@pytest.fixture(scope="module")
def migrated_store():
    import os
    import sqlite3
    import tempfile
    from pathlib import Path

    from modules.flow_gate.db import connection as conn_mod

    schema_dir = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        db_path = handle.name
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for sql_file in sorted(schema_dir.glob("*.sql")):
        try:
            conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    conn.commit()

    class _DB:
        def execute(self, sql, params=None):
            conn.execute(sql, params or [])
            conn.commit()

        def fetch_one(self, sql, params=None):
            row = conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None

        def fetch_all(self, sql, params=None):
            return [dict(r) for r in conn.execute(sql, params or []).fetchall()]

    class _Store(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = _DB()
            self._sq = None

    original = conn_mod.STORE
    conn_mod.STORE = _Store()
    yield
    conn_mod.STORE = original
    conn.close()
    os.unlink(db_path)


def _real_client(monkeypatch, token_rec, step_type):
    monkeypatch.setattr(help_routes, "verify_bearer", lambda _request: token_rec)
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", lambda _p: "remote")
    monkeypatch.setattr(
        remote_tool_service, "_worker_token_step_type_result", lambda _rec: (step_type, False)
    )
    monkeypatch.setattr(db_events, "insert_event", lambda **kwargs: None)
    app = FastAPI()
    app.include_router(help_routes.router)
    return TestClient(app, headers={"Authorization": "Bearer help-token-0372"})


@pytest.mark.parametrize("step_type", ["P", "TR", "TS", "N"])
def test_every_visible_item_builds_against_the_real_schema(monkeypatch, migrated_store, step_type):
    client = _real_client(monkeypatch, _token(), step_type)
    index = client.get("/api/v1/help")
    assert index.status_code == 200
    for item in index.json()["items"]:
        response = client.get(f"/api/v1/help/items/{item['name']}")
        assert response.status_code == 200, f"{item['name']} -> {response.text[:200]}"
    # …and the same content again through one bulk round trip.
    names = [item["name"] for item in index.json()["items"]][:help_catalog.BULK_ITEM_MAX]
    bulk = client.get("/api/v1/help?items=" + ",".join(names))
    assert bulk.status_code == 200, bulk.text[:200]
    assert bulk.json()["returned"] == len(names)


def test_design_template_resolves_a_body_against_the_real_schema(monkeypatch, migrated_store):
    client = _real_client(monkeypatch, _token(), "P")
    listing = client.get("/api/v1/help/items/design_template")
    assert listing.status_code == 200
    assert listing.json()["default_child"] == "P"

    body = client.get("/api/v1/help/items/design_template/P").json()
    content = body["content"]
    assert content["type_code"] == "P"
    # Never blocks: with no registered template the type-default skeleton answers.
    assert content["resolution"] in {
        "exact", "fallback-ko", "global-exact", "global-fallback-ko", "type-default",
    }
    assert content["body"].strip()
    assert content["rendered"].startswith(content["heading"])


def test_doc_type_item_matches_the_legacy_route_against_the_real_schema(monkeypatch, migrated_store):
    client = _real_client(monkeypatch, _token(), "P")
    legacy = client.get("/api/v1/help/doc_type").json()
    item = client.get("/api/v1/help/items/doc_type").json()
    assert item["content"]["types"] == legacy
