"""0111 T-A3: bounded startup reassignment through the real Q-answer admission/worker/finalize.

Only provider execution and persistence are faked. Tests wait for finalization before
returning; a single claim/run/lease covers all unique independent startup candidates.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from tests.modules.flow_gate import test_ai_invoke_no_output_retry_0359 as base
from tests.modules.flow_gate.test_ai_invoke_no_output_retry_0359 import env  # noqa: F401
from tests.modules.flow_gate.test_q_responder_chain_0661 import FakeItems, _item
from modules.flow_gate.services import q_answer_invoke_service as answer, q_service
from modules.flow_gate.services.ai_invoke import finalize, review
from modules.flow_gate.db import questions as db_questions
from flowgate.card_settings import question_wait


@pytest.fixture
def question(env, monkeypatch):
    fake = FakeItems([_item(201, 1, asker_actual_provider_id="aip_3")]).install(monkeypatch)
    row = fake.rows[201]
    monkeypatch.setattr(answer.db_answers, "list_by_question_item", lambda pk:
                        [{"author_kind": "ai"}] if row.get("answer_count") else [])
    monkeypatch.setattr(answer, "issue_answer_token", lambda **kw: {
        "raw_token": "tok_raw", "token_id": "tok_test", "mention": "Answer item 201",
        "scratch_dir": str(env["tmp"] / "responder"),
    })
    monkeypatch.setattr(db_questions, "get_by_pk", lambda pk: {"doc_id": base.DOC_REF})
    monkeypatch.setattr(q_service, "_announce_responder_state", lambda *a, **kw: None)
    return fake, row


def dispatch(**kw):
    kw.setdefault("responder_candidates", [])  # automatic dispatch opts into T-A3
    return answer.dispatch_answer_run(
        doc={"doc_id": base.DOC_REF, "project_id": "flowgate", "group_id": base.GROUP},
        item={"id": 201, "asker_provider": {"ai_provider_id": "aip_3"}},
        issued_to="owner", api_base_url="http://localhost/flowgate/api/v1",
        provider_id="aip_1", provider_source="card_review_ai", **kw,
    )


def finish(res, row):
    run = base._wait_finished(res["run_id"])
    base._wait_until(lambda: row.get("responder_state") != "dispatched", message="item finalization")
    return run


def script(monkeypatch, row, first, *, second="answer", api=False):
    launches = []

    def execute(provider, prompt, run):
        launches.append(provider["id"])
        action = first if len(launches) == 1 else second
        if action == "answer":
            row["answer_count"] = 1
            run.update(last_message="Answer registered", last_message_received=True)
        elif action == "content_failure":
            run.update(last_message="Could not answer this question", last_message_received=True)
        elif action == "human":
            row["answer_count"] = 1
            return "fast_fail", "human answered during startup"
        elif action == "handoff":
            row["responder_state"] = "user_decision"
            return "fast_fail", "handed to a person"
        elif action == "lost_claim":
            row.update(responder_state="dispatched", responder_run_id="new_owner")
            return "fast_fail", "claim superseded"
        elif action == "cancelled":
            run["cancel_event"].set()
            return "fast_fail", "cancelled"
        elif action in ("timeout", "pause"):
            run["timed_out" if action == "timeout" else "pause_requested"] = True
        elif action in ("fast_fail", "spawn_failed", "api_error"):
            return action, "provider could not start"
        run["exit_code"] = 0
        return "started_ok", None

    monkeypatch.setattr(base.svc, "_api_execute" if api else "_cli_execute", execute)
    return launches


@pytest.mark.parametrize("failure", ["fast_fail", "spawn_failed", "api_error", "zero_output"])
@pytest.mark.parametrize("api", [False, True])
def test_next_independent_provider_answers_same_item_once(env, question, monkeypatch, failure, api):
    fake, row = question
    if api:
        env["chain"]["providers"] = [dict(p, exec_type="api") for p in env["chain"]["providers"]]
    launches = script(monkeypatch, row, failure, api=api)
    run = finish(dispatch(), row)
    assert launches == ["aip_1", "aip_2"]
    assert run["outcome"] == "complete" and run["provider_id"] == "aip_2"
    assert row["responder_state"] == "answered" and row["answer_count"] == 1
    assert len(fake.claims) == 1 and row["responder_attempts"] == 1
    assert len(base.svc._runs) == 1
    switches = [p for e, p in env["events"] if e == "ai_invoke_provider_switched"]
    assert len(switches) == 1 and switches[0]["attempt_no"] == 2
    assert "aip_3" not in launches


@pytest.mark.parametrize("count", [1, 2])
def test_exhaustion_waits_for_human_and_event_replay_never_retries(env, question, monkeypatch, count):
    fake, row = question
    env["chain"]["providers"] = [base.P3, base.P1, base.P1] + ([base.P2, base.P2] if count == 2 else [])
    launches = script(monkeypatch, row, "fast_fail", second="fast_fail")
    run = finish(dispatch(), row)
    assert launches == ["aip_1", *(["aip_2"] if count == 2 else [])]
    assert len(launches) <= len({p["id"] for p in env["chain"]["providers"]})
    assert row["responder_state"] == "failed" and row["answer_count"] == 0
    assert row["responder_error_code"] == "provider_failed"
    assert run["stop_code"] == "providers_exhausted"
    for _ in range(4):
        assert question_wait._dispatch(base.GROUP, {"node_key": "card"},
                                      {"run_id": "asker", "provider_id": "aip_3"}, [row], False) is False
        finalize._settle_question_responder_run(run, [row])
    assert len(launches) == count and len(fake.claims) == 1


def test_no_independent_candidate_records_human_wait_without_launch(env, question, monkeypatch):
    fake, row = question
    env["chain"]["providers"] = [base.P3]
    launches = script(monkeypatch, row, "answer")
    with pytest.raises(HTTPException) as exc:
        dispatch()
    assert exc.value.detail["code"] == "no_independent_responder"
    assert row["responder_state"] == "failed" and row["answer_count"] == 0
    assert fake.claims == [] and launches == [] and base.svc._runs == {}


def test_header_precedes_default_tail_after_reviewer_fails(env, question, monkeypatch):
    fake, row = question
    row["asker_actual_provider_id"] = "external_worker"
    env["chain"]["providers"] = [base.P2, base.P1, base.P3]
    launches = script(monkeypatch, row, "fast_fail")
    finish(dispatch(responder_candidates=[("aip_3", "header")]), row)
    assert launches == ["aip_1", "aip_3"]


@pytest.mark.parametrize("action,expected", [
    ("content_failure", "failed"), ("human", "answered"), ("handoff", "user_decision"),
    ("cancelled", "failed"), ("timeout", "failed"), ("pause", "failed"),
])
def test_nonstartup_failure_or_human_decision_never_tries_next_provider(env, question, monkeypatch, action, expected):
    fake, row = question
    launches = script(monkeypatch, row, action)
    run = finish(dispatch(), row)
    assert launches == ["aip_1"] and row["responder_state"] == expected
    if action == "content_failure":
        assert row["responder_error_code"] == "no_answer_registered"
    if action == "cancelled":
        assert run["end_reason"] == "cancelled"
    if action == "timeout":
        assert run["end_reason"] == "timeout"


def test_lost_item_owner_stops_fallback_and_stale_finalize_cannot_fail_new_owner(env, question, monkeypatch):
    fake, row = question
    launches = script(monkeypatch, row, "lost_claim")
    res = dispatch()
    run = base._wait_finished(res["run_id"])
    assert launches == ["aip_1"] and row["responder_run_id"] == "new_owner"
    finalize._settle_question_responder_run(run, [row])
    assert row["responder_state"] == "dispatched"


def test_0110_candidate_policy_is_unchanged_and_bounded(env):
    assert review.question_responder_candidates("flowgate", "aip_1", [
        ("aip_1", "card_review_ai"), ("disabled", "header"), ("aip_2", "header"),
        ("aip_2", "duplicate"),
    ]) == [("aip_2", "header"), ("aip_3", "default")]
    assert review.question_responder_candidates("flowgate", None, [("aip_2", "header")]) == []


@pytest.fixture
def manual_request(env, question, monkeypatch):
    """Drive the Q&A panel route with real dispatch/admission/worker/finalize."""
    import json
    from starlette.requests import Request
    from modules.flow_gate.api.v1 import q_tapi_routes
    from modules.flow_gate.api import token_routes

    doc = {"doc_id": base.DOC_REF, "project_id": "flowgate", "group_id": base.GROUP}
    item = {"id": 201, "asker_provider": {"ai_provider_id": "aip_3"}}
    monkeypatch.setattr(q_tapi_routes, "_resolve_dispatch_target",
                        lambda *args: (doc, item, None))
    monkeypatch.setattr(token_routes, "_build_api_base", lambda request: "http://localhost/flowgate/api/v1")
    request = Request({"type": "http", "method": "POST", "path": "/"})

    def invoke(provider_id):
        response = q_tapi_routes.post_request_ai_answer(
            base.DOC_REF, 201, request,
            body=q_tapi_routes.AiAnswerRequest(provider_id=provider_id),
            current_user={"user_id": "owner"},
        )
        return response.status_code, json.loads(response.body)

    return invoke


@pytest.mark.parametrize("provider_id", ["aip_1", "aip_3"])
@pytest.mark.parametrize("failure", ["fast_fail", "spawn_failed"])
def test_manual_explicit_provider_startup_failure_never_switches(
        env, question, monkeypatch, manual_request, provider_id, failure):
    fake, row = question
    launches = script(monkeypatch, row, failure)
    status, response = manual_request(provider_id)
    assert status == 200
    run = finish(response, row)
    assert launches == [provider_id]
    assert run["attempt_no"] == 1
    assert [attempt["provider_id"] for attempt in run["fallback_history"]] == [provider_id]
    assert run.get("responder_startup_policy") is None
    assert len(fake.claims) == 1 and fake.claims[0][2] == provider_id
    assert row["responder_state"] == "failed" and row["answer_count"] == 0
    assert not [payload for event, payload in env["events"] if event == "ai_invoke_provider_switched"]


def test_manual_inactive_explicit_provider_preserves_422_without_launch(
        env, question, monkeypatch, manual_request):
    fake, row = question
    launches = script(monkeypatch, row, "answer")
    status, response = manual_request("disabled")
    assert status == 422 and response["code"] == "provider_unavailable"
    assert row["responder_state"] == "failed"
    assert row["responder_error_code"] == "provider_unavailable"
    assert fake.claims == [] and launches == [] and base.svc._runs == {}


def test_manual_explicit_asker_provider_needs_no_independent_candidate(
        env, question, monkeypatch, manual_request):
    fake, row = question
    env["chain"]["providers"] = [base.P3]
    launches = script(monkeypatch, row, "answer")
    status, response = manual_request("aip_3")
    assert status == 200
    run = finish(response, row)
    assert launches == ["aip_3"] and run.get("responder_startup_policy") is None
    assert len(fake.claims) == 1 and row["responder_state"] == "answered"


def test_question_wait_legacy_adapter_enables_automatic_startup_chain(env, question, monkeypatch):
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.db import documents
    from modules.flow_gate.graph_workflow.services import ai_node_starter

    fake, row = question
    monkeypatch.setattr(documents, "get_by_id", lambda doc_id: {
        "doc_id": doc_id, "project_id": "flowgate", "group_id": base.GROUP,
    })
    monkeypatch.setattr(answer, "resolve_item", lambda doc_id, item_id: {"id": item_id})
    monkeypatch.setattr(ai_node_starter, "api_base_url", lambda: "http://localhost/flowgate/api/v1")
    launches = script(monkeypatch, row, "fast_fail")
    run = finish(legacy_adapter.ask_responder(base.DOC_REF, 201, "owner", "aip_1", "card_review_ai"), row)
    assert launches == ["aip_1", "aip_2"] and run["outcome"] == "complete"
    assert len(fake.claims) == 1 and row["responder_state"] == "answered"
