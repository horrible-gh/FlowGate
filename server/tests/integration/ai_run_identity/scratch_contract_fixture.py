"""Real isolated ownership DB for existing scratch/CLI regression fixtures."""
from uuid import uuid5, NAMESPACE_URL
import pytest
from modules.flow_gate.db import ai_run_identity as identity
from tests.integration.ai_run_identity.test_ai_run_identity_0138 import identity_db

@pytest.fixture(autouse=True)
def scratch_identity(identity_db, monkeypatch, tmp_path):
    # Other suites may mock the general DB connection for unrelated settings.
    # Ownership retains its own fully migrated real SQLite store.
    monkeypatch.setattr(identity, "get_store", lambda: identity_db[1])
    # GC regressions call startup.run_all(). Keep the real lease/sweep order,
    # but isolate unrelated process registries, supervisors and service bootstraps.
    import startup
    for name in ("configure_console_encoding", "record_deployment", "preload_singletons",
                 "register_server_instance", "recover_git_sessions", "start_conversation_integration",
                 "start_card_document_integration", "start_card_settings_integration",
                 "start_card_completion_integration", "start_card_memo_integration",
                 "start_graph_policy_integration", "start_graph_validation_integration",
                 "start_graph_run_integration", "start_work_plan_deploy_integration",
                 "recover_graph_time_machine", "start_remote_ai_supervisor", "encrypt_ai_provider_keys",
                 "check_agent_pepper", "recover_ai_evaluations", "start_snapshot_cleanup",
                 "remove_retired_source_bundle_storage", "recover_chat_commands"):
        monkeypatch.setattr(startup, name, lambda: None)
    from flowgate.card_document_edit import integration as document_integration
    from flowgate.graph_run import execution
    monkeypatch.setattr(document_integration, "register", lambda: None)
    monkeypatch.setattr(execution, "recover", lambda: None)
    from contextlib import nullcontext
    from pathlib import Path
    from modules.flow_gate.graph_workflow.services import card_document_ports
    def scratch_write_gate(operation, candidates, action=None, *, trees=()):
        assert operation in ("paths", "paths_scope")
        assert all(Path(candidate).resolve().is_relative_to(tmp_path.resolve())
                   for candidate in (*candidates, *trees))
        return nullcontext() if operation == "paths_scope" else action()
    monkeypatch.setitem(card_document_ports._handlers, card_document_ports.DOCUMENT_WRITE_GATE, scratch_write_gate)


def nonce(project, run_id):
    return str(uuid5(NAMESPACE_URL, project + "/" + run_id))


def record_nonce(project, run_id):
    value = nonce(project, run_id)
    return value if identity.scratch_owner(project, run_id, value) else None


def owned_nonce(project, run_id):
    value = nonce(project, run_id)
    identity.claim({"run_id": run_id, "execution_nonce": value, "project_id": project,
                    "group_id": "", "started_at": "2026-08-28T01:00:00+00:00"})
    return value
