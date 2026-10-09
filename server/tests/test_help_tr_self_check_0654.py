"""0654 T0004: the tr_self_check help item (0684 T#4 removed the source_bundles item).

The item is documentation only. These tests pin it to the contracts it describes
(``api_server_tools.SCHEMAS["run_self_check"]``, ``tr_self_check_service`` and
``tr_self_check_policy``) so the help text cannot drift from the real validation.
"""
import json

import pytest

from modules.flow_gate.services import api_server_tools, help_catalog
from modules.flow_gate.services import tr_self_check_service as service

LOCALES = ("ko", "en", "ja")


@pytest.fixture(autouse=True)
def _no_database(monkeypatch):
    """visibility asks template_provision whether the type is a design type; keep it DB-free."""
    from modules.flow_gate import template_provision
    monkeypatch.setattr(template_provision, "is_design_type", lambda code: code in {"T", "TS", "TSR", "N", "NR"})

SELF_CHECK_ERROR_CODES = {
    "selfcheck_invalid_program", "selfcheck_invalid_args", "selfcheck_shell_operator",
    "selfcheck_inline_execution", "selfcheck_program_denied", "selfcheck_executable_not_found",
    "selfcheck_invalid_cwd", "selfcheck_disabled", "selfcheck_already_running",
    "selfcheck_source_busy", "selfcheck_recovery_incomplete", "selfcheck_worktree_unavailable",
}


def _ctx(locale="en", *, principal_kind="worker", action_scope="edit", doc_type="TR"):
    return {
        "principal_kind": principal_kind, "action_scope": action_scope, "doc_type": doc_type,
        "locale": locale, "base_url": "/flowgate/api/v1", "source_mode": "remote",
        "tool_kind": "read_write", "registry": None,
    }


def _content(locale="en"):
    return help_catalog.build_item("tr_self_check", _ctx(locale))["content"]


# ── H1 visibility ────────────────────────────────────────────────────────────

def test_visible_only_for_a_worker_on_a_tr_edit_step():
    assert help_catalog.decide_visibility("tr_self_check", _ctx()).visible is True
    assert "tr_self_check" in help_catalog.visible_names(_ctx())


def test_visible_for_a_worker_on_a_tr_new_step():
    # 0638 T#1: a TR(new) worker runs Self-check before its TR exists (doc_type is the head type).
    assert help_catalog.decide_visibility("tr_self_check", _ctx(action_scope="new")).visible is True
    assert "tr_self_check" in help_catalog.visible_names(_ctx(action_scope="new"))


@pytest.mark.parametrize("overrides", [
    {"action_scope": "review"},
    {"action_scope": "test_run"},
    {"action_scope": "new", "doc_type": "T"},
    {"action_scope": "new", "doc_type": "TSR"},
    {"doc_type": "TS"},
    {"doc_type": "TSR"},
    {"doc_type": "T"},
    {"doc_type": "N"},
    {"doc_type": "NR"},
    {"principal_kind": "user_session"},
])
def test_hidden_everywhere_else(overrides):
    decision = help_catalog.decide_visibility("tr_self_check", _ctx(**overrides))
    assert decision.visible is False
    assert decision.reason


def test_catalog_registration_is_complete():
    assert "tr_self_check" in help_catalog.CATALOG_ORDER
    assert help_catalog.ITEM_FORM["tr_self_check"] == "content"
    assert "tr_self_check" in help_catalog._CONTENT_SUPPLIERS
    for locale in LOCALES:
        assert help_catalog.TITLES[locale]["tr_self_check"]
        assert help_catalog.SUMMARIES[locale]["tr_self_check"]


# ── H2 / H11 field contract and drift guard ──────────────────────────────────

@pytest.mark.parametrize("locale", LOCALES)
def test_request_field_set_matches_the_tool_schema(locale):
    fields = {f["name"] for f in _content(locale)["request"]["fields"]}
    schema = api_server_tools.SCHEMAS["run_self_check"]
    assert fields == {"program", "args", "cwd", "timeout_seconds"}
    assert fields == set(schema["properties"])
    assert {f["name"] for f in _content(locale)["request"]["fields"] if f["required"]} == set(schema["required"])
    assert tuple(help_catalog.SELF_CHECK_REQUEST_FIELDS) == tuple(schema["properties"])


