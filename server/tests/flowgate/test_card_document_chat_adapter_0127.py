"""T#7: real revise/read integration through API call contexts and CLI HTTP.

SQLite, files, CAS, revision/event rows, rollback, guards and contacts run for real.
The T#1 fixture supplies card definitions, RBAC and notification sinks; token verification
is replaced with a live-row lookup while authz/recheck still read actual token/claim rows.
"""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_card_document_edit_0127 import (env, F, D, HEADER, business_state, ensure_guard,
                                          payload, service, reads as document_read)
from test_card_document_chat_0127 import make_run, make_token, TOKEN_ID, RUN_ID
from test_card_document_edit_composition_0127 import composition  # noqa: F401
from flowgate.card_document_edit import chat_adapter as adapter
from flowgate.card_document_edit import chat_authz, chat_gate, call_ledger, chat_tools, guards, target, contacts
from flowgate.card_document_edit.errors import GraphApiError
from modules.flow_gate.db import tokens, user_chat_source_access, documents, document_revisions
from modules.flow_gate.services.ai_invoke import worker

COORDS = {"flow_id": F, "node_key": "research", "slot": "instruction"}
CLI_URL = f"/flowgate/api/v2/chat/flows/{F}/nodes/research/slots/instruction/document"


@pytest.fixture
def chat(env, monkeypatch):
    chat_gate.reset()
    token = make_token(project="flowgate", group_id=F, issued_to="user")
    tokens.create({**token, "hash": "chat-adapter-hash", "pepper_id": "test", "created_at": "t"})
    user_chat_source_access.upsert("user", "flowgate", F, "read_only", "t")
    run = make_run(project_id="flowgate", group_id=F, issued_to="user")
    def verify(raw):
        row = tokens.get_by_id(TOKEN_ID)
        if raw != "chat" or chat_authz.token_live(row) != chat_authz.LIVE:
            raise GraphApiError(401, "unauthorized", {})
        return row
    monkeypatch.setattr(chat_authz, "_verify_token", verify)
    monkeypatch.setattr(chat_authz, "_get_run_record", lambda rid: run if rid == run["run_id"] else None)
    c = SimpleNamespace(env=env, run=run, token=token)
    yield c
    chat_gate.reset()


def request_body(chat, **over):
    return {**payload(chat.env.req), **over}


def api(chat, editing=False, inp=None, ctx=None, new_context=True):
    tool = chat_tools.REVISE_TOOL if editing else chat_tools.READ_TOOL
    if new_context:
        inp = inp if inp is not None else {**COORDS, **(request_body(chat) if editing else {})}
        inp = dict(inp)
        if editing:
            inp.pop("idempotency_key", None)
        ctx = call_ledger.accept_call(chat.run, {"id": "provider-id", "name": tool, "input": inp})
    handler = adapter.revise_card_document_call_handler if editing else adapter.read_card_document_call_handler
    return handler(chat.run, ctx), ctx


def cli(chat, editing=False, body=None, raw="chat", content=None, url=None):
    h = {"Authorization": "Bearer " + raw} if raw else {}
    url = url or CLI_URL + ("/revisions" if editing else "")
    if editing:
        if content is not None:
            return chat.env.client.post(url, headers=h, content=content)
        return chat.env.client.post(url, headers=h, json=body if body is not None else request_body(chat))
    return chat.env.client.get(url, headers=h)


def token_state(chat):
    return (chat.env.db._fetch_all("SELECT * FROM tokens"),
            chat.env.db._fetch_all("SELECT * FROM user_chat_source_access"))


def error(result, status, code):
    assert result[0] == status, result
    assert result[1]["ok"] is False and result[1]["error"]["code"] == code, result
    return result[1]["error"]["params"]


