"""0126 T#3 B8/B9/B10/B12: migrated SQLite, HTTP Graph and isolated Git.

AI/SSE transport and the Git session's repository/G lock are fixture boundaries.
"""
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from test_fg_human_completion_0126 import (
    FA, _h, _sql, db_path, decided, edge, env, legacy_cards, node, put_draft,
    decisions_env, _body, _get, _request, _post, _rows, completions, _command, _current,
    graph, confirm, approve, state, proof, fork_join,
)
pytestmark = pytest.mark.usefixtures('legacy_cards', 'completions')


def completed(e):
    keys = graph(e, names=('a','b','j'), links=[('root','a'),('root','b'),('a','j'),('b','j')], ai=True)
    for key in keys.values(): approve(e, key)
    confirm(e); run = _command(e)
    assert all(state(e,k)['status']=='done' for k in keys.values())
    return keys, run


def entries(e):
    return [json.loads(r['payload_json']) for r in _sql(e['db'],
        "SELECT * FROM graph_run_events WHERE cause='graph_time_machine' AND event_type='graph_run_status_changed' ORDER BY event_seq")]


def reject(e, key, slot='report', request_key='reject', status=200):
    body = _request(e,key,slot,'reject',request_key)
    return body, _post(e,key,body,slot,status=status)


@pytest.fixture(autouse=True)
def evidence_directory(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), 'EVIDENCE_DIRECTORY', tmp_path)


def evidence(e, data):
    with (EVIDENCE_DIRECTORY/'rejection-evidence.jsonl').open('a',encoding='utf-8') as stream:
        stream.write(json.dumps(data, ensure_ascii=False)+'\n')


@pytest.mark.parametrize('mode',['step','full'])
@pytest.mark.parametrize('assigned',[False,True])
@pytest.mark.parametrize('slot,stage',[('report','result'),('instruction','instruction')])
def test_b8_completed_rejection_keeps_independent_history_and_reenters(decisions_env,decided,fork_join,legacy_cards,slot,stage,assigned,mode):
    e=decisions_env; k,run=completed(e)
    before={t:_rows(e,t) for t in ('graph_node_attempts','graph_card_completions','graph_propagations',
                                   'graph_stage_assignments','document_reviews')}
    bstate=state(e,k['b']); docs=_get(e,k['a'])['documents']
    _sql(e['db'],'UPDATE documents SET target_id=? WHERE doc_id=?',
         (docs['report']['doc_id'],_get(e,k['b'])['documents']['report']['doc_id']))
    body,result=reject(e,k['a'],slot)
    assert result['recovery']['node_keys']==sorted([k['a'],k['j']])
    assert result['recovery']['generation']==1 and result['recovery']['next_stage']==stage
    assert state(e,k['b'])==bstate
    assert state(e,k['a'])['status']==state(e,k['j'])['status']=='pending'
    current=_get(e,k['a'])
    assert current['context']['recovery_generation']==1
    assert current['documents']['report']['doc_review_status']=='rejected'
    assert current['documents']['instruction']['doc_review_status']==('approved' if slot=='report' else 'rejected')
    for table, rows in before.items(): assert len(_rows(e,table))==len(rows)
    for row in before['graph_node_attempts']:
        after=next(r for r in _rows(e,'graph_node_attempts') if r['attempt_id']==row['attempt_id'])
        if row['node_key']==k['b']: assert after==row
        else: assert after['status']=='invalidated' and result['recovery']['operation_id'] in after['invalidated_reason']
    assert [x['operation_status'] for x in entries(e)]==['started','completed']
    _current(e); assert state(e,k['a'])['status']=='pending' and not e['jobs']
    again=_post(e,k['a'],body,slot)
    assert again['replayed'] and again['request_id']==result['request_id'] and len(entries(e))==2
    if assigned:
        from test_fg_ai_roles_l2_assignment import roles
        roles(k['a'],instruction='aip_card',execution='aip_card',review='not_used')
    _command(e,mode)
    assert [j['stage'] for j in e['jobs']]==[stage]
    assert state(e,k['a'])['stage']==stage
    new=[a for a in _rows(e,'graph_node_attempts') if a['node_key']==k['a'] and a['status']=='active']
    assert len(new)==1 and new[0]['attempt_id'] not in {a['attempt_id'] for a in before['graph_node_attempts']}
    evidence(e,{'scenario':slot,'recovery':result['recovery'],'history_ids':{
        'attempts':[a['attempt_id'] for a in before['graph_node_attempts']],
        'completions':[a['completion_id'] for a in before['graph_card_completions']]},'new_attempt':new[0]['attempt_id']})


