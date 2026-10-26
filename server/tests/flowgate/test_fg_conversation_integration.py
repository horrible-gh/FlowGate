"""AI invoke / turn integration of the Conversation contract (flowgate.v02.0032 T#1).

D0006 §2.3 (generic hook ports, composition root, handler failures never fail a run,
no-op without registration), §3.3/§3.4 (API tool and CLI transport), §3.7 (cancel,
finalize, startup recovery), §3.9 (reply re-anchor), §5.1 (HTTP routes) and completion
conditions 1-4, 8, 10, 14 and 26 end to end.
"""
from __future__ import annotations

import inspect
import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fg_conversation_support import (  # noqa: E402
    ADMIN, CHAT_DOC, GROUP, OTHER_USER, PROJECT, RUN_ID, USER, FakeExecutor, add_turn, bind_worktree,  # noqa: F401
    clean_commands_state, clean_hooks, clean_runs, fg_store, init_worktree, make_run, seed, set_policy,  # noqa: F401
    use_executor, wait_until,
)

from flowgate.conversation import commands  # noqa: E402
from flowgate.conversation import contracts as c  # noqa: E402
from flowgate.conversation import integration  # noqa: E402
from flowgate.conversation import legacy_adapter  # noqa: E402
from flowgate.conversation import repository as repo  # noqa: E402
from flowgate.conversation import routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import chat_run_hooks  # noqa: E402

SERVER_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture()
def env(fg_store, tmp_path, monkeypatch, clean_commands_state, clean_runs, clean_hooks):
    seed(fg_store)
    root = init_worktree(tmp_path / "wt")
    bind_worktree(monkeypatch, root)
    executor = FakeExecutor()
    use_executor(monkeypatch, executor)
    set_policy(monkeypatch, c.POLICY_USER_APPROVAL)
    events = []
    monkeypatch.setattr(legacy_adapter, "emit_event",
                        lambda event, project, group, payload: events.append((event, payload)))
    return {"root": root, "executor": executor, "events": events, "store": fg_store}


# ── ports without handlers ───────────────────────────────────────────────────

def test_unregistered_ports_are_no_ops(clean_hooks):
    run = {"action_scope": "chat", "run_id": "r"}
    assert chat_run_hooks.chat_tools(run) == []
    assert chat_run_hooks.chat_mention_sections("http://x/api/v1", "raw") == []
    assert chat_run_hooks.chat_run_cancel(run, lambda: None) is False
    chat_run_hooks.chat_run_start(run)
    chat_run_hooks.chat_run_finalize(run)
    chat_run_hooks.conversation_reply_appended("doc", {})
    assert "chat_command_capability" not in run


def test_handler_failures_never_escape(clean_hooks):
    def boom(*_a):
        raise RuntimeError("handler bug")

    for name in chat_run_hooks.HOOK_NAMES:
        chat_run_hooks.register(name, boom)
    run = {"action_scope": "chat"}
    chat_run_hooks.chat_run_start(run)
    chat_run_hooks.chat_run_finalize(run)
    chat_run_hooks.conversation_reply_appended("doc", {})
    assert chat_run_hooks.chat_tools(run) == []
    assert chat_run_hooks.chat_run_cancel(run, lambda: None) is False


def test_non_chat_runs_never_reach_the_handlers(clean_hooks):
    seen = []
    integration.register()
    chat_run_hooks.register(chat_run_hooks.ON_CHAT_RUN_START, lambda run: seen.append(run))
    chat_run_hooks.chat_run_start({"action_scope": "work"})
    assert seen == [] and chat_run_hooks.chat_tools({"action_scope": "work"}) == []


def test_register_connects_every_port_idempotently(clean_hooks):
    integration.register()
    integration.register()
    assert all(chat_run_hooks.registered(name) for name in chat_run_hooks.HOOK_NAMES)
    tools = chat_run_hooks.chat_tools({"action_scope": "chat", "chat_command_capability": True})
    assert [t["name"] for t in tools] == ["run_command"]
    # A run that was not admitted through the start hook has no command capability.
    assert chat_run_hooks.chat_tools({"action_scope": "chat"}) == []


# ── call sites in the existing engine (composition, order) ───────────────────

def _source(rel):
    return (SERVER_DIR / rel).read_text(encoding="utf-8")


