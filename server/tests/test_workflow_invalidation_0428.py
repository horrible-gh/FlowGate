"""0428 T#3: actual mutations, migrated transactions, historical decision survival."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workflow_completion_0428 import completion_database, db, ROOT, RESULT, GROUP
from test_workflow_proceed_0428 import prepared, TS, invoke
from modules.flow_gate.db import documents, test_runs, workflow_sequences as sequences
from modules.flow_gate.db import workflow_completion as decisions
from modules.flow_gate.db import workflow_invalidation as invalidation
from modules.flow_gate.services.workflow_completion_service import slot_completed


def current(db):
    return sequences.get_item_by_id(db[2])


def decide(db, key='event-before', **over):
    row = current(db)
    doc = documents.get_by_id(row['result_doc_id'])
    args = dict(sequence_id=row['sequence_id'], slot_id=row['id'],
                slot_generation=row['slot_generation'], result_doc_id=row['result_doc_id'],
                document_revision=doc['revision_no'], actor_user_id='human-1', request_key=key,
                dispatch={'chain_id':'chain-1', 'stop_run_id':'stop-1', 'stop_snapshot':{'stop_kind':'system'}})
    args.update(over)
    return decisions.record_decision(**args)


def assert_invalid(db, decision):
    old = decisions.get_decision(decision['decision_id'])
    assert old['invalidated_at'] and old['invalidation_reason']
    for name in ('decision_id','actor_user_id','decided_at','document_revision','slot_generation','result_doc_id'):
        assert old[name] == decision[name]
    assert current(db)['slot_generation'] > decision['slot_generation']
    assert current(db)['slot_decision_id'] is None
    row = next(r for r in sequences.get_sequence_items(db[1]['id']) if r['id'] == db[2])
    assert not slot_completed(row)
    assert decisions.pending_dispatches() == []
    dispatch = decisions.get_dispatch(decision['decision_id'], 'chain-1', 'stop-1')
    assert dispatch['state'] == 'cancelled' and dispatch['error_code'] == 'completion_invalidated'
    assert decisions.get_store()._fetch_one('SELECT * FROM workflow_completion_requests WHERE request_key = ?',
                                          ['event-before'])['decision_id'] == decision['decision_id']


@pytest.mark.parametrize('fields', [
    {'revision_no':8}, {'revision_no':6}, {'doc_review_status':'pending_review'},
    {'doc_review_status':'revised'}, {'doc_review_status':'rejected'},
    {'meta':json.dumps({'basis_id':'new-basis'})},
])
def test_document_mutations_including_same_revision_reopen(db, fields):
    old = decide(db)
    documents.update(RESULT, fields)
    assert_invalid(db, old)


def test_actual_review_cas_and_revision_cas_conflicts_do_not_reopen(db):
    old = decide(db)
    before = current(db)
    assert documents.update_review_status_cas(RESULT, 'pending_review', 'revised') is None
    assert not documents.update_review_cas(RESULT, 8, 'rejected', {'doc_review_status':'revised'})
    assert current(db) == before
    assert decisions.get_decision(old['decision_id'])['invalidated_at'] is None
    assert documents.update_review_status_cas(RESULT, 'rejected', 'revised')
    assert_invalid(db, old)


def test_same_revision_generic_cas_reopen(db):
    old = decide(db)
    assert db[0].update_cas('documents', RESULT, 'doc_id', 'doc_review_status', 'rejected',
                            {'doc_review_status':'rejected'})
    assert_invalid(db, old)


def test_revision_a_b_a_cannot_restore_completion(db):
    old = decide(db)
    documents.update(RESULT, {'revision_no':8})
    documents.update(RESULT, {'revision_no':7})
    assert_invalid(db, old)
    with pytest.raises(decisions.CompletionConflict, match='request_key_conflict'):
        decide(db)
    new = decide(db, 'new-confirmation')
    assert new['decision_id'] != old['decision_id']


@pytest.mark.parametrize('replace', [None, GROUP+'.0003-TR2'])
def test_result_clear_replace_and_a_b_a(db, replace):
    old = decide(db)
    sequences.set_item_result_doc_id(db[2], replace)
    sequences.set_item_result_doc_id(db[2], RESULT)
    assert_invalid(db, old)


def test_refresh_provider_and_same_result_binding_keep_identity(db):
    old = decide(db)
    before = current(db)
    documents.update(RESULT, {'title':'new display', 'meta':json.dumps({'provider_id':'new-provider'})})
    sequences.set_item_result_doc_id(db[2], RESULT)
    sequences.claim_item_result_doc_id(db[2], RESULT)
    sequences.update_sequence_item_execution_settings(db[2], review_count=2,
                                                     reviewer_provider_id='other')
    assert current(db)['slot_generation'] == before['slot_generation']
    assert decisions.get_decision(old['decision_id'])['invalidated_at'] is None
    assert decide(db)['decision_id'] == old['decision_id']


def make_run(prepared, *, status='failed'):
    run = test_runs.insert_run(doc_id=TS, revision_no=7, triggered_via='ui', runner_id='human-1',
        contract_version=2, result_meta=json.dumps({'basis_id':'A'}),
        cases=[{'case_no':'C1','title':'case','cmd':'','expect':'ok'}])
    prepared[0]._execute('UPDATE test_runs SET status = ?, overall = ? WHERE run_id = ?',
                         [status, 'FAIL', run['run_id']])
    test_runs.set_run_tsr_doc(run['run_id'], RESULT)
    return test_runs.get_run(run['run_id'])


@pytest.mark.parametrize('event', ['new_run','result_meta','case','ts_revision'])
def test_tsr_evidence_changes_without_tsr_revision(prepared, event):
    run = make_run(prepared)
    old = decide(prepared)
    tsr = documents.get_by_id(RESULT)
    if event == 'new_run':
        make_run(prepared, status='running')
    elif event == 'result_meta':
        test_runs.set_run_result_meta(run['run_id'], json.dumps({'basis_id':'B'}))
    elif event == 'case':
        case = test_runs.list_cases(run['run_id'])[0]
        test_runs.set_execution_case_result(case_row_id=case['id'], status='BLOCKED',
                                           actual='new evidence', case_meta='{}')
    else:
        documents.update(TS, {'revision_no':8})
    assert documents.get_by_id(RESULT) == tsr
    assert_invalid(prepared, old)


def test_identical_result_replay_chain_metadata_and_lost_finish_cas_keep_decision(prepared):
    run = make_run(prepared)
    old = decide(prepared)
    before = current(prepared)
    test_runs.set_run_result_meta(run['run_id'], run['result_meta'])
    test_runs.set_run_result_meta(run['run_id'], json.dumps({'basis_id':'A', 'chain':{'hop':'new'}}))
    test_runs.finish_run(run_id=run['run_id'], status='passed')  # failed, so CAS loses
    assert current(prepared) == before
    assert decisions.get_decision(old['decision_id'])['invalidated_at'] is None


def test_basis_a_b_a_measured_through_real_service(prepared, tmp_path, monkeypatch):
    from basis_support import make_repo
    from modules.flow_gate.services import test_basis_service as basis, source_fingerprint
    root = make_repo(tmp_path/'basis-live')
    monkeypatch.setattr(source_fingerprint, 'resolve_worktree', lambda *a: root)
    doc = documents.get_by_id(TS)
    first = basis.resolve(doc, [])
    old = decide(prepared)
    # Display and unchanged measurement preserve a decision.
    assert basis.resolve(doc, [])['basis_id'] == first['basis_id']
    assert basis.resolve(doc, [], memo=True)['basis_id'] == first['basis_id']
    assert current(prepared)['slot_generation'] == old['slot_generation']
    path = root/'server'/'app.py'
    original = path.read_bytes()
    path.write_text('VALUE = 2\n')
    assert basis.resolve(doc, [])['basis_id'] != first['basis_id']
    path.write_bytes(original)
    assert basis.resolve(doc, [])['basis_id'] == first['basis_id']
    assert_invalid(prepared, old)


def test_proceed_replay_observes_basis_and_commits_invalidation_before_refusal(prepared, monkeypatch):
    from modules.flow_gate.services import test_basis_service as basis
    live = {'value':'A'}
    monkeypatch.setattr(basis, 'live_state', lambda *a,**kw: {
        'live':{'basis_id':live['value']}, 'state':None, 'reasons':[], 'no_worktree':False})
    first = invoke(prepared)
    live['value'] = 'B'
    with pytest.raises(Exception, match='completion_slot_changed'):
        invoke(prepared, expected_slot_generation=first['slot_generation'])
    old = decisions.get_decision(first['decision_id'])
    assert old['invalidated_at'] and old['invalidation_reason'] == 'tsr_basis_changed'
    live['value'] = 'A'
    with pytest.raises(Exception, match='completion_slot_changed'):
        invoke(prepared, expected_slot_generation=first['slot_generation'])
    assert current(prepared)['slot_generation'] > first['slot_generation']


def test_time_machine_reopens_target_and_all_later_slots(prepared, monkeypatch):
    from modules.flow_gate.services import workflow_rework_service as rework
    prepared[0]._execute('UPDATE documents SET seq = 1 WHERE doc_id = ?', [TS])
    old = decide(prepared)
    later = next(r for r in sequences.get_sequence_items(prepared[1]['id']) if r['type']=='TR2')
    before_later = later['slot_generation']
    before_ts = sequences.get_item_by_result_doc_id(TS)['slot_generation']
    monkeypatch.setattr(rework, 'assert_group_mutation_allowed', lambda *a,**kw:None)
    monkeypatch.setattr(rework, 'assert_no_revert_pending', lambda *a:None)
    monkeypatch.setattr(rework.git_service, 'raise_if_git_session_blocks_reopen', lambda *a:None)
    monkeypatch.setattr(rework, '_rearm_git', lambda *a,**kw:None)
    monkeypatch.setattr(rework, 'record_return_point', lambda *a,**kw:None)
    monkeypatch.setattr(rework, 'return_point_payload', lambda *a:{})
    monkeypatch.setattr(rework, 'group_workflow_root_doc', lambda doc:documents.get_by_id(ROOT))
    monkeypatch.setattr(rework, 'workflow_step_doc_ids', lambda doc:{TS,RESULT})
    monkeypatch.setattr(rework.event_logger, 'log_event', lambda **kw:None)
    out = rework.reopen_to_target(RESULT, 2, 'human-1')
    assert RESULT in out['reopened']
    assert_invalid(prepared, old)
    assert sequences.get_item_by_id(later['id'])['slot_generation'] > before_later
    assert sequences.get_item_by_result_doc_id(TS)['slot_generation'] == before_ts


@pytest.mark.parametrize('root', [False,True])
def test_document_or_sequence_deletion_preserves_audit(prepared, root):
    old = decide(prepared)
    documents.delete(ROOT if root else RESULT)
    saved = decisions.get_decision(old['decision_id'])
    assert saved['invalidated_at'] and saved['actor_user_id'] == old['actor_user_id']
    assert decisions.get_dispatch(old['decision_id'],'chain-1','stop-1')['state']=='cancelled'
    assert decisions.pending_dispatches() == []


def test_mutation_cancellation_failure_rolls_back_evidence_generation_and_history(db, monkeypatch):
    old = decide(db)
    before = current(db)
    doc_before = documents.get_by_id(RESULT)
    execute = db[0]._execute
    def failing(sql, params=None):
        if 'UPDATE workflow_completion_dispatches' in sql:
            raise RuntimeError('cancel persistence failed')
        return execute(sql, params)
    monkeypatch.setattr(db[0], '_execute', failing)
    with pytest.raises(RuntimeError, match='cancel persistence failed'):
        documents.update(RESULT, {'revision_no':8})
    assert documents.get_by_id(RESULT) == doc_before
    assert current(db) == before
    assert decisions.get_decision(old['decision_id'])['invalidated_at'] is None
    assert decisions.get_dispatch(old['decision_id'],'chain-1','stop-1')['state']=='pending'


def test_dispatch_start_cas_after_invalidation_and_running_recovery(db):
    old = decide(db)
    dispatch = decisions.get_dispatch(old['decision_id'],'chain-1','stop-1')
    assert decisions.cas_dispatch(dispatch['dispatch_id'], expected_state='pending', expected_version=0,
                                  state='running',run_id='already-started')
    documents.update(RESULT, {'doc_review_status':'pending_review'})
    saved = decisions.get_dispatch(old['decision_id'],'chain-1','stop-1')
    assert saved['state']=='recovery_required' and saved['run_id']=='already-started'
    assert not decisions.cas_dispatch(saved['dispatch_id'],expected_state='recovery_required',
                                     expected_version=saved['state_version'],state='pending')


@pytest.mark.parametrize('race', range(5))
def test_invalidation_vs_dispatch_claim_cannot_leave_invalid_running_intent(db, race):
    old = decide(db)
    dispatch = decisions.get_dispatch(old['decision_id'],'chain-1','stop-1')
    with ThreadPoolExecutor(max_workers=2) as pool:
        claim=pool.submit(decisions.cas_dispatch, dispatch['dispatch_id'],expected_state='pending',
                          expected_version=0,state='running',run_id='claimed-run')
        mutate=pool.submit(documents.update, RESULT, {'revision_no':8})
        claim.result(timeout=15)
        mutate.result(timeout=15)
    saved=decisions.get_dispatch(old['decision_id'],'chain-1','stop-1')
    assert saved['state'] in {'cancelled','recovery_required'}
    assert decisions.get_decision(old['decision_id'])['invalidated_at']
    assert decisions.pending_dispatches()==[]



def test_source_basis_mutation_and_excluded_secret_have_correct_boundaries(prepared, tmp_path):
    from basis_support import make_repo
    root = make_repo(tmp_path/'source-live')
    old = decide(prepared)
    with invalidation.source_mutation('flowgate', GROUP, root):
        (root/'.env').write_text('SECRET=2\n')
    assert decisions.get_decision(old['decision_id'])['invalidated_at'] is None
    with invalidation.source_mutation('flowgate', GROUP, root):
        (root/'server'/'app.py').write_text('VALUE = 2\n')
    assert_invalid(prepared, old)


def test_partial_source_mutation_invalidates_before_error_is_propagated(prepared, tmp_path):
    from basis_support import make_repo
    root = make_repo(tmp_path/'partial-source')
    old = decide(prepared)
    with pytest.raises(RuntimeError, match='partial mutation'):
        with invalidation.source_mutation('flowgate', GROUP, root):
            (root/'server'/'app.py').write_text('VALUE = 2\n')
            raise RuntimeError('partial mutation')
    assert_invalid(prepared, old)


def test_canonical_tr2_save_revision_boundary_invalidates(db):
    from modules.flow_gate.documents import tr2_service
    old = decide(db)
    tr2_service._cas_bump(db[0], RESULT, 7, 'now', 'fixture.json', 'fixture.json')
    assert_invalid(db, old)
    assert documents.get_by_id(RESULT)['revision_no']==8


def test_sequence_delete_recreate_never_inherits_decision(db):
    old = decide(db)
    with invalidation.document_deletion(ROOT):
        db[0]._execute('DELETE FROM workflow_sequences WHERE id = ?', [db[1]['id']])
    sequences.insert_sequence(ROOT)
    seq = sequences.get_sequence_by_doc_id(ROOT)
    sequences.insert_sequence_item(seq['id'], 1, 'TSR', 'new report', 'R', 0)
    slot = sequences.get_sequence_items(seq['id'])[0]
    sequences.set_item_result_doc_id(slot['id'], RESULT)
    assert slot['id'] != old['slot_id'] or seq['id'] != old['sequence_id']
    assert not slot_completed(sequences.get_sequence_items(seq['id'])[0])
    assert decisions.get_decision(old['decision_id'])['invalidated_at']
    assert decisions.get_dispatch(old['decision_id'],'chain-1','stop-1')['state']=='cancelled'



def test_case_replay_keeps_decision_despite_new_server_timestamp(prepared):
    run = make_run(prepared)
    case = test_runs.list_cases(run['run_id'])[0]
    args = dict(case_row_id=case['id'],status='FAIL',actual='broken',case_meta='{}')
    test_runs.set_execution_case_result(**args)
    old = decide(prepared)
    prepared[0]._execute("UPDATE test_run_cases SET finished_at = '2000-01-01' WHERE id = ?", [case['id']])
    test_runs.set_execution_case_result(**args)
    assert decisions.get_decision(old['decision_id'])['invalidated_at'] is None
    assert current(prepared)['slot_generation']==old['slot_generation']


def test_new_store_reuses_valid_decision_and_keeps_old_invalidation(prepared, monkeypatch):
    from test_workflow_completion_0428 import SQLiteBridge
    from modules.flow_gate.db import connection
    invalidation.observe_basis(TS,'A')
    old=decide(prepared)
    store=connection.FlowGateStore()
    bridge=SQLiteBridge(prepared[0]._db.path)
    store._db=bridge
    store._sq=None
    with monkeypatch.context() as patcher:
        patcher.setattr(connection,'STORE',store)
        assert decide(prepared)['decision_id']==old['decision_id']
        invalidation.observe_basis(TS,'B')
        invalidation.observe_basis(TS,'A')
        assert_invalid(prepared,old)
    for con in bridge.connections:
        con.close()
    assert_invalid(prepared,old)



def test_invalid_ts_pair_is_refused_before_measuring_or_observing_other_evidence(prepared, monkeypatch):
    from modules.flow_gate.services import test_basis_service as basis
    from unittest.mock import MagicMock
    prepared[0]._execute("UPDATE documents SET type_code = 'R' WHERE doc_id = ?", [TS])
    measurement=MagicMock(side_effect=AssertionError('invalid pair measured'))
    monkeypatch.setattr(basis,'live_state',measurement)
    with pytest.raises(Exception, match='completion_test_basis_conflict'):
        invoke(prepared)
    measurement.assert_not_called()
    assert prepared[0]._fetch_one('SELECT COUNT(*) AS n FROM workflow_completion_basis',[])['n']==0



@pytest.mark.parametrize('protected',[False,True])
def test_pending_slot_deletion_cancels_legacy_uninvalidated_intent(db, protected):
    old=decide(db)
    # A pre-T#3 clear changed only result_doc_id. Reconcile that old row at deletion.
    db[0]._execute('UPDATE workflow_sequence_items SET result_doc_id = NULL WHERE id = ?', [db[2]])
    later=next(r for r in sequences.get_sequence_items(db[1]['id']) if r['type']=='TR2')
    if protected:
        sequences.delete_unprotected_pending_items(db[1]['id'], [later['id']])
        assert sequences.get_item_by_id(later['id']) is not None
    else:
        sequences.delete_pending_items(db[1]['id'])
    assert sequences.get_item_by_id(db[2]) is None
    saved=decisions.get_decision(old['decision_id'])
    assert saved['invalidated_at'] and saved['invalidation_reason']=='slot_deleted'
    assert decisions.get_dispatch(old['decision_id'],'chain-1','stop-1')['state']=='cancelled'
    sequences.insert_sequence_item(db[1]['id'], 3, 'TSR', 'new slot', 'R', 3)
    slot=next(r for r in sequences.get_sequence_items(db[1]['id']) if r['item_seq']==3)
    sequences.set_item_result_doc_id(slot['id'], RESULT)
    assert slot['id']!=old['slot_id']
    assert not slot_completed(next(r for r in sequences.get_sequence_items(db[1]['id']) if r['id']==slot['id']))
