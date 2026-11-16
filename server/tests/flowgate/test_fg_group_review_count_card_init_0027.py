"""flowgate.v02test.0027 (0007-T, A-server): the AI role group's default review count copied once at card creation.

Real migrated SQLite and the real draft route (the 0113 creation hook): a new card stores the type row's count, else
the common row's, else 0 — explicitly, 0 included; the result review assignment takes that count;
a later group change or a re-save never touches an existing card; a card's own value (also a PATCH null) is kept;
a failed group read or a value outside today's choices never blocks the draft. The WP first deploy's explicit 0 is
kept over the group default.
"""
import types

import pytest
from test_fg_card_settings import db_path, env, FA, FB, _sql, node, put_draft, placed  # noqa: F401 - fixtures
from test_fg_ai_roles_l1_card_init import group_defaults, save_defaults, state


def group_count(value, type_key="common"):
    from modules.flow_gate.db import ai_group_role_profiles as profiles
    from modules.flow_gate.db.connection import get_store
    group_defaults({}, type_key)
    with get_store().transaction():
        profiles.set_review_count("roles", type_key, value)


def stored(key, flow=FA):
    from flowgate.card_settings import repository
    return repository.stored(flow, key, "review_count")


def test_type_then_common_then_zero_stored_explicitly_per_card_kind(env):
    from flowgate.card_settings import repository
    save_defaults()
    group_count(1)
    group_count(2, "T")
    group_count(0, "N")
    km = put_draft(env, FA, 0, [node("tmp_t", "T"), node("tmp_n", "N"), node("tmp_ts", "TS"), node("tmp_wp", "WP"),
                                node("tmp_d", "D"), node("tmp_r", "R"), node("tmp_g", None, "gate")])["key_map"]
    assert stored(km["tmp_t"]) == 2                     # the type row
    assert stored(km["tmp_n"]) == 0                     # the type row's explicit 0 wins over common
    for ref in ("tmp_ts", "tmp_wp", "tmp_d"):
        assert stored(km[ref]) == 1                     # no type row value: common
    for ref in ("tmp_r", "tmp_g"):                    # no 검수 횟수 on these kinds: nothing is stored
        assert stored(km[ref]) is repository.ABSENT
    # Only the count is added to the stored settings (no extra field).
    assert set(repository.values(FA, km["tmp_t"])) == {"review_count"}
    # One creation = one settings version, as before the count was copied.
    assert all(state(FA, km[ref])[2] == 1 for ref in ("tmp_t", "tmp_n", "tmp_ts", "tmp_wp", "tmp_d"))


def test_no_role_group_or_no_counts_store_zero(env):
    from modules.flow_gate.db import ai_groups
    assert ai_groups.get_role_group() is None
    key = placed(env, FA)
    assert stored(key) == 0
    group_defaults({})
    group_defaults({}, "T")
    key = placed(env, FB)
    assert stored(key, FB) == 0


def test_group_change_and_resave_leave_existing_cards_and_card_values_alone(env):
    from flowgate.card_settings import repository, settings
    save_defaults()
    group_count(2)
    first = put_draft(env, FA, 0, [node("tmp_a"), node("tmp_b"), node("tmp_c")])["key_map"]
    a, b, c = first["tmp_a"], first["tmp_b"], first["tmp_c"]
    group_ref = {"group_id": FA, "project_id": "flowgate"}
    settings.write(group_ref, b, "review_count", 3, "u_lee")
    settings.write(group_ref, c, "review_count", None, "u_lee")      # PATCH null: back to the card default (no row)
    before = {k: stored(k) for k in (a, b, c)}
    assert before[a] == 2 and before[b] == 3 and before[c] is repository.ABSENT
    group_count(1)
    group_count(0, "T")
    second = put_draft(env, FA, 1, [node(a), node(b), node(c), node("tmp_new")])["key_map"]
    assert {k: stored(k) for k in (a, b, c)} == before
    assert stored(second["tmp_new"]) == 0                      # only the new card takes today's group value