def test_engine_call_sites_are_in_place():
    admission = _source("modules/flow_gate/services/ai_invoke/admission.py")
    assert admission.index("chat_run_hooks.chat_run_start(run)") < admission.index("_svc()._runs[run_id] = run")
    finalize = _source("modules/flow_gate/services/ai_invoke/finalize.py")
    steps = finalize[finalize.index("steps = ("):finalize.index("for name, action in steps")]
    assert steps.index('("source"') < steps.index('("chat_run"') < steps.index('("diagnostics"')
    chain = _source("modules/flow_gate/services/ai_invoke/chain.py")
    assert chain.index("chat_run_hooks.chat_run_cancel(run, announce)") < chain.index('run["cancel_event"].set()')
    turns = _source("modules/flow_gate/services/conversation_turn_service.py")
    assert turns.index("chat_run_hooks.conversation_reply_appended(doc_id, row)") < turns.index(
        "conversation_events.broadcast_turn_appended(doc, result)")
    startup = _source("startup.py")
    assert "integration.register()" in startup and "start_conversation_integration()" in startup
    main = _source("routers/main.py")
    assert "from flowgate.conversation.routes import router as _conversation_router" in main


def test_start_hook_grants_the_capability_and_snapshots(env):
    integration.register()
    add_turn(CHAT_DOC, "user")
    run = make_run(source_root=env["root"], capability=False, chat_start_seq=None, register=False)
    chat_run_hooks.chat_run_start(run)
    assert run["chat_command_capability"] is True
    assert run["chat_start_seq"] == 1
    assert run["source_start_tree"] and run["source_change_capture"] == c.CAPTURE_READY


def test_start_hook_without_worktree_keeps_the_run(env, monkeypatch):
    integration.register()
    bind_worktree(monkeypatch, None)
    run = make_run(source_root=env["root"], capability=False, register=False)
    chat_run_hooks.chat_run_start(run)
    assert run["chat_command_capability"] is True
    assert run["source_change_capture"] == c.CAPTURE_UNAVAILABLE
    # run_command then answers 409 worktree_unavailable as a tool result, not a failure.
    status, result = commands.run_tool(run, {"program": "git", "args": ["status"]})
    assert status == 409 and result["ok"] is False
    assert result["error"]["code"] == "chat_command_worktree_unavailable"


def test_cancel_through_the_engine_closes_commands_first(env):
    integration.register()
    run = make_run(source_root=env["root"])
    row = commands.create_request(run, {"program": "git", "args": ["status"]})
    assert svc.cancel_run(RUN_ID)["status"] == "cancelling"
    assert run["status"] == "cancelling" and run["cancel_event"].is_set()
    stored = repo.get_command(row["request_id"])
    assert stored["status"] == c.CANCELLED and stored["error_code"] == "chat_command_run_cancelled"
    with pytest.raises(c.ConversationError) as late:
        commands.create_request(run, {"program": "git"})
    assert late.value.code == "chat_command_run_cancelled"


def test_finalize_hook_closes_commands_then_records_the_change(env):
    integration.register()
    run = make_run(source_root=env["root"])
    chat_run_hooks.chat_run_start(run)
    pending = commands.create_request(run, {"program": "git", "args": ["status"]})
    (env["root"] / "made_by_run.txt").write_text("x\n", encoding="utf-8")
    run["status"] = "finished"
    chat_run_hooks.chat_run_finalize(run)
    assert repo.list_open_commands_for_run(RUN_ID) == []
    assert repo.get_command(pending["request_id"])["error_code"] == "chat_command_run_finished"
    change = repo.get_run_change(RUN_ID)
    assert change and [f["path"] for f in change["files"]] == ["made_by_run.txt"]
    assert ("chat_run_changes", {"project_id": PROJECT, "group_id": GROUP, "doc_id": CHAT_DOC,
                                 "run_id": RUN_ID, "files_changed": 1}) in env["events"]


def test_finalize_of_a_cancelled_run_uses_the_cancel_reason(env):
    integration.register()
    run = make_run(source_root=env["root"])
    pending = commands.create_request(run, {"program": "git"})
    run["cancel_event"].set()
    chat_run_hooks.chat_run_finalize(run)
    assert repo.get_command(pending["request_id"])["error_code"] == "chat_command_run_cancelled"


