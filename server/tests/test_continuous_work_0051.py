"""Continuous (unmanned) work — group 0051 R0001 / NR0003 B안 foundation.

Covers the backend slice implemented for T0004 (작업개시):
  • mention_service.build_mention(continuous=...) — the Q/no-choices guard is REPLACED
    (not removed) by the delegation/unmanned/no-stop/autonomous block (TS-5).
  • workflow_decision_service.advance_workflow(continuous=...) — continuation metadata
    rides the issued token; the response carries continuation_remaining (TS-6 carrier).
  • inbox_routes._continuation_self_chain — server-driven self-chaining: ordinary token
    is a no-op; AI review mode is the pre-flight Q-registration phase and PAUSES (0086
    TR0004 rework rev4 #1); non-review continuous auto-approves + mints the next token
    (TS-6/§6-①); target reached auto-approves the last step, then stops (rev4 #2).

All pure-function / monkeypatched — no DB or filesystem dependency.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from modules.flow_gate.services import mention_service
from modules.flow_gate.services import workflow_decision_service as svc


# ── Common mention args ────────────────────────────────────────────────────────

def _mention_kwargs(**over):
    base = dict(
        project="flowgate",
        module="default",
        group="0051",
        parent_type="R",
        parent_doc_number="R0001",
        parent_title="연속 작업 기능",
        parent_doc_id="R0001",
        parent_canonical_doc_id="flowgate.default.0051.0001-R",
        head_type="N",
        head_status="pending",
        scratch_dir="/tmp/scratch",
        raw_token="RAWTOKEN",
        api_base_url="http://h/flow_gate/api/v1",
    )
    base.update(over)
    return base


# ── TS-5: continuous mention branch ─────────────────────────────────────────────

def test_continuous_mention_replaces_q_guard_ko():
    out = mention_service.build_mention(continuous=True, locale="ko", **_mention_kwargs())
    # Unmanned/delegation/no-stop/autonomous block is present...
    assert "## Continuous work" in out
    assert "UNMANNED" in out
    assert "위임" in out and "중단" in out
    # ...and the Q-registration guard is gone (replaced, not merely appended).
    assert "Clarification guide" not in out
    assert "/questions" not in out
    # Operational sections are preserved.
    assert "## Artifact registration" in out
    assert "## doc_type guide" in out
    # The bottom Reminder repeats the continuous directive, not the no-choices guard.
    assert "Do NOT present choices" not in out


def test_continuous_mention_locale_en_no_korean_leak():
    out = mention_service.build_mention(continuous=True, locale="en", **_mention_kwargs())
    assert "UNMANNED" in out and "autonomously" in out
    # English-locale continuous block must not leak the Korean directive prose.
    assert "위임" not in out and "무인" not in out


def test_continuous_review_mention_is_q_phase_without_new_guidance():
    # 0086 TR0004 rework rev4 (reason #1): with [AI 검토 모드] ON the continuous block is the
    # PRE-FLIGHT Q-registration phase — the "new안내" (action:new Artifact registration) is
    # REMOVED and replaced by Q-registration guidance. Review ≠ go.
    review = mention_service.build_mention(
        continuous=True, continuous_review_mode=True, locale="ko", **_mention_kwargs()
    )
    plain = mention_service.build_mention(
        continuous=True, continuous_review_mode=False, locale="ko", **_mention_kwargs()
    )
    # Review phase header + embedded Q POST...
    assert "## Review phase" in review
    assert "/questions" in review and "질의" in review
    assert "사전" in review and ("go" in review or "검토" in review)
    # ...and the action:new artifact guidance is gone (the "new안내" the reviewer wanted removed).
    assert "## Artifact registration" not in review
    # The no-Q case is handled: register a 'review complete, confirm to proceed' Q.
    assert "검토 완료" in review
    # The plain (non-review/go) block keeps the action:new artifact section and never mentions Q.
    assert "## Artifact registration" in plain
    assert "/questions" not in plain
    assert review != plain


def test_continuous_review_mention_en_no_korean_leak():
    out = mention_service.build_mention(
        continuous=True, continuous_review_mode=True, locale="en", **_mention_kwargs()
    )
    assert "review" in out and "register a Q" in out
    assert "do not create" in out.lower() or "do not produce" in out.lower()
    assert "위임" not in out and "검토" not in out


def _decision_mention_kwargs(**over):
    base = dict(
        token_rec={"project": "flowgate", "group_id": "flowgate.default.0086"},
        target_doc={
            "doc_id": "flowgate.default.0086.0001-R",
            "type_code": "R",
            "seq": 1,
            "title": "연속 작업 기능",
            "module": "default",
        },
        api_base_url="http://h/flow_gate/api/v1",
        raw_token="RAWTOKEN",
    )
    base.update(over)
    return base


def test_decision_mention_review_mode_does_not_say_keep_going():
    # 0086 TR0004 rework rev5: the "워크플로 결정부터" decision mention is the FIRST mention copied
    # in a continuous run started before the workflow is decided. rev4 appended the non-review
    # "keep going until done, do NOT stop after deciding" line for ALL continuous decisions —
    # including [AI 검토 모드] — which contradicts review mode ("아직 go가 아니다"). rev5 splits it.
    review = mention_service.build_workflow_decision_mention(
        continuous=True, continuous_review_mode=True, locale="ko", **_decision_mention_kwargs()
    )
    go = mention_service.build_workflow_decision_mention(
        continuous=True, continuous_review_mode=False, locale="ko", **_decision_mention_kwargs()
    )
    # Non-review (go): the worker barrels through — "keep going until done, do NOT stop".
    assert "keep going until the" in go and "Do NOT stop after deciding" in go
    # Review phase: must NOT tell the worker to keep going / not stop after deciding.
    assert "keep going until the" not in review
    assert "Do NOT stop after deciding" not in review
    # Instead it frames deciding as the pre-flight review and tells it to wait for the go.
    assert "REVIEW phase" in review
    assert "do NOT auto-run" in review and "human" in review
    # The clarification body is the review (Q-phase) text in review mode, the unmanned block in go.
    assert "사전" in review  # pre-flight review prose
    assert review != go


def test_continuous_mention_locale_ja():
    out = mention_service.build_mention(continuous=True, locale="ja", **_mention_kwargs())
    assert "UNMANNED" in out and "無人" in out


def test_non_continuous_keeps_clarification_guard():
    out = mention_service.build_mention(continuous=False, locale="ko", **_mention_kwargs())
    assert "## Clarification guide" in out
    assert "/questions" in out
    assert "## Continuous work" not in out


def test_continuous_ignored_on_edit():
    # An edit (rejection rework) is a human-gated step — continuous must NOT apply.
    out = mention_service.build_mention(
        continuous=True, locale="ko", action_scope="edit", **_mention_kwargs()
    )
    assert "## Continuous work" not in out
    assert "Revision instructions" in out
    assert "## Clarification guide" in out


# ── advance_workflow continuous metadata (mirrors test_r0001_advance_supersede) ──

def _wire_advance(monkeypatch, head_item_seq=4, head_type="NR"):
    # head_type defaults to a REPORT step: under the group 0092 fix a continuous advance
    # auto-completes instruction heads (N/T/TS) server-side and only ever mints a worker
    # mention at a report head, so the mention-issuance path is exercised with a report.
    doc = {
        "doc_id": "flowgate.default.0051.0001-R",
        "group_id": "flowgate.default.0051",
        "project_id": "flowgate",
        "type_code": "R",
        "seq": 1,
    }
    monkeypatch.setattr(svc.db_documents, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _id: {"id": 7})
    monkeypatch.setattr(
        svc.db_wfseq, "get_effective_head",
        lambda _sid: {"type": head_type, "label": "조사레포트", "result_doc_id": None,
                      "result_doc_review_status": None, "item_seq": head_item_seq, "id": 11},
    )
    monkeypatch.setattr(svc.db_documents, "get_group_max_seq", lambda _gid: 1)
    monkeypatch.setattr(svc.db_documents, "fetch_recent_group_docs", lambda **_k: [])
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_id", lambda _s, _h=None: None)
    monkeypatch.setattr(svc.db_wfseq, "get_predecessor_result_doc_ids",
                        lambda _s, _h=None, limit=2: [])
    from modules.flow_gate.db import tokens as db_tokens
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    monkeypatch.setattr(svc.mention_service, "build_mention_from_token_rec", lambda **_k: "M")


def test_advance_continuous_carries_metadata_and_remaining(monkeypatch):
    _wire_advance(monkeypatch, head_item_seq=4)
    captured = {}
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **k: captured.update(k) or {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                                           "token_id": "tok-new", "expires_at": "2026-06-20T00:00:00"},
    )
    mention_kw = {}
    monkeypatch.setattr(
        svc.mention_service, "build_mention_from_token_rec",
        lambda **k: mention_kw.update(k) or "M",
    )

    result = svc.advance_workflow(
        doc_id="flowgate.default.0051.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        continuous=True, continuation_target_seq=6, continuation_review_mode=True,
    )

    assert captured["continuation_target_seq"] == 6
    assert captured["continuation_review_mode"] is True
    assert mention_kw["continuous"] is True
    # remaining = target(6) - head_item_seq(4) + 1 = 3
    assert result["continuation_remaining"] == 3
    assert result["continuous"] is True
    assert result["continuation_review_mode"] is True


def test_advance_non_continuous_has_no_metadata(monkeypatch):
    _wire_advance(monkeypatch)
    captured = {}
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **k: captured.update(k) or {"raw_token": "RAW", "scratch_dir": "/tmp/s",
                                           "token_id": "tok-new", "expires_at": "x"},
    )
    result = svc.advance_workflow(
        doc_id="flowgate.default.0051.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
    )
    assert captured["continuation_target_seq"] is None
    assert captured["continuation_review_mode"] is False
    assert result["continuation_remaining"] is None
    assert result["continuous"] is False


# ── inbox self-chain ─────────────────────────────────────────────────────────────

class _FakeRequest:
    def __init__(self):
        self.headers = {"x-locale": "ko"}
        self.base_url = "http://h/"


def _chain(token_rec, doc_type="NR", **patches):
    from modules.flow_gate.api import inbox_routes
    return inbox_routes._continuation_self_chain(
        _FakeRequest(), token_rec, "flowgate", "flowgate.default.0051.0003-NR", doc_type
    )


def test_self_chain_ordinary_token_is_noop():
    # No continuation_target_seq → ordinary token → no chaining.
    assert _chain({"doc_ref": "x", "issued_to": "pm"}) is None


def _wire_self_chain_advance(monkeypatch, *, is_admin=1, item_seq=2):
    """Wire the dependencies the self-chain auto-approve+advance path now touches.

    Resolver fix (0086 TR0004 rework): the self-chain resolves permissions through
    db.users.get_by_id + workflow._get_user_permissions (the is_admin stub the live
    approve button uses), NOT permission_service.get_user_permissions (∅ on live).
    """
    from modules.flow_gate.db import workflow_sequences as wfseq
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.services import workflow_decision_service as wds
    from modules.flow_gate.api import inbox_routes

    monkeypatch.setattr(wfseq, "get_item_by_result_doc_id", lambda _d: {"item_seq": item_seq})
    monkeypatch.setattr(db_users, "get_by_id", lambda _uid: {"user_id": _uid, "is_admin": is_admin})
    approve = MagicMock()
    monkeypatch.setattr(pipeline_service, "transition_document_review", approve)
    captured: dict = {}

    def _fake_advance(**k):
        captured.update(k)
        return {"token": "NEXTRAW", "token_id": "tok-next", "mention": "NEXT-MENTION",
                "expires_at": "2026-06-20", "continuation_remaining": 3}

    monkeypatch.setattr(wds, "advance_workflow", _fake_advance)
    monkeypatch.setattr(inbox_routes, "_inbox_api_base", lambda _r: "http://h/flow_gate/api/v1")
    return approve, captured


def test_self_chain_review_mode_pauses_for_q_phase(monkeypatch):
    # 0086 TR0004 rework rev4 (reason #1): review mode is the pre-flight Q-registration phase,
    # NOT "go". The self-chain must NOT auto-approve or advance in review mode — it pauses so
    # the human can resolve Qs and give the explicit go. (Reverses rev3's "advance anyway".)
    approve, captured = _wire_self_chain_advance(monkeypatch)
    env = _chain({"doc_ref": "flowgate.default.0051.0001-R", "issued_to": "pm",
                  "continuation_target_seq": 6, "continuation_review_mode": 1}, doc_type="NR")
    assert env["continuation_paused"] is True
    assert "next_token" not in env
    approve.assert_not_called()  # review mode never auto-approves


def test_self_chain_target_reached_approves_last_then_done(monkeypatch):
    # 0086 TR0004 rework rev4 (reason #2): the LAST step (target reached) must be auto-approved
    # too — the run goes up to before final approval, so the last work doc ends approved, not
    # left submitted. Auto-approve happens BEFORE the done check.
    approve, captured = _wire_self_chain_advance(monkeypatch, item_seq=6)
    env = _chain({"doc_ref": "x", "issued_to": "pm",
                  "continuation_target_seq": 6, "continuation_review_mode": 0}, doc_type="TR")
    approve.assert_called_once()  # last step approved
    assert env["continuation_done"] is True
    assert env["continuation_remaining"] == 0
    assert "next_token" not in env  # no further step after the target


def test_self_chain_advances_and_embeds_next_token(monkeypatch):
    # Non-review: auto-approve (via the is_admin resolver) + advance + embed next token.
    approve, captured = _wire_self_chain_advance(monkeypatch)
    env = _chain({"doc_ref": "flowgate.default.0051.0001-R", "issued_to": "pm",
                  "continuation_target_seq": 6, "continuation_review_mode": 0}, doc_type="NR")
    approve.assert_called_once()  # report auto-approved (NR0003 §6-①)
    assert env["next_token"] == "NEXTRAW"
    assert env["next_mention"] == "NEXT-MENTION"
    assert env["continuation_remaining"] == 3
    assert captured["continuation_review_mode"] is False


def test_self_chain_pauses_without_approve_permission(monkeypatch):
    # A GENUINE non-approver (is_admin=0 → the is_admin stub grants no document.approve)
    # still pauses honestly (P0005 §4 — approve is never bypassed). This is the honest
    # pause, distinct from the old bug where every approver paused on the ∅ resolver.
    from modules.flow_gate.db import workflow_sequences as wfseq
    from modules.flow_gate.db import users as db_users
    monkeypatch.setattr(wfseq, "get_item_by_result_doc_id", lambda _d: {"item_seq": 2})
    monkeypatch.setattr(db_users, "get_by_id", lambda _uid: {"user_id": _uid, "is_admin": 0})
    env = _chain({"doc_ref": "x", "issued_to": "pm",
                  "continuation_target_seq": 6, "continuation_review_mode": 0}, doc_type="NR")
    assert env["continuation_paused"] is True
    assert "next_token" not in env
