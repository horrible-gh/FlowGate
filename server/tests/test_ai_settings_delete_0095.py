"""0095: isolated owner-list saves and restrictive history deletion.

No configured database, provider credential or AI process is used. SQLite enforces
real FK/transaction behavior; PostgreSQL driver's SQLSTATE is tested separately.
"""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.flow_gate.db import ai_providers as db
from modules.flow_gate.db import system_settings
from modules.flow_gate.settings import ai_settings_service as svc
from modules.flow_gate.settings import ai_execution_policy_service as policy
from modules.flow_gate.settings.routers import ai_settings as routes


class Store:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
          CREATE TABLE ai_providers (
            provider_id TEXT PRIMARY KEY, project_id TEXT, name TEXT, exec_type TEXT,
            kind TEXT, enabled INTEGER, cli_command TEXT, api_base_url TEXT,
            api_model TEXT, api_key TEXT, sort_order INTEGER, created_at TEXT,
            updated_at TEXT, owner_scope TEXT, owner_user_id TEXT,
            project_access TEXT, exec_location TEXT);
          CREATE TABLE system_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT, value_type TEXT,
            description TEXT, updated_at TEXT, updated_by TEXT);
          CREATE TABLE user_defaults (
            user_id TEXT PRIMARY KEY,
            provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL);
          CREATE TABLE review_history (
            run_id TEXT PRIMARY KEY, stage TEXT,
            reviewer_provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE RESTRICT,
            rework_provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE RESTRICT);
        """)

    def _execute(self, sql, params=None):
        self.conn.execute(sql, params or [])

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self.conn.execute(sql, params or []).fetchall()]

    def _fetch_one(self, sql, params=None):
        row = self.conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    @contextmanager
    def transaction(self):
        # Explicit real rollback; catches domain exceptions raised from a driver error.
        self.conn.commit()
        with self.conn:
            yield self


@pytest.fixture
def store(monkeypatch):
    s = Store()
    monkeypatch.setattr(db, 'get_store', lambda: s)
    monkeypatch.setattr(system_settings, 'get_store', lambda: s)
    monkeypatch.setattr(policy, 'execution_policy_payload', lambda: {})
    monkeypatch.setattr(db._crypto, 'encrypt_api_key', lambda key: 'fake:' + key if key else None)
    monkeypatch.setattr(db._crypto, 'decrypt_api_key', lambda key: key[5:] if key else None)
    monkeypatch.setattr(svc._user_default_db, 'get', lambda owner: (
        s._fetch_one('SELECT provider_id FROM user_defaults WHERE user_id=?', [owner]) or {}
    ).get('provider_id'))
    yield s
    s.conn.close()


def cli(name='CLI', **kwargs):
    return dict(name=name, exec_type='cli', kind='custom', enabled=True,
                cli_command='fake-cli --prompt', **kwargs)


def system(items, default=None):
    return svc.save_system_settings(items, default, None)


@pytest.mark.parametrize('count', [19, 20, 21, 300])
def test_roundtrip_middle_delete_and_owner_isolation(store, count):
    first = system([cli(f's{i}') for i in range(count)])
    mine = svc.save_user_settings('alice', [cli(f'u{i}') for i in range(count)])
    other = svc.save_user_settings('bob', [cli('other')])
    assert len(svc.get_system_settings()['providers']) == count
    assert len(svc.get_user_settings('alice')['providers']) == count
    mid = count // 2
    keep_system = first['providers'][:mid] + first['providers'][mid + 1:]
    keep_mine = mine['providers'][:mid] + mine['providers'][mid + 1:]
    saved = system(keep_system, first['default_provider_id'])
    assert saved['providers'] == keep_system
    assert svc.get_user_settings('alice')['providers'] == mine['providers']
    assert svc.save_user_settings('alice', keep_mine)['providers'] == keep_mine
    assert svc.get_system_settings()['providers'] == keep_system
    assert svc.get_user_settings('bob')['providers'] == other['providers']
    assert store._fetch_one('SELECT COUNT(*) AS n FROM ai_providers')['n'] == 2 * (count - 1) + 1


def test_301_rejected_without_mutation(store):
    first = system([cli('original')])
    mine = svc.save_user_settings('alice', [cli('original-user')])
    too_many = [cli(f'p{i}') for i in range(301)]
    for save in (system, lambda items: svc.save_user_settings('alice', items)):
        with pytest.raises(svc.AiSettingsValidationError) as err:
            save(too_many)
        assert err.value.errors == [{'field': 'providers', 'reason': 'too_many'}]
    assert svc.get_system_settings()['providers'] == first['providers']
    assert svc.get_user_settings('alice')['providers'] == mine['providers']


def test_delete_default_last_and_user_default(store):
    first = system([cli('one'), cli('two')])
    pid = first['default_provider_id']
    store._execute('INSERT INTO user_defaults VALUES (?,?)', ['alice', pid])
    second = system(first['providers'][1:])
    assert second['default_provider_id'] == second['providers'][0]['id']
    assert svc.get_user_settings('alice')['default_provider_id'] is None
    assert system([])['default_provider_id'] is None
    assert svc.get_system_settings()['providers'] == []
    mine = svc.save_user_settings('alice', [cli()])
    store._execute('INSERT INTO user_defaults VALUES (?,?)', ['bob', mine['providers'][0]['id']])
    assert svc.save_user_settings('alice', [])['providers'] == []
    assert store._fetch_one('SELECT provider_id FROM user_defaults WHERE user_id=?', ['bob'])['provider_id'] is None


@pytest.mark.parametrize('owner', ['system', 'user'])
@pytest.mark.parametrize('reference', ['reviewer_provider_id', 'rework_provider_id'])
@pytest.mark.parametrize('stage', ['review', 'stopped'])
def test_referenced_delete_409_preserves_settings_and_history(store, owner, reference, stage):
    # Hit the HTTP router and real service/transaction with isolated storage.
    app = FastAPI()
    app.include_router(routes.router)
    for route in app.routes:
        for dep in getattr(getattr(route, 'dependant', None), 'dependencies', []):
            app.dependency_overrides[dep.call] = lambda: {'user_id': 'alice'}
    client = TestClient(app)
    path = '/system/ai-settings' if owner == 'system' else '/me/ai-providers'
    def put(items, default=None):
        return client.put(path, json={'providers': items, 'default_provider_id': default})
    first = put([cli('one'), cli('two'), cli('three')]).json()
    pid = first['providers'][1]['id']
    # Stopped loops still keep their references. Only fixture history is ever deleted.
    store._execute(f'INSERT INTO review_history(run_id,stage,{reference}) VALUES (?,?,?)', ['run', stage, pid])
    remaining = [dict(first['providers'][0])]  # drop a restricted and an unrelated row together
    remaining[0]['name'] = 'should roll back'
    response = put(remaining)
    assert response.status_code == 409
    assert response.json() == {'detail': {'code': 'provider_in_use'}}
    after = svc.get_system_settings() if owner == 'system' else svc.get_user_settings('alice')
    assert after['providers'] == first['providers']
    assert after['default_provider_id'] == first['default_provider_id']
    assert store._fetch_one('SELECT * FROM review_history')['stage'] == stage
    # Retry can succeed when history no longer references the provider (fixture only).
    store._execute('DELETE FROM review_history')
    assert put(remaining).status_code == 200


@pytest.mark.parametrize('attribute', ['sqlstate', 'pgcode'])
def test_postgres_fk_sqlstate_is_translated(attribute):
    class ForeignKeyViolation(Exception):
        pass
    exc = ForeignKeyViolation('fake diagnostic')
    setattr(exc, attribute, '23503')
    exc.diag = SimpleNamespace(constraint_name='ai_invoke_document_review_loops_reviewer_provider_id_fkey')
    class Reject:
        def _execute(self, sql, params):
            raise exc
    with pytest.raises(db.ProviderInUseError) as raised:
        db._delete_missing(Reject(), "owner_scope = 'system'", [], ['kept'])
    assert raised.value.__cause__ is exc
    assert raised.value.__cause__.diag.constraint_name.endswith('_fkey')


def test_unrelated_sql_error_is_not_relabelled():
    class Reject:
        def _execute(self, sql, params):
            raise RuntimeError('connection lost')
    with pytest.raises(RuntimeError, match='connection lost'):
        db._delete_missing(Reject(), "owner_scope = 'system'", [], [])


def test_api_key_keep_replace_clear(store):
    data = dict(name='API', kind='custom', exec_type='api', api_model='fake-model', api_key='fake-old')
    first = system([data])
    pid = first['providers'][0]['id']
    for key, expected in [(None, 'fake-old'), ('fake-new', 'fake-new'), ('', None)]:
        saved = system([{**data, 'id': pid, 'api_key': key}])
        assert saved['providers'][0]['api_key_set'] is (expected is not None)
        assert db.get_secret(None, pid) == expected
        assert 'api_key' not in saved['providers'][0]


def test_residual_validation_preserves_original_list(store):
    first = system([cli('one'), cli('two')])
    with pytest.raises(svc.AiSettingsValidationError) as err:
        system([{**first['providers'][0], 'cli_command': ' '}])
    assert err.value.errors == [{'index': 0, 'field': 'cli_command', 'reason': 'required_for_cli'}]
    assert svc.get_system_settings()['providers'] == first['providers']


@pytest.mark.parametrize('field,limit,api_mode', [
    ('name', 100, False), ('cli_command', 4000, False),
    ('api_model', 200, True), ('api_base_url', 500, True), ('api_key', 1000, True),
])
def test_field_length_contract(store, field, limit, api_mode):
    item = dict(name='API', exec_type='api', kind='custom', api_model='fake-model') if api_mode else cli()
    assert svc.validate_settings([{**item, field: 'x' * limit}], None, None, None) == []
    assert svc.validate_settings([{**item, field: 'x' * (limit + 1)}], None, None, None) == [
        {'index': 0, 'field': field, 'reason': 'too_long'}]


@pytest.mark.parametrize('owner', ['system', 'user'])
def test_owner_cannot_reuse_foreign_provider_id(store, owner):
    first = system([cli('system')])
    mine = svc.save_user_settings('alice', [cli('user')])
    if owner == 'system':
        saved = system([{**first['providers'][0], 'id': mine['providers'][0]['id']}])
        assert saved['providers'][0]['id'] != mine['providers'][0]['id']
        assert svc.get_user_settings('alice')['providers'] == mine['providers']
    else:
        saved = svc.save_user_settings('alice', [{**mine['providers'][0], 'id': first['providers'][0]['id']}])
        assert saved['providers'][0]['id'] != first['providers'][0]['id']
        assert svc.get_system_settings()['providers'] == first['providers']
