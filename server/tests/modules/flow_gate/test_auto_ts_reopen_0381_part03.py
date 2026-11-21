"""Independently selectable scenarios; shared fixtures remain in test_auto_ts_reopen_0381.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_auto_ts_reopen_0381 import (
    MagicMock,
    Optional,
    Path,
    _LIVE_0042_DOCS,
    _LIVE_0042_SEQUENCE,
    _LIVE_SETUP_CMD,
    _LIVE_SETUP_TAIL,
    _MockDB,
    _MockTxn,
    _PROD_DOCS,
    _PROD_MODULE_ERROR_TAIL,
    _PROD_NPM_SETUP_TAIL,
    _PROD_SEQUENCE,
    _SCHEMA_DIR,
    _SERVER_DIR,
    _UI_CONTRACT_FIXTURE,
    _UI_CONTRACT_KEYS,
    _auto,
    _create_run,
    _drive_code_red_broadcasts,
    _run_production_red,
    _run_red_on_seeded_group,
    _run_setup_abort,
    _seed_group,
    _seed_live_0042,
    _seed_production_group,
    _sequence_head,
    _slot_status,
    _ui_contract_payload,
    auto_store,
    contextmanager,
    json,
    os,
    pytest,
    sqlite3,
    sys,
)

def test_13_real_red_whose_case_log_names_a_missing_module_still_rewinds(
    auto_store, monkeypatch, tmp_path
):
    """The defect the live data exposed.

    classify_failure scanned EVERY step's log for "No module named" and called the run
    INFRA. But a test case that ran and printed ModuleNotFoundError is the product's own
    missing import — a real RED. Replaying the 30 most recent failed runs in production
    through the old rule mislabelled 3 of the 27 genuine REDs, and for those the rewind
    silently never fired: the TS stayed approved exactly as reported.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import engine_recipe_service as svc

    run = {"error": None, "case_passed": 0, "case_failed": 3}
    items = [{"kind": "case", "exit_code": 1, "output_tail": _PROD_MODULE_ERROR_TAIL}] * 3
    assert svc.classify_failure(run, items) == svc.CODE

    ids = _seed_production_group(auto_store, "moderr")
    finished = _run_production_red(
        monkeypatch, tmp_path, ids,
        [{"case_no": "TC-%d" % n, "result": "fail", "exit_code": 1,
          "output_tail": _PROD_MODULE_ERROR_TAIL} for n in (1, 2, 3)],
        setup=[{"case_no": "SETUP-1", "result": "pass", "exit_code": 0,
                "output_tail": _PROD_NPM_SETUP_TAIL}],
    )

    assert finished["status"] == "failed" and finished["error"] is None
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"
def test_14_missing_module_from_a_setup_step_is_still_infra_and_never_rewinds(
    auto_store, monkeypatch, tmp_path
):
    """The other half of the same rule — do not over-correct.

    When the environment build is what could not find the module, the test never ran:
    that stays INFRA and the TS must keep its approval so the 0157 repair loop owns it.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import engine_recipe_service as svc

    setup_items = [
        {"kind": "setup", "exit_code": 1, "output_tail": _PROD_MODULE_ERROR_TAIL},
        {"kind": "case", "exit_code": 1, "output_tail": "AssertionError"},
    ]
    assert svc.classify_failure({"error": None, "case_passed": 1, "case_failed": 1}, setup_items) == svc.INFRA

    ids = _seed_production_group(auto_store, "setuperr")
    finished = _run_production_red(
        monkeypatch, tmp_path, ids,
        [{"case_no": "TC-1", "result": "pass", "exit_code": 0, "output_tail": "ok"},
         {"case_no": "TC-2", "result": "fail", "exit_code": 1,
          "output_tail": "AssertionError: expected 2 got 3"}],
        setup=[{"case_no": "SETUP-1", "result": "fail", "exit_code": 1,
                "output_tail": _PROD_MODULE_ERROR_TAIL}],
    )
    assert finished["status"] == "failed"
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "approved"
def test_15_sequence_head_leaves_the_tsr_slot_and_returns_to_the_ts(
    auto_store, monkeypatch, tmp_path
):
    """The workflow sequence returns from [테스트레포트] to [테스트시나리지시].

    Asserted on both surfaces the UI reads: get_effective_head (the sequence strip's
    single source of truth) and workflow_head_index/-_type (what DocHeader renders as
    "현재 단계"). Driven by the real worker, with nothing in the rewind path mocked.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers import documents as doc_routes

    ids = _seed_group(auto_store, "seqhead")

    # Before: the TS is approved and RED produced no test report, so the sequence is
    # parked on the empty TSR slot, matching the reported stalled-report state.
    before = _sequence_head(ids)
    assert before is not None and before["type"] == "TSR"
    assert before["result_doc_id"] is None
    assert _slot_status(ids, "TS") == "done"
    before_view = doc_routes._parse_doc_workflow(db_docs.get_by_id(ids["TS"]))
    assert before_view["workflow_steps"] == ["T", "TS", "TSR", "TR"]
    assert before_view["workflow_head_index"] == 2          # TSR
    assert before_view["workflow_head_type"] == "TSR"

    finished = _run_red_on_seeded_group(monkeypatch, tmp_path, ids)
    assert finished["status"] == "failed" and finished["error"] is None

    # After: the head is the TS slot itself, and that slot is no longer 'done'.
    after = _sequence_head(ids)
    assert after is not None and after["type"] == "TS"
    assert after["result_doc_id"] == ids["TS"]
    assert _slot_status(ids, "TS") == "in_progress"
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"

    after_view = doc_routes._parse_doc_workflow(db_docs.get_by_id(ids["TS"]))
    assert after_view["workflow_head_index"] == 1           # TS, one step back
    assert after_view["workflow_head_type"] == "TS"
    assert after_view["workflow_head_doc_id"] == ids["TS"]
    # the TS is the head, i.e. the document the user is now expected to fix
    assert after_view["workflow_self_index"] == after_view["workflow_head_index"]
