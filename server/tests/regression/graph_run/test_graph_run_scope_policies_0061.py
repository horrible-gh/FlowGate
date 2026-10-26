"""0061 T#1 regression cases; execution belongs to the TS/TSR stage.

These fixtures use the real policy registry/scope and a recording store mock; no AI,
DB, token issuance or Git operation is needed. TR checks this file's syntax only.
"""
from unittest.mock import Mock

import pytest

from flowgate.graph_policies import completion, integration, rejection_path, report_pair
from flowgate.graph_validation.run_scope import run_scope
from flowgate.graph_validation.validator import validate_graph
from modules.flow_gate.graph_workflow.domain import policies, root_binding, run_policies, run_scope_ports
from modules.flow_gate.graph_workflow.domain.frontier import (
    Decision, NodeRun, RunEvent, RunState, propagate, resolve, virtual,
)
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge, GraphNode
from modules.flow_gate.graph_workflow.store import run_store


def node(key, doc_type="D", node_type="document"):
    return GraphNode(key, node_type, doc_type, key)


def graph(nodes, pairs):
    return Graph(tuple(nodes), tuple(GraphEdge(str(i), a, b) for i, (a, b) in enumerate(pairs)))


@pytest.fixture
def registered(monkeypatch):
    # Keep registration tests from changing the defaults/scope of other test modules.
    monkeypatch.setattr(policies, "_registered", dict(policies._registered))
    monkeypatch.setattr(policies, "_system_default", dict(policies._system_default))
    monkeypatch.setattr(run_policies, "_implementations", dict(run_policies._implementations))
    monkeypatch.setattr(run_scope_ports, "_handler", run_scope_ports._handler)
    integration.register()
    return run_policies.policies_of(policies.system_default_set()["entries"])


@pytest.fixture
def fork_join():
    return graph([node("r", "R"), node("a", "T"), node("b", "N"), node("j")],
                 [("r", "a"), ("r", "b"), ("a", "j"), ("b", "j")])


def state(g, **statuses):
    return RunState("running", {key: NodeRun(key, statuses.get(key, "pending")) for key in g.node_keys()})


@pytest.mark.parametrize("pairs", [
    [("r", "a"), ("a", "b"), ("b", "j")],
    [("r", "a"), ("r", "b"), ("a", "j")],
    [("r", "a"), ("r", "b"), ("a", "j"), ("b", "j")],
])
def test_registered_runs_have_no_missing_policy(registered, pairs):
    g = graph([node("r", "R"), node("a", "T"), node("b", "N"), node("j")], pairs)
    assert run_policies.undecided_for(run_policies.required_policies(g),
                                      policies.system_default_set()["entries"]) == []
    assert validate_graph(g, policies.current()) == []
    assert resolve(g, state(g), RunEvent("start"), registered).open == ["r"]


def test_excluded_structures_do_not_require_slots(registered):
    g = graph([node("r", "R"), node("d"), node("isolated", "B"), node("x", "T"),
               node("y", None, "gate"), node("z"), node("end")],
              [("r", "d"), ("x", "y"), ("x", "z"), ("y", "end"), ("z", "end")])
    g = Graph(g.nodes, g.edges + (GraphEdge("condition", "z", "y", {"if": True}),))
    assert run_scope(g).targets == {"r", "d"}
    assert run_policies.required_policies(g) == {"start_node", "completion", "rejection_path"}
    result = completion.ALL_DONE.evaluate(g, state(g, r="done", d="done"))
    assert result.complete


def test_root_binding_ignores_isolated_matching_root(registered):
    g = graph([node("isolated", "R"), node("r", "R"), node("d")], [("r", "d")])
    docs = [{"doc_id": "root", "type_code": "R"}]
    binding = root_binding.check_root_binding(g, "unassigned", docs, None)
    assert binding.node_key == "r" and binding.status == "bindable"
    stale = {"node_key": "isolated", "doc_id": "root", "doc_type": "R"}
    assert root_binding.check_root_binding(g, "graph", docs, stale).status == "blocked"


