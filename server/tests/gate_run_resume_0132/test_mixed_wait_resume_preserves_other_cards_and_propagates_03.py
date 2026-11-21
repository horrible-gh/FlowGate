"""Independently selectable scenario; shared harness stays in test_fg_gate_run_resume_0132."""
from __future__ import annotations

from test_fg_gate_run_resume_0132 import (
    FA,
    LEE,
    PROJECT,
    Path,
    _act,
    _advance,
    _confirm,
    _confirmed,
    _current,
    _decision,
    _due,
    _effects,
    _gate,
    _git,
    _h,
    _nodes,
    _rev,
    _run_job,
    _sql,
    _start,
    _states,
    _version,
    _work,
    assert_locked,
    db_path,
    edge,
    env,
    finish_sync_review,
    git_env,
    json,
    mixed,
    node,
    product,
    pushes,
    pytest,
    sys,
)

@pytest.mark.parametrize('wait', ['needs_intervention'])
@pytest.mark.parametrize('resume', ['retry'])
def test_mixed_wait_resume_preserves_other_cards_and_propagates(git_env,pushes,monkeypatch,wait,resume):
    from flowgate.gate_decisions import git_adapter as git,publish
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.services.git import job_store,job_sweeper
    r=git_env
    e,g,s,other,rid=mixed(r,wait)
    other_before=_nodes(_current(e))[other]
    _work(r,'shared.txt','group side\n')
    _gate(e,g,merge_mode='merge',merge_target='origin',push=True)
    body=_confirm(e,rid,g,'mixed-0132')
    did,jid=body['decision']['decision_id'],body['job']['job_id']
    real_push=git.push
    if resume=='retry':
        monkeypatch.setattr(git,'push',lambda *a,**kw:(git.PUSH_REJECTED,'push_rejected',{}))
    else:
        _advance(r,'main','shared.txt','remote side\n')
    job,_=_run_job(jid)
    assert job['phase_note']==('gate_recovery' if resume=='retry' else 'gate_conflict'),job
    wire=_current(e)
    assert (wire['status'],wire['idle_reason'])==('idle',wait)
    assert _nodes(wire)[g]['gate']['parked'] is True
    if resume=='retry':
        _act(e,rid,g,did,'retry',{'request_id':'retry-mixed','expected_decision_version':_version(e,did)},202)
        assert _current(e)['status']=='running'
    else:
        sync=_effects(e,did)['remote_sync']
        mid=json.loads(sync['evidence_json'])['merge_id']
        (r['base']/'shared.txt').write_text('resolved\n',encoding='utf-8')
        _git(['add','shared.txt'],cwd=r['base'])
        _git(['commit','--no-edit'],cwd=r['base'])
        svc._finish_reviewed_attempt(FA,mid,PROJECT,_rev(r['base'],'HEAD')[:7],False)
        _due(jid)
        job_sweeper.step_recovery_required_retry()
        assert job_store.get_job(jid)['status']=='pending'
        # Still idle until publish.run claims; no intake or explicit retry wake can hide a missing entry wake.
        assert _sql(e['db'],'SELECT status FROM graph_runs WHERE run_id=?',(rid,))[0]['status']=='idle'
    seen=[]
    def checked(*a,**kw):
        assert_locked(e,rid,g,s)
        assert _sql(e['db'],'SELECT status FROM graph_runs WHERE run_id=?',(rid,))[0]['status']=='running'
        seen.append(True)
        return real_push(*a,**kw)
    monkeypatch.setattr(git,'push',checked)
    # _complete must also wake an idle race, even though entry already woke the run.
    complete=publish._complete
    def race(ctx,plan):
        _sql(e['db'],"UPDATE graph_runs SET status='idle',idle_reason=? WHERE run_id=?",(wait,rid))
        return complete(ctx,plan)
    monkeypatch.setattr(publish,'_complete',race)
    job,_=_run_job(jid)
    assert job['status']=='succeeded',job
    assert seen
    wire=_current(e)
    assert _states(e)[g]=='done' and _states(e)[s]=='open'
    assert any(ev['cause']=='gate_confirmed' for ev in _sql(e['db'],'SELECT cause FROM graph_run_events WHERE node_key=?',(g,)))
    after=_nodes(wire)[other]
    for key in ('status','attempt_id'):
        assert after.get(key)==other_before.get(key)
    if wait=='waiting_confirm': assert not after['gate']['decision_in_progress']
    assert (wire['status'],wire['idle_reason'])==('idle',wait)
    assert _decision(e,did)['status']=='succeeded'
