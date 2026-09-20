"""flowgate.default.0551 T#2: last Q answer resumes the parked requester chain.

The production resume engine is exercised rather than reimplemented. Unit cases pin the
answer-side group gate and best-effort race policy; engine cases reuse 0359's real
admission/finalize/paused-row harness to prove lease ordering, restart recovery, chain
identity preservation, and a second question_pending stop after resume.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.db import group_ai_leases as db_group_ai_leases  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import q_service  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wds  # noqa: E402
from modules.flow_gate.services.ai_invoke import finalize  # noqa: E402

import test_ai_invoke_no_output_retry_0359 as base  # noqa: E402
from test_ai_invoke_no_output_retry_0359 import env  # noqa: E402,F401


DOC = "flowgate.default.0551.0007-T"
GROUP = "flowgate.default.0551"
API_BASE = "http://127.0.0.1:8089/flowgate/api/v1"


def _paused_row(**overrides):
    row = {
        "group_id": GROUP,
        "doc_ref": DOC,
        "paused_by": "u-requester",
        "paused_at": "2026-09-20T12:00:00+09:00",
        "stop_kind": "system",
        "stop_code": "question_pending",
        "stop_run_id": "aiv_requester",
        "chain_id": "aiv_chain_original",
    }
    row.update(overrides)
    return row


@pytest.fixture
def policy_env(monkeypatch):
    state = {
        "row": _paused_row(),
        "open_docs": [DOC],
        "resume_calls": [],
        "live": {},
    }

    monkeypatch.setattr(
        q_service.db_documents,
        "get_by_id",
        lambda doc_id: {"doc_id": doc_id, "group_id": GROUP},
    )
    monkeypatch.setattr(
        q_service.db_questions,
        "list_open_doc_ids_by_group",
        lambda group_id: list(state["open_docs"]),
    )
    monkeypatch.setattr(
        db_paused,
        "get_by_group",
        lambda group_id: dict(state["row"]) if state["row"] is not None else None,
    )
    monkeypatch.setattr(
        db_runs,
        "get",
        lambda run_id: {"run_id": run_id, "provider_id": "aip_requester"},
    )

    def _resume(**kwargs):
        state["resume_calls"].append(kwargs)
        state["row"] = None
        result = {
            "ok": True,
            "run_id": "aiv_resumed",
            "chain_id": "aiv_chain_original",
            "status": "running",
        }
        state["live"][result["run_id"]] = {}
        return result

    monkeypatch.setattr(svc, "resume_chain", _resume)
    monkeypatch.setattr(svc, "get_run_record", lambda run_id: state["live"].get(run_id))
    return state


class TestAnswerSidePolicy:
    def test_first_of_two_questions_does_not_resume_but_last_answer_does(self, policy_env):
        assert q_service.auto_resume_answered_chain(
            doc_id=DOC, api_base_url=API_BASE
        ) is None
        assert policy_env["resume_calls"] == []

        policy_env["open_docs"] = []
        trace = q_service.auto_resume_answered_chain(
            doc_id=DOC,
            api_base_url=API_BASE,
            locale="ko",
            responder_run={"run_id": "aiv_responder", "provider_id": "aip_responder"},
        )

        assert len(policy_env["resume_calls"]) == 1
        assert policy_env["resume_calls"][0]["user_id"] == "u-requester"
        assert trace == {
            "requester_run_id": "aiv_requester",
            "requester_provider_id": "aip_requester",
            "responder_run_id": "aiv_responder",
            "responder_provider_id": "aip_responder",
            "paused_chain_id": "aiv_chain_original",
            "resumed_run_id": "aiv_resumed",
            "resumed_chain_id": "aiv_chain_original",
        }
        assert policy_env["live"]["aiv_resumed"]["question_resume_trace"] == trace

    def test_duplicate_answer_observes_consumed_row_and_resumes_once(self, policy_env):
        policy_env["open_docs"] = []
        first = q_service.auto_resume_answered_chain(
            doc_id=DOC, api_base_url=API_BASE
        )
        second = q_service.auto_resume_answered_chain(
            doc_id=DOC, api_base_url=API_BASE
        )
        assert first is not None
        assert second is None
        assert len(policy_env["resume_calls"]) == 1

    @pytest.mark.parametrize(
        "row",
        [
            _paused_row(stop_kind="user"),
            _paused_row(stop_code="timeout"),
            None,
        ],
    )
    def test_only_question_pending_system_stop_is_eligible(self, policy_env, row):
        policy_env["row"] = row
        policy_env["open_docs"] = []
        assert q_service.auto_resume_answered_chain(
            doc_id=DOC, api_base_url=API_BASE
        ) is None
        assert policy_env["resume_calls"] == []

    def test_answer_cancel_race_is_best_effort_and_never_raises(self, policy_env, monkeypatch):
        policy_env["open_docs"] = []
        calls = []

        def _cancel_won(**kwargs):
            calls.append(kwargs)
            policy_env["row"] = None
            raise HTTPException(
                status_code=409,
                detail={"code": "resume_conflict", "message": "cancel consumed the row"},
            )

        monkeypatch.setattr(svc, "resume_chain", _cancel_won)
        assert q_service.auto_resume_answered_chain(
            doc_id=DOC, api_base_url=API_BASE
        ) is None
        assert len(calls) == 1
        assert policy_env["row"] is None


def _fake_store():
    store = MagicMock()

    @contextmanager
    def _transaction():
        yield store

    store.transaction.side_effect = _transaction
    return store


@pytest.mark.parametrize(
    ("unanswered", "expected_calls"),
    [([{"id": 11}], 0), ([], 1)],
)
def test_register_answer_invokes_auto_resume_only_after_last_item(
    monkeypatch, unanswered, expected_calls
):
    calls = []
    monkeypatch.setattr(q_service, "get_store", lambda: _fake_store())
    monkeypatch.setattr(
        q_service.db_questions,
        "get_container_by_doc",
        lambda doc_id: {"id": 1, "status": "pending", "project_id": "flowgate"},
    )
    monkeypatch.setattr(
        q_service.db_question_items,
        "get_by_pk",
        lambda item_id: {"id": item_id, "question_id": 1, "options": "[]"},
    )
    monkeypatch.setattr(q_service.db_answers, "insert", lambda **kwargs: None)
    monkeypatch.setattr(
        q_service.db_question_items, "increment_answer_count", lambda **kwargs: None
    )
    monkeypatch.setattr(
        q_service.db_question_items, "list_unanswered", lambda question_id: unanswered
    )
    monkeypatch.setattr(q_service.db_questions, "update_status", lambda *args: None)
    monkeypatch.setattr(
        q_service.db_answers,
        "list_by_question_item",
        lambda item_id: [{"id": 91}],
    )
    monkeypatch.setattr(q_service, "_notify_q_answered", lambda **kwargs: None)

    def _resume(**kwargs):
        calls.append(kwargs)
        return {
            "requester_run_id": "aiv_requester",
            "resumed_run_id": "aiv_resumed",
        }

    monkeypatch.setattr(q_service, "auto_resume_answered_chain", _resume)
    result = q_service.register_answer(
        doc_id=DOC,
        item_id=10,
        body="answer",
        author_kind="human",
        author_id="u-human",
        auto_resume_api_base_url=API_BASE,
    )

    assert len(calls) == expected_calls
    if unanswered:
        assert result["status"] == "pending"
        assert "question_resume_trace" not in result
    else:
        assert result["status"] == "done"
        assert result["question_resume_trace"]["resumed_run_id"] == "aiv_resumed"


def test_finalize_retries_only_after_responder_lease_release(env, monkeypatch):
    calls = []

    def _resume(**kwargs):
        calls.append(
            {
                **kwargs,
                "lease_at_call": db_group_ai_leases.get_active(base.GROUP),
            }
        )
        return None

    monkeypatch.setattr(q_service, "auto_resume_answered_chain", _resume)
    base._scripted_rework_worker(env, monkeypatch, [("answer registered", None)])
    started = base._start_rework(
        env,
        provider_id="aip_1",
        completion_oracle=lambda: True,
    )
    base._wait_finished(started["run_id"])
    base._wait_until(lambda: len(calls) == 1, message="answer resume finalize hook")

    assert calls[0]["lease_at_call"] is None
    assert calls[0]["responder_run"]["run_id"] == started["run_id"]


def test_restart_resume_preserves_chain_trace_and_requestions(env, monkeypatch):
    """One integrated timeline covers restart, identity carry, and repeated Q stop."""
    q_state = {"open": True}
    monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: doc_id)
    monkeypatch.setattr(
        svc.db_questions,
        "get_container_by_doc",
        lambda doc_id: {"id": 9, "status": "pending"} if q_state["open"] else None,
    )
    monkeypatch.setattr(
        q_service.db_questions,
        "list_open_doc_ids_by_group",
        lambda group_id: [base.DOC_REF] if q_state["open"] else [],
    )
    # T#1 is independently tested. This timeline controls the answer/resume edge only.
    monkeypatch.setattr(finalize, "_dispatch_question_responder", lambda run: None)

    base._scripted_worker(env, monkeypatch, [("need an answer", False)])
    requester_start = base._start(env, provider_id="aip_1")
    requester = base._wait_finished(requester_start["run_id"])
    assert requester["stop_code"] == "question_pending"

    durable = env["paused"].rows[base.GROUP]
    original_chain_id = requester["chain_id"]
    assert durable["chain_id"] == original_chain_id
    assert durable["stop_run_id"] == requester["run_id"]

    # Simulate a fresh server process: no in-memory requester run survives; only the
    # paused row and Q rows do. The final answer arrives after this reset.
    monkeypatch.setattr(svc, "_runs", {})
    q_state["open"] = False
    monkeypatch.setattr(
        db_runs,
        "get",
        lambda run_id: {"run_id": run_id, "provider_id": "aip_1"},
    )
    monkeypatch.setattr(
        wds,
        "advance_workflow",
        lambda **kwargs: {
            "token": "tok_resume",
            "token_id": "tok_resume_id",
            "scratch_dir": str(env["tmp"] / "resume-token"),
            "mention": "resume original work",
            "worker_document_type": "NR",
            "auto_handled_item_seqs": [],
        },
    )

    def _requestion(provider, prompt, run):
        q_state["open"] = True
        run["exit_code"] = 0
        run["last_message"] = "one more clarification is required"
        run["last_message_received"] = True
        return "started_ok", None

    monkeypatch.setattr(svc, "_cli_execute", _requestion)
    trace = q_service.auto_resume_answered_chain(
        doc_id=base.DOC_REF,
        api_base_url=API_BASE,
        responder_run={"run_id": "aiv_responder", "provider_id": "aip_2"},
    )
    assert trace is not None
    assert trace["requester_run_id"] == requester["run_id"]
    assert trace["requester_provider_id"] == "aip_1"
    assert trace["responder_run_id"] == "aiv_responder"
    assert trace["responder_provider_id"] == "aip_2"
    assert trace["paused_chain_id"] == original_chain_id
    assert trace["resumed_chain_id"] == original_chain_id
    assert trace["resumed_run_id"] != requester["run_id"]

    resumed = base._wait_finished(trace["resumed_run_id"])
    assert resumed["chain_id"] == original_chain_id
    assert resumed["doc_ref"] == base.DOC_REF
    assert resumed["issued_to"] == "usr_admin"
    assert resumed["question_resume_trace"] == trace
    assert resumed["stop_code"] == "question_pending"

    reparking = env["paused"].rows[base.GROUP]
    assert reparking["stop_run_id"] == resumed["run_id"]
    assert reparking["chain_id"] == original_chain_id