def test_16_production_shape_sequence_head_returns_to_the_ts(auto_store, monkeypatch, tmp_path):
    """Same assertion on the live group shape: B root at wf_done with an approved TSR.

    There every slot is 'done' and the head is None (the workflow reads as 최종승인 대기),
    so "가만히 있는" looks different but must resolve to the same place — the TS slot.
    """
    from modules.flow_gate.db import documents as db_docs

    ids = _seed_production_group(auto_store, "seqhead")
    assert _sequence_head(ids) is None                       # nothing left to do
    assert _slot_status(ids, "TS") == "done"
    assert _slot_status(ids, "TSR") == "done"

    finished = _run_production_red(monkeypatch, tmp_path, ids, [
        {"case_no": "TC-1", "result": "pass", "exit_code": 0, "output_tail": "14 passed"},
        {"case_no": "TC-2", "result": "fail", "exit_code": 1,
         "output_tail": "NameError: name 'sort' is not defined"},
    ])
    assert finished["status"] == "failed" and finished["error"] is None

    head = _sequence_head(ids)
    assert head is not None and head["type"] == "TS"
    assert head["result_doc_id"] == ids["TS"]
    assert _slot_status(ids, "TS") == "in_progress"
    assert db_docs.get_by_id(ids["B"])["doc_review_status"] == "wf_in_progress"
