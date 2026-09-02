import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.api.v1 import document_routes
from modules.flow_gate.services import ai_invoke_service as invoke
from modules.flow_gate.services import api_server_tools as tools
from modules.flow_gate.services import provider_capability_service as caps


def _run(tmp_path):
    return {"project_id": "p", "group_id": "g", "doc_ref": "d", "action_scope": "new", "source_root": str(tmp_path)}


# The advertised source tools for a "read" kind, in provider-alias order.
READ_SOURCE_NAMES = tuple(name for name, op in tools.SOURCE_OPS.items() if op in tools.tool_registry.READ_TOOLS)


@pytest.mark.parametrize("scope, expected", [
    ("new", list(tools.BASE_NAMES) + list(READ_SOURCE_NAMES)),
    ("edit", list(tools.BASE_NAMES) + list(READ_SOURCE_NAMES)),
    ("review", list(tools.BASE_NAMES) + list(READ_SOURCE_NAMES)),
    ("test_run", list(tools.BASE_NAMES)),
])
def test_registry_selects_scope_schema_and_tier(monkeypatch, tmp_path, scope, expected):
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": "T"})
    run = _run(tmp_path); run["action_scope"] = scope
    definitions = tools.definitions_for_run(run)
    assert [d["name"] for d in definitions] == expected
    assert next(d for d in definitions if d["name"] == "register_document")["schema"] == tools.REGISTER_SCHEMAS[scope]
    assert all("oneOf" in d["schema"] or d["schema"]["additionalProperties"] is False for d in definitions)


# 0492 TR0026: advertisement now comes from the one common judgment, so T -- whose write
# scope 0427 T0004 recalled -- is advertised read-only, and TSR/TS join TR as read_write.
@pytest.mark.parametrize("step_type", ["N", "NR", "CH", "P", "T"])
def test_non_mutating_types_get_read_tier(monkeypatch, tmp_path, step_type):
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": step_type})
    assert [d["name"] for d in tools.definitions_for_run(_run(tmp_path))] == list(tools.BASE_NAMES) + list(READ_SOURCE_NAMES)


@pytest.mark.parametrize("step_type", ["TR", "TSR", "TS"])
def test_mutating_types_get_read_write_and_test_tier(monkeypatch, tmp_path, step_type):
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": step_type})
    assert [d["name"] for d in tools.definitions_for_run(_run(tmp_path))] == list(tools.BASE_NAMES) + list(tools.SOURCE_OPS) + ["run_test"]


@pytest.mark.parametrize("step_type", ["N", "NR", "T", "TR", "TSR", "TS"])
def test_advertised_source_tools_never_exceed_the_granted_scopes(monkeypatch, tmp_path, step_type):
    """Advertised == granted: both halves must resolve through tool_registry (0349 D0004 D-2)."""
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": step_type})
    advertised = {tools.SOURCE_OPS[d["name"]] for d in tools.definitions_for_run(_run(tmp_path)) if d["name"] in tools.SOURCE_OPS}
    kind, _reason = tools.tool_registry.kind_for_step("new", step_type)
    assert advertised == set(tools.tool_registry.tool_names(kind))


def test_schema_rejects_context_injection_before_handler():
    with pytest.raises(tools.ToolError) as caught:
        tools.validate(tools.SCHEMAS["read_source_file"], {"path": "x", "cwd": "C:/escape"})
    assert caught.value.reason == "schema_validation_failed"


def test_group_root_is_ledger_identity(monkeypatch, tmp_path):
    other = tmp_path / "other"; other.mkdir()
    monkeypatch.setattr(tools.git_service, "effective_src_root", lambda p, g: other)
    with pytest.raises(tools.ToolError) as caught:
        tools.require_group_root(_run(tmp_path))
    assert caught.value.reason == "group_worktree_unavailable"


