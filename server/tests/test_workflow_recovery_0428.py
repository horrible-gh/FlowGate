"""0428 T#5: durable SQL, contention, hard-exit boundaries and callback races."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_workflow_dispatch_0428 import (
    completion_database, db, prepared, chain_stop, invoke, ROOT, RESULT, GROUP, TS,
    decisions, paused, dispatch, command, documents, seqdb, svc, runtime,
    test_run_service, test_basis_service,
)
from modules.flow_gate.db import ai_invoke_runs
from modules.flow_gate.services.ai_invoke import finalize


@pytest.fixture
def held(prepared, chain_stop, monkeypatch):
    original = dispatch.consume
    monkeypatch.setattr(dispatch, 'consume', lambda ident: dispatch.projection(decisions.dispatch_by_id(ident)))
    out = invoke(prepared)
    monkeypatch.setattr(dispatch, 'consume', original)
    return out


@pytest.fixture
def traced_start(chain_stop, monkeypatch):
    def start(**kw):
        run_id = dispatch.reserve_run('next-run')
        issue = kw['issue_builder'](run_id)
        run = {'run_id':run_id,'group_id':GROUP,'chain_id':kw['chain_id'],
               'issued_to':kw['issued_to'],'token_id':issue['token_id'], 'status':'running'}
        dispatch.before_worker(run)
        chain_stop['launched'].append((kw,issue))
        dispatch.worker_started(run)
        return {'ok':True,'run_id':run_id,'status':'running'}
    monkeypatch.setattr(svc, 'start_run', start)
    return start


@pytest.mark.parametrize('same_key',[True,False])
def test_concurrent_confirm_and_retransmit_one_worker(prepared, chain_stop, traced_start, same_key):
    gate=threading.Barrier(8)
    def click(i):
        gate.wait(timeout=10)
        return invoke(prepared,request_key='same' if same_key else 'key-'+str(i))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(click,range(8)))
    assert len({out['decision_id'] for out in results})==1
    assert all(out['continuation_status']=='started' and out['run_id']=='next-run' for out in results)
    assert len(chain_stop['launched'])==1
    assert prepared[0]._fetch_one('SELECT COUNT(*) AS n FROM workflow_completion_dispatches',[])['n']==1
    assert prepared[0]._fetch_one('SELECT COUNT(*) AS n FROM workflow_completion_decisions',[])['n']==1


def test_replay_while_canonical_worker_active(prepared, chain_stop, traced_start, monkeypatch):
    first=invoke(prepared)
    monkeypatch.setattr(runtime,'active_run_for_group',lambda group:{'run_id':'next-run'})
    assert invoke(prepared)==first
    assert len(chain_stop['launched'])==1


def test_concurrent_consumers_and_pass_use_one_dispatch(prepared, chain_stop, held, traced_start):
    gate=threading.Barrier(6)
    def callback(i):
        gate.wait(timeout=10)
        return (test_run_service.continue_chain_after_test_gate(documents.get_by_id(TS))
                if i%2 else dispatch.consume(held['dispatch_id']))
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(callback,range(6)))
    assert all(out['continuation_status']=='started' for out in results),results
    assert len(chain_stop['launched'])==1
    assert paused.get_by_group(GROUP) is None


def origin(chain_stop):
    return {'run_id':'stop-0428','mode':'continuous','group_id':GROUP,'doc_ref':ROOT,
            'chain_id':'chain-0428','issued_to':'chain-owner','test_gate_doc_id':TS,
            'stop_code':'test_run_pending','resumable':True,'status':'finished',
            'finished_at':'2026-10-12T00:00:00Z','continuation_target_seq':2,
            'docs_target':4,'docs_reached':2,'chain_docs_target':8,'chain_docs_reached':5}


@pytest.mark.parametrize('stop_code', ['test_run_pending', 'test_gate_blocked'])
def test_late_finalizer_pending_binds_and_starts_once(prepared, chain_stop, traced_start, monkeypatch, stop_code):
    run=origin(chain_stop)
    run['stop_code']=stop_code
    paused.delete_by_group(GROUP)
    monkeypatch.setattr(runtime,'get_run_record',lambda rid:run if rid=='stop-0428' else None)
    out=invoke(prepared)
    assert out['continuation_status']=='pending' and not chain_stop['launched']
    assert dispatch.consume_pending()[0]['continuation_status']=='pending'
    finalize._apply_stop_row(run,False)
    continued=test_run_service.continue_chain_after_test_gate(documents.get_by_id(TS))
    assert continued['continuation_status']=='started',continued
    assert len(chain_stop['launched'])==1
    # A repeated finalizer must not recreate the stop after consumption.
    finalize._apply_stop_row(run,False)
    assert paused.get_by_group(GROUP) is None
    assert test_run_service.continue_chain_after_test_gate(documents.get_by_id(TS))['run_id']=='next-run'
    assert len(chain_stop['launched'])==1


@pytest.mark.parametrize('field,value',[('chain_id','wrong'),('paused_by','other-owner'),('stop_kind','user'),('stop_code','question_pending')])
def test_late_stop_mismatch_keeps_row_no_worker(prepared, chain_stop, monkeypatch, field, value):
    run=origin(chain_stop)
    paused.delete_by_group(GROUP)
    monkeypatch.setattr(runtime,'get_run_record',lambda rid:run)
    out=invoke(prepared)
    finalize._apply_stop_row(run,False)
    prepared[0]._execute('UPDATE ai_invoke_paused_chains SET '+field+'=? WHERE group_id=?',[value,GROUP])
    before=paused.get_by_group(GROUP)
    result=dispatch.consume(out['dispatch_id'])
    assert result['continuation_status']=='recovery_required'
    assert paused.get_by_group(GROUP)==before and not chain_stop['launched']


def hard_exit(prepared, held, tmp_path, stage):
    """Separate interpreter commits the real claim, then exits without cleanup."""
    payload={'db':prepared[0]._db.path,'dispatch_id':held['dispatch_id'],'stage':stage}
    data=tmp_path/'crash.json'
    data.write_text(json.dumps(payload),encoding='utf8')
    script=tmp_path/'crash_worker.py'
    script.write_text('''
import json, os, sys
from test_workflow_completion_0428 import SQLiteBridge
from modules.flow_gate.db import connection, workflow_completion as db
from modules.flow_gate.services import workflow_completion_dispatch_service as dispatch
p=json.load(open(sys.argv[1],encoding="utf8"))
store=connection.FlowGateStore()
store._db=SQLiteBridge(p["db"])
store._sq=None
connection.STORE=store
d=db.dispatch_by_id(p["dispatch_id"])
if p["stage"] != "saved":
    row=json.loads(d["stop_snapshot_json"])["row"]
    dispatch.claim_stop(d,row)
if p["stage"] in {"reserved","issued","attempted","started"}:
    dispatch._launch_dispatch.set(d["dispatch_id"])
    dispatch.reserve_run("crash-run")
if p["stage"] in {"issued","attempted","started"}:
    dispatch.bind_token("crash-run","crash-token")
if p["stage"] in {"attempted","started"}:
    dispatch.before_worker({"run_id":"crash-run"})
if p["stage"] == "started":
    from modules.flow_gate.db import ai_invoke_runs
    ai_invoke_runs.upsert({"run_id":"crash-run", "project_id":"flowgate",
        "group_id":row["group_id"],"doc_ref":row["doc_ref"],"mode":"continuous",
        "chain_id":d["chain_id"],"issued_to":row["paused_by"],"token_id":"crash-token",
        "started_at":"2026-10-12T00:00:00Z","finished_at":"2026-10-12T00:01:00Z",
        "end_reason":"target_reached","outcome":"complete",
        "created_at":"2026-10-12T00:00:00Z","updated_at":"2026-10-12T00:01:00Z"})
os._exit(73)
''',encoding='utf8')
    import os
    env=dict(os.environ)
    server=Path(__file__).resolve().parents[1]
    env['PYTHONPATH']=str(server)+os.pathsep+str(server/'tests')
    result=subprocess.run([sys.executable,'-B',str(script),str(data)],env=env,
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==73,result.stdout+result.stderr


@pytest.mark.parametrize('stage',['saved','consumed','reserved'])
def test_hard_exit_before_token_recovers_same_decision(prepared, chain_stop, held, traced_start, tmp_path, stage):
    hard_exit(prepared,held,tmp_path,stage)
    if stage!='saved':
        recovered=dispatch.consume(held['dispatch_id'])
        assert recovered['continuation_status']=='retryable_failed',recovered
        assert dispatch.same_stop(paused.get_by_group(GROUP),chain_stop['row'])
    results=dispatch.consume_pending()
    # An ID reserved before an interrupted issuance is kept on retry. Use the
    # reservation in the test launch just as real admission reserve_run does.
    assert results[0]['continuation_status']=='started',results
    assert results[0]['run_id']==('crash-run' if stage=='reserved' else 'next-run')
    assert len(chain_stop['launched'])==1
    assert decisions.get_decision(held['decision_id'])['invalidated_at'] is None


@pytest.mark.parametrize('stage',['issued','attempted'])
def test_hard_exit_uncertain_boundary_never_relaunches(prepared, chain_stop, held, tmp_path, stage):
    hard_exit(prepared,held,tmp_path,stage)
    for _ in range(3):
        result=dispatch.consume(held['dispatch_id'])
        assert result['continuation_status']=='recovery_required',result
        dispatch.consume_pending()
    assert not chain_stop['launched'] and paused.get_by_group(GROUP) is None


def test_hard_exit_started_run_reused_after_registry_loss(prepared,chain_stop,held,tmp_path):
    hard_exit(prepared,held,tmp_path,'started')
    result=dispatch.consume(held['dispatch_id'])
    assert result['continuation_status']=='started' and result['run_id']=='crash-run',result
    assert dispatch.consume(held['dispatch_id'])==result
    assert not chain_stop['launched']
    assert prepared[0]._fetch_one('SELECT COUNT(*) AS n FROM ai_invoke_runs WHERE run_id=?',['crash-run'])['n']==1


@pytest.mark.parametrize('error_kind',['runtime','http','value'])
def test_start_response_loss_returns_confirmed_existing_run(prepared,chain_stop,monkeypatch,error_kind):
    def start(**kw):
        issue=kw['issue_builder']('next-run')
        run={'run_id':'next-run','group_id':GROUP,'chain_id':kw['chain_id'],
             'issued_to':kw['issued_to'],'token_id':issue['token_id']}
        dispatch.before_worker(run)
        chain_stop['launched'].append((kw,issue))
        dispatch.worker_started(run)
        if error_kind == 'http':
            from fastapi import HTTPException
            raise HTTPException(409,detail={'code':'run_started_but_response_lost'})
        if error_kind == 'value':
            raise ValueError('response lost after durable start')
        raise RuntimeError('response lost after durable start')
    monkeypatch.setattr(svc,'start_run',start)
    out=invoke(prepared)
    assert out['continuation_status']=='started',out
    assert invoke(prepared)==out and len(chain_stop['launched'])==1
    assert paused.get_by_group(GROUP) is None


def test_lost_completion_write_uses_confirmed_live_run(prepared,chain_stop,monkeypatch):
    original=decisions.cas_dispatch
    lives={}
    def fail_once(ident,**kw):
        if kw['state']=='completed' and not lives.get('failed'):
            lives['failed']=True
            raise RuntimeError('DB acknowledgement lost')
        return original(ident,**kw)
    monkeypatch.setattr(decisions,'cas_dispatch',fail_once)
    original_lookup=runtime.get_run_record
    monkeypatch.setattr(runtime,'get_run_record',lambda rid:lives.get(rid) or original_lookup(rid))
    def start(**kw):
        issue=kw['issue_builder']('next-run')
        run={'run_id':'next-run','group_id':GROUP,'chain_id':kw['chain_id'],
             'issued_to':kw['issued_to'],'token_id':issue['token_id']}
        dispatch.before_worker(run)
        lives['next-run']=run
        chain_stop['launched'].append((kw,issue))
        dispatch.worker_started(run)
    monkeypatch.setattr(svc,'start_run',start)
    out=invoke(prepared)
    assert out['continuation_status']=='recovery_required',out
    again=invoke(prepared)
    assert again['continuation_status']=='started' and again['run_id']=='next-run',again
    assert len(chain_stop['launched'])==1


def test_orphan_record_not_proof_of_started_worker(prepared,chain_stop,held,monkeypatch):
    d=decisions.dispatch_by_id(held['dispatch_id'])
    dispatch.claim_stop(d,chain_stop['row'])
    running=decisions.dispatch_by_id(d['dispatch_id'])
    assert decisions.cas_dispatch(d['dispatch_id'],expected_state='running',expected_version=running['state_version'],
        state='running',run_id='orphan',token_id='orphan-token',error_code='completion_launch_attempted')
    monkeypatch.setattr(ai_invoke_runs,'get',lambda rid:{'group_id':GROUP,'chain_id':'chain-0428',
        'issued_to':'chain-owner','token_id':'orphan-token','end_reason':'orphaned_by_restart'})
    result=dispatch.consume(d['dispatch_id'])
    assert result['continuation_status']=='recovery_required' and not chain_stop['launched']



@pytest.mark.parametrize('column,value',[('token_id','other-token'),('doc_ref',TS),('issued_to','other-owner')])
def test_persisted_run_identity_mismatch_requires_recovery(prepared,chain_stop,held,tmp_path,column,value):
    hard_exit(prepared,held,tmp_path,'started')
    prepared[0]._execute('UPDATE ai_invoke_runs SET '+column+'=? WHERE run_id=?',[value,'crash-run'])
    result=dispatch.consume(held['dispatch_id'])
    assert result['continuation_status']=='recovery_required' and not chain_stop['launched']


def test_old_key_without_invalidation_hook_is_cancelled(prepared,chain_stop,held):
    prepared[0]._execute('UPDATE documents SET revision_no=8 WHERE doc_id=?',[RESULT])
    result=dispatch.consume(held['dispatch_id'])
    assert result['continuation_status']=='cancelled' and not chain_stop['launched']
    assert paused.get_by_group(GROUP)==chain_stop['row']


def test_consumer_poller_idempotent_start_and_shutdown(monkeypatch):
    # Exercise the real lifecycle without sleeping or starting a detached test.
    polls=[]
    original=dispatch._consumer_stop
    class Stop:
        def clear(self): self.stopped=False
        def set(self): self.stopped=True
        def wait(self, seconds):
            if self.stopped: return True
            self.stopped=True
            return False
    stop=Stop()
    monkeypatch.setattr(dispatch,'_consumer_stop',stop)
    monkeypatch.setattr(dispatch,'consume_pending',lambda:polls.append('sweep'))
    dispatch.start_consumer()
    dispatch.stop_consumer()
    assert polls==['sweep']
    assert not dispatch._consumer_thread.is_alive()
    dispatch.start_consumer()
    dispatch.stop_consumer()
    assert polls==['sweep','sweep']



# Capture the production seams before the isolated CLI environment patches them.
from test_ai_invoke_pause_resume_0252 import fake_env, _provider
_REAL_START = svc.start_run
_REAL_WORKER = svc._worker
_REAL_LOOKUP = runtime.get_run_record
_DOC_METHODS = {name:getattr(documents,name) for name in
    ('get_group_max_seq','get_documents_by_group_id','get_by_id')}
_SEQ_METHODS = {name:getattr(seqdb,name) for name in
    ('get_sequence_for_member_doc','get_sequence_by_doc_id','get_sequence_items',
     'get_effective_head','get_sequence_snapshots_for_member_docs')}
_PAUSE_METHODS = {name:getattr(paused,name) for name in
    ('upsert','get_by_group','exists','delete_and_return','release_owned',
     'delete_by_group','delete_system_stop','list_by_user')}


def test_real_admission_cli_worker_binds_dispatch_before_response(prepared,chain_stop,fake_env,monkeypatch):
    import time
    for module,methods in [(documents,_DOC_METHODS),(seqdb,_SEQ_METHODS),(paused,_PAUSE_METHODS)]:
        for name,method in methods.items():
            monkeypatch.setattr(module,name,method)
    monkeypatch.setattr(svc,'start_run',_REAL_START)
    monkeypatch.setattr(runtime,'get_run_record',_REAL_LOOKUP)
    command_line = chr(34) + sys.executable + chr(34) + ' -c "import sys; sys.stdin.read(); print(123)"'
    fake_env['chain']['providers']=[_provider(command_line,pid='provider-1')]
    prepared[0]._execute('UPDATE ai_invoke_paused_chains SET continuation_restart_max_attempts=0, continuation_provider_overrides=NULL, continuation_work_executor_provider_id=NULL, continuation_review_count_overrides=NULL, continuation_reviewer_overrides=NULL WHERE group_id=?',[GROUP])
    workers=[]
    def worker(run, providers, mention):
        workers.append(run['run_id'])
        return _REAL_WORKER(run,providers,mention)
    monkeypatch.setattr(svc,'_worker',worker)
    out=invoke(prepared)
    assert out['continuation_status']=='started',out
    saved=decisions.dispatch_by_id(out['dispatch_id'])
    assert saved['run_id']==out['run_id'] and saved['token_id']=='tok_20260717_000001'
    run=_REAL_LOOKUP(out['run_id'])
    assert run['completion_launch_confirmed'] and run['completion_dispatch_id']==saved['dispatch_id']
    assert invoke(prepared)==out
    deadline=time.monotonic()+30
    while _REAL_LOOKUP(out['run_id'])['status']!='finished' and time.monotonic()<deadline:
        time.sleep(.05)
    assert _REAL_LOOKUP(out['run_id'])['status']=='finished'
    assert workers==[out['run_id']]
    assert ai_invoke_runs.get(out['run_id']) is not None


def test_active_replay_binds_new_request_key_to_canonical_decision(prepared, chain_stop, traced_start, monkeypatch):
    first = invoke(prepared)
    monkeypatch.setattr(runtime, 'active_run_for_group', lambda group: {'run_id': 'next-run'})
    replay = invoke(prepared, request_key='active-alias')
    assert replay == first
    assert decisions.decision_for_request('active-alias')['decision_id'] == first['decision_id']
    assert len(chain_stop['launched']) == 1


@pytest.mark.parametrize('field,value', [('stop_kind', 'user'), ('stop_code', 'question_pending'),
                                        ('chain_id', 'new-chain'), ('continuation_default_note', 'new-setting')])
def test_pending_finalizer_preserves_existing_stop(prepared, chain_stop, monkeypatch, field, value):
    run = origin(chain_stop)
    paused.delete_by_group(GROUP)
    monkeypatch.setattr(runtime, 'get_run_record', lambda rid: run if rid == 'stop-0428' else None)
    out = invoke(prepared)
    assert out['continuation_status'] == 'pending'
    finalize._apply_stop_row(run, False)
    prepared[0]._execute('UPDATE ai_invoke_paused_chains SET ' + field + '=? WHERE group_id=?', [value, GROUP])
    before = paused.get_by_group(GROUP)
    finalize._apply_stop_row(run, False)
    assert paused.get_by_group(GROUP) == before
    assert not chain_stop['launched']


def test_recovery_does_not_steal_live_prelaunch_source_lock(prepared, chain_stop, held, monkeypatch):
    from contextlib import contextmanager
    d = decisions.dispatch_by_id(held['dispatch_id'])
    dispatch.claim_stop(d, chain_stop['row'])
    @contextmanager
    def busy(doc):
        raise RuntimeError('another server owns source G through admission')
        yield
    original = command._group_lock
    monkeypatch.setattr(command, '_group_lock', busy)
    out = dispatch.consume(d['dispatch_id'])
    assert out['continuation_status'] == 'running'
    assert paused.get_by_group(GROUP) is None and not chain_stop['launched']
    monkeypatch.setattr(command, '_group_lock', original)
    out = dispatch.consume(d['dispatch_id'])
    assert out['continuation_status'] == 'retryable_failed'
    assert dispatch.same_stop(paused.get_by_group(GROUP), chain_stop['row'])


def test_separate_process_claim_race_has_exactly_one_winner(prepared, chain_stop, held, tmp_path):
    import os
    payload = tmp_path / 'claim.json'
    payload.write_text(json.dumps({'db': prepared[0]._db.path, 'dispatch_id': held['dispatch_id']}), encoding='utf8')
    script = tmp_path / 'claim_worker.py'
    script.write_text("""
