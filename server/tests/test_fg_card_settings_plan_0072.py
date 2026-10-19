"""flowgate.v02.0072 T#3 — the work plan (WP) card's settings: the AI pre-instruction writing switch and the run block.

The WP card had no settings (``fields.card_kind`` None). The Inspector's WP settings tab now reads and writes them
through the existing card settings GET / PATCH (one value per change, the same locks and refusals), as values only:

- the switch (``pre_instruction_auto``) is its own card value — stored, read again, back to the default with null —
  and turning it off (or on) touches no document: the plan's Steps keep their pre-instructions and attachments;
- the run block (work AI, restart, review AI, review count, time limit, review basis) is the card's, not the
  plan's Steps' ``provider_id`` / ``reviewer_provider_id``, and no run reads it: ``run_plan`` / ``after_result``
  hand nothing on and ``uses_ai`` stays false for the plan kind (no pre-instruction writer, no new run inheritance).

The fixtures are the card settings suite's (``test_fg_card_settings``: a real SQLite DB with every migration and the
Graph / card settings routes over a TestClient).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from test_fg_card_settings import (  # noqa: F401  (pytest fixtures and helpers of the card settings suite)
    FB,
    _sql,
    db_path,
    env,
    get_settings,
    node,
    put_draft,
    setting,
    shown,
    stored_rows,
)

PLAN_FIELDS = ["pre_instruction_auto", "run_ai", "restart", "review_ai", "review_count", "time_limit",
               "review_basis", "hold"]


def _plan_card(env) -> str:
    first = put_draft(env, FB, 0, [node("tmp_wp", "WP")])
    return first["key_map"]["tmp_wp"]


def _documents(env) -> list[dict]:
    return _sql(env["db"], "SELECT * FROM documents WHERE group_id = ? ORDER BY doc_id", (FB,))


def _files(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


# ── pure ─────────────────────────────────────────────────────────────────────

def test_a_work_plan_card_has_the_plan_settings_and_no_run_takes_them():
    from flowgate.card_settings import fields as f

    assert f.card_kind("document", "WP") == f.PLAN and f.card_kind("document", "wp") == f.PLAN
    assert f.PLAN in f.CARD_KINDS
    assert [x.name for x in f.fields_of(f.PLAN)] == PLAN_FIELDS[:-1]
    assert f.field_of(f.PLAN, "instruction") is None and f.field_of(f.PLAN, "instruction_ai") is None
    switch = f.field_of(f.PLAN, "pre_instruction_auto")
    assert (switch.kind, switch.default) == (f.FLAG, False)
    stored = {"pre_instruction_auto": True, "run_ai": "aip_card", "restart": 1, "review_count": 2,
              "time_limit": 120, "review_basis": "last"}
    # Values only: no run plan, no review step, no instruction stage, no result type.
    assert f.run_plan(f.PLAN, stored, "document", "WP") == {}
    assert f.after_result(f.PLAN, stored) is None
    assert f.instruction_stage("document", "WP", stored) is None
    assert f.result_doc_type("document", "WP", {"instruction": "omit"}) is None


def test_the_switch_takes_a_boolean_only():
    from flowgate.card_settings import fields as f

    switch = f.field_of(f.PLAN, "pre_instruction_auto")
    kw = {"repeat_choices": (-1, 0, 1), "team_exists": lambda _: True, "ai_exists": lambda _: True}
    assert f.check_value(switch, True, **kw) is True and f.check_value(switch, False, **kw) is False
    for bad in ("on", 1, 0, "true"):
        try:
            f.check_value(switch, bad, **kw)
        except f.InvalidValue as exc:
            assert exc.reason == "not_a_boolean"
        else:
            raise AssertionError(bad)


# ── read / write ─────────────────────────────────────────────────────────────

def test_a_work_plan_card_reads_its_defaults_and_saves_each_value_at_once(env):
    key = _plan_card(env)
    body = get_settings(env, FB, key)
    assert (body["card_kind"], body["locked"], list(body["fields"])) == ("plan", False, PLAN_FIELDS)
    assert shown(body) == {"pre_instruction_auto": False, "run_ai": None, "restart": 0, "review_ai": None,
                           "review_count": 0, "time_limit": 60, "review_basis": "type", "hold": False}
    assert body["fields"]["pre_instruction_auto"]["choices"] == [True, False]
    assert not any(f["stored"] for f in body["fields"].values())

    on = setting(env, FB, key, "pre_instruction_auto", True)
    assert on["fields"]["pre_instruction_auto"] == {"value": True, "stored": True, "default": False,
                                                    "choices": [True, False]}
    assert stored_rows(env, FB, key) == {(key, "pre_instruction_auto"): "true"}
    assert shown(get_settings(env, FB, key))["pre_instruction_auto"] is True          # read again
    off = setting(env, FB, key, "pre_instruction_auto", False)
    assert off["fields"]["pre_instruction_auto"]["stored"] is True and shown(off)["pre_instruction_auto"] is False
    assert stored_rows(env, FB, key) == {(key, "pre_instruction_auto"): "false"}
    back = setting(env, FB, key, "pre_instruction_auto", None)
    assert back["fields"]["pre_instruction_auto"]["stored"] is False and stored_rows(env, FB, key) == {}

    for field, value in (("run_ai", "aip_card"), ("restart", -1), ("review_ai", "aip_assignee"),
                         ("review_count", 2), ("time_limit", 120), ("review_basis", "last")):
        setting(env, FB, key, field, value)
    again = get_settings(env, FB, key)
    assert {k: v for k, v in shown(again).items() if again["fields"][k]["stored"]} == {
        "run_ai": "aip_card", "restart": -1, "review_ai": "aip_assignee", "review_count": 2, "time_limit": 120,
        "review_basis": "last"}


def test_values_outside_the_plan_settings_are_refused(env):
    key = _plan_card(env)
    for field, value, reason in (("pre_instruction_auto", "on", "not_a_boolean"),
                                 ("pre_instruction_auto", 1, "not_a_boolean"),
                                 ("run_ai", "aip_missing", "unknown_ai"), ("restart", 99, "not_a_choice"),
                                 ("time_limit", 61, "not_a_choice"), ("review_basis", "all", "not_a_choice")):
        refused = setting(env, FB, key, field, value, expect=422)
        assert refused["error"]["params"]["errors"] == [{"field": "value", "reason": reason}], field
    for field in ("instruction", "instruction_ai", "delegation", "push"):
        refused = setting(env, FB, key, field, "use", expect=422)
        assert refused["error"]["params"]["errors"][0]["reason"] == "not_a_setting_of_this_card"
        assert refused["error"]["params"]["errors"][0]["card_kind"] == "plan"
    assert stored_rows(env, FB, key) == {}


def test_the_switch_and_the_run_block_touch_no_document(env, tmp_path):
    """Turning the switch on and off (and the run block) changes the card's setting rows only: the documents (the
    plan's body with its Steps' pre-instructions and attachments) and the stored files stay byte for byte."""
    key = _plan_card(env)
    storage = tmp_path / "storage"
    before_docs, before_files = _documents(env), _files(storage)
    for field, value in (("pre_instruction_auto", True), ("pre_instruction_auto", False), ("run_ai", "aip_card"),
                         ("review_count", 1), ("pre_instruction_auto", None)):
        setting(env, FB, key, field, value)
    assert _documents(env) == before_docs
    assert _files(storage) == before_files
    assert stored_rows(env, FB, key) == {(key, "review_count"): "1", (key, "run_ai"): '"aip_card"'}


def test_a_work_plan_node_hands_nothing_to_a_run(env):
    from flowgate.card_settings import settings

    key = _plan_card(env)
    for field, value in (("pre_instruction_auto", True), ("run_ai", "aip_card"), ("review_count", 2),
                         ("time_limit", 120), ("restart", 1)):
        setting(env, FB, key, field, value)
    row = {"node_key": key, "node_type": "document", "doc_type": "WP", "assignee_kind": None}
    assert settings.uses_ai(FB, row) is False
    assert settings.run_settings(FB, row) == {}
    assert settings.result_type(FB, row) is None
    assert settings.instruction_document(FB, row) == {}
