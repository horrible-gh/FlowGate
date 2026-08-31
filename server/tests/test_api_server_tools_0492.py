import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.services import ai_invoke_service as invoke
from modules.flow_gate.services import api_server_tools as tools
from modules.flow_gate.services import provider_capability_service as caps


def _run(tmp_path):
    return {"project_id": "p", "group_id": "g", "doc_ref": "d", "action_scope": "new", "source_root": str(tmp_path)}


@pytest.mark.parametrize("scope", ["new", "edit", "review", "test_run"])
def test_registry_selects_scope_schema_and_tier(monkeypatch, tmp_path, scope):
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": "T"})
    monkeypatch.setattr(tools, "require_group_root", lambda _run: tmp_path)
    run = _run(tmp_path); run["action_scope"] = scope
    definitions = tools.definitions_for_run(run)
    assert [d["name"] for d in definitions] == list(tools.BASE_NAMES) + list(tools.SOURCE_NAMES)
    assert next(d for d in definitions if d["name"] == "register_document")["schema"] == tools.REGISTER_SCHEMAS[scope]
    assert all(d["schema"]["additionalProperties"] is False for d in definitions)


def test_non_source_type_gets_base_tier(monkeypatch, tmp_path):
    monkeypatch.setattr(tools.db_documents, "get_by_id", lambda _id: {"type_code": "P"})
    assert [d["name"] for d in tools.definitions_for_run(_run(tmp_path))] == list(tools.BASE_NAMES)


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
    assert status == 200 and result["token_seen"] == "live-token" and result["body"]["ref"] is None
    assert seen == [tmp_path, tmp_path]


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
    monkeypatch.setattr(tools, "require_group_root", lambda _run: tmp_path)
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
        for name in ("read_source_file", "read_document", "run_test")
    ] + [{
        "name": "register_document",
        "description": "register",
        "schema": tools.REGISTER_SCHEMAS["new"],
    }]
    register_input = {"doc_type": "TR", "title": "report", "content": "complete"}
    if case == "validation_failure":
        first_calls = [
            {"id": "valid", "name": "read_document", "input": {}},
            {"id": "invalid", "name": "run_test", "input": {}},
            {"id": "register", "name": "register_document", "input": register_input},
        ]
    else:
        first_calls = [
            {"id": "failed", "name": "read_source_file", "input": {"path": "a.py"}},
            {"id": "register", "name": "register_document", "input": register_input},
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
    monkeypatch.setattr(invoke, "_api_read_document", lambda *_args: (200, {"ok": True}))
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
    if case == "validation_failure":
        invalid = next(message for message in returned if message["tool_call_id"] == "invalid")
        assert "schema_validation_failed" in invalid["content"]
    else:
        assert {"failed", "trailing"} <= returned_ids
