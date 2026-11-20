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
@pytest.mark.parametrize('resume', ['sweeper'])
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
