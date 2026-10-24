"""0018 R0001: workflow-strip time-machine — sequence endpoint exposes the slot's
realised document identity (result_doc_id + result_seq) so the FE can map a clicked
strip cell to its rollback target (documents.seq → reopen target_seq).

The GET /workflow/{doc_id}/sequence serializer (_serialize_item) must surface
``result_doc_id`` and ``result_seq`` for every slot. Repeated types (e.g. a design
series appearing twice) are then resolved by slot identity on the FE, not indexOf.

Scenarios:
  S1  A realised, approved slot serializes result_doc_id + result_seq (the reopen target).
  S2  A pending slot (result_doc_id IS NULL) serializes both as None.
  S3  The registered SQL selects d.seq AS result_seq (join is in place).
"""
from __future__ import annotations

from modules.flow_gate.api.v1 import workflow_head_routes as whr


# ── S1: realised slot carries its document identity + seq ──────────────────────

def test_S1_realised_slot_exposes_result_doc_id_and_result_seq():
    item = {
        "id": 7,
        "item_seq": 3,
        "type": "T",
        "label": "작업지시",
        "doc_class": "R",
        "sort_order": 3,
        "result_doc_id": "flowgate.default.0018.0004-T",
        "result_doc_review_status": "approved",
        "result_seq": 4,
    }
    out = whr._serialize_item(item)
    assert out["status"] == "done"
    assert out["result_doc_id"] == "flowgate.default.0018.0004-T"
    # result_seq is the documents.seq the FE hands to reopen as target_seq.
    assert out["result_seq"] == 4
    assert out["type"] == "T"


# ── S2: unrealised (pending) slot exposes None for both ────────────────────────

def test_S2_pending_slot_exposes_null_identity():
    item = {
        "id": 8,
        "item_seq": 4,
        "type": "TR",
        "label": "작업레포트",
        "doc_class": "R",
        "sort_order": 4,
        "result_doc_id": None,
        "result_doc_review_status": None,
        "result_seq": None,
    }
    out = whr._serialize_item(item)
    assert out["status"] == "pending"
    assert out["result_doc_id"] is None
    assert out["result_seq"] is None


# ── S3: the registered SQL joins documents for d.seq AS result_seq ─────────────

def test_S3_registered_sql_selects_result_seq():
    import json
    from pathlib import Path

    root = Path(whr.__file__).resolve()
    # walk up to the server/ root, then the shared queries.json
    for _ in range(12):
        candidate = root.parent / "sql" / "queries" / "queries.json"
        if candidate.exists():
            queries = json.loads(candidate.read_text(encoding="utf-8"))
            sql = queries["workflow_sequences"]["get_sequence_items"]
            assert "d.seq AS result_seq" in sql, "get_sequence_items must select d.seq AS result_seq"
            return
        root = root.parent
    raise AssertionError("queries.json not found while walking up from workflow_head_routes.py")
