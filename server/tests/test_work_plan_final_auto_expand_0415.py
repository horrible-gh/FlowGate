"""T0005 final-WP expansion regressions.

These tests keep the review-final hook and the shared sequence-edit boundary tied together.
They intentionally use small fakes: the production services remain the only persistence path.
"""
from __future__ import annotations

import pytest

from modules.flow_gate.services import work_plan_sequence_service as wpseq
from modules.flow_gate.services import workflow_decision_service as wds
from modules.flow_gate.workflow import pipeline_service as pipeline

WP_ID = "flowgate.default.0415.0004-WP"
OWNER_ID = "flowgate.default.0415.0001-R"
DOC = {
    "doc_id": WP_ID,
    "target_id": OWNER_ID,
    "revision_no": 2,
    "doc_review_status": "approved",
}
CANDIDATE = {
    "workflow_tag": "before-tag",
    "plan_step_count": 1,
    "rows": [{
        "status": "pending", "type": "T", "label": "implement", "note": "",
        "source_doc_id": WP_ID, "source_revision_no": 2,
        "provider_id": None, "provider_display_name": None,
    }],
}


def _wire_expansion(monkeypatch, *, existing=None, candidate=CANDIDATE):
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_by_doc_id", lambda _id: {"id": 11})
    monkeypatch.setattr(wpseq.db_wfseq, "get_sequence_items", lambda _id: list(existing or []))
    monkeypatch.setattr(wpseq, "build_candidates", lambda **_kwargs: candidate)


def test_tc_1_final_wp_expands_through_shared_edit_ssot(monkeypatch):
    _wire_expansion(monkeypatch)
    seen = {}
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda *args, **kwargs: seen.update(args=args, kwargs=kwargs) or {"status": "updated"})
    result = wpseq.expand_final_work_plan(doc=DOC, plan={"steps": []})
    assert result["status"] == "expanded"
    assert seen["args"] == (OWNER_ID, [{key: value for key, value in CANDIDATE["rows"][0].items() if key != "status"}])
    assert seen["kwargs"]["expected_workflow_tag"] == "before-tag"
    assert seen["kwargs"]["expected_plan"] == {"wp_doc_id": WP_ID, "wp_revision_no": 2}
    assert seen["kwargs"]["applied_by"] == "wp_final_auto_expand"


def test_tc_2_rework_revision_is_the_only_revision_sent(monkeypatch):
    _wire_expansion(monkeypatch)
    seen = {}
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda *a, **k: seen.update(k) or {})
    wpseq.expand_final_work_plan(doc={**DOC, "revision_no": 3}, plan={"steps": []})
    assert seen["expected_plan"]["wp_revision_no"] == 3


@pytest.mark.parametrize("status", ["pending_review", "rejected", "revised"])
def test_tc_3_tc_4_nonfinal_states_do_not_mutate(status, monkeypatch):
    monkeypatch.setattr(wpseq, "build_candidates", lambda **_kwargs: pytest.fail("candidate mutation"))
    assert wpseq.expand_final_work_plan(doc={**DOC, "doc_review_status": status}, plan={}) == {
        "status": "skipped", "reason": "not_final"
    }


def test_tc_5_tc_8_uses_edit_ssot_and_preserves_provenance(monkeypatch):
    _wire_expansion(monkeypatch)
    seen = {}
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda _owner, rows, **kwargs: seen.update(rows=rows, kwargs=kwargs) or {})
    wpseq.expand_final_work_plan(doc=DOC, plan={})
    assert seen["rows"][0]["source_doc_id"] == WP_ID
    assert seen["rows"][0]["source_revision_no"] == 2
    assert seen["kwargs"]["applied_by"] == "wp_final_auto_expand"


@pytest.mark.parametrize("error", [wds.SequenceChanged(OWNER_ID, "old", "new"), wds.PlanRevisionChanged(WP_ID, 2, 3)])
def test_tc_6_tc_7_conflicts_propagate_without_retry_or_overwrite(monkeypatch, error):
    _wire_expansion(monkeypatch)
    calls = []
    def conflict(*_args, **_kwargs):
        calls.append(1)
        raise error
    monkeypatch.setattr(wds, "edit_workflow_pending", conflict)
    with pytest.raises(type(error)):
        wpseq.expand_final_work_plan(doc=DOC, plan={})
    assert len(calls) == 1


def test_tc_9_tc_10_reentry_skips_an_already_applied_revision(monkeypatch):
    _wire_expansion(monkeypatch, existing=[{"source_doc_id": WP_ID, "source_revision_no": 2}])
    monkeypatch.setattr(wpseq, "build_candidates", lambda **_kwargs: pytest.fail("duplicate candidate"))
    assert wpseq.expand_final_work_plan(doc=DOC, plan={}) == {
        "status": "skipped", "reason": "already_applied", "revision_no": 2
    }


def test_tc_11_placeable_policy_does_not_create_wp_child_rows():
    rows, _dropped, _uid = wpseq.plan_to_rows(
        {"steps": [{"key": "WP#1", "type": "WP", "note": "nested"}]}, WP_ID, 2
    )
    assert rows == []


def test_tc_12_tc_13_existing_sequence_edit_api_remains_available():
    assert callable(wds.edit_workflow_pending)
    assert "expected_workflow_tag" in wds.edit_workflow_pending.__code__.co_varnames
    assert "expected_plan" in wds.edit_workflow_pending.__code__.co_varnames


def test_tc_14_expansion_does_not_touch_continuation_state(monkeypatch):
    _wire_expansion(monkeypatch)
    monkeypatch.setattr(wds, "edit_workflow_pending", lambda *_args, **_kwargs: {"status": "updated"})
    result = wpseq.expand_final_work_plan(doc=DOC, plan={})
    assert result["status"] == "expanded"
    assert "continuation" not in result


def test_approval_hook_returns_success_when_final_expansion_fails(monkeypatch):
    """A durable approval stays successful when final expansion raises a CAS conflict."""
    stored = {**DOC, "type_code": "WP", "doc_review_status": "pending_review", "project_id": "p"}
    plan_body = {"steps": []}
    calls = []

    def update(_doc_id, fields):
        stored.update(fields)
        return dict(stored)

    def raise_sequence_changed(**kwargs):
        calls.append(kwargs)
        raise wds.SequenceChanged(OWNER_ID, "a", "b")

    monkeypatch.setattr(pipeline.db_docs, "get_by_id", lambda _doc_id: dict(stored))
    monkeypatch.setattr(pipeline.db_docs, "update", update)
    monkeypatch.setattr(pipeline, "_require_document_body_for_approval", lambda *_args: None)
    monkeypatch.setattr(pipeline, "log_state_changed", lambda **_kwargs: None)
    # Make the canonical-body reload succeed so the test reaches expand_final_work_plan.
    monkeypatch.setattr(
        "modules.flow_gate.services.work_plan_service.plan_path_for_doc", lambda _doc: "ignored.json"
    )
    monkeypatch.setattr(
        "modules.flow_gate.services.work_plan_service.load_body", lambda _path, **_kwargs: plan_body
    )
    monkeypatch.setattr(wpseq, "expand_final_work_plan", raise_sequence_changed)

    result = pipeline.transition_document_review(
        doc_id=WP_ID, action="approve", actor_user_id="u", user_permissions={"document.approve"}
    )

    assert calls == [{"doc": stored, "plan": plan_body, "locale": "ko"}]
    assert result["doc_review_status"] == "approved"
    assert stored["doc_review_status"] == "approved"