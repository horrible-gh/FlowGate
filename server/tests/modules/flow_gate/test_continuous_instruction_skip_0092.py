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
    # 0406 T0022 작업 3: 개수가 아니라 **어느 칸을** 서버가 대신 처리했는지를 돌려준다.
    # 그 목록이 없으면 "N/T 단계가 사라졌다"는 관찰을 사후에 설명할 근거가 없다.
    assert n == [1]
    assert created == ["N"]  # only the instruction head was auto-completed


def test_auto_complete_does_not_auto_approve_TS(monkeypatch):
    # group 0121 R0001: TS (테스트시나리오 지시) is intentionally NOT in INSTRUCTION_AUTO_TYPES.
    # When the head is TS the auto-complete loop must stop immediately (treat it like a report
    # head) so advance_workflow mints a worker token+mention for it — the AI authors TS itself.
    assert "TS" not in svc.INSTRUCTION_AUTO_TYPES
    _patch_perms(monkeypatch)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("TS", 5))
    calls = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: calls.append(k) or {"doc_id": "x"})
    n = svc._auto_complete_instruction_heads(
        spine_doc={"doc_id": "r", "group_id": "g", "project_id": "p", "module": "default"},
        seq={"id": 7}, actor_user_id="pm", locale="ko", target_seq=6,
    )
    assert n == [] and calls == []  # TS head → nothing auto-created; token issued by caller


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
    assert n == []
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
    assert n == [] and calls == []


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
    assert n == [] and calls == []


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


def test_advance_continuous_ai_direct_mints_instruction(monkeypatch):
    _patch_perms(monkeypatch)
    doc = {"doc_id": "flowgate.default.0230.0001-R", "group_id": "flowgate.default.0230",
           "project_id": "flowgate", "type_code": "R", "seq": 1}
    _wire_advance_min(monkeypatch, doc)

    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("T", 3))
    calls = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: calls.append(k) or {"doc_id": "x"})
    mention_kw = {}
    issue_kw = {}
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **k: issue_kw.update(k) or {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                                           "token_id": "tok", "expires_at": "2026-06-20"},
    )
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec",
                        lambda **k: mention_kw.update(k) or "M")

    result = svc.advance_workflow(
        doc_id="flowgate.default.0230.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        continuous=True, continuation_target_seq=6,
        continuation_instruction_mode="ai_direct",
    )
    assert calls == []
    assert mention_kw["head_type"] == "T"
    assert issue_kw["continuation_instruction_mode"] == "ai_direct"
    assert result["continuation_instruction_mode"] == "ai_direct"


# ── (0352 T0004 §2) per-item_seq N/T auto-approve selection within ai_direct ──────────

def test_advance_continuous_ai_direct_auto_approves_selected_item_seq(monkeypatch):
    # §4 완료기준 1: ai_direct + T at item_seq 1 and 3, selecting {3} auto-completes ONLY
    # item_seq 3 server-side; item_seq 1 (not selected) still mints a worker token.
    _patch_perms(monkeypatch)
    doc = {"doc_id": "flowgate.default.0353.0001-R", "group_id": "flowgate.default.0353",
           "project_id": "flowgate", "type_code": "R", "seq": 1}
    _wire_advance_min(monkeypatch, doc)

    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("T", 3))
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [{"item_seq": 3, "type": "T", "status": "pending"}],
    )
    created = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: created.append(k["type_code"]) or {"doc_id": "x"})
    mention_kw = {}
    issue_kw = {}
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **k: issue_kw.update(k) or {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                                           "token_id": "tok", "expires_at": "2026-06-20"},
    )
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec",
                        lambda **k: mention_kw.update(k) or "M")

    result = svc.advance_workflow(
        doc_id="flowgate.default.0353.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        continuous=True, continuation_target_seq=6,
        continuation_instruction_mode="ai_direct",
        continuation_auto_approve_item_seqs=[3],
    )
    # item_seq 3 (T) matched the selection → auto-completed server-side, same as auto_approved.
    assert created == ["T"]
    assert issue_kw["continuation_auto_approve_item_seqs"] == [3]
    assert result["continuation_auto_approve_item_seqs"] == [3]


