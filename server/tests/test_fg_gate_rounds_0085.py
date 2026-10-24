"""0085 T#4: D0007 §8-3, 8, 9 and the Time Machine portion of 18 only.

Real product routes/SQLite; publish + rewind uses a local bare origin. Faults at
Git and at the last DB annotation prove failed rewinds retain the recovery flag.
These are TR self-checks, not a TS/TSR gate.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_fg_card_settings import FA, _h, _sql, db_path, edge, env, node, setting
from test_fg_gate_intake_0085 import (
    LEE, _attempt, _confirm_next, _confirmed, _current, _decide, _decisions, _finish,
    _gate_events, _jobs, _linear, _nodes, _start, _states, product)
from test_fg_gate_publish_0085 import (
    _act, _confirm, _decision, _effects, _evidence, _gate, _git, _job_row,
    _parked_after_merge, _rev, _run_job, _side_commit, _version, _work, git_env)


@pytest.fixture(autouse=True)
def time_machine_routes(product):
    from flowgate.graph_time_machine import routes
    product['client'].app.include_router(routes.router)


def _rewind(env, run_id, key, expect=200):
    res = env['client'].post(f'/api/v1/runs/{run_id}/nodes/{key}/time-machine', json={}, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _inspect(key):
    from flowgate.graph_time_machine.service import inspect
    return inspect(FA, key, 'en')


def _resume(env, run_id, mode='full', expect=200):
    res = env['client'].post(f'/api/v1/runs/{run_id}/resume', json={'mode': mode}, headers=_h())
    assert res.status_code == expect, res.text
    return res.json()


def _history(env, run_id, key):
    res = env['client'].get(f'/api/v1/runs/{run_id}/nodes/{key}/gate-decisions', headers=_h())
    assert res.status_code == 200, res.text
    return res.json()['items']


def _assert_link(env, before, after, operation_id):
    prior = _decision(env, before)
    latest = _decision(env, after)
    assert prior['time_machine_ref'] == latest['time_machine_ref'] == operation_id
    assert prior['superseded_by_decision_id'] == after
    assert latest['previous_decision_id'] == before
    assert latest['attempt_id'] != prior['attempt_id']
    assert prior['status'] in ('succeeded', 'rejected', 'abandoned')


def test_rejected_gate_without_upstream_work_gets_a_new_attempt_and_preserves_history(product):
    env = product
    g, t = _linear(env)
    run_id = _start(env)['run_id']
    old = _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'reject', 'comment': 'redo'})['decision']
    assert _attempt(env, run_id, g)['status'] == 'ended'
    assert _inspect(g)['allowed']
    tm = _rewind(env, run_id, g)['time_machine']
    assert set(tm['impact']['node_keys']) == {g, t}
    assert _states(env)[g] == _states(env)[t] == 'pending'
    assert _decision(env, old['decision_id'])['status'] == 'rejected'
    _resume(env, run_id)
    assert _states(env)[g] == 'open' and _states(env)[t] == 'pending'
    new = _decide(env, run_id, g, {'decision': 'confirm', 'request_id': 'confirm'})['decision']
    _assert_link(env, old['decision_id'], new['decision_id'], tm['operation_id'])
    assert _states(env)[t] == 'open'
    assert [e['cause'] for e in _gate_events(env, g)] == ['gate_rejected', 'gate_confirmed']
    assert [h['comment'] for h in _history(env, run_id, g)] == ['redo', None]
    before = _facts(env)
    replay = _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'reject', 'comment': 'redo'})
    assert replay['decision']['decision_id'] == old['decision_id']
    assert _facts(env) == before


def test_upstream_rework_resets_rejected_gate_and_preserves_the_unrelated_branch(product):
    env = product
    keys = _confirmed(env, [node('tmp_r', 'R'), node('tmp_a', 'T', assignee=LEE),
                           node('tmp_g', None, 'gate'), node('tmp_t', 'T', assignee=LEE),
                           node('tmp_b', 'T', assignee=LEE)],
                      [edge('tmp_1', 'tmp_r', 'tmp_a'), edge('tmp_2', 'tmp_a', 'tmp_g'),
                       edge('tmp_3', 'tmp_g', 'tmp_t'), edge('tmp_4', 'tmp_r', 'tmp_b')])
    a, g, t, b = (keys['tmp_' + k] for k in ('a', 'g', 't', 'b'))
    run_id = _start(env)['run_id']
    _finish(env, a, 90)
    _resume(env, run_id)
    old = _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'r'})['decision']
    _finish(env, b, 91)
    stable = _sql(env['db'], 'SELECT * FROM graph_node_runs WHERE node_key = ?', (b,))
    _confirm_next(b, 'gn_extra')
    refused = _resume(env, run_id, expect=409)
    assert refused['error']['params']['reason'] == 'gate_rejected'
    tm = _rewind(env, run_id, a)['time_machine']
    assert set(tm['impact']['node_keys']) == {a, g, t}
    assert _sql(env['db'], 'SELECT * FROM graph_node_runs WHERE node_key = ?', (b,)) == stable
    _resume(env, run_id)
    assert _states(env)[a] == 'open' and _states(env)[g] == _states(env)[t] == 'pending'
    _finish(env, a, 92)
    _resume(env, run_id)
    new = _decide(env, run_id, g, {'decision': 'confirm', 'request_id': 'c'})['decision']
    _assert_link(env, old['decision_id'], new['decision_id'], tm['operation_id'])
    assert _states(env)[t] == 'open'


def _facts(env):
    return {table: _sql(env['db'], 'SELECT * FROM ' + table) for table in (
        'graph_node_runs', 'graph_node_attempts', 'gate_decisions', 'gate_decision_effects',
        'gate_archive_checkpoints', 'operation_job', 'documents')}


def test_completed_commands_preserve_results_and_revision_adoption_only_opens_added_pending(product):
    from flowgate.graph_run.editing import protect_definition
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.graph_workflow.store import definition_store, run_store
    env = product
    g, t = _linear(env)
    run_id = _start(env)['run_id']
    _decide(env, run_id, g, {'decision': 'confirm', 'comment': 'keep', 'request_id': 'done'})
    result = _finish(env, t, 90)
    assert _current(env)['idle_reason'] == 'completed'
    facts = _facts(env)
    gate_events = _gate_events(env, g)
    run_fields = 'run_id, revision_no, status, idle_reason, run_mode, started_at, finished_at, stopped_at'
    run_facts = _sql(env['db'], 'SELECT ' + run_fields + ' FROM graph_runs')
    for mode in ('full', 'step'):
        assert _start(env, mode)['run_id'] == run_id
        _resume(env, run_id, mode)
        assert _facts(env) == facts
        assert _sql(env['db'], 'SELECT ' + run_fields + ' FROM graph_runs') == run_facts
        assert _gate_events(env, g) == gate_events
    before = definition_store.revision(FA, 1)
    for mutation in ('gate', 'edge'):
        nodes, edges = copy.deepcopy(before['nodes']), copy.deepcopy(before['edges'])
        if mutation == 'gate':
            nodes = [n for n in nodes if n['node_key'] != g]
        else:
            edges = [e for e in edges if e['target'] != g]
        with pytest.raises(GraphApiError) as error:
            protect_definition(FA, nodes, edges)
        assert error.value.code == 'graph_node_protected'
    for job in _sql(env['db'], "SELECT job_id FROM operation_job WHERE kind = 'gate_cleanup' AND status = 'pending'"):
        assert _run_job(job['job_id'])[0]['status'] == 'succeeded'
    facts = _facts(env)
    _confirm_next(t, 'gn_extra')
    after = _start(env)
    assert after['run_id'] == run_id and after['revision_no'] == 2
    assert _states(env)['gn_extra'] == 'open'
    assert _states(env)[g] == _states(env)[t] == 'done'
    assert _nodes(after)[t]['result_doc_id'] == result
    assert _sql(env['db'], 'SELECT * FROM gate_decisions') == facts['gate_decisions']
    assert _sql(env['db'], 'SELECT * FROM operation_job') == facts['operation_job']


@pytest.mark.parametrize('status', ['pending', 'running', 'recovery_required', 'succeeded', 'cancelled', 'failed'])
def test_time_machine_excludes_terminal_gate_jobs_and_blocks_unfinished_jobs(product, status):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_store
    env = product
    g, _t = _linear(env)
    run_id = _start(env)['run_id']
    _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'reject'})
    with get_store().transaction():
        job = job_store.insert_gate_publish('flowgate', {'decision_id': 'fixture', 'group_id': FA,
                                            'run_id': run_id, 'node_key': g}, group_id=FA, target_key=g)
    _sql(env['db'], 'UPDATE operation_job SET status = ? WHERE job_id = ?', (status, job['job_id']))
    assessed = _inspect(g)
    terminal = status in ('succeeded', 'cancelled', 'failed')
    assert assessed['allowed'] is terminal
    if not terminal:
        assert 'gate_jobs_in_progress' in {r['code'] for r in assessed['reasons']}
        before = _facts(env)
        _rewind(env, run_id, g, expect=409)
        assert _facts(env) == before
    else:
        _rewind(env, run_id, g)


def test_unfinished_job_blocks_revision_and_protects_its_pending_gate_and_inputs(product):
    from flowgate.graph_run.editing import protect_definition
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.graph_workflow.store import definition_store
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import job_store
    env = product
    g, t = _linear(env)
    run_id = _start(env)['run_id']
    _decide(env, run_id, g, {'decision': 'reject', 'request_id': 'reject'})
    _rewind(env, run_id, g)
    _confirm_next(t, 'gn_extra')
    with get_store().transaction():
        job = job_store.insert_gate_publish('flowgate', {'decision_id': 'fixture', 'group_id': FA,
                                           'run_id': run_id, 'node_key': g}, group_id=FA, target_key=g)
    out = _resume(env, run_id, expect=409)
    assert out['error']['code'] == 'graph_revision_migration_blocked'
    assert out['error']['params']['reason'] == 'gate_jobs_in_progress'
    before = definition_store.revision(FA, 1)
    for remove_node in (False, True):
        nodes = [n for n in before['nodes'] if not remove_node or n['node_key'] != g]
        edges = [e for e in before['edges'] if e['target'] != g]
        with pytest.raises(GraphApiError) as error:
            protect_definition(FA, nodes, edges)
        assert error.value.code == 'graph_node_protected'
    _sql(env['db'], "UPDATE operation_job SET status = 'cancelled' WHERE job_id = ?", (job['job_id'],))
    assert _resume(env, run_id)['run']['revision_no'] == 2


@pytest.mark.parametrize('keep_branch', [True, False], ids=['kept_slot', 'released_slot'])
def test_done_gate_rewinds_only_internal_integration_and_preserves_publish_and_archives(git_env, keep_branch):
    from modules.flow_gate.db import tr_commit_ledger
    from modules.flow_gate.services import git_service as svc
    r, env = git_env, git_env['env']
    keys = _confirmed(env, [node('tmp_r', 'R'), node('tmp_g', None, 'gate'),
                           node('tmp_t', 'T', assignee=LEE), node('tmp_end', None, 'gate')],
                      [edge('tmp_1', 'tmp_r', 'tmp_g'), edge('tmp_2', 'tmp_g', 'tmp_t'),
                       edge('tmp_3', 'tmp_t', 'tmp_end')])
    g, t, end = (keys['tmp_' + key] for key in ('g', 't', 'end'))
    run_id = _start(env)['run_id']
    _gate(env, g, merge_mode='merge', merge_target='origin', push=True, archive=True, keep_branch=keep_branch)
    sha = _work(r, 'gate-result.txt')
    out = _confirm(env, run_id, g, 'first')
    did = out['decision']['decision_id']
    assert _run_job(out['job']['job_id'])[0]['status'] == 'succeeded'
    # Attribute one downstream integration, exactly as result_merge records it.
    start = _rev(r['wt'], 'HEAD')
    result_sha = _side_commit(r, start, 'tail.txt')
    _git(['merge', '--no-ff', '-m', 'tail integration', result_sha], cwd=r['wt'])
    integrated = _rev(r['wt'], 'HEAD')
    result_doc = _finish(env, t, 90)
    aid = _attempt(env, run_id, t)['attempt_id']
    _sql(env['db'], "UPDATE graph_node_attempts SET input_source = 'predecessors', input_commit = ?, "
                    "result_commit = ?, integrated_commit = ?, integration_status = 'integrated', "
                    "input_attempt_ids = ? WHERE attempt_id = ?",
         (start, result_sha, integrated, json.dumps([_decision(env, did)['attempt_id']]), aid))
    tr_commit_ledger.record_commit(group_id=FA, doc_id=result_doc, commit_sha=result_sha,
                                  commit_subject='tail', attempt_id=aid, node_key=t)
    # The completed run's actual end HEAD is newer than the first gate's archive. A terminal merge makes
    # that real end reachable, allowing normal keep_branch=false cleanup to release both branch and worktree.
    _resume(env, run_id)
    _gate(env, end, merge_mode='merge', merge_target='origin', push=True, archive=False, keep_branch=False)
    final = _confirm(env, run_id, end, 'end')
    assert _run_job(final['job']['job_id'])[0]['status'] == 'succeeded'
    assert _current(env)['idle_reason'] == 'completed'
    external = _rev(r['bare'], 'refs/heads/main')
    for job in _sql(env['db'], "SELECT job_id FROM operation_job WHERE kind = 'gate_cleanup' AND status = 'pending'"):
        assert _run_job(job['job_id'])[0]['status'] == 'succeeded'
    record = _sql(env['db'], 'SELECT * FROM gate_cleanup_records')[-1]
    assert record['end_head_sha'] == integrated and integrated != sha
    if keep_branch:
        assert record['result'] == 'retained' and r['wt'].is_dir()
    else:
        assert record['result'] == 'deleted' and record['basis_kind'] == 'merged_target'
        assert not r['wt'].exists() and svc.db_git.get_state(FA)['worktree_registered'] == 0
        assert _rev(r['base'], f"refs/heads/{r['branch']}") is None
    checkpoints = _sql(env['db'], 'SELECT * FROM gate_archive_checkpoints')
    effects = _effects(env, did)
    assessed = _inspect(g)
    assert assessed['allowed']
    history = assessed['impact']['gate_decisions']
    original = next(entry for entry in history if entry['decision_id'] == did)
    assert original['external_effects'] == 'retained' and original['archive_action'] == 'preserved'
    tm = _rewind(env, run_id, g)['time_machine']
    assert [c['commit_sha'] for c in tm['reverts']] == [integrated]
    revert = tm['reverts'][0]['revert_commit']
    assert _rev(r['wt'], revert + '^1') == integrated   # restored at actual end HEAD, never the old archive
    assert svc.db_git.get_state(FA)['worktree_registered'] == 1
    assert not (r['wt'] / 'tail.txt').exists() and (r['wt'] / 'gate-result.txt').exists()
    assert _rev(r['base'], 'refs/heads/main') == _rev(r['bare'], 'refs/heads/main') == external
    assert _sql(env['db'], 'SELECT * FROM gate_archive_checkpoints') == checkpoints
    assert _effects(env, did) == effects
    assert _states(env)[g] == _states(env)[t] == 'pending'
    sha2 = _work(r, 'gate-result.txt', 'reworked\n')
    assert sha2 != sha
    _resume(env, run_id)
    new = _confirm(env, run_id, g, 'second')
    assert _run_job(new['job']['job_id'])[0]['status'] == 'succeeded'
    new_id = new['decision']['decision_id']
    _assert_link(env, did, new_id, tm['operation_id'])
    assert _decision(env, new_id)['frozen_sha'] == sha2
    assert len(_sql(env['db'], 'SELECT * FROM gate_archive_checkpoints')) == 2
    assert _rev(r['base'], f'refs/flowgate/gate-archive/{did}') == sha


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
