"""0145: isolated legacy attribution and explicit re-review through the real Graph ledger.

SQLite fixture setup models the old new-token submission. External AI admission
is a recorder; fresh review rows and run-end contacts exercise normal completion.
"""
import json
import pytest
from fastapi import HTTPException

from test_fg_ai_roles_l2_assignment import (
    FA, X, B, _sql, db_path, env, decided, wired, starts, flow, roles, start, one,
    node_run, line_doc, stored, _write_written,
)
from test_fg_revision_adoption_precheck_0010 import document_write_ports
from test_fg_command_review_0125 import completions, fork_join, _pass
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import run_store, definition_store
from modules.flow_gate.services import graph_execution_hooks
from flowgate.card_settings import result_recovery, legacy_adapter as legacy, stage_assignment as sa
from flowgate.graph_run import execution, execution_store
from modules.flow_gate.services.graph_source_hooks import GraphAttemptInactive

pytestmark = pytest.mark.usefixtures('fork_join', 'completions')
ORIGIN = 'aiv_20261103_003467'


@pytest.fixture
def orphan(env, decided, monkeypatch, starts):
    from modules.flow_gate.db import ai_invoke_runs
    key = flow(env, extra_settings=(('instruction', 'omit'), ('review_count', 1)))
    roles(key, execution=X, review=B)
    run = start(env)
    writer = one(env, key, 'result')
    doc_id = line_doc(env, key, 'report')
    from modules.flow_gate.storage.paths import get_storage_root
    path = get_storage_root('flowgate', create=True) / 'docs' / f'{doc_id}.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('---\ntitle: Legacy\n---\n## Legacy result\n\nOriginal body.\n', encoding='utf-8')
    _sql(env['db'], 'UPDATE documents SET file_path=? WHERE doc_id=?', (f'docs/{doc_id}.md',doc_id))
    # Non-secret historical fixture: one real dispatch token, linked to its ended writer.
    token = env['jobs'][0]['token']['token_id']
    _sql(env['db'], "UPDATE tokens SET action_scope='new', ai_run_id=?, provider_id=?, created_at=?, consumed_at=? WHERE token_id=?",
         (ORIGIN, X, '2026-11-03T10:40:25Z', '2026-11-03T10:46:09Z', token))
    _sql(env['db'], "UPDATE graph_run_token_map SET stage='result' WHERE token_id=?", (token,))
    _sql(env['db'], "UPDATE documents SET revision_no=0, origin_ai_run_id=?, created_at=?, doc_review_status='pending_review' WHERE doc_id=?",
         (ORIGIN, '2026-11-03T10:46:09Z', doc_id))
    _sql(env['db'], "UPDATE graph_attempt_runs SET ai_run_id=?, status='ended', created_at=?, ended_at=? WHERE exec_id=?",
         (ORIGIN, '2026-11-03T10:40:16Z', '2026-11-03T10:46:20Z', writer['intent_exec_id']))
    _sql(env['db'], "UPDATE graph_node_runs SET status='review', result_doc_id=? WHERE node_key=?", (doc_id,key))
    _sql(env['db'], "UPDATE graph_runs SET status='idle', idle_reason='needs_intervention' WHERE run_id=?", (run['run_id'],))
    for event in ('doc_created', 'token_consumed'):
        meta = json.dumps({'token_id':token,'doc_id':doc_id,'revision_no':0}, separators=(',',':'))
        _sql(env['db'], "INSERT INTO workflow_events(event_type,project_id,group_id,document_id,actor_user_id,metadata,created_at) VALUES (?,'flowgate',?,?, 'u_kim',?,?)",
             (event,FA,doc_id,meta,'2026-11-03T10:46:09Z'))
    ai_invoke_runs.upsert({'run_id':ORIGIN,'group_id':'flowgate.v02.0092','project_id':'flowgate',
                           'doc_ref':'flowgate.v02.0126.0015-TR','mode':'single','outcome':'complete',
                           'started_at':'2026-11-04T00:19:18+09:00','finished_at':'2026-11-04T00:20:13+09:00',
                           'created_at':'2026-11-03T19:46:20+09:00','updated_at':'2026-11-04T00:20:13+09:00'})
    return env, key, doc_id, writer, token