def test_b9_reject_approve_race_and_old_context_have_one_winner(decisions_env,decided,fork_join):
    e=decisions_env; k,run=completed(e)
    body=_request(e,k['a'],action='reject',request_key='race-reject'); barrier=threading.Barrier(2)
    def call(action):
        barrier.wait()
        return e['client'].post(f"/api/v1/flows/{FA}/nodes/{k['a']}/documents/report/decision",
            json={**body,'action':action,'request_key':'race-'+action},headers=_h())
    with ThreadPoolExecutor(max_workers=2) as pool: responses=list(pool.map(call,['approve','reject']))
    assert sorted(r.status_code for r in responses)==[200,409], [r.text for r in responses]
    _post(e,k['a'],{**body,'request_key':'stale'},status=409)
    assert len([x for x in entries(e) if x['operation_status']=='started'])<=1


def test_b8_scope_activity_refuses_without_half_decision(decisions_env,decided,fork_join,monkeypatch):
    from flowgate.card_documents import decision_context
    e=decisions_env; k,run=completed(e); original=decision_context.settings_legacy.node_running
    monkeypatch.setattr(decision_context.settings_legacy,'node_running',lambda f,n:n==k['j'] or original(f,n))
    before=_rows(e); reject(e,k['a'],status=409)
    assert _rows(e)==before and entries(e)==[] and state(e,k['a'])['status']=='done'
    monkeypatch.setattr(decision_context.settings_legacy,'node_running',lambda f,n:n==k['b'] or original(f,n))
    reject(e,k['a']); assert state(e,k['b'])['status']=='done'


@pytest.fixture
def branch(tmp_path,monkeypatch):
    from flowgate.graph_time_machine import git_revert
    repo=tmp_path/'isolated-git'; repo.mkdir()
    def g(*args):
        return subprocess.run(['git','-c','user.name=fixture','-c','user.email=fixture@example.invalid',*args],
                              cwd=repo,check=True,capture_output=True,text=True).stdout.strip()
    g('init','-q'); g('config','core.autocrlf','false'); g('checkout','-q','-b','group')
    for key in ('a','b','j'): (repo/(key+'.txt')).write_bytes(b'base\n')
    g('add','.'); g('commit','-qm','base'); base=g('rev-parse','HEAD'); commits={}
    for key in ('a','b','j'):
        start=g('rev-parse','HEAD') if key=='j' else base
        g('checkout','-q','-b','attempt-'+key,start); (repo/(key+'.txt')).write_bytes((key+'\n').encode())
        g('commit','-qam','result '+key); result=g('rev-parse','HEAD'); g('checkout','-q','group')
        g('merge','--no-ff','-q','-m','integrate '+key,result)
        commits[key]={'result_commit':result,'integrated_commit':g('rev-parse','HEAD'),'input_commit':start}
    monkeypatch.setattr(git_revert.git,'open_cancel_session',lambda *a,**kw:{'ok':True,'session':{'wt_path':repo,'author_env':None}})
    monkeypatch.setattr(git_revert.git,'close_cancel_session',lambda *a:None)
    return SimpleNamespace(repo=repo,g=g,commits=commits,head=g('rev-parse','HEAD'))


def integrated(e,k,branch):
    for name,key in k.items():
        a=next(r for r in _rows(e,'graph_node_attempts') if r['node_key']==key); c=branch.commits[name]
        _sql(e['db'],"UPDATE graph_node_attempts SET input_source='predecessors',input_attempt_ids='[]',"
             "input_commit=?,result_commit=?,integrated_commit=?,integration_status='integrated' WHERE attempt_id=?",
             (c['input_commit'],c['result_commit'],c['integrated_commit'],a['attempt_id']))
        from modules.flow_gate.db import tr_commit_ledger
        from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
        doc_id=_get(e,key)['documents']['report']['doc_id']
        with_flow_guard(FA,lambda:tr_commit_ledger.record_commit(group_id=FA,doc_id=doc_id,
            commit_sha=c['result_commit'],commit_subject='fixture',attempt_id=a['attempt_id'],node_key=key))