def test_service_rejects_any_field_outside_the_help_field_set():
    extra = dict(help_catalog.SELF_CHECK_EXAMPLE, shell=True)
    with pytest.raises(service.SelfCheckError) as caught:
        service._validate_request(extra)
    assert caught.value.code == "selfcheck_invalid_request"


def test_help_timeout_bounds_match_the_service():
    example = dict(help_catalog.SELF_CHECK_EXAMPLE)
    for good in (1, help_catalog.SELF_CHECK_TIMEOUT_DEFAULT, help_catalog.SELF_CHECK_TIMEOUT_MAX):
        service._validate_request(dict(example, timeout_seconds=good))
    for bad in (0, help_catalog.SELF_CHECK_TIMEOUT_MAX + 1):
        with pytest.raises(service.SelfCheckError) as caught:
            service._validate_request(dict(example, timeout_seconds=bad))
        assert caught.value.code == "selfcheck_invalid_timeout"


# ── H3 valid example / H4 invalid example ────────────────────────────────────

@pytest.mark.parametrize("locale", LOCALES)
def test_canonical_example_passes_the_real_validation(locale):
    example = _content(locale)["request"]["example"]
    assert example == {
        "program": "pytest", "args": ["-q", "server/tests/test_x.py"], "cwd": ".", "timeout_seconds": 300,
    }
    program, args, cwd, timeout = service._validate_request(example)
    assert (program, args, cwd, timeout) == ("pytest", ["-q", "server/tests/test_x.py"], ".", 300)


@pytest.mark.parametrize("locale", LOCALES)
def test_command_string_example_is_documented_as_wrong(locale):
    request = _content(locale)["request"]
    assert request["invalid_example"] == {"program": "pytest -q server/tests/test_x.py"}
    assert request["invalid_example_note"]
    assert "args" in request["invalid_example_note"]
    # ...and it really is not a usable program name: it never resolves to an executable.
    from modules.flow_gate.services import tr_self_check_policy as policy
    assert " " in request["invalid_example"]["program"]
    with pytest.raises(policy.PolicyError):
        policy.resolve_command(request["invalid_example"]["program"], [], __import__("pathlib").Path("."), "")


# ── H5 lifecycle ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("locale", LOCALES)
def test_lifecycle_describes_run_read_cancel_and_terminal_states(locale):
    content = _content(locale)
    assert content["tools"] == {"run": "run_self_check", "read": "read_self_check", "cancel": "cancel_self_check"}
    flow = content["lifecycle"]["flow"]
    for token in ("run_self_check", "self_check_run_id", "read_self_check", "cancel_self_check"):
        assert token in flow
    assert content["lifecycle"]["statuses"] == ["pending", "running", "completed", "failed", "cancelled"]
    for field in ("exit_code", "timed_out", "stdout_tail", "stderr_tail", "source_changed_during_run",
                  "worktree_state_changed", "error_code"):
        assert field in content["lifecycle"]["result_fields"]
    assert "run_self_check" in content["lifecycle"]["non_zero_exit"]


# ── H6 no fallback ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("locale", LOCALES)
def test_no_fallback_names_no_other_backend(locale):
    # 0684 T#4: Source Bundle, AI Scratch and run_test no longer exist, so the help stops
    # naming them as refused fallbacks; "no other execution path" is the whole rule.
    text = " ".join(_content(locale)["no_fallback"])
    for token in ("Source Bundle", "AI Scratch", "run_test", "access_source_bundle", "run_source_bundle"):
        assert token not in text
    assert "test_command_missing" in text


def test_help_matches_the_live_tr_edit_boundary():
    # Nothing but Self-check executes for a TR edit worker: the Bundle tools and run_test are
    # not registered at all any more, so there is no legacy refusal left to describe.
    for retired in ("access_source_bundle", "run_source_bundle", "run_test"):
        assert retired not in api_server_tools.SCHEMAS
    assert set(api_server_tools.SELF_CHECK_NAMES) <= set(api_server_tools.SCHEMAS)


