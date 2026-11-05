"""0132 T#3 / NR §5.3-9(a-i): real bare origin, Runner/Sweeper and mixed human waits.

Each resume checks writer locks/wire, successful successor opening within full command, and untouched G2/W waits.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parent/'flowgate'))
from test_fg_card_settings import FA, _h, _sql, db_path, edge, env, node
from test_fg_gate_intake_0085 import LEE, _confirmed, _current, _nodes, _start, _states, product
from test_fg_gate_publish_0085 import (
    PROJECT, _act, _confirm, _decision, _effects, _gate, _git, _rev, _run_job, _version, _work, git_env)
from test_fg_gate_push_sync_0132 import _advance, _due, pushes


def mixed(r,wait):
    e=r['env']
    ns=[node('tmp_r','R'), node('tmp_g',None,'gate'),node('tmp_s','T',assignee=LEE)]
    es=[edge('tmp_rg','tmp_r','tmp_g'),edge('tmp_gs','tmp_g','tmp_s')]
    if wait=='waiting_confirm':
        ns.append(node('tmp_other',None,'gate'))
    else:
        ns.append(node('tmp_other','N',assignee=LEE))
    es.append(edge('tmp_ro','tmp_r','tmp_other'))
    keys=_confirmed(e,ns,es)
    rid=_start(e,'full')['run_id']
    return e,keys['tmp_g'],keys['tmp_s'],keys['tmp_other'],rid


def assert_locked(e,rid,g,s):
    from flowgate.gate_decisions import legacy_adapter as legacy
    from flowgate.graph_run import editing,hold
    from flowgate.graph_time_machine import service
    wire=_current(e)
    assert _nodes(wire)[g]['gate']['git_active'] is True
    def check(run,jobs):
        for call in (lambda:editing.require_editable(FA),lambda:hold.require_settable(FA,s,True),lambda:service.require_safe(FA,g)):
            with pytest.raises(legacy.GraphApiError) as exc: call()
            assert exc.value.code=='graph_run_locked'
            assert exc.value.params.get('reason')=='gate_git_active'
    legacy.guarded_run(rid,check)


@pytest.mark.parametrize('wait',['waiting_confirm','needs_intervention'])
@pytest.mark.parametrize('resume',['retry','sweeper'])
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


def finish_sync_review(r, e, jid, did):
    """Complete the real remote-sync review without waking through intake or explicit retry."""
    from modules.flow_gate.services import git_service as svc
    sync = _effects(e, did)['remote_sync']
    mid = json.loads(sync['evidence_json'])['merge_id']
    (r['base'] / 'shared.txt').write_text('resolved\n', encoding='utf-8')
    _git(['add', 'shared.txt'], cwd=r['base'])
    _git(['commit', '--no-edit'], cwd=r['base'])
    svc._finish_reviewed_attempt(FA, mid, PROJECT, _rev(r['base'], 'HEAD')[:7], False)


@pytest.mark.parametrize('wait', ['waiting_confirm', 'needs_intervention'])
@pytest.mark.parametrize('resume', ['retry', 'sweeper'])
def test_mixed_retry_repark_returns_to_original_wait(git_env, pushes, monkeypatch, wait, resume):
    """NR 9(e): both manual and review/Sweeper resumes may park again beside either human wait."""
    from flowgate.gate_decisions import git_adapter as git
    from modules.flow_gate.services.git import job_sweeper, job_store
    r = git_env
    e, g, s, other, rid = mixed(r, wait)
    before = _nodes(_current(e))[other]
    _work(r, 'shared.txt', 'group side\n')
    _gate(e, g, merge_mode='merge', merge_target='origin', push=True)
    body = _confirm(e, rid, g, 'repark-0132')
    did, jid = body['decision']['decision_id'], body['job']['job_id']
    if resume == 'retry':
        monkeypatch.setattr(git, 'push', lambda *a, **kw: (git.PUSH_REJECTED, 'push_rejected', {}))
    else:
        _advance(r, 'main', 'shared.txt', 'remote side\n')
    _run_job(jid)
    assert _current(e)['idle_reason'] == wait
    if resume == 'retry':
        _act(e, rid, g, did, 'retry', {'request_id': 'repark',
             'expected_decision_version': _version(e, did)}, 202)
    else:
        finish_sync_review(r, e, jid, did)
        _due(jid)
        job_sweeper.step_recovery_required_retry()
        assert job_store.get_job(jid)['status'] == 'pending'
    seen = []
    def reject(*a, **kw):
        assert_locked(e, rid, g, s)
        assert _sql(e['db'], 'SELECT status FROM graph_runs WHERE run_id=?', (rid,))[0]['status'] == 'running'
        seen.append(True)
        return git.PUSH_REJECTED, 'push_rejected', {}
    monkeypatch.setattr(git, 'push', reject)
    _run_job(jid)
    assert seen
    wire = _current(e)
    gate = _nodes(wire)[g]['gate']
    assert (wire['status'], wire['idle_reason']) == ('idle', wait)
    assert gate['parked'] and not gate['git_active'] and gate['recovery']['retryable']
    after = _nodes(wire)[other]
    assert (after['status'], after.get('attempt_id')) == (before['status'], before.get('attempt_id'))
    if wait == 'waiting_confirm':
        assert not after['gate']['decision_in_progress']
    assert _states(e)[s] == 'pending'


@pytest.mark.parametrize('intent', ['stop', 'pause', 'replace', 'tm'])
@pytest.mark.parametrize('resume', ['retry', 'sweeper'])
def test_user_intent_survives_publish_and_defers_successor(git_env, pushes, monkeypatch, intent, resume):
    """NR 9(f-h): neither retry nor an automatic claim may bypass the person's later intent."""
    from flowgate.gate_decisions import git_adapter as git, legacy_adapter as legacy
    from modules.flow_gate.services.git import job_sweeper, job_store
    r = git_env
    e, g, s, other, rid = mixed(r, 'waiting_confirm')
    _work(r, 'shared.txt', 'group side\n')
    _gate(e, g, merge_mode='merge', merge_target='origin', push=True)
    body = _confirm(e, rid, g, 'intent-0132')
    did, jid = body['decision']['decision_id'], body['job']['job_id']
    real_push = git.push
    if resume == 'retry':
        monkeypatch.setattr(git, 'push', lambda *a, **kw: (git.PUSH_REJECTED, 'push_rejected', {}))
    else:
        _advance(r, 'main', 'shared.txt', 'remote side\n')
    _run_job(jid)
    _current(e)
    if resume == 'sweeper':
        finish_sync_review(r, e, jid, did)
    if intent == 'stop':
        res = e['client'].post(f'/api/v1/runs/{rid}/stop', json={}, headers=_h())
        assert res.status_code == 200, res.text
        assert _current(e)['idle_reason'] == 'stopped'
    elif intent != 'pause':
        def event(run, jobs):
            ctx = legacy.load_context(run, PROJECT, 'u_lee', jobs)
            if intent == 'replace':
                legacy.run_store.set_run_command(rid, 'step', [other])
                ctx.run['run_mode'], ctx.run['step_node_keys'] = 'step', [other]
                legacy.emit_run_status(ctx, 'idle', 'idle', 'user_command')
            else:
                from flowgate.graph_time_machine import journal
                journal.emit(ctx, 'gtm_test_completed', journal.COMPLETED)
        legacy.guarded_run(rid, event)
    if resume == 'retry':
        _act(e, rid, g, did, 'retry', {'request_id': 'intent-retry',
             'expected_decision_version': _version(e, did)}, 202)
    else:
        _due(jid)
        job_sweeper.step_recovery_required_retry()
        assert job_store.get_job(jid)['status'] == 'pending'
    seen = []
    def checked(*a, **kw):
        if intent == 'pause':
            res = e['client'].post(f'/api/v1/runs/{rid}/pause', json={}, headers=_h())
            assert res.status_code == 200, res.text
        assert_locked(e, rid, g, s)
        row = _sql(e['db'], 'SELECT status FROM graph_runs WHERE run_id=?', (rid,))[0]
        assert row['status'] == ('paused' if intent == 'pause' else 'idle')
        seen.append(True)
        return real_push(*a, **kw)
    monkeypatch.setattr(git, 'push', checked)
    job, _ = _run_job(jid)
    assert job['status'] == 'succeeded', job
    assert seen and _states(e)[g] == 'done' and _states(e)[s] == 'pending'
    if intent == 'pause':
        assert _current(e)['status'] == 'paused'
    if intent == 'stop':
        assert _current(e)['idle_reason'] == 'stopped'


def test_target_moved_before_merge_parks_idle_and_stop_cancels(git_env,pushes):
    r=git_env
    e,g,s,other,rid=mixed(r,'waiting_confirm')
    _work(r,'ours.txt')
    _gate(e,g,merge_mode='merge',merge_target='origin',push=True)
    body=_confirm(e,rid,g,'target-moved-0132')
    did,jid=body['decision']['decision_id'],body['job']['job_id']
    (r['base']/'target.txt').write_text('another writer\n',encoding='utf-8')
    _git(['add','target.txt'],cwd=r['base'])
    _git(['commit','-m','advance target'],cwd=r['base'])
    job,_=_run_job(jid)
    assert (job['phase_note'],job['last_error_code'])==('gate_failed','target_moved'),job
    assert _current(e)['status']=='idle'
    res=e['client'].post(f'/api/v1/runs/{rid}/stop',json={},headers=_h())
    assert res.status_code==200,res.text
    assert _current(e)['idle_reason']=='stopped'
    assert _decision(e,did)['status']=='canceled'