def test_b10_b12_git_then_db_failure_restart_finishes_once(decisions_env,decided,fork_join,branch,monkeypatch):
    from flowgate.graph_time_machine import document_rejection,journal,operation
    e=decisions_env; k,run=completed(e); integrated(e,k,branch); before=_rows(e); real=document_rejection.finish
    def fail(*args):
        real(*args)
        raise RuntimeError('injected after decision and invalidation before commit')
    monkeypatch.setattr(document_rejection,'finish',fail)
    body,result=reject(e,k['a'],status=409)
    assert _rows(e)==before and state(e,k['a'])['status']=='done'
    assert [(branch.repo/(n+'.txt')).read_text().strip() for n in ('a','b','j')]==['base','b','base']
    assert [x['operation_status'] for x in entries(e)]==['started','git_applied','partial_failed']
    op=entries(e)[0]['operation_id']; head=branch.g('rev-parse','HEAD'); assert operation.busy(FA)
    monkeypatch.setattr(document_rejection,'finish',real); operation.recover()
    assert not operation.busy(FA) and branch.g('rev-parse','HEAD')==head
    assert state(e,k['a'])['status']=='pending' and state(e,k['b'])['status']=='done'
    assert _post(e,k['a'],body)['replayed']; operation.recover(); assert branch.g('rev-parse','HEAD')==head
    ledger=_rows(e,'tr_commit_ledger'); assert [r['state'] for r in ledger]==['canceled','live','canceled']
    assert len([x for x in entries(e) if x['operation_status']=='completed'])==1
    evidence(e,{'scenario':'db_restart','operation_id':op,'head_before':branch.head,'head_after':head,
                'journal':entries(e),'ledger':ledger})


def test_b12_real_git_conflict_compensates_without_decision(decisions_env,decided,fork_join,branch):
    e=decisions_env; k,run=completed(e); integrated(e,k,branch)
    (branch.repo/'a.txt').write_bytes(b'later-independent-change\n'); branch.g('commit','-qam','later')
    before=branch.g('rev-parse','HEAD'); decisions=_rows(e); body,out=reject(e,k['a'],status=409)
    assert _rows(e)==decisions and state(e,k['a'])['status']=='done'
    assert branch.g('diff',before,'HEAD')=='' and branch.g('status','--porcelain')==''
    assert entries(e)[-1]['operation_status']=='blocked' and entries(e)[-1]['git']['compensated']
    assert [r['state'] for r in _rows(e,'tr_commit_ledger')]==['live','live','live']
    evidence(e,{'scenario':'git_conflict','journal':entries(e),'head_before':before,'head_after':branch.g('rev-parse','HEAD')})


@pytest.mark.parametrize('activity',['requested','running','review','handoff'])
def test_b8_actual_scope_activity_is_atomic_and_independent_work_survives(decisions_env,decided,fork_join,monkeypatch,activity):
    from flowgate.card_settings import legacy_adapter,review_stage
    e=decisions_env; k,run=completed(e)
    attempt=next(a for a in _rows(e,'graph_node_attempts') if a['node_key']==k['j'])
    if activity in ('requested','running'):
        _sql(e['db'],'INSERT INTO graph_attempt_runs (exec_id,attempt_id,run_id,kind,status,created_at,request_json) '
             'VALUES (?,?,?,?,?,?,?)',('busy',attempt['attempt_id'],run['run_id'],'write',activity,'now','{}'))
    elif activity=='review':
        doc_id=_get(e,k['j'])['documents']['report']['doc_id']
        monkeypatch.setattr(legacy_adapter,'review_loop_active',lambda d:d==doc_id)
    else:
        monkeypatch.setattr(review_stage,'_handoff_pending',lambda flow,key:key==k['j'])
    before=_rows(e); reject(e,k['a'],status=409)
    assert _rows(e)==before and entries(e)==[]
    if activity in ('requested','running'):
        other=next(a for a in _rows(e,'graph_node_attempts') if a['node_key']==k['b'])
        _sql(e['db'],'UPDATE graph_attempt_runs SET attempt_id=? WHERE exec_id=?',(other['attempt_id'],'busy'))
        _sql(e['db'],"UPDATE graph_runs SET status='running', idle_reason=NULL WHERE run_id=?",(run['run_id'],))
        record=_rows(e,'graph_attempt_runs'); reject(e,k['a'])
        assert _rows(e,'graph_attempt_runs')==record and state(e,k['b'])['status']=='done'
        assert _current(e)['status']=='running' and state(e,k['a'])['status']=='pending'


