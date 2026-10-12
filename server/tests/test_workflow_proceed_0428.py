"""0428 T#2: independent HTTP command, real decision/audit SQL and safety gates."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from test_workflow_completion_0428 import completion_database, db, ROOT, RESULT, GROUP
from modules.flow_gate.db import documents, test_runs, workflow_sequences as seqdb
from modules.flow_gate.db import workflow_completion as decisions
from modules.flow_gate.services import workflow_proceed_service as command
from modules.flow_gate.services.mutation_policy import human_principal, system_principal, worker_principal
from modules.flow_gate.workflow.routers import workflow as routes
from modules.flow_gate.auth.middleware import get_current_user

TS = GROUP + '.0006-TS'


@pytest.fixture
def prepared(db):
    store, seq, slot = db
    store._execute("INSERT INTO users(user_id,username,email,password,is_admin,is_active,created_at,updated_at) VALUES ('human-1','reviewer0428','0428@test','hash',1,1,datetime('now'),datetime('now'))", [])
    store._execute("INSERT INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,status,revision_no,doc_review_status,created_at,updated_at) VALUES (?, 'flowgate','default',?,'TS',6,'basis','open',7,'approved',datetime('now'),datetime('now'))", [TS, GROUP])
    store._execute("UPDATE documents SET target_id = ? WHERE doc_id = ?", [TS, RESULT])
    seqdb.insert_sequence_item(seq['id'], 0, 'TS', 'basis', 'R', -1)
    ts_slot = next(i for i in seqdb.get_sequence_items(seq['id']) if i['type'] == 'TS')
    seqdb.set_item_result_doc_id(ts_slot['id'], TS)
    return db


@pytest.fixture
def client(prepared):
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: {'user_id':'human-1','is_admin':True}
    with TestClient(app) as c:
        yield c


def post(client, prepared, **over):
    body = dict(expected_revision=7, expected_slot_id=prepared[2], expected_slot_generation=seqdb.get_item_by_id(prepared[2])['slot_generation'],
                request_key='confirmed-1')
    body.update(over)
    return client.post('/api/v1/documents/'+RESULT+'/proceed-unapproved', json=body)


def counts(db):
    return tuple(db[0]._fetch_one('SELECT COUNT(*) AS n FROM '+t, [])['n'] for t in (
        'workflow_completion_decisions','workflow_completion_requests',
        'workflow_completion_dispatches','workflow_events','tr_commit_ledger','tr2_approval_attempts'))


def invoke(prepared, **over):
    kwargs = dict(doc_id=RESULT,expected_revision=7,expected_slot_id=prepared[2],
                  expected_slot_generation=seqdb.get_item_by_id(prepared[2])['slot_generation'],request_key='confirmed-1',actor_user_id='human-1',
                  user_permissions={'document.approve'},principal=human_principal({'user_id':'human-1'}))
    kwargs.update(over)
    return command.proceed_unapproved(**kwargs)


@pytest.mark.parametrize('overall', ['FAIL','BLOCKED','NOT_RUN'])
def test_tsr_keeps_actual_result_basis_cases_and_review(client, prepared, monkeypatch, overall):
    from modules.flow_gate.services import test_basis_service, test_run_service, workflow_decision_service as manual
    store, seq, slot = prepared
    meta = {'run_kind':'spec_results','basis_id':'basis-A','test_basis':{'basis_id':'basis-A','source':'original'}}
    run = test_runs.insert_run(doc_id=TS,revision_no=7,triggered_via='ui',runner_id='human-1',
                              contract_version=2,result_meta=json.dumps(meta),
                              cases=[{'case_no':'C1','title':'fails','cmd':'','expect':'ok'}])
    store._execute("UPDATE test_runs SET status='failed',overall=?,error='original-failure' WHERE run_id=?", [overall,run['run_id']])
    store._execute("UPDATE test_run_cases SET case_status=?,actual='broken',output_tail='original-evidence' WHERE run_id=?", [overall,run['run_id']])
    monkeypatch.setattr(test_basis_service,'live_state',lambda *a,**kw: {
        'live':{'basis_id':'basis-A'},'state':test_basis_service.VALID,'no_worktree':False,'reasons':[]})
    before_doc = documents.get_by_id(RESULT)
    before_run = test_runs.get_run(run['run_id'])
    before_cases = test_runs.list_cases(run['run_id'])
    before_gate = test_run_service.tsr_gate_state(before_doc)
    assert before_gate['passed'] is False and before_gate['overall'] == overall
    for seam in [(command.pipeline_service,'transition_document_review'),(manual,'start_run')]:
        if hasattr(seam[0],seam[1]): monkeypatch.setattr(*seam,MagicMock(side_effect=AssertionError('approval/start invoked')))
    with pytest.raises(ValueError,match='head_in_progress'):
        manual.advance_workflow(ROOT,'human-1','http://example')
    response = post(client,prepared)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out['slot_completed'] and out['completion_reason']=='proceeded_unapproved'
    assert out['doc_review_status']=='rejected' and out['actor_user_id']=='human-1'
    assert out['continuation_status']=='not_started' and out['run_id'] is None
    assert documents.get_by_id(RESULT)==before_doc
    assert test_runs.get_run(run['run_id'])==before_run
    assert test_runs.list_cases(run['run_id'])==before_cases
    assert test_run_service.tsr_gate_state(documents.get_by_id(RESULT))==before_gate
    monkeypatch.setattr(manual.token_service,'issue',lambda **kw: {'raw_token':'fixture','token_id':'fixture','scratch_dir':'fixture','expires_at':'2099-01-01'})
    monkeypatch.setattr(manual.mention_service,'build_mention_from_token_rec',lambda **kw:'next')
    advanced=manual.advance_workflow(ROOT,'human-1','http://example')
    assert advanced['head_item_seq']==2 and advanced['worker_document_type']=='TR2'
    assert counts(prepared)==(1,1,0,1,0,0)


def test_replay_and_different_key_keep_actor_time_and_one_audit(client,prepared):
    first=post(client,prepared)
    again=post(client,prepared)
    other=post(client,prepared,request_key='confirmed-2')
    assert first.status_code==again.status_code==other.status_code==200
    assert first.json()==again.json()==other.json()
    assert counts(prepared)==(1,2,0,1,0,0)
    event=prepared[0]._fetch_one("SELECT * FROM workflow_events WHERE event_type='workflow_proceeded_unapproved'",[])
    assert json.loads(event['metadata'])['decision_id']==first.json()['decision_id']


@pytest.mark.parametrize('field,value', [('expected_revision',6),('expected_slot_id',999),('expected_slot_generation',1)])
def test_stale_identity_refused_without_writes(client,prepared,field,value):
    before=counts(prepared)
    r=post(client,prepared,**{field:value})
    assert r.status_code==409 and counts(prepared)==before


@pytest.mark.parametrize('body', [
    {}, {'expected_revision':True}, {'expected_revision':'7'}, {'expected_slot_generation':-1},
    {'expected_slot_id':0}, {'request_key':''}, {'request_key':' '}, {'request_key':'x'*129},
    {'reason':'skip'}, {'confirmed':True}, {'actor_user_id':'forged'},
])
def test_request_contract_rejects_coercion_extra_fields_and_empty_key(client,prepared,body):
    before=counts(prepared)
    r=post(client,prepared,**body) if body else client.post('/api/v1/documents/'+RESULT+'/proceed-unapproved',json={})
    assert r.status_code==422 and counts(prepared)==before


@pytest.mark.parametrize('kind',['D','AC','TS','WP','TR'])
def test_only_tsr_tr2_are_supported(client,prepared,kind):
    prepared[0]._execute('UPDATE documents SET type_code=? WHERE doc_id=?',[kind,RESULT])
    assert post(client,prepared).status_code==422
    assert counts(prepared)==(0,0,0,0,0,0)


@pytest.mark.parametrize('review',['approved','wf_done','wf_in_progress'])
def test_approved_or_invalid_review_creates_no_decision(client,prepared,review):
    prepared[0]._execute('UPDATE documents SET doc_review_status=? WHERE doc_id=?',[review,RESULT])
    assert post(client,prepared).status_code==409
    assert counts(prepared)==(0,0,0,0,0,0)


def test_permission_and_machine_principals_refused(client,prepared):
    client.app.dependency_overrides[get_current_user]=lambda:{'user_id':'human-1','is_admin':False}
    assert post(client,prepared).status_code==403
    for principal in [system_principal(user_id='human-1'),worker_principal({'issued_to':'human-1'}),None,human_principal({'user_id':'other'})]:
        with pytest.raises(command.MutationPolicyError) as exc:
            invoke(prepared,principal=principal)
        assert exc.value.status_code==403
    assert counts(prepared)==(0,0,0,0,0,0)


def test_non_head_and_unreadable_head_fail_closed(client,prepared,monkeypatch):
    prepared[0]._execute("UPDATE documents SET doc_review_status='rejected' WHERE doc_id=?",[TS])
    r=post(client,prepared)
    assert r.status_code==409 and r.json()['error']['code']=='workflow_not_head'
    monkeypatch.setattr(seqdb,'get_effective_head',MagicMock(side_effect=RuntimeError('head read failed')))
    r=post(client,prepared)
    assert r.status_code==409 and r.json()['error']['code']=='workflow_head_unavailable'
    assert counts(prepared)==(0,0,0,0,0,0)


@pytest.mark.parametrize('seam,value,code',[
    ('disposed',True,'group_disposed'),('active_ai',{'run_id':'live'},'completion_ai_run_active'),
    ('active_test',{'run_id':'running'},'completion_test_run_active'),
    ('history',True,'tr_history_recovery_required'),
    ('attempt',{'state':'in_progress'},'tr2_in_progress'),
    ('attempt',{'state':'recovery_required'},'tr2_recovery_required'),
    ('revert',[RESULT],'workflow_revert_pending'),
    ('admission',{'error':'failure_origin_pending','run_id':'x'},'completion_test_not_settled'),
])
def test_safety_gates_do_not_write(client,prepared,monkeypatch,seam,value,code):
    targets={'disposed':(command.process_service,'is_group_disposed'),
             'active_ai':(command.runtime,'active_run_for_group'),
             'active_test':(test_runs,'get_active_by_group'),
             'history':(command.db_recovery,'has_unresolved'),
             'attempt':(command.db_attempts,'blocking_by_group'),
             'revert':(command.tr2_service,'group_revert_pending'),
             'admission':(command.spec_execution_service,'admission_blocker')}
    monkeypatch.setattr(*targets[seam],lambda *a,**kw:value)
    r=post(client,prepared)
    assert r.status_code==409 and r.json()['error']['code']==code,r.text
    assert counts(prepared)==(0,0,0,0,0,0)


@pytest.mark.parametrize('status',['running','cancelling'])
def test_real_active_test_rows_block(client,prepared,status):
    r=test_runs.insert_run(doc_id=TS,revision_no=7,triggered_via='ui',runner_id='human-1',cases=[])
    prepared[0]._execute('UPDATE test_runs SET status=? WHERE run_id=?',[status,r['run_id']])
    assert post(client,prepared).json()['error']['code']=='completion_test_run_active'
    assert counts(prepared)==(0,0,0,0,0,0)


def test_wrong_ts_pair_and_orphan_slot_block(client,prepared):
    prepared[0]._execute('UPDATE documents SET target_id=? WHERE doc_id=?',[ROOT,RESULT])
    assert post(client,prepared).json()['error']['code']=='completion_test_basis_conflict'
    seqdb.set_item_result_doc_id(prepared[2],None)
    assert post(client,prepared).json()['error']['code']=='completion_slot_missing'


def test_request_key_cannot_cross_validity_keys(client,prepared):
    first=post(client,prepared)
    assert first.status_code==200
    assert decisions.invalidate_slot(prepared[1]['id'],prepared[2],expected_generation=0,reason='reopen')
    before=counts(prepared)
    r=post(client,prepared,expected_slot_generation=1)
    assert r.status_code==409 and r.json()['error']['code']=='completion_request_key_conflict'
    assert counts(prepared)==before
    assert post(client,prepared,expected_slot_generation=1,request_key='new-generation').status_code==200


def test_audit_failure_rolls_back_decision_request_and_slot(client,prepared,monkeypatch):
    monkeypatch.setattr(command,'log_event',MagicMock(side_effect=RuntimeError('audit write failed')))
    with pytest.raises(RuntimeError,match='audit write failed'):
        post(client,prepared)
    assert counts(prepared)==(0,0,0,0,0,0)
    assert seqdb.get_item_by_id(prepared[2])['slot_decision_id'] is None


def test_concurrent_clicks_share_one_decision_audit(prepared):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda key:invoke(prepared,request_key=key),['click-a','click-b']))
    assert results[0]==results[1]
    assert counts(prepared)==(1,2,0,1,0,0)


def test_g_lock_contention_and_lost_lock_are_refused(client,prepared,monkeypatch):
    from group_lock_stub import hold_group_lock
    with hold_group_lock('flowgate',GROUP):
        r=post(client,prepared)
        assert r.status_code==409 and r.json()['error']['code']=='completion_source_locked'
    monkeypatch.setattr(command.lock_manager,'still_held',lambda *a:False)
    assert post(client,prepared).json()['error']['code']=='completion_source_locked'
    assert counts(prepared)==(0,0,0,0,0,0)


def test_authenticated_human_surface_and_independent_action(client,prepared):
    plain=FastAPI()
    plain.include_router(routes.router)
    with TestClient(plain) as unauth:
        r=unauth.post('/api/v1/documents/'+RESULT+'/proceed-unapproved',json={
            'expected_revision':7,'expected_slot_id':prepared[2],
            'expected_slot_generation':0,'request_key':'unauth'})
        assert r.status_code==401
    r=client.post('/api/v1/documents/'+RESULT+'/review_transitions/proceed_unapproved',json={})
    assert r.status_code==409
    assert counts(prepared)==(0,0,0,0,0,0)


def test_real_durable_group_lease_blocks_even_admin(client,prepared,monkeypatch):
    from modules.flow_gate.db import group_ai_leases as leases
    monkeypatch.setattr(leases,'_using_memory',lambda:False)
    held=leases.acquire(group_id=GROUP,project_id='flowgate',run_id='live-lease',
                        chain_id='chain-lease',action_scope='new',worker_identity='worker')
    assert held['run_id']=='live-lease'
    r=post(client,prepared)
    assert r.status_code==423 and r.json()['error']['code']=='GROUP_AI_RUN_LOCKED'
    assert counts(prepared)==(0,0,0,0,0,0)
    assert leases.get(GROUP)['run_id']=='live-lease'


def test_identity_changed_while_waiting_for_group_lock_is_rechecked(client,prepared,monkeypatch):
    from contextlib import contextmanager
    actual=command._group_lock
    @contextmanager
    def changing(doc):
        with actual(doc) as locked:
            prepared[0]._execute('UPDATE documents SET revision_no=8 WHERE doc_id=?',[RESULT])
            yield locked
    monkeypatch.setattr(command,'_group_lock',changing)
    r=post(client,prepared)
    assert r.status_code==409 and r.json()['error']['code']=='completion_revision_changed'
    assert counts(prepared)==(0,0,0,0,0,0)


@pytest.mark.parametrize('kind,code',[('user',None),('system','test_gate_blocked'),
                                     ('system','test_run_pending'),('system','question_pending'),
                                     ('system','hop_handoff_failed')])
def test_decision_keeps_all_paused_rows_and_starts_no_worker(client,prepared,monkeypatch,kind,code):
    from modules.flow_gate.db import ai_invoke_paused_chains as paused
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.services.ai_invoke import chain
    paused.upsert(group_id=GROUP,doc_ref=ROOT,paused_by='human-1',paused_at='2026-10-12T01:00:00+09:00',
                  continuation_target_seq=2,docs_target=1,docs_reached=0,chain_id='chain-A',
                  stop_kind=kind,stop_code=code,stop_run_id='stopped-A')
    before=paused.get_by_group(GROUP)
    start=MagicMock(side_effect=AssertionError('worker started'))
    resume=MagicMock(side_effect=AssertionError('resume called by command'))
    monkeypatch.setattr(ai_invoke_service,'start_run',start)
    original_resume=ai_invoke_service.resume_chain
    monkeypatch.setattr(ai_invoke_service,'resume_chain',resume)
    response=post(client,prepared)
    assert response.status_code==200,response.text
    assert paused.get_by_group(GROUP)==before
    start.assert_not_called();resume.assert_not_called()
    admission=chain._paused_row_resume_state('flowgate',before,include_target=True)
    if kind=='system' and code in {'test_gate_blocked','test_run_pending'}:
        assert admission['resume_block_code']=='test_gate_decision_dispatch_required'
        monkeypatch.setattr(ai_invoke_service,'resume_chain',original_resume)
        with pytest.raises(Exception) as exc:
            chain.resume_chain(group_id=GROUP,user_id='human-1',api_base_url='http://example',is_admin=True)
        assert exc.value.status_code==422
        assert exc.value.detail['code']=='test_gate_decision_dispatch_required'
        assert paused.get_by_group(GROUP)==before
        start.assert_not_called()
        prepared[0]._execute("UPDATE documents SET doc_review_status='approved' WHERE doc_id=?",[RESULT])
        ordinary=chain._paused_row_resume_state('flowgate',before,include_target=True)
        assert ordinary['resume_block_code']!='test_gate_decision_dispatch_required'
    else:
        assert admission['resume_block_code']!='test_gate_decision_dispatch_required'
