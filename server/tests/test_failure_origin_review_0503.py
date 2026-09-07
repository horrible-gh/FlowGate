from modules.flow_gate.services import failure_origin_review_service as review
from modules.flow_gate.services import test_run_service as svc
from modules.flow_gate.services.ai_invoke import oracle


def _doc():
    return {"doc_id": "p.default.0503.0001-TS", "project_id": "p",
            "group_id": "p.default.0503", "seq": 4, "owner_id": "u"}


def _run(**kw):
    return {"run_id": "trun_1", "doc_id": _doc()["doc_id"], "status": "failed",
            "runner_id": "u", "revision_no": 2, **kw}


def test_failure_origin_prompt_has_structured_evidence():
    text = review.build_failure_origin_mention(
        doc=_doc(), run=_run(), api_base_url="/api", raw_token="secret",
        items=[{"result": "fail", "case_no": "TC-2", "case_title": "x",
                "assert_mode": "json_equals:x=1", "expect": "one", "actual": "two",
                "exit_code": 0, "output_tail": "tail"}],
    )
    for value in ("trun_1", "TC-2", "json_equals:x=1", "expected: one",
                  "actual: two", "product_defect", "test_defect", "hold"):
        assert value in text


def test_code_rework_cycle_is_history_derived_and_stops_at_pass(monkeypatch):
    monkeypatch.setattr(svc.db_test_runs, "list_by_doc", lambda _doc: [
        _run(run_id="3", failure_origin="product_defect"),
        _run(run_id="2", failure_origin="test_defect"),
        _run(run_id="1", failure_origin="product_defect"),
        {"status": "passed", "failure_origin": None},
        _run(run_id="old", failure_origin="product_defect"),
    ])
    assert svc.count_code_rework_cycles(_doc()["doc_id"]) == 2


def test_product_third_cycle_pauses_without_reopen(monkeypatch):
    run = _run(failure_origin="product_defect")
    monkeypatch.setattr(svc, "count_code_rework_cycles", lambda _doc: 3)
    holds = []
    monkeypatch.setattr(svc.db_test_runs, "set_failure_origin_hold",
                        lambda run_id, reason: holds.append((run_id, reason)))
    monkeypatch.setattr(svc, "_auto_reopen_failed_scenario",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("reopen")))
    result = svc.resume_failure_origin_branch(_doc(), run)
    assert result["hold_reason"] == "test_code_rework_exhausted"
    assert holds == [("trun_1", "test_code_rework_exhausted")]


def test_three_classification_branches(monkeypatch):
    monkeypatch.setattr(svc, "count_code_rework_cycles", lambda _doc: 1)
    monkeypatch.setattr(svc.db_test_runs, "set_failure_origin_hold", lambda *_a: None)
    monkeypatch.setattr(svc.db_test_runs, "list_cases", lambda _run_id: [])
    reopened = []
    monkeypatch.setattr(svc, "_auto_reopen_failed_scenario",
                        lambda _d, r, reason, rework_instruction=None:
                        reopened.append((r["run_id"], reason, rework_instruction))
                        or {"auto_reopened": True, "run_id": r["run_id"]})
    assert svc.resume_failure_origin_branch(_doc(), _run(failure_origin="product_defect"))["continued"]
    assert svc.resume_failure_origin_branch(_doc(), _run(failure_origin="test_defect"))["continued"]
    assert not svc.resume_failure_origin_branch(_doc(), _run(failure_origin="hold"))["continued"]
    assert [(run_id, reason) for run_id, reason, _instr in reopened] == [
        ("trun_1", "test_run_product_defect_rework"),
        ("trun_1", "test_run_test_defect_rework"),
    ]
    # NR0003 §10-12: each branch's rework instruction carries its own constraint text.
    assert "product source" in reopened[0][2] and "not weaken" in reopened[0][2]
    assert "prohibited" in reopened[1][2] and "TS definition" in reopened[1][2]


