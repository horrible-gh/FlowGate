"""flowgate.v02.0045 T0015 (S5) — Frontier Resolver and run policy slots, pure (no DB).

L0009 §2.6.1~§2.6.4 / §3.2 / §3.4: the node transition table, the document-argument checks
(E-20 / E-36), propagation now (running) or later (stopped, drained by start / resume in
``source_event_seq`` order), the policies a revision needs (P0008 S5-1) and the snapshot
restore rule (E-33). No D0004 §8 rule is decided, so the decided policies here are test doubles
registered under their own value; nothing in the product is changed by them.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from modules.flow_gate.graph_workflow.domain import policies as pol  # noqa: E402
from modules.flow_gate.graph_workflow.domain import run_policies as rp  # noqa: E402
from modules.flow_gate.graph_workflow.domain.frontier import (  # noqa: E402
    NodeRun,
    PendingPropagation,
    RunEvent,
    RunState,
    local_transition,
    resolve,
)
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge, GraphNode  # noqa: E402


def doc_node(key, typ, node_type="document"):
    return GraphNode(key=key, node_type=node_type, doc_type=typ if node_type == "document" else None, label=key)


def graph(nodes, edges):
    return Graph(nodes=tuple(nodes), edges=tuple(GraphEdge(f"ge_{s}_{t}", s, t, c) for s, t, *rest in edges
                                                for c in [rest[0] if rest else None]))


LINEAR = graph([doc_node("gn_r", "R"), doc_node("gn_d", "D"), doc_node("gn_p", "P")],
               [("gn_r", "gn_d"), ("gn_d", "gn_p")])


class Recorder:
    """A decided test double for every slot; records which slots were asked."""

    def __init__(self, calls, route=((), ())):
        self.calls = calls
        self._route = route

    def select(self, *args):
        if len(args) == 2:                                      # start_node.select(revision, state)
            self.calls.append("start_node")
            return args[0].start_keys()
        self.calls.append("edge_condition")                     # edge_condition.select(node, result, edges)
        return args[2]

    def admit(self, candidates, _open):
        self.calls.append("parallelism")
        return candidates

    def openable(self, _target, states):
        self.calls.append("join")
        return "open" if all(s["state"] == "completed" for s in states) else "wait"

    def route(self, *_args):
        self.calls.append("rejection_path")
        return self._route

    def is_complete(self, revision, state):
        self.calls.append("completion")
        return all((state.node(k) or NodeRun(k, "pending")).status == "done" for k in revision.node_keys())


def decided(calls=None, undecided=(), route=((), ())):
    calls = [] if calls is None else calls
    rec = Recorder(calls, route)
    return {k: (rp.UndecidedPolicy(k) if k in undecided else rec) for k in pol.POLICY_KEYS}


def state(status="running", pending=(), **nodes):
    runs = {k: NodeRun(k, *(v if isinstance(v, tuple) else (v,))) for k, v in nodes.items()}
    return RunState(status, runs, list(pending))


# ── transition table (L0009 §3.2) ────────────────────────────────────────────

def test_node_transition_table():
    assert local_transition("pending", "opened") == "open"
    assert local_transition("open", "result_submitted") == "review"
    assert local_transition("review", "review_approved") == "done"
    assert local_transition("review", "review_rejected") == "rejected"
    assert local_transition("rejected", "revised") == "review"
    assert local_transition("review", "revised") == "review"
    assert local_transition("open", "gate_confirmed") == "done"
    assert local_transition("open", "gate_rejected") == "rejected"
    for status, cause in (("done", "review_approved"), ("pending", "result_submitted"), ("open", "review_approved"),
                          ("done", "revised"), ("rejected", "review_approved")):
        assert local_transition(status, cause) is None


# ── required policies (L0009 §2.6.3, P0008 S5-1) ─────────────────────────────

def test_required_policies_match_the_p0008_s5_1_example():
    g = graph([doc_node("gn_r", "R"), doc_node("gn_n", "N"), doc_node("gn_d", "D")], [("gn_r", "gn_n"), ("gn_n", "gn_d")])
    req = rp.required_policies(g, "gn_r")
    assert req == {"start_node", "report_pair", "rejection_path", "completion"}
    entries = pol.system_default_set()["entries"]
    assert rp.undecided_for(req, entries) == [
        {"policy": "start_node", "rule_ref": "Q-1"}, {"policy": "report_pair", "rule_ref": "Q-5"},
        {"policy": "rejection_path", "rule_ref": "Q-6"}, {"policy": "completion", "rule_ref": "Q-10"},
    ]


def test_required_policies_by_structure():
    only_root = graph([doc_node("gn_r", "R")], [])
    # 0061 run scope: an isolated root is no run start, so a run targets nothing of it
    assert rp.required_policies(only_root, "gn_r") == rp.required_policies(only_root) == {"start_node", "completion"}
    # flowgate.v02.0083 T#1 (0056 T#1 개정): the bound root is a done input whatever its review status (no status
    # input); the rejection path is asked for the cards after it, which a bound run start always has
    assert rp.required_policies(LINEAR, "gn_r") == rp.required_policies(LINEAR) == {
        "start_node", "completion", "rejection_path"}
    branch = graph([doc_node("a", "D"), doc_node("b", "P"), doc_node("c", "L"), doc_node("j", "DB")],
                   [("a", "b"), ("a", "c"), ("b", "j"), ("c", "j")])
    assert {"edge_condition", "parallelism", "join"} <= rp.required_policies(branch)
    conditioned = graph([doc_node("a", "D"), doc_node("b", "P")], [("a", "b", {"on": "approved"})])
    assert {"edge_condition", "parallelism"} <= rp.required_policies(conditioned)
    special = graph([doc_node("a", "D"), doc_node("g", None, "gate"), doc_node("c", None, "custom")], [])
    assert "gate_node" in rp.required_policies(special)
    # flowgate.v02.0053 T#8: a Custom card runs as a work instruction (report_pair), never on custom_node
    assert "custom_node" not in rp.required_policies(special) and "report_pair" in rp.required_policies(special)


def test_every_system_default_policy_is_undecided_until_section_8_is_answered():
    entries = pol.system_default_set()["entries"]
    assert list(entries) == list(pol.POLICY_KEYS)
    assert all(e == {"value": "undecided", "impl_version": pol.UNDECIDED_IMPL_VERSION} for e in entries.values())
    assert pol.policy_states() == {k: "undecided" for k in pol.GRAPH_POLICY_KEYS}


# ── policy implementations and snapshots (L0009 §2.6.2, E-33) ─────────────────

def test_undecided_implementation_refuses_and_unknown_snapshot_is_not_replaced():
    impl = rp.policies_of(pol.system_default_set()["entries"])
    with pytest.raises(rp.PolicyUndecided) as exc:
        impl["start_node"].select(LINEAR, state())
    assert exc.value.key == "start_node"
    entries = dict(pol.system_default_set()["entries"])
    entries["join"] = {"value": "and_join", "impl_version": "9"}
    with pytest.raises(rp.PolicySnapshotUnavailable) as missing:
        rp.policies_of(entries)
    assert missing.value.key == "join"


def test_registration_only_adds():
    first, other = object(), object()
    rp.register("join", "domain_test_join", "1", first)
    rp.register("join", "domain_test_join", "1", first)               # same object: no-op
    with pytest.raises(ValueError):
        rp.register("join", "domain_test_join", "1", other)
    rp.register("join", "domain_test_join", "2", other)               # a change is a new version
    assert rp.implementation("join", "domain_test_join", "1") is first
    assert rp.implementation("join", "domain_test_join", "2") is other


# ── resolver (L0009 §2.6.4) ──────────────────────────────────────────────────

def test_start_opens_start_nodes_then_drains_the_root_bound_record():
    calls = []
    s = state(gn_r=("done", "R1"), gn_d="pending", gn_p="pending",
              pending=[PendingPropagation("gn_r", "approved", 2)])
    d = resolve(LINEAR, s, RunEvent("start"), decided(calls))
    assert d.open == ["gn_d"]                       # gn_r is a start node but no longer pending
    assert d.propagation_done == [PendingPropagation("gn_r", "approved", 2)]
    assert d.run_transition is None and not d.ignored
    assert "edge_condition" not in calls and "join" not in calls and "parallelism" not in calls


def test_a_linear_revision_never_asks_the_branch_join_or_parallel_slots():
    policies = decided(undecided=("edge_condition", "join", "parallelism", "report_pair", "gate_node", "custom_node"))
    s = state(gn_r=("done", "R1"), gn_d=("review", "D1"), gn_p="pending")
    d = resolve(LINEAR, s, RunEvent("review_approved", "gn_d", doc_id="D1"), policies)
    assert [(t.node, t.from_status, t.to_status, t.cause) for t in d.node_transitions] == [
        ("gn_d", "review", "done", "review_approved")]
    assert d.open == ["gn_p"]
    assert [(p.node, p.outcome, p.status) for p in d.propagation_new] == [("gn_d", "approved", "done")]


def test_branch_and_join_ask_their_slots():
    calls = []
    g = graph([doc_node("a", "D"), doc_node("b", "P"), doc_node("c", "L"), doc_node("j", "DB")],
              [("a", "b"), ("a", "c"), ("b", "j"), ("c", "j")])
    s = state(a=("review", "A1"), b="pending", c="pending", j="pending")
    d = resolve(g, s, RunEvent("review_approved", "a", doc_id="A1"), decided(calls))
    assert d.open == ["b", "c"] and {"edge_condition", "parallelism"} <= set(calls)
    s2 = state(a=("done", "A1"), b=("review", "B1"), c="open", j="pending")
    d2 = resolve(g, s2, RunEvent("review_approved", "b", doc_id="B1"), decided(calls))
    assert d2.open == [] and "join" in calls                        # c not finished: the join waits


def test_document_argument_checks_ignore_mismatches():
    policies = decided()
    s = state(gn_r=("done", "R1"), gn_d=("open", None), gn_p=("review", "P1"))
    assert resolve(LINEAR, s, RunEvent("result_submitted", "gn_d", doc_id="P1"), policies).ignored  # other node's
    assert resolve(LINEAR, s, RunEvent("review_approved", "gn_p", doc_id="OLD"), policies).ignored  # not current
    assert resolve(LINEAR, s, RunEvent("revised", "gn_p", doc_id="P2", replaces="X"), policies).ignored
    assert resolve(LINEAR, s, RunEvent("review_approved", "gn_d", doc_id="D1"), policies).ignored   # open, no result
    assert resolve(LINEAR, s, RunEvent("review_approved", "nope", doc_id="D1"), policies).ignored   # no node
    ok = resolve(LINEAR, s, RunEvent("result_submitted", "gn_d", doc_id="D1"), policies)
    t = ok.node_transitions[0]
    assert (t.from_status, t.to_status, t.expected_doc_id, t.result_doc_id) == ("open", "review", None, "D1")
    assert ok.open == [] and ok.propagation_new == []
    revised = resolve(LINEAR, s, RunEvent("revised", "gn_p", doc_id="P2", replaces="P1"), policies)
    assert revised.node_transitions[0].result_doc_id == "P2" and revised.node_transitions[0].to_status == "review"


def test_a_stopped_run_records_results_without_propagating():
    s = state("stopped", gn_r=("done", "R1"), gn_d=("review", "D1"), gn_p="pending")
    d = resolve(LINEAR, s, RunEvent("review_approved", "gn_d", doc_id="D1"), decided())
    assert d.open == [] and d.run_transition is None
    assert [(p.node, p.outcome, p.status) for p in d.propagation_new] == [("gn_d", "approved", "pending")]


def test_resume_drains_pending_records_in_event_order():
    calls = []
    g = graph([doc_node("a", "D"), doc_node("b", "P"), doc_node("x", "L"), doc_node("y", "DB")],
              [("a", "x"), ("b", "y")])
    s = state(a=("done", "A1"), b=("rejected", "B1"), x="pending", y="pending",
              pending=[PendingPropagation("b", "rejected", 5), PendingPropagation("a", "approved", 3)])
    d = resolve(g, s, RunEvent("resume"), decided(calls))
    assert [p.source_event_seq for p in d.propagation_done] == [3, 5]
    assert d.open == ["x"]
    assert calls == ["rejection_path", "completion"]               # a (seq 3) needs no slot, then b (seq 5)


def test_rejection_follows_the_rejection_path_slot():
    s = state(gn_r=("done", "R1"), gn_d=("review", "D1"), gn_p="pending")
    waiting = resolve(LINEAR, s, RunEvent("review_rejected", "gn_d", doc_id="D1"), decided())
    assert waiting.node_transitions[0].to_status == "rejected" and waiting.open == [] and waiting.close == []
    back = resolve(LINEAR, s, RunEvent("review_rejected", "gn_d", doc_id="D1"), decided(route=(["gn_p"], [])))
    assert back.open == ["gn_p"]
    with pytest.raises(rp.PolicyUndecided):
        resolve(LINEAR, s, RunEvent("review_rejected", "gn_d", doc_id="D1"), decided(undecided=("rejection_path",)))


def test_the_last_approval_completes_the_run():
    s = state(gn_r=("done", "R1"), gn_d=("done", "D1"), gn_p=("review", "P1"))
    d = resolve(LINEAR, s, RunEvent("review_approved", "gn_p", doc_id="P1"), decided())
    assert d.run_transition == "completed" and d.open == []


def test_a_repeated_event_is_ignored():
    s = state(gn_r=("done", "R1"), gn_d=("done", "D1"), gn_p="pending")
    assert resolve(LINEAR, s, RunEvent("review_approved", "gn_d", doc_id="D1"), decided()).ignored
