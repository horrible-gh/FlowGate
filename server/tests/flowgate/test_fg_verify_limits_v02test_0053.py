"""flowgate.v02test.0053 0003-T (1-A): the card's two 검증 options and the one function that resolves a card's limits.

On the card settings harness (real SQLite DB with every migration, real Graph draft saves, the card settings routes):

- the two options exist on a run card only, default to "the system default" (None), offer their presets as
  ``choices`` and take any whole number inside their range (counts 1..999, minutes 1..30);
- ``resolve_verify_limits(flow_id, node_key)``: the card's value, else the system default; the 1800-second ceiling; a
  test card (TS) is never read, whatever a row says;
- migration 201: the chat's settings audit accepts the two new fields (and still refuses an unknown one).
"""
from __future__ import annotations

import sqlite3

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, FB, _sql, db_path, decided, env, get_settings, legacy_cards, node, placed, put_draft, setting, stored_rows,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")      # the harness's pre-AI-role cards, as the sibling suite uses

RUNS, MINUTES = "verify_max_runs", "verify_run_minutes"


def _system(runs=None, timeout=None):
    from modules.flow_gate.db import system_settings as db

    if runs is not None:
        db.set_value("verify_max_runs", str(runs), "integer")
    if timeout is not None:
        db.set_value("verify_run_timeout_sec", str(timeout), "integer")


def _limits(flow, key):
    from flowgate.card_settings import verify_limits

    got = verify_limits.resolve_verify_limits(flow, key)
    return got.max_runs, got.run_timeout_sec


def _put(flow, key, field, value):
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard

    with_flow_guard(flow, lambda: repository.put(flow, key, field, value, "u_kim"))


# ── the fields (pure) ────────────────────────────────────────────────────────

def test_only_a_run_card_has_the_two_verify_options():
    from flowgate.card_settings import fields as f

    names = [x.name for x in f.fields_of(f.RUN)]
    assert RUNS in names and MINUTES in names and names.index(RUNS) < names.index(MINUTES)
    for kind in (f.TEST, f.PLAN, f.CHECK, f.REQUIREMENT):
        assert RUNS not in [x.name for x in f.fields_of(kind)] and MINUTES not in [x.name for x in f.fields_of(kind)]
    runs, minutes = f.field_of(f.RUN, RUNS), f.field_of(f.RUN, MINUTES)
    assert (runs.default, minutes.default) == (None, None)                 # no value = the system default
    assert (f.choices_of(runs, ()), f.choices_of(minutes, ())) == ([3, 5, 10], [3, 5, 10, 20, 30])


@pytest.mark.parametrize("field, value", [
    (RUNS, 1), (RUNS, 3), (RUNS, 7), (RUNS, 999), (RUNS, 1000), (RUNS, 123456), (MINUTES, 1), (MINUTES, 3), (MINUTES, 17), (MINUTES, 30),
])
def test_a_whole_number_in_range_is_a_value_presets_or_custom(field, value):
    from flowgate.card_settings import fields as f

    spec = f.field_of(f.RUN, field)
    assert f.check_value(spec, value, repeat_choices=(), team_exists=lambda _: False, ai_exists=lambda _: False) == value


@pytest.mark.parametrize("field, value", [
    (RUNS, 0), (RUNS, -1), (RUNS, "3"), (RUNS, 3.0), (RUNS, True),
    (MINUTES, 0), (MINUTES, 31), (MINUTES, -3), (MINUTES, "5"), (MINUTES, 5.5), (MINUTES, False),
])
def test_anything_else_is_out_of_range(field, value):
    from flowgate.card_settings import fields as f

    spec = f.field_of(f.RUN, field)
    with pytest.raises(f.InvalidValue) as caught:
        f.check_value(spec, value, repeat_choices=(), team_exists=lambda _: False, ai_exists=lambda _: False)
    assert caught.value.reason == "out_of_range"


def test_the_run_plan_does_not_turn_the_verify_options_into_an_execution_budget():
    from flowgate.card_settings import fields as f

    assert f.run_plan(f.RUN, {}) == {"settings": {}, "step_timeout_sec": 14400, "restart_max": 3}
    plan = f.run_plan(f.RUN, {RUNS: 5, MINUTES: 10})
    assert plan["step_timeout_sec"] == 14400 and plan["restart_max"] == 3      # the run's own limits are not the verify's


