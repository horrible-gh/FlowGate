"""R0001 #1 / T0004 — get_predecessor_result_doc_ids returns the top-N produced items.

Pure-Python unit over get_sequence_items(): the helper that lets advance_workflow seed
Section 3 'Reference documents' with the previous document + the one before it.
"""
from modules.flow_gate.db import workflow_sequences as wfseq


def _items():
    # Mixed sort_order, some not yet produced (result_doc_id is None).
    return [
        {"id": 1, "sort_order": 0, "result_doc_id": "G-N0002"},
        {"id": 2, "sort_order": 1, "result_doc_id": "G-NR0003"},
        {"id": 3, "sort_order": 2, "result_doc_id": "G-T0004"},
        {"id": 4, "sort_order": 3, "result_doc_id": None},  # current head (TR), unproduced
    ]


def test_returns_top_two_most_recent_first(monkeypatch):
    monkeypatch.setattr(wfseq, "get_sequence_items", lambda _sid: _items())
    out = wfseq.get_predecessor_result_doc_ids(7, exclude_item_id=4, limit=2)
    assert out == ["G-T0004", "G-NR0003"]


def test_excludes_the_head_item(monkeypatch):
    # Even if the head already has a result doc, exclude_item_id drops it.
    items = _items()
    items[3]["result_doc_id"] = "G-TR0005"
    monkeypatch.setattr(wfseq, "get_sequence_items", lambda _sid: items)
    out = wfseq.get_predecessor_result_doc_ids(7, exclude_item_id=4, limit=2)
    assert "G-TR0005" not in out
    assert out == ["G-T0004", "G-NR0003"]


def test_empty_when_no_prior_production(monkeypatch):
    items = [{"id": 1, "sort_order": 0, "result_doc_id": None}]
    monkeypatch.setattr(wfseq, "get_sequence_items", lambda _sid: items)
    assert wfseq.get_predecessor_result_doc_ids(7, exclude_item_id=1) == []


def test_caps_at_limit(monkeypatch):
    monkeypatch.setattr(wfseq, "get_sequence_items", lambda _sid: _items())
    assert wfseq.get_predecessor_result_doc_ids(7, exclude_item_id=4, limit=1) == ["G-T0004"]
