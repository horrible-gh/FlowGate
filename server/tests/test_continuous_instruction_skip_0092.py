"""Continuous-chain instruction auto-completion — group 0092 B0001 / NR0003 B안.

The unmanned continuous chain used to issue an AI-worker mention for EVERY next step,
making no distinction between instruction-series steps (N/T/TS — "무엇을 하라", fillable
from a fixed server template) and report-series steps (NR/TR/TSR — the actual AI
deliverable). That spent ~3 redundant worker cycles per R→…→TSR lap (B0001's "token
two-fold" symptom).

The fix: ``workflow_decision_service._auto_complete_instruction_heads`` auto-creates +
auto-approves any instruction head server-side (reusing
``documents.create_next_approved_core``, the same managed "자동승인문서" mechanics), so
``advance_workflow`` only ever mints a worker mention at the following report head. These
tests pin that behavior with monkeypatched dependencies (no DB / filesystem).
"""
from __future__ import annotations

import pytest

from modules.flow_gate.services import workflow_decision_service as svc
from modules.flow_gate.documents.routers import documents as docs_mod


def _patch_perms(monkeypatch, *, is_admin=1):
    """Wire the lazy approver-permission resolver the auto-complete helper uses."""
    from modules.flow_gate.db import users as db_users
    monkeypatch.setattr(db_users, "get_by_id", lambda _uid: {"user_id": _uid, "is_admin": is_admin})


def _head(type_, item_seq, *, result_doc_id=None, result_review=None):
    return {
        "type": type_, "label": "x", "item_seq": item_seq, "id": 11,
        "result_doc_id": result_doc_id, "result_doc_review_status": result_review,
    }


# ── _auto_complete_instruction_heads (unit) ──────────────────────────────────────

def test_auto_complete_fills_instruction_then_stops_at_report(monkeypatch):
    _patch_perms(monkeypatch)
    # head starts at N (instruction); the fake core advances it to its paired report NR.
    state = {"type": "N", "item_seq": 1}
    monkeypatch.setattr(
        svc.db_wfseq, "get_effective_head",
        lambda _sid: _head(state["type"], state["item_seq"]),
    )
    created = []

    def _fake_core(**k):
        created.append(k["type_code"])
        state.update(type="NR", item_seq=2)  # slot filled + approved → head is now the report
        return {"doc_id": "flowgate.default.0092.0002-N"}

    monkeypatch.setattr(docs_mod, "create_next_approved_core", _fake_core)

    n = svc._auto_complete_instruction_heads(
        spine_doc={"doc_id": "flowgate.default.0092.0001-B", "group_id": "g",
                   "project_id": "p", "module": "default"},
        seq={"id": 7}, actor_user_id="pm", locale="ko", target_seq=6,
    )
    assert n == 1
    assert created == ["N"]  # only the instruction head was auto-completed


def test_auto_complete_noop_when_head_already_report(monkeypatch):
    _patch_perms(monkeypatch)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("NR", 2))
    calls = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: calls.append(k) or {"doc_id": "x"})
    n = svc._auto_complete_instruction_heads(
        spine_doc={"doc_id": "r", "group_id": "g", "project_id": "p", "module": "default"},
        seq={"id": 7}, actor_user_id="pm", locale="ko", target_seq=6,
    )
    assert n == 0
    assert calls == []  # report head → nothing auto-created, no perm/DB work either


def test_auto_complete_does_not_run_past_target(monkeypatch):
    _patch_perms(monkeypatch)
    # The instruction head sits beyond the chain's stop point → must not be created.
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("T", 9))
    calls = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: calls.append(k) or {"doc_id": "x"})
    n = svc._auto_complete_instruction_heads(
        spine_doc={"doc_id": "r", "group_id": "g", "project_id": "p", "module": "default"},
        seq={"id": 7}, actor_user_id="pm", locale="ko", target_seq=6,
    )
    assert n == 0 and calls == []


