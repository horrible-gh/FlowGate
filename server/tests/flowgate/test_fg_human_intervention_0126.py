"""0126 T#2 B5/B7: real ended review assignments/ledger and human decision HTTP."""
import pytest
from test_fg_ai_roles_l2_assignment import (
    FA, A, B, X, _sql, db_path, decided, env, wired, starts, record_starts, roles, stored,
    assignments, b1, command, events, ledger, node_run, one, write_line,
)
from test_fg_card_intervention_assigned_0119 import fork_join, _read, _wire
from test_fg_command_rerequest_0124 import _spent, _run, _loops
from test_fg_document_decisions_0126 import _get, _request, _post, _rows
from test_fg_human_completion_0126 import proof
from test_fg_card_completion_0119 import completions

pytestmark = pytest.mark.usefixtures('wired','completions')


@pytest.fixture(autouse=True)
def preserve_runtime_environment():
    # Token issuance can initialize this value lazily. Restore its exact prior state.
    import os
    key = 'FLOWGATE_AGENT_PEPPER_ID'
    previous = os.environ.get(key)
    yield
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


@pytest.mark.parametrize('reason',['retry_exhausted','review_count_exhausted','review_verdict_hold'])
@pytest.mark.parametrize('mode',['full','step'])
def test_b5_b7_ended_review_overridden_once_with_history_and_command_result(env,decided,monkeypatch,starts,fork_join,reason,mode):
    a,b,c,doc,review = _spent(env,monkeypatch,starts,reason)
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.store import run_store
    from flowgate.graph_run import command as commands
    if mode=='step':
        # A real command boundary retaining the ended review as its one target;
        # do not ask an AI to re-review merely to construct the test interval.
        def step():
            ctx=run_apply.load_context(_run(),'flowgate','u_kim')
            assert run_store.set_run_status(ctx.run_id,'idle','running') == 1
            assert run_store.set_run_command(ctx.run_id,'step',[a]) == 1
            ctx.run['run_mode']='step'; ctx.run['step_node_keys']=[a]
            run_apply.emit_run_status(ctx,'idle','running','user_command')
            run_store.set_run_status(ctx.run_id,'running','idle','needs_intervention'); ctx.run['idle_reason']='needs_intervention'
            run_apply.emit_run_status(ctx,'running','idle','run_idle')
        with_flow_guard(FA,step)
    previous=assignments(env,a); before=len(starts); history=_rows(env,'document_reviews')
    body=_request(env,a); _post(env,a,body)
    _read(env)
    assert node_run(env,a)['status']=='done'
    assert node_run(env,b)['status']==('open' if mode=='full' else 'pending')
    assert len(starts)==before
    assert assignments(env,a)==previous and _rows(env,'document_reviews')==history
    assert _wire(env,a).get('intervention') is None
    if mode=='step':
        assert _run()['idle_reason']=='step_done'
        assert _run()['run_mode']=='step' and _run()['step_node_keys']==[a]
    # Full now waits on b's manual write; that distinct intervention must survive.
    if mode=='full': assert _run()['idle_reason']=='needs_intervention'
    from flowgate.graph_run.command_result import payload
    result=payload(_run()['run_id'])['command_result']
    assert a not in [item['node_key'] for item in result['blocking']]
    _post(env,a,body); _read(env); proof(env,a)


def test_b12_integration_waits_before_done_and_reuses_one_completion(env,decided,monkeypatch,starts,fork_join):
    from flowgate.graph_run import result_merge
    a,b,c,doc,review=_spent(env,monkeypatch,starts)
    attempt=review['attempt_id']
    _sql(env['db'],"UPDATE graph_node_attempts SET integration_status = 'conflict' WHERE attempt_id = ?",(attempt,))
    _post(env,a,_request(env,a))
    assert node_run(env,a)['status']=='review' and node_run(env,b)['status']=='pending'
    records=_sql(env['db'],'SELECT * FROM graph_card_completions WHERE node_key = ?',(a,))
    assert len(records)==1 and records[0]['status']=='recorded'
    _sql(env['db'],"UPDATE graph_node_attempts SET integration_status = 'integrated' WHERE attempt_id = ?",(attempt,))
    _read(env); proof(env,a)


def test_b10_record_committed_apply_fault_reconciles_once(env,decided,monkeypatch,starts,fork_join):
    from flowgate.card_completion import completion,human
    from modules.flow_gate.graph_workflow.services import result_binder
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    a,b,c,doc,review=_spent(env,monkeypatch,starts)
    refresh=human.refresh; monkeypatch.setattr(human,'refresh',lambda _flow:None)
    _post(env,a,_request(env,a))
    def record():
        ctx=run_apply.load_context(_run(),'flowgate',None)
        human.reconcile(ctx,a)
    with_flow_guard(FA,record)
    rows=_sql(env['db'],'SELECT * FROM graph_card_completions WHERE node_key = ?',(a,))
    assert len(rows)==1 and rows[0]['status']=='recorded' and node_run(env,a)['status']=='review'
    monkeypatch.setattr(human,'refresh',refresh)
    for _ in range(3): _read(env)
    proof(env,a)