def request(orphan, explicit=True):
    env,key,doc_id,writer,token = orphan
    loop={'reviewer_provider_id':B,'review_count':1,'review_criteria':'document_type_default',
          'rework_provider_id':X,'rework_timeout_sec':3600,'failure_restart_max_attempts':0,'total_timeout_sec':14400}
    def run():
        return execution.request_loop('flowgate',FA,doc_id,'u_kim',loop,'basis',key)
    if explicit:
        with graph_execution_hooks.explicit_request():
            return run()
    return run()


def frozen(env):
    return {t:_sql(env['db'],'SELECT * FROM '+t) for t in (
        'ai_invoke_runs','documents','document_reviews','graph_node_attempts','graph_stage_assignments','graph_attempt_runs')}


def test_original_attribution_ignores_foreign_run_and_explicit_review_completes_normally(orphan, monkeypatch):
    env,key,doc_id,writer,token=orphan
    foreign=_sql(env['db'],'SELECT * FROM ai_invoke_runs')
    assert legacy.result_ai_run(FA,writer['run_id'],key,doc_id) is None
    assert sa.current_review_assignment(writer['attempt_id'],sa.RESULT_REVIEW) is None
    before=frozen(env)
    # Reads and reconciliation cannot fabricate the missing author outcome or review.
    from flowgate.card_settings import review_stage
    review_stage.reconcile(FA)
    assert frozen(env)==before
    # Confirm a newer definition while the old execution remains pinned and in review.
    from test_latest_definition_0145 import draft_body, put
    from test_fg_card_settings import node, edge, _h
    body=draft_body(definition_store.revision(FA,1))
    body['nodes'].append(node('tmp_after_recovery','T'))
    root=next(n['node_key'] for n in definition_store.revision(FA,1)['nodes'] if n.get('doc_type')=='R')
    body['edges'].append(edge('tmp_after_recovery_edge',root,'tmp_after_recovery'))
    assert put(env,body).status_code==200
    confirmed=env['client'].post(f'/api/v1/flows/{FA}/graph/revisions',
        json={'draft_version':1,'base_revision_no':1},headers=_h())
    assert confirmed.status_code==201,confirmed.text
    pre=env['client'].get(f'/api/v1/flows/{FA}/runs/revision-adoption',headers=_h()).json()
    # v02test.0018: the card in review does not refuse the newer revision; it stays pinned until a command adopts it.
    assert pre['adoptable'] is True and pre['target']=='revision'
    assert run_store.run_by_id(writer['run_id'])['revision_no']==1
    exec_id=request(orphan)
    rr=one(env,key,'result_review',1)
    assert rr['actor']=='ai' and rr['verdict'] is None and rr['intent_exec_id']==exec_id
    assert one(env,key,'result')['stage_state']=='done'
    assert run_store.active_attempt_id(writer['run_id'],key)==writer['attempt_id']
    ledger=_sql(env['db'],'SELECT * FROM graph_attempt_runs WHERE exec_id=?',(exec_id,))[0]
    proof=json.loads(ledger['request_json'])['recovery_provenance']
    assert proof=={'writer_exec_id':writer['intent_exec_id'],'writer_ai_run_id':ORIGIN,'token_id':token,'doc_id':doc_id,'revision_no':0}
    assert _sql(env['db'],'SELECT * FROM graph_card_completions')==[]
    _pass(env,key,1,doc_id,'air_fresh_0145_review')
    assert node_run(env,key)['status']=='done'
    completion=_sql(env['db'],'SELECT * FROM graph_card_completions')[0]
    assert completion['status']=='applied' and completion['cause']=='ai_review_passed'
    attempt=_sql(env['db'],'SELECT * FROM graph_node_attempts WHERE attempt_id=?',(writer['attempt_id'],))[0]
    assert attempt['status']=='ended'
    assert _sql(env['db'],'SELECT * FROM ai_invoke_runs')==foreign
    assert run_store.run_by_id(writer['run_id'])['revision_no']==1
    pre=env['client'].get(f'/api/v1/flows/{FA}/runs/revision-adoption',headers=_h()).json()
    assert pre['adoptable'] is True and pre['target']=='revision'
    response=env['client'].post(f'/api/v1/flows/{FA}/runs',json={'mode':'full'},headers=_h())
    assert response.status_code==201,response.text
    assert response.json()['run']['revision_no']==2
    assert node_run(env,key)['status']=='done'
    assert _sql(env['db'],'SELECT * FROM graph_card_completions WHERE completion_id=?',
                (completion['completion_id'],))[0]==completion
    assert _sql(env['db'],'SELECT * FROM ai_invoke_runs')==foreign


