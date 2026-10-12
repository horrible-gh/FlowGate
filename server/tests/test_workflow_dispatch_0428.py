"""0428 T#4: migrated decision/dispatch/stop SQL and existing resume/advance paths."""
from __future__ import annotations

import json
import os
for key, value in {"CONTEXT":"/flowgate", "ALLOWED_ORIGIN":"", "DB_TYPE":"sqlite"}.items():
    os.environ.setdefault(key, value)
from unittest.mock import MagicMock
import pytest
from fastapi import HTTPException

from test_workflow_completion_0428 import completion_database, db, ROOT, RESULT, GROUP
from test_workflow_proceed_0428 import prepared, client, post, invoke, TS
from modules.flow_gate.db import documents, workflow_sequences as seqdb, test_runs
from modules.flow_gate.db import workflow_completion as decisions, ai_invoke_paused_chains as paused
from modules.flow_gate.services import workflow_completion_dispatch_service as dispatch
from modules.flow_gate.services import workflow_proceed_service as command
from modules.flow_gate.services import test_run_service, test_basis_service, ai_invoke_service as svc
from modules.flow_gate.services import workflow_decision_service as manual
from modules.flow_gate.services.ai_invoke import chain, review, runtime


@pytest.fixture
def chain_stop(prepared, monkeypatch):
    for user_id in ('chain-owner','other-owner'):
        prepared[0]._execute("INSERT INTO users(user_id,username,email,password,is_admin,is_active,created_at,updated_at) VALUES (?, ?, ?, 'hash',0,1,datetime('now'),datetime('now'))",[user_id,user_id,user_id+'@test'])
    for provider in ('provider-1','actual-author'):
        prepared[0]._execute("INSERT INTO ai_providers(provider_id,project_id,name,exec_type,kind,created_at,updated_at) VALUES (?, 'flowgate', ?, 'cli', 'claude',datetime('now'),datetime('now'))",[provider,provider])
    token = {'ai_run_id':'stop-0428' ,'issued_to':'chain-owner','continuation_target_seq':2,
             'continuation_locale':'en'}
    monkeypatch.setattr(test_run_service, '_continuation_token_for_doc', lambda *a,**k:dict(token))
    monkeypatch.setattr(test_basis_service, 'live_state', lambda *a,**k:{'state':'valid','live':{'basis_id':'basis-A'}})
    monkeypatch.setattr(test_run_service, '_chain_api_base', lambda *a:'http://original/flowgate/api/v1')
    monkeypatch.setattr(runtime, 'get_run_record', lambda rid:{'chain_id':'chain-0428','test_gate_doc_id':TS} if rid=='stop-0428' else None)
    monkeypatch.setattr(chain.ai_settings_service, 'resolve_effective', lambda *a:{'providers':[{'id':'provider-1','name':'one'}]})
    monkeypatch.setattr(review, '_resumable_reviewer_overrides', lambda project,m:m)
    paused.upsert(group_id=GROUP, doc_ref=ROOT, paused_by='chain-owner', paused_at='2026-10-12T00:00:00Z',
        continuation_target_seq=2, docs_target=4, docs_reached=2,
        chain_id='chain-0428',chain_docs_target=8,chain_docs_reached=5,
        stop_kind='system',stop_code='test_gate_blocked',stop_run_id='stop-0428',
        stop_last_message_excerpt='original FAIL',continuation_base_provider_id='provider-1',
        continuation_provider_pinned=True,continuation_provider_overrides={'2':'provider-2'},
        continuation_work_executor_provider_id='actual-author',
        continuation_default_note='carry note',continuation_note_overrides={'2':'step note'},
        continuation_instruction_mode='ai_direct',continuation_auto_approve_item_seqs=[],
        continuation_step_timeout_sec=123,continuation_restart_max_attempts=4,
        continuation_review_count_overrides={'2':2},continuation_reviewer_overrides={'2':'reviewer-1'})
    monkeypatch.setattr(manual.token_service,'issue',lambda **k:{'raw_token':'raw','token_id':'next-token','scratch_dir':'fixture','expires_at':'2099'})
    monkeypatch.setattr(manual.mention_service,'build_mention_from_token_rec',lambda **k:'next')
    launched=[]
    def start(**kwargs):
        issued=kwargs['issue_builder']('next-run')
        launched.append((kwargs,issued))
        return {'ok':True,'status':'running','run_id':'next-run'}
    monkeypatch.setattr(svc,'start_run',start)
    return {'row':paused.get_by_group(GROUP),'token':token,'launched':launched}