@pytest.mark.parametrize("via", ["api", "cli"])
def test_read_save_replay_user_history_contacts_and_claim_unchanged(chat, monkeypatch, via):
    before = token_state(chat)
    original = chat.env.path.read_bytes()
    principals = []
    monkeypatch.setattr(guards.mutation_policy, "assert_group_mutation_allowed", lambda *a: principals.append(a))
    # The designated adapter releases its ticket before every type of contact executes.
    normal, replay = contacts.run, contacts.run_replay
    def contact(fn, plan):
        assert chat_gate.state(RUN_ID)[1] == 0
        assert plan.token_id is None
        return fn(plan)
    monkeypatch.setattr(contacts, "run", lambda p: contact(normal, p))
    monkeypatch.setattr(contacts, "run_replay", lambda p: contact(replay, p))
    if via == "api":
        got, _ = api(chat)
        assert got[0] == 200 and got[1]["content"] == original.decode()
        # A model cannot supply the stored actor, token, origin or effective key.
        inp = {**COORDS, **request_body(chat)}
        result, ctx = api(chat, True, inp)
        saved_state = business_state(chat.env)
        again, _ = api(chat, True, ctx=ctx, new_context=False)
        assert call_ledger.state_of(chat.run, ctx.server_call_id) == (call_ledger.DONE, 2)
    else:
        got = cli(chat)
        assert got.status_code == 200 and got.json()["content"] == original.decode()
        response = cli(chat, True)
        result = (response.status_code, response.json())
        saved_state = business_state(chat.env)
        response = cli(chat, True)
        again = (response.status_code, response.json())
    assert result[0] == again[0] == 200, (result, again)
    assert result[1]["revision_no"] == again[1]["revision_no"] == 1
    assert result[1]["replayed"] is False and again[1]["replayed"] is True
    assert business_state(chat.env) == saved_state
    history = document_revisions.list_by_doc(D)
    assert len(history) == 1 and history[0]["created_by"] == "user" and history[0]["edit_reason"] == "user_comment"
    row = chat.env.db._fetch_one("SELECT * FROM workflow_events WHERE event_type='doc_edited'")
    meta = json.loads(row["metadata"])
    assert row["actor_user_id"] == "user" and meta["actor_kind"] == "person_delegated" and meta["origin"] == "chat"
    assert meta["run_id"] == RUN_ID and meta["token_id"] == TOKEN_ID
    assert meta["server_call_id"] == (ctx.server_call_id if via == "api" else None)
    assert meta["idem_key"].startswith("chat-" + via + ":")
    assert [x[0] for x in chat.env.events] == ["document", "canvas", "graph"] * 2
    assert all(x[1][-1] is None for x in chat.env.events if x[0] == "graph")
    assert token_state(chat) == before
    assert principals and all(args[1].kind == "human" and args[1].user_id == "user" for args in principals)


@pytest.mark.parametrize("via", ["api", "cli"])
@pytest.mark.parametrize("field,value,code", [
    ("expected_revision", None, "expected_revision_missing"),
    ("expected_revision", True, "expected_revision_missing"),
    ("expected_revision", "0", "expected_revision_missing"),
    ("expected_revision", 0.0, "expected_revision_missing"),
    ("expected_revision", -1, "expected_revision_missing"),
    ("content", 42, "invalid_content"),
    ("expected_doc_id", None, "card_binding_missing"),
    ("actor", {"user_id": "other"}, "invalid_request"),
    ("origin", "agent", "invalid_request"),
    ("user_id", "other", "invalid_request"),
])
def test_input_precedes_mode_project_permissions_and_never_writes(chat, monkeypatch, via, field, value, code):
    chat.env.db._execute("UPDATE tokens SET source_access='read' WHERE token_id=?", [TOKEN_ID])
    monkeypatch.setattr(adapter.legacy, "load_flow", lambda *_: pytest.fail("input must precede Flow access"))
    state, ts = business_state(chat.env), token_state(chat)
    body = request_body(chat, **{field: value})
    if via == "api":
        result, _ = api(chat, True, {**COORDS, **body})
    else:
        r = cli(chat, True, body)
        result = r.status_code, r.json()
    error(result, 422, code)
    assert business_state(chat.env) == state and token_state(chat) == ts and chat.env.events == []


