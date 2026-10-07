"""flowgate.default.0684 T#3 — one official result source and a chain that waits on the run.

D#1 §3-8 / §3-9 (0684.0006-D), CH S6:

* An automated Case gets its official result from a server run only. A person (UI) or a
  test_run token may record results for manual/external Cases only; a result for an
  automated Case -- one no server runner exists for too -- is refused item by item, with its
  reason, and that Case stays NOT_RUN until the runner is extended.
* The test_run hand-off mention asks for the entered Cases only.
* The TS view tells the screen which Cases are entered and what the newest server run did.
* An unmanned chain whose next head is the TSR does not start a hop while the server run is
  in progress: it is bound to the run and parked on the test gate. The run's outcome resumes
  it, routes it to failure-origin review, resumes it for manual/external entry, or stops it.

The fixture is the 0549 suite's (a real SQLite result store over every migration).
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from test_test_spec_0549 import (  # noqa: F401 -- ``env`` is the fixture these tests use
    CASE_TC1_AUTOMATED,
    CASE_TC2,
    CASE_TC3,
    GROUP,
    TS_ID,
    TSR_ID,
    _approve_ts,
    _bind_tsr_slot,
    _chain,
    _pass_worker,
    _spec_doc,
    _submit,
    _token,
    env,
)

CHAIN = {"api_base_url": "http://h:8089/flowgate/api/v1", "locale": "ko"}
CASE_TC4_CLIENT = (
    "### TC-004: 클라이언트 화면 시험\n"
    "- category: normal\n"
    "- requirement: AC-4\n"
    "- execution_mode: automated\n"
    "- required: true\n"
    "- procedure: vitest 실행\n"
    "- expected: 통과\n"
    "- check_points: 화면\n"
    "- automation_ref: client/tests/main/x.spec.ts\n\n"
)


def _server_run_and_manual(env):
    """TC-001 runs on the server; TC-002 (manual, required) and TC-003 are entered."""
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC2 + CASE_TC3)


# ── 1. Result intake: entered Cases only ───────────────────────────────────────


def test_result_entry_cases_are_the_manual_and_external_ones_only(env):
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC2 + CASE_TC3 + CASE_TC4_CLIENT)
    cases = env.svc.load_spec_ts(TS_ID)[1]["cases"]
    # TC-004 is automated with no server runner (.spec.ts): it is not entered either.
    assert env.svc.result_entry_case_ids(cases) == ["TC-002", "TC-003"]


def test_a_result_for_an_automated_case_without_a_runner_is_refused_and_stays_not_run(env):
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC2 + CASE_TC3 + CASE_TC4_CLIENT)
    recorded, outcome = _submit(env, [{"case_id": "TC-004", "status": "PASS"},
                                      {"case_id": "TC-002", "status": "PASS"}])
    assert recorded["refused"] == [{"case_id": "TC-004", "status": "PASS",
                                    "reason": "automated_case_server_run_only"}]
    cases = {c["case_no"]: c for c in env.db_test_runs.list_cases(recorded["run"]["run_id"])}
    assert cases["TC-004"]["case_status"] == "NOT_RUN"
    assert cases["TC-002"]["case_status"] == "PASS"
    assert outcome["gate_passed"] is False  # a person's PASS never opens the gate for it
    with pytest.raises(HTTPException) as caught:
        _submit(env, [{"case_id": "TC-004", "status": "PASS"}])
    assert caught.value.status_code == 422
    assert caught.value.detail["error"] == "automated_results_server_only"
    assert caught.value.detail["entry_case_ids"] == ["TC-002", "TC-003"]


def test_a_mixed_submission_records_the_entered_cases_and_refuses_the_server_run_case(env):
    _server_run_and_manual(env)
    recorded, outcome = _submit(env, [{"case_id": "TC-001", "status": "PASS"},
                                      {"case_id": "TC-002", "status": "PASS"}])
    assert recorded["refused"] == [{"case_id": "TC-001", "status": "PASS",
                                    "reason": "automated_case_server_run_only"}]
    cases = {c["case_no"]: c for c in env.db_test_runs.list_cases(recorded["run"]["run_id"])}
    assert cases["TC-002"]["case_status"] == "PASS"
    assert cases["TC-001"]["case_status"] == "NOT_RUN"  # never recorded from an entry
    assert outcome["overall"] == "NOT_RUN" and outcome["gate_passed"] is False
    body = env.svc.spec_result_response(recorded["run"]["run_id"], outcome)
    assert body["refused_results"][0]["case_id"] == "TC-001"
    assert "Refused 1 result(s)" in body["message"]


def test_a_submission_of_server_run_cases_only_is_refused_without_a_record(env):
    _server_run_and_manual(env)
    with pytest.raises(HTTPException) as caught:
        _submit(env, [{"case_id": "TC-001", "status": "PASS"}])
    detail = caught.value.detail
    assert caught.value.status_code == 422
    assert detail["error"] == "automated_results_server_only"
    assert detail["refused_results"][0]["case_id"] == "TC-001"
    assert detail["entry_case_ids"] == ["TC-002", "TC-003"]
    assert env.db_test_runs.list_by_doc(TS_ID) == []


def test_a_junit_report_for_a_server_run_case_is_refused_too(env):
    _server_run_and_manual(env)
    xml = ("<testsuite><testcase name='TC_001 forbidden'>"
           "<properties><property name='flowgate.case_id' value='TC-001'/></properties>"
           "</testcase></testsuite>")
    with pytest.raises(HTTPException) as caught:
        _submit(env, junit_xml=xml)
    assert caught.value.detail["error"] == "automated_results_server_only"


def test_the_inbox_token_records_entries_and_reports_what_it_refused(env, monkeypatch):
    from unittest.mock import MagicMock

    from inbox_client import post_inbox
    from modules.flow_gate.api import inbox_routes

    _server_run_and_manual(env)
    monkeypatch.setattr(inbox_routes.token_service, "verify", lambda _raw: {**_token(14),
                                                                            "continuation_target_seq": None})
    monkeypatch.setattr(env.svc, "token_can_run_tests", lambda *_a, **_k: True)
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda doc_id: env.docs.get(doc_id))
    consume = MagicMock()
    monkeypatch.setattr(inbox_routes.token_service, "consume", consume)
    resp = post_inbox({"action": "test_run", "project": "flowgate", "doc_id": TS_ID,
                       "results": [{"case_id": "TC-001", "status": "PASS"},
                                   {"case_id": "TC-002", "status": "PASS"}]})
    data = resp.json()
    assert resp.status_code == 201, data
    assert [item["case_id"] for item in data["refused_results"]] == ["TC-001"]
    consume.assert_called_once()

    only_automated = post_inbox({"action": "test_run", "project": "flowgate", "doc_id": TS_ID,
                                 "results": [{"case_id": "TC-001", "status": "PASS"}]})
    assert only_automated.status_code == 422
    assert only_automated.json()["error"] == "automated_results_server_only"
    assert consume.call_count == 1  # a refused submission does not spend the token


def test_the_server_run_still_records_its_automated_result(env, monkeypatch, tmp_path):
    _server_run_and_manual(env)
    _approve_ts(env, monkeypatch, tmp_path)
    _pass_worker(env, monkeypatch, tmp_path)
    env.svc.execute_run(env.db_test_runs.pick_next_running())
    recorded = env.db_test_runs.latest_by_doc(TS_ID)
    cases = {c["case_no"]: c["case_status"] for c in env.db_test_runs.list_cases(recorded["run_id"])}
    assert cases["TC-001"] == "PASS"
    meta = json.loads(recorded["result_meta"])
    assert meta["execution_run_id"] and meta["refused_results"] == []


# ── 2. The hand-off mention asks for entered Cases only ───────────────────────


def test_the_result_entry_mention_lists_entered_cases_and_names_the_server_run(env):
    _server_run_and_manual(env)
    text = env.svc._build_spec_result_mention(doc=env.docs[TS_ID], api_base_url="http://h/api/v1",
                                              raw_token="raw-tok", continuous=True, locale="ko")
    entered, _, server = text.partition("Automated Cases -- server run only (do not submit):")
    assert "- TC-002 (manual, required)" in entered.split("Cases you record:")[1]
    assert "- TC-003 (external, optional)" in entered
    assert "- TC-001 (automated, required)" in server.split("## Reference document")[0]
    assert "TC-001" not in entered.split("Cases you record:")[1]
    assert "run the project's own tests" not in text
    assert "official result comes from the FlowGate server run only" in text
    assert "Authorization: Bearer raw-tok" in text


# ── 3. The TS view: entered Cases and the newest server run ───────────────────


def test_the_ts_view_names_entered_cases_and_the_newest_server_run(env, monkeypatch, tmp_path):
    _server_run_and_manual(env)
    result, _ = _approve_ts(env, monkeypatch, tmp_path)
    view = env.svc.describe_test_document(env.docs[TS_ID])
    assert view["result_entry_case_ids"] == ["TC-002", "TC-003"]
    assert view["last_execution"]["run_id"] == result["spec_execution"]["run_id"]
    assert view["last_execution"]["phase"] == "queued"
    env.svc.request_cancel(result["spec_execution"]["run_id"])
    view = env.svc.describe_test_document(env.docs[TS_ID])
    assert view["active_run"] is None and view["last_execution"]["status"] == "cancelled"
    tsr_view = env.svc.describe_test_document(env.docs[TSR_ID])
    assert tsr_view["last_execution"]["status"] == "cancelled"
    assert tsr_view["ts_review_status"] == "approved"


def test_a_prepare_refusal_is_the_newest_execution_without_a_result(env, monkeypatch, tmp_path):
    from modules.flow_gate.services import spec_execution_service as execution
    _server_run_and_manual(env)
    _approve_ts(env, monkeypatch, tmp_path)

    def refuse(doc, run, cases):
        raise ValueError("basis_capture_failed: source_busy")
    monkeypatch.setattr(execution.ExecutionRootResolver, "prepare", refuse)
    env.svc.execute_run(env.db_test_runs.pick_next_running())
    view = env.svc.describe_test_document(env.docs[TS_ID])
    assert view["last_execution"]["prepare_refused"]["error"] == "basis_capture_failed"
    assert view["latest_result"] is None


# ── 4. The chain waits on the server run ────────────────────────────────────


@pytest.fixture
def tsr_head(env, monkeypatch):
    """The workflow: TS approved, the TSR is the effective head."""
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import token_service

    head = {"id": 5, "type": "TSR", "item_seq": 11, "result_doc_id": TSR_ID,
            "result_doc_review_status": "pending_review"}
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_effective_head", lambda _s: head)
    monkeypatch.setattr(db_wfseq, "get_predecessor_result_doc_id", lambda _s, _h: TS_ID)
    issued: list[dict] = []
    consumed: list[str] = []

    def issue(**kwargs):
        issued.append(kwargs)
        return {"raw_token": "raw", "token_id": f"tok-{len(issued)}", "expires_at": None,
                "scratch_dir": "/tmp/x"}

    def consume(token_id, project_id, doc_id=None, **_kw):
        consumed.append(token_id)
        # The chain identity the gate later reads is this consumed token.
        env.chain_token = {"issued_to": issued[-1]["issued_to"], "continuation_locale": "ko",
                           "continuation_target_seq": issued[-1]["continuation_target_seq"]}
        return True
    monkeypatch.setattr(token_service, "issue", issue)
    monkeypatch.setattr(token_service, "consume", consume)
    return {"head": head, "issued": issued, "consumed": consumed}


def _hand(env):
    return env.svc.hand_chain_to_server_run(
        "flowgate.default.0549.0001-B", issued_to="usr_admin", api_base_url=CHAIN["api_base_url"],
        locale="ko", continuation_target_seq=14, ai_run_id="aiv_hop")


def test_a_chain_binds_itself_to_the_running_server_run_without_a_hop_token(
        env, monkeypatch, tmp_path, tsr_head):
    _server_run_and_manual(env)
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC3)
    result, _ = _approve_ts(env, monkeypatch, tmp_path)
    run_id = result["spec_execution"]["run_id"]
    handed = _hand(env)
    assert handed["run_id"] == run_id and handed["finished"] is False
    assert handed["ts_doc_id"] == TS_ID
    # The chain identity is recorded and spent at once: no worker ever holds it.
    issued = tsr_head["issued"][-1]
    assert issued["action_scope"] == "test_run" and issued["doc_ref"] == TS_ID
    assert issued["continuation_target_seq"] == 14 and issued["ai_run_id"] == "aiv_hop"
    assert tsr_head["consumed"] == [handed["token_id"]]
    assert json.loads(env.db_test_runs.get_run(run_id)["result_meta"])["chain"] == CHAIN
    assert len(env.db_test_runs.list_by_doc(TS_ID)) == 1  # no second run
    # The run PASSes: the chain's TSR is approved and the parked chain resumes.
    _chain(env, target_seq=14, next_incomplete=12)
    env.chain_token = {"issued_to": "usr_admin", "continuation_target_seq": 14,
                       "continuation_locale": "ko"}
    _bind_tsr_slot(env)
    _pass_worker(env, monkeypatch, tmp_path)
    env.svc.execute_run(env.db_test_runs.pick_next_running())
    assert env.db_test_runs.latest_by_doc(TS_ID)["triggered_via"] == "token"
    assert env.docs[TSR_ID]["doc_review_status"] == "approved"
    assert env.resumes and env.resumes[-1]["api_base_url"] == CHAIN["api_base_url"]


def test_a_run_that_already_passed_approves_the_report_for_the_chain(
        env, monkeypatch, tmp_path, tsr_head):
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC3)
    _approve_ts(env, monkeypatch, tmp_path)
    _pass_worker(env, monkeypatch, tmp_path)
    env.svc.execute_run(env.db_test_runs.pick_next_running())
    assert env.docs[TSR_ID]["doc_review_status"] != "approved"  # no chain existed then
    handed = _hand(env)
    assert handed["finished"] is True and handed["tsr_doc_id"] == TSR_ID
    assert env.docs[TSR_ID]["doc_review_status"] == "approved"


def _run_while_the_chain_binds(env, monkeypatch, picked):
    """Run the server run on another thread the moment the chain's context is on it.

    The run reaches its terminal write while the hand-off has attached the chain but not
    yet issued the chain's token -- the window a FAIL used to slip through to a human.
    """
    import threading

    from modules.flow_gate.services import spec_execution_service as execution

    real_attach = execution.attach_chain
    worker: dict = {}

    def attach_then_run(doc, chain_context, **kwargs):
        attached = real_attach(doc, chain_context, **kwargs)
        thread = threading.Thread(target=env.svc.execute_run, args=(picked,), daemon=True)
        worker["thread"] = thread
        thread.start()
        thread.join(timeout=1.0)  # the run must not finish before the chain's identity exists
        worker["ended_before_binding"] = not thread.is_alive()
        return attached
    monkeypatch.setattr(execution, "attach_chain", attach_then_run)
    return worker


def test_a_fail_that_ends_while_the_chain_binds_goes_to_failure_origin_review(
        env, monkeypatch, tmp_path, tsr_head):
    from modules.flow_gate.services import spec_execution_service as execution
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC3)
    _approve_ts(env, monkeypatch, tmp_path)
    _pass_worker(env, monkeypatch, tmp_path)
    monkeypatch.setattr(execution.ExistingRunnerAdapter, "run_pytest",
                        lambda nodeid, root, scratch, active: (
                            "fail", "<testsuite><testcase name='test_tc_1'>"
                                    "<failure message='boom'/></testcase></testsuite>", 1, ""))
    worker = _run_while_the_chain_binds(env, monkeypatch, env.db_test_runs.pick_next_running())
    handed = _hand(env)
    worker["thread"].join(timeout=30)
    assert not worker["thread"].is_alive()
    assert tsr_head["consumed"] == [handed["token_id"]]
    recorded = env.db_test_runs.latest_by_doc(TS_ID)
    assert recorded["overall"] == "FAIL" and recorded["triggered_via"] == "token"
    # D#1 §3-9: the chain's FAIL is classified by failure-origin review, not left to a human.
    assert env.dispatched == [recorded["run_id"]]
    assert env.db_test_runs.get_run(recorded["run_id"])["error"] == "failure_origin_pending"
    assert worker["ended_before_binding"] is False  # it waited for the chain to be bound


def test_a_prepare_refusal_while_the_chain_binds_still_leaves_its_notice(
        env, monkeypatch, tmp_path, tsr_head):
    from modules.flow_gate.services import spec_execution_service as execution
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC3)
    result, _ = _approve_ts(env, monkeypatch, tmp_path)

    def refuse(doc, run, cases):
        raise ValueError("basis_capture_failed: source_busy")
    monkeypatch.setattr(execution.ExecutionRootResolver, "prepare", refuse)
    worker = _run_while_the_chain_binds(env, monkeypatch, env.db_test_runs.pick_next_running())
    _hand(env)
    worker["thread"].join(timeout=30)
    assert worker["ended_before_binding"] is False
    run_id = result["spec_execution"]["run_id"]
    assert env.db_test_runs.get_run(run_id)["error"] == "basis_capture_failed"
    assert env.notified == [run_id]


@pytest.mark.parametrize("case", ["not_tsr_head", "report_approved", "no_server_run"])
def test_the_ordinary_hop_starts_when_there_is_no_server_run_to_wait_on(
        env, monkeypatch, tmp_path, tsr_head, case):
    if case == "not_tsr_head":
        tsr_head["head"]["type"] = "TR"
        env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC3)
        _approve_ts(env, monkeypatch, tmp_path)
    elif case == "report_approved":
        env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC3)
        _approve_ts(env, monkeypatch, tmp_path)
        tsr_head["head"]["result_doc_review_status"] = "approved"
    else:
        # Manual Cases only: the approval queued nothing.
        env.contents[TS_ID] = _spec_doc(CASE_TC2 + CASE_TC3)
        _approve_ts(env, monkeypatch, tmp_path)
    assert _hand(env) is None
    assert tsr_head["issued"] == []


def test_the_engine_parks_the_chain_instead_of_starting_a_hop(monkeypatch):
    from modules.flow_gate.db import test_runs as db_test_runs
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services.ai_invoke import chain, review

    calls: dict[str, list] = {"hand": [], "park": [], "resume": [], "spawn": []}
    monkeypatch.setattr(chain, "_resolve_continuation_target", lambda doc_ref, target, to_end=False: 14)

    def hand(doc_ref, **kwargs):
        calls["hand"].append((doc_ref, kwargs))
        return {"ts_doc_id": TS_ID, "run_id": "trun_1", "finished": False, "tsr_doc_id": TSR_ID}
    monkeypatch.setattr(test_run_service, "hand_chain_to_server_run", hand)
    monkeypatch.setattr(test_run_service, "resume_test_gate_chain_for_hop",
                        lambda doc_id, **kw: calls["resume"].append(doc_id))
    status = {"value": "running"}
    monkeypatch.setattr(db_test_runs, "get_run", lambda _r: {"status": status["value"]})

    class Svc:
        @staticmethod
        def _park_handoff(run, pending, code):
            calls["park"].append(code)

        @staticmethod
        def _spawn_auto_resume(group_id, bundle):
            calls["spawn"].append(group_id)
    monkeypatch.setattr(review, "_svc", lambda: Svc)
    monkeypatch.setattr(chain, "_svc", lambda: Svc)
    monkeypatch.setattr(review, "_materialize_work_plan_instruction_before_gate", lambda b: None)
    monkeypatch.setattr(review, "resolve_review_gate", lambda b: {"stage": "work", "slot": None})
    bundle = {"doc_ref": "flowgate.default.0549.0001-B", "issued_to": "usr_admin",
              "target_seq": 14, "locale": "ko", "api_base_url": CHAIN["api_base_url"],
              "review_mode": False, "instruction_mode": None, "auto_approve_item_seqs": None}
    run = {"run_id": "aiv_hop", "group_id": GROUP}
    assert review.run_review_gate(GROUP, bundle, run) is False  # parked: no next hop
    assert calls["spawn"] == [] and calls["park"] == ["test_run_pending"]
    assert run["test_gate_doc_id"] == TS_ID
    assert calls["hand"][0][1]["continuation_target_seq"] == 14
    assert calls["hand"][0][1]["ai_run_id"] == "aiv_hop"
    assert calls["resume"] == []  # the run is still going; its finalization answers
    # A run that ended while the chain was being bound is answered right away.
    status["value"] = "passed"
    assert chain.park_for_server_test_run(GROUP, bundle, {"run_id": "aiv_hop2"}) is True
    assert calls["resume"] == [TS_ID]
    # No server run to wait on: the ordinary hop starts.
    monkeypatch.setattr(test_run_service, "hand_chain_to_server_run", lambda *a, **k: None)
    assert review.run_review_gate(GROUP, bundle, {"run_id": "aiv_hop3"}) is True
    assert calls["spawn"] == [GROUP]


def test_a_hand_off_failure_falls_back_to_the_ordinary_hop(monkeypatch):
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services.ai_invoke import chain

    monkeypatch.setattr(chain, "_resolve_continuation_target", lambda *a, **k: 14)

    def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(test_run_service, "hand_chain_to_server_run", broken)
    assert chain.park_for_server_test_run(GROUP, {"doc_ref": "x", "issued_to": "u"}, {}) is False


def test_only_entered_cases_left_resumes_the_chain_for_result_entry(env, monkeypatch, tmp_path):
    _server_run_and_manual(env)
    _approve_ts(env, monkeypatch, tmp_path)
    _chain(env, target_seq=14, next_incomplete=11)
    _bind_tsr_slot(env)
    from modules.flow_gate.services import spec_execution_service as execution
    execution.attach_chain(env.docs[TS_ID], CHAIN)
    _pass_worker(env, monkeypatch, tmp_path)
    env.svc.execute_run(env.db_test_runs.pick_next_running())
    recorded = env.db_test_runs.latest_by_doc(TS_ID)
    assert recorded["overall"] == "NOT_RUN"  # TC-002 (manual, required) is not entered yet
    assert env.resumes and env.resumes[-1]["group_id"] == GROUP
    assert env.relabels == [] and env.notified == []
    # The resumed hop enters a NOT_RUN again: that is a human's answer, not a reason to ask.
    resumed = len(env.resumes)
    _submit(env, [{"case_id": "TC-002", "status": "NOT_RUN"}], triggered_via="token")
    assert len(env.resumes) == resumed
    assert env.relabels and env.relabels[-1][1] == "test_gate_blocked"


def test_an_automated_case_left_open_does_not_ask_for_entry(env, monkeypatch, tmp_path):
    from modules.flow_gate.services import spec_execution_service as execution
    _server_run_and_manual(env)
    _approve_ts(env, monkeypatch, tmp_path)
    _chain(env, target_seq=14, next_incomplete=11)
    _bind_tsr_slot(env)
    execution.attach_chain(env.docs[TS_ID], CHAIN)
    _pass_worker(env, monkeypatch, tmp_path)
    monkeypatch.setattr(execution.ExistingRunnerAdapter, "run_pytest",
                        lambda nodeid, root, scratch, active: (
                            "pass", "<testsuite><testcase name='test_tc_1'><skipped/></testcase>"
                                    "</testsuite>", 0, ""))
    env.svc.execute_run(env.db_test_runs.pick_next_running())
    recorded = env.db_test_runs.latest_by_doc(TS_ID)
    assert recorded["overall"] in {"BLOCKED", "NOT_RUN"}
    assert env.resumes == []
    assert env.relabels and env.relabels[-1][1] == "test_gate_blocked"


def test_a_prepare_refusal_stops_the_riding_chain_with_a_notice(env, monkeypatch, tmp_path):
    from modules.flow_gate.services import spec_execution_service as execution
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC3)
    result, _ = _approve_ts(env, monkeypatch, tmp_path)
    _chain(env, target_seq=14, next_incomplete=11)
    execution.attach_chain(env.docs[TS_ID], CHAIN)

    def refuse(doc, run, cases):
        raise ValueError("basis_capture_failed: source_busy")
    monkeypatch.setattr(execution.ExecutionRootResolver, "prepare", refuse)
    env.svc.execute_run(env.db_test_runs.pick_next_running())
    run_id = result["spec_execution"]["run_id"]
    assert env.db_test_runs.get_run(run_id)["error"] == "basis_capture_failed"
    assert env.notified == [run_id]
    assert env.relabels and env.relabels[-1][1] == "test_gate_blocked"
    assert env.resumes == []


def test_a_run_without_a_chain_stops_nothing(env, monkeypatch, tmp_path):
    from modules.flow_gate.services import spec_execution_service as execution
    env.contents[TS_ID] = _spec_doc(CASE_TC1_AUTOMATED + CASE_TC3)
    _approve_ts(env, monkeypatch, tmp_path)

    def refuse(doc, run, cases):
        raise ValueError("basis_capture_failed: source_busy")
    monkeypatch.setattr(execution.ExecutionRootResolver, "prepare", refuse)
    env.svc.execute_run(env.db_test_runs.pick_next_running())
    assert env.notified == [] and env.relabels == []
