"""flowgate.v02.0113 T#2 rework (0030-M §2, 0002-D 개정 §3.1-6 · §3.2 · §3.2-A · §3.3-6 · §3.4) — the first AI setup saved
and applied to the current Flow's AI-settings cards in one transaction (``POST /flows/{flow_id}/ai-roles/first-setup``).

- Success: the account's three values and its setup completed marker; every run / test / WP card of the Flow's current
  definition takes the values with the marker source ``first_setup`` (a WP card: execution and review only), its init
  row ``initialized``; another Flow, the requirement / gate cards and every non-AI field stay as they were.
- All three ``not_used``; a card actually running refuses the whole request with nothing written (account included);
  a failed write rolls everything back; a completed account applied to another Flow; the same request id applied once;
  two requests of one account serialized; ``PUT /me/ai-roles`` changes no existing card (new cards copy it).
- Resolution: a ``first_setup`` slot is the role's explicit value (L-1 1st priority); the legacy fields and the pending
  fill no longer decide it. Migration 166 in three dialects.

On the real SQLite DB and routes of ``test_fg_card_settings`` with the stage assignment harness of
``test_fg_ai_roles_l2_assignment`` (the AI start and the review loop admission are recorders: nothing calls an AI).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi import Request

from test_fg_ai_roles_l2_assignment import (  # noqa: F401 — fixtures used by name
    FA, X, _sql, db_path, decided, env, flow, ledger, roles, start, starts, wired,
)
from test_fg_card_settings import FB, _h, node, put_draft, setting, stored_rows
from test_fg_card_settings_review import _executions_ended

PC, PA = "aip_card", "aip_assignee"
MIGRATIONS = Path(__file__).resolve().parents[2] / "sql" / "migrations"


@pytest.fixture()
def api(env):
    """The AI role routes as ``main`` mounts them (the account routes' user from the same ``Bearer user:`` header)."""
    from flowgate.ai_roles.routes import router
    from modules.flow_gate.auth.middleware import get_current_user

    app = env["client"].app
    app.include_router(router)

    def current(request: Request):
        raw = request.headers.get("Authorization", "")
        return {"user_id": raw[len("Bearer user:"):] if raw.startswith("Bearer user:") else "u_kim"}

    app.dependency_overrides[get_current_user] = current
    yield env
    app.dependency_overrides.pop(get_current_user, None)


def value(v):
    return {"kind": "not_used"} if v == "not_used" else {"kind": "assigned", "provider_id": v}


def body(i="not_used", e="not_used", r="not_used", request_id="req-1"):
    return {"request_id": request_id, "roles": {"instruction": value(i), "execution": value(e), "review": value(r)}}


def first_setup(env, flow_id, payload, user="u_kim", expect=200):
    res = env["client"].post(f"/api/v1/flows/{flow_id}/ai-roles/first-setup", json=payload,
                             headers={"Authorization": f"Bearer user:{user}"})
    assert res.status_code == expect, res.text
    return res.json()


def state(flow_id, key):
    from flowgate.ai_roles import role_store
    from flowgate.card_settings import repository

    return role_store.slots(flow_id, key), role_store.card_init(flow_id, key), repository.settings_version(flow_id, key)


def account():
    from flowgate.ai_roles import role_store

    return role_store.account_roles("u_kim")


def requests_recorded(env):
    return _sql(env["db"], "SELECT user_id, request_id, flow_id FROM ai_role_setup_requests ORDER BY created_at")


def slot_values(flow_id, key):
    return {role: (s.value.kind, s.value.provider_id, s.source, s.set_by) for role, s in state(flow_id, key)[0].items()}


FIVE = {"tmp_t": ("T", "document"), "tmp_ts": ("TS", "document"), "tmp_wp": ("WP", "document"),
        "tmp_r": ("R", "document"), "tmp_g": (None, "gate")}


def five_cards(env, flow_id=FA):
    """run (T), test (TS), WP, requirement (R) and gate cards in a draft of ``flow_id``; returns ref -> node key."""
    return put_draft(env, flow_id, 0, [node(ref, *kind) for ref, kind in FIVE.items()])["key_map"]


def saved_again(env, km, version, *extra):
    """The draft of the five cards saved again (their keys), with ``extra`` new nodes."""
    return put_draft(env, FA, version, [node(km[ref], *kind) for ref, kind in FIVE.items()] + list(extra))["key_map"]


# ── success ──────────────────────────────────────────────────────────────────

def test_the_setup_saves_the_account_and_unifies_every_ai_card_of_the_flow_only(api):
    from flowgate.ai_roles import role_store

    env = api
    assert account() is None
    km = five_cards(env)
    other = put_draft(env, FB, 0, [node("tmp_o", "T")])["key_map"]["tmp_o"]
    t, ts, wp = km["tmp_t"], km["tmp_ts"], km["tmp_wp"]
    # an unset account: the cards wait for their init (no slot); each gets its own value first
    assert state(FA, t)[1].state == "pending_init" and state(FA, t)[0] == {}
    setting(env, FA, t, "execution_role", value(PA))
    setting(env, FA, ts, "review_role", value("not_used"))
    setting(env, FA, wp, "execution_role", value(PC))
    setting(env, FB, other, "execution_role", value(PA))
    setting(env, FA, t, "restart", 1)
    setting(env, FA, t, "review_count", 1)
    settings_before, other_before = stored_rows(env, FA), state(FB, other)
    versions = {k: state(FA, k)[2] for k in (t, ts, wp)}
    plain = {k: state(FA, k) for k in (km["tmp_r"], km["tmp_g"])}

    answer = first_setup(env, FA, body(PC, PC, "not_used"))

    assert answer["ok"] and answer["replayed"] is False and answer["flow_id"] == FA
    assert [(c["node_key"], c["roles"]) for c in answer["cards"]] == [
        (t, ["instruction", "execution", "review"]), (ts, ["instruction", "execution", "review"]), (wp, ["execution", "review"])]
    saved = account()
    assert saved is not None and saved.setup_completed_at
    assert {r: (v.kind, v.provider_id) for r, v in saved.values.items()} == {
        "instruction": ("assigned", PC), "execution": ("assigned", PC), "review": ("not_used", None)}
    assert answer["account"]["setup_completed"] is True
    for key in (t, ts):
        assert slot_values(FA, key) == {"instruction": ("assigned", PC, "first_setup", "u_kim"),
                                         "execution": ("assigned", PC, "first_setup", "u_kim"),
                                         "review": ("not_used", None, "first_setup", "u_kim")}
    assert slot_values(FA, wp) == {"execution": ("assigned", PC, "first_setup", "u_kim"),
                                   "review": ("not_used", None, "first_setup", "u_kim")}          # no instruction role
    for key in (t, ts, wp):
        slots, init, version = state(FA, key)
        assert init.state == role_store.INITIALIZED and version == versions[key] + 1
    assert {k: state(FA, k) for k in plain} == plain                         # R and gate: no roles, untouched
    assert state(FB, other) == other_before                                  # another Flow: untouched
    assert stored_rows(env, FA) == settings_before                           # no non-AI field written
    assert requests_recorded(env) == [{"user_id": "u_kim", "request_id": "req-1", "flow_id": FA}]


def test_all_three_not_used_is_a_setup_too_and_resolves_to_a_person(api):
    from flowgate.ai_roles import resolve
    from flowgate.card_settings import repository

    env = api
    km = five_cards(env)
    first_setup(env, FA, body())
    assert all(v.kind == "not_used" for v in account().values.values())
    for key in (km["tmp_t"], km["tmp_ts"], km["tmp_wp"]):
        assert {s.value.kind for s in state(FA, key)[0].values()} == {"not_used"}
        result = resolve.resolve_role(FA, repository.card_node(FA, key), "execution", project_id="flowgate")
        assert (result.outcome, result.source) == ("person", "first_setup")


def test_a_first_setup_slot_is_the_explicit_value_over_legacy_fields_and_the_pending_fill(api):
    from flowgate.ai_roles import card_init, resolve
    from flowgate.card_settings import fields, repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    env = api
    km = five_cards(env)
    pending, legacy_key = km["tmp_t"], km["tmp_ts"]
    # a card made before the role feature: no init row, an old run AI in its stored fields
    _sql(env["db"], "DELETE FROM graph_node_role_init WHERE group_id = ? AND node_key = ?", (FA, legacy_key))
    with_flow_guard(FA, lambda: repository.put(FA, legacy_key, "run_ai", PA, "u_kim"))
    before = resolve.resolve_role(FA, repository.card_node(FA, legacy_key), "execution", project_id="flowgate")
    assert (before.source, before.provider_id) == ("legacy_fields", PA)
    first_setup(env, FA, body("not_used", PC, "not_used"))
    after = resolve.resolve_role(FA, repository.card_node(FA, legacy_key), "execution", project_id="flowgate")
    assert (after.outcome, after.source, after.provider_id, after.legacy_flag) == ("ai", "first_setup", PC, False)
    assert repository.values(FA, legacy_key)["run_ai"] == PA                # the legacy field is kept, not read
    assert state(FA, legacy_key)[1].state == "initialized"
    # the pending fill has nothing left to do: the account changed afterwards does not refill the card
    from flowgate.ai_roles import role_store
    role_store.save_account_roles("u_kim", {r: role_store.RoleValue("assigned", PA, "x") for r in role_store.ROLES})
    done = state(FA, pending)
    assert with_flow_guard(FA, lambda: card_init.initialize_pending_card(FA, pending, fields.RUN)) == "NO_CHANGE"
    assert state(FA, pending) == done


# ── refusal and rollback ─────────────────────────────────────────────────────

def test_a_card_actually_running_refuses_the_whole_request_and_writes_nothing(api, decided, starts):
    from flowgate.card_settings import legacy_adapter

    env = api
    key = flow(env)
    roles(key, X, "not_used", "not_used")                                  # its instruction AI starts at once
    start(env)
    [row] = ledger(env)
    assert row["status"] == "requested" and legacy_adapter.node_running(FA, key) is True
    before = state(FA, key)

    refused = first_setup(env, FA, body(PC, PC, PC), expect=409)

    assert refused["error"]["code"] == "ai_role_setup_cards_running"
    assert refused["error"]["params"]["cards"] == [{"node_key": key, "label": "T"}]
    assert account() is None                                               # the account save rolled back too
    assert state(FA, key) == before and requests_recorded(env) == []
    # the execution over, the same request goes through (a refusal records nothing)
    _executions_ended()
    assert legacy_adapter.node_running(FA, key) is False
    applied = first_setup(env, FA, body(PC, PC, PC))
    assert applied["replayed"] is False and [c["node_key"] for c in applied["cards"]] == [key]
    assert {s.source for s in state(FA, key)[0].values()} == {"first_setup"}


@pytest.mark.parametrize("fail_at", ["second_slot", "request_record"])
def test_a_failed_write_rolls_back_the_account_and_every_card(api, monkeypatch, fail_at):
    from flowgate.ai_roles import role_store

    env = api
    km = five_cards(env)
    keys = (km["tmp_t"], km["tmp_ts"], km["tmp_wp"])
    before = {k: state(FA, k) for k in keys}
    if fail_at == "second_slot":
        original, calls = role_store.write_slot, []

        def write_slot(*args):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("slot failed")
            return original(*args)

        monkeypatch.setattr(role_store, "write_slot", write_slot)
    else:
        def record(*args, **kwargs):
            raise RuntimeError("record failed")

        monkeypatch.setattr(role_store, "record_setup_request", record)
    failed = first_setup(env, FA, body(PC, PC, PC), expect=500)
    assert failed["error"]["code"] == "internal_error"
    assert account() is None and {k: state(FA, k) for k in keys} == before and requests_recorded(env) == []


def test_bad_requests_are_refused_before_anything_is_written(api, monkeypatch):
    from flowgate.ai_roles import role_store

    env = api
    km = five_cards(env)
    before = state(FA, km["tmp_t"])
    for payload, fields_ in [
        ({"roles": body()["roles"]}, ["request_id"]),
        ({**body(), "flow_id": FB}, ["flow_id"]),
        ({**body(), "request_id": "bad id with spaces"}, ["request_id"]),
        ({"request_id": "r", "roles": {"instruction": value("not_used")}}, ["roles.execution", "roles.review"]),
        (body(PC, "unknown_ai"), ["roles.execution"]),
    ]:
        refused = first_setup(env, FA, payload, expect=422)
        assert refused["error"]["code"] == "validation_failed"
        assert [e["field"] for e in refused["error"]["params"]["errors"]] == fields_
    assert first_setup(env, "flowgate.v02.0999", body(), expect=404)["error"]["code"] == "flow_not_found"
    monkeypatch.setattr(role_store, "setup_requests_available", lambda: False)        # migration 166 not applied
    assert first_setup(env, FA, body(), expect=409)["error"]["code"] == "ai_role_setup_unavailable"
    assert account() is None and state(FA, km["tmp_t"]) == before and requests_recorded(env) == []


# ── concurrency, replay and later changes ────────────────────────────────────

def test_a_completed_account_applies_its_new_values_to_another_flow_and_keeps_the_first_flow(api):
    env = api
    a = put_draft(env, FA, 0, [node("tmp_a", "T")])["key_map"]["tmp_a"]
    b = put_draft(env, FB, 0, [node("tmp_b", "T")])["key_map"]["tmp_b"]
    first_setup(env, FA, body(PC, PC, PC, "tab-1"))
    first_a = state(FA, a)
    # another tab, set up already: neither refused nor skipped, its own values overwrite the account and go to its Flow
    answer = first_setup(env, FB, body(PA, "not_used", PA, "tab-2"))
    assert answer["replayed"] is False and [c["node_key"] for c in answer["cards"]] == [b]
    assert {r: v.provider_id for r, v in account().values.items()} == {"instruction": PA, "execution": None, "review": PA}
    assert slot_values(FB, b)["instruction"] == ("assigned", PA, "first_setup", "u_kim")
    assert state(FA, a) == first_a                                           # the first tab's Flow keeps its values


def test_the_same_request_id_is_applied_once_and_answers_its_first_result(api):
    env = api
    km = five_cards(env)
    t = km["tmp_t"]
    first = first_setup(env, FA, body(PC, PC, PC, "same"))
    applied = state(FA, t)
    setting(env, FA, t, "execution_role", value(PA))                      # the card changed afterwards
    changed = state(FA, t)
    again = first_setup(env, FA, body("not_used", "not_used", "not_used", "same"))
    assert again["replayed"] is True
    assert {k: again[k] for k in ("flow_id", "request_id", "roles", "cards")} == \
        {k: first[k] for k in ("flow_id", "request_id", "roles", "cards")}
    assert state(FA, t) == changed != applied                               # nothing written again
    assert all(v.provider_id == PC for v in account().values.values())
    assert len(requests_recorded(env)) == 1


def test_two_requests_of_one_account_are_serialized_in_the_server(api, monkeypatch):
    from flowgate.ai_roles import first_setup as service
    from modules.flow_gate.db import groups

    env = api
    a = put_draft(env, FA, 0, [node("tmp_a", "T")])["key_map"]["tmp_a"]
    b = put_draft(env, FB, 0, [node("tmp_b", "T")])["key_map"]["tmp_b"]
    trace, original = [], service._apply

    def traced(flow_id, *args):
        trace.append(("enter", flow_id))
        time.sleep(0.3)
        try:
            return original(flow_id, *args)
        finally:
            trace.append(("exit", flow_id))

    monkeypatch.setattr(service, "_apply", traced)
    results, errors = {}, []

    def send(flow_id, payload):
        try:
            results[flow_id] = service.apply(groups.get_by_id(flow_id), "u_kim", payload)
        except Exception as exc:  # noqa: BLE001 — reported below
            errors.append(exc)

    threads = [threading.Thread(target=send, args=(FA, body(PC, PC, PC, "t-a"))),
               threading.Thread(target=send, args=(FB, body(PA, PA, PA, "t-b")))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == [] and set(results) == {FA, FB}
    assert [step for step, _ in trace] == ["enter", "exit", "enter", "exit"]   # one at a time, never interleaved
    assert trace[0][1] == trace[1][1] and trace[2][1] == trace[3][1]
    last = trace[-1][1]
    assert {v.provider_id for v in account().values.values()} == {PC if last == FA else PA}
    assert {s.value.provider_id for s in state(FA, a)[0].values()} == {PC}
    assert {s.value.provider_id for s in state(FB, b)[0].values()} == {PA}


def test_the_personal_settings_put_changes_no_existing_card_and_new_cards_copy_it(api):
    env = api
    km = five_cards(env)
    t = km["tmp_t"]
    first_setup(env, FA, body(PC, PC, PC))
    applied = state(FA, t)
    res = env["client"].put("/api/v1/me/ai-roles", json={"roles": {r: value(PA) for r in ("instruction", "execution", "review")}},
                            headers={"Authorization": "Bearer user:u_kim"})
    assert res.status_code == 200, res.text
    assert state(FA, t) == applied                                          # 7-4: not applied back to existing cards
    created = saved_again(env, km, 1, node("tmp_new", "T"))["tmp_new"]
    assert {(s.value.provider_id, s.source) for s in state(FA, created)[0].values()} == {(PA, "creation_copy")}


def test_a_new_card_saved_after_the_setup_copies_the_saved_values(api):
    env = api
    km = put_draft(env, FA, 0, [node("tmp_a", "T")])["key_map"]
    first_setup(env, FA, body(PC, "not_used", PC))
    new = put_draft(env, FA, 1, [node(km["tmp_a"]), node("tmp_b", "T")])["key_map"]["tmp_b"]
    assert slot_values(FA, new) == {"instruction": ("assigned", PC, "creation_copy", "u_kim"),
                                    "execution": ("not_used", None, "creation_copy", "u_kim"),
                                    "review": ("assigned", PC, "creation_copy", "u_kim")}


# ── migration 166 ────────────────────────────────────────────────────────────

def test_migration_166_widens_the_slot_source_and_adds_the_request_table_in_three_dialects():
    for dialect in ("sqlite", "postgres", "mysql"):
        sql = (MIGRATIONS / dialect / "166_ai_role_first_setup.sql").read_text(encoding="utf-8")
        assert "'first_setup'" in sql and "CREATE TABLE IF NOT EXISTS ai_role_setup_requests" in sql, dialect
        assert "PRIMARY KEY (user_id, request_id)" in sql and "REFERENCES" not in sql.split("ai_role_setup_requests (")[1]
        assert "DELETE FROM graph_node_role_slots WHERE" not in sql.replace("BEGIN DELETE FROM graph_node_role_slots", "")


def test_migration_166_keeps_every_slot_row_and_can_run_again(tmp_path):
    files = sorted((MIGRATIONS / "sqlite").glob("*.sql"))
    conn = sqlite3.connect(str(tmp_path / "m.db"))
    for mig in files:
        if mig.name >= "166":
            break
        conn.executescript(mig.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES ('p', 'p', 't', 't')")
    conn.execute("INSERT INTO groups (group_id, project_id, title, created_at, updated_at) VALUES ('g', 'p', 'g', 't', 't')")
    conn.execute("INSERT INTO graph_node_role_slots VALUES ('g', 'n', 'review', 'not_used', NULL, NULL, 'card_explicit', 'u', 't')")
    conn.commit()
    m166 = (MIGRATIONS / "sqlite" / "166_ai_role_first_setup.sql").read_text(encoding="utf-8")
    conn.executescript(m166)
    conn.executescript(m166)
    assert conn.execute("SELECT * FROM graph_node_role_slots").fetchall() == [
        ("g", "n", "review", "not_used", None, None, "card_explicit", "u", "t")]
    conn.execute("INSERT INTO graph_node_role_slots VALUES ('g', 'n', 'execution', 'not_used', NULL, NULL, 'first_setup', 'u', 't')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO graph_node_role_slots VALUES ('g', 'n', 'instruction', 'not_used', NULL, NULL, 'legacy', 'u', 't')")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("DELETE FROM groups WHERE group_id = 'g'")                  # the cleanup trigger is back
    assert conn.execute("SELECT COUNT(*) FROM graph_node_role_slots").fetchone()[0] == 0
    conn.close()
