from __future__ import annotations

import json

from modules.flow_gate.api import inbox_routes
from modules.flow_gate.db import connection as db_connection
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.settings import system_settings_service
from modules.flow_gate.workflow import event_logger
import startup


def test_annotation_metric_counts_both_operations(monkeypatch):
    class Store:
        def _fetch_all(self, sql, params):
            assert "GROUP BY event_type" in sql
            return [
                {"event_type": event_logger.EVT_REVIEW_ANNOTATION_READ_FAILED, "n": 2},
                {"event_type": event_logger.EVT_REVIEW_ANNOTATION_WRITE_FAILED, "n": 3},
            ]
    monkeypatch.setattr("modules.flow_gate.db.connection.get_store", lambda: Store())
    assert event_logger.count_review_annotation_failures(since="2026-08-27T00:00:00+09:00") == {
        "read_failed": 2, "write_failed": 3, "total": 5,
        "since": "2026-08-27T00:00:00+09:00",
    }


def test_deployment_marker_contains_sha_version_and_jst(monkeypatch):
    seen = []
    monkeypatch.setattr(system_settings_service, "_runtime_build", lambda: ("1.2.3", "abc1234"))
    monkeypatch.setattr(event_logger, "log_event", lambda **kw: seen.append(kw) or kw)
    system_settings_service.record_deployment_started()
    assert len(seen) == 1
    assert seen[0]["event_type"] == event_logger.EVT_DEPLOYMENT_STARTED
    assert seen[0]["metadata"]["build_id"] == "abc1234"
    assert seen[0]["metadata"]["app_version"] == "1.2.3"
    assert seen[0]["metadata"]["started_at"].endswith("+09:00")


def test_deployment_marker_persists_through_fk_enforced_store(monkeypatch, test_db):
    class Store:
        def _execute(self, sql, params=None):
            test_db.execute(sql, params or [])

        def _fetch_one(self, sql, params=None):
            row = test_db.execute(sql, params or []).fetchone()
            return dict(row) if row is not None else None

    test_db.execute("PRAGMA foreign_keys = ON")
    monkeypatch.setattr(db_connection, "STORE", Store())
    monkeypatch.setattr(system_settings_service, "_runtime_build", lambda: ("1.2.3", "abc1234"))

    created = system_settings_service.record_deployment_started()

    assert created["event_type"] == event_logger.EVT_DEPLOYMENT_STARTED
    assert created["project_id"] == "__SYSTEM__"
    assert created["actor_user_id"] == "u-system"
    assert json.loads(created["metadata"])["build_id"] == "abc1234"


def test_startup_calls_deployment_marker_once(monkeypatch):
    calls = []
    # Exercise orchestration without starting services, touching host storage, or opening a DB.
    phases = (
        "configure_console_encoding", "check_runtime_dependencies", "preload_singletons",
        "recover_ai_invoke_leases", "recover_git_sessions", "encrypt_ai_provider_keys",
        "start_scratch_sweeps", "register_server_instance", "start_conversation_integration",
        "start_card_document_integration", "start_card_settings_integration",
        "start_card_completion_integration", "start_card_memo_integration",
        "start_graph_policy_integration", "start_graph_validation_integration",
        "start_graph_run_integration", "start_work_plan_deploy_integration",
        "recover_graph_time_machine", "repair_graph_drafts", "start_remote_ai_supervisor",
        "check_agent_pepper", "recover_ai_evaluations", "start_snapshot_cleanup",
        "remove_retired_source_bundle_storage", "recover_chat_commands", "start_attachment_requests",
    )
    for phase in phases:
        monkeypatch.setattr(startup, phase, lambda: None)
    from flowgate.graph_run import execution
    from flowgate.card_document_edit import integration
    monkeypatch.setattr(execution, "recover", lambda: None)
    monkeypatch.setattr(integration, "register", lambda: None)
    monkeypatch.setattr(startup, "record_deployment", lambda: calls.append(1))
    startup.run_all()
    assert calls == [1]


def test_change_summary_event_copies_response_core(monkeypatch):
    summary = {"changed": False, "lines_added": 0, "lines_removed": 0, "sections_changed": []}
    seen = []
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda doc_id: {
        "id": 41, "doc_id": doc_id, "project_id": "flowgate", "group_id": "flowgate.default.0468"})
    monkeypatch.setattr(event_logger, "log_event", lambda **kw: seen.append(kw) or kw)
    inbox_routes._record_change_summary("flowgate.default.0468.0013-T", 2, summary, "worker")
    meta = seen[0]["metadata"]
    assert meta == {"changed": False, "lines_added": 0, "lines_removed": 0,
                    "doc_id": "flowgate.default.0468.0013-T", "revision_no": 2}
    assert json.loads(json.dumps(meta))["changed"] == summary["changed"]


def test_annotation_failure_uses_fk_safe_system_fallbacks(monkeypatch):
    seen = []
    monkeypatch.setattr(ai_invoke_service.db_docs, "get_by_id", lambda doc_id: None)
    monkeypatch.setattr(event_logger, "log_review_annotation_failed",
                        lambda **kw: seen.append(kw) or kw)

    ai_invoke_service._log_review_annotation_failure(
        "read", {"doc_id": "missing-doc"}, {}, RuntimeError("db down"),
    )

    assert seen[0]["project_id"] == "__SYSTEM__"
    assert seen[0]["actor_user_id"] == "u-system"


def test_change_summary_event_uses_fk_safe_system_fallbacks(monkeypatch):
    seen = []
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda doc_id: None)
    monkeypatch.setattr(event_logger, "log_event", lambda **kw: seen.append(kw) or kw)

    inbox_routes._record_change_summary("missing-doc", 1, {"changed": False}, None)

    assert seen[0]["project_id"] == "__SYSTEM__"
    assert seen[0]["actor_user_id"] == "u-system"


def test_change_summary_event_failure_never_breaks_save_path(monkeypatch):
    monkeypatch.setattr(inbox_routes.db_docs, "get_by_id", lambda doc_id: (_ for _ in ()).throw(RuntimeError("db down")))
    assert inbox_routes._record_change_summary("doc", 1, {"changed": None, "error": "summary unavailable"}, "worker") is None