def test_patch_null_is_not_recopied(env):
    from flowgate.card_settings import repository, settings
    group_count(2)
    key = placed(env, FA)
    settings.write({"group_id": FA, "project_id": "flowgate"}, key, "review_count", None, "u_lee")
    assert repository.stored(FA, key, "review_count") is repository.ABSENT
    put_draft(env, FA, 1, [node(key), node("tmp_more")])
    assert repository.stored(FA, key, "review_count") is repository.ABSENT


@pytest.mark.parametrize("count,rounds", [(2, 2), (1, 1), (-1, -1), (0, None)])
def test_result_review_assignment_takes_the_copied_count(env, monkeypatch, count, rounds):
    from flowgate.card_settings import repository
    from flowgate.card_settings import stage_assignment as sa
    group_count(count)
    key = placed(env, FA)
    values = repository.values(FA, key)
    assert values["review_count"] == count
    monkeypatch.setattr(sa._store, "read", lambda *a, **k: None)
    a = sa._a(sa.AI, source="slot", provider_id="aip_card")
    out = sa.with_result_review_plan(a, "att_x", sa.AI, types.SimpleNamespace(source="slot"), values)
    if rounds is None:
        assert out["actor"] == sa.NO_REVIEW and out["actor_reason"] == sa.NO_REVIEW_SETTING
    else:
        assert out["actor"] == sa.AI and out["review_rounds"] == rounds


@pytest.mark.parametrize("count,ceiling,rounds", [(5, 5, 5), (4, 5, 4), (5, 3, 1)])
def test_result_review_rounds_follow_the_policy_choices_not_a_frozen_set(env, monkeypatch, count, ceiling, rounds):
    """TR0008 rev1 rejection: the slot path (AI author, AI reviewer, source slot) with a stored 5 under the policy max 5
    plans 5 rounds, not 1. A count left outside today's choices after the max was lowered keeps the old fallback, 1."""
    from flowgate.card_settings import stage_assignment as sa
    from modules.flow_gate.settings import ai_execution_policy_service as policy
    monkeypatch.setattr(policy, "get_repeat_count_max", lambda: ceiling)
    monkeypatch.setattr(sa._store, "read", lambda *a, **k: None)
    a = sa._a(sa.AI, source="slot", provider_id="aip_card")
    out = sa.with_result_review_plan(a, "att_x", sa.AI, types.SimpleNamespace(source="slot"), {"review_count": count})
    assert out["actor"] == sa.AI and out["review_rounds"] == rounds


@pytest.mark.parametrize("ceiling,needs_review", [(5, True), (3, False)])
def test_instruction_review_count_follows_the_policy_choices(env, monkeypatch, ceiling, needs_review):
    """The same frozen set was on the instruction review count: 5 under max 5 needs review (not "unknown")."""
    from flowgate.card_settings import fields, repository
    from flowgate.card_settings import stage_assignment as sa
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.settings import ai_execution_policy_service as policy
    key = placed(env, FA)
    with_flow_guard(FA, lambda: repository.put(FA, key, "instruction_review_count", 5, "u_kim"))
    monkeypatch.setattr(policy, "get_repeat_count_max", lambda: ceiling)
    assert sa.instruction_review_setting(fields.RUN, FA, key) == ("needs_review" if needs_review else sa._UNKNOWN)


def test_failed_group_read_leaves_no_row_and_draft_commits(env, monkeypatch):
    from flowgate.ai_roles import card_init
    from flowgate.card_settings import repository
    from modules.flow_gate.db.connection import get_store
    save_defaults()
    group_count(2)
    def broken():
        return get_store()._fetch_all("SELECT * FROM missing_group_profiles", [])
    monkeypatch.setattr(card_init, "creation_group_profiles", broken)
    key = placed(env, FA)
    assert repository.stored(FA, key, "review_count") is repository.ABSENT
    assert state(FA, key)[1].state == "initialized"
    assert _sql(env["db"], "SELECT * FROM graph_drafts WHERE group_id=?", (FA,))


def test_group_value_outside_card_choices_falls_back_to_zero(env, monkeypatch):
    from modules.flow_gate.settings import ai_execution_policy_service as policy
    group_count(9)                                         # stored under an older, higher ceiling
    monkeypatch.setattr(policy, "get_repeat_count_max", lambda: 3)
    key = placed(env, FA)
    assert stored(key) == 0


