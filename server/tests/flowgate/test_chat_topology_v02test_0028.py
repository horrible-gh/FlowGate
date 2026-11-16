"""Explicit chat fork/join: real draft persistence, validation, rollback, legacy compatibility."""
import pytest

from test_fg_card_settings import legacy_cards, FA, db_path, decided, env, node, put_draft
from test_chat_control_v02test_0023 import _group, _draft, _ai_card, _write_instruction

pytestmark = pytest.mark.usefixtures("legacy_cards", "topology_policies")


@pytest.fixture()
def topology_policies(monkeypatch):
    from modules.flow_gate.graph_workflow.domain import policies
    from modules.flow_gate.graph_workflow.services import graph_validation_ports
    from flowgate.graph_validation.validator import validate_graph
    from flowgate.graph_policies import fork, join, gate, start
    for module in (fork, join, gate, start):
        for key, value, version, impl in module.SLOTS:
            monkeypatch.setitem(policies._registered, key, impl)
    monkeypatch.setitem(graph_validation_ports._handlers, "validate", validate_graph)


def link(a, b):
    return {"source": a, "target": b}


def diamond():
    return {"cards": [{"label": "A"}, {"label": "B"}, {"label": "C"}], "links": [
        link("$after", "card:0"), link("$after", "card:1"),
        link("card:0", "card:2"), link("card:1", "card:2"), link("card:2", "$end")]}


def seed(env):
    put_draft(env, FA, 0, [node("tmp_r", "R")])
    return _draft()["nodes"][0]["node_key"]


def test_diamond_saved_once_with_instruction_lines(env, decided):
    from flowgate.chat_control import service
    root = seed(env)
    result = service.place(_group(), "u_kim", diamond(), "en")
    a, b, c = [card["node_key"] for card in result["cards"]]
    end = result["gate"]["node_key"]
    assert _draft()["draft_version"] == 2
    assert {(e["source"], e["target"]) for e in _draft()["edges"]} == {
        (root, a), (root, b), (a, c), (b, c), (c, end)}
    assert all(card["instruction"]["doc_id"] for card in result["cards"])


def test_fork_without_end_then_join_existing_branches(env, decided, monkeypatch):
    from flowgate.chat_control import service
    seed(env)
    first = service.place(_group(), "u_kim", {"cards": [{}, {}], "gate": False,
        "links": [link("$after", "card:0"), link("$after", "card:1")]})
    a, b = [c["node_key"] for c in first["cards"]]
    # New join card, fed by both pre-existing branches.
    second = service.place(_group(), "u_kim", {"cards": [{}], "after": a,
        "links": [link("$after", "card:0"), link(b, "card:0"), link("card:0", "$end")]})
    c = second["cards"][0]["node_key"]
    assert {e["source"] for e in _draft()["edges"] if e["target"] == c} == {a, b}
    # Add a third branch, then attach it to the existing work-card join.
    third = service.place(_group(), "u_kim", {"cards": [{}], "after": first["after"], "gate": False,
        "links": [link("$after", "card:0")]})
    d = third["cards"][0]["node_key"]
    owned = []
    monkeypatch.setattr(service, "_record_owner", lambda flow, keys, owner: owned.extend(keys))
    before = {(e["source"], e["target"]) for e in _draft()["edges"]}
    joined = service.place(_group(), "u_kim", {"cards": [], "after": d, "join": c,
        "links": [link("$after", "$end")]})
    assert joined["cards"] == [] and joined["join"] == c and joined["gate"] is None
    assert owned == []
    assert {(e["source"], e["target"]) for e in _draft()["edges"]} == before | {(d, c)}


def test_reuse_confirm_insertion_and_settings_roles_mapping(env, decided, monkeypatch):
    from flowgate.chat_control import service
    seed(env)
    initial = service.place(_group(), "u_kim", {"cards": [{}]})
    payload = diamond()
    payload["cards"][1]["roles"] = {"instruction": {"kind": "not_used"}}
    payload["cards"][2]["settings"] = {"review_count": 0}
    chosen = []
    settings = []
    monkeypatch.setattr(service.placement_roles, "precheck", lambda *a: None)
    monkeypatch.setattr(service, "_precheck_settings", lambda *a: None)
    monkeypatch.setattr(service.placement_roles, "write", lambda f, p, u, pairs, o: chosen.extend(pairs))
    monkeypatch.setattr(service, "_write_settings", lambda f, k, s, *a: settings.append((k, s)))
    result = service.place(_group(), "u_kim", payload)
    assert result["gate"] == {"node_key": initial["gate"]["node_key"], "created": False}
    assert chosen[0][0] == result["cards"][1]["node_key"]
    assert settings == [(result["cards"][2]["node_key"], {"review_count": 0})]
    assert not any(e["source"] == initial["cards"][0]["node_key"] and
                   e["target"] == initial["gate"]["node_key"] for e in _draft()["edges"])


