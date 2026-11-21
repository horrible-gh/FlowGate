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