@pytest.mark.parametrize('damage', ['token_group','unconsumed','revoked','wrong_origin','new_revision',
    'wrong_execution','invalid_attempt','slot','existing_review','review_disabled','reviewer_mismatch','held','late_token','cancelled'])
def test_unverified_or_unreviewed_recovery_is_refused_and_rolls_back(orphan, damage):
    env,key,doc_id,writer,token=orphan
    if damage=='token_group': _sql(env['db'],"UPDATE tokens SET group_id='other' WHERE token_id=?",(token,))
    elif damage=='unconsumed': _sql(env['db'],'UPDATE tokens SET consumed_at=NULL WHERE token_id=?',(token,))
    elif damage=='revoked': _sql(env['db'],"UPDATE tokens SET revoked_at='2026-11-03T10:47:00Z' WHERE token_id=?",(token,))
    elif damage=='wrong_origin': _sql(env['db'],"UPDATE documents SET origin_ai_run_id='aiv_20261103_999999' WHERE doc_id=?",(doc_id,))
    elif damage=='new_revision': _sql(env['db'],'UPDATE documents SET revision_no=1 WHERE doc_id=?',(doc_id,))
    elif damage=='wrong_execution': _sql(env['db'],"UPDATE graph_attempt_runs SET ai_run_id='other' WHERE exec_id=?",(writer['intent_exec_id'],))
    elif damage=='invalid_attempt': _sql(env['db'],"UPDATE graph_node_attempts SET status='invalidated', active_node_key=NULL WHERE attempt_id=?",(writer['attempt_id'],))
    elif damage=='slot': _sql(env['db'],"UPDATE graph_node_documents SET doc_id=? WHERE node_key=? AND slot='report'",(FA+'.0001-R',key))
    elif damage=='existing_review':
        from modules.flow_gate.db import document_reviews
        document_reviews.insert_review(doc_id,0,B,'issues','[]',None,'t',review_run_id='old_review')
    elif damage=='review_disabled': roles(key,execution=X,review='not_used')
    elif damage=='reviewer_mismatch': roles(key,execution=X,review=X)
    elif damage=='held': stored(key,'hold',True)
    elif damage=='cancelled': _sql(env['db'],"UPDATE documents SET status='cancelled' WHERE doc_id=?",(doc_id,))
    else: _sql(env['db'],"UPDATE tokens SET consumed_at='2026-11-03T10:47:00Z' WHERE token_id=?",(token,))
    before=frozen(env)
    with pytest.raises((HTTPException, GraphAttemptInactive, execution_store.LeaseLost)):
        request(orphan)
    assert frozen(env)==before
    assert sa.current_review_assignment(writer['attempt_id'],sa.RESULT_REVIEW) is None
    assert _sql(env['db'],'SELECT * FROM graph_card_completions')==[]


@pytest.mark.parametrize('status',['idle','running'])
def test_automatic_request_cannot_recover_an_orphan(orphan,status):
    env,*_=orphan
    _sql(env['db'],'UPDATE graph_runs SET status=?',(status,))
    before=frozen(env)
    with pytest.raises((HTTPException, GraphAttemptInactive, execution_store.LeaseLost)):
        request(orphan,False)
    assert frozen(env)==before


