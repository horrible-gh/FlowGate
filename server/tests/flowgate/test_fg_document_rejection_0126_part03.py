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
