"""0156 T3: deterministic persistence checks; no provider, Git repo, or waiting window."""
import json
import threading
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock
import pytest
from test_fg_card_settings import db_path, env, decided, legacy_cards, FA, _h, _sql, _run_one_card
from flowgate.graph_run import execution, execution_store as ledger, scheduling, settle, stop, workspace, result_merge
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services import run_apply, run_service, node_dispatch
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.services.ai_invoke import remote_supervisor as supervisor, terminal

pytestmark = pytest.mark.usefixtures('legacy_cards')
REAL_ACQUIRE = scheduling.acquire

@pytest.fixture
def scene(env, decided, monkeypatch):
    key = _run_one_card(env, {'kind':'human','user_id':'u_kim','provider_id':None})
    response = env['client'].post(f'/api/v1/flows/{FA}/runs',json={'revision_no':1},headers=_h())
    assert response.status_code == 201, response.text
    run = run_store.active_run(FA)
    rid = run['run_id']
    _sql(env['db'], "UPDATE graph_runs SET status='running',idle_reason=NULL WHERE run_id=?", (rid,))
    aid = run_store.active_attempt_id(rid,key)
    assert aid
    eid = ledger.request(aid,rid,'write',{'type':'write','job':{}})
    monkeypatch.setattr(workspace,'verify_execution',lambda _:None)
    monkeypatch.setattr(workspace,'cleanup',lambda _:True)
    monkeypatch.setattr(workspace,'cleanup_finished',lambda:None)
    monkeypatch.setattr(supervisor,'is_held',lambda _:False)
    monkeypatch.setattr(supervisor,'run_has_unsettled_job',lambda _:False)
    monkeypatch.setattr(supervisor,'_hold_rows',lambda *a:[])
    monkeypatch.setattr(supervisor,'_settle_in_process',lambda svc:None)
    monkeypatch.setattr(supervisor,'_jobs',lambda:SimpleNamespace(advance_ai_jobs=lambda:None,sweep_orphaned_ai_jobs=lambda _:None,unfenced_expired_ai_jobs=lambda:[]))
    monkeypatch.setattr(ai_invoke_service,'get_run_record',lambda _:None)
    monkeypatch.setattr(execution.ai_invoke_runs,'get',lambda _:None)
    monkeypatch.setattr(stop,'KILL_WAIT_SEC',0)
    monkeypatch.setattr(ai_invoke_service,'cancel_run',Mock())
    if hasattr(supervisor,'_graph_refill_candidates'):
        monkeypatch.setattr(supervisor,'_graph_refill_candidates',{})
    calls=[]
    def replay(row):
        # Execute real admission/CAS and acquire, with no process construction.
        ident='fake-'+str(len(calls))
        try:
            scheduling.admit(row['exec_id'],ident,FA)
            token=REAL_ACQUIRE(row['exec_id'],ident)
            if token: calls.append((row['exec_id'],ident,token))
        except Exception:
            if not scheduling.duplicate_start(row['exec_id'],ident): raise
    monkeypatch.setattr(execution,'replay',Mock(side_effect=replay))
    return SimpleNamespace(env=env,rid=rid,key=key,aid=aid,eid=eid,calls=calls)

def snapshot(s):
    return {'run':run_store.run_by_id(s.rid),'execution':ledger.get(s.eid),
            'attempt':workspace.attempt(s.aid),'node':next(n for n in run_store.node_runs(s.rid) if n['node_key']==s.key),
            'events':run_store._all('SELECT cause,reason FROM graph_run_events WHERE run_id=?',[s.rid]),
            'executions':run_store._all('SELECT exec_id,status FROM graph_attempt_runs WHERE run_id=?',[s.rid]),'starts':list(s.calls)}

def observe(s,label):
    facts=snapshot(s)
    with (Path(s.env['db']).parent/'observations.jsonl').open('a',encoding='utf-8') as out:
        out.write(json.dumps({'trigger':label,**facts},default=str)+'\n')
    print('OBS',label,json.dumps({'run':(facts['run']['status'],facts['run'].get('idle_reason')),
        'exec':(facts['execution']['exec_id'],facts['execution']['status'],facts['execution']['ai_run_id']),
        'attempt':(facts['attempt']['attempt_id'],facts['attempt']['status'],facts['attempt'].get('invalidated_reason')),
        'node':facts['node']['status'],'starts':len(s.calls),'rows':len(facts['executions'])}))

