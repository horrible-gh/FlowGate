"""flowgate.v02gui.0028 — chat card AI roles: a chat reads and changes the instruction / execution / review AI of the
work cards it placed itself (``flowgate.chat_control.card_roles``), never another card, never the account defaults.

On the card settings harness (real SQLite DB with every migration, real Graph draft saves, real role stores):

- a placement records its placing chat (CH conversation document, token, AI run) for each new work card only;
- the read answers the card's roles, version / context and the public AI list for its own card;
- a change by a reading-mode chat, a card worker, another chat or on another card is refused; a stale version is 409,
  a running card is 409 ``node_settings_locked``, a role the card does not have is 422; a successful change writes one
  audit row per role (before / after) and leaves the account defaults and the other cards as they were;
- the CLI routes, the API tools and the CLI mention.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, FB, _sql, db_path, decided, env, node, put_draft,
)

CH1, CH2 = f"{FA}.0002-CH", f"{FA}.0003-CH"
EXEC_CARD = {"kind": "assigned", "provider_id": "aip_card"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _group(flow=FA):
    from modules.flow_gate.graph_workflow.services import canvas_service
    return canvas_service.load_flow(flow)


def _scope(flow=FA, token_id="tok_1", run_id="aiv_1"):
    from flowgate.graph_read.authz import Scope
    return Scope(flow, "flowgate", "u_kim", token_id, run_id)


def _caller(doc=CH1, flow=FA, token_id="tok_1", run_id="aiv_1"):
    from flowgate.chat_control import authz
    token = {"action_scope": "chat", "doc_ref": doc, "token_id": token_id, "ai_run_id": run_id,
             "issued_to": "u_kim", "group_id": flow, "project": "flowgate"}
    return authz.Caller(_scope(flow, token_id, run_id), _group(flow), token, {"run_id": run_id, "doc_ref": doc})


def _place(n=1, doc=CH1, token_id="tok_1", run_id="aiv_1"):
    """Cards placed by the chat ``doc`` (``service.place`` with its owner, as the routes / tools pass it)."""
    from flowgate.chat_control import authz, service
    owner = authz.owner(_scope(FA, token_id, run_id), doc)
    return service.place(_group(), "u_kim", {"cards": [{"label": f"c{i}"} for i in range(n)]}, "ko", owner=owner)


def _snapshot(key, flow=FA):
    from flowgate.ai_roles import role_store
    from flowgate.card_settings import repository
    return repository.values(flow, key), role_store.slots(flow, key), repository.settings_version(flow, key)


def _audit(key=None):
    from flowgate.chat_control import card_role_store
    return card_role_store.audit_rows(FA, key)


def _refused(call, status, code):
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    with pytest.raises(GraphApiError) as caught:
        call()
    assert (caught.value.status, caught.value.code) == (status, code), caught.value.params
    return caught.value


@pytest.fixture()
def guard(monkeypatch):
    """The in-guard chat recheck (``chat_authz.chat_recheck``: run / token / edit mode / claim) and the write permission,
    recorded; ``state["recheck"]`` may hold an error the recheck raises."""
    from flowgate.card_document_edit import chat_authz
    from flowgate.chat_control import card_roles

    state = {"calls": [], "recheck": None, "perm": True}

    def recheck(run, token, holder):
        state["calls"].append((run.get("run_id"), token.get("token_id")))
        if state["recheck"] is not None:
            raise state["recheck"]

    monkeypatch.setattr(chat_authz, "chat_recheck", recheck)
    monkeypatch.setattr(card_roles, "has_permission", lambda user, project, perm: state["perm"])
    return state


@pytest.fixture()
def chat_auth(monkeypatch, guard):
    """The chat token boundary of the transports: ``graph_read.authz`` verify / authorize stubbed for the token
    ``state["token"]``, its edit mode ``state["kind"]`` and the Flow write permission."""
    from flowgate.card_document_edit import chat_authz
    from flowgate.chat_control import authz
    from flowgate.graph_read import authz as graph_authz

    state = guard
    state.update(kind="read_write", token={"action_scope": "chat", "doc_ref": CH1, "token_id": "tok_1",
                                           "ai_run_id": "aiv_1", "issued_to": "u_kim", "group_id": FA,
                                           "project": "flowgate"})

    def authorize(token, flow_id, run_hint=None):
        if token.get("group_id") != flow_id:
            graph_authz.refuse("flow_access_denied")
        return _scope(flow_id, token["token_id"], token["ai_run_id"]), _group(flow_id)

    monkeypatch.setattr(graph_authz, "verify", lambda raw: state["token"] if raw == "TOKEN" else graph_authz.refuse("unauthorized", 401))
    monkeypatch.setattr(graph_authz, "authorize", authorize)
    monkeypatch.setattr(chat_authz, "_get_run_record", lambda run_id: {"run_id": run_id, "doc_ref": state["token"]["doc_ref"]})
    monkeypatch.setattr(chat_authz, "effective_write_kind", lambda token: state["kind"])
    monkeypatch.setattr(chat_authz, "claim_pending", lambda token: False)
    monkeypatch.setattr(authz, "has_permission", lambda user, project, perm: state["perm"])
    return state


@pytest.fixture()
def client(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from flowgate.chat_control import routes

    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


# ── placement records the placing chat ───────────────────────────────────────

def test_a_placement_records_its_chat_for_each_work_card_only(env, decided):
    from flowgate.chat_control import card_role_store

    placed = _place(2)
    keys = [c["node_key"] for c in placed["cards"]]
    for key in keys:
        row = card_role_store.placed(FA, key)
        assert (row["chat_doc_id"], row["token_id"], row["ai_run_id"], row["placed_by"]) == (CH1, "tok_1", "aiv_1", "u_kim")
        assert row["placed_at"]
    # the start card and the confirm card a placement adds have no AI roles: no record, so not this chat's cards
    assert card_role_store.placed(FA, placed["gate"]["node_key"]) is None
    assert card_role_store.placed(FA, placed["root"]["node_key"]) is None
    assert set(card_role_store.placed_keys(FA, CH1)) == set(keys)
    assert card_role_store.placed_keys(FA, CH2) == []


def test_a_placement_without_a_chat_owner_records_nothing(env, decided):
    from flowgate.chat_control import card_role_store, service

    placed = service.place(_group(), "u_kim", {"cards": [{}]}, "ko")
    assert card_role_store.placed(FA, placed["cards"][0]["node_key"]) is None


def test_cli_route_and_api_tool_placements_record_the_requesting_chat(env, decided, chat_auth, client, monkeypatch):
    from flowgate.chat_control import authz, card_role_store, tools

    res = client.post(f"/api/v2/chat/flows/{FA}/cards", json={"cards": [{"label": "cli"}]},
                      headers={"Authorization": "Bearer TOKEN"})
    assert res.status_code == 201, res.text
    key = res.json()["cards"][0]["node_key"]
    assert card_role_store.placed(FA, key)["chat_doc_id"] == CH1

    # API transport: the run handed over (its doc_ref bound to its current token by from_run) owns the cards
    monkeypatch.setattr(authz, "from_run", lambda run, flow_id: (_scope(flow_id, "tok_9", "aiv_9"), _group(flow_id)))
    status, body = tools.place_call({"group_id": FA, "doc_ref": CH2, "locale": "ko"}, {"cards": [{"label": "api"}]})
    assert status == 200, body
    row = card_role_store.placed(FA, body["cards"][0]["node_key"])
    assert (row["chat_doc_id"], row["token_id"], row["ai_run_id"]) == (CH2, "tok_9", "aiv_9")


# ── read ─────────────────────────────────────────────────────────────────────

def test_the_chat_reads_the_roles_of_its_own_card(env, decided, guard):
    from flowgate.ai_roles import role_store
    from flowgate.chat_control import card_roles

    key = _place()["cards"][0]["node_key"]
    answer = card_roles.view(_caller(), key)
    assert answer["card_kind"] == "run" and answer["applicable_roles"] == list(role_store.ROLES)
    assert set(answer["roles"]) == set(role_store.ROLES)
    assert all(set(r) == set(card_roles.STATE_KEYS) for r in answer["roles"].values())
    assert answer["settings_version"] == _snapshot(key)[2] and answer["locked"] is False
    assert set(answer["context"]) == {"run_id", "graph_revision_no", "draft_version"}
    assert answer["options_state"] == "ok" and {o["id"] for o in answer["options"]} >= {"aip_card", "aip_assignee"}
    # only public AI fields: never a key, endpoint or setting of the AI
    assert all(set(o) == {"id", "name", "exec_type", "kind", "owner_scope"} for o in answer["options"])
    assert answer["placed"]["chat_doc_id"] == CH1 and answer["roles_available"] is True
    for hidden in ("fields", "preserved", "legacy"):
        assert hidden not in answer


def test_a_card_worker_is_refused_on_read_and_change():
    from flowgate.chat_control import authz

    worker = {"action_scope": "edit", "doc_ref": "x.0001-T", "token_id": "tok_w", "ai_run_id": "aiv_w"}
    _refused(lambda: authz.read_caller(worker, FA, {"run_id": "aiv_w"}), 403, "token_kind_not_allowed")
    _refused(lambda: authz.write_caller(worker, FA, {"run_id": "aiv_w"}), 403, "token_kind_not_allowed")
    _refused(lambda: authz.read_caller(None, FA), 403, "token_kind_not_allowed")


def test_another_groups_flow_is_refused_by_the_read_boundary(monkeypatch):
    """The real ``graph_read.authz.authorize``: a chat token of another group never reaches the Flow."""
    from flowgate.card_document_edit import chat_authz
    from flowgate.chat_control import authz
    from flowgate.graph_read import authz as graph_authz

    monkeypatch.setattr(chat_authz, "token_live", lambda token, now=None: chat_authz.LIVE)
    monkeypatch.setattr(chat_authz, "check_run_token_binding", lambda run, token: None)
    monkeypatch.setattr(graph_authz, "_live", lambda run: None)
    token = {"action_scope": "chat", "doc_ref": f"{FB}.0001-CH", "token_id": "t", "ai_run_id": "r", "group_id": FB}
    _refused(lambda: authz.read_caller(token, FA, {"run_id": "r", "status": "running"}), 403, "flow_access_denied")


def test_cards_this_chat_did_not_place_are_refused(env, decided, guard):
    from flowgate.chat_control import card_role_store, card_roles

    mine = _place()["cards"][0]["node_key"]
    other_chat = _place(doc=CH2, token_id="tok_2", run_id="aiv_2")["cards"][0]["node_key"]
    person = put_draft(env, FB, 0, [node("tmp_c", "T")])["key_map"]["tmp_c"]
    before = {k: _snapshot(k, f) for k, f in ((mine, FA), (other_chat, FA), (person, FB))}
    body = {"roles": {"execution": EXEC_CARD}, "expected_settings_version": 0}
    # another chat's card (same Flow), a card a person placed, a card of no Flow, the start / confirm cards
    err = _refused(lambda: card_roles.view(_caller(), other_chat), 403, card_roles.NOT_OWNED)
    assert err.params["placed_by_this_chat"] == [mine]
    _refused(lambda: card_roles.change(_caller(), other_chat, body), 403, card_roles.NOT_OWNED)
    _refused(lambda: card_roles.change(_caller(flow=FB), person, body), 403, card_roles.NOT_OWNED)
    _refused(lambda: card_roles.view(_caller(), "gn_missing"), 403, card_roles.NOT_OWNED)
    # the record is per Flow: the same node key asked on another Flow is not this chat's card
    _refused(lambda: card_roles.view(_caller(flow=FB), mine), 403, card_roles.NOT_OWNED)
    assert card_role_store.placed(FA, other_chat)["chat_doc_id"] == CH2
    assert {k: _snapshot(k, f) for k, f in ((mine, FA), (other_chat, FA), (person, FB))} == before
    assert _audit() == [] and guard["calls"] == []


# ── change ───────────────────────────────────────────────────────────────────

def test_a_change_writes_the_roles_with_one_audit_row_each_and_nothing_else(env, decided, guard):
    from flowgate.ai_roles import role_store as rs
    from flowgate.chat_control import card_roles

    account = {r: rs.RoleValue("assigned", "aip_assignee", "snap") for r in rs.ROLES}
    rs.save_account_roles("u_kim", account)
    account_before = rs.account_roles("u_kim")
    keys = [c["node_key"] for c in _place(2)["cards"]]
    mine, sibling = keys
    sibling_before = _snapshot(sibling)
    values_before, slots_before, version = _snapshot(mine)
    assert slots_before["execution"].value.provider_id == "aip_assignee"      # copied from the account at creation
    env["events"].clear()

    answer = card_roles.change(_caller(), mine, {"roles": {"execution": EXEC_CARD, "review": {"kind": "not_used"}},
                                                 "expected_settings_version": version})
    assert answer["changed_roles"] == ["execution", "review"] and answer["settings_version"] == version + 1
    assert answer["roles"]["execution"]["selected"] == EXEC_CARD and answer["roles"]["execution"]["outcome"] == "ai"
    assert answer["roles"]["review"]["selected"] == {"kind": "not_used", "provider_id": None}
    assert answer["roles"]["instruction"]["selected"] == {"kind": "assigned", "provider_id": "aip_assignee"}
    assert guard["calls"] == [("aiv_1", "tok_1")]                             # re-judged inside the guard
    values, slots, new_version = _snapshot(mine)
    assert values == values_before and new_version == version + 1            # no other card setting moved
    assert slots["execution"].source == "card_explicit" and slots["execution"].set_by == "u_kim"
    assert slots["instruction"] == slots_before["instruction"]
    assert _snapshot(sibling) == sibling_before                              # the other card is untouched
    assert rs.account_roles("u_kim") == account_before                       # the account defaults are untouched
    assert env["events"], "the change announces a canvas change"

    rows = {r["role"]: r for r in _audit(mine)}
    assert set(rows) == {"execution", "review"} and len(_audit(mine)) == 2
    assert set(answer["audit_ids"]) == {r["audit_id"] for r in rows.values()}
    ex = rows["execution"]
    assert (ex["token_id"], ex["ai_run_id"], ex["chat_doc_id"], ex["actor_user_id"], ex["group_id"], ex["node_key"]) == \
        ("tok_1", "aiv_1", CH1, "u_kim", FA, mine)
    assert ex["before"]["selected"] == {"kind": "assigned", "provider_id": "aip_assignee"}
    assert ex["after"]["selected"] == EXEC_CARD and ex["changed"] is True and ex["settings_version"] == version + 1
    assert rows["review"]["after"]["outcome"] == "person" and ex["created_at"]

    # the same values again: nothing changes, the version stays, the request is still audited (changed false)
    again = card_roles.change(_caller(), mine, {"roles": {"execution": EXEC_CARD}, "expected_settings_version": version + 1})
    assert again["changed_roles"] == [] and again["settings_version"] == version + 1
    assert [r["changed"] for r in _audit(mine) if r["role"] == "execution"] == [True, False]


def test_the_same_chat_in_a_later_run_still_owns_its_cards(env, decided, guard):
    from flowgate.chat_control import card_roles

    key = _place()["cards"][0]["node_key"]
    later = _caller(token_id="tok_7", run_id="aiv_7")                        # same CH, a new run and token
    version = card_roles.view(later, key)["settings_version"]
    answer = card_roles.change(later, key, {"roles": {"execution": EXEC_CARD}, "expected_settings_version": version})
    assert answer["changed_roles"] == ["execution"]
    assert _audit(key)[0]["token_id"] == "tok_7" and _audit(key)[0]["ai_run_id"] == "aiv_7"


def test_a_reading_mode_chat_is_refused_and_writes_nothing(env, decided, chat_auth, client):
    from flowgate.chat_control import authz

    key = _place()["cards"][0]["node_key"]
    before = _snapshot(key)
    chat_auth["kind"] = "read"
    _refused(lambda: authz.write_caller(chat_auth["token"], FA), 403, "chat_edit_mode_required")
    res = client.patch(f"/api/v2/chat/flows/{FA}/nodes/{key}/roles", headers={"Authorization": "Bearer TOKEN"},
                       json={"roles": {"execution": EXEC_CARD}, "expected_settings_version": before[2]})
    assert res.status_code == 403 and res.json()["error"]["code"] == "chat_edit_mode_required"
    # reading is any mode
    res = client.get(f"/api/v2/chat/flows/{FA}/nodes/{key}/roles", headers={"Authorization": "Bearer TOKEN"})
    assert res.status_code == 200 and res.json()["chat_can_change"] is False
    assert _snapshot(key) == before and _audit() == []


def test_the_in_guard_recheck_refuses_a_chat_that_left_edit_mode_or_its_permission(env, decided, guard):
    from flowgate.card_document_edit import chat_errors
    from flowgate.chat_control import card_roles

    key = _place()["cards"][0]["node_key"]
    before = _snapshot(key)
    body = {"roles": {"execution": EXEC_CARD}, "expected_settings_version": before[2]}
    guard["recheck"] = chat_errors.edit_mode_denied("aiv_1", "read")
    _refused(lambda: card_roles.change(_caller(), key, body), 403, "chat_edit_mode_required")
    guard["recheck"] = chat_errors.run_finished("aiv_1")
    _refused(lambda: card_roles.change(_caller(), key, body), 409, chat_errors.RUN_FINISHED)
    guard["recheck"], guard["perm"] = None, False
    _refused(lambda: card_roles.change(_caller(), key, body), 403, "flow_access_denied")
    assert _snapshot(key) == before and _audit() == []


def test_a_stale_version_or_context_is_409_and_writes_nothing(env, decided, guard):
    from flowgate.chat_control import card_roles

    key = _place()["cards"][0]["node_key"]
    read = card_roles.view(_caller(), key)
    before = _snapshot(key)
    err = _refused(lambda: card_roles.change(_caller(), key, {"roles": {"execution": EXEC_CARD},
                                                              "expected_settings_version": read["settings_version"] + 1}),
                   409, "gate_context_changed")
    assert err.params["reason"] == "settings_version" and err.params["settings_version"] == read["settings_version"]
    stale = {**read["context"], "draft_version": (read["context"]["draft_version"] or 0) + 5}
    err = _refused(lambda: card_roles.change(_caller(), key, {"roles": {"execution": EXEC_CARD}, "expected_context": stale,
                                                              "expected_settings_version": read["settings_version"]}),
                   409, "gate_context_changed")
    assert err.params["reason"] == "settings_context"
    assert _snapshot(key) == before and _audit() == []
    # the context the read answered is accepted
    ok = card_roles.change(_caller(), key, {"roles": {"execution": EXEC_CARD}, "expected_context": read["context"],
                                            "expected_settings_version": read["settings_version"]})
    assert ok["changed_roles"] == ["execution"]


def test_a_running_card_is_locked(env, decided, guard, monkeypatch):
    from flowgate.card_settings import settings
    from flowgate.chat_control import card_roles

    key = _place()["cards"][0]["node_key"]
    before = _snapshot(key)
    monkeypatch.setattr(settings.legacy, "node_running", lambda *a: True)
    assert card_roles.view(_caller(), key)["locked"] is True
    _refused(lambda: card_roles.change(_caller(), key, {"roles": {"execution": EXEC_CARD},
                                                        "expected_settings_version": before[2]}),
             409, "node_settings_locked")
    assert _snapshot(key) == before and _audit() == []


def test_a_role_the_card_does_not_have_is_refused(env, decided, guard):
    """A plan card has no instruction role (``resolve.applicable_roles``). A chat places only T / TS cards (all three
    roles), so its record is written here directly to reach the rule."""
    from flowgate.chat_control import card_role_store, card_roles

    key = put_draft(env, FB, 0, [node("tmp_c", "WP")])["key_map"]["tmp_c"]
    card_role_store.record_placed(FB, [key], CH1, "tok_1", "aiv_1", "u_kim")
    caller = _caller(flow=FB)
    assert card_roles.view(caller, key)["applicable_roles"] == ["execution", "review"]
    before = _snapshot(key, FB)
    err = _refused(lambda: card_roles.change(caller, key, {"roles": {"instruction": EXEC_CARD, "execution": EXEC_CARD},
                                                           "expected_settings_version": before[2]}),
                   422, "invalid_request")
    assert err.params["errors"] == [{"field": "roles.instruction", "reason": "role_not_applicable", "card_kind": "plan",
                                     "applicable_roles": ["execution", "review"]}]
    assert _snapshot(key, FB) == before                                     # all or nothing: execution not written


@pytest.mark.parametrize("body, field, reason", [
    (None, "body", "not_an_object"),
    ({"expected_settings_version": 0}, "roles", "required_non_empty_object"),
    ({"roles": {"hold": True}, "expected_settings_version": 0}, "roles.hold", "not_a_role"),
    ({"roles": {"merge_target": "x"}, "expected_settings_version": 0}, "roles.merge_target", "not_a_role"),
    ({"field": "hold", "value": True, "roles": {"execution": EXEC_CARD}, "expected_settings_version": 0}, "field", "unknown_field"),
    ({"roles": {"execution": EXEC_CARD}}, "expected_settings_version", "required"),
    ({"roles": {"execution": EXEC_CARD}, "expected_settings_version": True}, "expected_settings_version", "not_a_version"),
    ({"roles": {"execution": None}, "expected_settings_version": 0}, "roles.execution", "role_unset_not_allowed"),
    ({"roles": {"execution": {"kind": "assigned"}}, "expected_settings_version": 0}, "roles.execution", "role_value_malformed"),
    ({"roles": {"review": {"kind": "not_used", "provider_id": "x"}}, "expected_settings_version": 0}, "roles.review", "role_value_malformed"),
])
def test_only_the_three_roles_are_accepted(body, field, reason):
    from flowgate.chat_control import card_roles

    err = _refused(lambda: card_roles.parse_change(body), 422, "invalid_request")
    assert {"field": field, "reason": reason}.items() <= err.params["errors"][0].items(), err.params


@pytest.mark.parametrize("setup, cause", [("other_user", "not_selectable"), ("disabled", "inactive"), ("deleted", "deleted")])
def test_another_users_or_an_unavailable_ai_is_refused(env, decided, guard, setup, cause):
    from flowgate.chat_control import card_roles

    key = _place()["cards"][0]["node_key"]
    pid = "aip_lee"
    if setup == "other_user":
        _sql(env["db"], "INSERT INTO ai_providers(provider_id,name,exec_type,kind,enabled,owner_scope,owner_user_id,"
                        "created_at,updated_at) VALUES (?,?,'api','openai',1,'user','u_lee','t','t')", (pid, pid))
    elif setup == "disabled":
        pid = "aip_card"
        _sql(env["db"], "UPDATE ai_providers SET enabled=0 WHERE provider_id='aip_card'")
    else:
        pid = "aip_gone"
    before = _snapshot(key)
    err = _refused(lambda: card_roles.change(_caller(), key, {
        "roles": {"review": {"kind": "not_used"}, "execution": {"kind": "assigned", "provider_id": pid}},
        "expected_settings_version": before[2]}), 422, "invalid_request")
    assert err.params["errors"] == [{"field": "roles.execution", "reason": "ai_not_selectable", "cause": cause}]
    assert _snapshot(key) == before and _audit() == []                       # the review change rolled back too


# ── transports ───────────────────────────────────────────────────────────────

def test_cli_routes_read_and_change_with_the_bearer(env, decided, chat_auth, client):
    key = _place()["cards"][0]["node_key"]
    res = client.get(f"/api/v2/chat/flows/{FA}/nodes/{key}/roles", headers={"Authorization": "Bearer TOKEN"})
    assert res.status_code == 200, res.text
    read = res.json()
    assert read["ok"] is True and read["chat_can_change"] is True and "next" in read
    res = client.patch(f"/api/v2/chat/flows/{FA}/nodes/{key}/roles", headers={"Authorization": "Bearer TOKEN"},
                       json={"roles": {"execution": EXEC_CARD}, "expected_settings_version": read["settings_version"],
                             "expected_context": read["context"]})
    assert res.status_code == 200, res.text
    assert res.json()["changed_roles"] == ["execution"]
    again = client.get(f"/api/v2/chat/flows/{FA}/nodes/{key}/roles", headers={"Authorization": "Bearer TOKEN"}).json()
    assert again["roles"]["execution"]["selected"] == EXEC_CARD
    assert again["settings_version"] == read["settings_version"] + 1
    # no Bearer: 401 before the body; another group's token: refused by the boundary
    assert client.patch(f"/api/v2/chat/flows/{FA}/nodes/{key}/roles", content=b"not json").status_code == 401
    chat_auth["token"] = {**chat_auth["token"], "group_id": FB}
    res = client.get(f"/api/v2/chat/flows/{FA}/nodes/{key}/roles", headers={"Authorization": "Bearer TOKEN"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "flow_access_denied"
    # a card worker's Bearer
    chat_auth["token"] = {**chat_auth["token"], "group_id": FA, "action_scope": "edit"}
    for method in ("get", "patch"):
        res = getattr(client, method)(f"/api/v2/chat/flows/{FA}/nodes/{key}/roles",
                                      headers={"Authorization": "Bearer TOKEN"},
                                      **({"json": {}} if method == "patch" else {}))
        assert res.status_code == 403 and res.json()["error"]["code"] == "token_kind_not_allowed"


def test_api_tools_hand_the_run_to_the_card_role_service(monkeypatch):
    from flowgate.chat_control import authz, card_roles, tools

    seen = []
    monkeypatch.setattr(authz, "caller_from_run",
                        lambda run, flow_id, write: seen.append((flow_id, write)) or "CALLER")
    monkeypatch.setattr(card_roles, "view", lambda caller, key: {"viewed": key, "caller": caller})
    monkeypatch.setattr(card_roles, "change", lambda caller, key, body: {"changed": key, "body": body})
    status, body = tools.get_roles_call({"group_id": FA}, {"node_key": "gn_1"})
    assert status == 200 and body["viewed"] == "gn_1" and seen[-1] == (FA, False)
    status, body = tools.set_roles_call({"group_id": FA}, {"flow_id": FA, "node_key": "gn_1", "roles": {"review": {"kind": "not_used"}},
                                                           "expected_settings_version": 3})
    assert status == 200 and body["body"] == {"roles": {"review": {"kind": "not_used"}}, "expected_settings_version": 3}
    assert seen[-1] == (FA, True)
    status, body = tools.get_roles_call({"group_id": FA}, {"node_key": "gn_1", "field": "hold"})
    assert status == 422 and body["error"]["params"]["errors"] == [{"field": "field", "reason": "unknown_field"}]
    status, body = tools.set_roles_call({"group_id": FA}, {"roles": {}})
    assert status == 422 and body["error"]["params"]["errors"][0]["field"] == "node_key"


def test_tools_and_cli_mention_offer_the_card_role_api():
    from flowgate.chat_control import tools

    by_name = {t["name"]: t for t in tools.descriptors()}
    assert {"get_card_roles", "set_card_roles"} <= set(by_name)
    assert by_name["set_card_roles"]["schema"]["required"] == ["node_key", "roles", "expected_settings_version"]
    assert set(by_name["set_card_roles"]["schema"]["properties"]["roles"]["properties"]) == {"instruction", "execution", "review"}
    assert all(callable(t["handler"]) for t in by_name.values())
    text = tools.cli_mention("http://h/flowgate/api/v1", "TOKEN")
    for word in ("GET http://h/flowgate/api/v2/chat/flows/{flow_id}/nodes/{node_key}/roles",
                 "PATCH http://h/flowgate/api/v2/chat/flows/{flow_id}/nodes/{node_key}/roles",
                 "expected_settings_version", "chat_card_not_owned", "only cards this chat placed",
                 "Only a GET after it", "report its code and message", "node_settings_locked"):
        assert word in text, word