# ── error guidance ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("locale", LOCALES)
def test_every_required_self_check_error_has_next_action_guidance(locale):
    rows = {row["code"]: row["guidance"] for row in _content(locale)["errors"]}
    assert SELF_CHECK_ERROR_CODES <= set(rows)
    assert all(rows[code].strip() for code in rows)
    assert "selfcheck_invalid_timeout" in rows and "selfcheck_invalid_request" in rows


def test_documented_error_codes_exist_in_the_service_or_policy_source():
    from modules.flow_gate.services import tr_self_check_policy as policy
    import inspect
    source = inspect.getsource(service) + inspect.getsource(policy)
    for row in _content("en")["errors"]:
        assert f'"{row["code"]}"' in source, row["code"]


# ── H7 / H8 (0684 T#4) ───────────────────────────────────────────────────────

def test_source_bundle_recovery_item_is_retired():
    assert "source_bundles" not in help_catalog.CATALOG_ORDER
    assert "source_snapshots" not in help_catalog.CATALOG_ORDER
    assert not any("Bundle" in row["guidance"] for row in _content("en")["errors"])


# ── H9 locales ───────────────────────────────────────────────────────────────

def test_all_locales_carry_the_same_contract_shape():
    shapes = []
    for locale in LOCALES:
        content = _content(locale)
        shapes.append((
            [f["name"] for f in content["request"]["fields"]],
            content["request"]["example"],
            content["lifecycle"]["statuses"],
            [row["code"] for row in content["errors"]],
            len(content["no_fallback"]),
        ))
    assert shapes[0] == shapes[1] == shapes[2]


def test_locales_are_actually_translated():
    ko, en, ja = (json.dumps(_content(l), ensure_ascii=False) for l in LOCALES)
    assert ko != en and en != ja and ko != ja
    assert "Self-check" in ja and any("぀" <= ch <= "ヿ" for ch in ja)
    assert any("가" <= ch <= "힣" for ch in ko)


# ── H10 index / bulk regression ──────────────────────────────────────────────

def test_index_lists_the_item_after_test_commands_for_tr_edit_only():
    order = help_catalog.visible_names(_ctx())
    assert order.index("tr_self_check") == order.index("changed_files_format") - 1
    new_order = help_catalog.visible_names(_ctx(action_scope="new"))
    assert new_order.index("tr_self_check") == new_order.index("changed_files_format") - 1
    assert "tr_self_check" not in help_catalog.visible_names(_ctx(action_scope="review"))


def test_build_item_shape_is_the_usual_content_envelope():
    item = help_catalog.build_item("tr_self_check", _ctx("ko"))
    assert item["name"] == "tr_self_check"
    assert item["form"] == "content"
    assert item["title"] == help_catalog.TITLES["ko"]["tr_self_check"]
    json.dumps(item, ensure_ascii=False)


# ── H13 raw HTTP contract (rejection rej_01M3S9M2ER93RHFT) ───────────────────

def _registered_routes():
    from modules.flow_gate.api.v1 import self_check_routes
    return {(method, route.path) for route in self_check_routes.router.routes
            for method in route.methods if method != "HEAD"}


@pytest.mark.parametrize("locale", LOCALES)
def test_http_operations_match_the_registered_routes(locale):
    http = _content(locale)["http"]
    assert http["base_url"] == "/flowgate/api/v1"
    assert http["note"]
    listed = {(op["method"], "/api/v1" + op["path"]) for op in http["operations"]}
    assert listed == _registered_routes()
    for op in http["operations"]:
        assert op["url"] == http["base_url"] + op["path"]


def _registered_draft_routes():
    from modules.flow_gate.api.v1 import self_check_routes
    return {(method, route.path) for route in self_check_routes.draft_router.routes
            for method in route.methods if method != "HEAD"}


