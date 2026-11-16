"""flowgate.v02gui.0032 (0002-CH seq 15-17) — a chat reads and changes the review count, restarts and time limit of the
cards it placed (``flowgate.chat_control.run_settings``), with the roles or alone, and chooses them when it places.

On the card settings harness (real SQLite DB with every migration, real Graph draft saves, real role stores):

- the read answers each setting's value, default and choices for the card's kind (a TS card: review count only);
- a change writes the values with one audit row each, moves the version once together with roles, and leaves the
  roles, the other settings and the other cards as they were; null takes the default back;
- a value the person's screen cannot pick, a setting the card does not have, an unknown setting, a running card, a
  stale version and a reading-mode chat are refused and write nothing (roles sent with them roll back too);
- a placement may carry each card's settings: checked before anything is saved, written in the save's guard, audited,
  answered per card;
- the CLI route, the API tool schemas and the CLI mention offer it.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, FB, _sql, db_path, decided, env,
)
from test_chat_card_roles_v02gui_0028 import (  # noqa: F401 — fixtures used by name
    CH1, EXEC_CARD, _audit, _caller, _group, _refused, _scope, _snapshot, chat_auth, client, guard,
)


def _place(cards, doc=CH1):
    from flowgate.chat_control import authz, service
    return service.place(_group(), "u_kim", {"cards": cards}, "ko", owner=authz.owner(_scope(), doc))


def _setting_audit(key=None):
    from flowgate.chat_control import card_role_store
    return card_role_store.setting_audit_rows(FA, key)


# ── read ─────────────────────────────────────────────────────────────────────

def test_the_read_answers_the_run_settings_with_their_choices(env, decided, guard):
    from flowgate.card_settings import fields
    from flowgate.chat_control import card_roles

    work, test = (c["node_key"] for c in _place([{"label": "w"}, {"label": "t", "doc_type": "TS"}])["cards"])
    answer = card_roles.view(_caller(), work)
    assert answer["applicable_settings"] == ["review_count", "restart", "time_limit"]
    assert set(answer["settings"]) == {"review_count", "restart", "time_limit"}
    assert set(answer["settings"]["review_count"]) == {"value", "stored", "default", "choices"}
    assert answer["settings"]["time_limit"]["choices"] == list(fields.TIME_LIMIT_MINUTES)
    assert answer["settings"]["restart"]["default"] == fields.DEFAULT_RESTART_COUNT
    assert {-1, 0, 1, 2} <= set(answer["settings"]["review_count"]["choices"])
    assert "settings" in answer["next"] and answer["settings_note"]
    # a test card has the review count only; nothing else of the card's settings is shown
    ts = card_roles.view(_caller(), test)
    assert ts["applicable_settings"] == ["review_count"] and list(ts["settings"]) == ["review_count"]
    assert "fields" not in answer and "hold" not in answer["settings"]


# ── change ───────────────────────────────────────────────────────────────────

def test_a_settings_change_writes_the_values_audits_each_and_touches_nothing_else(env, decided, guard):
    from flowgate.card_settings import repository
    from flowgate.chat_control import card_roles

    mine, sibling = (c["node_key"] for c in _place([{"label": "a"}, {"label": "b"}])["cards"])
    sibling_before = _snapshot(sibling)
    values_before, slots_before, version = _snapshot(mine)
    env["events"].clear()
    answer = card_roles.change(_caller(), mine, {"settings": {"review_count": 2, "time_limit": 60},
                                                 "expected_settings_version": version})
    assert answer["changed_settings"] == ["review_count", "time_limit"] and answer["changed_roles"] == []
    assert answer["settings_version"] == version + 1 and answer["audit_ids"] == []
    assert answer["settings"]["review_count"]["value"] == 2 and answer["settings"]["review_count"]["stored"] is True
    assert answer["settings"]["time_limit"]["value"] == 60
    values, slots, new_version = _snapshot(mine)
    assert values == {**values_before, "review_count": 2, "time_limit": 60} and new_version == version + 1
    assert slots == slots_before and _audit(mine) == []                       # the roles did not move
    assert _snapshot(sibling) == sibling_before
    assert env["events"], "the change announces a canvas change"
    rows = {r["field"]: r for r in _setting_audit(mine)}
    assert set(rows) == {"review_count", "time_limit"}
    assert set(answer["setting_audit_ids"]) == {r["audit_id"] for r in rows.values()}
    rc = rows["review_count"]
    assert (rc["token_id"], rc["ai_run_id"], rc["chat_doc_id"], rc["actor_user_id"], rc["node_key"]) == \
        ("tok_1", "aiv_1", CH1, "u_kim", mine)
    assert rc["before"] == {"value": values_before.get("review_count", 0), "stored": "review_count" in values_before}
    assert rc["after"] == {"value": 2, "stored": True} and rc["changed"] is True
    assert rc["settings_version"] == version + 1

    # the same value again: nothing changes, still audited; null takes the default back (no stored row)
    again = card_roles.change(_caller(), mine, {"settings": {"review_count": 2}, "expected_settings_version": version + 1})
    assert again["changed_settings"] == [] and again["settings_version"] == version + 1
    back = card_roles.change(_caller(), mine, {"settings": {"time_limit": None}, "expected_settings_version": version + 1})
    assert back["changed_settings"] == ["time_limit"] and "time_limit" not in repository.values(FA, mine)
    assert back["settings"]["time_limit"] == {**back["settings"]["time_limit"], "value": 240, "stored": False}
    assert [r["changed"] for r in _setting_audit(mine) if r["field"] == "review_count"] == [True, False]


def test_roles_and_settings_in_one_request_save_once(env, decided, guard):
    from flowgate.chat_control import card_roles

    key = _place([{"label": "a"}])["cards"][0]["node_key"]
    version = _snapshot(key)[2]
    answer = card_roles.change(_caller(), key, {"roles": {"execution": EXEC_CARD}, "settings": {"review_count": 1},
                                                "expected_settings_version": version})
    assert answer["changed_roles"] == ["execution"] and answer["changed_settings"] == ["review_count"]
    assert answer["settings_version"] == version + 1                         # one version move for both
    assert answer["roles"]["execution"]["selected"] == EXEC_CARD
    assert [r["role"] for r in _audit(key)] == ["execution"]
    assert [r["field"] for r in _setting_audit(key)] == ["review_count"]


@pytest.mark.parametrize("settings, field, reason", [
    ({"review_count": -2}, "settings.review_count", "not_a_choice"),
    ({"time_limit": 61}, "settings.time_limit", "not_a_choice"),
    ({"restart": 999999}, "settings.restart", "not_a_choice"),
])
def test_a_value_the_screen_cannot_pick_is_refused_and_rolls_the_roles_back(env, decided, guard, settings, field, reason):
    from flowgate.chat_control import card_roles

    key = _place([{"label": "a"}])["cards"][0]["node_key"]
    before = _snapshot(key)
    err = _refused(lambda: card_roles.change(_caller(), key, {"roles": {"execution": EXEC_CARD}, "settings": settings,
                                                              "expected_settings_version": before[2]}),
                   422, "invalid_request")
    assert {"field": field, "reason": reason}.items() <= err.params["errors"][0].items(), err.params
    assert isinstance(err.params["errors"][0]["choices"], list)
    assert _snapshot(key) == before and _audit() == [] and _setting_audit() == []


def test_a_setting_the_card_does_not_have_is_refused(env, decided, guard):
    from flowgate.chat_control import card_roles

    key = _place([{"label": "t", "doc_type": "TS"}])["cards"][0]["node_key"]
    before = _snapshot(key)
    err = _refused(lambda: card_roles.change(_caller(), key, {"settings": {"review_count": 1, "restart": 1},
                                                              "expected_settings_version": before[2]}),
                   422, "invalid_request")
    assert err.params["errors"] == [{"field": "settings.restart", "reason": "setting_not_applicable",
                                     "card_kind": "test", "applicable_settings": ["review_count"]}]
    assert _snapshot(key) == before and _setting_audit() == []
    ok = card_roles.change(_caller(), key, {"settings": {"review_count": 1}, "expected_settings_version": before[2]})
    assert ok["changed_settings"] == ["review_count"]


@pytest.mark.parametrize("body, field, reason", [
    ({"settings": {"hold": True}, "expected_settings_version": 0}, "settings.hold", "not_a_chat_setting"),
    ({"settings": {"review_basis": "last"}, "expected_settings_version": 0}, "settings.review_basis", "not_a_chat_setting"),
    ({"settings": {"review_count": "2"}, "expected_settings_version": 0}, "settings.review_count", "not_an_integer"),
    ({"settings": {"review_count": True}, "expected_settings_version": 0}, "settings.review_count", "not_an_integer"),
    ({"settings": [], "expected_settings_version": 0}, "settings", "not_an_object"),
    ({"settings": {}, "expected_settings_version": 0}, "settings", "required_non_empty_object"),
])
def test_only_the_three_run_settings_are_accepted(body, field, reason):
    from flowgate.chat_control import card_roles

    err = _refused(lambda: card_roles.parse_request(body), 422, "invalid_request")
    assert {"field": field, "reason": reason}.items() <= err.params["errors"][0].items(), err.params


def test_settings_alone_need_no_roles_and_a_running_card_or_stale_version_is_refused(env, decided, guard, monkeypatch):
    from flowgate.card_settings import settings
    from flowgate.chat_control import card_roles

    roles, chosen, version, _ctx = card_roles.parse_request({"settings": {"restart": 0}, "expected_settings_version": 3})
    assert (roles, chosen, version) == ({}, {"restart": 0}, 3)
    key = _place([{"label": "a"}])["cards"][0]["node_key"]
    before = _snapshot(key)
    _refused(lambda: card_roles.change(_caller(), key, {"settings": {"review_count": 1},
                                                        "expected_settings_version": before[2] + 1}),
             409, "gate_context_changed")
    monkeypatch.setattr(settings.legacy, "node_running", lambda *a: True)
    _refused(lambda: card_roles.change(_caller(), key, {"settings": {"review_count": 1},
                                                        "expected_settings_version": before[2]}),
             409, "node_settings_locked")
    assert _snapshot(key) == before and _setting_audit() == []


def test_without_migration_191_a_settings_change_is_409_and_roles_still_work(env, decided, guard, monkeypatch):
    from flowgate.chat_control import card_role_store, card_roles, run_settings

    key = _place([{"label": "a"}])["cards"][0]["node_key"]
    before = _snapshot(key)
    monkeypatch.setattr(card_role_store, "setting_audit_available", lambda: False)
    _refused(lambda: card_roles.change(_caller(), key, {"settings": {"review_count": 1},
                                                        "expected_settings_version": before[2]}),
             409, run_settings.UNAVAILABLE)
    assert _snapshot(key) == before
    ok = card_roles.change(_caller(), key, {"roles": {"execution": EXEC_CARD}, "expected_settings_version": before[2]})
    assert ok["changed_roles"] == ["execution"] and ok["changed_settings"] == []


# ── placement ────────────────────────────────────────────────────────────────

def test_a_placement_writes_each_cards_settings_and_answers_them(env, decided):
    from flowgate.card_settings import repository

    placed = _place([{"label": "a", "settings": {"review_count": 2}},
                     {"label": "b", "roles": {"execution": EXEC_CARD}, "settings": {"review_count": 1, "restart": 0}},
                     {"label": "t", "doc_type": "TS", "settings": {"review_count": -1}},
                     {"label": "d"}])
    a, b, t, d = placed["cards"]
    assert repository.values(FA, a["node_key"])["review_count"] == 2
    assert a["settings"]["review_count"] == 2 and a["settings"]["time_limit"] == 240
    assert (b["settings"]["review_count"], b["settings"]["restart"]) == (1, 0)
    assert b["roles"]["execution"]["source"] == "chat"
    assert t["settings"] == {"review_count": -1}
    assert "review_count" not in repository.values(FA, d["node_key"]) and set(d["settings"]) == {
        "review_count", "restart", "time_limit"}
    rows = _setting_audit()
    assert sorted((r["node_key"], r["field"]) for r in rows) == sorted([
        (a["node_key"], "review_count"), (b["node_key"], "review_count"), (b["node_key"], "restart"),
        (t["node_key"], "review_count")])
    assert all(r["chat_doc_id"] == CH1 and r["changed"] is True for r in rows)
    assert "review count" in placed["next"]


@pytest.mark.parametrize("card, field, reason", [
    ({"label": "a", "settings": {"time_limit": 61}}, "cards[0].settings.time_limit", "not_a_choice"),
    ({"label": "t", "doc_type": "TS", "settings": {"time_limit": 60}}, "cards[0].settings.time_limit",
     "setting_not_applicable"),
    ({"label": "a", "settings": {"hold": True}}, "cards[0].settings.hold", "not_a_chat_setting"),
])
def test_a_placement_with_a_setting_the_card_cannot_take_places_nothing(env, decided, card, field, reason):
    from flowgate.chat_control import card_role_store
    from modules.flow_gate.graph_workflow.store import definition_store

    before = definition_store.draft(FA)
    err = _refused(lambda: _place([card]), 422, "invalid_request")
    assert {"field": field, "reason": reason}.items() <= err.params["errors"][0].items(), err.params
    assert definition_store.draft(FA) == before and card_role_store.placed_keys(FA, CH1) == []
    assert _setting_audit() == []


def test_a_settings_refusal_inside_the_guard_rolls_the_whole_placement_back(env, decided, monkeypatch):
    from flowgate.chat_control import card_role_store, run_settings, service
    from modules.flow_gate.graph_workflow.store import definition_store

    _place([{"label": "first"}])
    before = definition_store.draft(FA)
    kept = card_role_store.placed_keys(FA, CH1)
    monkeypatch.setattr(service, "_precheck_settings", lambda cards: None)          # the choices changed meanwhile
    real = run_settings.check
    monkeypatch.setattr(run_settings, "check", lambda kind, req, field, choices=None: real(kind, req, field, (0,)))
    err = _refused(lambda: _place([{"label": "a", "roles": {"execution": EXEC_CARD}, "settings": {"review_count": 2}}]),
                   422, "invalid_request")
    assert err.params["errors"][0]["reason"] == "not_a_choice"
    assert definition_store.draft(FA) == before and card_role_store.placed_keys(FA, CH1) == kept
    assert _audit() == [] and _setting_audit() == []


# ── transports ───────────────────────────────────────────────────────────────

def test_cli_route_changes_settings_and_a_reading_mode_chat_is_refused(env, decided, chat_auth, client):
    key = _place([{"label": "a"}])["cards"][0]["node_key"]
    url = f"/api/v2/chat/flows/{FA}/nodes/{key}/roles"
    read = client.get(url, headers={"Authorization": "Bearer TOKEN"}).json()
    res = client.patch(url, headers={"Authorization": "Bearer TOKEN"},
                       json={"settings": {"review_count": 2}, "expected_settings_version": read["settings_version"],
                             "expected_context": read["context"]})
    assert res.status_code == 200, res.text
    assert res.json()["changed_settings"] == ["review_count"]
    again = client.get(url, headers={"Authorization": "Bearer TOKEN"}).json()
    assert again["settings"]["review_count"]["value"] == 2
    chat_auth["kind"] = "read"
    res = client.patch(url, headers={"Authorization": "Bearer TOKEN"},
                       json={"settings": {"review_count": 1}, "expected_settings_version": again["settings_version"]})
    assert res.status_code == 403 and res.json()["error"]["code"] == "chat_edit_mode_required"
    assert len(_setting_audit(key)) == 1


def test_tools_and_cli_mention_offer_the_run_settings(monkeypatch):
    from flowgate.chat_control import authz, card_roles, tools

    by_name = {t["name"]: t for t in tools.descriptors()}
    item = by_name["place_flow_cards"]["schema"]["properties"]["cards"]["items"]["properties"]
    assert set(item["settings"]["properties"]) == {"review_count", "restart", "time_limit"}
    assert item["settings"]["additionalProperties"] is False
    assert set(by_name["set_card_roles"]["schema"]["properties"]["settings"]["properties"]) == {
        "review_count", "restart", "time_limit"}
    assert "review_count" in by_name["set_card_roles"]["description"]
    assert "review count" in by_name["get_card_roles"]["description"]
    monkeypatch.setattr(authz, "caller_from_run", lambda run, flow_id, write: "CALLER")
    monkeypatch.setattr(card_roles, "change", lambda caller, key, body: {"changed": key, "body": body})
    status, body = tools.set_roles_call({"group_id": FA}, {"node_key": "gn_1", "settings": {"review_count": 2},
                                                           "expected_settings_version": 3})
    assert status == 200 and body["body"] == {"settings": {"review_count": 2}, "expected_settings_version": 3}
    text = tools.cli_mention("http://h/flowgate/api/v1", "TOKEN")
    for word in ('"settings": {"review_count": 1}', '{"settings": {"review_count": 2}', "setting_not_applicable",
                 "Decide each card's review count yourself", "review count with a short reason"):
        assert word in text, word
    assert "review count" in tools.PROMPT_HINT and "settings" in tools.PLACE_DESCRIPTION
