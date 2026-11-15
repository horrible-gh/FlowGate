"""flowgate.v02gui.0019 — a root card with no line holds its document; Run All on it opens nothing, refuses nothing.

0004-CH turns 13-15: a Flow whose only cards are the Bug / 요구사항 start card (and cards not connected to it) was
refused at Run All with ``root_binding_no_start_node`` (422 ``graph_validation_failed``), because the root binding
looked only at the run start nodes, and a root card with no outgoing line is no run start (flowgate.v02.0061).
The binding now falls back to the root card with no line when no connected one exists; a connected root card still
wins (0061). The run scope is not changed: a card with no line is not run.
"""
import pytest

from test_fg_card_settings import FA, _h, db_path, edge, env, node, put_draft  # noqa: F401 — fixtures by name
from flowgate.graph_validation.run_scope import run_scope
from modules.flow_gate.graph_workflow.domain import policies, root_binding, run_policies, run_scope_ports
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge, GraphNode
from modules.flow_gate.graph_workflow.store import assignment_store, definition_store


@pytest.fixture(autouse=True)
def document_write_ports(monkeypatch):
    # Match startup's document write registration before the shared fixture seeds its root document.
    from flowgate.card_document_edit import integration
    from modules.flow_gate.graph_workflow.services import card_document_ports
    monkeypatch.setattr(card_document_ports, "_handlers", dict(card_document_ports._handlers))
    integration.register()


@pytest.fixture
def registered(monkeypatch):
    """The product policies and run scope (``flowgate.graph_policies.integration``), as startup registers them."""
    from flowgate.graph_policies import integration
    monkeypatch.setattr(policies, "_registered", dict(policies._registered))
    monkeypatch.setattr(policies, "_system_default", dict(policies._system_default))
    monkeypatch.setattr(run_policies, "_implementations", dict(run_policies._implementations))
    monkeypatch.setattr(run_scope_ports, "_handler", run_scope_ports._handler)
    integration.register()
    from flowgate.graph_validation import integration as validation
    from modules.flow_gate.graph_workflow.services import graph_validation_ports
    monkeypatch.setattr(graph_validation_ports, "_handlers", dict(graph_validation_ports._handlers))
    validation.register()


def doc(key, doc_type="T"):
    return GraphNode(key, "document", doc_type, key)


def graph(nodes, pairs=()):
    return Graph(tuple(nodes), tuple(GraphEdge(f"e{i}", a, b) for i, (a, b) in enumerate(pairs)))


BUG = [{"doc_id": "f.0001-B", "type_code": "B"}]


# ── binding judgement (pure) ─────────────────────────────────────────────────

def test_a_lone_root_card_with_no_line_is_bound(registered):
    for g in (graph([doc("gn_b", "B")]), graph([doc("gn_b", "B"), doc("gn_t")])):
        assert run_scope(g).starts == ()                                   # still no run start (0061)
        check = root_binding.check_root_binding(g, "unassigned", BUG, None)
        assert (check.status, check.node_key, check.doc_id, check.issues) == ("bindable", "gn_b", "f.0001-B", [])


def test_a_connected_root_card_still_wins_over_one_with_no_line(registered):
    g = graph([doc("gn_lone", "B"), doc("gn_b", "B"), doc("gn_t")], [("gn_b", "gn_t")])
    assert root_binding.check_root_binding(g, "unassigned", BUG, None).node_key == "gn_b"
    stale = {"node_key": "gn_lone", "doc_id": "f.0001-B", "doc_type": "B"}
    assert root_binding.check_root_binding(g, "graph", BUG, stale).status == "blocked"


def test_what_stays_refused(registered):
    twins = root_binding.check_root_binding(graph([doc("gn_b1", "B"), doc("gn_b2", "B")]), "unassigned", BUG, None)
    assert twins.issues[0]["code"] == "root_binding_ambiguous_start" and twins.issues[0]["node_keys"] == ["gn_b1", "gn_b2"]
    none = root_binding.check_root_binding(graph([doc("gn_t")]), "unassigned", BUG, None)
    assert none.issues[0]["code"] == "root_binding_no_start_node"
    behind = root_binding.check_root_binding(graph([doc("gn_t"), doc("gn_b", "B")], [("gn_t", "gn_b")]),
                                             "unassigned", BUG, None)
    assert behind.issues[0]["code"] == "root_binding_no_start_node"     # a root card with an incoming line


def test_removing_every_line_of_the_bound_card_keeps_the_binding(registered):
    record = {"node_key": "gn_b", "doc_id": "f.0001-B", "doc_type": "B"}
    check = root_binding.check_root_binding(graph([doc("gn_b", "B"), doc("gn_t")]), "graph", BUG, record)
    assert (check.status, check.node_key, check.issues) == ("inherited", "gn_b", [])
    behind = graph([doc("gn_t"), doc("gn_b", "B")], [("gn_t", "gn_b")])
    assert root_binding.check_root_binding(behind, "graph", BUG, record).issues[0]["code"] == "root_binding_start_removed"


# ── Run All on the real API ──────────────────────────────────────────────────

def _confirm(env, version, base=None):
    body = {"draft_version": version} if base is None else {"draft_version": version, "base_revision_no": base}
    return env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json=body, headers=_h())


def _run_all(env):
    return env["client"].post(f"/api/v1/flows/{FA}/runs", json={"mode": "full"}, headers=_h())


def test_run_all_with_no_line_runs_nothing_and_a_drawn_line_then_runs(env, registered):
    saved = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_t", "T")])
    r_key, t_key = saved["key_map"]["tmp_r"], saved["key_map"]["tmp_t"]
    confirmed = _confirm(env, saved["draft_version"])
    assert confirmed.status_code == 201, confirmed.text                  # was 422 root_binding_no_start_node
    assert confirmed.json()["root_binding"]["status"] == "bound"
    assert confirmed.json()["root_binding"]["node_key"] == r_key
    assert assignment_store.root_binding(FA)["node_key"] == r_key

    first = _run_all(env)
    assert first.status_code == 201, first.text                          # was 409 root_binding_lost
    run = first.json()["run"]
    assert all(n["status"] not in ("open", "review") for n in run["node_runs"]), run["node_runs"]
    assert {n["node_key"] for n in run["node_runs"]} <= {r_key}           # the T card with no line is not run

    # the person draws the line Bug -> Task and presses Run All again: the run goes on with it
    rev = definition_store.revision(FA, 1)
    redraft = put_draft(env, FA, 0, [node(n["node_key"], n["doc_type"]) for n in rev["nodes"]],
                        [edge("tmp_e", r_key, t_key)])
    again = _confirm(env, redraft["draft_version"], base=1)
    assert again.status_code == 201, again.text
    second = _run_all(env)
    assert second.status_code == 201, second.text
    nodes = {n["node_key"]: n for n in second.json()["run"]["node_runs"]}
    assert nodes[r_key]["status"] == "done"
    assert t_key in nodes and nodes[t_key]["status"] != "pending", nodes
