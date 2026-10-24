"""TR-1 HTTP contract tests for /help/tools (group 0349)."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.api.v1 import help_routes
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import events as db_events
from modules.flow_gate.services import auth_outbound, remote_tool_service, token_service
from modules.flow_gate.settings import source_mode_service


def _client(monkeypatch, token_rec):
    monkeypatch.setattr(help_routes, "verify_bearer", lambda _request: token_rec)
    monkeypatch.setattr(help_routes, "_record_help_tools_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", lambda _project: "remote")
    monkeypatch.setattr(
        remote_tool_service,
        "_worker_token_step_type_result",
        lambda _rec: ("TR", False),
    )
    app = FastAPI()
    app.include_router(help_routes.router)
    return TestClient(app)


def test_list_requires_bearer_and_reuses_error_envelope(monkeypatch):
    def verify(request):
        if not request.headers.get("Authorization"):
            return auth_outbound._fail(401, "Authorization header is required")
        return {"project": "flowgate", "action_scope": "review"}
    monkeypatch.setattr(help_routes, "verify_bearer", verify)
    app = FastAPI()
    app.include_router(help_routes.router)
    response = TestClient(app).get("/api/v1/help/tools")
    assert response.status_code == 401
    assert response.json() == {
        "ok": False,
        "http_status": 401,
        "error_message": "Authorization header is required",
        "help_url": auth_outbound.help_url(),
    }


@pytest.mark.parametrize(
    "token_rec, expected_kind, expected_names",
    [
        # 0482 T0011 x 0492 D0004 D-2: resolve_base_dirty is SCOPE-bound, not kind-wide.
        # An edit/TR/TSR/TS token gets 403 from _exec_resolve_base_dirty, so the catalog
        # must not offer it the tenth tool; only its own scope sees it.
        ({"project": "flowgate", "action_scope": "edit"}, "read_write", ["read", "grep", "glob", "stat", "diff", "log", "show", "merge_preview", "write", "patch", "remove"]),
        ({"project": "flowgate", "action_scope": "resolve_base_dirty"}, "read_write", ["read", "grep", "glob", "stat", "diff", "log", "show", "merge_preview", "write", "patch", "remove", "resolve_base_dirty"]),
        ({"project": "flowgate", "action_scope": "review"}, "read", ["read", "grep", "glob", "stat", "diff", "log", "show", "merge_preview"]),
        ({"project": "flowgate", "action_scope": "test_run"}, "none", []),
    ],
)
def test_list_read_write_read_none_shapes(monkeypatch, token_rec, expected_kind, expected_names):
    client = _client(monkeypatch, token_rec)
    response = client.get("/api/v1/help/tools")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["kind"] == expected_kind
    assert [tool["name"] for tool in body["tools"]] == expected_names
    assert body["detail_url"].endswith("/help/tools/{name}")
    scratch_notes = [note for note in body["notes"] if "0382" in note]
    assert bool(scratch_notes) is (expected_kind == "read_write")


def test_query_locale_precedes_x_locale_and_invalid_folds_ko(monkeypatch):
    client = _client(monkeypatch, {"project": "flowgate", "action_scope": "review"})
    preferred = client.get("/api/v1/help/tools?locale=en", headers={"x-locale": "ja"})
    assert preferred.json()["locale"] == "en"
    assert preferred.json()["tools"][0]["summary"].startswith("Read a single")

    invalid = client.get("/api/v1/help/tools?locale=zh", headers={"x-locale": "ja"})
    assert invalid.status_code == 200
    assert invalid.json()["locale"] == "ko"
    assert invalid.json()["tools"][0]["summary"] == "원격 프로젝트 소스의 파일 하나를 읽는다."


@pytest.mark.parametrize("name", ["read", "write"])
def test_detail_read_and_write_contracts(monkeypatch, name):
    client = _client(monkeypatch, {"project": "flowgate", "action_scope": "edit"})
    response = client.get(f"/api/v1/help/tools/{name}?locale=en")
    assert response.status_code == 200
    body = response.json()
    tool = body["tool"]
    assert tool["name"] == name
    assert tool["method"] == "POST"
    assert tool["path"] == f"/remote/{name}"
    assert tool["example_request"]["url"].endswith(f"/remote/{name}")
    assert tool["example_request"]["headers"]["Authorization"] == "Bearer <YOUR_TOKEN>"
    assert tool["request_fields"]
    assert tool["errors"]
    assert tool["cautions"]
    assert len(body["notes"]) == (5 if name == "write" else 3)
    if name == "write":
        assert any("scratch directory" in note and "0382" in note for note in body["notes"])


@pytest.mark.parametrize("locale", ["ko", "ja", "en"])
@pytest.mark.parametrize("name", ["diff", "log"])
def test_diff_log_details_render_in_every_catalog_locale(monkeypatch, locale, name):
    client = _client(monkeypatch, {"project": "flowgate", "action_scope": "review"})
    tool = client.get(f"/api/v1/help/tools/{name}?locale={locale}").json()["tool"]
    assert tool["name"] == name and tool["scope"] == "read"
    assert tool["example_request"]["body"]["target_ref"] == "origin/main"
    assert tool["example_response"]["merge_base"]
    assert tool["summary"] and tool["request_fields"] and tool["errors"] and tool["cautions"]


def test_detail_unknown_is_404_before_availability_check(monkeypatch):
    client = _client(monkeypatch, {"project": "flowgate", "action_scope": "test_run"})
    response = client.get("/api/v1/help/tools/foo")
    assert response.status_code == 404
    assert response.json()["error_message"] == "Unknown tool: foo"


def test_detail_known_but_unavailable_is_403(monkeypatch):
    client = _client(monkeypatch, {"project": "flowgate", "action_scope": "review"})
    response = client.get("/api/v1/help/tools/write")
    assert response.status_code == 403
    assert response.json()["error_message"] == "Tool 'write' is not available for this token"


def test_help_top_level_lists_tools_routes_after_help_question():
    app = FastAPI()
    app.include_router(help_routes.router)
    body = TestClient(app).get("/api/v1/help").json()
    paths = [endpoint["path"] for endpoint in body["endpoints"]]
    question = paths.index("/help/question")
    assert paths[question + 1:question + 3] == ["/help/tools", "/help/tools/{name}"]
    # 0372 lists the /help/items routes right after them; /events/stream still closes.
    assert paths[question + 3:question + 5] == [
        "/help/items/{name}",
        "/help/items/{name}/{child}",
    ]
    assert paths[question + 5] == "/events/stream"


def test_authenticated_200_403_404_are_logged_best_effort(monkeypatch):
    token_rec = {
        "project": "flowgate",
        "action_scope": "review",
        "doc_ref": "flowgate.default.0349.0008-T",
    }
    monkeypatch.setattr(help_routes, "verify_bearer", lambda _request: token_rec)
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", lambda _project: "remote")
    monkeypatch.setattr(db_documents, "get_by_id", lambda _doc_id: {"doc_id": token_rec["doc_ref"]})
    inserted = []
    monkeypatch.setattr(db_events, "insert_event", lambda **kwargs: inserted.append(kwargs))
    app = FastAPI()
    app.include_router(help_routes.router)
    client = TestClient(app)

    assert client.get("/api/v1/help/tools").status_code == 200
    assert client.get("/api/v1/help/tools/write").status_code == 403
    assert client.get("/api/v1/help/tools/foo").status_code == 404
    assert len(inserted) == 3
    notes = [json.loads(event["note"]) for event in inserted]
    assert [note["http_status"] for note in notes] == [200, 403, 404]
    assert [note["view"] for note in notes] == ["list", "detail", "detail"]
    assert all(event["event_type"] == "help_tools_viewed" for event in inserted)


def test_user_jwt_returns_200_empty_tools(monkeypatch):
    client = _client(monkeypatch, {"issued_to": "console-user", "_is_user_jwt": True})
    response = client.get("/api/v1/help/tools?locale=en")
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "none"
    assert body["source_mode"] is None
    assert body["reason"] is None
    assert body["tools"] == []


def test_help_call_does_not_consume_token(monkeypatch):
    token_rec = {
        "token_id": "tok-0349",
        "issued_to": "worker",
        "project": "flowgate",
        "action_scope": "review",
        "doc_ref": "flowgate.default.0349.0008-T",
        "consumed_at": None,
    }
    calls = []
    def verify(raw):
        calls.append(raw)
        return dict(token_rec)
    monkeypatch.setattr(token_service, "verify", verify)
    monkeypatch.setattr(auth_outbound, "has_permission", lambda *_args: True)
    monkeypatch.setattr(help_routes, "verify_bearer", auth_outbound.verify_bearer)
    monkeypatch.setattr(help_routes, "_record_help_tools_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(source_mode_service, "resolve_effective_mode", lambda _project: "remote")
    app = FastAPI()
    app.include_router(help_routes.router)
    client = TestClient(app)

    response = client.get("/api/v1/help/tools", headers={"Authorization": "Bearer same-token"})
    assert response.status_code == 200
    # The same raw token still verifies for the subsequent artifact operation.
    later = token_service.verify("same-token")
    assert later["consumed_at"] is None
    assert calls == ["same-token", "same-token"]
