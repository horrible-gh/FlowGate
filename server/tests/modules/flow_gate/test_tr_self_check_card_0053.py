"""0053 1-D: time boundaries, card ownership and console-only controls."""
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.services import tr_self_check_card as service
from modules.flow_gate.api.v1 import self_check_card_routes as routes
from modules.flow_gate.graph_workflow.services.errors import GraphApiError

NOW = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)


def row(rid='r1', node='a', flow='f', state='running', age=240, **extra):
    return dict(self_check_run_id=rid, node_key=node, group_id=flow, project_id='p',
                status=state, started_at=(NOW-timedelta(seconds=age)).isoformat(),
                enqueued_at=NOW.isoformat(), queue_order=rid, **extra)


@pytest.fixture
def setup(monkeypatch):
    data = []
    monkeypatch.setattr(service, '_now', lambda: NOW)
    monkeypatch.setattr(service.repository, 'card_node', lambda f, n: {
        'node_type': 'document', 'doc_type': 'T', 'label': 'Card '+n})
    monkeypatch.setattr(service, 'resolve_verify_limits', lambda f, n: SimpleNamespace(run_timeout_sec=180))
    monkeypatch.setattr(service.instance_registry, 'node_key', lambda: 'host')
    def rows(flow_id=None, node_key=None, run_id=None):
        return [r for r in data if (flow_id is None or r['group_id'] == flow_id)
                and (node_key is None or r['node_key'] == node_key)
                and (run_id is None or r['self_check_run_id'] == run_id)
                and (run_id is not None or r['status'] in ('queued', 'pending', 'running'))]
    monkeypatch.setattr(service, '_rows', rows)
    return data


@pytest.mark.parametrize('age,visible', [(239.999, False), (240, True), (240.001, True)])
def test_display_boundary(setup, age, visible):
    setup.append(row(age=age))
    actual = service.status('f', 'a')
    assert actual['visible'] is visible
    assert actual['actions'] == (['force_stop'] if visible else [])


def test_resolved_card_limit_and_latest_launch(setup, monkeypatch):
    monkeypatch.setattr(service, 'resolve_verify_limits', lambda f, n: SimpleNamespace(run_timeout_sec=600))
    setup.extend([row('old', age=900), row('new', age=659)])
    assert service.status('f', 'a')['visible'] is False
    setup[1]['started_at'] = (NOW-timedelta(seconds=660)).isoformat()
    assert service.status('f', 'a')['state'] == 'overdue'


@pytest.mark.parametrize('stamp', [None, '', 'invalid'])
def test_unknown_start_is_not_a_timeout(setup, stamp):
    setup.append(row())
    setup[0]['started_at'] = stamp
    assert not service.status('f', 'a')['visible']


def test_offsets_compare_as_instants(setup):
    setup.append(row())
    setup[0]['started_at'] = '2026-11-19T20:56:00+09:00'
    assert service.status('f', 'a')['visible']


def test_waiting_appears_with_holder_and_link(setup):
    setup.extend([row(node='holder', age=239), row('wait', state='queued')])
    assert not service.status('f', 'a')['visible']
    setup[0]['started_at'] = (NOW-timedelta(seconds=240)).isoformat()
    actual = service.status('f', 'a')
    assert actual['state'] == 'waiting'
    assert actual['blocker']['node_key'] == 'holder'
    assert actual['actions'] == ['cancel_waiting']
    setup[0]['status'] = 'failed'
    assert not service.status('f', 'a')['visible']


def test_other_project_identity_not_leaked(setup):
    setup.extend([row(flow='other', node='secret'), row('wait', state='queued')])
    actual = service.status('f', 'a')
    assert actual['visible'] and actual['blocker'] is None
    assert 'secret' not in str(actual)
    assert service.status('f', 'a', lambda p: True)['blocker']['flow_id'] == 'other'
    # 1-D client: [→] opens a card of the open project only, so the blocker names its project.
    assert service.status('f', 'a', lambda p: True)['blocker']['project_id'] == 'p'


def test_other_host_cannot_block(setup):
    setup.extend([row(node='holder', host_node_key='different'), row('wait', state='queued')])
    assert not service.status('f', 'a')['visible']


