"""flowgate.v02.0045 T0011 (S1+S2) — pure Graph Workflow domain rules.

Kind / state / editable tables (P0008 §2.2·§2.3·§2.6), ownership (D0004 §3.1), auto layout
(L0009 §2.7), rematch stages and tie rules (L0009 §2.3.4~§2.3.6), supersede handoff
(L0009 §2.3.7) and the layout request shape (P0008 S2-3).
"""
from __future__ import annotations

import itertools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.domain.auto_layout import layout
from modules.flow_gate.graph_workflow.domain.layout_request import LAYOUT_POSITIONS_MAX, NODE_KEY_MAX_LEN, parse_layout
from modules.flow_gate.graph_workflow.domain.ownership import judge_ownership
from modules.flow_gate.graph_workflow.domain.rematch import lcs_pairs, linear_pairs, rematch, same_type_and_label
from modules.flow_gate.graph_workflow.domain.supersede import chain_reaches, live_end, pending_handoffs

FLOW = "flowgate.v02.0051"


# ── P0008 §2.2 / §2.3 / §2.5 / §2.6 ─────────────────────────────────────────

@pytest.mark.parametrize("type_code,kind", [
    ("R", "requirement"), ("b", "requirement"), ("DB", "design"), ("NR", "research"), ("WP", "plan"),
    ("TR2", "task"), ("V", "test"), ("DC", "approval"), ("A", "memo"), ("ZZ", "unknown"), (None, "unknown"),
])
def test_kind_table(type_code, kind):
    assert cm.kind_for_type(type_code) == kind


@pytest.mark.parametrize("review,type_code,state", [
    ("approved", "R", "done"), ("wf_done", "R", "done"), ("pending_review", "N", "review"),
    ("revised", "D", "review"), ("rejected", "D", "blocked"), ("wf_in_progress", "R", "in_progress"),
    (None, "M", "done"), (None, "CH", "done"), (None, "Q", "in_progress"), ("weird", "D", None),
])
def test_document_state_table(review, type_code, state):
    assert cm.document_state(review, type_code) == state


def test_slot_without_result_is_draft():
    assert cm.slot_state(None, "approved", "D") == "draft"
    assert cm.slot_state("x", "approved", "D") == "done"


def test_alive_rule():
    assert cm.is_alive({"group_id": FLOW, "status": "open"}, FLOW)
    assert not cm.is_alive({"group_id": FLOW, "status": "cancelled"}, FLOW)
    assert not cm.is_alive({"group_id": FLOW, "status": "open", "superseded_by": "x"}, FLOW)
    assert not cm.is_alive({"group_id": "other", "status": "open"}, FLOW)


def test_collapse_query_keeps_order_and_ignores_unknown():
    assert cm.parse_collapse("conversation,memo,archive, memo") == (["conversation", "memo"], ["archive"])
    assert cm.parse_collapse(None) == ([], [])


def test_editable_table_for_s1_s2_nodes():
    for state in cm.OWNERSHIP_STATES:
        assert cm.editable_for(state, "document") == {"move": True, "structure": False}
        assert cm.editable_for(state, "sequence_slot") == {"move": True, "structure": False}
    assert cm.editable_for("unassigned", "draft_node") == {"move": True, "structure": True}


def test_ownership_truth_table():
    assert judge_ownership(False, False) == "unassigned"
    assert judge_ownership(False, True) == "v01"
    assert judge_ownership(True, False) == "graph"
    assert judge_ownership(True, True) == "conflict"


# ── L0009 §2.7 auto layout ──────────────────────────────────────────────────

def test_documents_layout_matches_p0008_s1_1():
    keys = ["R", "N", "M", "Q", "D"]
    placed = layout("documents", free_docs=keys)
    assert [(placed[k]["x"], placed[k]["y"], placed[k]["source"]) for k in keys] == [
        (0, 0, "auto"), (260, 0, "auto"), (520, 0, "auto"), (780, 0, "auto"), (1040, 0, "auto"),
    ]


def test_sequence_layout_matches_p0008_s1_2_and_follows_saved_previous():
    placed = layout("sequence", chain=["R", "N", "NR", "D"], free_docs=["M"])
    assert [(placed[k]["x"], placed[k]["y"]) for k in ["R", "N", "NR", "D", "M"]] == [
        (0, 0), (260, 0), (520, 0), (780, 0), (260, 180),
    ]
    moved = layout("sequence", chain=["R", "N", "P"], saved={"N": (300.0, 40.0)})
    assert moved["N"] == {"x": 300.0, "y": 40.0, "source": "saved"}
    assert moved["P"] == {"x": 560.0, "y": 40.0, "source": "auto"}