@pytest.mark.parametrize("payload, code", [
    ({"cards": [{}], "links": [link("$after", "card:0"), link("card:0", "card:0"),
                                 link("card:0", "$end")]}, "chat_cards_graph_invalid"),
    ({"cards": [{}], "links": [link("gn_missing", "card:0")]}, "chat_cards_link_unknown"),
    ({"cards": [{}], "links": [link("$after", "card:0")]}, "chat_cards_links_incomplete"),
    ({"cards": [], "join": "gn_missing", "links": [link("$after", "$end")]}, "chat_cards_join_unknown"),
    ({"cards": [{}], "links": [link("$after", "card:2")]}, "invalid_request"),
    ({"cards": [{}], "links": None}, "invalid_request"),
    ({"cards": [{}], "join": "gn_x"}, "invalid_request"),
])
def test_invalid_request_does_not_change_draft(env, decided, payload, code):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    seed(env)
    before = _draft()
    with pytest.raises(GraphApiError) as caught:
        service.place(_group(), "u_kim", payload)
    assert caught.value.code == code
    assert _draft() == before


def test_protected_join_rejected_without_changes(env, decided, monkeypatch):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    seed(env)
    first = service.place(_group(), "u_kim", {"cards": [{}]})
    end = first["gate"]["node_key"]
    monkeypatch.setattr(service, "_run_rows", lambda f: {end: {"status": "review"}})
    before = _draft()
    with pytest.raises(GraphApiError) as caught:
        service.place(_group(), "u_kim", {"cards": [{}], "after": first["after"], "join": end,
            "links": [link("$after", "card:0"), link("card:0", "$end")]})
    assert caught.value.code == "chat_cards_join_protected"
    assert _draft() == before


def test_invalid_empty_flow_does_not_create_start_card(env, decided):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    assert _draft() is None
    with pytest.raises(GraphApiError):
        service.place(_group(), "u_kim", {"cards": [{}], "links": [link("$after", "card:0")]})
    assert _draft() is None
    result = service.place(_group(), "u_kim", diamond())
    assert result["root"]["created"] and _draft()["draft_version"] == 1


def test_diamond_starts_both_branches_and_leaves_join_pending(env, decided):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.store import run_store
    seed(env)
    result = service.place(_group(), "u_kim", diamond())
    for card in result["cards"]:
        _ai_card(card["node_key"])
        _write_instruction(card["instruction"]["doc_id"], "Implement the change.")
    run = service.start(_group(), "u_kim")["run"]
    a, b, c = [card["node_key"] for card in result["cards"]]
    jobs = {j["node_key"] for j in env["jobs"]}
    assert {a, b} <= jobs and c not in jobs
    rows = {r["node_key"]: r for r in run_store.node_runs(run["run_id"])}
    assert rows[a]["status"] == rows[b]["status"] == "open"
    assert rows[c]["status"] == "pending"
    # Exercise the real frontier and AND policy on the graph this API saved:
    # one approval waits; the last approval opens C once; replay cannot reopen C.
    from flowgate.graph_policies import fork, join
    from modules.flow_gate.graph_workflow.services import graph_service
    from modules.flow_gate.graph_workflow.store import definition_store
    from modules.flow_gate.graph_workflow.domain.frontier import NodeRun, RunState, propagate
    graph = graph_service.as_graph(definition_store.revision(FA, run["revision_no"]))
    rules = {key: impl for key, _, _, impl in fork.SLOTS + join.SLOTS}
    states = {k: NodeRun(k, "pending") for k in graph.node_keys()}
    states[result["after"]] = NodeRun(result["after"], "done")
    states[a], states[b] = NodeRun(a, "done"), NodeRun(b, "review")
    assert propagate(graph, RunState("running", states), a, "approved", rules).open == []
    states[b] = NodeRun(b, "done")
    assert propagate(graph, RunState("running", states), b, "approved", rules).open == [c]
    states[c] = NodeRun(c, "open")
    assert propagate(graph, RunState("running", states), b, "approved", rules).open == []


def test_excluded_source_join_is_rejected_without_writes(env, decided):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_orphan", "T")])
    root = next(n["node_key"] for n in _draft()["nodes"] if n["doc_type"] == "R")
    orphan = next(n["node_key"] for n in _draft()["nodes"] if n["doc_type"] == "T")
    before = _draft()
    with pytest.raises(GraphApiError) as caught:
        service.place(_group(), "u_kim", {"cards": [{}], "after": root, "links": [
            link("$after", "card:0"), link(orphan, "card:0"), link("card:0", "$end")]})
    assert caught.value.code == "chat_cards_graph_invalid"
    assert "excluded_path_join" in {i["code"] for i in caught.value.params["issues"]}
    assert _draft() == before


def test_late_role_failure_rolls_back_topology_and_documents(env, decided, monkeypatch):
    from flowgate.chat_control import service
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from test_fg_card_settings import _sql
    seed(env)
    before = _draft()
    docs = _sql(env["db"], "SELECT doc_id FROM documents ORDER BY doc_id")
    payload = diamond()
    payload["cards"][0]["roles"] = {"instruction": {"kind": "not_used"}}
    monkeypatch.setattr(service.placement_roles, "precheck", lambda *a: None)
    def refuse(*args):
        raise GraphApiError(422, "ai_not_selectable", {})
    monkeypatch.setattr(service.placement_roles, "write", refuse)
    with pytest.raises(GraphApiError, match="ai_not_selectable"):
        service.place(_group(), "u_kim", payload)
    assert _draft() == before
    assert _sql(env["db"], "SELECT doc_id FROM documents ORDER BY doc_id") == docs