@pytest.mark.parametrize('stop_code',['test_gate_blocked','test_run_pending'])
def test_http_exact_stop_advances_once_preserves_owner_settings(client,prepared,chain_stop,stop_code):
    store=prepared[0]
    store._execute('UPDATE ai_invoke_paused_chains SET stop_code=? WHERE group_id=?',[stop_code,GROUP])
    before=documents.get_by_id(RESULT)
    out=post(client,prepared).json()
    assert out['slot_completed'] and out['continuation_status']=='started',out
    assert out['dispatch_state']=='completed' and out['run_id']=='next-run'
    assert out['actor_user_id']=='human-1'
    assert paused.get_by_group(GROUP) is None
    assert documents.get_by_id(RESULT)==before
    assert len(chain_stop['launched'])==1
    kw,issue=chain_stop['launched'][0]
    assert issue['worker_document_type']=='TR2'
    assert kw['issued_to']=='chain-owner' and kw['chain_id']=='chain-0428'
    assert kw['continuation_target_seq']==2 and kw['continuation_to_end'] is False
    assert kw['api_base_url']=='http://original/flowgate/api/v1' and kw['continuation_locale']=='en'
    for key,value in [('provider_id','provider-1'),('provider_pinned',True),
        ('continuation_provider_overrides',{'2':'provider-2'}),('continuation_default_note','carry note'),
        ('continuation_note_overrides',{'2':'step note'}),('continuation_instruction_mode','ai_direct'),
        ('continuation_auto_approve_item_seqs',[]),('continuation_step_timeout_sec',123),
        ('continuation_restart_max_attempts',4),('continuation_review_count_overrides',{'2':2}),
        ('continuation_reviewer_overrides',{'2':'reviewer-1'}),('chain_docs_target',8),('chain_docs_reached',5)]:
        assert kw[key]==value,(key,kw[key])
    again=post(client,prepared).json()
    assert again==out and len(chain_stop['launched'])==1
    assert review._pending_review_slot(ROOT) is None


@pytest.mark.parametrize('column,value',[
    ('stop_kind','user'),('stop_code','question_pending'),('stop_code','provider_unavailable'),
    ('stop_run_id','other-run'),('chain_id',None),('paused_by','other-owner'),
    ('doc_ref','flowgate.default.0428.0003-TR2')])
def test_manual_other_stop_or_unlinked_chain_never_consumed(client,prepared,chain_stop,column,value):
    prepared[0]._execute('UPDATE ai_invoke_paused_chains SET '+column+'=? WHERE group_id=?',[value,GROUP])
    row=paused.get_by_group(GROUP)
    out=post(client,prepared).json()
    assert out['slot_completed'] and out['continuation_status']=='not_started',out
    assert paused.get_by_group(GROUP)==row and chain_stop['launched']==[]
    assert decisions.dispatch_for_decision(out['decision_id']) is None


def test_mismatched_live_origin_chain_is_not_an_intent(client,prepared,chain_stop,monkeypatch):
    monkeypatch.setattr(runtime,'get_run_record',lambda rid:{'chain_id':'other-chain','test_gate_doc_id':TS})
    row=paused.get_by_group(GROUP)
    out=post(client,prepared).json()
    assert out['continuation_status']=='not_started' and paused.get_by_group(GROUP)==row


@pytest.mark.parametrize('to_end,empty',[ (False,False),(True,True),(False,True)])
def test_no_remaining_slot_ends_once_no_worker(client,prepared,chain_stop,to_end,empty):
    if empty:
        prepared[0]._execute('DELETE FROM workflow_sequence_items WHERE item_seq=2',[])
    prepared[0]._execute('UPDATE ai_invoke_paused_chains SET continuation_target_seq=? WHERE group_id=?',[None if to_end else 1,GROUP])
    out=post(client,prepared).json()
    assert out['slot_completed'] and out['continuation_status']=='ended' and out['run_id'] is None,out
    assert paused.get_by_group(GROUP) is None and chain_stop['launched']==[]
    assert post(client,prepared).json()==out
    events=prepared[0]._fetch_all("SELECT * FROM workflow_events WHERE event_type='continuous_work_ended'",[])
    assert len(events)==1 and events[0]['actor_user_id']=='chain-owner'


def test_run_to_end_keeps_dynamic_target(client,prepared,chain_stop):
    prepared[0]._execute('UPDATE ai_invoke_paused_chains SET continuation_target_seq=NULL WHERE group_id=?',[GROUP])
    out=post(client,prepared).json()
    assert out['continuation_status']=='started',out
    kw=chain_stop['launched'][0][0]
    assert kw['continuation_target_seq']==2 and kw['continuation_to_end'] is True


