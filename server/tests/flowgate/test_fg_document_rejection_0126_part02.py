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
