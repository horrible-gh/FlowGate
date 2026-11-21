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
