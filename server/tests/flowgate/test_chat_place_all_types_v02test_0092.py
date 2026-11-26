"""F92 R1-R7 — a chat places every palette work card, not only work (T) and test (TS).

The work plan card (WP) is retired (a draft save refuses a new one: ``work_plan_retired``), so it stays outside.

On the card settings harness (real SQLite DB with every migration, real Graph draft saves, real role stores):

- R1: ``doc_type`` takes T, TS, N, D, P, L, DB (default T); another value (WP too) is 422 naming the whole list;
- R2: a placed card is the card a person drops from the palette: palette item, default name and document lines
  (N: N + NR, D / P / L / DB: DS + design, TS: TS + TSR);
- R3: the card role read / change works for every placed type;
- R4: the run settings of each kind (N / D / P / L / DB like T, TS the review count only);
- R5: the answer names each card's instruction line;
- R6: a start refuses an empty instruction of every placed type;
- R7: the tool schema, the CLI mention and the help contract name the types.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, FB, _sql, db_path, decided, env,
)
from test_chat_card_roles_v02gui_0028 import (  # noqa: F401 — fixtures used by name
    CH1, EXEC_CARD, _caller, _group, _refused, _scope, guard,
)

ALL_TYPES = ("T", "TS", "N", "D", "P", "L", "DB")
PALETTE = {"T": "task", "TS": "test", "N": "research", "D": "basic_design", "P": "protocol", "L": "logic",
           "DB": "db_design"}
KO_NAMES = {"T": "작업", "TS": "테스트", "N": "조사", "D": "기본 설계", "P": "프로토콜", "L": "로직", "DB": "DB설계"}
FIVE = ["review_count", "restart", "time_limit", "verify_max_runs", "verify_run_minutes"]


def _place(cards, doc=CH1, **more):
    from flowgate.chat_control import authz, service
    return service.place(_group(), "u_kim", {"cards": cards, **more}, "ko", owner=authz.owner(_scope(), doc))


def _draft():
    from modules.flow_gate.graph_workflow.store import definition_store
    return definition_store.draft(FA)


def _lines(key):
    from flowgate.card_documents import repository
    return [(r["slot"], r["doc_type"]) for r in repository.rows(FA, [key])]


# ── R1 / R2: the request ─────────────────────────────────────────────────────

def test_every_palette_work_type_is_accepted_with_its_palette_item_and_default_name():
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.node_title_names import PALETTE_KEY_OF_DOC_TYPE

    # every palette type but the retired work plan card
    assert service.WORK_TYPES == ALL_TYPES and set(ALL_TYPES) == set(PALETTE_KEY_OF_DOC_TYPE) - {"WP"}
    for chunk in (ALL_TYPES[:5], ALL_TYPES[5:]):
        cards, _after, _gate = service.parse_place({"cards": [{"doc_type": t} for t in chunk]}, "ko")
        assert [(c["doc_type"], c["palette_item_id"], c["label"]) for c in cards] == [
            (t, PALETTE[t], KO_NAMES[t]) for t in chunk]
    cards, _after, _gate = service.parse_place({"cards": [{}, {"doc_type": "N"}, {"doc_type": "D"}]}, "en")
    assert [(c["doc_type"], c["label"]) for c in cards] == [("T", "Task"), ("N", "Research"), ("D", "Basic design")]


@pytest.mark.parametrize("doc_type", ["WP", "T2", "B", "CH", "NR", "n", "", None, 1])
def test_another_type_is_refused_naming_every_allowed_type(doc_type):
    from flowgate.chat_control import service

    err = _refused(lambda: service.parse_place({"cards": [{"doc_type": doc_type}]}, "ko"), 422, "invalid_request")
    assert err.params["errors"] == [{"field": "cards[0].doc_type", "reason": "not_one_of:T,TS,N,D,P,L,DB,R"}]


# ── R2 / R5: placed cards ────────────────────────────────────────────────────

def test_research_design_work_test_are_placed_in_one_chain_as_palette_cards(env, decided):
    placed = _place([{"doc_type": "N"}, {"doc_type": "D", "label": "배치 설계"}, {"doc_type": "T"},
                     {"doc_type": "TS"}])
    cards = placed["cards"]
    assert [(c["doc_type"], c["label"]) for c in cards] == [("N", "조사"), ("D", "배치 설계"), ("T", "작업"),
                                                              ("TS", "테스트")]
    nodes = {n["node_key"]: n for n in _draft()["nodes"]}
    assert [(nodes[c["node_key"]]["node_type"], nodes[c["node_key"]]["doc_type"],
             nodes[c["node_key"]]["palette_item_id"]) for c in cards] == [
        ("document", "N", "research"), ("document", "D", "basic_design"), ("document", "T", "task"),
        ("document", "TS", "test")]
    keys = [c["node_key"] for c in cards]
    edges = {(e["source"], e["target"]) for e in _draft()["edges"]}
    assert set(zip([placed["after"]] + keys, keys + [placed["gate"]["node_key"]])) <= edges
    assert nodes[placed["gate"]["node_key"]]["node_type"] == "gate"
    # the document lines a person's palette card gets
    assert [_lines(k) for k in keys] == [
        [("instruction", "N"), ("report", "NR")], [("instruction", "DS"), ("report", "D")],
        [("instruction", "T"), ("report", "TR")],
        [("instruction", "TS"), ("scenario", "TS"), ("report", "TSR")]]
    for card in cards:
        assert card["instruction"]["slot"] == "instruction" and card["instruction"]["doc_id"]
        assert set(card["roles"]) == {"instruction", "execution", "review"}
    assert [list(c["settings"]) for c in cards] == [FIVE, FIVE, FIVE, ["review_count"]]
    assert "instruction" in placed["next"]


@pytest.mark.parametrize("doc_type, lines", [
    ("P", [("instruction", "DS"), ("report", "P")]), ("L", [("instruction", "DS"), ("report", "L")]),
    ("DB", [("instruction", "DS"), ("report", "DB")]),
])
def test_each_design_card_has_its_instruction_and_design_document(env, decided, doc_type, lines):
    card = _place([{"doc_type": doc_type, "settings": {"review_count": 2, "restart": 0, "time_limit": 60,
                                                       "verify_max_runs": 5, "verify_run_minutes": 2}}])["cards"][0]
    node = next(n for n in _draft()["nodes"] if n["node_key"] == card["node_key"])
    assert (node["doc_type"], node["palette_item_id"], node["label"]) == (doc_type, PALETTE[doc_type],
                                                                          KO_NAMES[doc_type])
    assert _lines(card["node_key"]) == lines and card["instruction"]["doc_id"]
    assert card["settings"] == {"review_count": 2, "restart": 0, "time_limit": 60, "verify_max_runs": 5,
                                "verify_run_minutes": 2}


def test_the_card_role_read_works_for_a_research_and_a_design_card(env, decided, guard):
    from flowgate.chat_control import card_roles

    research, design = (c["node_key"] for c in _place([{"doc_type": "N"}, {"doc_type": "DB"}])["cards"])
    for key in (research, design):
        answer = card_roles.view(_caller(), key)
        assert answer["card_kind"] == "run" and answer["applicable_settings"] == FIVE
        assert answer["applicable_roles"] == ["instruction", "execution", "review"]
        done = card_roles.change(_caller(), key, {"roles": {"execution": EXEC_CARD}, "settings": {"review_count": 2},
                                                  "expected_settings_version": answer["settings_version"]})
        assert done["changed_roles"] == ["execution"] and done["changed_settings"] == ["review_count"]


# ── R1 / R4: refusals place nothing ──────────────────────────────────────────

@pytest.mark.parametrize("doc_type", ["WP"])
def test_the_retired_work_plan_card_is_not_a_placeable_type(env, decided, doc_type):
    """A draft save refuses a new work plan card (``work_plan_retired``); the chat request is refused before it."""
    from flowgate.chat_control import card_role_store

    before = _draft()
    err = _refused(lambda: _place([{"doc_type": "N"}, {"doc_type": doc_type}]), 422, "invalid_request")
    assert err.params["errors"] == [{"field": "cards[1].doc_type", "reason": "not_one_of:T,TS,N,D,P,L,DB,R"}]
    assert _draft() == before and card_role_store.placed_keys(FA, CH1) == []


@pytest.mark.parametrize("card, field", [
    ({"doc_type": "TS", "settings": {"restart": 1}}, "cards[0].settings.restart"),
    ({"doc_type": "TS", "settings": {"verify_max_runs": 5}}, "cards[0].settings.verify_max_runs"),
])
def test_a_setting_the_card_kind_has_not_places_nothing(env, decided, card, field):
    from flowgate.chat_control import card_role_store

    before = _draft()
    err = _refused(lambda: _place([card]), 422, "invalid_request")
    assert {"field": field, "reason": "setting_not_applicable"}.items() <= err.params["errors"][0].items()
    assert err.params["errors"][0]["applicable_settings"]
    assert _draft() == before and card_role_store.placed_keys(FA, CH1) == []


def test_a_value_outside_the_choices_of_a_research_card_answers_the_choices(env, decided):
    err = _refused(lambda: _place([{"doc_type": "N", "settings": {"time_limit": 61}}]), 422, "invalid_request")
    assert err.params["errors"][0]["reason"] == "not_a_choice" and 60 in err.params["errors"][0]["choices"]


# ── R6: the start check ──────────────────────────────────────────────────────

def test_an_empty_instruction_of_every_placed_type_refuses_the_start(env, decided):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.graph_workflow.store import definition_store, run_store

    first = _place([{"doc_type": t} for t in ("N", "D", "P", "L", "DB")])
    second = _place([{"doc_type": "T"}, {"doc_type": "TS"}])
    placed = [c["node_key"] for c in first["cards"] + second["cards"]]
    assert set(placed) <= set(service.missing_instructions(FA, _draft()))
    latest = definition_store.latest_revision_no(FA)
    with pytest.raises(GraphApiError) as caught:
        service.start(_group(), "u_kim", None)
    assert caught.value.status == 409 and caught.value.code == "chat_run_instruction_missing"
    assert set(placed) <= set(caught.value.params["node_keys"])
    assert definition_store.latest_revision_no(FA) == latest and run_store.current_run(FA) is None


# ── R1: topology keeps its rules for every type ──────────────────────────────

@pytest.fixture()
def topology_policies(monkeypatch):
    """The fork / join / gate / start policies and the graph validator an explicit topology is judged with
    (registered at startup in production; as ``test_chat_topology_v02test_0028``)."""
    from flowgate.graph_policies import fork, gate, join, start
    from flowgate.graph_validation.validator import validate_graph
    from modules.flow_gate.graph_workflow.domain import policies
    from modules.flow_gate.graph_workflow.services import graph_validation_ports

    for module in (fork, join, gate, start):
        for key, _value, _version, impl in module.SLOTS:
            monkeypatch.setitem(policies._registered, key, impl)
    monkeypatch.setitem(graph_validation_ports._handlers, "validate", validate_graph)


def test_a_fork_of_two_design_cards_joins_in_a_work_card(env, decided, topology_policies):
    placed = _place([{"doc_type": "P"}, {"doc_type": "L"}, {"doc_type": "T"}], links=[
        {"source": "$after", "target": "card:0"}, {"source": "$after", "target": "card:1"},
        {"source": "card:0", "target": "card:2"}, {"source": "card:1", "target": "card:2"},
        {"source": "card:2", "target": "$end"}])
    p, l, t = (c["node_key"] for c in placed["cards"])
    edges = {(e["source"], e["target"]) for e in _draft()["edges"]}
    assert {(placed["after"], p), (placed["after"], l), (p, t), (l, t), (t, placed["gate"]["node_key"])} <= edges


# ── R7: tools and help ───────────────────────────────────────────────────────

def test_the_tool_schema_and_the_contract_name_every_type():
    from flowgate.chat_control import service, tools

    item = tools.PLACE_SCHEMA["properties"]["cards"]["items"]["properties"]
    assert item["doc_type"]["enum"] == list(service.WORK_TYPES) + ["R"] == list(ALL_TYPES) + ["R"]
    assert "WP" not in item["doc_type"]["enum"]
    for text in (item["doc_type"]["description"], tools.PLACE_DESCRIPTION,
                 tools.contract_place("/flowgate/api/v1", "TOKEN")):
        for word in ("N research", "D basic design", "P protocol", "L logic", "DB DB design", "TS test"):
            assert word in text, (word, text)
    assert "do not place research/design/plan cards" not in tools.GUIDE and "Research and design cards" in tools.GUIDE
    assert "Any placed type" in tools.contract_roles("/flowgate/api/v1", "TOKEN")
