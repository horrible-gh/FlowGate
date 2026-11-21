"""Independently selectable scenarios; shared fixtures remain in test_fg_execution_persistence_0156.py."""
from __future__ import annotations

from test_fg_execution_persistence_0156 import (
    FA,
    Mock,
    Path,
    REAL_ACQUIRE,
    SimpleNamespace,
    _h,
    _run_one_card,
    _sql,
    ai_invoke_service,
    db_path,
    decided,
    env,
    execution,
    expired,
    isolate_unowned_sweep_state,
    json,
    ledger,
    legacy_cards,
    node_dispatch,
    observe,
    pytest,
    pytestmark,
    result_merge,
    run_apply,
    run_service,
    run_store,
    scene,
    scheduling,
    settle,
    snapshot,
    stop,
    supervisor,
    terminal,
    threading,
    workspace,
)

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
