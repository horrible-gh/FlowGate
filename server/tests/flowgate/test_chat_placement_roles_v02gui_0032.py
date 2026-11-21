"""decisions 6-8 — the chat chooses the AI roles of the cards it places.

On the card settings harness (real SQLite DB with every migration, real Graph draft saves, real role stores):

- a placement may carry each card's ``roles``: written as the card's own choice (``card_explicit``) in the draft
  save's guard, audited per role, answered per card with where each role came from;
- roles left out keep the creation defaults and are listed in ``defaults_used``;
- another user's AI is refused before anything is placed; a refusal inside the guard rolls the whole placement back;
- ``GET .../ai-options`` lists the AIs and the AI groups with role profiles in any mode;
- ``from_group`` applies a named AI group's profile through the card role change (explicit roles win);
- the API tools and the CLI mention offer all of it.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, FB, _sql, db_path, decided, env,
)
from test_chat_card_roles_v02gui_0028 import (  # noqa: F401 — fixtures used by name
    CH1, _audit, _caller, _group, _refused, _scope, _snapshot, chat_auth, client, guard,
)

CHOSEN = {"instruction": {"kind": "not_used"},
          "execution": {"kind": "assigned", "provider_id": "aip_card"},
          "review": {"kind": "assigned", "provider_id": "aip_assignee"}}


def _place(cards, doc=CH1):
    from flowgate.chat_control import authz, service
    return service.place(_group(), "u_kim", {"cards": cards}, "ko", owner=authz.owner(_scope(), doc))


def _general_group(gid="grp_team", typed=None, common=None):
    """An AI group with role profiles (not the role group, so card creation never copies from it)."""
    from modules.flow_gate.db import ai_group_role_profiles as profiles
    from modules.flow_gate.db import ai_groups

    ai_groups.create({"ai_group_id": gid, "name": gid, "name_key": gid, "name_key_hash": gid.ljust(64, "0"),
                      "description": ""})
    for type_key, cells in (("common", common or {}), ("T", typed)):
        if cells is None:
            continue
        profiles.create(gid, type_key)
        for role, value in cells.items():
            profiles.set_cell(gid, type_key, role, value["kind"], value.get("provider_id"), value.get("provider_id"))
    return gid


# ── placement with chosen roles ──────────────────────────────────────────────

def test_a_placement_writes_the_chosen_roles_as_the_cards_own_and_audits_them(env, decided):
    from flowgate.ai_roles import role_store as rs

    rs.save_account_roles("u_kim", {r: rs.RoleValue("assigned", "aip_assignee", "snap") for r in rs.ROLES})
    placed = _place([{"label": "a", "roles": CHOSEN}, {"label": "b", "roles": {"execution": CHOSEN["execution"]}}])
    first, second = placed["cards"]
    slots = rs.slots(FA, first["node_key"])
    assert {r: (s.value.kind, s.value.provider_id, s.source) for r, s in slots.items()} == {
        "instruction": ("not_used", None, "card_explicit"),
        "execution": ("assigned", "aip_card", "card_explicit"),
        "review": ("assigned", "aip_assignee", "card_explicit")}
    assert {r: v["source"] for r, v in first["roles"].items()} == {r: "chat" for r in rs.ROLES}
    assert first["roles"]["execution"] == {"kind": "assigned", "provider_id": "aip_card", "name": "aip_card",
                                           "source": "chat"}
    # the second card chose its execution AI only: the other two stayed on the account default copied at creation
    assert {r: v["source"] for r, v in second["roles"].items()} == {
        "instruction": "default", "execution": "chat", "review": "default"}
    assert placed["defaults_used"] == [{"node_key": second["node_key"], "role": "instruction", "source": "default"},
                                       {"node_key": second["node_key"], "role": "review", "source": "default"}]
    assert "defaults_used" in placed["next"]
    rows = _audit(first["node_key"])
    assert sorted(r["role"] for r in rows) == ["execution", "instruction", "review"]
    assert all(r["chat_doc_id"] == CH1 and r["token_id"] == "tok_1" and r["actor_user_id"] == "u_kim" for r in rows)
    ex = next(r for r in rows if r["role"] == "execution")
    assert ex["before"]["selected"] == {"kind": "assigned", "provider_id": "aip_assignee"}
    assert ex["after"]["selected"] == {"kind": "assigned", "provider_id": "aip_card"} and ex["changed"] is True
    assert rs.account_roles("u_kim").values["execution"].provider_id == "aip_assignee"   # account untouched


def test_a_placement_without_roles_keeps_the_defaults_and_lists_them(env, decided):
    placed = _place([{"label": "a"}])
    card = placed["cards"][0]
    assert {v["source"] for v in card["roles"].values()} <= {"default", "unset"}
    assert {d["role"] for d in placed["defaults_used"]} == {"instruction", "execution", "review"}
    assert _audit() == []


@pytest.mark.parametrize("roles, field, reason", [
    ({"hold": True}, "cards[0].roles.hold", "not_a_role"),
    ({"execution": None}, "cards[0].roles.execution", "role_unset_not_allowed"),
    ({"execution": {"kind": "assigned"}}, "cards[0].roles.execution", "role_value_malformed"),
    ({"review": {"kind": "not_used", "provider_id": "x"}}, "cards[0].roles.review", "role_value_malformed"),
    ({"review": {"kind": "not_used", "name": "x"}}, "cards[0].roles.review", "role_value_malformed"),
    ("all", "cards[0].roles", "not_an_object"),
])
def test_the_roles_of_a_placed_card_follow_the_card_role_grammar(roles, field, reason):
    from flowgate.chat_control import service

    err = _refused(lambda: service.parse_place({"cards": [{"label": "a", "roles": roles}]}, "ko"), 422,
                   "invalid_request")
    assert {"field": field, "reason": reason}.items() <= err.params["errors"][0].items(), err.params


def test_another_users_ai_is_refused_before_anything_is_placed(env, decided):
    from flowgate.chat_control import card_role_store
    from modules.flow_gate.graph_workflow.store import definition_store

    _sql(env["db"], "INSERT INTO ai_providers(provider_id,name,exec_type,kind,enabled,owner_scope,owner_user_id,"
                    "created_at,updated_at) VALUES ('aip_lee','aip_lee','api','openai',1,'user','u_lee','t','t')")
    before = definition_store.draft(FA)
    err = _refused(lambda: _place([{"label": "a", "roles": {"execution": {"kind": "assigned",
                                                                          "provider_id": "aip_lee"}}}]),
                   422, "invalid_request")
    assert err.params["errors"] == [{"field": "cards[0].roles.execution", "reason": "ai_not_selectable",
                                     "cause": "not_selectable", "provider_id": "aip_lee"}]
    assert definition_store.draft(FA) == before and card_role_store.placed_keys(FA, CH1) == []


def test_a_refusal_inside_the_guard_rolls_the_whole_placement_back(env, decided, monkeypatch):
    from flowgate.chat_control import card_role_store, placement_roles
    from modules.flow_gate.graph_workflow.store import definition_store

    _place([{"label": "first"}])                       # the start card and a first card exist already
    before = definition_store.draft(FA)
    kept = card_role_store.placed_keys(FA, CH1)
    _sql(env["db"], "UPDATE ai_providers SET enabled=0 WHERE provider_id='aip_card'")
    monkeypatch.setattr(placement_roles, "precheck", lambda cards, user_id: None)   # the AI turned off meanwhile
    err = _refused(lambda: _place([{"label": "a", "roles": {"review": {"kind": "not_used"},
                                                            "execution": CHOSEN["execution"]}}]),
                   422, "invalid_request")
    assert err.params["errors"][0]["reason"] == "ai_not_selectable"
    assert definition_store.draft(FA) == before and card_role_store.placed_keys(FA, CH1) == kept
    assert _audit() == []


# ── the AI list before a placement ───────────────────────────────────────────

def test_ai_options_list_the_selectable_ais_and_groups_in_any_mode(env, decided, chat_auth, client):
    _general_group(common={"execution": {"kind": "assigned", "provider_id": "aip_card"}})
    chat_auth["kind"] = "read"                        # a reading-mode chat reads the list too
    res = client.get(f"/api/v2/chat/flows/{FA}/ai-options", headers={"Authorization": "Bearer TOKEN"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["options_state"] == "ok" and {o["id"] for o in body["options"]} >= {"aip_card", "aip_assignee"}
    assert all(set(o) == {"id", "name", "exec_type", "kind", "owner_scope"} for o in body["options"])
    assert [g["ai_group_id"] for g in body["role_groups"]] == ["grp_team"]
    assert body["role_groups"][0]["type_keys"] == ["common"] and "next" in body
    chat_auth["token"] = {**chat_auth["token"], "group_id": FB}
    res = client.get(f"/api/v2/chat/flows/{FA}/ai-options", headers={"Authorization": "Bearer TOKEN"})
    assert res.status_code == 403 and res.json()["error"]["code"] == "flow_access_denied"


# ── a named AI group's preset (from_group) ───────────────────────────────────

def test_from_group_applies_the_groups_profile_and_explicit_roles_win(env, decided, guard):
    from flowgate.ai_roles import role_store as rs
    from flowgate.chat_control import card_roles

    gid = _general_group(common={"execution": {"kind": "assigned", "provider_id": "aip_card"},
                                 "instruction": {"kind": "assigned", "provider_id": "aip_card"}},
                         typed={"review": {"kind": "not_used"}})
    key = _place([{"label": "a"}])["cards"][0]["node_key"]
    version = _snapshot(key)[2]
    answer = card_roles.change(_caller(), key, {"from_group": gid, "roles": {"instruction": {"kind": "not_used"}},
                                                "expected_settings_version": version})
    assert answer["from_group"] == gid and answer["applied_roles"] == ["execution", "instruction", "review"]
    slots = rs.slots(FA, key)
    assert (slots["execution"].value.provider_id, slots["review"].value.kind, slots["instruction"].value.kind) == \
        ("aip_card", "not_used", "not_used")
    assert {r["role"] for r in _audit(key)} == {"execution", "instruction", "review"}


def test_from_group_refusals(env, decided, guard):
    from flowgate.chat_control import card_roles

    key = _place([{"label": "a"}])["cards"][0]["node_key"]
    version = _snapshot(key)[2]
    _refused(lambda: card_roles.change(_caller(), key, {"from_group": "grp_none", "expected_settings_version": version}),
             404, "ai_group_not_found")
    empty = _general_group("grp_empty", common={})
    _refused(lambda: card_roles.change(_caller(), key, {"from_group": empty, "expected_settings_version": version}),
             422, "ai_group_no_profile")
    err = _refused(lambda: card_roles.parse_change({"from_group": " ", "expected_settings_version": 0}), 422,
                   "invalid_request")
    assert err.params["errors"][0]["field"] == "from_group"
    # without a group the roles stay required
    err = _refused(lambda: card_roles.parse_change({"expected_settings_version": 0}), 422, "invalid_request")
    assert err.params["errors"][0] == {"field": "roles", "reason": "required_non_empty_object"}
    assert _snapshot(key)[2] == version and _audit() == []


# ── tools and mention ────────────────────────────────────────────────────────

def test_tools_and_cli_mention_offer_role_choice(monkeypatch):
    from flowgate.chat_control import authz, placement_roles, tools

    by_name = {t["name"]: t for t in tools.descriptors()}
    item = by_name["place_flow_cards"]["schema"]["properties"]["cards"]["items"]["properties"]
    assert set(item["roles"]["properties"]) == {"instruction", "execution", "review"}
    assert "from_group" in by_name["set_card_roles"]["schema"]["properties"]
    assert by_name["get_ai_options"]["schema"]["additionalProperties"] is False
    seen = []
    monkeypatch.setattr(authz, "caller_from_run",
                        lambda run, flow_id, write: seen.append((flow_id, write))
                        or authz.Caller(_scope(), {"group_id": FA}, {}, None))
    monkeypatch.setattr(placement_roles, "options", lambda user_id, flow_id: {"user": user_id, "flow": flow_id})
    status, body = tools.options_call({"group_id": FA}, {})
    assert status == 200 and body["user"] == "u_kim" and seen[-1] == (FA, False)
    status, body = tools.options_call({"group_id": FA}, {"x": 1})
    assert status == 422
    text = tools.cli_mention("http://h/flowgate/api/v1", "TOKEN")
    for word in ("GET http://h/flowgate/api/v2/chat/flows/{flow_id}/ai-options", '"roles": {"instruction": {"kind": '
                 '"not_used"}', "defaults_used", "from_group", "only when the user names", "not_used when you write"):
        assert word in text, word
    assert "Choose each card's AI yourself" in tools.PROMPT_HINT