def test_reply_port_moves_the_anchor(env):
    integration.register()
    run = make_run(source_root=env["root"], chat_start_seq=0)
    row = commands.create_request(run, {"program": "git"})
    seq = add_turn(CHAT_DOC, "ai", RUN_ID)
    chat_run_hooks.conversation_reply_appended(CHAT_DOC, {"speaker": "ai", "source_run_id": RUN_ID, "seq": seq})
    stored = repo.get_command(row["request_id"])
    assert (stored["anchor_seq"], stored["anchor_position"], stored["anchor_state"]) == (seq, "before", "reply")


def test_startup_registers_and_recovers(env):
    import startup

    run = make_run(source_root=env["root"])
    row = commands.create_request(run, {"program": "git"})
    chat_run_hooks.clear()
    startup.start_conversation_integration()
    assert chat_run_hooks.registered(chat_run_hooks.PROVIDE_CHAT_TOOLS)
    stored = repo.get_command(row["request_id"])
    assert stored["status"] == c.CANCELLED and stored["error_code"] == "chat_command_server_restarted"


# ── the API provider tool loop (same live run, result back to the model) ─────

def test_api_worker_exposes_run_command_and_returns_the_result_to_the_same_run(env, monkeypatch):
    integration.register()
    set_policy(monkeypatch, c.POLICY_ALWAYS_APPROVE)
    noisy = ('"quoted"\n' * 9000)
    use_executor(monkeypatch, FakeExecutor(exit_code=1, stdout=noisy, stderr=noisy))
    run = make_run(source_root=env["root"])
    run.update({"raw_token": "raw-token", "docs_target": 0, "timed_out": False,
                "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1"})
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_conversation_context", lambda *_: (200, {"head_seq": 0, "turns": []}))
    registered = {}
    monkeypatch.setattr(svc, "_conversation_turn_register",
                        lambda run, token, payload: registered.update(payload=payload) or (201, {"ok": True}))
    calls = []

    def fake_call(*args):
        conversation, tool_name = args[3], args[5]
        calls.append({"tools": [t["name"] for t in tool_name], "conversation": list(conversation)})
        if len(calls) == 1:
            return None, [{"id": "call_cmd", "name": "run_command",
                           "input": {"program": "git", "args": ["status"]}}], {"role": "assistant"}
        return "done", [{"id": "call_reply", "name": "send_chat_reply", "input": {"body": "ran it"}}], {
            "role": "assistant"}

    monkeypatch.setattr(svc, "_call_openai", fake_call)
    outcome = svc._api_execute({"id": "p", "kind": "openai", "api_base_url": "https://api.example",
                                "api_model": "m"}, "prompt", run)
    assert outcome == ("started_ok", None)
    assert calls[0]["tools"] == ["run_command", "send_chat_reply"]
    assert "`run_command`" in calls[0]["conversation"][2]["content"]
    tool_msg = next(m for m in calls[1]["conversation"] if m.get("role") == "tool")
    assert tool_msg["tool_call_id"] == "call_cmd"
    assert len(tool_msg["content"]) <= c.AI_RESULT_MAX_CHARS
    result = json.loads(tool_msg["content"])
    assert result["status"] == "failed" and result["exit_code"] == 1
    assert result["stdout_truncated"] is True and result["stderr_truncated"] is True
    assert registered["payload"] == {"body": "ran it"}
    assert run["last_tool_name"] == "conversation_turn_register"