def test_source_call_uses_same_root_and_live_token(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(tools, "require_group_root", lambda run: seen.append(Path(run["source_root"])) or Path(run["source_root"]))
    monkeypatch.setattr(tools.remote_tool_service, "handle", lambda op, token, body: (200, {"ok": True, "op": op, "token_seen": token, "body": body}))
    status, result = tools.source_call(_run(tmp_path), "live-token", "read_source_file", {"path": "a.py"})
    assert status == 200 and result["token_seen"] == "live-token" and result["body"] == {"path": "a.py"}
    assert seen == []


def test_run_test_is_sync_allowlisted_and_cwd_bound(monkeypatch, tmp_path):
    captured = {}
    class Proc:
        returncode = 7
        def communicate(self, timeout):
            captured["timeout"] = timeout
            return b"stdout-tail", b"stderr-tail"
    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Proc()
    monkeypatch.setattr(tools, "test_root", lambda _run: tmp_path)
    monkeypatch.setattr(tools.test_command_service, "current_os", lambda: "windows")
    monkeypatch.setattr(tools.test_command_service, "current_shell", lambda: "cmd.exe")
    monkeypatch.setattr(tools.test_command_service, "list_for_view", lambda _project: [{"command": "pytest -q", "verified_os": "windows"}])
    monkeypatch.setattr(tools.subprocess, "Popen", popen)
    status, result = tools.run_test(_run(tmp_path), {"command": " pytest   -q "}, 9)
    assert status == 200 and result["exit_code"] == 7
    assert captured["cwd"] == tmp_path and captured["timeout"] == 9
    assert set(captured["env"]) == {"PATH", "SYSTEMROOT", "TEMP", "TMP"}


@pytest.mark.parametrize("kind", ["openai", "claude"])
def test_provider_round_trip_preserves_multiple_calls(monkeypatch, kind):
    specs = [{"name": "read_source_file", "description": "read", "schema": tools.SCHEMAS["read_source_file"]}, {"name": "run_test", "description": "test", "schema": tools.SCHEMAS["run_test"]}]
    captured = {}
    if kind == "openai":
        response = {"choices": [{"message": {"content": None, "tool_calls": [{"id": "1", "function": {"name": "read_source_file", "arguments": '{"path":"a.py"}'}}, {"id": "2", "function": {"name": "run_test", "arguments": '{"command":"pytest -q"}'}}]}}]}
        monkeypatch.setattr(invoke, "_http_post_json", lambda u, h, b, t: captured.update(body=b) or response)
        _, calls, _ = invoke._call_openai("https://example", "m", "k", [], 1, specs, "", {}, False)
        assert captured["body"]["tools"][1]["function"]["name"] == "run_test"
    else:
        response = {"content": [{"type": "tool_use", "id": "1", "name": "read_source_file", "input": {"path": "a.py"}}, {"type": "tool_use", "id": "2", "name": "run_test", "input": {"command": "pytest -q"}}]}
        monkeypatch.setattr(invoke, "_http_post_json", lambda u, h, b, t: captured.update(body=b) or response)
        _, calls, _ = invoke._call_anthropic("https://example", "m", "k", [], 1, specs, "", {}, False)
        assert captured["body"]["tools"][1]["name"] == "run_test"
    assert [call["name"] for call in calls] == ["read_source_file", "run_test"]


def test_api_capabilities_follow_toolset_readiness(monkeypatch):
    monkeypatch.setattr(tools, "ready", lambda: True)
    value = caps.provider_capabilities({"id": "x", "enabled": True, "exec_type": "api"})
    assert value["source_read"] and value["source_write"] and value["test"]
    assert not value["shell"]


@pytest.mark.parametrize("case", ["validation_failure", "register_midstream_and_non_2xx"])
def test_dispatcher_returns_a_result_for_every_call_id(monkeypatch, tmp_path, case):
    specs = [
        {"name": name, "description": name, "schema": tools.SCHEMAS[name]}
        for name in ("read_source_file", "read_document", "read_help", "run_test")
    ] + [{
        "name": "register_document",
        "description": "register",
        "schema": tools.REGISTER_SCHEMAS["new"],
    }]
    register_input = {"doc_type": "TR", "title": "report", "content": "complete"}
    if case == "validation_failure":
        first_calls = [
            {"id": "valid", "name": "read_document", "input": {"lines": {"start": 1, "end": 5}, "chars": None}},
            {"id": "help", "name": "read_help", "input": {}},
            {"id": "invalid", "name": "run_test", "input": {}},
            {"id": "register", "name": "register_document", "input": register_input},
        ]
    else:
        first_calls = [
            {"id": "failed", "name": "read_source_file", "input": {"path": "a.py"}},
            {"id": "register", "name": "register_document", "input": register_input},
            {"id": "help", "name": "read_help", "input": {}},
            {"id": "trailing", "name": "read_document", "input": {}},
        ]

    provider_conversations = []
    provider_calls = iter([
        (None, first_calls, {"role": "assistant", "content": None}),
        (None, [{"id": "finish", "name": "register_document", "input": register_input}],
         {"role": "assistant", "content": None}),
    ])

    def call_provider(_url, _model, _key, conversation, *_args):
        provider_conversations.append(list(conversation))
        return next(provider_calls)

    registrations = iter([(422, {"error": "retry"}), (200, {"ok": True, "doc_id": "d"})])
    monkeypatch.setattr(invoke.ai_settings_service, "get_provider_secret", lambda *_args: "key")
    monkeypatch.setattr(invoke.api_server_tools, "definitions_for_run", lambda _run: specs)
    monkeypatch.setattr(invoke, "_call_openai", call_provider)
    monkeypatch.setattr(invoke, "_remaining_sec", lambda _run: 30)
    monkeypatch.setattr(invoke, "_inbox_register", lambda *_args: next(registrations))
    seen_help = []
    seen_document = []
    monkeypatch.setattr(invoke, "_api_read_document",
                        lambda _run, _token, body: seen_document.append(body) or (200, {"ok": True}))
    monkeypatch.setattr(invoke.api_server_tools, "read_help",
                        lambda _run, _token, body: seen_help.append(body) or (200, {"ok": True}))
    monkeypatch.setattr(invoke.api_server_tools, "source_call",
                        lambda *_args: (500, {"error": "source failed"}))

    run = _run(tmp_path)
    run.update({
        "run_id": "run",
        "raw_token": "token",
        "docs_target": 1,
        "mode": "single",
        "cancel_event": SimpleNamespace(is_set=lambda: False),
        "timed_out": False,
    })
    status, detail = invoke._api_execute(
        {"id": "provider", "kind": "openai", "api_base_url": "https://example", "api_model": "m"},
        "prompt",
        run,
    )

    assert (status, detail) == ("started_ok", None)
    returned = [
        message for message in provider_conversations[1]
        if message.get("role") == "tool"
    ]
    returned_ids = {message["tool_call_id"] for message in returned}
    assert returned_ids == {call["id"] for call in first_calls}
    assert seen_help == [{}]
    if case == "validation_failure":
        assert seen_document == [{"lines": {"start": 1, "end": 5}}]
    if case == "validation_failure":
        invalid = next(message for message in returned if message["tool_call_id"] == "invalid")
        assert "schema_validation_failed" in invalid["content"]
    else:
        assert {"failed", "trailing"} <= returned_ids


def test_full_remote_source_toolset_is_exposed_without_worktree(monkeypatch, tmp_path):
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": "TR"})
    names = [item["name"] for item in tools.definitions_for_run(_run(tmp_path))]
    assert names == list(tools.BASE_NAMES) + list(tools.SOURCE_NAMES)
    assert {"read", "grep", "glob", "stat", "diff", "log", "patch", "write", "remove"} == set(tools.SOURCE_OPS.values())
    for name in tools.SOURCE_OPS:
        assert tools.SCHEMAS[name]["additionalProperties"] is False


def test_source_call_delegates_root_selection_to_remote_service(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "require_group_root", lambda _run: pytest.fail("adapter must not precheck root"))
    monkeypatch.setattr(tools.remote_tool_service, "handle", lambda op, token, body: (409, {"ok": False, "op": op}))
    assert tools.source_call(_run(tmp_path), "live", "remove_source_file", {"path": "gone.py"}) == (409, {"ok": False, "op": "remove"})


def _stub_route_response(status=200, payload=None):
    return SimpleNamespace(status_code=status, body=json.dumps(payload if payload is not None else {}).encode("utf-8"))


def test_read_document_ranges_are_converted_to_http_contract(monkeypatch, tmp_path):
    """0505 T0018: _api_read_document now calls document_routes.get_document_section
    in-process instead of routing a path string through _api_bound_request -- the a-b
    range contract this asserted moves from a URL suffix to an explicit kwarg."""
    captured = {}

    def fake_section(_request, _doc_ref, **kwargs):
        captured.update(kwargs)
        return _stub_route_response()

    monkeypatch.setattr(document_routes, "get_document_section", fake_section)
    invoke._api_read_document(_run(tmp_path), "live", {"lines": {"start": 2, "end": 4}})
    assert captured["lines"] == "2-4"
    captured.clear()
    invoke._api_read_document(_run(tmp_path), "live", {"chars": {"start": 0, "end": 12}})
    assert captured["chars"] == "0-12"


@pytest.mark.parametrize("tool_input, selector", [
    ({}, None),
    ({"section": "Overview"}, {"section": "Overview"}),
    ({"section_id": "intro"}, {"section_id": "intro"}),
    ({"lines": {"start": 1, "end": 1}}, {"lines": "1-1"}),
    ({"chars": {"start": 0, "end": 0}}, {"chars": "0-0"}),
])
def test_read_document_full_selector_and_boundary_contract(monkeypatch, tmp_path, tool_input, selector):
    calls = {}

    def fake_document(_request, _doc_ref):
        calls["doc"] = True
        return _stub_route_response()

    def fake_section(_request, _doc_ref, **kwargs):
        calls["section"] = kwargs
        return _stub_route_response()

    monkeypatch.setattr(document_routes, "get_document", fake_document)
    monkeypatch.setattr(document_routes, "get_document_section", fake_section)
    tools.validate(tools.SCHEMAS["read_document"], tool_input)
    invoke._api_read_document(_run(tmp_path), "live", tool_input)
    if selector is None:
        assert calls == {"doc": True}
    else:
        for key in ("section", "section_id", "lines", "chars"):
            assert calls["section"][key] == selector.get(key)


@pytest.mark.parametrize("tool_input", [
    {"lines": {"start": 4, "end": 2}},
    {"chars": {"start": 12, "end": 1}},
    {"section": "Overview", "lines": {"start": 1, "end": 2}},
    {"section_id": "intro", "chars": {"start": 0, "end": 2}},
    {"lines": {"start": 0, "end": 1}},
    {"chars": {"start": -1, "end": 0}},
])
def test_read_document_rejects_invalid_selector_and_ranges(tool_input):
    with pytest.raises(tools.ToolError) as caught:
        tools.validate(tools.SCHEMAS["read_document"], tool_input)
    assert caught.value.reason == "schema_validation_failed"


def test_read_document_null_selector_is_removed_before_http_conversion(monkeypatch, tmp_path):
    captured = {}

    def fake_section(_request, _doc_ref, **kwargs):
        captured.update(kwargs)
        return _stub_route_response()

    monkeypatch.setattr(document_routes, "get_document_section", fake_section)
    canonical = tools.normalize_read_document_input({"lines": {"start": 1, "end": 5}, "chars": None})
    assert canonical == {"lines": {"start": 1, "end": 5}}
    invoke._api_read_document(_run(tmp_path), "live", canonical)
    assert captured["lines"] == "1-5"
    assert captured["chars"] is None


@pytest.mark.parametrize("tool_input", [{"section": ""}, {"lines": {"start": 1}}, {"unknown": "x"}])
def test_read_document_flat_schema_keeps_invalid_nested_inputs_rejected(tool_input):
    with pytest.raises(tools.ToolError) as caught:
        tools.normalize_read_document_input(tool_input)
    assert caught.value.reason == "schema_validation_failed"


def test_read_help_uses_catalog_without_http(monkeypatch, tmp_path):
    token = {"continuation_locale": "en", "doc_ref": "d"}
    ctx = {"base_url": "/flowgate/api/v1", "locale": "en", "doc_id": "d", "doc_type": "T", "action_scope": "new", "tool_kind": "read", "source_mode": "remote", "reason": None}
    monkeypatch.setattr(tools.token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(tools.help_catalog, "resolve_context", lambda *_args: ctx)
    monkeypatch.setattr(tools.help_catalog, "build_index", lambda _ctx: {"items": [{"name": "submit"}], "hidden": []})
    status, payload = tools.read_help(_run(tmp_path), "live", {})
    assert status == 200 and payload["form"] == "index" and payload["items"] == [{"name": "submit"}]
    with pytest.raises(tools.ToolError) as caught:
        tools.normalize_read_help_input({"child": "read"})
    assert caught.value.reason == "schema_validation_failed"


@pytest.mark.parametrize("mode, config, root_exists, expected_reason", [
    ("integrated_healthy", {"enabled": True}, True, None),
    ("integrated_unresolved", {"enabled": True}, False, "group_worktree_unavailable"),
    ("non_git", None, True, None),
    ("integration_disabled", {"enabled": False}, True, None),
    ("missing_root", None, False, "source_root_unavailable"),
])
def test_project_mode_root_policy(monkeypatch, tmp_path, mode, config, root_exists, expected_reason):
    root = tmp_path / "selected"
    if root_exists:
        root.mkdir()
    run = _run(root)
    monkeypatch.setattr(tools.git_service.db_git, "get_config", lambda _project: config)
    monkeypatch.setattr(tools, "require_group_root", lambda _run: root if root_exists else (_ for _ in ()).throw(tools.ToolError(409, "group_worktree_unavailable")))
    if expected_reason:
        with pytest.raises(tools.ToolError) as caught:
            tools.test_root(run)
        assert caught.value.reason == expected_reason
    else:
        assert tools.test_root(run) == root


def test_git_integration_lookup_failure_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(tools.git_service.db_git, "get_config", lambda _project: (_ for _ in ()).throw(RuntimeError("database unavailable")))
    monkeypatch.setattr(tools, "require_group_root", lambda _run: pytest.fail("lookup failure must not select a root"))
    with pytest.raises(tools.ToolError) as caught:
        tools.test_root(_run(tmp_path))
    assert caught.value.reason == "git_integration_lookup_failed"

@pytest.mark.parametrize("step_type", ["TR", "TSR", "TS"])
def test_non_git_mutating_types_complete_read_mutation_test_and_register(monkeypatch, tmp_path, step_type):
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": step_type})
    seen = []
    monkeypatch.setattr(tools.remote_tool_service, "handle", lambda op, _token, _body: seen.append(op) or (200, {"ok": True, "op": op}))
    monkeypatch.setattr(tools, "run_test", lambda _run, _input, _remaining: seen.append("run_test") or (200, {"ok": True}))
    monkeypatch.setattr(invoke.ai_settings_service, "get_provider_secret", lambda *_args: "key")
    monkeypatch.setattr(invoke, "_remaining_sec", lambda _run: 30)
    monkeypatch.setattr(invoke, "_inbox_register", lambda *_args: (200, {"ok": True, "doc_id": "registered"}))
    calls = [
        {"id": "read", "name": "read_source_file", "input": {"path": "a.py"}},
        {"id": "write", "name": "write_source_file", "input": {"path": "a.py", "content": "x"}},
        {"id": "test", "name": "run_test", "input": {"command": "pytest -q"}},
        {"id": "register", "name": "register_document", "input": {"doc_type": "TR", "content": "complete"}},
    ]
    monkeypatch.setattr(invoke, "_call_openai", lambda *_args: (None, calls, {"role": "assistant", "content": None}))
    run = _run(tmp_path)
    run.update({"run_id": "run", "raw_token": "live", "docs_target": 1, "mode": "single", "cancel_event": SimpleNamespace(is_set=lambda: False), "timed_out": False})
    assert invoke._api_execute({"id": "provider", "kind": "openai", "api_base_url": "https://example", "api_model": "m"}, "prompt", run) == ("started_ok", None)
    assert seen == ["read", "write", "run_test"]


def test_read_document_schema_is_flat_and_nulls_are_local_only():
    schema = tools.SCHEMAS["read_document"]
    assert "oneOf" not in schema
    assert set(schema["properties"]) == {"section", "section_id", "lines", "chars"}
    assert tools.normalize_read_document_input({"section": None, "chars": None}) == {}
    # Null remains invalid for ordinary tools: normalization is deliberately tool-local.
    with pytest.raises(tools.ToolError):
        tools.validate(tools.SCHEMAS["read_source_file"], {"path": None})


@pytest.mark.parametrize("kind", ["openai", "claude"])
def test_provider_arguments_share_read_document_canonicalization(monkeypatch, kind):
    specs = [{"name": "read_document", "description": "read", "schema": tools.READ_DOCUMENT_SCHEMA}]
    if kind == "openai":
        response = {"choices": [{"message": {"content": None, "tool_calls": [{
            "id": "doc", "function": {"name": "read_document",
            "arguments": '{"lines":{"start":1,"end":5},"chars":null}'}
        }]}}]}
        monkeypatch.setattr(invoke, "_http_post_json", lambda *_args: response)
        _, calls, _ = invoke._call_openai("https://example", "m", "k", [], 1, specs, "", {}, False)
    else:
        response = {"content": [{"type": "tool_use", "id": "doc", "name": "read_document",
                                 "input": {"lines": {"start": 1, "end": 5}, "chars": None}}]}
        monkeypatch.setattr(invoke, "_http_post_json", lambda *_args: response)
        _, calls, _ = invoke._call_anthropic("https://example", "m", "k", [], 1, specs, "", {}, False)
    canonical = tools.normalize_read_document_input(calls[0]["input"])
    assert canonical == {"lines": {"start": 1, "end": 5}}


def test_read_help_item_child_visibility_supplier_and_no_url(monkeypatch, tmp_path):
    assert set(tools.READ_HELP_SCHEMA["properties"]) == {"item", "child"}
    token = {"continuation_locale": "en", "doc_ref": "d"}
    ctx = {"base_url": "/flowgate/api/v1", "locale": "en"}
    monkeypatch.setattr(tools.token_service, "verify", lambda _raw: token)
    monkeypatch.setattr(tools.help_catalog, "resolve_context", lambda *_args: ctx)
    monkeypatch.setattr(tools.help_catalog, "CATALOG_ORDER", ("visible", "hidden"))
    monkeypatch.setattr(tools.help_catalog, "decide_visibility",
                        lambda name, _ctx: SimpleNamespace(visible=name == "visible"))
    monkeypatch.setattr(tools.help_catalog, "enumerate_children",
                        lambda *_args: [{"name": "child"}])
    monkeypatch.setattr(tools.help_catalog, "build_item",
                        lambda name, _ctx: {"name": name, "form": "content"})
    monkeypatch.setattr(tools.help_catalog, "build_child",
                        lambda name, child, _ctx: {"name": name, "child": child})
    assert tools.read_help(_run(tmp_path), "live", {"item": "visible"})[1]["name"] == "visible"
    assert tools.read_help(_run(tmp_path), "live", {"item": "visible", "child": "child"})[1]["child"] == "child"
    assert tools.read_help(_run(tmp_path), "live", {"item": "missing"})[0] == 404
    assert tools.read_help(_run(tmp_path), "live", {"item": "hidden"})[0] == 403
    assert tools.read_help(_run(tmp_path), "live", {"item": "visible", "child": "missing"})[0] == 404
    monkeypatch.setattr(tools.help_catalog, "build_item",
                        lambda *_args: (_ for _ in ()).throw(tools.help_catalog.HelpSupplierError("failed")))
    status, payload = tools.read_help(_run(tmp_path), "live", {"item": "visible"})
    assert status == 500 and "live" not in str(payload)


def test_api_prompt_uses_read_help_while_http_mention_is_untouched():
    original = "first\nGET http://host/flowgate/api/v1/help\nlast"
    adapted = invoke._api_help_prompt(original)
    assert "read_help" in adapted
    assert "GET http://host/flowgate/api/v1/help" not in adapted
    assert original.endswith("\nlast")

def test_glm_openai_contract_dispatches_and_counts_received_calls(monkeypatch, tmp_path):
    captured = {}
    received = {"doc_type": "TR", "title": "GLM report", "content": "complete"}
    response = {"choices": [{"message": {"content": None, "tool_calls": [{
        "id": "glm-register", "type": "function", "function": {
            "name": "register_document", "arguments": received,
        },
    }]}}]}
    monkeypatch.setattr(invoke.ai_settings_service, "get_provider_secret", lambda *_args: "key")
    monkeypatch.setattr(invoke, "_http_post_json", lambda _u, _h, body, _t: captured.update(body=body) or response)
    dispatched = []
    monkeypatch.setattr(invoke, "_inbox_register", lambda _run, _token, payload: dispatched.append(payload) or (200, {"ok": True, "doc_id": "glm-doc"}))
    monkeypatch.setattr(invoke, "_remaining_sec", lambda _run: 30)

    run = _run(tmp_path)
    run.update({"run_id": "glm", "doc_ref": None, "raw_token": "token", "docs_target": 1, "mode": "single",
                "cancel_event": SimpleNamespace(is_set=lambda: False), "timed_out": False})
    assert invoke._api_execute(
        {"id": "glm", "kind": "custom", "api_base_url": "https://open.bigmodel.cn/api/paas/v4", "api_model": "glm-4"},
        "complete the task", run,
    ) == ("started_ok", None)
    assert captured["body"]["tools"] == [{"type": "function", "function": {
        "name": "register_document", "description": invoke._REGISTER_TOOL_DESC,
        "parameters": invoke._REGISTER_TOOL_SCHEMA,
    }}]
    assert captured["body"]["tool_choice"] == {"type": "function", "function": {"name": "register_document"}}
    assert captured["body"]["messages"][0]["role"] == "system"
    assert "natural-language claim" in captured["body"]["messages"][0]["content"]
    assert dispatched == [received]
    assert run["tool_calls_received"] == 1
    assert run["tool_calls_executed"] == 1
    assert run["tool_call_misses"] == 0


def test_openai_invalid_and_multiple_direct_calls_return_results_without_misses(monkeypatch, tmp_path):
    responses = iter([
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "bad-name", "function": {"name": "not_exposed", "arguments": "{}"}},
            {"id": "bad-json", "function": {"name": "register_document", "arguments": "{"}},
        ]}}]},
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "valid", "function": {"name": "register_document", "arguments": '{"doc_type":"TR","title":"ok","content":"done"}'}},
        ]}}]},
    ])
    conversations = []
    monkeypatch.setattr(invoke.ai_settings_service, "get_provider_secret", lambda *_args: "key")
    monkeypatch.setattr(invoke, "_http_post_json", lambda *_args: next(responses))
    monkeypatch.setattr(invoke, "_remaining_sec", lambda _run: 30)
    monkeypatch.setattr(invoke, "_inbox_register", lambda *_args: (200, {"ok": True, "doc_id": "ok"}))
    original = invoke._call_openai
    def capture(*args):
        conversations.append(list(args[3]))
        return original(*args)
    monkeypatch.setattr(invoke, "_call_openai", capture)

    run = _run(tmp_path)
    run.update({"run_id": "invalid", "doc_ref": None, "raw_token": "token", "docs_target": 1, "mode": "single",
                "cancel_event": SimpleNamespace(is_set=lambda: False), "timed_out": False})
    assert invoke._api_execute({"id": "glm", "kind": "custom", "api_base_url": "https://example", "api_model": "glm-4"}, "prompt", run) == ("started_ok", None)
    tool_results = [item for item in conversations[-1] if item.get("role") == "tool"]
    assert {item["tool_call_id"] for item in tool_results} == {"bad-name", "bad-json"}
    assert run["tool_calls_received"] == 3
    assert run["tool_calls_executed"] == 1
    assert run["tool_call_misses"] == 0