def test_collision_pushes_auto_node_down():
    placed = layout("documents", free_docs=["A", "B"], saved={"A": (250.0, 10.0)})
    assert placed["B"] == {"x": 260, "y": 120, "source": "auto"}


# ── L0009 §2.3.4~§2.3.6 rematch ─────────────────────────────────────────────

def _prev(key, item_id, item_seq, typ, label, order, source="sequence_slot", card=None, result=None):
    return {"node_key": key, "identity_source": source, "item_id": item_id, "item_seq": item_seq,
            "item_type": typ, "label": label, "sort_order": order, "source_wp_card_id": card,
            "result_doc_id": result}


def _cur(item_id, item_seq, typ, label, order, card=None, result=None):
    return {"id": item_id, "item_seq": item_seq, "type": typ, "label": label, "sort_order": order,
            "source_wp_card_id": card, "result_doc_id": result}


def _keys(result):
    return {a.row["id"]: a.node_key for a in result.assignments}


def _slot_keys():
    counter = itertools.count(1)
    return lambda: f"slot_new{next(counter)}"


def test_p0008_s2_4_rematch_by_card_and_new_row():
    prev = [
        _prev("doc:doc-N", 4811, 1, "N", "조사", 0, "document", result="doc-N"),
        _prev("doc:doc-NR", 4812, 2, "NR", "조사 결과", 1, "document", result="doc-NR"),
        _prev("slot_q7", 4820, 5, "D", "정렬 기본설계", 2, card="c_k0"),
    ]
    cur = [
        _cur(4811, 1, "N", "조사", 0, result="doc-N"),
        _cur(4812, 2, "NR", "조사 결과", 1, result="doc-NR"),
        _cur(4831, 6, "P", "정렬 API 프로토콜", 2),
        _cur(4832, 7, "D", "정렬 기본설계", 3, card="c_k0"),
    ]
    result = rematch(prev, cur, {4811, 4812}, new_slot_key=_slot_keys())
    assert _keys(result) == {4811: "doc:doc-N", 4812: "doc:doc-NR", 4831: "slot_new1", 4832: "slot_q7"}
    assert result.retired_keys == []
    assert result.handoffs == []


def test_started_slot_hands_its_identity_to_the_document_then_retires():
    prev = [_prev("slot_a", 10, 3, "D", "설계", 0)]
    cur = [_cur(10, 3, "D", "설계", 0, result="doc-D")]
    result = rematch(prev, cur, {10}, new_slot_key=_slot_keys())
    assert _keys(result) == {10: "doc:doc-D"}
    assert result.handoffs == [("slot_a", "doc:doc-D")]


def test_protected_report_row_keeps_row_identity():
    prev = [_prev("slot_tr", 21, 4, "TR", "보고", 1)]
    cur = [_cur(20, 3, "T", "구현", 0, result="doc-T"), _cur(21, 4, "TR", "보고", 1)]
    result = rematch(prev, cur, {20, 21}, new_slot_key=_slot_keys())
    assert _keys(result) == {20: "doc:doc-T", 21: "row:21"}
    assert result.handoffs == [("slot_tr", "row:21")]


def test_superseded_result_document_is_followed():
    prev = [_prev("doc:old", 30, 1, "D", "설계", 0, "document", result="old")]
    cur = [_cur(30, 1, "D", "설계", 0, result="new")]
    docs = {"old": {"doc_id": "old", "superseded_by": "new"}, "new": {"doc_id": "new"}}
    result = rematch(prev, cur, {30}, new_slot_key=_slot_keys(),
                     supersede_reaches=lambda a, b: chain_reaches(a, b, docs))
    assert result.handoffs == [("doc:old", "doc:new")]


def test_tie_rules():
    # Two recorded, one current with the same type+label: the earlier recorded one pairs.
    prev = [_prev("slot_1", 1, 1, "D", "설계", 0), _prev("slot_2", 2, 2, "D", "설계", 1)]
    result = rematch(prev, [_cur(9, 3, "D", "설계", 0)], set(), new_slot_key=_slot_keys())
    assert _keys(result) == {9: "slot_1"} and result.retired_keys == ["slot_2"]
    # One recorded, two current: the earlier current row pairs, the later one is new.
    prev = [_prev("slot_1", 1, 1, "D", "설계", 0)]
    result = rematch(prev, [_cur(8, 2, "D", "설계", 0), _cur(9, 3, "D", "설계", 1)], set(), new_slot_key=_slot_keys())
    assert _keys(result) == {8: "slot_1", 9: "slot_new1"}
    # A changed label is a new node; the old identity retires.
    result = rematch(prev, [_cur(8, 2, "D", "다른 설계", 0)], set(), new_slot_key=_slot_keys())
    assert _keys(result) == {8: "slot_new1"} and result.retired_keys == ["slot_1"]