def expired(s,monkeypatch,kind):
    token=REAL_ACQUIRE(s.eid,'ai')
    assert token
    run={'run_id':'ai','group_id':FA,'graph_exec_id':s.eid,'graph_lease_token':token,'status':'finished','proc':None,'cancel_event':threading.Event()}
    ended={'status':'finished'}
    if kind=='a':
        with monkeypatch.context() as injection:
            injection.setattr(ledger,'finish',Mock(side_effect=RuntimeError('injected DB error')))
            assert terminal.attempt(run,'graph_execution_end',lambda:execution.finish(run)) is False
    if kind in ('b','L1'): run.update(status='running',proc=SimpleNamespace(poll=lambda:0 if kind=='b' else None))
    if kind=='L2': run.update(status='running',_provider_active=True)
    if kind=='L3': run.update(status='running',remote_job_active=True)
    if kind=='L4': monkeypatch.setattr(supervisor,'is_held',lambda _:True)
    if kind=='L5': ended=None
    monkeypatch.setattr(ai_invoke_service,'get_run_record',lambda _:None if kind in ('c','L4') else run)
    monkeypatch.setattr(execution.ai_invoke_runs,'get',lambda _:ended)
    _sql(s.env['db'],"UPDATE graph_attempt_runs SET lease_expires_at='2000-01-01T00:00:00Z' WHERE exec_id=?",(s.eid,))
    return run

@pytest.mark.parametrize('kind',['a','b','c','L1','L2','L3','L4','L5'])
def test_d4_runtime_and_live(scene,monkeypatch,kind):
    s=scene; expired(s,monkeypatch,kind)
    run_store._affected("INSERT INTO graph_node_runs(run_id,node_key,status,updated_at) VALUES (?, 'sibling', 'open', ?)",[s.rid,ledger.now()])
    sibling_attempt=run_store.open_attempt(s.rid,'sibling',1)
    sibling=ledger.request(sibling_attempt,s.rid,'review',{})
    sibling_token=ledger.acquire(sibling,'sibling-ai')
    before=snapshot(s)
    spies={name:Mock(wraps=getattr(obj,name)) for obj,name in [(ledger,'mark_lost'),(stop,'invalidate_attempt'),(stop,'reset_node')]}
    for obj,name in [(ledger,'mark_lost'),(stop,'invalidate_attempt'),(stop,'reset_node')]: monkeypatch.setattr(obj,name,spies[name])
    execution.settle(FA); observe(s,'E1')
    # E2: another execution's already-owned terminal boundary invokes settle.
    execution.finish({'graph_exec_id':sibling,'graph_lease_token':sibling_token,'run_id':'sibling-ai','group_id':FA,'cancel_event':threading.Event()})
    observe(s,'E2')
    command=s.env['client'].post(f'/api/v1/flows/{FA}/runs',json={'mode':'step'},headers=_h())
    assert command.status_code==409
    observe(s,'E3-409')
    supervisor.tick();supervisor.tick();observe(s,'E5')
    if kind in ('a','c'):
        assert ledger.get(s.eid)['status']=='lost'
        assert workspace.attempt(s.aid)['invalidated_reason']=='lease_expired'
        assert spies['mark_lost'].call_count==1
    else:
        assert ledger.get(s.eid)==before['execution']
        assert workspace.attempt(s.aid)==before['attempt']
        assert snapshot(s)['node']==before['node']
        assert not s.calls
        execution.replay.assert_not_called()
        for spy in spies.values():spy.assert_not_called()
        execution.recover_execution(s.eid)
        for spy in spies.values():spy.assert_not_called()
    assert len(snapshot(s)['executions'])==len(before['executions'])

@pytest.mark.parametrize('kind',['a','b','c','L1','L2','L3','L4','L5'])
def test_d4_stop_and_startup(scene,monkeypatch,kind):
    s=scene;expired(s,monkeypatch,kind)
    if kind in ('a','c'):
        execution.recover(); observe(s,'E6')
        assert ledger.get(s.eid)['status']=='lost'
        return
    result=stop.stop_run({'group_id':FA},s.rid,None,'u_kim')
    observe(s,'E4')
    supervisor.tick();stop.advance(FA,s.rid);observe(s,'E4-repeat')
    if kind=='L5':
        # A finished in-memory record is sufficient for explicit Stop, even without
        # its durable end. This is Stop cleanup, never expiry recovery/replay.
        assert run_store.run_by_id(s.rid)['status']=='idle'
        assert run_store.run_by_id(s.rid)['idle_reason']=='stopped'
        assert ledger.get(s.eid)['status']=='cancelled'
    else:
        assert run_store.run_by_id(s.rid)['status']=='stopping'
        assert ledger.get(s.eid)['status']=='running'
    assert not s.calls
    ai_invoke_service.cancel_run.assert_called()

