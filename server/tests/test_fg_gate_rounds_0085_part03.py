"""Independently selectable scenarios; shared fixtures remain in test_fg_gate_rounds_0085.py."""
from __future__ import annotations

from tests.test_fg_gate_rounds_0085 import (
    FA,
    LEE,
    Path,
    _act,
    _assert_link,
    _attempt,
    _confirm,
    _confirm_next,
    _confirmed,
    _current,
    _decide,
    _decision,
    _decisions,
    _effects,
    _evidence,
    _facts,
    _finish,
    _gate,
    _gate_events,
    _git,
    _h,
    _history,
    _inspect,
    _job_row,
    _jobs,
    _linear,
    _nodes,
    _parked_after_merge,
    _resume,
    _rev,
    _rewind,
    _run_job,
    _side_commit,
    _sql,
    _start,
    _states,
    _version,
    _work,
    copy,
    db_path,
    edge,
    env,
    git_env,
    json,
    node,
    product,
    pytest,
    setting,
    sys,
    time_machine_routes,
)

@pytest.mark.parametrize('failure', ['git', 'db'])
def test_abandoned_round_retains_reset_on_failure_then_allows_settings_revision_and_new_confirm(
        git_env, monkeypatch, failure):
    from flowgate.gate_decisions import repository as repo, rounds
    from flowgate.graph_time_machine import git_revert
    from modules.flow_gate.graph_workflow.store import definition_store
    from flowgate.graph_run.editing import protect_definition
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    r = git_env
    env, g, t, run_id, did, job_id, sha, _ = _parked_after_merge(r, monkeypatch)
    external = _rev(r['base'], 'refs/heads/main')
    repo.record_effect(did, 'push', status=repo.UNKNOWN)
    _act(env, run_id, g, did, 'resolve', {'request_id': 'abandon', 'expected_decision_version': _version(env, did),
         'resolution': 'abandon', 'reason': 'remote unavailable', **_evidence(env, g)}, 200)
    assert _job_row(env, job_id)['status'] == 'cancelled'
    assert _current(env)['idle_reason'] == 'blocked_recovery'
    # Stop and a plain command keep the marker and do not open dependents.
    stop = env['client'].post(f'/api/v1/runs/{run_id}/stop', json={}, headers=_h())
    assert stop.status_code == 200, stop.text
    assert _current(env)['idle_reason'] == 'stopped'
    _resume(env, run_id)
    assert _decision(env, did)['recovery_reset_required'] == 1 and _states(env)[t] == 'pending'
    assert setting(env, FA, g, 'merge_mode', 'hold', expect=409)['error']['code'] == 'gate_settings_locked'
    before = definition_store.revision(FA, 1)
    with pytest.raises(GraphApiError):
        protect_definition(FA, [n for n in before['nodes'] if n['node_key'] != g], before['edges'])
    with pytest.raises(GraphApiError):
        protect_definition(FA, before['nodes'], [e for e in before['edges'] if e['target'] != g])
    _confirm_next(t, 'gn_extra')
    assert _resume(env, run_id, expect=409)['error']['params']['reason'] == 'gate_recovery_reset_required'
    assessed = _inspect(g)
    assert assessed['allowed']
    # No remote read is needed for preview or rewind, even though effects remain unknown.
    assert any(h['unknown_effects'] for h in assessed['impact']['gate_decisions'])
    effects = _effects(env, did)
    events = _gate_events(env, g)
    if failure == 'git':
        original = git_revert.apply
        monkeypatch.setattr(git_revert, 'apply', lambda *a, **k: {
            'status': git_revert.BLOCKED, 'reason': 'injected', 'reverts': [], 'compensated': True})
    else:
        original = repo.move
        def fail_after_annotation(*args, **kwargs):
            changed = original(*args, **kwargs)
            if kwargs.get('recovery_reset_required') is False:
                raise RuntimeError('injected after flag clear, before commit')
            return changed
        monkeypatch.setattr(repo, 'move', fail_after_annotation)
    _rewind(env, run_id, g, expect=409)
    assert _decision(env, did)['recovery_reset_required'] == 1
    assert _decision(env, did)['time_machine_ref'] is None
    assert _effects(env, did) == effects and _gate_events(env, g) == events
    if failure == 'git':
        monkeypatch.setattr(git_revert, 'apply', original)
        tm = _rewind(env, run_id, g)['time_machine']
    else:
        monkeypatch.setattr(repo, 'move', original)
        from flowgate.graph_time_machine.operation import recover
        recover()
        tm = {'operation_id': _decision(env, did)['time_machine_ref']}
    assert _decision(env, did)['recovery_reset_required'] == 0
    assert _sql(env['db'], 'SELECT status FROM graph_node_attempts WHERE attempt_id = ?',
                (_decision(env, did)['attempt_id'],))[0]['status'] == 'invalidated'
    assert _rev(r['base'], 'refs/heads/main') == external
    assert _rev(r['base'], f'refs/flowgate/gate-recovery/{did}') == sha
    setting(env, FA, g, 'merge_mode', 'hold')
    assert _resume(env, run_id)['run']['revision_no'] == 2
    assert _states(env)[g] == 'open' and _states(env)[t] == 'pending'
    new = _confirm(env, run_id, g, 'new-round', expect=200)['decision']
    _assert_link(env, did, new['decision_id'], tm['operation_id'])
    assert _states(env)[t] == 'open'
    assert _decision(env, did)['status'] == 'abandoned' and _effects(env, did) == effects
    assert _nodes(_current(env))[g]['gate']['archived'] is False
