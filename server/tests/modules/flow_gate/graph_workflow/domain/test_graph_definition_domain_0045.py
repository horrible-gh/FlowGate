"""flowgate.v02.0045 T0013 (S3+S4) — pure Graph definition domain.

P0008 §5.1 / S3-4 (draft request checks), S3-5 (issue table, structure table, order),
L0009 §2.4 (root binding), §2.5 (validator, SCC, order), §2.7 (layered auto placement).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from modules.flow_gate.graph_workflow.domain import draft_request as dr  # noqa: E402
from modules.flow_gate.graph_workflow.domain import policies  # noqa: E402
from modules.flow_gate.graph_workflow.domain.auto_layout import layered_depths, layout  # noqa: E402
from modules.flow_gate.graph_workflow.domain.graph_model import Graph, GraphEdge, GraphNode  # noqa: E402
from modules.flow_gate.graph_workflow.domain.graph_validator import strongly_connected, validate_graph  # noqa: E402
from modules.flow_gate.graph_workflow.domain.root_binding import check_root_binding  # noqa: E402

UNDECIDED = policies.current()


def doc_node(key, doc_type, label=None):
    return GraphNode(key=key, node_type="document", doc_type=doc_type, label=label or doc_type)


def graph(nodes, edges):
    return Graph(nodes=tuple(nodes), edges=tuple(GraphEdge(e, s, t) for e, s, t in edges))


def codes(issues):
    return [(i["code"], i["structure"]) for i in issues]


# ── draft request (P0008 S3-4) ───────────────────────────────────────────────

def test_s3_4_example_reports_shape_errors_in_request_order():
    req = dr.parse_draft({
        "draft_version": 4,
        "nodes": [
            {"node_key": None, "client_key": "tmp_2", "node_type": "document", "doc_type": None,
             "palette_item_id": "task", "label": "구현", "assignee": None},
            {"node_key": "gn_zz9", "client_key": None, "node_type": "document", "doc_type": "D",
             "palette_item_id": None, "label": "설계", "assignee": None},
        ],
        "edges": [{"edge_id": None, "client_key": "tmp_e9", "source": "tmp_2", "target": "tmp_404", "condition": None}],
    })
    assert req.draft_version == 4
    errors = req.errors + dr.unknown_keys(req, node_keys=["gn_a1"], edge_ids=[])
    assert dr.sort_errors(errors) == [
        {"field": "nodes[0].doc_type", "reason": "required_for_document_node"},
        {"field": "nodes[1].node_key", "reason": "not_in_draft_or_revision"},
        {"field": "edges[0].target", "reason": "unknown_node_reference"},
    ]


@pytest.mark.parametrize("node, field, reason", [
    ({"client_key": "tmp_1", "node_type": "gate", "doc_type": "D", "label": "x"}, "nodes[0].doc_type",
     "must_be_null_for_non_document"),
    ({"client_key": "tmp_1", "node_type": "memo", "doc_type": None, "label": "x"}, "nodes[0].node_type",
     "not_one_of:document,gate,custom"),
    ({"client_key": "k1", "node_type": "document", "doc_type": "D", "label": "x"}, "nodes[0].client_key",
     "invalid_client_key"),
    ({"client_key": "tmp_1", "node_type": "document", "doc_type": "D", "label": "  "}, "nodes[0].label", "label_length"),
    ({"client_key": "tmp_1", "node_type": "document", "doc_type": "D", "label": "x" * 201}, "nodes[0].label",
     "label_length"),
    ({"client_key": "tmp_1", "node_type": "document", "doc_type": "D", "label": "x",
      "assignee": {"kind": "ai", "provider_id": None, "user_id": "u"}}, "nodes[0].assignee", "invalid_assignee"),
    ({"client_key": "tmp_1", "node_type": "document", "doc_type": "D", "label": "x",
      "assignee": {"kind": "bot"}}, "nodes[0].assignee", "invalid_assignee"),
    ({"node_key": None, "client_key": None, "node_type": "document", "doc_type": "D", "label": "x"},
     "nodes[0].node_key", "required"),
])
def test_node_format_reasons(node, field, reason):
    req = dr.parse_draft({"draft_version": 0, "nodes": [node], "edges": []})
    assert {"field": field, "reason": reason} in req.errors


def test_duplicate_keys_across_nodes_and_edges_and_limits():
    node = {"client_key": "tmp_1", "node_type": "document", "doc_type": "D", "label": "x"}
    req = dr.parse_draft({"draft_version": 0, "nodes": [node, dict(node)],
                          "edges": [{"client_key": "tmp_1", "source": "tmp_1", "target": "tmp_1"}]})
    assert {"field": "nodes[1].client_key", "reason": "duplicate_key"} in req.errors
    assert {"field": "edges[0].client_key", "reason": "duplicate_key"} in req.errors
    too_many = dr.parse_draft({"draft_version": 0, "nodes": [node] * 301, "edges": []})
    assert too_many.errors == [{"field": "nodes", "reason": "too_many_items"}]
    edges = dr.parse_draft({"draft_version": 0, "nodes": [], "edges": [{}] * 601})
    assert edges.errors == [{"field": "edges", "reason": "too_many_items"}]


def test_version_and_doc_type_checks():
    assert dr.parse_version({"draft_version": -1})[1] == [{"field": "draft_version", "reason": "negative"}]
    assert dr.parse_version({"draft_version": True})[1] == [{"field": "draft_version", "reason": "not_an_integer"}]
    assert dr.parse_version({"draft_version": 0}, minimum=1)[1] == [{"field": "draft_version", "reason": "not_positive"}]
    req = dr.parse_draft({"draft_version": 0, "edges": [], "nodes": [
        {"client_key": "tmp_1", "node_type": "document", "doc_type": "zz", "label": "x"}]})
    assert req.nodes[0].doc_type == "ZZ"
    assert dr.unknown_doc_types(req, ["D", "R"]) == [{"field": "nodes[0].doc_type", "reason": "invalid_doc_type"}]


# ── validator (L0009 §2.5, P0008 S3-5) ───────────────────────────────────────

def test_s3_5_example_order_branch_join_gate():
    g = Graph(
        nodes=(doc_node("gn_a1", "R"), doc_node("gn_a2", "D"), doc_node("gn_a3", "T"), doc_node("gn_a4", "T"),
               GraphNode(key="gn_a5", node_type="gate", doc_type=None, label="확인", palette_item_id="confirm")),
        edges=tuple(GraphEdge(*e) for e in [("ge_a1", "gn_a1", "gn_a2"), ("ge_a2", "gn_a2", "gn_a3"),
                                            ("ge_a3", "gn_a2", "gn_a4"), ("ge_a4", "gn_a3", "gn_a5"),
                                            ("ge_a5", "gn_a4", "gn_a5")]),
    )
    issues = validate_graph(g, UNDECIDED)
    assert codes(issues) == [("rule_undecided", "branch"), ("rule_undecided", "join"), ("rule_undecided", "gate_node")]
    assert [(i["rule_ref"], i["node_keys"], i["edge_ids"]) for i in issues] == [
        ("Q-2", ["gn_a2"], ["ge_a2", "ge_a3"]),
        ("Q-4", ["gn_a5"], ["ge_a4", "ge_a5"]),
        ("Q-11", ["gn_a5"], []),
    ]
    assert all(i["severity"] == "error" for i in issues)


def test_linear_palette_graph_is_confirmable():
    g = graph([doc_node("gn_1", "R"), doc_node("gn_2", "D"), doc_node("gn_3", "T")],
              [("ge_1", "gn_1", "gn_2"), ("ge_2", "gn_2", "gn_3")])
    assert validate_graph(g, UNDECIDED) == []


def test_empty_dangling_duplicate_and_unreachable():
    assert codes(validate_graph(Graph(), UNDECIDED)) == [("empty_graph", None)]
    g = graph([doc_node("gn_1", "R"), doc_node("gn_2", "D")],
              [("ge_1", "gn_1", "gn_2"), ("ge_2", "gn_1", "gn_2"), ("ge_3", "gn_2", "gn_9")])
    issues = validate_graph(g, UNDECIDED)
    assert ("duplicate_edge", None) in codes(issues) and ("dangling_edge", None) in codes(issues)
    dup = next(i for i in issues if i["code"] == "duplicate_edge")
    assert dup["node_keys"] == ["gn_1", "gn_2"] and dup["edge_ids"] == ["ge_1", "ge_2"]
    # the duplicate pair is also a branch and a join (undecided rules)
    assert ("rule_undecided", "branch") in codes(issues) and ("rule_undecided", "join") in codes(issues)
    # edge-only issue (dangling) is ordered after the node issues
    assert issues[-1]["code"] == "dangling_edge"


def test_cycle_without_start_is_unreachable_and_cycle():
    """L0009 E-17: every node in a cycle -> unreachable_node each + one cycle issue."""
    g = graph([doc_node("gn_1", "D"), doc_node("gn_2", "T")], [("ge_1", "gn_1", "gn_2"), ("ge_2", "gn_2", "gn_1")])
    issues = validate_graph(g, UNDECIDED)
    assert codes(issues) == [("unreachable_node", None), ("rule_undecided", "cycle"), ("unreachable_node", None)]
    cycle = next(i for i in issues if i["structure"] == "cycle")
    assert cycle["rule_ref"] == "Q-6" and cycle["node_keys"] == ["gn_1", "gn_2"] and cycle["edge_ids"] == ["ge_1", "ge_2"]


def test_self_loop_report_custom_condition_and_non_palette():
    g = Graph(
        nodes=(doc_node("gn_1", "R"), doc_node("gn_2", "NR"), doc_node("gn_3", "DS"),
               GraphNode(key="gn_4", node_type="custom", doc_type=None, label="메모", palette_item_id="custom.3")),
        edges=(GraphEdge("ge_1", "gn_1", "gn_2"), GraphEdge("ge_2", "gn_2", "gn_2"),
               GraphEdge("ge_3", "gn_2", "gn_3", {"when": "approved"}), GraphEdge("ge_4", "gn_3", "gn_4")),
    )
    found = codes(validate_graph(g, UNDECIDED))
    for structure in ("report_pair", "cycle", "edge_condition", "non_palette_doc_type", "branch"):
        assert ("rule_undecided", structure) in found, structure
    # flowgate.v02.0053 T#8: a Custom card is a plain document card, not an undecided structure
    assert ("rule_undecided", "custom_node") not in found


def test_multiple_start_is_one_issue_and_a_decided_policy_is_consulted():
    g = graph([doc_node("gn_1", "R"), doc_node("gn_2", "D"), doc_node("gn_3", "T")],
              [("ge_1", "gn_1", "gn_3"), ("ge_2", "gn_2", "gn_3")])
    issues = validate_graph(g, UNDECIDED)
    start = [i for i in issues if i["structure"] == "multiple_start"]
    assert len(start) == 1 and start[0]["node_keys"] == ["gn_1", "gn_2"] and start[0]["rule_ref"] == "Q-1"

    class Allow:
        key = "allow"

        def validate(self, _g, _occurrence):
            return []

    decided = {**UNDECIDED, "start_node": Allow(), "join": Allow()}
    assert validate_graph(g, decided) == []


def test_strongly_connected_components():
    adj = {"a": ["b"], "b": ["c"], "c": ["a"], "d": ["d"], "e": []}
    comps = sorted(strongly_connected(["a", "b", "c", "d", "e"], adj))
    assert comps == [["a", "b", "c"], ["d"], ["e"]]


def test_policy_slots_are_all_undecided():
    assert set(policies.current()) == set(policies.POLICY_KEYS) and len(policies.POLICY_KEYS) == 9
    assert policies.policy_states() == {k: "undecided" for k in (
        "start_node", "edge_condition", "parallelism", "join", "report_pair", "rejection_path", "completion")}


# ── root binding (L0009 §2.4) ────────────────────────────────────────────────

R1 = {"doc_id": "f.0001-R", "type_code": "R"}
LINEAR = graph([doc_node("gn_r", "R"), doc_node("gn_d", "D")], [("ge_1", "gn_r", "gn_d")])


def test_binding_unassigned_cases():
    ok = check_root_binding(LINEAR, "unassigned", [R1, {"doc_id": "f.0002-N", "type_code": "N"}], None)
    assert (ok.status, ok.node_key, ok.doc_id, ok.issues) == ("bindable", "gn_r", "f.0001-R", [])
    assert check_root_binding(LINEAR, "unassigned", [], None).status == "not_needed"
    two = check_root_binding(LINEAR, "unassigned", [R1, {"doc_id": "f.0004-R", "type_code": "R"}], None)
    assert two.status == "blocked" and two.issues[0]["code"] == "root_binding_ambiguous_root"
    assert two.issues[0]["node_keys"] == ["doc:f.0001-R", "doc:f.0004-R"]
    assert two.preview() == {"status": "blocked", "node_key": None, "doc_id": None, "reason": "root_binding_ambiguous_root"}
    bug = check_root_binding(LINEAR, "unassigned", [{"doc_id": "f.0001-B", "type_code": "B"}], None)
    assert bug.issues[0]["code"] == "root_binding_no_start_node" and bug.issues[0]["node_keys"] == ["doc:f.0001-B"]
    twin = graph([doc_node("gn_r1", "R"), doc_node("gn_r2", "R")], [])
    amb = check_root_binding(twin, "unassigned", [R1], None)
    assert amb.issues[0]["code"] == "root_binding_ambiguous_start" and amb.issues[0]["node_keys"] == ["gn_r1", "gn_r2"]


def test_binding_graph_cases():
    record = {"node_key": "gn_r", "doc_id": "f.0001-R", "doc_type": "R"}
    kept = check_root_binding(LINEAR, "graph", [], record, lambda d: "f.0009-R")
    assert (kept.status, kept.node_key, kept.doc_id) == ("inherited", "gn_r", "f.0009-R")
    assert check_root_binding(LINEAR, "graph", [], None).status == "not_needed"
    removed = check_root_binding(graph([doc_node("gn_d", "D")], []), "graph", [], record)
    assert removed.issues[0]["code"] == "root_binding_start_removed"
    assert removed.issues[0]["node_keys"] == [] and removed.issues[0]["removed_node_key"] == "gn_r"
    not_start = graph([doc_node("gn_x", "N"), doc_node("gn_r", "R")], [("ge_1", "gn_x", "gn_r")])
    changed = check_root_binding(not_start, "graph", [], record)
    assert changed.issues[0]["node_keys"] == ["gn_r"] and changed.issues[0]["removed_node_key"] == "gn_r"
    retyped = check_root_binding(graph([doc_node("gn_r", "D")], []), "graph", [], record)
    assert retyped.status == "blocked"


# ── layered auto placement (L0009 §2.7) ──────────────────────────────────────

def test_layered_depth_uses_longest_path_and_survives_cycles():
    assert layered_depths(["a", "b", "c"], [("a", "b"), ("b", "c"), ("a", "c")]) == {"a": 0, "b": 1, "c": 2}
    assert layered_depths(["x", "y"], [("x", "y"), ("y", "x")]) == {"x": 0, "y": 1}


def test_documents_projection_places_draft_layers_below_documents():
    placed = layout("documents", free_docs=["doc:r"], graph_nodes=["gn_a1", "gn_a2", "gn_a3"],
                    graph_edges=[("gn_a1", "gn_a2"), ("gn_a1", "gn_a3")], saved={})
    assert (placed["doc:r"]["x"], placed["doc:r"]["y"]) == (0, 0)
    assert (placed["gn_a1"]["x"], placed["gn_a1"]["y"]) == (0, 320)
    assert (placed["gn_a2"]["x"], placed["gn_a2"]["y"]) == (260, 320)
    assert (placed["gn_a3"]["x"], placed["gn_a3"]["y"]) == (260, 460)


def test_graph_projection_puts_free_documents_under_the_graph():
    placed = layout("graph", free_docs=["doc:m"], graph_nodes=["gn_1", "gn_2"], graph_edges=[("gn_1", "gn_2")],
                    saved={"gn_2": (500.0, 300.0)})
    assert placed["gn_2"] == {"x": 500.0, "y": 300.0, "source": "saved"}
    assert (placed["doc:m"]["x"], placed["doc:m"]["y"]) == (0, 500)