def test_first_record_gives_every_pending_row_a_new_slot_key():
    cur = [_cur(1, 1, "N", "조사", 0, result="doc-N"), _cur(2, 2, "NR", "결과", 1), _cur(3, 3, "D", "설계", 2)]
    result = rematch([], cur, {1, 2}, new_slot_key=_slot_keys())
    assert _keys(result) == {1: "doc:doc-N", 2: "row:2", 3: "slot_new1"}


def test_lcs_and_linear_fallback_agree_on_simple_input():
    ps = [{"item_type": t, "label": t} for t in "ABCD"]
    cs = [{"type": t, "label": t} for t in "AXCD"]
    expected = [("A", "A"), ("C", "C"), ("D", "D")]
    for pairs in (lcs_pairs(ps, cs, same_type_and_label), linear_pairs(ps, cs, same_type_and_label)):
        assert [(p["label"], c["label"]) for p, c in pairs] == expected


# ── L0009 §2.3.7 supersede handoff ──────────────────────────────────────────

def _doc(doc_id, superseded_by=None, status="open", group=FLOW):
    return {"doc_id": doc_id, "superseded_by": superseded_by, "status": status, "group_id": group}


def test_handoff_goes_to_the_live_end_ordered_by_hops():
    docs = {d["doc_id"]: d for d in [_doc("a", "b"), _doc("b", "c"), _doc("c")]}
    assert live_end(FLOW, docs["a"], docs) == (docs["c"], 2)
    out = pending_handoffs(FLOW, docs, {"doc:a", "doc:b"}, set())
    assert out == [{"from_key": "doc:b", "to_key": "doc:c", "hops": 1},
                   {"from_key": "doc:a", "to_key": "doc:c", "hops": 2}]
    assert pending_handoffs(FLOW, docs, {"doc:a"}, {"doc:a"}) == []


def test_handoff_skips_cancelled_foreign_and_cyclic_chains():
    docs = {d["doc_id"]: d for d in [
        _doc("a", "b"), _doc("b", status="cancelled"),
        _doc("x", "y"), _doc("y", group="other"),
        _doc("p", "q"), _doc("q", "p"),
    ]}
    assert pending_handoffs(FLOW, docs, {"doc:a", "doc:x", "doc:p"}, set()) == []


# ── P0008 S2-3 layout request ───────────────────────────────────────────────

def test_layout_request_rules():
    assert parse_layout({"positions": [], "viewport": None}).only_empty
    bad = parse_layout({"positions": [{"node_key": "doc:a", "x": "320", "y": 60}], "viewport": {"x": 0, "y": 0, "zoom": 0}})
    assert bad.errors == [{"field": "positions[0].x", "reason": "not_a_number"},
                          {"field": "viewport.zoom", "reason": "not_positive"}]
    too_many = parse_layout({"positions": [{"node_key": "k", "x": 1, "y": 1}] * (LAYOUT_POSITIONS_MAX + 1)})
    assert too_many.errors == [{"field": "positions", "reason": "too_many_items"}]
    nan = parse_layout({"positions": [{"node_key": "k", "x": float("nan"), "y": 1}]})
    assert nan.errors == [{"field": "positions[0].x", "reason": "not_a_number"}]
    good = parse_layout({"positions": [{"node_key": "k", "x": 1, "y": 2.5}], "viewport": {"x": -40, "y": 12, "zoom": 0.85}})
    assert not good.errors and good.positions == [{"node_key": "k", "x": 1.0, "y": 2.5}]
    assert good.viewport == {"x": -40.0, "y": 12.0, "zoom": 0.85}
    assert parse_layout({"viewport": {"x": 0, "y": 0, "zoom": 1}}).errors == []


def test_node_key_limit_is_the_p0008_contract_120():
    # P0008 §1.6 / L0009 §1.4 E-15: 120 characters pass, 121 (and the 200 column width) do not.
    assert NODE_KEY_MAX_LEN == 120
    assert parse_layout({"positions": [{"node_key": "k" * 120, "x": 1, "y": 1}]}).errors == []
    for length in (121, 200):
        req = parse_layout({"positions": [{"node_key": "k" * length, "x": 1, "y": 1}]})
        assert req.errors == [{"field": "positions[0].node_key", "reason": "too_long"}] and req.positions == []