def test_rework_instruction_carries_classification_constraint_and_evidence():
    items = [{"result": "fail", "case_no": "TC-2", "case_title": "x",
              "assert_mode": "json_equals:x=1", "expect": "one", "actual": "two",
              "exit_code": 0, "output_tail": "tail"}]
    product = review.build_rework_instruction(
        classification="product_defect", doc=_doc(), run=_run(), items=items,
    )
    assert "PRODUCT_DEFECT" in product and "not weaken" in product
    assert "TC-2" in product and "expected: one" in product and "actual: two" in product

    test_defect = review.build_rework_instruction(
        classification="test_defect", doc=_doc(), run=_run(), items=items,
    )
    assert "TEST_DEFECT" in test_defect and "prohibited" in test_defect
    assert "TC-2" in test_defect


def test_terminal_code_recovery_modes(monkeypatch):
    monkeypatch.setattr(svc.engine_recipe_service, "classify_failure",
                        lambda *_a: svc.engine_recipe_service.CODE)
    monkeypatch.setattr(svc, "_maybe_notify_chain_failure", lambda *_a, **_k: None)
    reopened = []
    monkeypatch.setattr(svc, "_auto_reopen_failed_scenario",
                        lambda *_a, **_k: reopened.append(True) or {"auto_reopened": True})
    monkeypatch.setattr(svc.db_test_runs, "set_failure_origin_pending", lambda *_a: None)
    monkeypatch.setattr(svc.db_test_runs, "set_failure_origin_hold", lambda *_a: None)

    monkeypatch.setattr(svc.engine_recipe_service, "handle_run_failure", lambda *_a: "skip")
    svc._handle_terminal_case_failure(_doc(), _run(), [])
    assert len(reopened) == 1

    monkeypatch.setattr(svc.engine_recipe_service, "handle_run_failure", lambda *_a: "error")
    result = svc._handle_terminal_case_failure(_doc(), _run(), [])
    assert result["hold_reason"] == "failure_origin_recovery_error"
    assert len(reopened) == 1


def test_continuous_code_marks_pending_before_invocation(monkeypatch):
    monkeypatch.setattr(svc.engine_recipe_service, "classify_failure",
                        lambda *_a: svc.engine_recipe_service.CODE)
    monkeypatch.setattr(svc.engine_recipe_service, "handle_run_failure",
                        lambda *_a: svc.engine_recipe_service.CODE)
    order = []
    monkeypatch.setattr(svc.db_test_runs, "set_failure_origin_pending",
                        lambda *_a: order.append("pending"))
    monkeypatch.setattr(review, "dispatch_failure_origin_review",
                        lambda **_k: order.append("classify"))
    monkeypatch.setattr(svc, "_auto_reopen_failed_scenario",
                        lambda *_a, **_k: order.append("reopen"))
    result = svc._handle_terminal_case_failure(_doc(), _run(), [])
    assert result["classification_pending"]
    assert order == ["pending", "classify"]


def test_oracle_rejects_stale_classification_marker(monkeypatch):
    rows = [_run(failure_origin="product_defect", failure_origin_reviewed_at="old")]
    monkeypatch.setattr(oracle.db_tokens, "get_by_id", lambda _id: {
        "doc_ref": _doc()["doc_id"], "failure_origin_target_run_id": "trun_1",
        "failure_origin_before_marker": "old",
    })
    monkeypatch.setattr(oracle.db_test_runs, "get_run", lambda _id: rows[0])
    check = oracle._scope_oracle("failure_origin_review", "tok", _doc()["doc_id"])
    assert check is not None and check() is False
    rows[0] = _run(failure_origin="test_defect", failure_origin_reviewed_at="new")
    assert check() is True


def test_restart_recovers_all_three_durable_states(monkeypatch):
    pending = _run(error="failure_origin_pending")
    classified = _run(run_id="classified", failure_origin="test_defect")
    reopened = _run(run_id="reopened", failure_origin="product_defect")
    monkeypatch.setattr(svc.db_test_runs, "list_failure_origin_recovery_candidates",
                        lambda: [pending, classified, reopened])
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _id: _doc())
    issued = []
    monkeypatch.setattr(review, "dispatch_failure_origin_review",
                        lambda **kw: issued.append(kw["run"]["run_id"]))
    resumed = []
    monkeypatch.setattr(svc, "resume_failure_origin_branch",
                        lambda _doc, run: resumed.append(run["run_id"]) or {})
    svc._resume_failure_origin_after_restart()
    assert issued == ["trun_1"]
    assert resumed == ["classified", "reopened"]
