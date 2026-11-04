"""0127 T#6: chat connection base (0010-L §2.1 W1~W4, §2.2~§2.4, §2.7, §3.1, §3.2, §4.3).

Unit level: the call acceptance port and the optional tool keys (W1/W2), the API worker's
acceptance/dispatch order (W3), the cancel/finalize gate hooks (W4), ``chat_authz``
(effective kind, claim state, liveness, ``authorize_chat_run``, ``chat_recheck``),
``call_ledger`` (server_call_id, frozen records, re-dispatch, identity loss, the 500 cap)
and ``chat_gate`` (per-run edit gate, drain, closed reasons). Token / claim rows are
replaced by in-memory reads except in the one real-SQLite test at the end.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _fg_conversation_support import clean_hooks  # noqa: E402,F401

from flowgate.card_document_edit import call_ledger, chat_authz, chat_errors, chat_gate, chat_tools  # noqa: E402
from flowgate.card_document_edit.errors import GraphApiError  # noqa: E402
from modules.flow_gate.services import chat_run_hooks  # noqa: E402

SERVER_DIR = Path(__file__).resolve().parents[2]
RUN_ID = "aiv_chat_0127"
TOKEN_ID = "tok_chat_0127"
USER = "usr_chat"
DOC_REF = "fgp.v02.0001.0001-CH"


def _future(hours=1):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def make_token(**over):
    token = {"token_id": TOKEN_ID, "action_scope": "chat", "ai_run_id": RUN_ID, "project": "fgp",
             "group_id": "fgp.v02.0001", "doc_ref": DOC_REF, "issued_to": USER, "source_access": "read_write",
             "revoked_at": None, "consumed_at": None, "expires_at": _future()}
    token.update(over)
    return token


def make_run(**over):
    run = {"run_id": RUN_ID, "action_scope": "chat", "doc_ref": DOC_REF, "project_id": "fgp",
           "group_id": "fgp.v02.0001", "current_token_id": TOKEN_ID, "token_id": TOKEN_ID, "issued_to": USER,
           "status": "running", "finished_at": None, "cancel_event": threading.Event()}
    run.update(over)
    return run


class Reads:
    """In-memory token rows / claim rows with a read counter (RECHECK_DB_READS, CB-04a)."""

    def __init__(self, monkeypatch):
        self.tokens = {TOKEN_ID: make_token()}
        self.access = {}
        self.token_reads = 0
        self.access_reads = 0
        self.fail = None
        self.verified = {}
        self.runs = {}

        def read_token(token_id):
            self.token_reads += 1
            if self.fail == "token":
                raise RuntimeError("db down")
            row = self.tokens.get(token_id)
            return dict(row) if row else None

        def read_access(user_id):
            self.access_reads += 1
            if self.fail == "access":
                raise RuntimeError("db down")
            row = self.access.get(user_id)
            return dict(row) if row else None

        def verify(raw):
            if raw not in self.verified:
                from fastapi import HTTPException
                raise HTTPException(status_code=401, detail="Token has been revoked")
            return dict(self.verified[raw])

        monkeypatch.setattr(chat_authz, "_read_token", read_token)
        monkeypatch.setattr(chat_authz, "_read_access_row", read_access)
        monkeypatch.setattr(chat_authz, "_verify_token", verify)
        monkeypatch.setattr(chat_authz, "_get_run_record", lambda run_id: self.runs.get(run_id))

    @property
    def reads(self):
        return self.token_reads + self.access_reads


@pytest.fixture
def reads(monkeypatch):
    return Reads(monkeypatch)


@pytest.fixture(autouse=True)
def fresh_gates():
    chat_gate.reset()
    yield
    chat_gate.reset()


def refused(fn, *args):
    with pytest.raises(GraphApiError) as info:
        fn(*args)
    return info.value


# ── W1 / W2: the call acceptance port and the optional descriptor keys ───────

def test_accept_port_is_a_no_op_without_a_handler_and_for_non_chat_runs(clean_hooks):
    assert chat_run_hooks.ACCEPT_CHAT_CALL in chat_run_hooks.HOOK_NAMES
    assert chat_run_hooks.accept_chat_call({"action_scope": "chat"}, {"id": "c"}) is None
    seen = []
    chat_run_hooks.register(chat_run_hooks.ACCEPT_CHAT_CALL, lambda run, call: seen.append(call) or "ctx")
    assert chat_run_hooks.accept_chat_call({"action_scope": "work"}, {"id": "c"}) is None
    assert chat_run_hooks.accept_chat_call(None, {"id": "c"}) is None
    assert seen == []
    assert chat_run_hooks.accept_chat_call({"action_scope": "chat"}, {"id": "c"}) == "ctx"
    assert seen == [{"id": "c"}]


def test_accept_port_failure_answers_none(clean_hooks):
    def boom(*_a):
        raise RuntimeError("ledger bug")

    chat_run_hooks.register(chat_run_hooks.ACCEPT_CHAT_CALL, boom)
    assert chat_run_hooks.accept_chat_call({"action_scope": "chat"}, {"id": "c"}) is None


def test_tool_keys_unchanged_and_optional_keys_are_checked(clean_hooks):
    assert chat_run_hooks.CHAT_TOOL_KEYS == ("name", "description", "schema", "handler")
    base = {"name": "t", "description": "d", "schema": {"type": "object"}, "handler": lambda run, inp: (200, {})}
    good = [dict(base), dict(base, name="t2", call_handler=lambda run, ctx: (200, {}), own_validation=True)]
    bad = [dict(base, name="b1", call_handler="not callable"), dict(base, name="b2", own_validation="yes"),
           {k: v for k, v in base.items() if k != "handler"}]
    chat_run_hooks.register(chat_run_hooks.PROVIDE_CHAT_TOOLS, lambda run: good + bad)
    assert [tool["name"] for tool in chat_run_hooks.chat_tools({"action_scope": "chat"})] == ["t", "t2"]


def test_card_document_descriptors_are_valid_tools_with_a_fail_closed_shell(clean_hooks):
    tools = chat_tools.descriptors()
    assert [tool["name"] for tool in tools] == ["read_card_document", "revise_card_document"]
    for tool in tools:
        assert tool["handler"] is chat_tools.stub_handler and tool["own_validation"] is True
        assert "call_handler" not in tool and tool["schema"]["additionalProperties"] is False
    assert tools[1]["schema"]["required"] == ["flow_id", "node_key", "slot", "expected_doc_id",
                                              "expected_revision", "content"]
    assert tools[1]["schema"]["properties"]["expected_revision"] == {"type": "integer", "minimum": 0}
    handler = lambda run, ctx: (200, {})  # noqa: E731
    attached = chat_tools.descriptors({"revise_card_document": handler})
    assert attached[1]["call_handler"] is handler and "call_handler" not in attached[0]
    with pytest.raises(TypeError):
        chat_tools.descriptor("read_card_document", "nope")
    with pytest.raises(ValueError):
        chat_tools.descriptor("run_command")
    chat_run_hooks.register(chat_run_hooks.PROVIDE_CHAT_TOOLS, lambda run: chat_tools.descriptors())
    assert len(chat_run_hooks.chat_tools({"action_scope": "chat"})) == 2


def test_stub_handler_authenticates_first_then_refuses_call_identity(reads):
    status, body = chat_tools.stub_handler(make_run(), {"flow_id": "x", "content": "never read"})
    assert status == 422 and body["ok"] is False and "server_ts" in body
    assert body["error"]["code"] == "invalid_request"
    assert body["error"]["params"] == {"errors": [{"field": "call_context", "reason": "invalid_format",
                                                   "validation": {"reason": "call_identity_unavailable"}}]}
    # CO-03: authentication / run life come before the identity refusal.
    reads.tokens[TOKEN_ID]["revoked_at"] = "2026-11-04T00:00:00+00:00"
    status, body = chat_tools.stub_handler(make_run(), {})
    assert status == 401 and body["error"]["code"] == "unauthorized" and body["error"]["params"] == {}
    reads.tokens[TOKEN_ID]["revoked_at"] = None
    status, body = chat_tools.stub_handler(make_run(status="cancelling"), {})
    assert status == 409 and body["error"] == {**body["error"], "code": "chat_document_run_cancelled",
                                               "params": {"run_id": RUN_ID}}
    status, body = chat_tools.stub_handler(make_run(status="finished"), {})
    assert status == 409 and body["error"]["code"] == "chat_document_run_finished"
    status, body = chat_tools.stub_handler(make_run(current_token_id=None, token_id=None), {})
    assert status == 403 and body["error"]["code"] == "chat_document_forbidden" and body["error"]["params"] == {}


# ── W3: the API worker accepts every call once, before validation ─────────────

def test_worker_accepts_each_call_once_before_validation_and_dispatches_the_context(clean_hooks, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service as svc
    from modules.flow_gate.services.ai_invoke import worker

    accepted, plain_inputs, context_calls = [], [], []

    def accept(run, call):
        accepted.append(json.loads(json.dumps({"name": call.get("name"), "input": call.get("input")})))
        return ("ctx", len(accepted))

    def plain(run, tool_input):
        plain_inputs.append(tool_input)
        return 200, {"ok": True, "plain": True}

    def by_context(run, ctx, *rest):
        context_calls.append((ctx, rest))
        return 200, {"ok": True, "context": list(ctx)}

    strict = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"],
              "additionalProperties": False}
    tools = [
        {"name": "plain_tool", "description": "d", "schema": strict, "handler": plain},
        {"name": "card_tool", "description": "d", "schema": strict, "handler": chat_tools.stub_handler,
         "call_handler": by_context, "own_validation": True},
    ]
    chat_run_hooks.register(chat_run_hooks.PROVIDE_CHAT_TOOLS, lambda run: tools)
    chat_run_hooks.register(chat_run_hooks.ACCEPT_CHAT_CALL, accept)
    run = {"project_id": "fgp", "run_id": "aiv_w3", "docs_target": 0, "raw_token": "raw", "token_id": "tok",
           "doc_ref": DOC_REF, "action_scope": "chat", "mode": "single", "cancel_event": threading.Event(),
           "provider": {"name": "x"}, "timed_out": False, "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1"}
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_remaining_sec", lambda _run: 60)
    monkeypatch.setattr(worker, "_retry_remaining_sec", lambda _run: 60)
    monkeypatch.setattr(svc, "_conversation_context", lambda *_: (200, {"head_seq": 0, "turns": []}))
    monkeypatch.setattr(svc, "_conversation_turn_register", lambda *_: (201, {"ok": True}))
    turns = []

    def fake_call(*args):
        turns.append(list(args[3]))
        if len(turns) == 1:
            return None, [
                {"id": "call_0", "name": "card_tool", "input": {"n": "not an integer", "extra": 1}},
                {"id": "call_1", "name": "plain_tool", "input": {"n": 3}},
                {"id": "call_2", "name": "plain_tool", "input": {"n": "bad"}},
            ], {"role": "assistant"}
        return "done", [{"id": "call_r", "name": "send_chat_reply", "input": {"body": "ok"}}], {"role": "assistant"}

    monkeypatch.setattr(svc, "_call_openai", fake_call)
    outcome = svc._api_execute({"id": "p", "kind": "openai", "api_base_url": "https://x", "api_model": "m"},
                               "prompt", run)
    assert outcome == ("started_ok", None)
    # Exactly once per received call, with the provider's original (unvalidated) input.
    assert accepted == [{"name": "card_tool", "input": {"n": "not an integer", "extra": 1}},
                        {"name": "plain_tool", "input": {"n": 3}},
                        {"name": "plain_tool", "input": {"n": "bad"}},
                        {"name": "send_chat_reply", "input": {"body": "ok"}}]
    # own_validation skipped the schema; the call handler got ONLY the context of that call.
    assert context_calls == [(("ctx", 1), ())]
    # The existing (run, input) contract is unchanged and still schema-validated.
    assert plain_inputs == [{"n": 3}]
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in turns[1] if m.get("role") == "tool"}
    assert results["call_0"]["context"] == ["ctx", 1]
    assert results["call_1"]["plain"] is True
    assert results["call_2"]["ok"] is False


def test_worker_source_accepts_before_any_validation():
    text = (SERVER_DIR / "modules/flow_gate/services/ai_invoke/worker.py").read_text(encoding="utf-8")
    accept = text.index("chat_run_hooks.accept_chat_call(run, call)")
    loop = text[accept:]
    assert accept < text.index('api_server_tools.validate(spec["schema"], tool_input)')
    assert loop.index("normalize_read_document_input") > 0
    assert "chat_tool_call_handlers[call[\"name\"]](run, call_context)" in text
    assert "chat_tool_handlers[call[\"name\"]](run, call[\"input\"])" in text


# ── W4: cancel / finalize close the edit gate ────────────────────────────────

def test_cancel_hook_closes_the_gate_before_the_command_stop(monkeypatch):
    from flowgate.conversation import commands, integration

    seen = []
    monkeypatch.setattr(commands, "stop_run",
                        lambda run_id, on_closed=None: seen.append(chat_gate.closed_reason(run_id)) or on_closed())
    announced = []
    integration.on_chat_run_cancel(make_run(), lambda: announced.append(True))
    assert seen == [chat_gate.CANCELLED] and announced == [True]


def test_cancel_hook_still_stops_commands_when_the_gate_fails(monkeypatch):
    from flowgate.conversation import commands, integration

    monkeypatch.setattr(chat_gate, "close", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gate bug")))
    stopped = []
    monkeypatch.setattr(commands, "stop_run", lambda run_id, on_closed=None: stopped.append(run_id))
    integration.on_chat_run_cancel(make_run(), lambda: None)
    assert stopped == [RUN_ID]


def test_finalize_hook_closes_finished_first_and_keeps_a_cancel(monkeypatch):
    from flowgate.conversation import commands, integration, run_changes

    order = []
    monkeypatch.setattr(commands, "cancel_for_run",
                        lambda run_id, reason: order.append(("commands", chat_gate.closed_reason(run_id))))
    monkeypatch.setattr(run_changes, "finalize_run", lambda run: order.append(("changes", None)))
    integration.on_chat_run_finalize(make_run())
    assert order == [("commands", chat_gate.FINISHED), ("changes", None)]
    other = make_run(run_id="aiv_other", post_process_recovered=True)
    chat_gate.close("aiv_other", chat_gate.CANCELLED, 0)
    integration.on_chat_run_finalize(other)
    assert chat_gate.closed_reason("aiv_other") == chat_gate.CANCELLED


def test_integration_registers_every_port_including_call_acceptance(clean_hooks):
    from flowgate.conversation import integration

    integration.register()
    assert all(chat_run_hooks.registered(name) for name in chat_run_hooks.HOOK_NAMES)
    ctx = chat_run_hooks.accept_chat_call(make_run(), {"id": "call_0", "name": "x", "input": {}})
    assert isinstance(ctx, call_ledger.CallContext) and ctx.run_id == RUN_ID and ctx.tool_call_id == "call_0"
    # run_command keeps its (run, input) descriptor alongside the T#7 card tools.
    tools = chat_run_hooks.chat_tools({"action_scope": "chat", "chat_command_capability": True})
    assert [t["name"] for t in tools] == ["run_command", "read_card_document", "revise_card_document"]
    assert "call_handler" not in tools[0] and all(callable(t["call_handler"]) for t in tools[1:])


# ── §2.2 effective kind, claim state, liveness ───────────────────────────────

@pytest.mark.parametrize("source_access, kind", [
    ("read_write", "read_write"), ("read", "read"), (None, "read"), ("admin", "read"), ("", "read"),
])
def test_effective_write_kind_is_the_issue_time_column(source_access, kind):
    assert chat_authz.effective_write_kind(make_token(source_access=source_access)) == kind


def test_effective_write_kind_out_of_domain_is_refused(monkeypatch):
    monkeypatch.setattr(chat_authz, "_kind_for_token", lambda token: ("none", "token_scope_none"))
    assert chat_authz.effective_write_kind(make_token()) == "none"
    error = chat_errors.edit_mode_denied(RUN_ID, "weird")
    assert error.status == 403 and error.params == {"run_id": RUN_ID, "effective_kind": "none",
                                                    "required_kind": "read_write"}


def test_claim_pending_only_reads_the_marker(reads):
    token = make_token()
    assert chat_authz.claim_pending(token) is False                     # no row
    reads.access[USER] = {"source_access_mode": "edit_once", "one_shot_token_id": None}
    assert chat_authz.claim_pending(token) is False                     # AVAILABLE
    reads.access[USER] = {"source_access_mode": "read_only", "one_shot_token_id": TOKEN_ID}
    assert chat_authz.claim_pending(token) is True                      # CLAIMED by this token
    reads.access[USER] = {"source_access_mode": "read_only", "one_shot_token_id": "tok_other"}
    assert chat_authz.claim_pending(token) is False
    reads.fail = "access"
    assert refused(chat_authz.claim_pending, token).code == "internal_error"   # E8: never "allowed"


def test_token_live_keeps_the_verify_priority():
    now = datetime.now(timezone.utc)
    assert chat_authz.token_live(None, now) == chat_authz.REVOKED
    assert chat_authz.token_live(make_token(revoked_at="x", consumed_at="y", expires_at="2000-01-01T00:00:00"),
                                 now) == chat_authz.REVOKED
    assert chat_authz.token_live(make_token(consumed_at="y", expires_at="2000-01-01T00:00:00"), now) == \
        chat_authz.CONSUMED
    assert chat_authz.token_live(make_token(expires_at=now.isoformat()), now) == chat_authz.EXPIRED   # <= now
    assert chat_authz.token_live(make_token(expires_at="2000-01-01T00:00:00"), now) == chat_authz.EXPIRED
    assert chat_authz.token_live(make_token(expires_at="garbage"), now) == chat_authz.EXPIRED
    assert chat_authz.token_live(make_token(), now) == chat_authz.LIVE


# ── §2.3 authorize_chat_run ──────────────────────────────────────────────────

def test_api_authorizes_the_live_current_token(reads):
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    assert run["run_id"] == RUN_ID and token["token_id"] == TOKEN_ID and reads.token_reads == 1


def test_api_missing_current_token_id_is_403_before_any_token_read(reads):
    error = refused(chat_authz.authorize_chat_run, chat_authz.API, None, make_run(current_token_id=None, token_id=""))
    assert (error.status, error.code, error.params) == (403, "chat_document_forbidden", {})
    assert reads.token_reads == 0                                        # CB-04a


@pytest.mark.parametrize("change", [
    {"revoked_at": "2026-11-04T00:00:00+00:00"}, {"consumed_at": "2026-11-04T00:00:00+00:00"},
    {"expires_at": "2000-01-01T00:00:00+00:00"}, None,
])
def test_api_dead_token_behind_an_id_is_401(reads, change):
    if change is None:
        reads.tokens.clear()                                            # CB-04d: row gone
    else:
        reads.tokens[TOKEN_ID].update(change)                           # CB-04c
    error = refused(chat_authz.authorize_chat_run, chat_authz.API, None, make_run())
    assert (error.status, error.code, error.params) == (401, "unauthorized", {})


def test_api_without_a_run_is_finished(reads):
    error = refused(chat_authz.authorize_chat_run, chat_authz.API, None, None)
    assert (error.status, error.code) == (409, "chat_document_run_finished")


def test_current_token_falls_back_to_token_id(reads):
    chat_authz.authorize_chat_run(chat_authz.API, None, make_run(current_token_id=None))     # CB-03
    reads.tokens["tok_new"] = make_token(token_id="tok_new")
    error = refused(chat_authz.check_run_token_binding, make_run(current_token_id=None), reads.tokens["tok_new"])
    assert error.code == "chat_document_forbidden"


@pytest.mark.parametrize("run_change, token_change", [
    ({}, {"action_scope": "edit"}),                 # not a chat token
    ({}, {"ai_run_id": ""}),
    ({"action_scope": "work"}, {}),                 # CB-02
    ({"doc_ref": ""}, {}),
    ({}, {"doc_ref": "fgp.v02.0001.0002-CH"}),      # CB-01
    ({}, {"doc_ref": None}),
    ({"project_id": "other"}, {}),
    ({"group_id": "fgp.v02.0009"}, {}),
    ({}, {"ai_run_id": "aiv_other"}),
    ({"current_token_id": "tok_newer"}, {}),        # not the current token
    ({"issued_to": "usr_b"}, {}),                   # CB-07
    ({}, {"issued_to": None}),
])
def test_binding_failures_are_forbidden_without_facts(run_change, token_change):
    error = refused(chat_authz.check_run_token_binding, make_run(**run_change), make_token(**token_change))
    assert (error.status, error.code, error.params) == (403, "chat_document_forbidden", {})


def test_binding_uses_the_project_column_fallback():
    token = make_token()
    token.pop("project")
    token["project_id"] = "fgp"
    chat_authz.check_run_token_binding(make_run(), token)


@pytest.mark.parametrize("run_change, gate, code", [
    ({"status": "cancelling"}, None, "chat_document_run_cancelled"),
    ({}, "cancelled", "chat_document_run_cancelled"),          # drain: status still running (CG-02a)
    ({"status": "finished"}, None, "chat_document_run_finished"),
    ({"finished_at": "2026-11-04T00:00:00Z"}, None, "chat_document_run_finished"),
    ({}, "finished", "chat_document_run_finished"),            # CG-04
    ({"status": "cancelling"}, "finished", "chat_document_run_cancelled"),   # CG-04a
    ({"status": "finished"}, "cancelled", "chat_document_run_cancelled"),    # CG-02c: first reason kept
])
def test_run_life_codes_follow_status_then_the_gate_reason(reads, run_change, gate, code):
    if gate:
        chat_gate.close(RUN_ID, gate, 0)
    error = refused(chat_authz.authorize_chat_run, chat_authz.API, None, make_run(**run_change))
    assert (error.status, error.code, error.params) == (409, code, {"run_id": RUN_ID})


def test_cancel_event_is_cancelled(reads):
    run = make_run()
    run["cancel_event"].set()
    assert refused(chat_authz.authorize_chat_run, chat_authz.API, None, run).code == "chat_document_run_cancelled"


def test_cli_verifies_the_bearer_then_the_run(reads):
    assert refused(chat_authz.authorize_chat_run, chat_authz.CLI, None, None).code == "unauthorized"
    error = refused(chat_authz.authorize_chat_run, chat_authz.CLI, "revoked", None)
    assert (error.status, error.code, error.params, error.message) == (401, "unauthorized", {},
                                                                       "Token has been revoked")
    reads.verified["good"] = make_token()
    error = refused(chat_authz.authorize_chat_run, chat_authz.CLI, "good", None)
    assert (error.status, error.code) == (409, "chat_document_run_finished")      # CG-07: run gone
    reads.runs[RUN_ID] = make_run()
    run, token = chat_authz.authorize_chat_run(chat_authz.CLI, "good", None)
    assert run is reads.runs[RUN_ID] and token["token_id"] == TOKEN_ID
    reads.runs[RUN_ID] = make_run(current_token_id=None, token_id=None)            # CB-04b
    error = refused(chat_authz.authorize_chat_run, chat_authz.CLI, "good", None)
    assert (error.status, error.code, error.params) == (403, "chat_document_forbidden", {})
    reads.verified["work"] = make_token(action_scope="edit")
    assert refused(chat_authz.authorize_chat_run, chat_authz.CLI, "work", None).code == "chat_document_forbidden"


def test_api_and_cli_answer_the_same_for_an_id_less_run(reads):
    reads.verified["good"] = make_token()
    reads.runs[RUN_ID] = make_run(current_token_id=None, token_id=None)
    api = refused(chat_authz.authorize_chat_run, chat_authz.API, None, reads.runs[RUN_ID])
    cli = refused(chat_authz.authorize_chat_run, chat_authz.CLI, "good", None)
    assert (api.status, api.code, api.params) == (cli.status, cli.code, cli.params)
    reads.runs[RUN_ID]["current_token_id"] = TOKEN_ID                              # CB-04f
    chat_authz.authorize_chat_run(chat_authz.API, None, reads.runs[RUN_ID])


# ── §2.7 chat_recheck (inside the boundary) ──────────────────────────────────

def test_recheck_passes_the_gate_with_exactly_two_reads(reads):
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    before = reads.reads
    holder = chat_gate.TicketHolder()
    chat_authz.chat_recheck(run, token, holder)
    assert reads.reads - before == chat_authz.RECHECK_DB_READS == 2
    assert chat_gate.state(RUN_ID) == ("OPEN", 1)
    chat_gate.release(holder.ticket)
    assert chat_gate.state(RUN_ID) == ("OPEN", 0)


def test_recheck_on_a_closed_gate_reads_nothing(reads):
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    chat_gate.close(RUN_ID, chat_gate.CANCELLED, 0)
    before, holder = reads.reads, chat_gate.TicketHolder()
    error = refused(chat_authz.chat_recheck, run, token, holder)
    assert error.code == "chat_document_run_cancelled" and reads.reads == before and holder.ticket is None


@pytest.mark.parametrize("mutate, code", [
    (lambda r: r.tokens[TOKEN_ID].update(revoked_at="x"), "unauthorized"),                 # CB-06
    (lambda r: r.tokens[TOKEN_ID].update(consumed_at="x"), "unauthorized"),                # CG-05
    (lambda r: r.tokens[TOKEN_ID].update(source_access="read"), "chat_document_edit_mode_denied"),
    (lambda r: r.access.update({USER: {"one_shot_token_id": TOKEN_ID}}), "chat_document_forbidden"),   # CM-12
])
def test_recheck_refusals_hold_a_released_ticket(reads, mutate, code):
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    mutate(reads)
    holder = chat_gate.TicketHolder()
    try:
        assert refused(chat_authz.chat_recheck, run, token, holder).code == code
    finally:
        chat_gate.release(holder.ticket)
    assert chat_gate.state(RUN_ID) == ("OPEN", 0)


def test_recheck_rejects_a_replaced_current_token_even_while_it_is_live(reads):
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    run["current_token_id"] = "tok_next"                                 # CB-05
    holder = chat_gate.TicketHolder()
    error = refused(chat_authz.chat_recheck, run, token, holder)
    chat_gate.release(holder.ticket)
    assert (error.status, error.code) == (403, "chat_document_forbidden")


def test_recheck_mode_denied_params_and_db_failure(reads):
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    reads.tokens[TOKEN_ID]["source_access"] = None
    holder = chat_gate.TicketHolder()
    error = refused(chat_authz.chat_recheck, run, token, holder)
    chat_gate.release(holder.ticket)
    assert error.params == {"run_id": RUN_ID, "effective_kind": "read", "required_kind": "read_write"}
    reads.tokens[TOKEN_ID]["source_access"] = "read_write"
    reads.fail = "token"
    holder = chat_gate.TicketHolder()
    error = refused(chat_authz.chat_recheck, run, token, holder)
    chat_gate.release(holder.ticket)
    assert (error.status, error.code) == (500, "internal_error")         # E8


def test_recheck_status_defence_for_a_cancel_that_skipped_the_gate(reads):
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    run["status"] = "cancelling"
    holder = chat_gate.TicketHolder()
    assert refused(chat_authz.chat_recheck, run, token, holder).code == "chat_document_run_cancelled"
    chat_gate.release(holder.ticket)
    run["status"] = "finished"
    holder = chat_gate.TicketHolder()
    assert refused(chat_authz.chat_recheck, run, token, holder).code == "chat_document_run_finished"
    chat_gate.release(holder.ticket)


def test_chat_authz_never_writes():
    text = (SERVER_DIR / "flowgate/card_document_edit/chat_authz.py").read_text(encoding="utf-8")
    for forbidden_call in ("upsert(", ".claim(", ".commit(", ".rollback(", "revoke(", "consume(", "_execute(",
                           "UPDATE ", "INSERT "):
        assert forbidden_call not in text


# ── §2.4 / §3.2 / §4.3 call ledger ───────────────────────────────────────────

def _uuid4(value):
    return uuid.UUID(value).version == 4 and str(uuid.UUID(value)) == value


def test_every_acceptance_gets_its_own_uuid4_even_for_a_repeated_provider_id():
    run = make_run()
    first = call_ledger.accept_call(run, {"id": "call_0", "name": "revise_card_document", "input": {"a": 1}})
    second = call_ledger.accept_call(run, {"id": "call_1", "name": "read_card_document", "input": {}})
    run["_api_turn"] = 2
    third = call_ledger.accept_call(run, {"id": "call_0", "name": "revise_card_document", "input": {"a": 1}})
    ids = [first.server_call_id, second.server_call_id, third.server_call_id]
    assert all(_uuid4(i) for i in ids) and len(set(ids)) == 3            # CI-01 / CI-02
    keys = [call_ledger.api_key(call_ledger.resolve_call(run, ctx)) for ctx in (first, second, third)]
    assert len(set(keys)) == 3
    assert call_ledger.resolve_call(run, third).turn_seq == 2


def test_api_key_form():
    run = make_run()
    ctx = call_ledger.accept_call(run, {"id": "call_0", "name": "x", "input": {}})
    record = call_ledger.resolve_call(run, ctx)
    key = call_ledger.api_key(record)
    raw = json.dumps([RUN_ID, ctx.server_call_id, "call_0"], separators=(",", ":"))
    assert key == "chat-api:" + hashlib.sha256(raw.encode("utf-8")).hexdigest() and len(key) == 73
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key)


def test_records_are_frozen_copies_and_fingerprints_are_key_sorted():
    run = make_run()
    data = {"content": "body", "nested": {"list": [1, 2]}, "expected_revision": 7}
    ctx = call_ledger.accept_call(run, {"id": "call_0", "name": "x", "input": data})
    data["content"] = "changed"
    data["nested"]["list"].append(3)
    record = call_ledger.resolve_call(run, ctx)
    assert record.input_copy() == {"content": "body", "nested": {"list": [1, 2]}, "expected_revision": 7}
    with pytest.raises(TypeError):
        record.input["content"] = "x"
    with pytest.raises(Exception):
        record.input_sha256 = "x"
    copy_ = record.input_copy()
    copy_["content"] = "local"
    assert call_ledger.resolve_call(run, ctx).input["content"] == "body"
    assert call_ledger.sha256_of({"b": 1, "a": [1, {"d": 2, "c": 3}]}) == \
        call_ledger.sha256_of({"a": [1, {"c": 3, "d": 2}], "b": 1})


def test_redispatch_reads_the_same_record_and_state_transitions():
    run = make_run()
    ctx = call_ledger.accept_call(run, {"id": "call_0", "name": "x", "input": {"n": 1}})
    assert call_ledger.state_of(run, ctx.server_call_id) == (call_ledger.ACCEPTED, 0)
    with call_ledger.dispatching(run, ctx) as record:
        assert call_ledger.state_of(run, ctx.server_call_id) == (call_ledger.DISPATCHED, 1)
    assert call_ledger.state_of(run, ctx.server_call_id) == (call_ledger.DONE, 1)
    with pytest.raises(RuntimeError):
        with call_ledger.dispatching(run, ctx) as again:
            assert again is record                                       # same record, same key
            assert call_ledger.state_of(run, ctx.server_call_id) == (call_ledger.REDISPATCHED, 2)
            raise RuntimeError("handler failed")
    assert call_ledger.state_of(run, ctx.server_call_id) == (call_ledger.DONE, 2)     # refusal is DONE too
    assert call_ledger.size(run) == 1                                    # no new record on re-dispatch


def _identity_refusal(fn, *args):
    error = refused(fn, *args)
    assert (error.status, error.code) == (422, "invalid_request")
    assert error.params == {"errors": [{"field": "call_context", "reason": "invalid_format",
                                        "validation": {"reason": "call_identity_unavailable"}}]}
    return error


def test_identity_loss_never_invents_an_id(caplog):
    run = make_run()
    ctx = call_ledger.accept_call(run, {"id": "call_0", "name": "x", "input": {"n": 1}})
    with caplog.at_level(logging.WARNING):
        _identity_refusal(call_ledger.resolve_call, run, None)                         # no context
    assert "no_context" in caplog.text
    _identity_refusal(call_ledger.resolve_call, run, {"run_id": RUN_ID, "server_call_id": ctx.server_call_id,
                                                     "tool_call_id": "call_0"})       # not a server context
    _identity_refusal(call_ledger.resolve_call, make_run(run_id="aiv_other"), ctx)     # run mismatch
    _identity_refusal(call_ledger.resolve_call, make_run(), ctx)                       # run context lost
    lost = call_ledger.CallContext(RUN_ID, str(uuid.uuid4()), "call_0")
    _identity_refusal(call_ledger.resolve_call, run, lost)                             # record missing
    _identity_refusal(call_ledger.resolve_call, run,
                      call_ledger.CallContext(RUN_ID, ctx.server_call_id, "call_9"))   # provider id mismatch
    record = call_ledger.resolve_call(run, ctx)
    object.__setattr__(record, "input_sha256", "0" * 64)                              # CI-06 injection
    _identity_refusal(call_ledger.resolve_call, run, ctx)
    assert call_ledger.size(run) == 1


def test_decision_order_identity_before_provider_id():
    run = make_run()
    ctx = call_ledger.accept_call(run, {"id": "  ", "name": "x", "input": {}})
    assert ctx.tool_call_id is None
    record = call_ledger.resolve_call(run, ctx)
    error = refused(call_ledger.require_tool_call_id, record)                          # CI-07
    assert error.params == {"errors": [{"field": "tool_call_id", "reason": "required"}]}
    _identity_refusal(call_ledger.resolve_call, run, None)                             # §4.3 first branch wins
    for bad in (None, 7, "", "\t"):
        assert call_ledger.clean_id(bad) is None
    assert call_ledger.clean_id("call_0") == "call_0"


def test_the_ledger_cap_refuses_new_calls_without_evicting():
    run = make_run()
    first = call_ledger.accept_call(run, {"id": "call_first", "name": "x", "input": {"i": 0}})
    for i in range(1, call_ledger.CALL_LEDGER_MAX):
        assert call_ledger.accept_call(run, {"id": f"c{i}", "name": "x", "input": {"i": i}}) is not None
    assert call_ledger.size(run) == call_ledger.CALL_LEDGER_MAX == 500
    assert call_ledger.accept_call(run, {"id": "c_over", "name": "x", "input": {}}) is None      # CI-05
    assert call_ledger.size(run) == 500
    assert call_ledger.resolve_call(run, first).input_copy() == {"i": 0}


def test_non_chat_or_id_less_runs_are_not_accepted():
    assert call_ledger.accept_call({"action_scope": "work", "run_id": "r"}, {"id": "c"}) is None
    assert call_ledger.accept_call({"action_scope": "chat"}, {"id": "c"}) is None
    assert call_ledger.accept_call(None, {"id": "c"}) is None
    ctx = call_ledger.accept_call(make_run(), "not a dict")
    assert ctx is not None and ctx.tool_call_id is None


def test_concurrent_acceptance_keeps_every_record():
    run = make_run()
    out = []
    threads = [threading.Thread(target=lambda i=i: out.append(
        call_ledger.accept_call(run, {"id": f"c{i}", "name": "x", "input": {"i": i}}))) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert call_ledger.size(run) == 40 and len({ctx.server_call_id for ctx in out}) == 40


# ── §2.7 / §3.1 the edit gate ────────────────────────────────────────────────

def test_gate_parameters():
    from flowgate.conversation import commands

    assert chat_gate.CANCEL_DRAIN_SEC == 10
    assert chat_gate.closed_runs_max() == commands._CLOSED_RUNS_MAX


def test_open_gate_counts_inflight_and_release_is_idempotent():
    assert chat_gate.state(RUN_ID) == ("OPEN", 0) and chat_gate.closed_reason(RUN_ID) is None
    one, two = chat_gate.enter(RUN_ID), chat_gate.enter(RUN_ID)
    assert chat_gate.state(RUN_ID) == ("OPEN", 2)
    chat_gate.release(one)
    chat_gate.release(one)
    chat_gate.release(None)
    assert chat_gate.state(RUN_ID) == ("OPEN", 1)
    chat_gate.release(two)
    assert chat_gate.state(RUN_ID) == ("OPEN", 0)


@pytest.mark.parametrize("reason, code", [(chat_gate.CANCELLED, "chat_document_run_cancelled"),
                                          (chat_gate.FINISHED, "chat_document_run_finished")])
def test_closed_gate_refuses_enter_and_keeps_the_first_reason(reason, code):
    assert chat_gate.close(RUN_ID, reason, 0) is True                    # never entered: made and closed
    error = refused(chat_gate.enter, RUN_ID)
    assert (error.status, error.code, error.params) == (409, code, {"run_id": RUN_ID})
    assert chat_gate.state(RUN_ID) == (f"CLOSED({reason})", 0)
    other = chat_gate.FINISHED if reason == chat_gate.CANCELLED else chat_gate.CANCELLED
    chat_gate.close(RUN_ID, other, 0)
    assert chat_gate.closed_reason(RUN_ID) == reason


def test_close_waits_for_inflight_edits_and_refuses_new_ones_meanwhile():
    ticket = chat_gate.enter(RUN_ID)
    finished = threading.Event()
    result = {}

    def cancel():
        result["drained"] = chat_gate.close(RUN_ID, chat_gate.CANCELLED, 5)
        finished.set()

    thread = threading.Thread(target=cancel)
    thread.start()
    deadline = time.monotonic() + 5
    while chat_gate.closed_reason(RUN_ID) is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert chat_gate.closed_reason(RUN_ID) == chat_gate.CANCELLED and not finished.is_set()
    assert refused(chat_gate.enter, RUN_ID).code == "chat_document_run_cancelled"     # CG-02a
    assert chat_gate.state(RUN_ID) == ("CLOSED(cancelled)", 1)                        # inflight unchanged
    chat_gate.release(ticket)                                                         # commit/rollback done
    thread.join(5)
    assert finished.is_set() and result["drained"] is True
    assert chat_gate.state(RUN_ID) == ("CLOSED(cancelled)", 0)


def test_drain_timeout_warns_and_never_blocks_the_cancel(caplog):
    ticket = chat_gate.enter(RUN_ID)
    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        assert chat_gate.close(RUN_ID, chat_gate.CANCELLED, 0.05) is False           # CG-03
    assert time.monotonic() - started < 2
    assert "chat_doc_edit_drain_timeout" in caplog.text
    chat_gate.release(ticket)                                                         # CLOSED release
    assert chat_gate.state(RUN_ID) == ("CLOSED(cancelled)", 0)
    assert chat_gate.close(RUN_ID, chat_gate.CANCELLED, 0.05) is True                 # CG-06


def test_unknown_reason_and_empty_run_id():
    with pytest.raises(ValueError):
        chat_gate.close(RUN_ID, "stopped", 0)
    assert chat_gate.close(None, chat_gate.CANCELLED, 0) is True
    assert chat_gate.closed_reason(None) is None
    assert refused(chat_gate.enter, None).code == "chat_document_forbidden"


def test_closed_records_are_bounded_by_the_referenced_limit(monkeypatch):
    from flowgate.conversation import commands

    monkeypatch.setattr(commands, "_CLOSED_RUNS_MAX", 3)
    for i in range(5):
        chat_gate.close(f"aiv_{i}", chat_gate.FINISHED, 0)
    assert [chat_gate.closed_reason(f"aiv_{i}") for i in range(5)] == [None, None, "finished", "finished",
                                                                         "finished"]
    monkeypatch.setattr(chat_gate, "closed_runs_max", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    chat_gate.close("aiv_kept", chat_gate.CANCELLED, 0)
    assert chat_gate.closed_reason("aiv_kept") == chat_gate.CANCELLED


# ── gate lifetime: drain timeout → eviction → last release (TR0023 rework) ───

def _limit(monkeypatch, value):
    from flowgate.conversation import commands

    monkeypatch.setattr(commands, "_CLOSED_RUNS_MAX", value)


def _pending_old(monkeypatch):
    """aiv_old: one edit in flight, closed with a drain timeout, then pushed out of retention."""
    _limit(monkeypatch, 1)
    ticket = chat_gate.enter("aiv_old")
    assert chat_gate.close("aiv_old", chat_gate.CANCELLED, 0) is False            # drain timeout
    assert chat_gate.close("aiv_new", chat_gate.FINISHED, 0) is True              # evicts aiv_old
    return ticket


def test_an_evicted_gate_stays_closed_until_its_last_release_then_goes(monkeypatch):
    _limit(monkeypatch, 1)
    one, two = chat_gate.enter("aiv_old"), chat_gate.enter("aiv_old")
    old = one.gate
    assert chat_gate.close("aiv_old", chat_gate.CANCELLED, 0) is False
    assert chat_gate.close("aiv_new", chat_gate.FINISHED, 0) is True
    assert "aiv_old" not in chat_gate._closed and old.evicted and chat_gate._gates["aiv_old"] is old
    # pending removal: still CLOSED with the first reason, new entries refused, inflight unchanged
    assert chat_gate.state("aiv_old") == ("CLOSED(cancelled)", 2)
    assert chat_gate.closed_reason("aiv_old") == chat_gate.CANCELLED
    assert refused(chat_gate.enter, "aiv_old").code == "chat_document_run_cancelled"
    assert chat_gate.state("aiv_old") == ("CLOSED(cancelled)", 2)
    chat_gate.release(one)
    assert chat_gate._gates.get("aiv_old") is old and chat_gate.state("aiv_old") == ("CLOSED(cancelled)", 1)
    chat_gate.release(two)                                                         # the last release removes it
    assert "aiv_old" not in chat_gate._gates and old.detached and old.inflight == 0
    assert chat_gate.registry_sizes() == (1, 1)
    chat_gate.release(two)                                                         # double release: no effect
    assert old.inflight == 0
    for i in range(10):                                                            # the review's reproduction
        chat_gate.close(f"aiv_more_{i}", chat_gate.FINISHED, 0)
    assert chat_gate.registry_sizes() == (1, 1)
    assert set(chat_gate._gates) == set(chat_gate._closed) == {"aiv_more_9"}


def test_closing_a_pending_gate_again_retains_it_with_the_first_reason(monkeypatch):
    ticket = _pending_old(monkeypatch)
    old = ticket.gate
    assert chat_gate.close("aiv_old", chat_gate.FINISHED, 0) is False             # still in flight
    assert not old.evicted and list(chat_gate._closed) == ["aiv_old"] and "aiv_new" not in chat_gate._gates
    assert chat_gate.closed_reason("aiv_old") == chat_gate.CANCELLED
    chat_gate.release(ticket)                                                      # retained: kept closed
    assert chat_gate._gates.get("aiv_old") is old and chat_gate.state("aiv_old") == ("CLOSED(cancelled)", 0)
    assert chat_gate.registry_sizes() == (1, 1)


def test_a_close_draining_a_pending_gate_keeps_it_through_the_last_release(monkeypatch):
    ticket = _pending_old(monkeypatch)
    old = ticket.gate
    result = {}
    thread = threading.Thread(target=lambda: result.update(drained=chat_gate.close("aiv_old", chat_gate.FINISHED, 5)))
    thread.start()
    deadline = time.monotonic() + 5
    while old.waiters == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert old.waiters == 1
    chat_gate.release(ticket)                                                      # last release during the drain
    assert chat_gate._gates.get("aiv_old") is old and not old.detached             # no open window
    assert refused(chat_gate.enter, "aiv_old").code == "chat_document_run_cancelled"
    thread.join(5)
    assert result["drained"] is True and old.waiters == 0
    assert chat_gate._gates.get("aiv_old") is old and not old.evicted and list(chat_gate._closed) == ["aiv_old"]
    assert chat_gate.state("aiv_old") == ("CLOSED(cancelled)", 0) and chat_gate.registry_sizes() == (1, 1)


def test_a_ticket_only_ever_releases_its_own_gate(monkeypatch):
    ticket = _pending_old(monkeypatch)
    chat_gate.release(ticket)
    assert "aiv_old" not in chat_gate._gates and chat_gate.state("aiv_old") == ("OPEN", 0)
    # retention forgot aiv_old (like any evicted record): a later entry finds no gate and gets a new one
    fresh = chat_gate.enter("aiv_old")
    assert fresh.gate is not ticket.gate and chat_gate.state("aiv_old") == ("OPEN", 1)
    chat_gate.release(ticket)                                                      # stale ticket
    chat_gate.release(chat_gate.Ticket("aiv_old"))                                 # not issued by enter
    assert chat_gate.state("aiv_old") == ("OPEN", 1) and ticket.gate.inflight == 0
    chat_gate.release(fresh)
    assert chat_gate.state("aiv_old") == ("OPEN", 0) and "aiv_old" not in chat_gate._gates   # no open leftover
    assert chat_gate.registry_sizes() == (1, 1)


def test_open_gates_leave_the_registry_when_nothing_is_in_flight():
    tickets = [chat_gate.enter(f"aiv_open_{i}") for i in range(20)]
    assert chat_gate.registry_sizes() == (20, 0)
    for ticket in tickets:
        chat_gate.release(ticket)
    assert chat_gate.registry_sizes() == (0, 0)


def test_recheck_still_refuses_a_run_whose_gate_record_was_forgotten(reads, monkeypatch):
    _limit(monkeypatch, 1)
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    holder = chat_gate.TicketHolder()
    chat_authz.chat_recheck(run, token, holder)                                    # an edit in flight
    run["status"] = "cancelling"
    assert chat_gate.close(RUN_ID, chat_gate.CANCELLED, 0) is False
    chat_gate.close("aiv_other", chat_gate.FINISHED, 0)                            # evicts RUN_ID
    chat_gate.release(holder.ticket)                                               # last release: removed
    assert RUN_ID not in chat_gate._gates
    run.update(status="cancelled", finished_at="2026-11-04T00:00:00Z")
    late = chat_gate.TicketHolder()
    error = refused(chat_authz.chat_recheck, run, token, late)
    chat_gate.release(late.ticket)
    assert (error.status, error.code) == (409, "chat_document_run_finished")
    assert RUN_ID not in chat_gate._gates and chat_gate.registry_sizes() == (1, 1)


def test_cleanup_races_never_reopen_the_closed_gate_or_cross_tickets(monkeypatch):
    """Last release, a second close and a new entry race on a pending (evicted) gate."""
    for _ in range(200):
        chat_gate.reset()
        ticket = _pending_old(monkeypatch)
        old = ticket.gate
        barrier = threading.Barrier(3)
        entered, closed = [], []

        def releaser():
            barrier.wait()
            chat_gate.release(ticket)

        def closer():
            barrier.wait()
            chat_gate.close("aiv_old", chat_gate.FINISHED, 2)
            closed.append(chat_gate.state("aiv_old")[0])

        def enterer():
            barrier.wait()
            try:
                got = chat_gate.enter("aiv_old")
            except GraphApiError as error:
                entered.append(error.code)
            else:
                entered.append(got)
                chat_gate.release(got)

        threads = [threading.Thread(target=f) for f in (releaser, closer, enterer)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert not any(t.is_alive() for t in threads)
        result = entered[0]
        if isinstance(result, chat_gate.Ticket):
            # only possible after the closed gate left the registry; never the closed gate itself
            assert result.gate is not old and old.detached and result.gate.inflight == 0
        else:
            assert result in ("chat_document_run_cancelled", "chat_document_run_finished")
        # a close never leaves the run open, and the old gate is never reopened or over-released
        assert closed[0] in ("CLOSED(cancelled)", "CLOSED(finished)")
        assert old.closed and old.reason == chat_gate.CANCELLED and old.inflight == 0
        if chat_gate._gates.get("aiv_old") is old:
            assert closed[0] == "CLOSED(cancelled)"
        gate = chat_gate._gates["aiv_old"]
        assert gate.closed and not gate.evicted and gate.inflight == 0
        assert list(chat_gate._closed) == ["aiv_old"] and chat_gate.registry_sizes() == (1, 1)


def test_concurrent_edits_closes_and_evictions_leave_only_retained_closed_gates(monkeypatch):
    _limit(monkeypatch, 3)
    runs = [f"aiv_stress_{i}" for i in range(8)]
    seen, lock = set(), threading.Lock()

    def editor(run_id):
        for _ in range(60):
            try:
                ticket = chat_gate.enter(run_id)
            except GraphApiError:
                time.sleep(0.0005)
                continue
            with lock:
                seen.add(ticket.gate)
            time.sleep(0.0002)
            chat_gate.release(ticket)
            chat_gate.release(ticket)                                              # double release, harmless

    def closer(run_id, reason):
        time.sleep(0.002)
        chat_gate.close(run_id, reason, 0)                                         # drain timeouts likely

    threads = []
    for i, run_id in enumerate(runs):
        threads += [threading.Thread(target=editor, args=(run_id,)) for _ in range(3)]
        threads.append(threading.Thread(target=closer, args=(run_id, chat_gate.REASONS[i % 2])))
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads)
    assert all(gate.inflight == 0 for gate in seen)                                # no ticket crossed gates
    gates, records = chat_gate.registry_sizes()
    assert gates == records <= 3 and set(chat_gate._gates) == set(chat_gate._closed)
    for run_id, gate in chat_gate._gates.items():
        assert gate.closed and not gate.evicted and gate.inflight == 0
        assert chat_gate._closed[run_id] == gate.reason


def test_hook_helpers_never_raise(monkeypatch):
    monkeypatch.setattr(chat_gate, "close", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    chat_gate.close_on_cancel(make_run())
    chat_gate.close_on_finalize(make_run())
    chat_gate.close_on_cancel(None)


def test_no_recheck_passes_after_the_gate_closed(reads):
    """I-CG in miniature: an entry racing a close either got in before (and is drained) or is refused."""
    run, token = chat_authz.authorize_chat_run(chat_authz.API, None, make_run())
    passed_after_close, closed_at = [], []
    lock = threading.Lock()

    def edit():
        holder = chat_gate.TicketHolder()
        try:
            chat_authz.chat_recheck(run, token, holder)
            with lock:
                if closed_at:
                    passed_after_close.append(True)
            time.sleep(0.002)
        except GraphApiError:
            pass
        finally:
            chat_gate.release(holder.ticket)

    def cancel():
        chat_gate.close(RUN_ID, chat_gate.CANCELLED, 5)
        with lock:
            closed_at.append(time.monotonic())

    threads = [threading.Thread(target=edit) for _ in range(30)] + [threading.Thread(target=cancel)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert passed_after_close == [] and chat_gate.state(RUN_ID) == ("CLOSED(cancelled)", 0)


# ── real SQLite rows (token / claim reads) ───────────────────────────────────

def test_real_store_reads(monkeypatch, migrated_sqlite_db):
    from modules.flow_gate.db import _SqliteDbAdapter, connection, projects, tokens, user_chat_source_access

    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER_ID", os.environ.get("FLOWGATE_AGENT_PEPPER_ID", "primary"))
    db = connection.FlowGateStore.__new__(connection.FlowGateStore)
    db._db, db._sq = _SqliteDbAdapter(migrated_sqlite_db("chat_0127.db")), None
    monkeypatch.setattr(connection, "STORE", db)
    db._execute("INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
                "VALUES (?, ?, ?, 'x', 't', 't')", [USER, USER, USER + "@test.invalid"])
    projects.create({"project_id": "fgp", "project_name": "fgp"})
    run = make_run(group_id=None)
    error = refused(chat_authz.authorize_chat_run, chat_authz.API, None, run)          # CB-04d: no tokens row
    assert (error.status, error.code) == (401, "unauthorized")
    tokens.create({"token_id": TOKEN_ID, "hash": "hash-chat-0127", "pepper_id": "test", "project": "fgp",
                   "group_id": None, "doc_ref": DOC_REF, "action_scope": "chat", "issued_to": USER,
                   "created_at": connection.now_iso(), "expires_at": "2099-01-01T00:00:00+00:00",
                   "ai_run_id": RUN_ID, "source_access": "read_write"})
    row = tokens.get_by_id(TOKEN_ID)
    assert chat_authz.token_live(row) == chat_authz.LIVE
    assert chat_authz.effective_write_kind(row) == "read_write"
    error = refused(chat_authz.authorize_chat_run, chat_authz.API, None, run)          # live row; group axis empty
    assert (error.status, error.code) == (403, "chat_document_forbidden")
    assert chat_authz.claim_pending(row) is False                                       # no claim row
    user_chat_source_access.upsert(USER, "edit_once", "2026-11-04T00:00:00Z")
    assert chat_authz.claim_pending(row) is False
    db._execute("UPDATE user_chat_source_access SET one_shot_token_id = ?, one_shot_claimed_at = ? "
                "WHERE user_id = ?", [TOKEN_ID, "2026-11-04T00:00:01Z", USER])
    before = dict(user_chat_source_access.get(USER))
    assert chat_authz.claim_pending(row) is True
    assert user_chat_source_access.get(USER) == before                                 # E3: read only
    tokens.revoke(TOKEN_ID)
    error = refused(chat_authz.authorize_chat_run, chat_authz.API, None, run)
    assert (error.status, error.code) == (401, "unauthorized")