import json, sys
from test_workflow_completion_0428 import SQLiteBridge
from modules.flow_gate.db import connection, workflow_completion as db
from modules.flow_gate.services import workflow_completion_dispatch_service as dispatch
p=json.load(open(sys.argv[1],encoding='utf8'))
store=connection.FlowGateStore()
store._db=SQLiteBridge(p['db'])
store._sq=None
connection.STORE=store
d=db.dispatch_by_id(p['dispatch_id'])
row=json.loads(d['stop_snapshot_json'])['row']
try:
    dispatch.claim_stop(d,row)
    print('CLAIMED')
except ValueError:
    print('CONFLICT')
""", encoding='utf8')
    env = dict(os.environ)
    server = Path(__file__).resolve().parents[1]
    env['PYTHONPATH'] = str(server) + os.pathsep + str(server / 'tests')
    procs = [subprocess.Popen([sys.executable, '-B', str(script), str(payload)],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for _ in range(4)]
    results = []
    try:
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=30)
            assert proc.returncode == 0, stdout + stderr
            results.append(stdout.strip())
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
    assert results.count('CLAIMED') == 1 and results.count('CONFLICT') == 3, results
    assert decisions.dispatch_by_id(held['dispatch_id'])['state'] == 'running'
    assert paused.get_by_group(GROUP) is None and not chain_stop['launched']