def test_api_worker_input_error_result_stays_whole_json(env, monkeypatch):
    integration.register()
    run = make_run(source_root=env["root"])
    run.update({"raw_token": "raw-token", "docs_target": 0, "timed_out": False,
                "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1"})
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_conversation_context", lambda *_: (200, {"head_seq": 0, "turns": []}))
    monkeypatch.setattr(svc, "_conversation_turn_register", lambda run, token, payload: (201, {"ok": True}))
    calls = []

    def fake_call(*args):
        calls.append(list(args[3]))
        if len(calls) == 1:
            bad = {"program": "git", "args": ["--git-dir=" + '"' * 8000, "status"]}
            return None, [{"id": "call_cmd", "name": "run_command", "input": bad}], {"role": "assistant"}
        return "done", [{"id": "call_reply", "name": "send_chat_reply", "input": {"body": "ok"}}], {
            "role": "assistant"}

    monkeypatch.setattr(svc, "_call_openai", fake_call)
    outcome = svc._api_execute({"id": "p", "kind": "openai", "api_base_url": "https://api.example",
                                "api_model": "m"}, "prompt", run)
    assert outcome == ("started_ok", None)
    tool_msg = next(m for m in calls[1] if m.get("role") == "tool")
    assert len(tool_msg["content"]) <= c.AI_RESULT_MAX_CHARS
    result = json.loads(tool_msg["content"])
    assert result["ok"] is False and result["error"]["code"] == "chat_command_invalid_cwd"
    assert result["meta_truncated"] is True


def test_api_worker_without_registration_keeps_the_single_reply_tool(clean_hooks, monkeypatch):
    seen = {}
    run = {"project_id": PROJECT, "run_id": "aiv_plain", "docs_target": 0, "raw_token": "raw",
           "token_id": "tok", "doc_ref": CHAT_DOC, "action_scope": "chat", "mode": "single",
           "cancel_event": threading.Event(), "provider": {"name": "x"}, "timed_out": False,
           "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1"}
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_remaining_sec", lambda _run: 60)
    monkeypatch.setattr(svc, "_conversation_context", lambda *_: (200, {"head_seq": 0, "turns": []}))
    monkeypatch.setattr(svc, "_conversation_turn_register", lambda *_: (201, {"ok": True}))

    def fake_call(*args):
        seen["tool_name"] = args[5]
        return "hi", {"id": "c1", "input": {"body": "hi"}}, {"role": "assistant"}

    monkeypatch.setattr(svc, "_call_openai", fake_call)
    assert svc._api_execute({"id": "p", "kind": "openai", "api_base_url": "https://x", "api_model": "m"},
                            "prompt", run) == ("started_ok", None)
    assert seen["tool_name"] == "send_chat_reply"


# ── the CLI mention ──────────────────────────────────────────────────────────

def _mention(command_execution):
    from modules.flow_gate.services import invoke_mention_service

    return invoke_mention_service.build_conversation_mention(
        doc_id=CHAT_DOC, project=PROJECT, module="v02", group_name=GROUP, raw_token="raw-run-token",
        token_id="tok_1", api_base_url="http://127.0.0.1:8089/flowgate/api/v1/",
        command_execution=command_execution,
    )


def test_cli_mention_section_only_for_a_live_run(env):
    integration.register()
    live = _mention(True)
    assert "## Command execution" in live
    assert "POST http://127.0.0.1:8089/flowgate/api/v1/chat-commands" in live
    assert "Authorization: Bearer raw-run-token" in live
    assert "## Command execution" not in _mention(False)
    chat_run_hooks.clear()
    assert "## Command execution" not in _mention(True)


# ── chat settings: the command policy ────────────────────────────────────────

def test_command_policy_is_a_chat_setting(env):
    from modules.flow_gate.services import chat_settings_service as css

    assert css.resolve_command_policy(USER) == "user_approval"
    css.save_chat_settings(USER, {"command_policy": "always_approve"})
    assert css.resolve_command_policy(USER) == "always_approve"
    settings, is_default = css.resolve_chat_settings(USER)
    assert settings["command_policy"] == "always_approve" and is_default is True
    with pytest.raises(css.ChatSettingsError):
        css.validate_patch({"command_policy": "sometimes"})
    response = css.settings_response(USER)
    assert response["settings"]["command_policy"] == "always_approve"
    assert response["domain"]["command_policy"] == ["always_approve", "user_approval", "reject"]


# ── HTTP routes ──────────────────────────────────────────────────────────────

def _client(user):
    app = FastAPI()
    app.include_router(routes.router, prefix="/flowgate/api/v1")
    app.dependency_overrides[routes.get_current_user] = lambda: user
    return TestClient(app)


