"""Unmanned-chain test-run wiring (group 0150).

Covers the chain entrance and scope-inheritance delta on top of test_test_run_0138.py:
the inbox action allowlist (the dead-code 400 fixed in T0004), the chain envelope on the
202, advance_workflow's TSR-head test_run token, and the TSR auto-approve gate.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock


def _resp_json(resp) -> dict:
    return json.loads(bytes(resp.body).decode("utf-8"))


class _FakeRequest:
    """Minimal Request stand-in for the inbox entry (headers + async json)."""

    def __init__(self, body: dict, bearer: str = "raw-token"):
        self._body = body
        self.headers = {"Authorization": f"Bearer {bearer}"}

    async def json(self):
        return self._body


def test_inbox_action_allowlist_accepts_test_run(monkeypatch):
    # Regression for the group 0150 NR0003 §4 finding: action=="test_run" was dispatched
    # below an allowlist that rejected it, so the chain entrance 400'd before the handler.
    from modules.flow_gate.api import inbox_routes

    sentinel = inbox_routes.JSONResponse(status_code=299, content={"reached": True})

    async def _stub(_request, _raw, _body):
        return sentinel

    monkeypatch.setattr(inbox_routes, "_handle_test_run", _stub)

    resp = asyncio.run(
        inbox_routes.inbox(_FakeRequest({"action": "test_run"}))
    )
    assert resp.status_code == 299  # dispatched to the handler, not the 400 allowlist


def test_inbox_action_allowlist_still_rejects_unknown(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    resp = asyncio.run(
        inbox_routes.inbox(_FakeRequest({"action": "bogus"}))
    )
    assert resp.status_code == 400


def _chain_token_rec(target_seq=7):
    return {
        "token_id": "tok-1",
        "project": "flowgate",
        "issued_to": "user-1",
        "action_scope": "test_run",
        "doc_ref": "flowgate.default.0150.0005-TS",
        "dry_run_count": 0,
        "continuation_target_seq": target_seq,
        "continuation_review_mode": 0,
        "continuation_locale": "ko",
    }


def test_inbox_test_run_chain_token_gets_continuation_envelope(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import test_run_service

    monkeypatch.setattr(
        inbox_routes.token_service, "verify", lambda _raw: _chain_token_rec()
    )
    monkeypatch.setattr(test_run_service, "token_can_run_tests", lambda *_a, **_k: True)
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_by_id",
        lambda _id: {"doc_id": _id, "project_id": "flowgate"},
    )
    monkeypatch.setattr(
        test_run_service,
        "validate_and_create_run",
        MagicMock(return_value={
            "ok": True,
            "run_id": "trun_20260704_000001",
            "doc_id": "flowgate.default.0150.0005-TS",
            "status": "running",
            "case_total": 1,
            "message": "Test run trun_20260704_000001 started (1 cases).",
        }),
    )
    monkeypatch.setattr(inbox_routes.token_service, "consume", MagicMock())

    resp = asyncio.run(
        inbox_routes._handle_test_run(
            MagicMock(),
            "raw",
            {
                "action": "test_run",
                "project": "flowgate",
                "doc_id": "flowgate.default.0150.0005-TS",
            },
        )
    )
    data = _resp_json(resp)
    assert resp.status_code == 202
    assert data["continuation"] is True
    assert data["continuation_async"] is True
    assert data["continuation_target_seq"] == 7
    assert "Do NOT write the TSR" in data["message"]


def test_inbox_test_run_ordinary_token_has_no_envelope(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import test_run_service

    rec = _chain_token_rec(target_seq=None)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: rec)
    monkeypatch.setattr(test_run_service, "token_can_run_tests", lambda *_a, **_k: True)
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_by_id",
        lambda _id: {"doc_id": _id, "project_id": "flowgate"},
    )
    monkeypatch.setattr(
        test_run_service,
        "validate_and_create_run",
        MagicMock(return_value={"ok": True, "run_id": "r", "status": "running"}),
    )
    monkeypatch.setattr(inbox_routes.token_service, "consume", MagicMock())

    resp = asyncio.run(
        inbox_routes._handle_test_run(
            MagicMock(),
            "raw",
            {
                "action": "test_run",
                "project": "flowgate",
                "doc_id": "flowgate.default.0150.0005-TS",
            },
        )
    )
    data = _resp_json(resp)
    assert resp.status_code == 202
    assert "continuation" not in data


def test_issue_test_run_request_chain_inherits_scope_and_mention(monkeypatch):
    from modules.flow_gate.services import test_run_service

    captured = {}

    def fake_issue(**kwargs):
        captured.update(kwargs)
        return {
            "raw_token": "RAW",
            "token_id": "tok-9",
            "expires_at": "2026-07-05T00:00:00+09:00",
            "scratch_dir": "scratch",
        }

    monkeypatch.setattr(test_run_service.token_service, "issue", fake_issue)
    monkeypatch.setattr(
        test_run_service.db_docs,
        "get_by_id",
        lambda _id: {
            "doc_id": _id,
            "project_id": "flowgate",
            "module": "default",
            "group_id": "flowgate.default.0150",
            "title": "TS",
        },
    )

    result = test_run_service.issue_test_run_request(
        doc_id="flowgate.default.0150.0005-TS",
        issued_to="user-1",
        api_base_url="http://x/api/v1",
        continuation_target_seq=7,
        continuation_review_mode=False,
        locale="ko",
        continuous=True,
    )

    assert captured["action_scope"] == "test_run"
    assert captured["continuation_target_seq"] == 7
    assert captured["continuation_locale"] == "ko"
    assert result["action_scope"] == "test_run"
    assert "UNMANNED" in result["mention"]  # chain framing present
    assert "Do NOT write the TSR" in result["mention"]
    assert '"action": "test_run"' in result["mention"]


def test_issue_test_run_request_manned_stays_ordinary(monkeypatch):
    from modules.flow_gate.services import test_run_service

    captured = {}

    def fake_issue(**kwargs):
        captured.update(kwargs)
        return {
            "raw_token": "RAW",
            "token_id": "tok-9",
            "expires_at": "2026-07-05T00:00:00+09:00",
            "scratch_dir": "scratch",
        }

    monkeypatch.setattr(test_run_service.token_service, "issue", fake_issue)
    monkeypatch.setattr(
        test_run_service.db_docs,
        "get_by_id",
        lambda _id: {
            "doc_id": _id,
            "project_id": "flowgate",
            "module": "default",
            "group_id": "flowgate.default.0150",
            "title": "TS",
        },
    )

    result = test_run_service.issue_test_run_request(
        doc_id="flowgate.default.0150.0005-TS",
        issued_to="user-1",
        api_base_url="http://x/api/v1",
    )

    assert captured["continuation_target_seq"] is None
    assert captured["continuation_locale"] is None
    assert "UNMANNED" not in result["mention"]


def test_chain_auto_approve_tsr_requires_continuation_token(monkeypatch):
    from modules.flow_gate.db import tokens as db_tokens
    from modules.flow_gate.services import test_run_service

    approve_calls = []

    def fake_transition(**kwargs):
        approve_calls.append(kwargs)

    import modules.flow_gate.workflow.pipeline_service as pipeline_service
    monkeypatch.setattr(pipeline_service, "transition_document_review", fake_transition)

    import modules.flow_gate.workflow.routers.workflow as workflow_router
    monkeypatch.setattr(
        workflow_router, "_get_user_permissions", lambda _u: {"document.approve"}
    )
    from modules.flow_gate.db import users as db_users
    monkeypatch.setattr(
        db_users, "get_by_id", lambda _id: {"user_id": _id, "is_admin": 1}
    )
    monkeypatch.setattr(
        test_run_service.db_docs,
        "get_by_id",
        lambda _id: {"doc_id": _id, "id": 42, "group_id": "flowgate.default.0150"},
    )
    import modules.flow_gate.workflow.event_logger as event_logger
    monkeypatch.setattr(
        event_logger, "log_continuous_work_ended", lambda **_k: None, raising=False
    )

    ts_doc = {
        "doc_id": "flowgate.default.0150.0005-TS",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0150",
    }

    # 1) manned run: consumed token has NULL continuation → no auto-approve
    monkeypatch.setattr(
        db_tokens,
        "get_latest_consumed_by_scope_doc_ref",
        lambda _scope, _ref: {"issued_to": "user-1", "continuation_target_seq": None},
    )
    test_run_service._maybe_chain_auto_approve_tsr(ts_doc, "flowgate.default.0150.0006-TSR")
    assert approve_calls == []

    # 2) chain run: continuation token → approve fires
    monkeypatch.setattr(
        db_tokens,
        "get_latest_consumed_by_scope_doc_ref",
        lambda _scope, _ref: {"issued_to": "user-1", "continuation_target_seq": 7},
    )
    test_run_service._maybe_chain_auto_approve_tsr(ts_doc, "flowgate.default.0150.0006-TSR")
    assert len(approve_calls) == 1
    assert approve_calls[0]["action"] == "approve"
    assert approve_calls[0]["doc_id"] == "flowgate.default.0150.0006-TSR"


def test_cancelled_chain_run_never_triggers_auto_approve_repair_notify_or_next_token(monkeypatch, tmp_path):
    """flowgate.default.0358 T0004 완료 기준 / 위험 1: a cancelled run inside an
    unmanned chain must not silently resurrect the chain. _maybe_chain_auto_approve_tsr
    (the only path to the next chain token, via advance_workflow's TSR-head wiring) is
    reachable only from assemble_tsr's own tail call (:1679) — this pins that a
    cancelled run never reaches assemble_tsr, so auto-approve/repair/failure-notify/the
    next token are all transitively unreachable, not just individually unasserted."""
    from modules.flow_gate.services import test_run_service as svc

    doc = {
        "doc_id": "flowgate.default.0150.9200-TS",
        "project_id": "flowgate",
        "branch": "main",
        "group_id": "flowgate.default.0150",
    }
    run = {"run_id": "trun_chain_cancel", "doc_id": doc["doc_id"], "revision_no": 1}

    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(svc.db_test_runs, "cas_cancelling_to_cancelled", lambda _rid, **_k: None)
    monkeypatch.setattr(
        svc.db_test_runs, "get_run",
        lambda _rid: {**run, "status": "cancelled", "error": "cancelled_by_user"},
    )
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root", lambda *a, **k: tmp_path
    )
    monkeypatch.setattr(svc, "_emit_finished", lambda *_a, **_k: None)

    assemble_calls = []
    monkeypatch.setattr(svc, "assemble_tsr", lambda *a, **k: assemble_calls.append(1))
    auto_approve_calls = []
    monkeypatch.setattr(
        svc, "_maybe_chain_auto_approve_tsr", lambda *a, **k: auto_approve_calls.append(1)
    )
    recovery_calls = []
    monkeypatch.setattr(
        svc.engine_recipe_service, "handle_run_failure", lambda *a, **k: recovery_calls.append(1)
    )
    notify_calls = []
    monkeypatch.setattr(svc, "_maybe_notify_chain_failure", lambda *a, **k: notify_calls.append(1))

    # Pre-register the cancel, as request_cancel does before the worker picks up.
    entry = svc._register_active_run(run["run_id"])
    entry.cancel_event.set()
    try:
        svc._execute_run_inner(run)
    finally:
        svc._active_runs.pop(run["run_id"], None)

    assert assemble_calls == []
    assert auto_approve_calls == []  # unreachable: only called from inside assemble_tsr
    assert recovery_calls == []
    assert notify_calls == []


def test_advance_workflow_tsr_head_mints_test_run_token(monkeypatch):
    from modules.flow_gate.services import workflow_decision_service as wds
    from modules.flow_gate.services import test_run_service

    spine = {
        "doc_id": "flowgate.default.0150.0001-R",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0150",
        "module": "default",
        "seq": 1,
        "type_code": "R",
    }
    ts_doc = {
        "doc_id": "flowgate.default.0150.0005-TS",
        "project_id": "flowgate",
        "group_id": "flowgate.default.0150",
        "type_code": "TS",
        "doc_review_status": "approved",
    }
    docs = {spine["doc_id"]: spine, ts_doc["doc_id"]: ts_doc}
    monkeypatch.setattr(wds.db_documents, "get_by_id", lambda _id: docs.get(_id))
    monkeypatch.setattr(
        wds.db_wfseq, "get_sequence_for_member_doc", lambda _id: {"id": 11}
    )
    monkeypatch.setattr(
        wds, "_auto_complete_instruction_heads", lambda **_k: 0
    )
    monkeypatch.setattr(
        wds.db_wfseq,
        "get_effective_head",
        lambda _sid: {
            "id": 99,
            "type": "TSR",
            "label": "테스트레포트",
            "item_seq": 6,
            "result_doc_id": None,
            "result_doc_review_status": None,
        },
    )
    monkeypatch.setattr(
        wds.db_wfseq,
        "get_predecessor_result_doc_id",
        lambda _sid, _hid: ts_doc["doc_id"],
    )
    from modules.flow_gate.db import tokens as db_tokens
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _ref: None)

    issued = {}

    def fake_issue_request(**kwargs):
        issued.update(kwargs)
        return {
            "doc_ref": kwargs["doc_id"],
            "action_scope": "test_run",
            "group_id": "flowgate.default.0150",
            "token": "RAW",
            "token_id": "tok-7",
            "expires_at": "2026-07-05T00:00:00+09:00",
            "scratch_dir": "scratch",
            "mention": "MENTION",
        }

    monkeypatch.setattr(test_run_service, "issue_test_run_request", fake_issue_request)

    adv = wds.advance_workflow(
        doc_id=spine["doc_id"],
        issued_to="user-1",
        api_base_url="http://x/api/v1",
        locale="ko",
        continuous=True,
        continuation_target_seq=7,
        continuation_review_mode=False,
    )

    assert issued["doc_id"] == ts_doc["doc_id"]
    assert issued["continuation_target_seq"] == 7
    assert issued["continuous"] is True
    assert adv["action_scope"] == "test_run"
    assert adv["token"] == "RAW"
    assert adv["mention"] == "MENTION"
    assert adv["continuation_remaining"] == 2  # target 7, head item_seq 6
