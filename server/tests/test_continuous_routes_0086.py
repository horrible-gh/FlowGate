"""Continuous (unmanned) work — group 0086 R0001 (T0003): HTTP route wiring.

The service layer (advance_workflow / token_service.issue / mention continuous branch /
inbox self-chain) is covered by test_continuous_work_0051.py. This module covers the thin
route layer added in 0086 so the FE can START a continuous run:

  • POST /workflow/advance forwards continuous / continuation_target_seq /
    continuation_review_mode into advance_workflow (the primary FE path).
  • The advance + token-issue request bodies accept the new continuation fields.
  • POST /token/issue (fallback path) forwards the continuation flags and flips the
    mention to its continuous branch when a target is given.
"""
from __future__ import annotations

from modules.flow_gate.api.v1 import workflow_decision_routes as wdr
from modules.flow_gate.api import token_routes as tr
from modules.flow_gate.services import workflow_decision_service as wds


class _FakeRequest:
    def __init__(self):
        self.headers = {"x-locale": "ko"}
        self.base_url = "http://h/"


# ── request-body models accept the new fields ─────────────────────────────────

def test_advance_body_accepts_continuation_fields():
    body = wdr.AdvanceBodyRequest(
        doc_id="flowgate.default.0086.0001-R",
        continuous=True,
        continuation_target_seq=6,
        continuation_review_mode=True,
        continuation_instruction_mode="ai_direct",
    )
    assert body.continuous is True
    assert body.continuation_target_seq == 6
    assert body.continuation_review_mode is True
    assert body.continuation_instruction_mode == "ai_direct"


def test_advance_body_defaults_are_ordinary():
    body = wdr.AdvanceBodyRequest(doc_id="x")
    assert body.continuous is False
    assert body.continuation_target_seq is None
    assert body.continuation_review_mode is False
    assert body.continuation_instruction_mode is None


def test_token_issue_request_accepts_continuation_fields():
    body = tr.TokenIssueRequest(
        project="flowgate", group="0086",
        continuation_target_seq=6, continuation_review_mode=True,
        continuation_instruction_mode="ai_direct",
    )
    assert body.continuation_target_seq == 6
    assert body.continuation_review_mode is True
    assert body.continuation_instruction_mode == "ai_direct"


# ── /workflow/advance forwards continuation params (primary FE path) ───────────

def _wire_advance_route(monkeypatch, captured):
    monkeypatch.setattr(wdr, "verify_bearer", lambda _r: {"issued_to": "pm-1"})
    monkeypatch.setattr(wdr._db_documents, "get_by_id", lambda _id: {"group_id": "g", "project_id": "flowgate"})
    monkeypatch.setattr(wdr._process_service, "is_group_disposed", lambda _g: False)
    monkeypatch.setattr(
        wdr, "advance_workflow",
        lambda **k: captured.update(k) or {"ok": True, "continuation_remaining": 3},
    )


def test_advance_route_forwards_continuation(monkeypatch):
    captured: dict = {}
    _wire_advance_route(monkeypatch, captured)
    resp = wdr.post_workflow_advance_rpc(
        wdr.AdvanceBodyRequest(
            doc_id="flowgate.default.0086.0001-R",
            continuous=True, continuation_target_seq=6, continuation_review_mode=True,
            continuation_instruction_mode="ai_direct",
        ),
        _FakeRequest(),
    )
    assert resp.status_code == 201
    assert captured["continuous"] is True
    assert captured["continuation_target_seq"] == 6
    assert captured["continuation_review_mode"] is True
    assert captured["continuation_instruction_mode"] == "ai_direct"


def test_advance_route_ordinary_advance_unchanged(monkeypatch):
    captured: dict = {}
    _wire_advance_route(monkeypatch, captured)
    wdr.post_workflow_advance_rpc(
        wdr.AdvanceBodyRequest(doc_id="flowgate.default.0086.0001-R"),
        _FakeRequest(),
    )
    assert captured["continuous"] is False
    assert captured["continuation_target_seq"] is None
    assert captured["continuation_review_mode"] is False