def test_known_advance_failure_keeps_decision_restores_whole_stop_then_retries(client,prepared,chain_stop,monkeypatch):
    def fail(**kw):
        raise ValueError('fixture cannot advance')
    real_start=svc.start_run
    monkeypatch.setattr(svc,'start_run',fail)
    row=paused.get_by_group(GROUP)
    out=post(client,prepared).json()
    assert out['slot_completed'] and out['continuation_status']=='retryable_failed',out
    restored=paused.get_by_group(GROUP)
    # upsert bookkeeping timestamp changes, selections and stop identity remain.
    assert dispatch.same_stop(restored,row)
    monkeypatch.setattr(svc,'start_run',real_start)
    # A saved full snapshot must track the restored row's timestamp for retry.
    replay=post(client,prepared).json()
    assert replay['decision_id']==out['decision_id']
    assert replay['continuation_status']=='started',replay


def test_unknown_start_error_is_recovery_not_success_or_second_worker(client,prepared,chain_stop,monkeypatch):
    calls=[]
    def uncertain(**kw):
        calls.append(kw)
        raise RuntimeError('launch response lost')
    monkeypatch.setattr(svc,'start_run',uncertain)
    out=post(client,prepared).json()
    assert out['slot_completed'] and out['continuation_status']=='recovery_required',out
    assert post(client,prepared).json()==out and len(calls)==1


def test_missing_saved_stop_requires_recovery_without_worker(prepared,chain_stop,monkeypatch):
    consumer=dispatch.consume
    monkeypatch.setattr(dispatch,'consume',lambda ident:dispatch.projection(decisions.dispatch_by_id(ident)))
    out=invoke(prepared)
    saved=decisions.dispatch_for_decision(out['decision_id'])
    paused.delete_by_group(GROUP)
    result=consumer(saved['dispatch_id'])
    assert result['continuation_status']=='recovery_required' and result['run_id'] is None
    assert chain_stop['launched']==[]


@pytest.mark.parametrize('column,value',[('stop_code','question_pending'),('chain_id','other-chain'),('continuation_target_seq',99),('continuation_default_note','new choice')])
def test_full_snapshot_cas_preserves_same_owner_newer_stop(prepared,chain_stop,column,value):
    original=paused.get_by_group(GROUP)
    prepared[0]._execute('UPDATE ai_invoke_paused_chains SET '+column+'=? WHERE group_id=?',[value,GROUP])
    newer=paused.get_by_group(GROUP)
    result=paused.release_system_test_snapshot(original)
    assert isinstance(result,paused.ReleaseSuperseded) and paused.get_by_group(GROUP)==newer


def test_invalidated_dispatch_never_starts(prepared,chain_stop,monkeypatch):
    monkeypatch.setattr(dispatch,'consume',lambda ident:dispatch.projection(decisions.dispatch_by_id(ident)))
    out=invoke(prepared)
    d=decisions.dispatch_for_decision(out['decision_id'])
    decisions.invalidate_slot(prepared[1]['id'],prepared[2],expected_generation=0,reason='reopen')
    assert decisions.dispatch_by_id(d['dispatch_id'])['state']=='cancelled'
    assert dispatch.consume_pending()==[] and chain_stop['launched']==[]


def test_ordinary_resume_cannot_use_decision(client,prepared,chain_stop,monkeypatch):
    monkeypatch.setattr(dispatch,'consume',lambda ident:dispatch.projection(decisions.dispatch_by_id(ident)))
    out=post(client,prepared).json()
    assert out['continuation_status']=='pending'
    row=paused.get_by_group(GROUP)
    with pytest.raises(HTTPException) as exc:
        svc.resume_chain(group_id=GROUP,user_id='chain-owner',api_base_url='http://example')
    assert exc.value.detail['code']=='test_gate_decision_dispatch_required'
    assert paused.get_by_group(GROUP)==row and chain_stop['launched']==[]


