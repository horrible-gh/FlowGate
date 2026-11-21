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
