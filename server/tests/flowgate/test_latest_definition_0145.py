"""0145 T#2: pinned execution, editable latest definition and adoption parity."""
import copy
import pytest

from test_fg_revision_adoption_precheck_0010 import (
    FA, GROUP, _h, _sql, db_path, env, decided, candidate, document_write_ports,
    facts, assess, confirm_candidate,
)
from test_fg_card_settings import node
from modules.flow_gate.graph_workflow.store import definition_store, run_store


def canvas(env, view=None):
    res = env['client'].get(f'/api/v1/flows/{FA}/canvas', headers=_h(), params={'view': view} if view else {})
    assert res.status_code == 200, res.text
    return res.json()


def draft_body(record):
    return {'draft_version': 0, 'nodes': [dict(n, client_key=None) for n in record['nodes']],
            'edges': [dict(e, client_key=None) for e in record['edges']]}


def put(env, body):
    return env['client'].put(f'/api/v1/flows/{FA}/graph/draft', json=body, headers=_h())


def test_latest_definition_adds_independent_card_and_preserves_pinned_execution(candidate):
    env, *_ = candidate
    confirm_candidate()
    pinned = canvas(env)
    latest = canvas(env, 'latest')
    assert pinned['view']['revision_no'] == pinned['run']['revision_no'] == 1
    assert latest['view']['revision_no'] == 2 and latest['run']['revision_no'] == 1
    assert 'gn_added' not in {n['node_key'] for n in pinned['nodes']}
    assert 'gn_added' in {n['node_key'] for n in latest['nodes']}
    record = definition_store.revision(FA, 2)
    body = draft_body(record)
    body['nodes'].append(node('tmp_independent', 'T'))
    before = copy.deepcopy(run_store.run_by_id('run_precheck'))
    saved = put(env, body)
    assert saved.status_code == 200, saved.text
    assert run_store.run_by_id('run_precheck') == before
    assert definition_store.revision(FA, 2) == record
    assert definition_store.draft(FA)['base_revision_no'] == 2
    added = saved.json()['key_map']['tmp_independent']
    assert added in {n['node_key'] for n in canvas(env, 'draft')['nodes']}
    assert canvas(env)['view']['revision_no'] == 1


@pytest.mark.parametrize('change', ['node', 'input'])
def test_latest_editor_still_refuses_protected_nodes_and_incoming_edges(candidate, change):
    env, keys, *_ = candidate
    confirm_candidate()
    body = draft_body(definition_store.revision(FA, 2))
    if change == 'node':
        body['nodes'][0]['label'] = 'changed protected root'
    else:
        body['edges'].append({'client_key': 'tmp_input', 'source': 'gn_added', 'target': keys['tmp_g']})
    before = facts(env)
    response = put(env, body)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'graph_node_protected'
    assert facts(env) == before


@pytest.mark.parametrize('status', ['running', 'paused', 'stopping'])
def test_latest_view_never_unlocks_an_active_run(candidate, status):
    env, *_ = candidate
    confirm_candidate()
    _sql(env['db'], 'UPDATE graph_runs SET status = ? WHERE run_id = ?', (status, 'run_precheck'))
    body = draft_body(definition_store.revision(FA, 2))
    body['nodes'].append(node('tmp_independent', 'T'))
    before = facts(env)
    response = put(env, body)
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'graph_run_locked'
    assert facts(env) == before


@pytest.mark.parametrize('reason', ['active_attempts', 'open', 'review', 'gate_rejected',
                                    'gate_recovery_reset_required', 'gate_jobs_in_progress',
                                    'reservation', 'run_policy_undecided', 'protected_input'])
def test_confirmed_latest_precheck_matches_actual_command_without_false_adoptable(candidate, monkeypatch, reason):
    from flowgate.gate_decisions import rounds, repository
    from modules.flow_gate.graph_workflow.domain import policies
    from modules.flow_gate.db.connection import get_store
    env, keys, nodes, edges = candidate
    gate = keys['tmp_g']
    if reason == 'active_attempts':
        monkeypatch.setattr(run_store, 'has_active_attempts', lambda _: True)
    elif reason in ('open', 'review', 'gate_rejected'):
        _sql(env['db'], 'UPDATE graph_node_runs SET status = ? WHERE node_key = ?',
             ('rejected' if reason == 'gate_rejected' else reason, gate))
    elif reason == 'gate_recovery_reset_required':
        monkeypatch.setattr(repository, 'reset_required', lambda _f, k: {'id': 'reset'} if k == gate else None)
    elif reason == 'gate_jobs_in_progress':
        monkeypatch.setattr(rounds, 'unfinished', lambda _: [{'job_id': 'job', 'target_key': gate}])
    elif reason == 'reservation':
        monkeypatch.setattr(repository, 'reservations_of_group', lambda _: [{'decision_id': 'd', 'node_key': gate}])
    elif reason == 'run_policy_undecided':
        policies.set_system_default('report_pair', policies.UNDECIDED, policies.UNDECIDED_IMPL_VERSION)
    else:
        edges.append({'edge_id': 'bad_input', 'source': 'gn_added', 'target': gate, 'condition': None})
        with get_store().transaction():
            definition_store.replace_draft_content(FA, nodes, edges)
    confirm_candidate()
    before = facts(env)
    result = assess(env)
    assert result['adoptable'] is False and result['error']['status'] == 409
    assert facts(env) == before
    response = env['client'].post(f'/api/v1/flows/{FA}/runs', json={'mode': 'full'}, headers=_h())
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == result['error']['code']
    assert response.json()['error']['params'] == result['error']['params']
    assert facts(env) == before


def test_confirmed_latest_allowed_precheck_and_command_adopt(candidate):
    env, *_ = candidate
    confirm_candidate()
    before = facts(env)
    result = assess(env)
    assert result['adoptable'] is True and result['target'] == 'revision'
    assert facts(env) == before
    response = env['client'].post(f'/api/v1/flows/{FA}/runs', json={'mode': 'full'}, headers=_h())
    assert response.status_code == 201, response.text
    assert response.json()['run']['revision_no'] == 2