@pytest.mark.parametrize("client_key", [None, "", "has space", "x" * 129, "_bad", "한글", True])
def test_cli_requires_valid_client_key_before_mode(chat, client_key):
    chat.env.db._execute("UPDATE tokens SET source_access='read' WHERE token_id=?", [TOKEN_ID])
    r = cli(chat, True, request_body(chat, idempotency_key=client_key))
    p = error((r.status_code, r.json()), 422, "invalid_request")
    assert p["errors"][0]["field"] == "idempotency_key"


@pytest.mark.parametrize("via", ["api", "cli"])
@pytest.mark.parametrize("dead", ["revoked", "consumed", "expired", "cancelled", "finished", "current_missing"])
def test_auth_life_precedes_lost_context_or_malformed_json(chat, via, dead):
    status, code = 401, "unauthorized"
    if dead == "revoked":
        chat.env.db._execute("UPDATE tokens SET revoked_at='x' WHERE token_id=?", [TOKEN_ID])
    elif dead == "consumed":
        chat.env.db._execute("UPDATE tokens SET consumed_at='x' WHERE token_id=?", [TOKEN_ID])
    elif dead == "expired":
        chat.env.db._execute("UPDATE tokens SET expires_at='2000-01-01' WHERE token_id=?", [TOKEN_ID])
    elif dead == "current_missing":
        chat.run.update(current_token_id=None, token_id=None)
        status, code = 403, "chat_document_forbidden"
    else:
        # Run still says running: the closed edit gate alone must determine the code.
        chat_gate.close(RUN_ID, dead, 0)
        status, code = 409, "chat_document_run_" + dead
    state, ts = business_state(chat.env), token_state(chat)
    if via == "api":
        result, _ = api(chat, True, new_context=False)
    else:
        r = cli(chat, True, content="{")
        result = r.status_code, r.json()
    error(result, status, code)
    assert business_state(chat.env) == state and token_state(chat) == ts


@pytest.mark.parametrize("raw", [None, "user", "JWT", "revoked-token"])
def test_cli_rejects_non_chat_bearer_before_json(chat, raw):
    r = cli(chat, True, raw=raw, content="{")
    error((r.status_code, r.json()), 401, "unauthorized")


@pytest.mark.parametrize("kind", ["read", None, "broken"])
def test_read_skips_write_mode_but_write_refuses_before_target(chat, monkeypatch, kind):
    if kind == "broken":
        # A corrupt legacy row cannot be INSERTed through today's CHECK constraint.
        # Emulate only the row read; leave real schema and persisted rows untouched.
        original = chat_authz._read_token
        monkeypatch.setattr(chat_authz, "_read_token", lambda tid: {**original(tid), "source_access": kind})
    else:
        chat.env.db._execute("UPDATE tokens SET source_access=? WHERE token_id=?", [kind, TOKEN_ID])
    assert api(chat)[0][0] == 200 and cli(chat).status_code == 200
    monkeypatch.setattr(adapter.legacy, "load_flow", lambda *_: pytest.fail("mode must precede target"))
    result, _ = api(chat, True)
    p = error(result, 403, "chat_document_edit_mode_denied")
    assert p == {"run_id": RUN_ID, "effective_kind": "read", "required_kind": "read_write"}


@pytest.mark.parametrize("editing", [False, True])
@pytest.mark.parametrize("project,status,code", [(None, 404, "flow_not_found"), ("foreign", 403, "chat_document_forbidden")])
def test_flow_and_project_checks_for_both_paths(chat, monkeypatch, editing, project, status, code):
    def load(flow):
        if project is None:
            raise GraphApiError(404, "flow_not_found", {"flow_id": flow})
        return {"project_id": project}
    monkeypatch.setattr(adapter.legacy, "load_flow", load)
    result, _ = api(chat, editing)
    p = error(result, status, code)
    assert not {"doc_id", "revision_no", "content"} & set(p)
    r = cli(chat, editing)
    error((r.status_code, r.json()), status, code)


def test_mode_then_permission_then_document_state(chat, monkeypatch):
    state = business_state(chat.env)
    monkeypatch.setattr(guards, "has_permission", lambda *a: False)
    result, _ = api(chat, True)
    p = error(result, 403, "permission_denied")
    assert p == {"flow_id": F, "required": "perm_document_update"}
    result, _ = api(chat)
    error(result, 403, "permission_denied")
    assert business_state(chat.env) == state


