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

@pytest.mark.parametrize('intent', ['stop'])
@pytest.mark.parametrize('resume', ['sweeper'])
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