# ── the API: read / write one value at a time ────────────────────────────────

def test_a_run_card_reads_the_options_unset_with_their_presets_and_saves_each_at_once(env):
    key = placed(env, FB, "T")
    body = get_settings(env, FB, key)
    assert body["fields"][RUNS] == {"value": None, "stored": False, "default": None, "choices": [3, 5, 10]}
    assert body["fields"][MINUTES] == {"value": None, "stored": False, "default": None, "choices": [3, 5, 10, 20, 30]}
    version = body["settings_version"]

    one = setting(env, FB, key, RUNS, 5)
    assert one["fields"][RUNS]["value"] == 5 and one["fields"][RUNS]["stored"] is True
    assert one["settings_version"] == version + 1
    custom = setting(env, FB, key, MINUTES, 17)                                # a custom value outside the presets
    assert custom["fields"][MINUTES]["value"] == 17
    assert stored_rows(env, FB, key) == {(key, MINUTES): "17", (key, RUNS): "5"}
    back = setting(env, FB, key, RUNS, None)                                   # blank = the system default again
    assert back["fields"][RUNS]["stored"] is False and back["fields"][RUNS]["value"] is None
    assert stored_rows(env, FB, key) == {(key, MINUTES): "17"}


@pytest.mark.parametrize("field, value", [(RUNS, 0), (RUNS, -2), (RUNS, "3"), (RUNS, True),
                                          (MINUTES, 0), (MINUTES, 31), (MINUTES, 2.5), (MINUTES, "10")])
def test_a_value_out_of_range_is_refused_and_writes_nothing(env, field, value):
    key = placed(env, FB, "T")
    before = get_settings(env, FB, key)
    refused = setting(env, FB, key, field, value, expect=422)
    assert refused["error"]["code"] == "invalid_request"
    assert refused["error"]["params"]["errors"] == [{"field": "value", "reason": "out_of_range"}]
    assert stored_rows(env, FB, key) == {} and get_settings(env, FB, key)["settings_version"] == before["settings_version"]


def test_a_test_card_and_a_gate_have_no_such_setting(env):
    first = put_draft(env, FB, 0, [node("tmp_ts", "TS"), node("tmp_g", None, "gate")])
    for key in first["key_map"].values():
        for field in (RUNS, MINUTES):
            refused = setting(env, FB, key, field, 5, expect=422)
            assert refused["error"]["params"]["errors"][0]["reason"] == "not_a_setting_of_this_card"
        assert RUNS not in get_settings(env, FB, key)["fields"]
    assert stored_rows(env, FB) == {}


# ── resolve_verify_limits ────────────────────────────────────────────────────

def test_the_card_value_wins_else_the_system_default(env):
    key = placed(env, FB, "T")
    assert _limits(FB, key) == (3, 180)                                         # shipped defaults: 3 runs, 180 seconds
    _system(runs=7, timeout=300)
    assert _limits(FB, key) == (7, 300)                                         # the system default moves every unset card
    setting(env, FB, key, RUNS, 5)
    assert _limits(FB, key) == (5, 300)                                         # only the count is the card's
    setting(env, FB, key, MINUTES, 10)
    assert _limits(FB, key) == (5, 600)
    setting(env, FB, key, RUNS, None)
    setting(env, FB, key, MINUTES, None)
    assert _limits(FB, key) == (7, 300)                                         # blank = the system default again
    from flowgate.card_settings import verify_limits
    assert verify_limits.resolve_verify_limits(FB, key) == verify_limits.VerifyLimits(max_runs=7, run_timeout_sec=300)


def test_one_verification_never_exceeds_1800_seconds(env):
    first = put_draft(env, FB, 0, [node("tmp_a", "T"), node("tmp_b", "T")])
    a, b = first["key_map"]["tmp_a"], first["key_map"]["tmp_b"]
    setting(env, FB, a, MINUTES, 30)
    assert _limits(FB, a)[1] == 1800                                            # the card's largest pick is the ceiling
    _put(FB, a, MINUTES, 31)                                                    # a hand-edited row above it is not read
    assert _limits(FB, a)[1] == 180
    _system(timeout=1800)
    assert _limits(FB, b)[1] == 1800                                            # the system default may be the ceiling too
    from modules.flow_gate.db import system_settings as db
    db.set_value("verify_run_timeout_sec", "99999", "integer")                  # a corrupt system value: the shipped default
    assert _limits(FB, b)[1] == 180


