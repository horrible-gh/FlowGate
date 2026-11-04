"""Composition used by inbox tests mounting routers without server startup."""
import pytest


@pytest.fixture(autouse=True)
def card_document_edit_ports(monkeypatch):
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports
    from flowgate.card_document_edit import integration
    # Keep each test's registry isolated, as a separate app startup would be.
    monkeypatch.setattr(ports, "_handlers", dict(ports._handlers))
    integration.register()


@pytest.fixture(autouse=True)
def legacy_body_storage(request, monkeypatch, tmp_path):
    # These suites create their existing bodies under their per-test tmp_path.
    if request.module.__name__.rsplit(".", 1)[-1] in {
        "test_inbox_conversation_0044", "test_inbox_step9_wiring",
        "test_reject_resubmit_own_slot_0457", "test_t574_rejection_history",
    }:
        monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path))
