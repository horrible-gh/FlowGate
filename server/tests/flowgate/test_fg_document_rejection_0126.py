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
