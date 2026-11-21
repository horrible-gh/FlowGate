"""Independently selectable scenarios; shared fixtures remain in test_review_doc_path_0393.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_review_doc_path_0393 import (
    DOC_ID,
    GROUP_ID,
    MagicMock,
    PROJECT,
    Path,
    USER,
    VERDICT_PAYLOAD,
    _SERVER_DIR,
    _body,
    _raw_post_inbox,
    _write_payload,
    ai_runtime,
    build_live_sqlite_db,
    datetime,
    db_reviews,
    help_catalog,
    inbox_routes,
    install_live_sqlite_store,
    json,
    mention_service,
    os,
    post_inbox,
    pytest,
    review_env,
    sys,
    timezone,
)

def test_review_submission_snapshots_requested_and_actual_provider_mismatch(review_env, monkeypatch):
    """The inbox binds server-owned run evidence; the model cannot claim its provider."""
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: {
        "run_id": run_id,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "requested_provider_id": "aip_sonnet",
        "provider_id": "aip_opus",
        "provider": {"id": "aip_opus", "name": "Opus at review time"},
        "selected_provider_source": "reviewer_override",
        "attempt_no": 2,
    })

    response = post_inbox(_body(verdict="pass", findings=[], comment="ok"))

    assert response.status_code == 201, response.text
    kwargs = review_env["insert"].call_args.kwargs
    assert kwargs == {
        "doc_id": DOC_ID,
        "revision_no": 2,
        "reviewer_id": "aip_opus",
        "verdict": "pass",
        "findings_json": "[]",
        "comment": "ok",
        "reviewed_at": kwargs["reviewed_at"],
        "review_run_id": "air_review_0393",
        "requested_provider_id": "aip_sonnet",
        "actual_provider_id": "aip_opus",
        "actual_provider_name": "Opus at review time",
        "provider_source": "fallback",
        "attempt_no": 2,
        "fallback_used": True,
    }
def test_review_submission_keeps_project_default_source_when_provider_matches(review_env, monkeypatch):
    monkeypatch.setattr(ai_runtime, "get_run_record", lambda run_id: {
        "run_id": run_id,
        "action_scope": "review",
        "doc_ref": DOC_ID,
        "requested_provider_id": "aip_sonnet",
        "provider_id": "aip_sonnet",
        "provider": {"id": "aip_sonnet", "name": "Sonnet snapshot"},
        "selected_provider_source": "project_default",
        "attempt_no": 1,
    })

    response = post_inbox(_body(verdict="pass", findings=[], comment="ok"))

    assert response.status_code == 201, response.text
    kwargs = review_env["insert"].call_args.kwargs
    assert kwargs["review_run_id"] == "air_review_0393"
    assert kwargs["requested_provider_id"] == kwargs["actual_provider_id"] == "aip_sonnet"
    assert kwargs["actual_provider_name"] == "Sonnet snapshot"
    assert kwargs["provider_source"] == "project_default"
    assert kwargs["attempt_no"] == 1
    assert kwargs["fallback_used"] is False
def test_a_path_outside_the_scratch_directory_is_refused(review_env):
    doc_path = _write_payload(review_env["outside"])

    response = post_inbox(
        _body(doc_path=doc_path),
    )

    assert response.status_code == 422
    assert "doc_path is not accessible" in response.json()["error_message"]
    review_env["insert"].assert_not_called()
