"""8, 9 and the Time Machine portion of 18 only.

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
