"""0428 T#6 UI admission read model, preserved audit/verdict, independent SSE."""
import json
from unittest.mock import AsyncMock
import pytest
from test_workflow_proceed_0428 import prepared, client, post, counts, TS
from test_workflow_completion_0428 import completion_database, db, ROOT, RESULT, GROUP
from modules.flow_gate.db import workflow_completion as decisions, workflow_sequences as seqdb
from modules.flow_gate.services import workflow_proceed_service as command
from modules.flow_gate.api.v1.events import publisher


@pytest.fixture(autouse=True)
def failing_result(prepared, monkeypatch):
    from modules.flow_gate.db import test_runs
    from modules.flow_gate.services import test_basis_service
    result = test_runs.insert_run(doc_id=TS, revision_no=7, triggered_via='ui', runner_id='human-1',
        contract_version=2, result_meta=json.dumps({'run_kind':'spec_results','basis_id':'basis-A',
        'test_basis':{'basis_id':'basis-A','source':'original'}}),
        cases=[{'case_no':'C1','title':'original failure','cmd':'','expect':'ok'}])
    prepared[0]._execute("UPDATE test_runs SET status='failed', overall='FAIL' WHERE run_id=?", [result['run_id']])
    prepared[0]._execute("UPDATE test_run_cases SET case_status='FAIL', actual='broken', output_tail='original evidence' WHERE run_id=?", [result['run_id']])
    monkeypatch.setattr(test_basis_service, 'live_state', lambda *a,**kw: {
        'live':{'basis_id':'basis-A'},'state':test_basis_service.VALID,'no_worktree':False,'reasons':[]})


def state(client):
    response = client.get('/api/v1/documents/' + RESULT + '/unapproved-progress')
    assert response.status_code == 200, response.text
    return response.json()


def test_admission_preview_is_read_only_then_audit_warns_at_root_and_final(client, prepared):
    before = counts(prepared)
    preview = state(client)
    assert preview['allowed'] is True
    assert preview['slot_id'] == prepared[2] and preview['document_revision'] == 7
    assert preview['test_gate']['passed'] is False
    assert counts(prepared) == before
    confirmed = post(client, prepared).json()
    preview = state(client)
    assert preview['allowed'] is False and preview['reason'] == 'completion_already_completed'
    history = preview['history']
    assert len(history) == 1 and history[0]['valid'] is True
    assert history[0]['decision_id'] == confirmed['decision_id']
    assert history[0]['actor_user_id'] == confirmed['actor_user_id']
    assert history[0]['decided_at'] == confirmed['decided_at']
    assert history[0]['test_gate']['passed'] is False
    assert counts(prepared) == (1, 1, 0, 1, 0, 0)
    at_root = client.get('/api/v1/documents/' + ROOT + '/unapproved-progress').json()
    at_final = client.get('/api/v1/groups/' + GROUP + '/unapproved-progress').json()
    assert at_root['history'] == at_final['history'] == history


@pytest.mark.parametrize('seam,value,reason', [
    ('permissions', None, 'completion_permission_denied'),
    ('disposed', True, 'group_disposed'),
    ('active_ai', {'run_id':'live'}, 'completion_ai_run_active'),
    ('active_test', {'run_id':'running'}, 'completion_test_run_active'),
    ('attempt', {'state':'recovery_required'}, 'tr2_recovery_required'),
    ('revert', [RESULT], 'workflow_revert_pending'),
])
def test_preview_matches_post_refusal_without_side_effects(client, prepared, monkeypatch, seam, value, reason):
    if seam == 'permissions':
        from modules.flow_gate.auth.middleware import get_current_user
        client.app.dependency_overrides[get_current_user] = lambda: {'user_id':'human-1','is_admin':False}
    else:
        targets = {'disposed':(command.process_service,'is_group_disposed'),
                   'active_ai':(command.runtime,'active_run_for_group'),
                   'active_test':(command.db_test_runs,'get_active_by_group'),
                   'attempt':(command.db_attempts,'blocking_by_group'),
                   'revert':(command.tr2_service,'group_revert_pending')}
        monkeypatch.setattr(*targets[seam], lambda *a,**kw:value)
    before = counts(prepared)
    preview = state(client)
    assert preview['allowed'] is False and preview['reason'] == reason
    assert post(client, prepared).json()['error']['code'] == reason
    assert counts(prepared) == before


