"""0114 / B0001 — corrupted instruction-label guard.

NR0003 root cause: N/T/TS instruction-step labels are submitted verbatim and can arrive
already mangled by a lossy environment (Hangul → single ASCII '?' per glyph, the
encode('ascii', errors='replace') signature). The server/DB stored them faithfully, so the
ContinuousWorkDialog displayed "?????". This guard (a) detects that signature and (b) falls
back to the document type display name on the read path (sequence GET — heal the 12 rows
already stored). 0391 T0005 §5-5: the write path (decide/edit) no longer falls back — it
rejects a corrupted label outright before the sequence is persisted, so the sender is told
instead of having their intended text silently discarded.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("TESTING", "1")

from modules.flow_gate.services import workflow_decision_service as svc


# ── detector ───────────────────────────────────────────────────────────────────

def test_detects_ascii_replace_mojibake():
    # Real corrupted rows observed in production (NR0003 §2/§3-1).
    assert svc._label_is_corrupted("??? ?? ? 0082(??3) ?? ? ?? ??")
    assert svc._label_is_corrupted("xR ?? ?? ?? ? ?? ?? ?? ??")
    assert svc._label_is_corrupted("????/?? ? ?? ???? ?? locus? ????? ??")


def test_clean_hangul_label_is_not_corrupted():
    assert not svc._label_is_corrupted("조사지시")
    assert not svc._label_is_corrupted("0082 dispose-FK 직접수정")


def test_ascii_label_with_trailing_question_mark_is_not_corrupted():
    # A legitimate ASCII label that merely ends in '?' must survive ('?' does not dominate).
    assert not svc._label_is_corrupted("Done?")
    assert not svc._label_is_corrupted("Really?")


def test_empty_and_single_mark_are_not_corrupted():
    assert not svc._label_is_corrupted("")
    assert not svc._label_is_corrupted(None)
    assert not svc._label_is_corrupted("a?")  # below the 2-mark floor


# ── safe-label fallback ──────────────────────────────────────────────────────────

def test_safe_label_falls_back_to_type_name(monkeypatch):
    monkeypatch.setattr(svc, "get_type_name", lambda t, locale="ko": {"N": "조사지시"}.get(t, ""))
    assert svc._safe_label("???? ?? ? ?? ?? ??", "N") == "조사지시"


def test_safe_label_passes_clean_label_through():
    assert svc._safe_label("0082 직접수정", "T") == "0082 직접수정"


# ── write path: decide_workflow rejects before INSERT (0391 T0005 §5-5) ─────────
# Supersedes the old "sanitize on write" contract this test locked in: the swap gave
# the sender no signal and discarded their intended text. decide_workflow now raises
# instead, and nothing is inserted — verified below via the empty `inserted` list.

def test_decide_workflow_rejects_corrupted_label(monkeypatch):
    inserted: list[dict] = []

    @contextmanager
    def _txn():
        yield

    fake_store = MagicMock()
    fake_store.transaction.side_effect = _txn

    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: {"doc_id": _id})
    seq_row = {"id": 7}
    calls = {"n": 0}

    def _by_doc(_id):
        calls["n"] += 1
        return None if calls["n"] == 1 else seq_row

    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", _by_doc)
    monkeypatch.setattr(svc.db_wfseq, "insert_sequence", lambda _id: None)
    monkeypatch.setattr(
        svc.db_wfseq, "insert_sequence_item",
        lambda **kw: inserted.append(kw),
    )
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: None)
    monkeypatch.setattr(svc.db_documents, "update", lambda *a, **k: None)
    monkeypatch.setattr(svc, "get_store", lambda: fake_store)
    monkeypatch.setattr(svc, "get_type_name", lambda t, locale="ko": {"N": "조사지시", "NR": "조사레포트"}.get(t, t))

    with pytest.raises(ValueError, match="corrupted_label"):
        svc.decide_workflow(
            "flowgate.default.0114.0001-R",
            "R",
            [{"id": 1, "type": "N", "label": "???? ?? ? ?? ???? ?? locus? ?? ??"}],
        )

    assert inserted == []  # rejected before any INSERT — no partial sequence


# ── read path: get_workflow_sequence heals already-stored rows ──────────────────

def test_get_workflow_sequence_heals_corrupted_rows(monkeypatch):
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: {"doc_id": _id})
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", lambda _id: {"id": 3})
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [
            {"id": 1, "item_seq": 1, "type": "TS", "label": "??? ?? ? 0082(??3) ??",
             "doc_class": "R", "sort_order": 0, "status": "done"},
            {"id": 2, "item_seq": 2, "type": "TSR", "label": "테스트레포트",
             "doc_class": "R", "sort_order": 1, "status": "pending"},
        ],
    )
    monkeypatch.setattr(svc, "get_type_name", lambda t, locale="ko": {"TS": "테스트시나리오지시"}.get(t, t))

    out = svc.get_workflow_sequence("flowgate.default.0114.0001-R")
    by_seq = {it["item_seq"]: it["label"] for it in out["items"]}
    assert by_seq[1] == "테스트시나리오지시"   # corrupted → type name
    assert by_seq[2] == "테스트레포트"          # clean label untouched