def test_cli_routes_with_the_run_token(env, monkeypatch):
    run = make_run(source_root=env["root"])
    token = {"token_id": run["current_token_id"], "ai_run_id": RUN_ID, "project": PROJECT, "group_id": GROUP}
    monkeypatch.setattr(legacy_adapter, "verify_token", lambda raw: dict(token) if raw == "good" else (_ for _ in ()).throw(Exception("bad")))
    client = _client({"user_id": USER})
    res = client.post("/flowgate/api/v1/chat-commands", json={"program": "git", "args": ["status"], "wait_seconds": 0},
                      headers={"Authorization": "Bearer good"})
    assert res.status_code == 200 and res.json()["terminal"] is False
    rid = res.json()["request"]["request_id"]
    for headers in ({}, {"Authorization": "Bearer bad"}):
        denied = client.post("/flowgate/api/v1/chat-commands", json={"program": "git"}, headers=headers)
        assert denied.status_code == 403 and denied.json() == {
            "ok": False, "error": {"code": "chat_command_forbidden", "message": denied.json()["error"]["message"]}}
    bad_input = client.post("/flowgate/api/v1/chat-commands", json={"program": "bash"},
                            headers={"Authorization": "Bearer good"})
    assert bad_input.status_code == 422 and bad_input.json()["error"]["code"] == "chat_command_program_denied"
    commands.decide(rid, {"user_id": USER}, "reject")
    done = client.get(f"/flowgate/api/v1/chat-commands/{rid}?wait=not-a-number", headers={"Authorization": "Bearer good"})
    assert done.status_code == 200 and done.json()["terminal"] is True
    assert done.json()["result"]["rejected_by"] == "user"
    missing = client.get("/flowgate/api/v1/chat-commands/ccr_none?wait=0", headers={"Authorization": "Bearer good"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "chat_command_not_found"


def test_decision_route_contract(env):
    run = make_run(source_root=env["root"])
    row = commands.create_request(run, {"program": "git"})
    rid = row["request_id"]
    owner, stranger = _client({"user_id": USER}), _client({"user_id": OTHER_USER})
    res = stranger.post(f"/flowgate/api/v1/chat-commands/{rid}/decision", json={"decision": "approve"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "chat_command_decision_forbidden"
    res = owner.post(f"/flowgate/api/v1/chat-commands/{rid}/decision", json={"decision": "approve", "group_id": GROUP})
    assert res.status_code == 422 and res.json()["error"]["code"] == "chat_command_invalid_decision"
    res = owner.post(f"/flowgate/api/v1/chat-commands/{rid}/decision", json={"decision": "approve"})
    assert res.status_code == 200 and res.json()["ok"] is True
    assert res.json()["request"]["status"] in ("approved", "running", "succeeded")
    again = owner.post(f"/flowgate/api/v1/chat-commands/{rid}/decision", json={"decision": "approve"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "chat_command_already_decided"
    res = owner.post("/flowgate/api/v1/chat-commands/ccr_none/decision", json={"decision": "approve"})
    assert res.status_code == 404 and res.json()["error"]["code"] == "chat_command_not_found"
    assert wait_until(lambda: repo.get_command(rid)["status"] == c.SUCCEEDED)


def test_activity_and_diff_routes(env, monkeypatch):
    integration.register()
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda user, perm, project: True)
    run = make_run(source_root=env["root"])
    chat_run_hooks.chat_run_start(run)
    commands.create_request(run, {"program": "git"})
    (env["root"] / "keep.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    chat_run_hooks.chat_run_finalize(run)
    client = _client({"user_id": USER})
    body = client.get(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}").json()
    assert body["ok"] is True and len(body["commands"]) == 1 and len(body["changes"]) == 1
    assert body["commands"][0]["status"] == "cancelled"
    diff = client.get(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}/runs/{RUN_ID}/diff?path=keep.txt")
    assert diff.status_code == 200 and diff.json()["data"]["status"] == "M"
    missing = client.get(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}/runs/aiv_none/diff?path=keep.txt")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "chat_run_change_not_found"
    nodoc = client.get("/flowgate/api/v1/chat-activity/fgp.v02.0001.0042-CH")
    assert nodoc.status_code == 404 and nodoc.json()["error"]["code"] == "chat_document_not_found"
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda user, perm, project: False)
    denied = client.get(f"/flowgate/api/v1/chat-activity/{CHAT_DOC}")
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"


def test_route_handlers_are_plain_functions():
    for route in routes.router.routes:
        assert not inspect.iscoroutinefunction(route.endpoint), route.path