def fit_document(chat, size):
    chat.env.path.write_bytes((HEADER + "x").encode())
    body = adapter.success_body(document_read.read_card(**COORDS, actor=chat.env.req.actor))
    padding = size - adapter.serialized_length(body)
    assert padding >= 0
    chat.env.path.write_bytes((HEADER + "x" * (padding + 1)).encode())
    body = adapter.success_body(document_read.read_card(**COORDS, actor=chat.env.req.actor))
    assert adapter.serialized_length(body) == size


@pytest.mark.parametrize("size", [15999, 16000, 16001])
def test_api_size_measures_full_envelope_at_inclusive_boundary_cli_unlimited(chat, monkeypatch, size):
    monkeypatch.setattr(worker, "API_TOOL_RESULT_MAX_CHARS", 16000)
    fit_document(chat, size)
    ensure_guard(chat.env)
    state, ts = business_state(chat.env), token_state(chat)
    result, _ = api(chat)
    if size <= 16000:
        assert result[0] == 200 and adapter.serialized_length(result[1]) == size
        saved, _ = api(chat, True)
        assert saved[0] == 200
    else:
        p = error(result, 413, "result_too_large")
        expected = {**COORDS, "doc_id": D, "revision_no": 0, "size_chars": size,
                    "limit_chars": 16000, "limit_scope": "api_chat_transport",
                    "alternative_paths": ["cli_chat:/api/v2/chat", "designer", "agent"]}
        assert p == expected
        saved, _ = api(chat, True)
        assert error(saved, 413, "result_too_large") == expected
        assert business_state(chat.env) == state and token_state(chat) == ts and chat.env.events == []
        assert cli(chat).status_code == 200
        assert cli(chat, True).status_code == 200


def test_size_is_serialized_codepoints_with_json_escaping_and_runtime_limit(chat, monkeypatch):
    chat.env.path.write_bytes((HEADER + ('한글\n"' * 120)).encode())
    r, _ = api(chat)
    assert r[0] == 200
    size = len(json.dumps(r[1], ensure_ascii=False))
    assert size > len(r[1]["content"]) and size < len(json.dumps(r[1]))
    monkeypatch.setattr(worker, "API_TOOL_RESULT_MAX_CHARS", size)
    assert api(chat)[0][0] == 200
    monkeypatch.setattr(worker, "API_TOOL_RESULT_MAX_CHARS", size - 1)
    result, _ = api(chat)
    assert error(result, 413, "result_too_large")["size_chars"] == size


def test_guard_runs_on_fresh_current_before_replay_revision_and_content(chat, monkeypatch):
    saved, ctx = api(chat, True)
    assert saved[0] == 200
    fit_document(chat, 16001)
    state = business_state(chat.env)
    for inp in (None, {**COORDS, **request_body(chat, expected_revision=999, content="bad")}):
        result, _ = api(chat, True, inp, ctx=ctx, new_context=inp is not None)
        error(result, 413, "result_too_large")
        assert business_state(chat.env) == state
    assert len(chat.env.events) == 3


@pytest.mark.parametrize("case,code", [("slot", "slot_document_changed"), ("unreadable", "not_editable"), ("terminal", "not_editable")])
def test_slot_editability_and_body_errors_precede_size(chat, monkeypatch, case, code):
    fit_document(chat, 16001)
    body = request_body(chat)
    if case == "slot":
        body["expected_doc_id"] = "old-document"
    elif case == "unreadable":
        chat.env.path.unlink()
    else:
        monkeypatch.setattr(guards.document_service, "is_final_approved", lambda d: True)
    result, _ = api(chat, True, {**COORDS, **body})
    error(result, 409 if case == "slot" else 422, code)


@pytest.mark.parametrize("change,code", [("claim", "chat_document_forbidden"), ("cancel", "chat_document_run_cancelled"),
                                        ("token", "unauthorized"), ("mode", "chat_document_edit_mode_denied")])
