"""0084 TR0005 (B, defensive) — advance / token-mention resolve a non-R member doc.

B0001: copying the next-step mention from the action bar of a non-R document (e.g. a
chat CH, which is a slot's produced child / result_doc_id) yielded the *chat*-bound
mention instead of the next-step "new" mention. NR0003 found the server cause: both
``advance_workflow`` and ``_build_mention_for_token`` resolved the workflow sequence with
the root-only ``get_sequence_by_doc_id``. A produced child is a member of the sequence
but not its root, so the lookup returned None → ``sequence_not_decided`` (400) / a head-
less degraded mention. The fix routes both through ``get_sequence_for_member_doc``, which
falls back from the root lookup to the child's owning sequence.

These tests drive a *child* doc_ref (root lookup misses, member lookup hits) and pin that
the sequence now resolves. The FE A-fix sends the parent R, so this is the defensive net.
"""
import pytest

from modules.flow_gate.api import token_routes
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import tokens as db_tokens
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.services import mention_service
from modules.flow_gate.services import workflow_decision_service as svc


CHILD = "flowgate.default.0084.0001-CH"  # a produced child (result_doc_id), not a root R


def _as_member(monkeypatch, *, is_member: bool):
    """Make get_sequence_for_member_doc resolve (or not) for a non-root child doc.

    Roots fail the keyed lookup (get_sequence_by_doc_id -> None); a produced child is
    recovered via its sequence-item (get_item_by_result_doc_id -> get_sequence_by_id).
    """
    monkeypatch.setattr(db_wfseq, "get_sequence_by_doc_id", lambda _id: None)
    if is_member:
        monkeypatch.setattr(
            db_wfseq, "get_item_by_result_doc_id", lambda _id: {"sequence_id": 7}
        )
        monkeypatch.setattr(db_wfseq, "get_sequence_by_id", lambda _sid: {"id": 7})
    else:
        monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", lambda _id: None)


# ── advance_workflow ────────────────────────────────────────────────────────────

def _wire_advance(monkeypatch):
    doc = {"doc_id": CHILD, "group_id": "flowgate.default.0084",
           "project_id": "flowgate", "type_code": "CH", "seq": 4}
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(
        svc.db_wfseq, "get_effective_head",
        lambda _sid: {"id": 5, "type": "N", "label": "조사", "result_doc_id": None,
                      "result_doc_review_status": None},
    )
    monkeypatch.setattr(svc.db_documents, "get_group_max_seq", lambda _gid: 4)
    monkeypatch.setattr(svc.db_documents, "fetch_recent_group_docs", lambda **_k: [])
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_id", lambda _sid, _hid=None: None)
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_ids", lambda _sid, _hid=None, limit=2: [])
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **_k: {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                      "token_id": "tok-new", "expires_at": "2026-06-16T00:00:00"},
    )
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec", lambda **_k: "mention")


def test_advance_resolves_sequence_from_member_child(monkeypatch):
    _wire_advance(monkeypatch)
    _as_member(monkeypatch, is_member=True)
    # Before the fix this raised ValueError("sequence_not_decided:...CH").
    result = svc.advance_workflow(
        doc_id=CHILD, issued_to="pm-1", api_base_url="http://h/flow_gate/api/v1",
    )
    assert result["token"] == "RAW"
    assert result["token_id"] == "tok-new"


def test_advance_still_rejects_truly_sequenceless_doc(monkeypatch):
    _wire_advance(monkeypatch)
    _as_member(monkeypatch, is_member=False)  # neither root nor member
    with pytest.raises(ValueError, match="sequence_not_decided"):
        svc.advance_workflow(
            doc_id=CHILD, issued_to="pm-1", api_base_url="http://h/flow_gate/api/v1",
        )


# ── _build_mention_for_token ─────────────────────────────────────────────────────

class _FakeRequest:
    base_url = "http://192.168.0.250:8089/"


def test_token_mention_resolves_sequence_from_member_child(monkeypatch):
    """With a child doc_ref, the mention builder must still find the head (so the next-step
    'new' mention is built) rather than falling through to a head-less degraded mention."""
    parent_doc = {"doc_id": CHILD, "seq": 4, "revision_no": 0}
    monkeypatch.setattr(db_documents, "get_by_id", lambda did: parent_doc)
    monkeypatch.setattr(db_documents, "get_group_max_seq", lambda gid: 4)
    monkeypatch.setattr(db_documents, "fetch_recent_group_docs", lambda **k: [])
    _as_member(monkeypatch, is_member=True)
    monkeypatch.setattr(db_wfseq, "get_effective_head", lambda sid: {"id": 5, "type": "N"})
    monkeypatch.setattr(db_wfseq, "get_predecessor_result_doc_id", lambda sid, hid: None)
    monkeypatch.setattr(db_wfseq, "get_predecessor_result_doc_ids", lambda sid, hid, limit=2: [])

    captured: dict = {}

    def _fake_build(**kwargs):
        captured.update(kwargs)
        return "MENTION"

    monkeypatch.setattr(mention_service, "build_mention_from_token_rec", _fake_build)

    out = token_routes._build_mention_for_token(
        doc_ref=CHILD, group_id="flowgate.default.0084", project_id="flowgate",
        scratch_dir="/tmp", raw_token="tok", request=_FakeRequest(),
        ref_doc_ids=[CHILD],
    )
    assert out == "MENTION"
    # The head was resolved (sequence found via the member helper) → head_type is the next
    # step, so the worker receives the "new" next-step mention, not a head-less CH copy.
    assert captured["head_type"] == "N"