def test_live_heartbeat(scene,monkeypatch):
    s=scene;r=expired(s,monkeypatch,'L1')
    with pytest.raises(ledger.LeaseLost): execution.heartbeat('ai')
    ai_invoke_service.cancel_run.assert_called_once_with('ai')
    assert not s.calls
    # Cancellation request alone is not termination; no replacement can acquire.
    supervisor.tick()
    assert ledger.get(s.eid)['status']=='running'
    r.update(status='finished',proc=None,cancelled=True)
    execution.finish(r);observe(s,'heartbeat-cancelled-finish')
    assert ledger.get(s.eid)['status'] in ('cancelled','ended')

@pytest.mark.parametrize('kind',['B2','B3','B6','R6-a','R6-b','R6-c'])
@pytest.mark.parametrize('gui',[False,True])
def test_requested_recovery(scene,monkeypatch,kind,gui):
    s=scene
    if kind=='B2':
        monkeypatch.setattr(execution._time_machine(),'busy',lambda _:True)
        with pytest.raises(Exception): scheduling.admit(s.eid,'old',FA)
        monkeypatch.setattr(execution._time_machine(),'busy',lambda _:False)
    elif kind=='B3':
        from modules.flow_gate.db import group_ai_leases
        assert group_ai_leases.acquire(group_id=FA,project_id='flowgate',run_id='legacy',chain_id=None,action_scope='new',worker_identity=None)
        with pytest.raises(Exception): scheduling.admit(s.eid,'old',FA)
        terminal.release_owned_lease({'group_id':FA,'run_id':'legacy'},'test')
    elif kind in ('B6','R6-c'):
        scheduling.admit(s.eid,'old',FA)
        if kind=='R6-c':
            original=scheduling.with_flow_guard
            def broken(flow,body):
                if body.__name__=='release':raise RuntimeError('injected admission release')
                return original(flow,body)
            monkeypatch.setattr(scheduling,'with_flow_guard',broken)
            def failed():
                scheduling._admission_owner.set((s.eid,'old',FA))
                raise RuntimeError('injected starter')
            with pytest.raises(RuntimeError):scheduling.admission(failed)
            monkeypatch.setattr(scheduling,'with_flow_guard',original)
    elif kind=='R6-a':
        run_service.run_jobs([lambda: (_ for _ in ()).throw(RuntimeError('before start_ai'))])
    elif kind=='R6-b':
        monkeypatch.setattr(node_dispatch,'_ai_starter',Mock(side_effect=RuntimeError('starter')))
        original_hook=node_dispatch.graph_execution_hooks.call
        monkeypatch.setattr(node_dispatch.graph_execution_hooks,'call',Mock(side_effect=RuntimeError('start_failed')))
        monkeypatch.setattr(node_dispatch,'record_ai_failure',Mock(side_effect=RuntimeError('record_ai_failure')))
        run_service.run_jobs([lambda:node_dispatch.start_ai({'run_id':s.rid,'node_key':s.key,'graph_exec_id':s.eid,'stage':'result'})])
        monkeypatch.setattr(node_dispatch.graph_execution_hooks,'call',original_hook)
    observe(s,kind+'-injected')
    if gui:execution.settle(FA)
    supervisor.tick();observe(s,'tick1')
    supervisor.tick();observe(s,'tick2')
    assert ledger.get(s.eid)['status']=='running'
    assert len(s.calls)==1


def test_schema_and_availability(scene):
    s=scene
    assert supervisor.available()
    cols=run_store._all('PRAGMA table_info(graph_run_command_requests)',[])
    assert {r['name']:r['pk'] for r in cols if r['pk']}=={'group_id':1,'command_request_id':2}
    ddl=run_store._one("SELECT sql FROM sqlite_master WHERE name='graph_run_command_requests'",[])['sql']
    assert 'CHECK' in ddl
    assert run_store._one("SELECT name FROM sqlite_master WHERE name='trg_graph_command_group_deleted'",[])