# ── /token/issue fallback forwards flags + flips the mention to continuous ─────

def test_token_issue_route_forwards_continuation_and_continuous_mention(monkeypatch):
    issue_kw: dict = {}
    monkeypatch.setattr(tr, "validate_project_id", lambda _p: None)
    monkeypatch.setattr(tr, "validate_group_id", lambda _g: None)
    monkeypatch.setattr(tr, "validate_doc_id", lambda _d: None)
    monkeypatch.setattr(tr.db_projects, "get_by_id", lambda _p: {"project_id": "flowgate"})
    monkeypatch.setattr(tr, "_resolve_group", lambda _p, _g: "flowgate.default.0086")
    monkeypatch.setattr(tr, "has_permission", lambda _u, _p, _perm: True)
    monkeypatch.setattr(
        tr.token_service, "issue",
        lambda **k: issue_kw.update(k) or {
            "raw_token": "RAW", "token_id": "tok", "expires_at": "x",
            "scratch_dir": "/tmp/s", "group_id": "flowgate.default.0086",
        },
    )
    mention_kw: dict = {}
    monkeypatch.setattr(
        tr, "_build_mention_for_token",
        lambda **k: mention_kw.update(k) or "MENTION",
    )

    resp = tr.issue_token(
        tr.TokenIssueRequest(
            project="flowgate", module="default", group="0086",
            action_scope="new", doc_ref="flowgate.default.0086.0001-R",
            continuation_target_seq=6, continuation_review_mode=True,
        ),
        _FakeRequest(),
        {"user_id": "pm-1"},
    )
    assert resp.ok is True
    assert issue_kw["continuation_target_seq"] == 6
    assert issue_kw["continuation_review_mode"] is True
    # A continuation target flips the mention to its continuous branch.
    assert mention_kw["continuous"] is True


def test_token_issue_route_ordinary_is_not_continuous(monkeypatch):
    monkeypatch.setattr(tr, "validate_project_id", lambda _p: None)
    monkeypatch.setattr(tr, "validate_group_id", lambda _g: None)
    monkeypatch.setattr(tr, "validate_doc_id", lambda _d: None)
    monkeypatch.setattr(tr.db_projects, "get_by_id", lambda _p: {"project_id": "flowgate"})
    monkeypatch.setattr(tr, "_resolve_group", lambda _p, _g: "flowgate.default.0086")
    monkeypatch.setattr(tr, "has_permission", lambda _u, _p, _perm: True)
    monkeypatch.setattr(
        tr.token_service, "issue",
        lambda **k: {"raw_token": "RAW", "token_id": "tok", "expires_at": "x",
                     "scratch_dir": "/tmp/s", "group_id": "flowgate.default.0086"},
    )
    mention_kw: dict = {}
    monkeypatch.setattr(
        tr, "_build_mention_for_token",
        lambda **k: mention_kw.update(k) or "MENTION",
    )
    tr.issue_token(
        tr.TokenIssueRequest(
            project="flowgate", module="default", group="0086",
            action_scope="new", doc_ref="flowgate.default.0086.0001-R",
        ),
        _FakeRequest(),
        {"user_id": "pm-1"},
    )
    assert mention_kw["continuous"] is False


# ── Start BEFORE the workflow is decided ("워크플로 결정부터", TR0004 rework) ─────────
# The reviewer rejected TR0004 because continuous work could not run until the workflow
# was decided. The continuous run now starts FROM the workflow decision: the decision-
# request issues a workflow_decide token carrying the run-to-end sentinel, and once the
# decision is saved the decide endpoint self-chains the first real step.