def test_non_glm_wrong_direct_tool_name_is_missed_and_not_dispatched(monkeypatch, tmp_path):
    valid = {"doc_type": "TR", "title": "ok", "content": "done"}
    responses = iter([
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "wrong", "function": {"name": "some_other_tool", "arguments": "{}"}},
        ]}}]},
        {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "register", "function": {"name": "register_document", "arguments": valid}},
        ]}}]},
    ])
    monkeypatch.setattr(invoke.ai_settings_service, "get_provider_secret", lambda *_args: "key")
    monkeypatch.setattr(invoke, "_http_post_json", lambda *_args: next(responses))
    monkeypatch.setattr(invoke, "_remaining_sec", lambda _run: 30)
    dispatched = []
    monkeypatch.setattr(
        invoke, "_inbox_register",
        lambda _run, _token, payload: dispatched.append(payload) or (200, {"ok": True, "doc_id": "ok"}),
    )

    run = _run(tmp_path)
    run.update({"run_id": "openai-wrong-name", "doc_ref": None, "raw_token": "token", "docs_target": 1,
                "mode": "single", "cancel_event": SimpleNamespace(is_set=lambda: False), "timed_out": False})
    assert invoke._api_execute(
        {"id": "openai", "kind": "openai", "api_base_url": "https://example", "api_model": "gpt-test"},
        "prompt", run,
    ) == ("started_ok", None)
    assert dispatched == [valid]
    assert run["tool_calls_received"] == 1
    assert run["tool_calls_executed"] == 1
    assert run["tool_call_misses"] == 1