@pytest.mark.parametrize('status',['running','paused','idle'])
@pytest.mark.parametrize('opened',[False,True])
def test_closed_conflict(scene,monkeypatch,status,opened):
    s=scene
    ledger.cancel(s.eid,None,'requested')
    _sql(s.env['db'],"UPDATE graph_runs SET status=?,idle_reason='merge_conflict' WHERE run_id=?",(status,s.rid))
    _sql(s.env['db'],"UPDATE graph_node_attempts SET integration_status='conflict',integration_reason='merge_session:1' WHERE attempt_id=?",(s.aid,))
    monkeypatch.setattr(result_merge,'_session_open',lambda _:opened)
    monkeypatch.setattr(settle,'_needs_intervention',lambda *a:False)
    monkeypatch.setattr(result_merge,'_group_root',lambda _:(None,None,None))
    merging=Mock(side_effect=AssertionError('must never remerge an aborted conflict'))
    monkeypatch.setattr(result_merge,'_merge',merging)
    result_merge._retry_released('flowgate',FA,set())
    row=workspace.attempt(s.aid)
    if opened:
        assert row['integration_status']=='conflict'
    else:
        assert (row['integration_status'],row['integration_reason'])==('failed','conflict_aborted')
        assert run_store.run_by_id(s.rid)['status']!='running'
        if status=='running':assert run_store.run_by_id(s.rid)['idle_reason']=='merge_conflict'
    merging.assert_not_called()
    observe(s,'Q3')


def test_stopping_finished_uses_stop_only(scene,monkeypatch):
    s=scene;expired(s,monkeypatch,'a')
    _sql(s.env['db'],"UPDATE graph_runs SET status='stopping' WHERE run_id=?",(s.rid,))
    lost=Mock(wraps=ledger.mark_lost)
    monkeypatch.setattr(ledger,'mark_lost',lost)
    supervisor.tick();supervisor.tick()
    assert run_store.run_by_id(s.rid)['status']=='idle'
    assert run_store.run_by_id(s.rid)['idle_reason']=='stopped'
    lost.assert_not_called()
    assert not s.calls


def test_live_launcher_not_replayed(scene,monkeypatch):
    s=scene;scheduling.admit(s.eid,'launcher',FA)
    monkeypatch.setattr(ai_invoke_service,'get_run_record',lambda _:{'status':'running'})
    supervisor.tick();supervisor.tick();execution.recover_execution(s.eid)
    assert ledger.get(s.eid)['ai_run_id']=='launcher'
    assert not s.calls


def test_refill_duplicate_admission(scene,monkeypatch):
    s=scene
    # Both committed refill callbacks already exist when the first launcher claims.
    ctx=SimpleNamespace(run=run_store.run_by_id(s.rid),run_id=s.rid,flow_id=FA,jobs=[])
    scheduling.queue_requests(ctx)
    assert len(ctx.jobs)==1
    scheduling.admit(s.eid,'original',FA)
    monkeypatch.setattr(ai_invoke_service,'get_run_record',lambda _:{'status':'running'})
    supervisor.tick();supervisor.tick()
    assert REAL_ACQUIRE(s.eid,'original')
    ctx.jobs[0]()
    assert ledger.get(s.eid)['ai_run_id']=='original'
    assert not s.calls
    assert len(snapshot(s)['executions'])==1


@pytest.mark.parametrize('gui',[False,True])
def test_slot_released_refills_existing_request(scene,monkeypatch,gui):
    s=scene
    _sql(s.env['db'],"UPDATE groups SET graph_execution_limit=1 WHERE group_id=?",(FA,))
    # A predecessor owns the only slot; the requested successor keeps its identity.
    run_store._affected("INSERT INTO graph_node_runs(run_id,node_key,status,updated_at) VALUES (?, 'prior', 'open', ?)",[s.rid,ledger.now()])
    prior_attempt=run_store.open_attempt(s.rid,'prior',1)
    predecessor=ledger.request(prior_attempt,s.rid,'review',{})
    token=ledger.acquire(predecessor,'prior')
    with pytest.raises(Exception):scheduling.admit(s.eid,'new',FA)
    execution.finish({'graph_exec_id':predecessor,'graph_lease_token':token,'run_id':'prior','group_id':FA,'cancel_event':threading.Event()})
    if gui:execution.settle(FA)
    assert len(s.calls)==1
    assert ledger.get(s.eid)['status']=='running'


def test_live_launcher_waiting_for_slot_acquires_once(scene,monkeypatch):
    s=scene;scheduling.admit(s.eid,'launcher',FA)
    monkeypatch.setattr(ai_invoke_service,'get_run_record',lambda _:{'status':'running'})
    _sql(s.env['db'],"UPDATE groups SET graph_execution_limit=1 WHERE group_id=?",(FA,))
    # Direct ledger acquisition represents the already admitted predecessor.
    run_store._affected("INSERT INTO graph_node_runs(run_id,node_key,status,updated_at) VALUES (?, 'prior', 'open', ?)",[s.rid,ledger.now()])
    prior_attempt=run_store.open_attempt(s.rid,'prior',1)
    predecessor=ledger.request(prior_attempt,s.rid,'review',{})
    token=ledger.acquire(predecessor,'prior')
    assert REAL_ACQUIRE(s.eid,'launcher') is None
    ledger.end(predecessor,token)
    assert REAL_ACQUIRE(s.eid,'launcher')
    supervisor.tick();supervisor.tick()
    assert not s.calls