def test_boundary_recheck_rejects_changes_and_releases_ticket(chat, monkeypatch, change, code):
    ensure_guard(chat.env)
    before = business_state(chat.env)
    original = adapter.revise
    token_after = []
    def revise(req):
        if change == "claim":
            chat.env.db._execute("UPDATE user_chat_source_access SET one_shot_token_id=?, one_shot_claimed_at='t' WHERE user_id='user'", [TOKEN_ID])
        elif change == "cancel":
            chat_gate.close(RUN_ID, "cancelled", 0)
        elif change == "token":
            chat.env.db._execute("UPDATE tokens SET revoked_at='x' WHERE token_id=?", [TOKEN_ID])
        else:
            chat.env.db._execute("UPDATE tokens SET source_access='read' WHERE token_id=?", [TOKEN_ID])
        token_after.append(token_state(chat))
        return original(req)
    monkeypatch.setattr(adapter, "revise", revise)
    result, _ = api(chat, True)
    error(result, 401 if change == "token" else 409 if change == "cancel" else 403, code)
    assert chat_gate.state(RUN_ID)[1] == 0
    assert business_state(chat.env) == before and token_state(chat) == token_after[0]
    assert chat.env.events == []


def test_cli_effective_key_binds_run_and_changed_input_is_not_replayed(chat):
    key = "attempt:1"
    expected = "chat-cli:" + hashlib.sha256(json.dumps([RUN_ID, key], separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert adapter.cli_key(RUN_ID, key) == expected
    assert adapter.cli_key("next-run", key) != expected
    assert cli(chat, True, request_body(chat, idempotency_key=key)).status_code == 200
    changed = cli(chat, True, request_body(chat, idempotency_key=key, content=HEADER + "another"))
    error((changed.status_code, changed.json()), 409, "idempotency_key_reused")
    chat.run["run_id"] = "next-run"
    chat.env.db._execute("UPDATE tokens SET ai_run_id='next-run' WHERE token_id=?", [TOKEN_ID])
    r = cli(chat, True, request_body(chat, idempotency_key=key))
    error((r.status_code, r.json()), 409, "revision_conflict")


def test_api_requires_context_tool_id_and_original_tool_identity(chat):
    result, _ = api(chat, True, new_context=False)
    error(result, 422, "invalid_request")
    ctx = call_ledger.accept_call(chat.run, {"name": chat_tools.REVISE_TOOL, "input": {**COORDS, **request_body(chat)}})
    p = error(adapter.revise_card_document_call_handler(chat.run, ctx), 422, "invalid_request")
    assert p["errors"][0]["field"] == "tool_call_id"
    ctx = call_ledger.accept_call(chat.run, {"id": "c", "name": chat_tools.READ_TOOL, "input": {**COORDS, **request_body(chat)}})
    error(adapter.revise_card_document_call_handler(chat.run, ctx), 422, "invalid_request")
    assert chat.env.events == []


def test_projection_failure_still_notifies_after_commit_and_reports_500(chat, monkeypatch):
    monkeypatch.setattr(target, "line_projection", lambda *a: (_ for _ in ()).throw(RuntimeError("projection")))
    result, _ = api(chat, True)
    error(result, 500, "internal_error")
    assert documents.get_by_id(D)["revision_no"] == 1
    assert [x[0] for x in chat.env.events] == ["document", "canvas", "graph"]
    assert chat_gate.state(RUN_ID)[1] == 0


def test_cli_mention_and_descriptors_cover_contract_without_command_capability():
    from flowgate.conversation import integration
    offered = integration.provide_chat_tools({"action_scope": "chat"})
    # flowgate.v02test.0023: the Graph reads (0145), the control tower tools and the attachment request
    # (chat.0017) come first; the card document contract is the last five.
    assert [t["name"] for t in offered] == ["read_flow_canvas", "read_flow_current_run", "place_flow_cards",
                                            "start_flow_run", "request_attachment_upload",
                                            chat_tools.READ_TOOL, chat_tools.REVISE_TOOL, chat_tools.READ_DOC_TOOL,
                                            chat_tools.REVISE_DOC_TOOL, chat_tools.CREATE_DOC_TOOL]
    tools = offered[-5:]
    assert all(t["completion"] is False and callable(t["call_handler"]) for t in tools)
    doc_section = tools[3]["cli_mention"]("http://host/flowgate/api/v1/", "raw")
    for word in ("/api/v2/chat/documents/{doc_id}", "/revisions", "POST http://host/flowgate/api/v2/chat/documents",
                 "expected_revision", "idempotency_key", "never ask the user for flow_id"):
        assert word in doc_section
    section = tools[1]["cli_mention"]("http://host/flowgate/api/v1/", "raw")
    for word in ("/api/v2/chat/flows/", "GET", "POST", "expected_doc_id", "expected_revision", "content", "idempotency_key",
                 "UUID v4", "conflict", "/inbox", "shell", "PATCH", "Read mode", "500", "does not consume", "413", "Designer", "Agent"):
        assert word in section
    assert "api/v1/api/v2" not in section


@pytest.mark.parametrize("via", ["api", "cli"])
@pytest.mark.parametrize("kind", ["read", "read_write"])
@pytest.mark.parametrize("permission", [False, True])
@pytest.mark.parametrize("final", [False, True])
def test_mode_permission_document_state_matrix(chat, monkeypatch, via, kind, permission, final):
    chat.env.db._execute("UPDATE tokens SET source_access=? WHERE token_id=?", [kind, TOKEN_ID])
    monkeypatch.setattr(guards, "has_permission", lambda *a: permission)
    monkeypatch.setattr(guards.document_service, "is_final_approved", lambda d: final)
    # Flow guard registration is outside the transaction; compare business effects
    # against an initialized guard, including its unchanged bump on refusals.
    ensure_guard(chat.env)
    before = business_state(chat.env), token_state(chat)
    if via == "api":
        result, _ = api(chat, True)
    else:
        response = cli(chat, True)
        result = response.status_code, response.json()
    if kind != "read_write":
        error(result, 403, "chat_document_edit_mode_denied")
    elif not permission:
        error(result, 403, "permission_denied")
    elif final:
        error(result, 422, "not_editable")
    else:
        assert result[0] == 200
        assert token_state(chat) == before[1]
        return
    assert (business_state(chat.env), token_state(chat)) == before


def test_one_shot_consumed_run_can_save_repeatedly_without_rearming_or_mode_reread(chat):
    # The fixture's read_only mode and empty marker model a committed edit_once claim.
    before = token_state(chat)
    assert cli(chat, True).status_code == 200
    assert token_state(chat) == before
    # A user setting change affects the next token/run, never this issued token's kind.
    user_chat_source_access.upsert("user", "flowgate", F, "edit_once", "new-setting")
    before = token_state(chat)
    r = cli(chat, True, request_body(chat, expected_revision=1, content=HEADER + "second", idempotency_key="second"))
    assert r.status_code == 200 and r.json()["revision_no"] == 2
    assert token_state(chat) == before


def test_claim_pending_is_refused_without_clearing_or_consuming_it(chat):
    chat.env.db._execute("UPDATE user_chat_source_access SET one_shot_token_id=?, one_shot_claimed_at='t' WHERE user_id='user'", [TOKEN_ID])
    ensure_guard(chat.env)
    before = business_state(chat.env), token_state(chat)
    result, _ = api(chat, True)
    assert error(result, 403, "chat_document_forbidden") == {}
    r = cli(chat, True)
    error((r.status_code, r.json()), 403, "chat_document_forbidden")
    assert (business_state(chat.env), token_state(chat)) == before
    assert chat_gate.state(RUN_ID)[1] == 0


def test_new_provider_call_with_reused_provider_id_is_a_new_save_and_input_is_frozen(chat):
    inp = {**COORDS, "expected_doc_id": D, "expected_revision": 0, "content": HEADER + "frozen"}
    ctx = call_ledger.accept_call(chat.run, {"id": "same-provider-id", "name": chat_tools.REVISE_TOOL, "input": inp})
    inp.update(content=HEADER + "tampered", actor="other")
    result = adapter.revise_card_document_call_handler(chat.run, ctx)
    assert result[0] == 200 and chat.env.path.read_text(encoding="utf-8") == HEADER + "frozen"
    inp = {**COORDS, "expected_doc_id": D, "expected_revision": 1, "content": HEADER + "second"}
    second = call_ledger.accept_call(chat.run, {"id": "same-provider-id", "name": chat_tools.REVISE_TOOL, "input": inp})
    assert second.server_call_id != ctx.server_call_id
    assert adapter.revise_card_document_call_handler(chat.run, second)[0] == 200
    assert documents.get_by_id(D)["revision_no"] == 2


def test_api_client_cannot_supply_idempotency_key(chat):
    inp = {**COORDS, **request_body(chat)}
    ctx = call_ledger.accept_call(chat.run, {"id": "c", "name": chat_tools.REVISE_TOOL, "input": inp})
    p = error(adapter.revise_card_document_call_handler(chat.run, ctx), 422, "invalid_request")
    assert p["errors"] == [{"field": "idempotency_key", "reason": "unknown_field"}]


def test_failed_revision_storage_rolls_back_document_and_preserves_token_claim(chat, monkeypatch):
    from flowgate.card_document_edit import store
    ensure_guard(chat.env)
    before = business_state(chat.env), token_state(chat)
    monkeypatch.setattr(store, "snapshot_policy_apply", lambda *a: (_ for _ in ()).throw(RuntimeError("history storage failed")))
    result, _ = api(chat, True)
    error(result, 500, "internal_error")
    assert (business_state(chat.env), token_state(chat)) == before
    assert chat.env.events == [] and chat_gate.state(RUN_ID)[1] == 0


def test_request_reads_fresh_size_after_another_transport_enlarges_document(chat, monkeypatch):
    # Change through the shared human service after chat entry auth, before its boundary.
    original = adapter.revise
    def concurrent(req):
        from dataclasses import replace
        out = service.revise(replace(chat.env.req, content=HEADER + "한" * 16001, idempotency_key="human-growth"))
        contacts.run(out.plan)
        return original(req)
    monkeypatch.setattr(adapter, "revise", concurrent)
    result, _ = api(chat, True)
    p = error(result, 413, "result_too_large")
    assert p["revision_no"] == 1
    assert documents.get_by_id(D)["revision_no"] == 1
    assert len(document_revisions.list_by_doc(D)) == 1 and len(chat.env.events) == 3


def test_api_worker_dispatches_registered_read_and_revision_then_delivers_whole_json(chat, monkeypatch):
    from modules.flow_gate.services import ai_invoke_service as svc, chat_run_hooks
    from flowgate.conversation import integration
    monkeypatch.setattr(chat_run_hooks, "_handlers", {})
    integration.register()
    chat.run.update(raw_token="chat", docs_target=0, timed_out=False, mode="single",
                    provider={"name": "mock"}, api_base_url="http://host/flowgate/api/v1")
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_remaining_sec", lambda _run: 60)
    monkeypatch.setattr(worker, "_retry_remaining_sec", lambda _run: 60)
    monkeypatch.setattr(svc, "_conversation_context", lambda *_: (200, {"head_seq": 0, "turns": []}))
    monkeypatch.setattr(svc, "_conversation_turn_register", lambda *_: (201, {"ok": True}))
    turns, names = [], []
    def provider(*args):
        turns.append(list(args[3]))
        names.append([t["name"] for t in args[5]])
        if len(turns) == 1:
            return None, [{"id": "read", "name": chat_tools.READ_TOOL, "input": COORDS}], {"role": "assistant"}
        results = {m["tool_call_id"]: json.loads(m["content"]) for m in turns[-1] if m.get("role") == "tool"}
        if len(turns) == 2:
            snapshot = results["read"]
            assert snapshot["content"] == chat.env.path.read_bytes().decode()
            inp = {**COORDS, "expected_doc_id": snapshot["doc_id"], "expected_revision": snapshot["revision_no"], "content": HEADER + "worker saved"}
            return None, [{"id": "edit", "name": chat_tools.REVISE_TOOL, "input": inp}], {"role": "assistant"}
        assert results["edit"]["ok"] is True and results["edit"]["revision_no"] == 1
        return "done", [{"id": "reply", "name": "send_chat_reply", "input": {"body": "saved"}}], {"role": "assistant"}
    monkeypatch.setattr(svc, "_call_openai", provider)
    before = token_state(chat)
    outcome = svc._api_execute({"id": "provider", "kind": "openai", "api_base_url": "https://example.invalid", "api_model": "mock"}, "prompt", chat.run)
    assert outcome == ("started_ok", None)
    assert chat_tools.READ_TOOL in names[0] and chat_tools.REVISE_TOOL in names[0]
    assert all(name in names[0] for name in ("read_chat_history", "read_help", "send_chat_reply"))
    assert chat.env.path.read_text(encoding="utf-8") == HEADER + "worker saved"
    assert token_state(chat) == before
    assert [x[0] for x in chat.env.events] == ["document", "canvas", "graph"]


@pytest.mark.parametrize("via", ["api", "cli"])
@pytest.mark.parametrize("case,status,code", [
    ("revision", 409, "revision_conflict"),
    ("content", 422, "invalid_content"),
    ("lock", 423, "GROUP_AI_RUN_LOCKED"),
])
def test_rejected_saves_preserve_claim_token_document_and_guard(chat, monkeypatch, via, case, status, code):
    body = request_body(chat)
    if case == "revision":
        body["expected_revision"] = 7
    elif case == "content":
        # 0002-CH turn 48: a body without front matter keeps the current one; a changed identity is still refused
        body["content"] = "---\nproject: flowgate\nmodule: v02\ngroup: 0127\ntype: T\ndoc_number: 0002-N\n---\nchanged"
    else:
        def deny(group_id, principal, operation):
            assert group_id == F and principal.kind == "human" and principal.user_id == "user"
            raise guards.mutation_policy.MutationPolicyError(423, code, "locked", group_id=group_id)
        monkeypatch.setattr(guards.mutation_policy, "assert_group_mutation_allowed", deny)
    ensure_guard(chat.env)
    before = business_state(chat.env), token_state(chat)
    if via == "api":
        result, _ = api(chat, True, {**COORDS, **body})
    else:
        response = cli(chat, True, body)
        result = response.status_code, response.json()
    error(result, status, code)
    assert (business_state(chat.env), token_state(chat)) == before
    assert chat.env.events == [] and chat_gate.state(RUN_ID)[1] == 0


@pytest.mark.parametrize("via", ["api", "cli"])
def test_a_body_without_front_matter_keeps_the_current_front_matter(chat, monkeypatch, via):
    """0002-CH turn 48: the chat's line edit, like its doc_id edit, keeps the document's front matter when the body has
    none (the test server chat failed once with 422 document_identity_changed before sending it again)."""
    monkeypatch.setattr(guards.mutation_policy, "assert_group_mutation_allowed", lambda *a: None)
    body = request_body(chat, content="## body only\n")
    if via == "api":
        result, _ = api(chat, True, {**COORDS, **body})
    else:
        response = cli(chat, True, body)
        result = response.status_code, response.json()
    assert result[0] == 200 and result[1]["revision_no"] == 1, result
    assert chat.env.path.read_text(encoding="utf-8") == HEADER + "\n## body only\n"


def test_cli_chat_routes_use_main_composition_and_human_policy(chat, composition, monkeypatch):
    from fastapi.testclient import TestClient
    _, current, _ = composition
    chat.env.client = TestClient(current)
    principals = []
    def policy(group_id, principal, operation):
        principals.append(principal)
        assert group_id == F and operation == "card_document_edit"
    monkeypatch.setattr(guards.mutation_policy, "assert_group_mutation_allowed", policy)
    before = token_state(chat)
    got = cli(chat)
    assert got.status_code == 200 and got.json()["revision_no"] == 0
    saved = cli(chat, True)
    assert saved.status_code == 200 and saved.json()["revision_no"] == 1
    assert principals and all(p.kind == "human" and p.user_id == "user" for p in principals)
    assert token_state(chat) == before
    assert [e[0] for e in chat.env.events] == ["document", "canvas", "graph"]
