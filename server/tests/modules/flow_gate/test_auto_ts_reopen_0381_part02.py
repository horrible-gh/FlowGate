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

def test_7_prior_tsr_is_preserved_and_revised_in_place(auto_store, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "tsr", with_tsr=True)
    run = _create_run(ids["TS"], ids["user_id"])
    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    assert _auto(ids, run)["auto_reopened"] is True
    prior = db_docs.get_by_id(ids["TSR"])
    assert prior is not None
    assert prior["doc_review_status"] == "pending_review"

    monkeypatch.setattr(test_run_service, "_register_tsr_workflow_result", lambda *_: None)
    revised_id = test_run_service._revise_active_tsr(
        db_docs.get_by_id(ids["TS"]), prior, "# fresh green report\n", "Fresh report"
    )
    assert revised_id == ids["TSR"]
    assert db_docs.get_by_id(ids["TSR"])["revision_no"] == 1
def test_8_done_root_preserves_ac_and_rearms_git(auto_store, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "done", root_done=True, with_ac=True, with_tsr=True)
    run = _create_run(ids["TS"], ids["user_id"])
    reopen_git = MagicMock()
    monkeypatch.setattr(rework.git_service, "reopen_group_git", reopen_git)
    assert _auto(ids, run)["auto_reopened"] is True
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_in_progress"
    ac = db_docs.get_by_id(ids["AC"])
    assert ac is not None
    assert ac["status"] == "archived"
    assert json.loads(ac["meta"])["workflow_invalidated_run_id"] == run["run_id"]
    assert rework.git_service._group_ac_doc_id(ids["group_id"]) is None

    # Once the real sequence documents are approved again, the archived historical AC
    # must not make the workflow look final-approved; a fresh AC gate is pending.
    from modules.flow_gate.documents.routers import documents as doc_routes
    for type_code in ("TS", "TSR", "TR"):
        db_docs.update(ids[type_code], {"doc_review_status": "approved"})
    parsed = doc_routes._parse_doc_workflow(db_docs.get_by_id(ids["R"]))
    assert parsed["workflow_head_type"] == "AC"
    assert parsed["workflow_head_status"] == "pending"
    reopen_git.assert_called_once_with(ids["project_id"], ids["group_id"])