def test_paused_and_hold_are_not_automatic_refill(scene,monkeypatch):
    s=scene
    _sql(s.env['db'],"UPDATE graph_runs SET status='paused' WHERE run_id=?",(s.rid,))
    supervisor.tick();supervisor.tick()
    assert not s.calls
    _sql(s.env['db'],"UPDATE graph_runs SET status='running' WHERE run_id=?",(s.rid,))
    monkeypatch.setattr(result_merge,'holding',lambda _:True)
    supervisor.tick();supervisor.tick()
    assert not s.calls
    monkeypatch.setattr(result_merge,'holding',lambda _:False)
    supervisor.tick()
    assert not s.calls
    supervisor.tick()
    assert len(s.calls)==1


def test_time_machine_finish_refills_after_commit(scene,monkeypatch):
    s=scene
    from flowgate.graph_time_machine import operation
    monkeypatch.setattr(operation.git_revert,'apply',lambda *a,**kw:{'status':operation.git_revert.OK,'reverts':[]})
    monkeypatch.setattr(operation.rewind,'rewind',lambda *a:{'invalidated_attempt_ids':[]})
    original=execution.settle
    def outside(flow):
        from modules.flow_gate.db.connection import in_transaction
        from modules.flow_gate.graph_workflow.services.flow_guard import held_guard
        assert not in_transaction() and held_guard() is None
        original(flow)
    monkeypatch.setattr(execution,'settle',outside)
    result=operation._finish(FA,s.rid,'test-operation',{'node_key':s.key,'commits':[]},None)
    assert result['operation_status']==operation.journal.COMPLETED
    assert len(s.calls)==1


def test_state_change_requires_two_fresh_ticks(scene):
    s=scene
    supervisor.tick()
    assert not s.calls
    _sql(s.env['db'],"UPDATE graph_attempt_runs SET lease_touch_seq=lease_touch_seq+1 WHERE exec_id=?",(s.eid,))
    supervisor.tick()
    assert not s.calls
    supervisor.tick()
    assert len(s.calls)==1


def test_live_provider_cannot_launch_a_replacement(scene,monkeypatch):
    s=scene;r=expired(s,monkeypatch,'L1')
    assert execution.launch(r) is False
    assert ledger.get(s.eid)['status']=='running'
    assert not s.calls


def test_source_recovery_rules_unchanged():
    import inspect
    stage=inspect.getsource(supervisor._recover_graph)
    assert all(word not in stage for word in ('Thread(', 'Timer(', 'sleep(', 'mark_lost(', 'invalidate_attempt(', 'reset_node('))
    assert inspect.getsource(supervisor.tick).count('("graph", _recover_graph)')==1
    assert 'recover_execution' not in inspect.getsource(execution.settle)
    assert 'mark_lost' not in inspect.getsource(settle.settle_plan)+inspect.getsource(settle.settle_run)


@pytest.mark.parametrize('gui',[False,True])
def test_ledgerless_integration_job_failure(scene,monkeypatch,gui):
    s=scene
    ledger.cancel(s.eid,None,'requested')
    _sql(s.env['db'],"UPDATE graph_node_runs SET status='review',result_doc_id=? WHERE run_id=? AND node_key=?",('injected-result',s.rid,s.key))
    _sql(s.env['db'],"UPDATE graph_node_attempts SET integration_status='pending' WHERE attempt_id=?",(s.aid,))
    with monkeypatch.context() as injection:
        injection.setattr(result_merge,'request',Mock(side_effect=RuntimeError('post-commit integration request')))
        run_service.run_jobs([lambda:result_merge.request(FA)])
    assert workspace.attempt(s.aid)['integration_status']=='pending'
    # Not S1: durable pending integration exists. Existing lock-release drain resumes it.
    monkeypatch.setattr(result_merge.result_commit,'commit_result',lambda *a:{'skipped_reason':'injected_commit_failure'})
    if gui:execution.settle(FA)
    result_merge._retry_released('flowgate',FA,set())
    assert workspace.attempt(s.aid)['integration_status']=='failed'
    assert run_store.run_by_id(s.rid)['status']=='idle'
    assert not s.calls
    observe(s,'R6-d')