def test_dispatch_preserves_real_failed_run_cases_and_basis(client,prepared,chain_stop):
    run=test_runs.insert_run(doc_id=TS,revision_no=7,triggered_via='ui',runner_id='chain-owner',
        contract_version=2,result_meta=json.dumps({'basis_id':'basis-A','test_basis':{'basis_id':'basis-A'}}),
        cases=[{'case_no':'C1','title':'failed case','cmd':'','expect':'ok'}])
    store=prepared[0]
    store._execute("UPDATE test_runs SET status='failed',overall='FAIL',error='original' WHERE run_id=?",[run['run_id']])
    store._execute("UPDATE test_run_cases SET case_status='FAIL',actual='broken',output_tail='evidence' WHERE run_id=?",[run['run_id']])
    before_run=test_runs.get_run(run['run_id'])
    before_cases=test_runs.list_cases(run['run_id'])
    before_doc=documents.get_by_id(RESULT)
    out=post(client,prepared).json()
    assert out['continuation_status']=='started',out
    assert test_runs.get_run(run['run_id'])==before_run
    assert test_runs.list_cases(run['run_id'])==before_cases
    assert documents.get_by_id(RESULT)==before_doc


def test_historical_instruction_selection_survives_actual_advance(client,prepared,chain_stop):
    store,seq,slot=prepared
    store._execute('UPDATE workflow_sequence_items SET item_seq=3 WHERE item_seq=2',[])
    store._execute('UPDATE workflow_sequence_items SET item_seq=2 WHERE id=?',[slot])
    instruction=GROUP+'.0007-T'
    store._execute("INSERT INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,status,revision_no,doc_review_status,created_at,updated_at) VALUES (?, 'flowgate','default',?,'T',7,'instruction','open',0,'approved',datetime('now'),datetime('now'))",[instruction,GROUP])
    seqdb.insert_sequence_item(seq['id'],1,'T','earlier instruction','I',-2)
    history=next(i for i in seqdb.get_sequence_items(seq['id']) if i['type']=='T')
    seqdb.set_item_result_doc_id(history['id'],instruction)
    store._execute("UPDATE ai_invoke_paused_chains SET continuation_target_seq=3,continuation_auto_approve_item_seqs='[1]' WHERE group_id=?",[GROUP])
    out=post(client,prepared).json()
    assert out['continuation_status']=='started',out
    kw=chain_stop['launched'][0][0]
    assert kw['continuation_target_seq']==3 and kw['continuation_auto_approve_item_seqs']==[1]
    assert chain_stop['launched'][0][1]['worker_document_type']=='TR2'


def test_launch_shares_source_lock_context(client,prepared,chain_stop,monkeypatch):
    start=svc.start_run
    observed=[]
    def reenter(**kwargs):
        outcome,ctx=command.lock_manager.acquire_group('flowgate',GROUP,holder_kind='nested_admission')
        observed.append(outcome.ok)
        assert outcome.ok
        command.lock_manager.release(ctx,outcome.lock_key)
        return start(**kwargs)
    monkeypatch.setattr(svc,'start_run',reenter)
    out=post(client,prepared).json()
    assert out['continuation_status']=='started' and observed==[True],out
    assert command.lock_manager.current_context() is None


def test_end_event_failure_rolls_back_stop_and_claim_then_retries(client,prepared,chain_stop,monkeypatch):
    from modules.flow_gate.workflow import event_logger
    logger=event_logger.log_continuous_work_ended
    monkeypatch.setattr(event_logger,'log_continuous_work_ended',MagicMock(side_effect=RuntimeError('event storage failed')))
    prepared[0]._execute('DELETE FROM workflow_sequence_items WHERE item_seq=2',[])
    before=paused.get_by_group(GROUP)
    out=post(client,prepared).json()
    assert out['slot_completed'] and out['continuation_status']=='retryable_failed',out
    assert paused.get_by_group(GROUP)==before and chain_stop['launched']==[]
    assert prepared[0]._fetch_one("SELECT COUNT(*) AS n FROM workflow_events WHERE event_type='continuous_work_ended'",[])['n']==0
    monkeypatch.setattr(event_logger,'log_continuous_work_ended',logger)
    again=post(client,prepared).json()
    assert again['decision_id']==out['decision_id'] and again['continuation_status']=='ended'


def test_basis_change_before_consume_cancels_intent_and_keeps_stop(prepared,chain_stop,monkeypatch):
    consumer=dispatch.consume
    monkeypatch.setattr(dispatch,'consume',lambda ident:dispatch.projection(decisions.dispatch_by_id(ident)))
    out=invoke(prepared)
    before=paused.get_by_group(GROUP)
    monkeypatch.setattr(test_basis_service,'live_state',lambda *a,**k:{'state':'valid','live':{'basis_id':'basis-B'}})
    result=consumer(out['dispatch_id'])
    assert result['continuation_status']=='cancelled' and result['run_id'] is None
    assert paused.get_by_group(GROUP)==before and chain_stop['launched']==[]
    assert decisions.get_decision(out['decision_id'])['invalidated_at']