def test_decision_request_body_accepts_continuation_fields():
    body = wdr.WorkflowDecisionRequestBody(
        doc_id="flowgate.default.0086.0001-R",
        continuous=True,
        continuation_review_mode=True,
    )
    assert body.continuous is True
    assert body.continuation_review_mode is True


def test_decision_request_body_defaults_are_ordinary():
    body = wdr.WorkflowDecisionRequestBody(doc_id="x")
    assert body.continuous is False
    assert body.continuation_review_mode is False


def test_request_workflow_decision_continuous_carries_sentinel_and_continuous_mention(monkeypatch):
    issue_kw: dict = {}
    mention_kw: dict = {}
    monkeypatch.setattr(wds.db_documents, "get_by_id",
                        lambda _id: {"type_code": "R", "group_id": "flowgate.default.0086",
                                     "project_id": "flowgate", "seq": 1, "doc_id": _id})
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", lambda _id: None)  # not decided
    monkeypatch.setattr(wds.db_documents, "get_group_max_seq", lambda _g: 1)
    monkeypatch.setattr(wds.db_documents, "fetch_recent_group_docs", lambda **k: [])
    monkeypatch.setattr(
        wds.token_service, "issue",
        lambda **k: issue_kw.update(k) or {
            "raw_token": "RAW", "token_id": "tok", "expires_at": "x", "scratch_dir": "/tmp/s"},
    )
    monkeypatch.setattr(
        wds.mention_service, "build_workflow_decision_mention",
        lambda **k: mention_kw.update(k) or "MENTION",
    )
    result = wds.request_workflow_decision(
        doc_id="flowgate.default.0086.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        continuous=True, continuation_review_mode=True,
    )
    # The workflow_decide token carries the run-to-end sentinel (target unknown pre-decision).
    assert issue_kw["continuation_target_seq"] == wds.CONTINUATION_TO_END
    assert issue_kw["continuation_review_mode"] is True
    # The decision mention swaps to its continuous (delegation/unmanned) branch.
    assert mention_kw["continuous"] is True
    assert result["continuous"] is True


def test_request_workflow_decision_ordinary_is_not_continuous(monkeypatch):
    issue_kw: dict = {}
    mention_kw: dict = {}
    monkeypatch.setattr(wds.db_documents, "get_by_id",
                        lambda _id: {"type_code": "R", "group_id": "flowgate.default.0086",
                                     "project_id": "flowgate", "seq": 1, "doc_id": _id})
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", lambda _id: None)
    monkeypatch.setattr(wds.db_documents, "get_group_max_seq", lambda _g: 1)
    monkeypatch.setattr(wds.db_documents, "fetch_recent_group_docs", lambda **k: [])
    monkeypatch.setattr(wds.token_service, "issue",
                        lambda **k: issue_kw.update(k) or {
                            "raw_token": "RAW", "token_id": "tok", "expires_at": "x", "scratch_dir": "/tmp/s"})
    monkeypatch.setattr(wds.mention_service, "build_workflow_decision_mention",
                        lambda **k: mention_kw.update(k) or "MENTION")
    wds.request_workflow_decision(
        doc_id="flowgate.default.0086.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
    )
    assert issue_kw["continuation_target_seq"] is None
    assert issue_kw["continuation_review_mode"] is False
    assert mention_kw["continuous"] is False