def test_later_round_rewind_preserves_the_legacy_predecessors_original_time_machine_reference(product):
    env = product
    g, _t = _linear(env)
    run_id = _start(env)['run_id']
    old = _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'legacy'})['decision']
    # The synthetic attempt identity used by pre-migration-151 decision intake.
    legacy = f'pre151:{run_id}:{g}'
    _sql(env['db'], 'UPDATE gate_decisions SET attempt_id = ?, valid_attempt_key = ? WHERE decision_id = ?',
         (legacy, legacy, old['decision_id']))
    first = _rewind(env, run_id, g)['time_machine']['operation_id']
    _resume(env, run_id)
    second = _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'second'})['decision']
    assert second['previous_decision_id'] == old['decision_id']
    before = _decision(env, old['decision_id'])
    assert before['time_machine_ref'] == first
    next_operation = _rewind(env, run_id, g)['time_machine']['operation_id']
    assert next_operation != first
    assert _decision(env, old['decision_id']) == before
    assert _decision(env, second['decision_id'])['time_machine_ref'] == next_operation
def test_new_round_inherits_predecessor_after_no_effect_failure_cancel_and_settings_patch(git_env):
    r, env = git_env, git_env['env']
    g, t = _linear(env)
    run_id = _start(env)['run_id']
    prior = _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'old-round'})['decision']['decision_id']
    tm = _rewind(env, run_id, g)['time_machine']['operation_id']
    _resume(env, run_id)
    attempt = _attempt(env, run_id, g)
    _work(r, 'new-round.txt')
    _gate(env, g, merge_mode='merge')
    # Fails before any merge/push/archive effect; cancel must keep this new attempt open.
    (r['base'] / 'README.md').write_text('uncommitted base change\n', encoding='utf-8')
    accepted = _confirm(env, run_id, g, 'new-intent')
    canceled_id = accepted['decision']['decision_id']
    _assert_link(env, prior, canceled_id, tm)
    job, _ = _run_job(accepted['job']['job_id'])
    assert job['status'] == 'recovery_required' and job['last_error_code'] == 'base_dirty'
    assert _decision(env, canceled_id)['status'] == 'failed'
    _act(env, run_id, g, canceled_id, 'cancel', {'request_id': 'cancel-no-effect',
         'expected_decision_version': _version(env, canceled_id)}, 200)
    canceled = _decision(env, canceled_id)
    assert canceled['status'] == 'canceled'
    assert _attempt(env, run_id, g) == attempt and _states(env)[g] == 'open'
    assert _states(env)[t] == 'pending'
    setting(env, FA, g, 'merge_mode', 'hold')
    fresh = _confirm(env, run_id, g, 'replacement', expect=200,
                     expected_attempt_id=attempt['attempt_id'])['decision']['decision_id']
    _assert_link(env, prior, fresh, tm)
    assert _decision(env, fresh)['status'] == 'succeeded'
    assert _decision(env, canceled_id) == canceled
    assert _states(env)[t] == 'open'
    # Replaying the canceled request returns its history without stealing the effective replacement link.
    _confirm(env, run_id, g, 'new-intent', expect=200)
    _assert_link(env, prior, fresh, tm)
def test_legacy_impact_distinguishes_reject_without_effects_from_confirm_unknown(product):
    from test_fg_gate_intake_0085 import _legacy_event
    env = product
    g, _t = _linear(env)
    run_id = _start(env)['run_id']
    _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'current'})
    for cause in ('gate_rejected', 'gate_confirmed'):
        _legacy_event(env, run_id, g, cause, LEE['user_id'], 'legacy history')
    assessed = _inspect(g)
    assert assessed['allowed']
    legacy = {e['decision']: e for e in assessed['impact']['gate_decisions'] if e['source'] == 'legacy_event'}
    assert set(legacy) == {'reject', 'confirm'}
    assert (legacy['reject']['external_effects'], legacy['reject']['unknown_effects']) == ('none', False)
    assert (legacy['confirm']['external_effects'], legacy['confirm']['unknown_effects']) == ('unknown', True)
    for entry in legacy.values():
        assert entry['effects'] is None and entry['archive_action'] == 'none'
        assert entry['archive_id'] is None and entry['frozen_sha'] is None and entry['target_sha'] is None