def test_17_ui_contract_payload_matches_the_checked_in_fixture(
    auto_store, monkeypatch, tmp_path
):
    """Pin the browser-facing payload before and after the automatic rewind.

    'uilink' is not an arbitrary suffix — the fixture's doc ids are built from it, so a
    renamed seed makes this fail loudly instead of silently comparing nothing.
    """
    fixture = json.loads(_UI_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    ids = _seed_production_group(auto_store, "uilink")
    assert ids["TS"] == fixture["tab_doc_id"]

    assert _ui_contract_payload(ids["TS"]) == fixture["before"]

    finished = _run_production_red(monkeypatch, tmp_path, ids, [
        {"case_no": "TC-1", "result": "pass", "exit_code": 0, "output_tail": "14 passed"},
        {"case_no": "TC-2", "result": "fail", "exit_code": 1,
         "output_tail": "NameError: name 'sort' is not defined"},
    ])
    assert finished["status"] == "failed" and finished["error"] is None

    assert _ui_contract_payload(ids["TS"]) == fixture["after"]
def test_18_manual_setup_abort_sends_the_scenario_back_to_pre_approval(
    auto_store, monkeypatch, tmp_path
):
    """The reported case, replayed end to end: setup failures also return to [테스트시나리지시]."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.db import workflow_return_points as db_rp

    ids = _seed_production_group(auto_store, "setupabort")
    assert _sequence_head(ids) is None                      # Waiting for final approval; the sequence is stalled.
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "approved"

    finished = _run_setup_abort(monkeypatch, tmp_path, ids)

    # the live row shape, reproduced
    assert finished["status"] == "failed"
    assert finished["error"] == "setup_failed"
    assert (finished["case_passed"] or 0) == 0 and (finished["case_failed"] or 0) == 0

    # …and now the workflow actually moves
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"
    head = _sequence_head(ids)
    assert head is not None and head["type"] == "TS"
    assert head["result_doc_id"] == ids["TS"]
    assert _slot_status(ids, "TS") == "in_progress"
    assert db_docs.get_by_id(ids["B"])["doc_review_status"] == "wf_in_progress"
    assert db_rp.get_by_group(ids["group_id"]) is not None

    # the failed setup evidence is kept, not thrown away
    cases = db_runs.list_cases(finished["run_id"])
    assert any(c.get("kind") == "setup" and c.get("result") == "fail" for c in cases)

    # and the browser gets the same payload the UI contract test pins
    payload = _ui_contract_payload(ids["TS"])
    assert payload["workflow_head_type"] == "TS"
    assert payload["workflow_head_status"] == "in_progress"
    assert payload["doc_review_status"] == "pending_review"
def test_19_unmanned_setup_abort_still_belongs_to_the_repair_loop(monkeypatch):
    """Do not steal the 0157 INFRA loop's failure (acceptance criteria 3)."""
    from modules.flow_gate.services import engine_recipe_service
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    auto = MagicMock()
    notify = MagicMock()
    monkeypatch.setattr(rework, "auto_reopen_failed_ts", auto)
    monkeypatch.setattr(test_run_service, "_maybe_notify_chain_failure", notify)

    for recovery in ("repair", "escalated"):
        monkeypatch.setattr(engine_recipe_service, "handle_run_failure", lambda *_a, r=recovery: r)
        assert test_run_service._handle_setup_stage_failure(
            {"doc_id": "g.0006-TS"}, {"run_id": "setup-%s" % recovery}, []
        ) is None
    auto.assert_not_called()
    notify.assert_not_called()
def test_20_the_reported_group_and_run_end_up_where_the_reporter_asked(
    auto_store, monkeypatch, tmp_path
):
    """The whole complaint, measured on both surfaces of the reporter's own group.

    Before: the strip parks on the empty [테스트레포트] slot, the [테스트시나리오지시] cell is
    'done' (green, app.css:403) and the action bar for that tab is the forward [다음 단계] —
    there is nothing to approve or reject. After: the head IS the TS slot, its cell is
    'current' (blue, app.css:405) and the bar is the 승인/반려 review bar.
    """
    from modules.flow_gate.db import documents as db_docs

    fixture = json.loads(_UI_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    ids = _seed_live_0042(auto_store, "repro")
    assert ids["TS"] == fixture["reported_tab_doc_id"]

    before = _ui_contract_payload(ids["TS"])
    assert before == fixture["reported_before"]
    assert before["doc_review_status"] == "approved"
    assert before["workflow_head_type"] == "TSR"        # Parked on the empty test-report slot.
    assert before["workflow_head_status"] == "pending"
    assert before["workflow_head_index"] == 5
    assert before["workflow_self_index"] == 4          # the TS sits BEHIND the head → 'done'

    finished = _run_setup_abort(monkeypatch, tmp_path, ids)
    assert finished["status"] == "failed" and finished["error"] == "setup_failed"

    after = _ui_contract_payload(ids["TS"])
    assert after == fixture["reported_after"]
    assert after["doc_review_status"] == "pending_review"
    assert after["workflow_head_type"] == "TS"
    assert after["workflow_head_status"] == "in_progress"
    assert after["workflow_head_index"] == 4           # the head moved back one slot
    assert after["workflow_head_doc_id"] == ids["TS"]
    assert after["workflow_head_index"] == after["workflow_self_index"]

    # the R root was already mid-flight and must not be pushed anywhere
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_in_progress"
    # the earlier steps keep their approval — only the TS and what follows it come back
    for type_code in ("N", "NR", "T", "TR"):
        assert db_docs.get_by_id(ids[type_code])["doc_review_status"] == "approved"