def test_kickoff_resolves_sentinel_to_last_item_and_advances(monkeypatch):
    adv_kw: dict = {}
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", lambda _id: {"id": 7})
    monkeypatch.setattr(
        wds.db_wfseq, "get_sequence_items",
        lambda _sid: [{"item_seq": 3}, {"item_seq": 4}, {"item_seq": 6}],
    )
    monkeypatch.setattr(
        wds, "advance_workflow",
        lambda **k: adv_kw.update(k) or {
            "token": "NEXT", "token_id": "tok2", "mention": "NEXT_MENTION",
            "expires_at": "y", "continuation_remaining": 4},
    )
    env = wds.continuation_kickoff_after_decide(
        doc_id="flowgate.default.0086.0001-R", issued_to="pm-1",
        api_base_url="http://h/flow_gate/api/v1",
        continuation_target_seq=wds.CONTINUATION_TO_END,
        continuation_review_mode=False,
    )
    # Sentinel resolved to the last item_seq (6) and forwarded as the concrete target.
    assert adv_kw["continuation_target_seq"] == 6
    assert adv_kw["continuous"] is True
    assert env["continuation"] is True
    assert env["continuation_target_seq"] == 6
    assert env["next_token"] == "NEXT"
    assert env["next_mention"] == "NEXT_MENTION"
    assert env["continuation_remaining"] == 4
    assert "continuation_paused" not in env


def test_kickoff_no_op_for_ordinary_token():
    # An ordinary workflow_decide token (no continuation metadata) does not chain.
    assert wds.continuation_kickoff_after_decide(
        doc_id="x", issued_to="pm-1", api_base_url="http://h/",
        continuation_target_seq=None,
    ) is None


def test_kickoff_pauses_when_advance_blocks(monkeypatch):
    def _boom(**_k):
        raise ValueError("sequence_exhausted:x")
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_by_doc_id", lambda _id: {"id": 7})
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_items", lambda _sid: [{"item_seq": 1}])
    monkeypatch.setattr(wds, "advance_workflow", _boom)
    env = wds.continuation_kickoff_after_decide(
        doc_id="x", issued_to="pm-1", api_base_url="http://h/",
        continuation_target_seq=wds.CONTINUATION_TO_END,
    )
    # A blocked advance pauses the chain (never raises) — the decision is already saved.
    assert env["continuation_paused"] is True
    assert "next_token" not in env


def test_decide_route_kicks_off_continuation_for_continuation_token(monkeypatch):
    kickoff_kw: dict = {}
    monkeypatch.setattr(
        wdr, "verify_bearer",
        lambda _r: {
            "issued_to": "pm-1", "project": "flowgate", "token_id": "tok",
            "action_scope": "workflow_decide", "doc_ref": "flowgate.default.0086.0001-R",
            "continuation_target_seq": wds.CONTINUATION_TO_END, "continuation_review_mode": False,
            "ai_run_id": "aiv_decide_1", "is_admin": False,
        },
    )
    monkeypatch.setattr(wdr, "decide_workflow", lambda **k: {"status": "decided"})
    monkeypatch.setattr(
        wdr._db_documents, "get_by_id",
        lambda _id: {"group_id": "flowgate.default.0086", "project_id": "flowgate",
                     "status": "open", "doc_review_status": "wf_in_progress"},
    )
    monkeypatch.setattr(wdr._process_service, "is_group_disposed", lambda _g: False)
    import modules.flow_gate.services.token_service as _ts
    monkeypatch.setattr(_ts, "consume", lambda **k: None)
    monkeypatch.setattr(
        wdr, "continuation_kickoff_after_decide",
        lambda **k: kickoff_kw.update(k) or {"continuation": True, "next_token": "NEXT"},
    )

    resp = wdr.post_workflow_decide(
        "flowgate.default.0086.0001-R",
        wdr.DecideRequest(doc_class="R", sequence=[wdr.SequenceItem(id=1, type="T", label="작업지시")]),
        _FakeRequest(),
    )
    assert resp.status_code == 201
    # The decide endpoint forwarded the token's continuation metadata to the kickoff…
    assert kickoff_kw["continuation_target_seq"] == wds.CONTINUATION_TO_END
    assert kickoff_kw["doc_id"] == "flowgate.default.0086.0001-R"
    assert kickoff_kw["ai_run_id"] == "aiv_decide_1"
    # …and merged the resulting envelope into the 201 body so the worker proceeds.
    import json as _json
    payload = _json.loads(resp.body)
    assert payload["next_token"] == "NEXT"
    assert payload["continuation"] is True