def test_a_value_that_is_not_valid_is_not_set(env):
    key = placed(env, FB, "T")
    for field, bad in ((RUNS, 0), (RUNS, -1), (RUNS, "5"), (RUNS, True), (RUNS, None),
                       (MINUTES, 0), (MINUTES, 31), (MINUTES, "10"), (MINUTES, 2.5), (MINUTES, False)):
        _put(FB, key, field, bad)
    assert _limits(FB, key) == (3, 180)
    _put(FB, key, RUNS, 12)
    assert _limits(FB, key) == (12, 180)                                        # the valid one still counts


def test_a_count_above_999_is_a_value_not_a_fallback(env):
    key = placed(env, FB, "T")
    assert setting(env, FB, key, RUNS, 1000)["fields"][RUNS]["value"] == 1000   # 'a whole number of 1 or more'
    assert _limits(FB, key) == (1000, 180)                                      # and the resolver answers it as it is
    _put(FB, key, RUNS, 100000)
    assert _limits(FB, key) == (100000, 180)


def test_a_test_card_is_never_read_and_an_unknown_card_has_the_system_default(env):
    first = put_draft(env, FB, 0, [node("tmp_ts", "TS"), node("tmp_g", None, "gate"), node("tmp_x", None, "custom")])
    ts, gate, custom = (first["key_map"][k] for k in ("tmp_ts", "tmp_g", "tmp_x"))
    _system(runs=4, timeout=240)
    for key in (ts, gate):                                                      # rows a TS / gate card cannot have by the API
        _put(FB, key, RUNS, 9)
        _put(FB, key, MINUTES, 20)
        assert _limits(FB, key) == (4, 240)
    assert _limits(FB, "no_such_card") == (4, 240) and _limits("no.such.flow", ts) == (4, 240)
    setting(env, FB, custom, RUNS, 2)                                           # a palette [+] card works like a work card
    assert _limits(FB, custom) == (2, 240)


def test_the_card_value_is_per_flow_and_per_card(env):
    a = placed(env, FA, "T")
    b = placed(env, FB, "T")
    setting(env, FA, a, RUNS, 8)
    assert _limits(FA, a) == (8, 180) and _limits(FB, b) == (3, 180)
    assert _limits(FB, a) == (3, 180)                                           # the same key in another flow is another card


# ── migration 201: the chat's settings audit ─────────────────────────────────

def _audit_insert(db, field, audit_id):
    _sql(db, "INSERT INTO chat_card_setting_audit (audit_id, group_id, node_key, field, before_json, after_json, changed,"
             " settings_version, token_id, ai_run_id, chat_doc_id, actor_user_id, created_at)"
             " VALUES (?, ?, 'n1', ?, NULL, '{}', 1, 1, 't', 'r', 'ch', 'u_kim', 'now')", (audit_id, FA, field))


def test_the_chat_settings_audit_accepts_the_two_verify_fields_and_keeps_its_check(env):
    for i, field in enumerate(("review_count", "restart", "time_limit", RUNS, MINUTES)):
        _audit_insert(env["db"], field, f"a{i}")
    assert [r["field"] for r in _sql(env["db"], "SELECT field FROM chat_card_setting_audit ORDER BY audit_id")] == [
        "review_count", "restart", "time_limit", RUNS, MINUTES]
    with pytest.raises(sqlite3.IntegrityError):
        _audit_insert(env["db"], "hold", "a9")
    triggers = _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_chat_card_setting_audit%'")
    assert [t["name"] for t in triggers] == ["trg_chat_card_setting_audit_group_deleted"]
    indexes = _sql(env["db"], "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_chat_card_setting_audit_card'")
    assert len(indexes) == 1
    _sql(env["db"], "DELETE FROM groups WHERE group_id = ?", (FA,))               # the trigger still cleans the audit up
    assert _sql(env["db"], "SELECT COUNT(*) AS n FROM chat_card_setting_audit")[0]["n"] == 0