def test_invalidated_decision_keeps_original_verdict_and_identity(client, prepared):
    confirmed = post(client, prepared).json()
    old = state(client)['history'][0]
    decisions.invalidate_slot(prepared[1]['id'], prepared[2], expected_generation=old['slot_generation'], reason='test-result-changed')
    audit = state(client)['history'][0]
    assert audit['valid'] is False and audit['invalidation_reason'] == 'test-result-changed'
    assert audit['test_gate'] == old['test_gate']
    assert audit['decision_id'] == confirmed['decision_id']
    assert audit['decided_at'] == confirmed['decided_at']
    # Same revision reopens with new generation; old key does not complete it.
    assert state(client)['allowed'] is True


def test_sse_preserves_review_and_failure_payload_and_failure_delivery_durable(client, prepared, monkeypatch):
    broadcast = AsyncMock()
    monkeypatch.setattr(publisher,'broadcast_event',broadcast)
    response = post(client, prepared)
    assert response.status_code == 200
    event = broadcast.call_args.args[0]
    assert event.event_type.value == 'workflow_completion_changed'
    assert event.payload['doc_review_status'] == 'rejected'
    assert event.payload['slot_completed'] is True
    assert 'next_status' not in event.payload and 'rejection_history' not in event.payload
    broadcast.side_effect = RuntimeError('SSE offline')
    assert post(client, prepared).json() == response.json()
    assert state(client)['history'][0]['decision_id'] == response.json()['decision_id']
    assert counts(prepared) == (1,1,0,1,0,0)


@pytest.mark.parametrize('delete_root', [False, True])
def test_deleted_document_or_sequence_remains_in_group_audit(client, prepared, delete_root):
    from modules.flow_gate.db import documents
    confirmed = post(client, prepared).json()
    before = state(client)['history'][0]
    documents.delete(ROOT if delete_root else RESULT)
    history = client.get('/api/v1/groups/' + GROUP + '/unapproved-progress').json()['history']
    assert len(history) == 1
    assert history[0]['decision_id'] == confirmed['decision_id']
    assert history[0]['actor_user_id'] == before['actor_user_id']
    assert history[0]['test_gate'] == before['test_gate']
    assert history[0]['actual_result'] == before['actual_result']
    assert history[0]['valid'] is False and history[0]['invalidated_at']


def test_invalidation_sse_only_after_commit_not_rollback(client, prepared, monkeypatch):
    confirmed = post(client, prepared).json()
    events = []
    monkeypatch.setattr(publisher, 'broadcast_event_threadsafe', lambda event: events.append(event))
    with pytest.raises(RuntimeError):
        with prepared[0].transaction():
            decisions.invalidate_slot(prepared[1]['id'], prepared[2], expected_generation=confirmed['slot_generation'], reason='test-rollback')
            assert events == []
            raise RuntimeError('rollback')
    assert events == []
    saved = decisions.get_decision(confirmed['decision_id'])
    assert saved['invalidated_at'] is None
    decisions.invalidate_slot(prepared[1]['id'], prepared[2], expected_generation=confirmed['slot_generation'], reason='test-commit')
    assert len(events) == 1 and events[0].event_type.value == 'workflow_completion_changed'
    assert events[0].project == 'flowgate' and events[0].group_id == GROUP


def test_history_scope_does_not_leak_other_group(client, prepared):
    post(client, prepared)
    assert client.get('/api/v1/groups/flowgate.default.9999/unapproved-progress').json()['history'] == []


def test_group_audit_survives_deletion_of_last_member(client, prepared):
    from modules.flow_gate.db import documents
    confirmed = post(client, prepared).json()
    member_ids = prepared[0]._fetch_all("SELECT doc_id FROM documents WHERE group_id = ?", [GROUP])
    for member in member_ids:
        documents.delete(member['doc_id'])
    assert prepared[0]._fetch_one("SELECT COUNT(*) AS n FROM documents WHERE group_id = ?", [GROUP])['n'] == 0
    history = client.get('/api/v1/groups/' + GROUP + '/unapproved-progress').json()['history']
    assert len(history) == 1 and history[0]['decision_id'] == confirmed['decision_id']
    assert history[0]['valid'] is False