def test_restarted_holder_resets_waiting_display(setup):
    setup.extend([row('old', node='holder', age=500), row('new', node='holder', age=1),
                  row('wait', state='queued')])
    assert not service.status('f', 'a')['visible']


@pytest.mark.parametrize('state', ['completed', 'failed', 'cancelled'])
def test_terminal_runs_hidden(setup, state):
    setup.append(row(state=state))
    assert service.status('f', 'a')['state'] == 'hidden'


def test_ts_hidden_and_cannot_control(setup, monkeypatch):
    monkeypatch.setattr(service.repository, 'card_node', lambda *a: {'node_type':'document','doc_type':'TS'})
    setup.append(row())
    assert not service.status('f', 'a')['visible']
    with pytest.raises(GraphApiError, match='selfcheck_not_supported'):
        service.control('f', 'a', 'r1', 'force_stop')


def test_unknown_card(setup, monkeypatch):
    monkeypatch.setattr(service.repository, 'card_node', lambda *a: None)
    with pytest.raises(GraphApiError, match='node_not_found'):
        service.status('f', 'missing')


@pytest.mark.parametrize('flow,node,rid', [('wrong','a','r1'),('f','wrong','r1'),('f','a','wrong')])
def test_controls_refuse_foreign_run(setup, monkeypatch, flow, node, rid):
    setup.append(row())
    monkeypatch.setattr(service.queue, 'force_stop', lambda *a: pytest.fail('must not stop'))
    with pytest.raises(GraphApiError, match='selfcheck_run_not_found'):
        service.control(flow, node, rid, 'force_stop')


def test_cancel_waiting_delegates_and_feedback_is_transient(setup, monkeypatch):
    setup.append(row(state='queued'))
    def cancel(rid):
        assert rid == 'r1'
        setup[0].update(status='cancelled', error_code=service.queue.CANCELLED_WAITING)
        return {key: value for key, value in setup[0].items() if key != 'node_key'}
    monkeypatch.setattr(service.queue, 'cancel_waiting', cancel)
    assert service.control('f','a','r1','cancel_waiting')['state'] == 'cancelled'
    assert service.control('f','a','r1','cancel_waiting')['state'] == 'cancelled'
    assert not service.status('f','a')['visible']


def test_cancel_start_race_never_kills(setup, monkeypatch):
    setup.append(row(state='queued'))
    monkeypatch.setattr(service.queue, 'cancel_waiting', lambda rid: None)
    monkeypatch.setattr(service.queue, 'force_stop', lambda *a: pytest.fail('must not kill'))
    with pytest.raises(GraphApiError, match='selfcheck_not_waiting'):
        service.control('f','a','r1','cancel_waiting')


@pytest.mark.parametrize('final,expected', [('failed','stopped'),('cancelled','stopped'),
                                         ('running','stop_failed'),('pending','stop_failed')])
def test_stop_result_is_honest(setup, monkeypatch, final, expected):
    setup.append(row())
    def stop(rid):
        assert rid == 'r1'
        setup[0].update(status=final, cancel_requested=1)
        return {key: value for key, value in setup[0].items() if key != 'node_key'}
    monkeypatch.setattr(service.queue, 'force_stop', stop)
    actual = service.control('f','a','r1','force_stop')
    assert actual['state'] == expected
    assert actual['actions'] == (['force_stop'] if expected == 'stop_failed' else [])
    assert service.status('f','a')['state'] == ('stop_failed' if expected == 'stop_failed' else 'hidden')


def test_stop_does_not_cancel_queued_run(setup):
    setup.append(row(state='queued'))
    with pytest.raises(GraphApiError, match='selfcheck_not_running'):
        service.control('f','a','r1','force_stop')


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes.envelope, 'verify_bearer', lambda request: {'_is_user_jwt': True, 'issued_to':'u'})
    monkeypatch.setattr(routes.canvas_service, 'load_flow', lambda f: {'group_id':f,'project_id':'p'})
    return TestClient(app)


@pytest.mark.parametrize('suffix,method', [('', 'get'),('/runs/r1/force-stop','post'),('/runs/r1/cancel-waiting','post')])
def test_every_endpoint_requires_write_permission(client, monkeypatch, suffix, method):
    seen=[]
    monkeypatch.setattr(routes.envelope, 'has_permission', lambda u,p,perm: seen.append(perm) or False)
    monkeypatch.setattr(service, 'status', lambda *a: pytest.fail('unauthorized read'))
    monkeypatch.setattr(service, 'control', lambda *a: pytest.fail('unauthorized control'))
    response=getattr(client,method)('/api/v1/flows/f/nodes/a/self-check'+suffix)
    assert response.status_code == 403
    assert seen == ['perm_document_update']


