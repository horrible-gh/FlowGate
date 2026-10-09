"""flowgate.v02.0045 T0011 (S1+S2) — legacy projector keeps only live documents (P0008 §2.4).

The v0.1 root lookup (``find_sequence_by_group_root``) also returns cancelled / superseded R·B,
and a slot result document can be cancelled or superseded after it filled the slot. Neither
may stay on the Canvas as a node or as the end of a reference edge (P0008 §2.4·§2.7,
D0004 §3.7 "root 문서").
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.graph_workflow.projection.legacy_projector import project  # noqa: E402

FLOW = "flowgate.v02.0048"


def _d(seq, typ, **extra):
    doc_id = f"{FLOW}.{seq:04d}-{typ}"
    return {"doc_id": doc_id, "type_code": typ, "seq": seq, "title": f"{typ} {seq}", "group_id": FLOW,
            "status": "open", "doc_review_status": "approved", **extra}


def _id(seq, typ):
    return f"{FLOW}.{seq:04d}-{typ}"


def _rows(*results):
    return [{"id": 10 + i, "item_seq": i + 1, "type": typ, "label": f"{typ} 슬롯", "sort_order": i,
             "result_doc_id": rid} for i, (typ, rid) in enumerate(results)]


def _keys(rows):
    return {int(r["id"]): ((f"doc:{r['result_doc_id']}", "document") if r["result_doc_id"]
                           else (f"slot_{r['id']:012d}", "sequence_slot")) for r in rows}


def _project(docs, root_id, rows):
    return project(FLOW, "v01", docs, {"id": 1, "doc_id": root_id}, rows, _keys(rows), set())


def _edges(out):
    return [(e["source"], e["target"], e["edge_class"]) for e in out.edges]


def test_cancelled_root_is_not_drawn_and_its_relations_go_external():
    docs = [_d(1, "R", status="cancelled"), _d(2, "N", triggered_by=_id(1, "R"))]
    rows = _rows(("N", _id(2, "N")), ("NR", None))
    out = _project(docs, _id(1, "R"), rows)
    slot = "slot_000000000011"
    assert [n["node_key"] for n in out.nodes] == [f"doc:{_id(2, 'N')}", slot]
    assert out.chain == [f"doc:{_id(2, 'N')}", slot] and out.sequence_root_doc_id is None
    assert _edges(out) == [(f"doc:{_id(2, 'N')}", slot, "execution")]
    assert [(r["relation"], r["other_doc_id"]) for r in out.external_relations] == [("triggered_by", _id(1, "R"))]


def test_superseded_root_is_shown_as_the_live_end_of_its_chain():
    docs = [_d(1, "R", superseded_by=_id(3, "R")), _d(2, "N", triggered_by=_id(1, "R")),
            _d(3, "R", doc_review_status="wf_in_progress")]
    rows = _rows(("N", _id(2, "N")))
    out = _project(docs, _id(1, "R"), rows)
    keys = [n["node_key"] for n in out.nodes]
    assert keys == [f"doc:{_id(3, 'R')}", f"doc:{_id(2, 'N')}"] and f"doc:{_id(1, 'R')}" not in keys
    assert out.chain == keys and out.free_docs == [] and out.sequence_root_doc_id == _id(3, "R")
    assert out.nodes[0]["document"]["supersedes"] == [_id(1, "R")]
    assert _edges(out) == [(f"doc:{_id(3, 'R')}", f"doc:{_id(2, 'N')}", "execution")]
    assert [r["other_doc_id"] for r in out.external_relations] == [_id(1, "R")]


def test_root_with_cancelled_replacement_or_missing_row_is_not_drawn():
    docs = [_d(1, "R", superseded_by=_id(3, "R")), _d(3, "R", status="cancelled")]
    assert _project(docs, _id(1, "R"), []).nodes == []
    assert _project([], _id(1, "R"), []).nodes == []


def test_slot_whose_result_is_no_longer_alive_keeps_the_slot_without_the_document():
    docs = [_d(1, "R"), _d(2, "N", superseded_by=_id(4, "N")), _d(3, "M", target_id=_id(2, "N")),
            _d(4, "N", doc_review_status="pending_review"), _d(5, "T", status="cancelled")]
    rows = _rows(("N", _id(2, "N")), ("T", _id(5, "T")))
    out = _project(docs, _id(1, "R"), rows)
    by_key = {n["node_key"]: n for n in out.nodes}
    for key, label in ((f"doc:{_id(2, 'N')}", "N 슬롯"), (f"doc:{_id(5, 'T')}", "T 슬롯")):
        assert by_key[key]["origin"] == "sequence_slot" and by_key[key]["document"] is None
        assert by_key[key]["title"] == label
    assert out.free_docs == [f"doc:{_id(3, 'M')}", f"doc:{_id(4, 'N')}"]
    assert all(e["edge_class"] == "execution" for e in out.edges)
    assert [(r["node_key"], r["other_doc_id"]) for r in out.external_relations] == [
        (f"doc:{_id(3, 'M')}", _id(2, "N"))]