def test_auto_complete_skips_in_progress_head(monkeypatch):
    _patch_perms(monkeypatch)
    # A produced-but-unapproved instruction head means a doc is mid-flight; leave it alone.
    monkeypatch.setattr(
        svc.db_wfseq, "get_effective_head",
        lambda _sid: _head("N", 1, result_doc_id="flowgate.default.0092.0002-N",
                            result_review="pending_review"),
    )
    calls = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: calls.append(k) or {"doc_id": "x"})
    n = svc._auto_complete_instruction_heads(
        spine_doc={"doc_id": "r", "group_id": "g", "project_id": "p", "module": "default"},
        seq={"id": 7}, actor_user_id="pm", locale="ko", target_seq=6,
    )
    assert n == 0 and calls == []


def test_auto_complete_pauses_when_approve_denied(monkeypatch):
    # A genuine non-approver: create_next_approved_core raises 403 → the helper re-raises as a
    # ValueError so advance_workflow's caller pauses the chain honestly (P0005 §4).
    _patch_perms(monkeypatch, is_admin=0)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("T", 3))

    def _deny(**k):
        raise docs_mod.NextApprovedError(403, "document.approve permission is required.")

    monkeypatch.setattr(docs_mod, "create_next_approved_core", _deny)
    with pytest.raises(ValueError) as ei:
        svc._auto_complete_instruction_heads(
            spine_doc={"doc_id": "r", "group_id": "g", "project_id": "p", "module": "default"},
            seq={"id": 7}, actor_user_id="pm", locale="ko", target_seq=6,
        )
    assert "instruction_auto_complete_failed:T" in str(ei.value)


# ── advance_workflow integration ─────────────────────────────────────────────────

def _wire_advance_min(monkeypatch, doc):
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _id: {"id": 7})
    monkeypatch.setattr(svc.db_documents, "get_group_max_seq", lambda _gid: 1)
    monkeypatch.setattr(svc.db_documents, "fetch_recent_group_docs", lambda **_k: [])
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_id", lambda _s, _h=None: None)
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_ids",
                        lambda _s, _h=None, limit=2: [])
    from modules.flow_gate.db import tokens as db_tokens
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **k: {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                     "token_id": "tok", "expires_at": "2026-06-20"},
    )


def test_advance_continuous_skips_instruction_mints_report(monkeypatch):
    _patch_perms(monkeypatch)
    doc = {"doc_id": "flowgate.default.0092.0001-B", "group_id": "flowgate.default.0092",
           "project_id": "flowgate", "type_code": "B", "seq": 1}
    _wire_advance_min(monkeypatch, doc)

    state = {"type": "N", "item_seq": 1}
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head",
                        lambda _sid: _head(state["type"], state["item_seq"]))
    created = []

    def _fake_core(**k):
        created.append(k["type_code"])
        state.update(type="NR", item_seq=2)
        return {"doc_id": "flowgate.default.0092.0002-N"}

    monkeypatch.setattr(docs_mod, "create_next_approved_core", _fake_core)
    mention_kw = {}
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec",
                        lambda **k: mention_kw.update(k) or "M")

    result = svc.advance_workflow(
        doc_id="flowgate.default.0092.0001-B", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        continuous=True, continuation_target_seq=6,
    )
    # The N instruction was auto-completed server-side; the worker mention is for the report.
    assert created == ["N"]
    assert mention_kw["head_type"] == "NR"
    assert result["continuous"] is True
    # remaining = target(6) - report head_item_seq(2) + 1 = 5
    assert result["continuation_remaining"] == 5


def test_advance_managed_does_not_auto_complete_instruction(monkeypatch):
    # Managed advance (continuous=False) is untouched: the head stays N and the worker mention
    # is minted for N — the FE still drives "자동승인문서" explicitly in managed mode.
    _patch_perms(monkeypatch)
    doc = {"doc_id": "flowgate.default.0092.0001-B", "group_id": "flowgate.default.0092",
           "project_id": "flowgate", "type_code": "B", "seq": 1}
    _wire_advance_min(monkeypatch, doc)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("N", 1))
    calls = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: calls.append(k) or {"doc_id": "x"})
    mention_kw = {}
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec",
                        lambda **k: mention_kw.update(k) or "M")

    svc.advance_workflow(
        doc_id="flowgate.default.0092.0001-B", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
    )
    assert calls == []  # no server-side auto-completion in managed mode
    assert mention_kw["head_type"] == "N"  # mention minted for the instruction itself