def test_multiple_starts_still_refused(registered):
    g = graph([node("r", "R"), node("b", "B"), node("d"), node("e")], [("r", "d"), ("b", "e")])
    with pytest.raises(run_policies.PolicyUndecided) as error:
        resolve(g, state(g), RunEvent("start"), registered)
    assert error.value.key == "start_node"


def test_create_run_only_inserts_scope_targets(registered, monkeypatch):
    g = graph([node("r", "R"), node("d"), node("x", "T")], [("r", "d")])
    store = Mock()
    monkeypatch.setattr(run_store, "get_store", lambda: store)
    monkeypatch.setattr(run_store, "run_by_id", lambda _: {"run_id": "run"})
    snapshot = {"source": "system", "set_id": None, "set_version": None, "digest": "d",
                "entries": policies.system_default_set()["entries"]}
    run_store.create_run("run", "flow", 1, "user", snapshot, g)
    inserts = [call.args[1][1] for call in store._execute.call_args_list
               if "INSERT INTO graph_node_runs" in call.args[0]]
    assert inserts == ["r", "d"]


@pytest.mark.parametrize("status", ["pending", "open", "review", "rejected", "closed"])
def test_waiting_is_not_completion(registered, fork_join, status):
    s = state(fork_join, r="done", a="done", b="done", j=status)
    assert not completion.ALL_DONE.is_complete(fork_join, s)


def test_empty_scope_and_active_attempts(registered, fork_join):
    empty = graph([node("isolated", "R")], [])
    assert completion.ALL_DONE.evaluate(empty, state(empty)).reason == "no_targets"
    s = state(fork_join, **dict.fromkeys(fork_join.node_keys(), "done"))
    assert completion.ALL_DONE.is_complete(fork_join, s)
    s.has_active_attempts = True
    assert completion.ALL_DONE.evaluate(fork_join, s).reason == "active_attempts"
    assert not completion.ALL_DONE.is_complete(fork_join, virtual(s, Decision()))
    assert not resolve(fork_join, s, RunEvent("resume"), registered).run_transition


def test_rejected_gate_blocks_its_descendants_and_join_only(registered):
    g = graph([node("r", "R"), node("gate", None, "gate"), node("a"), node("b"), node("j")],
              [("r", "gate"), ("r", "b"), ("gate", "a"), ("a", "j"), ("b", "j")])
    s = state(g, r="done", gate="rejected", b="done")
    rejected = propagate(g, s, "gate", "rejected", registered)
    assert rejected.open == rejected.close == []
    assert propagate(g, s, "b", "approved", registered).open == []
    # The sibling opens independently of the rejected gate.
    s = state(g, r="done", gate="rejected")
    assert propagate(g, s, "r", "approved", registered).open == ["b"]


def test_ordinary_card_rejection_does_not_call_route(registered, fork_join):
    route = Mock()
    current = {**registered, "rejection_path": route}
    assert propagate(fork_join, state(fork_join, a="review"), "a", "rejected", current).open == []
    route.route.assert_not_called()


@pytest.mark.parametrize("doc_type", ["NR", "TR", "TR2", "TSR"])
def test_standalone_report_stays_invalid(registered, doc_type):
    g = graph([node("r", "R"), node("report", doc_type)], [("r", "report")])
    assert any(i.get("structure") == "report_pair" for i in validate_graph(g, policies.current()))


def test_registration_is_idempotent_and_snapshots_resolve(registered):
    before = policies.system_default_set()["entries"]
    integration.register()
    assert before == policies.system_default_set()["entries"]
    restored = run_policies.policies_of(before)
    assert restored["completion"] is completion.ALL_DONE
    assert restored["rejection_path"] is rejection_path.STOP_REJECTED_PATH
    assert restored["report_pair"] is report_pair.INSTRUCTION_RESULT_PAIR