@pytest.mark.parametrize('suffix,method', [('', 'get'),('/runs/r1/force-stop','post'),('/runs/r1/cancel-waiting','post')])
def test_worker_tokens_refused(client, monkeypatch, suffix, method):
    monkeypatch.setattr(routes.envelope, 'verify_bearer', lambda request: {'issued_to':'u','action_scope':'edit'})
    response=getattr(client,method)('/api/v1/flows/f/nodes/a/self-check'+suffix)
    assert response.status_code == 403
    assert response.json()['error']['code'] == 'token_kind_not_allowed'


def test_http_contract(client, setup, monkeypatch):
    monkeypatch.setattr(routes.envelope, 'has_permission', lambda *a: True)
    setup.append(row())
    response=client.get('/api/v1/flows/f/nodes/a/self-check')
    assert response.status_code == 200
    assert response.json()['verification']['state'] == 'overdue'
    monkeypatch.setattr(service.queue, 'force_stop', lambda rid: row(state='failed'))
    response=client.post('/api/v1/flows/f/nodes/a/self-check/runs/r1/force-stop')
    assert response.status_code == 200
    assert response.json()['verification']['state'] == 'stopped'


def test_repository_joins_real_attempt_ownership(monkeypatch):
    db=sqlite3.connect(':memory:')
    db.row_factory=sqlite3.Row
    db.executescript("""
        CREATE TABLE tr_self_check_runs (self_check_run_id TEXT, group_id TEXT, attempt_id TEXT, status TEXT);
        CREATE TABLE graph_node_attempts (attempt_id TEXT, run_id TEXT, node_key TEXT);
        CREATE TABLE graph_runs (run_id TEXT, group_id TEXT);
        INSERT INTO graph_runs VALUES ('graph1','f'),('graph2','other');
        INSERT INTO graph_node_attempts VALUES ('a1','graph1','card'),('a2','graph2','card');
        INSERT INTO tr_self_check_runs VALUES ('valid','f','a1','running'),
            ('ended','f','a1','failed'),('foreign','other','a2','running'),
            ('forged','f','a2','running'),('chat','f',NULL,'running');
    """)
    class Store:
        def _fetch_all(self, sql, args):
            return [dict(r) for r in db.execute(sql,args)]
    monkeypatch.setattr(service.runs,'get_store',lambda: Store())
    assert [r['self_check_run_id'] for r in service._rows('f','card')] == ['valid']
    assert service._rows('f','card','foreign') == []
    assert service._rows('f','card','forged') == []
    assert service._rows('f',"card' OR 1=1 --") == []
    assert service._rows('f','card','ended')[0]['status'] == 'failed'
    db.close()


@pytest.mark.parametrize('action,initial,final,expected', [
    ('force-stop', 'running', 'failed', 'stopped'),
    ('force-stop', 'running', 'running', 'stop_failed'),
    ('cancel-waiting', 'queued', 'cancelled', 'cancelled'),
])
def test_control_http_with_unjoined_queue_row(client, setup, monkeypatch, action, initial, final, expected):
    monkeypatch.setattr(routes.envelope, 'has_permission', lambda *a: True)
    setup.append(row(state=initial))
    ended = row(state=final, finished_at=NOW.isoformat())
    del ended['node_key']  # Queue results contain only tr_self_check_runs columns.
    monkeypatch.setattr(service.queue, action.replace('-', '_'), lambda rid: ended)
    response = client.post('/api/v1/flows/f/nodes/a/self-check/runs/r1/' + action)
    assert response.status_code == 200
    actual = response.json()['verification']
    assert actual['node_key'] == 'a'
    assert actual['state'] == expected
    assert actual['run']['status'] == final
    assert actual['run']['finished_at'] == NOW.isoformat()
    assert actual['run']['show_at'] == NOW.isoformat()
    assert actual['actions'] == (['force_stop'] if expected == 'stop_failed' else [])
    assert 'node_key' not in ended  # Response enrichment must not mutate the queue result.