def test_b9_late_token_and_callback_stay_invalid_after_new_attempt(decisions_env,decided,fork_join):
    from flowgate.graph_run import fencing
    from modules.flow_gate.services import graph_source_hooks
    e=decisions_env; k,run=completed(e)
    old=next(a for a in _rows(e,'graph_node_attempts') if a['node_key']==k['a'])
    _sql(e['db'],'INSERT INTO graph_run_token_map (token_id,run_id,node_key,mapped_at,attempt_id,stage) VALUES (?,?,?,?,?,?)',
         ('late-token',run['run_id'],k['a'],'now',old['attempt_id'],'result'))
    body,out=reject(e,k['a']); _command(e,'step'); before=state(e,k['a'])
    with pytest.raises(graph_source_hooks.GraphAttemptInactive):
        fencing.require_token({'token_id':'late-token'},'inbox_new')
    assert fencing.node_refused(run['run_id'],k['a'],'result_stage',old['attempt_id'],idle_ok=True)
    assert state(e,k['a'])==before
    items=_rows(e,'graph_document_recovery_items')
    assert any(r['item_kind']=='token' and r['item_id']=='late-token' for r in items)
    assert all(c['status']=='revoked' for c in _rows(e,'graph_card_completions') if c['node_key']==k['a'])


def test_b9_same_rejection_concurrent_has_one_generation(decisions_env,decided,fork_join,monkeypatch):
    from flowgate.graph_time_machine import git_revert
    e=decisions_env; k,run=completed(e); body=_request(e,k['a'],action='reject')
    entered=threading.Event(); release=threading.Event(); real=git_revert.apply
    def delayed(*a,**kw):
        entered.set(); assert release.wait(20)
        return real(*a,**kw)
    monkeypatch.setattr(git_revert,'apply',delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(_post,e,k['a'],body)
        assert entered.wait(20)
        second=_post(e,k['a'],body,status=409)
        release.set(); result=first.result(30)
    assert _post(e,k['a'],body)['replayed']
    assert len([x for x in entries(e) if x['operation_status']=='started'])==1
    assert {r['generation'] for r in _rows(e,'graph_document_recovery_items')}=={1}


def test_b10_real_git_crash_before_commit_resumes_from_marker(decisions_env,decided,fork_join,branch,monkeypatch):
    from flowgate.graph_time_machine import git_revert,operation
    from flowgate.card_documents import decisions
    from modules.flow_gate.db import groups
    e=decisions_env; k,run=completed(e); integrated(e,k,branch)
    body=_request(e,k['a'],action='reject'); original=git_revert.git.revert_tr_commit
    class Crash(BaseException): pass
    def crash(session,*,commit_sha,subject,body,mainline=None):
        subprocess.run(['git','revert','--no-commit','--no-edit','-m','1',commit_sha],cwd=branch.repo,check=True,capture_output=True)
        raise Crash()
    monkeypatch.setattr(git_revert.git,'revert_tr_commit',crash)
    with pytest.raises(Crash): decisions.write(groups.get_by_id(FA),k['a'],'report',body,'u_kim')
    op=entries(e)[0]['operation_id']
    assert git_revert._marker_path(branch.repo,op).exists() and branch.g('status','--porcelain')
    assert state(e,k['a'])['status']=='done'
    monkeypatch.setattr(git_revert.git,'revert_tr_commit',original)
    operation.recover()
    assert not operation.busy(FA) and not git_revert._marker_path(branch.repo,op).exists()
    assert [(branch.repo/(n+'.txt')).read_text().strip() for n in ('a','b','j')]==['base','b','base']
    assert _post(e,k['a'],body)['replayed'] and branch.g('status','--porcelain')==''
    evidence(e,{'scenario':'git_crash','operation_id':op,'journal':entries(e)})


def test_b9_revision_moving_between_git_and_db_keeps_journal_fenced(decisions_env,decided,fork_join,branch,monkeypatch):
    from flowgate.graph_time_machine import git_revert,operation
    e=decisions_env; k,run=completed(e); integrated(e,k,branch); real=git_revert.apply
    doc_id=_get(e,k['a'])['documents']['report']['doc_id']; before=_rows(e)
    def changed(*a,**kw):
        out=real(*a,**kw)
        _sql(e['db'],'UPDATE documents SET revision_no=revision_no+1 WHERE doc_id=?',(doc_id,))
        return out
    monkeypatch.setattr(git_revert,'apply',changed)
    body,out=reject(e,k['a'],status=409)
    assert _rows(e)==before and state(e,k['a'])['status']=='done' and operation.busy(FA)
    assert entries(e)[-1]['operation_status']=='partial_failed'
    assert entries(e)[-1]['error']['code']=='document_context_changed'
    assert _rows(e,'graph_document_recovery_items')==[]


def test_b8_direct_reapproval_keeps_old_completion_revoked(decisions_env,decided,fork_join):
    e=decisions_env; k,run=completed(e); reject(e,k['a'],'instruction')
    approve(e,k['a'],request_key='new-decision')
    from flowgate.card_completion import human
    from flowgate.card_documents import decision_context
    n,r,c,d=decision_context.current(FA,k['a'])
    assert human.evidence(FA,n), (_get(e,k['a']),d)
    assert state(e,k['a'])['status']=='pending'
    _command(e,'step')
    records=[c for c in _rows(e,'graph_card_completions') if c['node_key']==k['a']]
    assert len(records)==2 and {c['status'] for c in records}=={'revoked','applied'}
    assert len({c['attempt_id'] for c in records})==2 and state(e,k['j'])['status']=='pending'
    reject(e,k['a'],request_key='second-rejection')
    assert _get(e,k['a'])['context']['recovery_generation']==2


def test_b8_authored_instruction_carries_but_old_token_does_not(decisions_env,decided,fork_join):
    from modules.flow_gate.db import workflow_events
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from flowgate.card_completion import legacy_adapter
    e=decisions_env; k,run=completed(e); docs=_get(e,k['a'])['documents']
    old=next(a for a in _rows(e,'graph_node_attempts') if a['node_key']==k['a'])
    def seed():
        from modules.flow_gate.graph_workflow.store import run_store
        run_store.get_store()._execute('INSERT INTO graph_run_token_map (token_id,run_id,node_key,mapped_at,attempt_id,stage) '
            'VALUES (?,?,?,?,?,?)',['authored',run['run_id'],k['a'],'now',old['attempt_id'],'instruction'])
        workflow_events.create({'event_type':'token_issued','project_id':'flowgate','group_id':FA,'actor_user_id':'u_kim',
                                'metadata':json.dumps({'token_id':'authored','action_scope':'edit'})})
        workflow_events.create({'event_type':'token_consumed','project_id':'flowgate','group_id':FA,'actor_user_id':'u_kim',
                                'metadata':json.dumps({'token_id':'authored','doc_id':docs['instruction']['doc_id']},separators=(',',':'))})
    with_flow_guard(FA,seed)
    reject(e,k['a']); assert docs['instruction']['doc_id'] not in legacy_adapter.stale_documents(FA,k['a'])
    _command(e,'step'); assert [j['stage'] for j in e['jobs']]==['result']
    # A later unrelated invalidation cannot inherit the earlier carry exception.
    _sql(e['db'],"UPDATE graph_node_attempts SET invalidated_reason='user_stop' WHERE attempt_id=?",(old['attempt_id'],))
    assert docs['instruction']['doc_id'] in legacy_adapter.stale_documents(FA,k['a'])


def test_b12_tr2_plain_commit_is_reverted_and_success_history_is_not_reused(decisions_env,decided,fork_join,branch):
    from modules.flow_gate.db import documents,tr2_approval_attempts,tr_commit_ledger
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from flowgate.card_completion import own_effects
    e=decisions_env; k,run=completed(e); doc_id=_get(e,k['a'])['documents']['report']['doc_id']
    parent=branch.g('rev-parse','HEAD'); (branch.repo/'own.txt').write_bytes(b'TR2 change\n')
    branch.g('add','own.txt'); branch.g('commit','-qm','TR2 apply'); commit=branch.g('rev-parse','HEAD')
    def seed():
        doc=documents.update(doc_id,{'type_code':'TR2'})
        ledger=tr_commit_ledger.record_commit(group_id=FA,doc_id=doc_id,commit_sha=commit,commit_subject='TR2 apply')
        success=tr2_approval_attempts.create(doc=doc,actor_user_id='u_kim',spec_fingerprint='0'*64,
                                            baseline_fingerprint='1'*64,request_key='fixture-tr2')
        tr2_approval_attempts.finish(success['attempt_id'],state='succeeded',result_code='applied',
                                    pre_apply_head_sha=parent,commit_sha=commit,ledger_row_id=ledger['id'])
        return success['attempt_id'],ledger['id']
    aid,lid=with_flow_guard(FA,seed); assert own_effects._tr2_succeeded(doc_id,0)
    body,out=reject(e,k['a'])
    assert not (branch.repo/'own.txt').exists() and branch.g('diff',parent,'HEAD')==''
    assert tr_commit_ledger.get_by_id(lid)['state']=='canceled'
    assert tr2_approval_attempts.by_id(aid)['state']=='succeeded' and not own_effects._tr2_succeeded(doc_id,0)
    assert len(out['recovery']['canceled_ledger_row_ids'])==1
    _post(e,k['a'],_request(e,k['a'],request_key='same-revision-again'),status=409)
    assert _get(e,k['a'])['documents']['report']['doc_review_status']=='rejected'
    evidence(e,{'scenario':'tr2','operation_id':out['recovery']['operation_id'],'tr2_attempt_id':aid,
                'ledger_row_id':lid,'commit':commit,'revert':branch.g('rev-parse','HEAD')})


def test_b12_terminal_test_history_is_preserved_and_active_admission_blocks(decisions_env,decided,fork_join):
    e=decisions_env; k,run=completed(e); ts=_get(e,k['a'])['documents']['instruction']['doc_id']
    _sql(e['db'],"UPDATE documents SET type_code='TS' WHERE doc_id=?",(ts,))
    _sql(e['db'],"INSERT INTO test_runs (run_id,doc_id,revision_no,status,triggered_via,runner_id,created_at) "
         "VALUES ('test-busy',?,0,'running','ui','u_kim','now')",(ts,))
    reject(e,k['a'],status=409); assert entries(e)==[]
    _sql(e['db'],"UPDATE test_runs SET status='passed' WHERE run_id='test-busy'")
    history=_rows(e,'test_runs'); reject(e,k['a'])
    assert _rows(e,'test_runs')==history and _get(e,k['a'])['documents']['instruction']['doc_review_status']=='approved'


def test_migration_173_is_repeatable_and_preserves_generations(decisions_env,decided,fork_join):
    import sqlite3
    from pathlib import Path
    e=decisions_env; k,run=completed(e); reject(e,k['a']); before=_rows(e,'graph_document_recovery_items')
    sql=(Path(__file__).resolve().parents[2]/'sql/migrations/sqlite/173_graph_document_recovery.sql').read_text(encoding='utf-8')
    with sqlite3.connect(e['db']) as conn: conn.executescript(sql); conn.executescript(sql)
    assert _rows(e,'graph_document_recovery_items')==before and _get(e,k['a'])['context']['recovery_generation']==1


@pytest.mark.parametrize('corrupt',['event','propagation'])
def test_b8_missing_completion_provenance_refuses_before_journal(decisions_env,decided,fork_join,corrupt):
    e=decisions_env; k,run=completed(e)
    completion=next(c for c in _rows(e,'graph_card_completions') if c['node_key']==k['a'])
    if corrupt=='event':
        _sql(e['db'],"UPDATE graph_run_events SET payload_json='{}' WHERE run_id=? AND event_seq=?",
             (run['run_id'],completion['applied_event_seq']))
    else:
        _sql(e['db'],'UPDATE graph_propagations SET node_key=? WHERE run_id=? AND source_event_seq=?',
             (k['b'],run['run_id'],completion['applied_event_seq']))
    before=_rows(e); reject(e,k['a'],status=409)
    assert _rows(e)==before and entries(e)==[] and state(e,k['a'])['status']=='done'


@pytest.mark.parametrize('status',['running','cancelling'])
def test_b12_active_test_before_graph_run_blocks_rejection(decisions_env,decided,status):
    e=decisions_env; k=graph(e,names=('a',))
    ts=_get(e,k['a'])['documents']['instruction']['doc_id']
    _sql(e['db'],"UPDATE documents SET type_code='TS' WHERE doc_id=?",(ts,))
    _sql(e['db'],"INSERT INTO test_runs (run_id,doc_id,revision_no,status,triggered_via,runner_id,created_at) "
         "VALUES ('test-pending',?,0,?,'ui','u_kim','now')",(ts,status))
    before=_rows(e); reject(e,k['a'],status=409)
    assert _rows(e)==before and not _rows(e,'graph_runs')