@pytest.mark.parametrize("locale", LOCALES)
def test_tr_new_http_operations_match_the_registered_draft_routes(locale):
    # 0638 T#1: the TR(new) help points at the token-owned draft routes, never a document path.
    content = help_catalog.build_item("tr_self_check", _ctx(locale, action_scope="new"))["content"]
    assert content["stage"] == "new"
    http = content["http"]
    assert http["note"] and http["note"] != _content(locale)["http"]["note"]
    listed = {(op["method"], "/api/v1" + op["path"]) for op in http["operations"]}
    assert listed == _registered_draft_routes()
    assert all("{tr_doc_id}" not in op["path"] for op in http["operations"])
    assert {op["name"] for op in http["operations"]} == {"run", "read", "cancel", "list"}
    assert _content(locale)["stage"] == "edit"


def test_http_help_alone_is_enough_to_run_read_and_cancel():
    ops = {op["name"]: op for op in _content("en")["http"]["operations"]}
    assert (ops["run"]["method"], ops["run"]["path"]) == ("POST", "/documents/{tr_doc_id}/self-check/runs")
    assert ops["run"]["success_status"] == 202
    assert (ops["read"]["method"], ops["read"]["path"]) == ("GET", "/documents/{tr_doc_id}/self-check/runs/{run_id}")
    assert (ops["cancel"]["method"], ops["cancel"]["path"]) == (
        "POST", "/documents/{tr_doc_id}/self-check/runs/{run_id}/cancel")


def test_http_response_contract_names_real_fields():
    http = _content("en")["http"]
    assert http["run_request_body"] == "request.example"
    public_keys = set(service.public({}).keys())
    assert set(http["response_ok_fields"]) - {"ok"} <= public_keys
    assert http["error_shape"]["ok"] is False
    assert set(http["error_shape"]["error"]) == {"code", "message", "details"}
    assert set(http["auth_error_shape"]) == {"ok", "http_status", "error_message", "help_url"}


def _client(monkeypatch, bearer=None):
    """The real router, mounted alone; only the token and document lookups are stubbed."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import self_check_routes as routes

    doc = {"project_id": "p1", "group_id": "g1"}
    monkeypatch.setattr(routes.selfcheck, "_document", lambda doc_id: doc)
    if bearer is not None:
        monkeypatch.setattr(routes, "verify_bearer", lambda request: bearer)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


RUNS = "/api/v1/documents/doc-1/self-check/runs"


@pytest.mark.parametrize("token", [
    {"action_scope": "new", "doc_ref": "doc-1", "group_id": "g1", "project": "p1"},
    {"action_scope": "edit", "doc_ref": "other-doc", "group_id": "g1", "project": "p1"},
])
def test_real_403_response_matches_the_documented_error_shape(monkeypatch, token):
    response = _client(monkeypatch, bearer=token).get(RUNS)
    assert response.status_code == 403
    declared = _content("en")["http"]["error_shape"]
    body = response.json()
    assert body["ok"] is declared["ok"]
    assert set(body["error"]) == set(declared["error"])
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["details"] == {}


def test_real_missing_token_response_matches_the_documented_auth_error_shape(monkeypatch):
    response = _client(monkeypatch).get(RUNS)
    assert response.status_code == 401
    body = response.json()
    declared = _content("en")["http"]["auth_error_shape"]
    assert set(body) == set(declared)
    assert body["ok"] is False and body["http_status"] == 401
    assert "error" not in body


def test_real_selfcheck_error_response_matches_the_documented_error_shape(monkeypatch):
    token = {"action_scope": "edit", "doc_ref": "doc-1", "group_id": "g1", "project": "p1", "issued_to": "u"}
    client = _client(monkeypatch, bearer=token)

    def _refuse(*args):
        raise service.SelfCheckError(409, "selfcheck_already_running", "scr_1")

    monkeypatch.setattr(service, "start", _refuse)
    response = client.post(RUNS, json={"program": "pytest"})
    body = response.json()
    assert response.status_code == 409 and body["ok"] is False
    assert set(body["error"]) == set(_content("en")["http"]["error_shape"]["error"])
    assert body["error"]["code"] in SELF_CHECK_ERROR_CODES
    assert body["error"]["details"] == {"self_check_run_id": "scr_1"}

