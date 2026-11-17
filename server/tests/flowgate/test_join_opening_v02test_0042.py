"""Join safety across confirmed revisions, without changing command scope."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from flowgate.graph_run import scheduling, settle
from modules.flow_gate.graph_workflow.domain import frontier
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphNode, GraphEdge
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.graph_workflow.store import definition_store


@pytest.fixture
def join(monkeypatch):
    nodes = tuple(GraphNode(k, "document", "T", k) for k in ("a", "b", "c", "independent"))
    graph = Graph(nodes, (GraphEdge("ac", "a", "c"),))
    state = frontier.RunState("running", {k: frontier.NodeRun(k, s) for k, s in
                                        (("a", "done"), ("b", "open"), ("c", "pending"),
                                         ("independent", "pending"))})
    ctx = SimpleNamespace(run={"status": "running", "revision_no": 1}, revision=graph,
                          flow_id="flow", run_id="run", command_pending=False)
    record = {"nodes": [{"node_key": n.key} for n in nodes],
              "edges": [{"source": "a", "target": "c"}, {"source": "b", "target": "c"}]}
    monkeypatch.setattr(frontier, "run_scope", lambda g: SimpleNamespace(targets=set(g.node_keys())))
    monkeypatch.setattr(run_apply, "read_run_state", lambda _: state)
    monkeypatch.setattr(definition_store, "latest_revision_no", lambda _: 2)
    monkeypatch.setattr(definition_store, "revision", lambda *_: record)
    return ctx, state, record


@pytest.mark.parametrize("status", ["pending", "open", "review", "rejected", "closed", None])
@pytest.mark.parametrize("path", ["opening", "refill"])
def test_added_predecessor_must_finish_on_every_opening_path(join, status, path):
    ctx, state, _ = join
    if status is None:
        state.node_runs.pop("b")
    else:
        state.node_runs["b"] = frontier.NodeRun("b", status)
    found = scheduling.opening(ctx, ["c", "independent"]) if path == "opening" else settle._candidates(ctx, state)
    assert "c" not in found
    assert "independent" in found
    assert ctx.run["revision_no"] == 1


def test_last_predecessor_releases_join_once_and_preserves_step_scope(join):
    ctx, state, _ = join
    state.mode, state.step_targets = "step", frozenset({"c"})
    assert scheduling.opening(ctx, ["c", "independent"]) == []
    state.node_runs["b"] = frontier.NodeRun("b", "done")
    assert scheduling.opening(ctx, ["c", "c", "independent"]) == ["c"]
    state.node_runs["c"] = frontier.NodeRun("c", "open")
    assert scheduling.opening(ctx, ["c"]) == []
    assert state.step_targets == frozenset({"c"})
    assert ctx.run["revision_no"] == 1


def test_saved_decision_cannot_create_attempt_or_dispatch_before_join(join, monkeypatch):
    ctx, state, _ = join
    attempt, dispatch, opened = Mock(), Mock(), Mock()
    monkeypatch.setattr(run_apply.run_store, "open_attempt", attempt)
    monkeypatch.setattr(run_apply.node_dispatch, "dispatch", dispatch)
    monkeypatch.setattr(run_apply.run_store, "open_node_run", opened)
    def hook(action, *args):
        if action == "opening":
            return scheduling.opening(*args)
        return action == "can_open"
    monkeypatch.setattr(run_apply.graph_execution_hooks, "call", hook)
    run_apply.apply_decision(ctx, frontier.Decision(open=["c"]), settle=False)
    attempt.assert_not_called()
    dispatch.assert_not_called()
    opened.assert_not_called()


def test_unreadable_new_revision_does_not_allow_stale_opening(join, monkeypatch):
    ctx, _, _ = join
    monkeypatch.setattr(definition_store, "revision", lambda *_: None)
    assert scheduling.opening(ctx, ["c"]) == []


def test_removed_target_is_not_opened_from_old_decision(join):
    ctx, _, record = join
    record["nodes"] = [n for n in record["nodes"] if n["node_key"] != "c"]
    assert scheduling.opening(ctx, ["c"]) == []


def test_old_revision_dependencies_remain_until_explicit_adoption(join):
    ctx, state, record = join
    state.node_runs["a"] = frontier.NodeRun("a", "review")
    state.node_runs["b"] = frontier.NodeRun("b", "done")
    record["edges"] = [{"source": "b", "target": "c"}]
    assert scheduling.opening(ctx, ["c"]) == []


def test_current_revision_keeps_existing_join_rule(join, monkeypatch):
    ctx, state, _ = join
    ctx.revision = Graph(ctx.revision.nodes, (*ctx.revision.edges, GraphEdge("bc", "b", "c")))
    monkeypatch.setattr(definition_store, "latest_revision_no", lambda _: 1)
    assert scheduling.opening(ctx, ["c"]) == []
    state.node_runs["b"] = frontier.NodeRun("b", "done")
    assert scheduling.opening(ctx, ["c"]) == ["c"]