def test_a_real_review_failure_leaves_attempt_active_and_has_no_completion(orphan):
    from test_fg_card_intervention_assigned_0119 import _loop_end, _loop_row
    env,key,doc_id,writer,token=orphan
    exec_id=request(orphan)
    _loop_row(doc_id,'air_0145_failed_review','review')
    _loop_end(env,exec_id,doc_id,'air_0145_failed_review','review_count_exhausted')
    assert node_run(env,key)['status']=='review'
    assert run_store.active_attempt_id(writer['run_id'],key)==writer['attempt_id']
    assert _sql(env['db'],'SELECT * FROM graph_card_completions')==[]
    assert one(env,key,'result_review',1)['verdict']=='not_passed:review_count_exhausted'


@pytest.fixture
def explicit_api(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.flow_gate.api.v1 import ai_invoke_routes
    monkeypatch.setattr(ai_invoke_routes,'verify_bearer',
        lambda request: {'_is_user_jwt':True,'issued_to':'u_kim','is_admin':True})
    calls=[]
    def admit(**kw):
        calls.append(kw)
        if getattr(admit,'refuse',False):
            raise HTTPException(409,detail={'code':'provider_unavailable'})
        return {'run_id':'air_http_0145','status':'running'}
    monkeypatch.setattr(ai_invoke_routes.ai_invoke_service,'start_run',admit)
    app=FastAPI();app.include_router(ai_invoke_routes.router)
    client=TestClient(app,raise_server_exceptions=False)
    def post(doc_id):
        return client.post('/api/v1/ai-invoke/start',headers={'Authorization':'Bearer user'},json={
            'project':'flowgate','module':'v02','group':FA.rsplit('.',1)[-1],
            'doc_ref':doc_id,'action_scope':'review','mode':'single','document_review_loop':{
                'review_count':1,'review_criteria':'document_type_default','reviewer_provider_id':B,
                'rework_provider_id':X,'rework_timeout_sec':3600,'failure_restart_max_attempts':0,'total_timeout_sec':14400}})
    return post,calls,admit


def test_existing_http_rereview_api_binds_the_verified_assignment_before_admission(orphan,explicit_api):
    env,key,doc_id,writer,token=orphan
    post,calls,_=explicit_api
    response=post(doc_id)
    assert response.status_code==200,response.text
    assert len(calls)==1 and calls[0]['graph_exec_id']
    rr=one(env,key,'result_review',1)
    assert rr['intent_exec_id']==calls[0]['graph_exec_id'] and rr['verdict'] is None
    assert _sql(env['db'],'SELECT * FROM graph_card_completions')==[]
    _pass(env,key,1,doc_id,'air_fresh_http_0145')
    assert node_run(env,key)['status']=='done'


def test_http_refuses_bad_attribution_before_ai_admission(orphan,explicit_api):
    env,key,doc_id,writer,token=orphan
    post,calls,_=explicit_api
    _sql(env['db'],'UPDATE tokens SET consumed_at=NULL WHERE token_id=?',(token,))
    response=post(doc_id)
    assert response.status_code==409,response.text
    assert calls==[] and one(env,key,'result_review',1) is None
    assert _sql(env['db'],'SELECT * FROM graph_card_completions')==[]


def test_failed_http_admission_keeps_result_uncompleted_and_ends_only_its_request(orphan,explicit_api):
    env,key,doc_id,writer,token=orphan
    post,calls,admit=explicit_api
    admit.refuse=True
    response=post(doc_id)
    assert response.status_code==409,response.text
    assert len(calls)==1
    rr=one(env,key,'result_review',1)
    ledger=execution_store.get(calls[0]['graph_exec_id'])
    assert ledger['status'] not in ('requested','running')
    assert _sql(env['db'],'SELECT * FROM graph_card_completions')==[]
    assert run_store.active_attempt_id(writer['run_id'],key)==writer['attempt_id']