@pytest.mark.parametrize("ceiling,expected", [
    (5, {"common": (5, "common"), "T": (5, "common"), "N": (4, "type"), "TS": (2, "type"), "WP": (-1, "type")}),
    (3, {"common": (0, "default"), "T": (0, "default"), "N": (0, "default"), "TS": (2, "type"), "WP": (-1, "type")}),
])
def test_api_effective_count_is_what_a_new_card_copies_after_the_max_changes(env, monkeypatch, ceiling, expected):
    """TR0008 rev2 rejection: common 5 (and a type's 4) saved under the max 5, then the max lowered to 3. The API kept
    answering effective 5 / source common for common and its inheriting types while the creation copy stored 0. Now
    both use one judgment (``effective_review_count`` against the card choices): the stored values stay as they are,
    and each row's effective value is exactly what a new card of that type stores — before and after lowering."""
    from flowgate.ai_roles import legacy_adapter
    from modules.flow_gate.services import ai_group_role_profile_service as service
    from modules.flow_gate.settings import ai_execution_policy_service as policy
    save_defaults()
    monkeypatch.setattr(policy, "get_repeat_count_max", lambda: 5)
    for type_key in ("common", "T", "N", "TS", "WP"):
        group_defaults({}, type_key)
    service.set_review_count("roles", "common", {"review_count": 5})
    service.set_review_count("roles", "N", {"review_count": 4})
    service.set_review_count("roles", "TS", {"review_count": 2})
    group_count(-1, "WP")                                      # e.g. written by an older rule; a valid card value
    monkeypatch.setattr(policy, "get_repeat_count_max", lambda: ceiling)
    assert service.card_review_count_choices() == legacy_adapter.card_review_count_choices()
    counts = service.profiles("roles")["review_counts"]
    # Stored values are never changed by a read or by lowering the max.
    assert {k: counts[k]["review_count"] for k in expected} == {
        "common": 5, "T": None, "N": 4, "TS": 2, "WP": -1}
    assert {k: (counts[k]["effective_review_count"], counts[k]["source"]) for k in expected} == expected
    km = put_draft(env, FA, 0, [node("tmp_t", "T"), node("tmp_n", "N"), node("tmp_ts", "TS"), node("tmp_wp", "WP"),
                                node("tmp_d", "D")])["key_map"]
    # D has no type row: a new D card copies what the common row shows.
    for ref, type_key in (("tmp_t", "T"), ("tmp_n", "N"), ("tmp_ts", "TS"), ("tmp_wp", "WP"), ("tmp_d", "common")):
        assert stored(km[ref]) == counts[type_key]["effective_review_count"], type_key


def test_value_stored_before_the_copy_is_kept(env):
    """The WP first deploy writes its plan values before ``initialize_wp_card``: an explicit 0 (or an unreadable row)
    is never replaced by the group default."""
    from flowgate.ai_roles import card_init
    from flowgate.card_settings import repository
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.db.connection import get_store
    group_count(2)
    handler = ports._handler(ports.CARDS_CREATED)
    ports.unregister(ports.CARDS_CREATED)
    try:
        km = put_draft(env, FA, 0, [node("tmp_zero"), node("tmp_bad"), node("tmp_none")])["key_map"]
    finally:
        ports.register(ports.CARDS_CREATED, handler)
    zero, bad, none = km["tmp_zero"], km["tmp_bad"], km["tmp_none"]
    def deploy():
        repository.put(FA, zero, "review_count", 0, "u_kim")
        get_store()._execute("INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at, "
                             "updated_by) VALUES (?, ?, 'review_count', '{broken', 't', 'u_kim')", [FA, bad])
        for key in (zero, bad, none):
            card_init.initialize_wp_card(FA, key, "run", "u_kim", {})
    with_flow_guard(FA, deploy)
    assert stored(zero) == 0
    assert stored(bad) is repository.UNREADABLE
    assert stored(none) == 2
    with pytest.raises(RuntimeError):                       # outside its Flow guard nothing is written
        card_init._initialize(FA, none, "run", "u_kim", {}, True)