def test_advance_continuous_ai_direct_leaves_unselected_item_seq_to_worker(monkeypatch):
    # §4 완료기준 1 (converse): T at item_seq 1 is NOT in the selection {3} → the worker
    # token is minted for it, exactly like plain (no-selection) ai_direct.
    _patch_perms(monkeypatch)
    doc = {"doc_id": "flowgate.default.0353.0001-R", "group_id": "flowgate.default.0353",
           "project_id": "flowgate", "type_code": "R", "seq": 1}
    _wire_advance_min(monkeypatch, doc)

    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("T", 1))
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [
            {"item_seq": 1, "type": "T", "status": "pending"},
            {"item_seq": 3, "type": "T", "status": "pending"},
        ],
    )
    calls = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: calls.append(k) or {"doc_id": "x"})
    mention_kw = {}
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec",
                        lambda **k: mention_kw.update(k) or "M")

    result = svc.advance_workflow(
        doc_id="flowgate.default.0353.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        continuous=True, continuation_target_seq=6,
        continuation_instruction_mode="ai_direct",
        continuation_auto_approve_item_seqs=[3],
    )
    assert calls == []  # item_seq 1 is not selected → not auto-completed
    assert mention_kw["head_type"] == "T"
    assert result["token"] == "RAW" if "token" in result else True


# ── (0352 T0004 §2) normalize / validate / is_auto_handled_step (pure functions) ──────

def test_normalize_auto_approve_item_seqs_dedupes_sorts_and_empties():
    assert svc.normalize_continuation_auto_approve_item_seqs(None) == []
    assert svc.normalize_continuation_auto_approve_item_seqs([]) == []
    assert svc.normalize_continuation_auto_approve_item_seqs([7, 3, 3, 1]) == [1, 3, 7]


def test_normalize_auto_approve_item_seqs_rejects_non_positive_and_non_int():
    import pytest
    with pytest.raises(ValueError):
        svc.normalize_continuation_auto_approve_item_seqs([0])
    with pytest.raises(ValueError):
        svc.normalize_continuation_auto_approve_item_seqs([-1])
    with pytest.raises(ValueError):
        svc.normalize_continuation_auto_approve_item_seqs(["3"])
    with pytest.raises(ValueError):
        svc.normalize_continuation_auto_approve_item_seqs([True])


def test_is_auto_handled_step_formula():
    # eligible = type in {N,T}; auto = eligible and (auto_approved OR (ai_direct and selected))
    assert svc.is_auto_handled_step(
        head_type="T", item_seq=3, instruction_mode="auto_approved",
    ) is True
    assert svc.is_auto_handled_step(
        head_type="T", item_seq=3, instruction_mode="ai_direct",
        auto_approve_item_seqs=[3],
    ) is True
    assert svc.is_auto_handled_step(
        head_type="T", item_seq=1, instruction_mode="ai_direct",
        auto_approve_item_seqs=[3],
    ) is False
    # TS is never eligible, in either mode, selected or not (group 0121 R0001).
    assert svc.is_auto_handled_step(
        head_type="TS", item_seq=3, instruction_mode="ai_direct",
        auto_approve_item_seqs=[3],
    ) is False
    assert svc.is_auto_handled_step(
        head_type="TS", item_seq=3, instruction_mode="auto_approved",
    ) is False
    # A report/other type is never eligible.
    assert svc.is_auto_handled_step(
        head_type="TR", item_seq=4, instruction_mode="auto_approved",
    ) is False


def test_validate_auto_approve_item_seqs_rejects_ineligible_type(monkeypatch):
    import pytest
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [
            {"item_seq": 1, "type": "N", "status": "pending"},
            {"item_seq": 2, "type": "NR", "status": "pending"},
        ],
    )
    # §4 완료기준 8: a non-N/T item_seq (here NR) is rejected.
    with pytest.raises(ValueError, match="ineligible_auto_approve_item_seq"):
        svc.validate_continuation_auto_approve_item_seqs([2], "r-doc", target_seq=6)


