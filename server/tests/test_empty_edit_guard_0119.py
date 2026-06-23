"""0119 / B0001 — "돌아갈수 없는 워크플로" (decided-but-empty zombie sequence) guard.

NR0003 root cause: edit_workflow_pending accepted an empty new_items list. When a workflow
was decided but not yet started (every step still pending, nothing locked) and the user
deleted ALL steps, the sequence dropped to ZERO items — a decided-but-empty "zombie":
  - re-decide is blocked by already_decided (the sequence row still exists),
  - advance dies with sequence_exhausted (no head),
  - the workflow strip + [Edit] button collapse (FE recovery, separate change).

§6-A guard: refuse an edit that would empty a workflow when nothing is locked. A shrink that
keeps >=1 locked step is still allowed (locked steps + the AC gate remain a valid sequence).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("TESTING", "1")

from modules.flow_gate.services import workflow_decision_service as svc


def _seq(_id):  # get_sequence_by_doc_id stub
    return {"id": 9}


def test_empty_edit_with_no_locked_items_is_rejected(monkeypatch):
    """Decided-but-unstarted workflow + empty edit → invalid_sequence_empty (the bug)."""
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", _seq)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda _sid: [])  # nothing locked

    with pytest.raises(ValueError) as exc:
        svc.edit_workflow_pending("flowgate.default.0119.0001-B", [])
    assert str(exc.value).startswith("invalid_sequence_empty:")


def test_empty_edit_with_all_pending_items_is_rejected(monkeypatch):
    """Pending items present but no result_doc_id (not locked) + empty edit → rejected."""
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", _seq)
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [
            {"id": 1, "type": "N", "result_doc_id": None, "doc_class": "R"},
            {"id": 2, "type": "NR", "result_doc_id": None, "doc_class": "R"},
        ],
    )
    with pytest.raises(ValueError) as exc:
        svc.edit_workflow_pending("flowgate.default.0119.0001-B", [])
    assert str(exc.value).startswith("invalid_sequence_empty:")


def test_empty_edit_with_a_locked_item_is_allowed(monkeypatch):
    """A locked (realized) step exists → emptying pending is a valid 'stop here' shrink."""
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", _seq)

    items = [{"id": 1, "type": "N", "result_doc_id": "flowgate.default.0119.0002-N", "doc_class": "R"}]
    calls = {"items": 0}

    def _items(_sid):
        calls["items"] += 1
        # 1st call: pre-edit snapshot (has the locked item); later: post-delete read.
        return items if calls["items"] == 1 else items

    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", _items)
    monkeypatch.setattr(svc.db_wfseq, "delete_pending_items", lambda _sid: None)
    monkeypatch.setattr(svc.db_wfseq, "get_max_item_seq", lambda _sid: 1)
    monkeypatch.setattr(svc.db_wfseq, "insert_sequence_item", lambda **kw: None)
    monkeypatch.setattr(svc.db_documents, "update", lambda *a, **k: None)
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: {"doc_id": _id, "doc_review_status": "wf_in_progress"})

    @contextmanager
    def _txn():
        yield

    fake_store = MagicMock()
    fake_store.transaction.side_effect = _txn
    monkeypatch.setattr(svc, "get_store", lambda: fake_store)

    out = svc.edit_workflow_pending("flowgate.default.0119.0001-B", [])
    assert out["status"] == "updated"
    assert out["pending_count"] == 0


def test_nonempty_edit_recovers_an_empty_sequence(monkeypatch):
    """§6-B recovery: an already-empty sequence accepts new pending steps via edit."""
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", _seq)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda _sid: [])  # currently empty
    monkeypatch.setattr(svc.db_wfseq, "delete_pending_items", lambda _sid: None)
    monkeypatch.setattr(svc.db_wfseq, "get_max_item_seq", lambda _sid: 0)
    inserted: list[dict] = []
    monkeypatch.setattr(svc.db_wfseq, "insert_sequence_item", lambda **kw: inserted.append(kw))
    monkeypatch.setattr(svc.db_documents, "update", lambda *a, **k: None)
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: {"doc_id": _id, "doc_review_status": "wf_in_progress", "project_id": "", "group_id": ""})
    monkeypatch.setattr(svc, "get_type_name", lambda t, locale="ko": t)

    @contextmanager
    def _txn():
        yield

    fake_store = MagicMock()
    fake_store.transaction.side_effect = _txn
    monkeypatch.setattr(svc, "get_store", lambda: fake_store)

    out = svc.edit_workflow_pending(
        "flowgate.default.0119.0001-B",
        [{"type": "T", "label": "작업지시"}],
    )
    assert out["status"] == "updated"
    assert out["pending_count"] == 1
    assert [row["type_"] for row in inserted] == ["T"]


# ── route mapping: PATCH /workflow/sequence → 400 invalid_sequence_empty ─────────

def test_patch_route_maps_empty_guard_to_400(monkeypatch):
    """End-to-end mapping: the service ValueError surfaces as HTTP 400 with the error code."""
    from modules.flow_gate.api.v1 import workflow_decision_routes as routes

    monkeypatch.setattr(routes, "verify_bearer", lambda _request: {
        "token_id": "tok-1", "project": "flowgate", "issued_to": "user-1",
    })
    # No disposed group, real doc lookup not needed beyond the dispose guard.
    monkeypatch.setattr(routes._db_documents, "get_by_id", lambda _id: {
        "doc_id": _id, "group_id": "flowgate.default.0119",
    })
    monkeypatch.setattr(routes, "_disposed_group_response", lambda *_a, **_k: None)
    monkeypatch.setattr(
        routes, "edit_workflow_pending",
        lambda **_k: (_ for _ in ()).throw(
            ValueError("invalid_sequence_empty:flowgate.default.0119.0001-B")
        ),
    )

    body = routes.EditSequenceBodyRequest(doc_id="flowgate.default.0119.0001-B", items=[])
    resp = routes.patch_workflow_sequence_endpoint(body, MagicMock())

    assert resp.status_code == 400
    import json as _json
    payload = _json.loads(bytes(resp.body))
    assert payload["error"] == "invalid_sequence_empty"
    assert payload["doc_id"] == "flowgate.default.0119.0001-B"