def test_9_sequence_mutation_archives_approved_ac_and_reopens_atomically(auto_store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers import documents as doc_routes
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services import workflow_decision_service as decision

    ids = _seed_group(auto_store, "seqmut", root_done=True, with_ac=True)
    result = decision.edit_workflow_pending(
        ids["R"], [{"type": "M", "label": "Follow-up"}]
    )

    assert result["pending_count"] == 1
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_in_progress"
    old_ac = db_docs.get_by_id(ids["AC"])
    assert old_ac["status"] == "archived"
    assert json.loads(old_ac["meta"])["workflow_invalidated_reason"] == "workflow_sequence_changed"
    assert git_service._group_ac_doc_id(ids["group_id"]) is None
    parsed = doc_routes._parse_doc_workflow(db_docs.get_by_id(ids["R"]))
    # the pending TSR right after the started TS is protected — it
    # stays in place ahead of the new M row instead of being deleted, so it is the head.
    assert parsed["workflow_head_type"] == "TSR"
    assert parsed["workflow_head_status"] == "pending"
def test_10_empty_pending_tail_mutation_archives_approved_ac(auto_store):
    """Removing the final pending row is a definition change even when no rows replace it."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services import workflow_decision_service as decision

    # Without TSR, the seeded sequence has locked realised rows plus one pending TSR tail.
    ids = _seed_group(auto_store, "seqempty", root_done=True, with_ac=True)
    sequence = db_wfseq.get_sequence_by_doc_id(ids["R"])
    # that pending TSR follows a started TS, so it is protected and no
    # edit removes it. The removable pending tail is an ordinary row after it.
    db_wfseq.insert_sequence_item(sequence["id"], 9, "M", "Tail", "doc", 9)
    pending = [
        item for item in db_wfseq.get_sequence_items(sequence["id"])
        if item["result_doc_id"] is None
    ]
    assert [item["type"] for item in pending] == ["TSR", "M"]

    result = decision.edit_workflow_pending(ids["R"], [])

    assert result["pending_count"] == 0
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_in_progress"
    assert [
        item["type"] for item in db_wfseq.get_sequence_items(sequence["id"])
        if item["result_doc_id"] is None
    ] == ["TSR"]
    old_ac = db_docs.get_by_id(ids["AC"])
    assert old_ac["status"] == "archived"
    assert json.loads(old_ac["meta"])["workflow_invalidated_reason"] == "workflow_sequence_changed"
    assert git_service._group_ac_doc_id(ids["group_id"]) is None
def test_11_identical_final_sequence_save_preserves_current_approval(auto_store):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services import workflow_decision_service as decision

    ids = _seed_group(
        auto_store, "seqnoop", root_done=True, with_ac=True, with_tsr=True
    )
    result = decision.edit_workflow_pending(ids["R"], [])

    assert result["workflow_changed"] is False
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_done"
    assert db_docs.get_by_id(ids["AC"])["status"] != "archived"
    assert git_service._group_ac_doc_id(ids["group_id"]) == ids["AC"]
def test_11_phantom_document_is_not_reopened(auto_store, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "phantom", with_phantom=True)
    run = _create_run(ids["TS"], ids["user_id"])
    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    outcome = _auto(ids, run)
    assert outcome["auto_reopened"] is True
    assert db_docs.get_by_id(ids["TR"])["doc_review_status"] == "pending_review"
    assert db_docs.get_by_id(ids["P"])["doc_review_status"] == "approved"
def test_10_full_worker_pipeline_real_code_red_reopens_ts_end_to_end(auto_store, monkeypatch, tmp_path):
    """Drives the exact production entry point a live worker uses (`_execute_run_inner`)
    through a genuine CODE RED with NONE of classify_failure / handle_run_failure /
    auto_reopen_failed_ts / reopen_to_target mocked out. Only the OS-level process
    execution is stubbed (as test_5 does for the passed-run case) so this proves the
    real wiring — not a spy on it — is what flips the TS back to "승인이전" (pending_review).
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "e2e")
    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())

    run = db_runs.insert_run(
        doc_id=ids["TS"],
        revision_no=0,
        triggered_via="ui",
        runner_id=ids["user_id"],
        cases=[{"case_no": "TC-1", "title": "assert result", "cmd": "run-case", "expect": "pass"}],
    )

    monkeypatch.setattr(test_run_service.storage_paths, "resolve_project_src_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(test_run_service, "_record_source_root", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_allocate_port", lambda: 19002)
    monkeypatch.setattr(test_run_service, "_scratch_dir", lambda *_: tmp_path / "scratch")
    monkeypatch.setattr(test_run_service, "_execute_setup", lambda *_: (False, None))

    def _fake_case(_doc, _run, case, *_a):
        db_runs.mark_case_finished(
            case_id=case["id"],
            result="fail",
            exit_code=1,
            duration_ms=5,
            output_tail="AssertionError: expected 2 got 3",
        )

    monkeypatch.setattr(test_run_service, "_execute_case", _fake_case)
    monkeypatch.setattr(test_run_service, "_execute_teardown", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_finalize_services", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_remove_scratch", lambda *_: None)
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        test_run_service, "_broadcast", lambda kind, _doc, payload: emitted.append((kind, payload))
    )

    test_run_service._execute_run_inner(run)

    finished = db_runs.get_run(run["run_id"])
    assert finished["status"] == "failed"
    assert finished["error"] is None  # a real case RED, not setup/report failure

    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"
    assert db_docs.get_by_id(ids["TR"])["doc_review_status"] == "pending_review"
    assert db_rp.get_by_group(ids["group_id"]) is not None

    event_rows = get_store()._fetch_all(
        "SELECT metadata FROM workflow_events WHERE event_type = 'workflow_reopen'"
    )
    assert len(event_rows) == 1
    metadata = json.loads(event_rows[0]["metadata"])
    assert metadata.get("run_id") == run["run_id"]
    assert metadata.get("reason") == "test_run_code_failure"

    # The screen half: the real pipeline must end on a refresh emitted AFTER the rewind
    # committed, otherwise every open browser keeps rendering the pre-rewind TS.
    assert emitted[-1][0] == "group_view_refresh"
    assert emitted[-1][1]["reason"] == "test_run_code_failure_auto_reopen"
    assert emitted[-1][1]["doc_id"] == ids["TS"]
def test_12_production_group_shape_red_sends_the_ts_back(auto_store, monkeypatch, tmp_path):
    """A live group's real shape — B root at wf_done, an approved TSR behind the TS, and a
    file-less AC — must still put the test-scenario instruction back to its pre-approval state.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_return_points as db_rp

    ids = _seed_production_group(auto_store, "shape")
    finished = _run_production_red(monkeypatch, tmp_path, ids, [
        {"case_no": "TC-1", "result": "pass", "exit_code": 0, "output_tail": "14 passed"},
        {"case_no": "TC-2", "result": "fail", "exit_code": 1,
         "output_tail": "NameError: name 'sort' is not defined"},
    ])

    assert finished["status"] == "failed" and finished["error"] is None
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"
    assert db_docs.get_by_id(ids["TSR"])["doc_review_status"] == "pending_review"
    # the B root, not just an R root, must come back off wf_done
    assert db_docs.get_by_id(ids["B"])["doc_review_status"] == "wf_in_progress"
    # a file-less AC is archived, never deleted (FlowGate is a time machine)
    ac = db_docs.get_by_id(ids["AC"])
    assert ac is not None and ac["status"] == "archived"
    assert db_rp.get_by_group(ids["group_id"]) is not None