def test_validate_auto_approve_item_seqs_rejects_beyond_target(monkeypatch):
    import pytest
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [
            {"item_seq": 1, "type": "N", "status": "pending"},
            {"item_seq": 3, "type": "T", "status": "pending"},
        ],
    )
    # §4 완료기준 8: target range 밖 item_seq is rejected.
    with pytest.raises(ValueError, match="out_of_range_auto_approve_item_seq"):
        svc.validate_continuation_auto_approve_item_seqs([3], "r-doc", target_seq=2)


def test_validate_auto_approve_item_seqs_rejects_ts_type(monkeypatch):
    import pytest
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [{"item_seq": 5, "type": "TS", "status": "pending"}],
    )
    # §2: TS is never a selectable target — group 0121 R0001 excludes it from
    # INSTRUCTION_AUTO_TYPES entirely, so it fails the same "ineligible type" check.
    with pytest.raises(ValueError, match="ineligible_auto_approve_item_seq"):
        svc.validate_continuation_auto_approve_item_seqs([5], "r-doc", target_seq=6)


def test_validate_auto_approve_item_seqs_rejects_unknown_item_seq(monkeypatch):
    import pytest
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda _sid: [])
    with pytest.raises(ValueError, match="unknown_auto_approve_item_seq"):
        svc.validate_continuation_auto_approve_item_seqs([9], "r-doc", target_seq=9)


def test_validate_auto_approve_item_seqs_rejects_already_done_at_front_door(monkeypatch):
    import pytest
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [{"item_seq": 1, "type": "T", "status": "done"}],
    )
    # §2: 이미 끝난(승인 완료) 단계의 item_seq는 422 — the default (reject_already_done=True)
    # front-door validation catches this.
    with pytest.raises(ValueError, match="already_done_auto_approve_item_seq"):
        svc.validate_continuation_auto_approve_item_seqs([1], "r-doc", target_seq=6)


def test_validate_auto_approve_item_seqs_internal_reuse_allows_already_done(monkeypatch):
    # advance_workflow's own internal reuse across an ongoing chain must NOT re-reject an
    # item_seq that the server itself already completed earlier in the same run — otherwise
    # the chain would 422 itself to death on the very next hop after an auto-approved step.
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
    monkeypatch.setattr(
        svc.db_wfseq, "get_sequence_items",
        lambda _sid: [{"item_seq": 1, "type": "T", "status": "done"}],
    )
    svc.validate_continuation_auto_approve_item_seqs(
        [1], "r-doc", target_seq=6, reject_already_done=False,
    )  # must not raise


def test_validate_auto_approve_item_seqs_empty_selection_is_always_a_noop():
    # No DB lookup at all when the selection is empty — "no selection" never validates.
    svc.validate_continuation_auto_approve_item_seqs([], "r-doc", target_seq=6)


def test_advance_continuous_issues_worker_token_for_TS(monkeypatch):
    # group 0121 R0001: with TS removed from INSTRUCTION_AUTO_TYPES, a TS head is NOT
    # auto-completed — advance_workflow mints a worker token+mention for TS itself so the AI
    # authors the test-scenario directive. No create_next_approved_core call occurs.
    _patch_perms(monkeypatch)
    doc = {"doc_id": "flowgate.default.0121.0001-R", "group_id": "flowgate.default.0121",
           "project_id": "flowgate", "type_code": "R", "seq": 1}
    _wire_advance_min(monkeypatch, doc)

    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: _head("TS", 5))
    calls = []
    monkeypatch.setattr(docs_mod, "create_next_approved_core",
                        lambda **k: calls.append(k) or {"doc_id": "x"})
    mention_kw = {}
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec",
                        lambda **k: mention_kw.update(k) or "M")

    result = svc.advance_workflow(
        doc_id="flowgate.default.0121.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        continuous=True, continuation_target_seq=6,
    )
    assert calls == []  # TS was NOT auto-approved
    assert mention_kw["head_type"] == "TS"  # worker mention is for TS itself
    assert result["token"] == "RAW"


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
