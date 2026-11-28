"""Server assembly prefers the existing card slot over an orphan report."""
import json
import pytest
from test_card_document_edit_0127 import env, D, F
from test_test_card_rework_0081 import setup, body
from modules.flow_gate.db import documents, test_runs
from modules.flow_gate.services import test_run_service as runs
from flowgate.card_documents import repository
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard


def test_next_assembly_converges_orphan_run_to_revision_zero_slot(env, monkeypatch):
    doc, run, orphan = setup(env, monkeypatch)
    slot_id = F + '.0099-TSR'
    documents.create({'doc_id': slot_id, 'project_id': 'flowgate', 'module': 'v02', 'group_id': F,
                      'type_code': 'TSR', 'seq': 99, 'title': 'Empty report', 'status': 'draft', 'owner_id': 'user'})
    repository.insert(F, 'research', 'scenario', 'TS', D, True, 1, 'user')
    repository.insert(F, 'research', 'report', 'TSR', slot_id, True, 2, 'user')
    assert documents.get_by_id(slot_id)['revision_no'] == 0
    assert test_runs.get_run(run['run_id'])['tsr_doc_id'] == orphan
    before = documents.get_by_id(orphan)
    report = runs.assemble_tsr(doc, test_runs.get_run(run['run_id']), [], run_chain=False)
    assert report == slot_id
    assert documents.get_by_id(slot_id)['revision_no'] >= 1
    assert documents.get_by_id(slot_id)['target_id'] == D
    assert 'TC-001' in body(slot_id)
    assert test_runs.get_run(run['run_id'])['tsr_doc_id'] == slot_id
    assert documents.get_by_id(orphan) == before  # no historical data deletion
    assert runs.assemble_tsr(doc, test_runs.get_run(run['run_id']), [], run_chain=False) == slot_id


@pytest.mark.parametrize("initial", [
    {"consumed_at": "before"},
    {"revoked_at": "before"},
    {"action_scope": "edit"},
    {},
])
def test_handoff_without_valid_unconsumed_baseline_is_not_output(monkeypatch, initial):
    from modules.flow_gate.services.ai_invoke import oracle
    token = ({"action_scope": "test_run", "doc_ref": "ts", **initial} if initial else {})
    monkeypatch.setattr(oracle.db_tokens, "get_by_id", lambda token_id: token)
    monkeypatch.setattr(oracle.db_test_runs, "list_by_doc", lambda doc_id: [{"run_id": "old"}])
    landed = oracle._scope_oracle("test_run", "worker-token", "ts")
    assert not landed()
    token.update(action_scope="test_run", doc_ref="ts", consumed_at="after", revoked_at=None)
    assert not landed()


def test_existing_server_run_handoff_counts_as_output_only_after_its_token_is_consumed(monkeypatch):
    from modules.flow_gate.services.ai_invoke import oracle
    token = {"action_scope": "test_run", "doc_ref": "ts", "consumed_at": None, "revoked_at": None}
    monkeypatch.setattr(oracle.db_tokens, "get_by_id", lambda token_id: token)
    monkeypatch.setattr(oracle.db_test_runs, "list_by_doc", lambda doc_id: [{"run_id": "already_queued"}])
    landed = oracle._scope_oracle("test_run", "worker-token", "ts")
    assert not landed()
    token["consumed_at"] = "now"
    assert landed()
    # Reusing that token for another worker must not inherit this success.
    retry = oracle._scope_oracle("test_run", "worker-token", "ts")
    assert not retry()
    token["consumed_at"] = "later"
    assert not retry()
    token["doc_ref"] = "other-ts"
    assert not landed()
    token["doc_ref"], token["revoked_at"] = "ts", "now"
    assert not landed()
