"""0095/0116: owner-list saves, history-preserving deletion and active-loop conflicts.

No configured database, provider credential or AI process is used. SQLite enforces
real FK, tombstone, name-index and transaction behavior.
"""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

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
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript("""
          CREATE TABLE ai_providers (
            provider_id TEXT PRIMARY KEY, project_id TEXT, name TEXT, exec_type TEXT,
            kind TEXT, enabled INTEGER, cli_command TEXT, api_base_url TEXT,
            api_model TEXT, api_key TEXT, sort_order INTEGER, created_at TEXT,
            updated_at TEXT, owner_scope TEXT, owner_user_id TEXT,
            project_access TEXT, exec_location TEXT, target_agent_id TEXT);
          CREATE TABLE system_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT, value_type TEXT,
            description TEXT, updated_at TEXT, updated_by TEXT);
          CREATE TABLE user_ai_defaults (
            user_id TEXT PRIMARY KEY,
            provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL);
          CREATE TABLE ai_invoke_document_review_loops (
            run_id TEXT PRIMARY KEY, current_stage TEXT, stop_reason TEXT,
            reviewer_provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE RESTRICT,
            rework_provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE RESTRICT);
          CREATE UNIQUE INDEX ux_ai_providers_system_name ON ai_providers(name) WHERE owner_scope='system';
          CREATE UNIQUE INDEX ux_ai_providers_user_name ON ai_providers(owner_user_id,name) WHERE owner_scope='user';
          CREATE TABLE tokens (token_id TEXT PRIMARY KEY, provider_id TEXT REFERENCES ai_providers(provider_id));
          CREATE TABLE group_ai_leases (run_id TEXT, token_id TEXT, state TEXT);
          CREATE TABLE ai_provider_doctype_map (provider_id TEXT REFERENCES ai_providers(provider_id), doc_type TEXT);
          CREATE TABLE ai_invoke_paused_chains (group_id TEXT PRIMARY KEY,
            continuation_base_provider_id TEXT REFERENCES ai_providers(provider_id),
            continuation_work_executor_provider_id TEXT REFERENCES ai_providers(provider_id));
          CREATE TABLE ai_group_members (ai_group_id TEXT, provider_id TEXT REFERENCES ai_providers(provider_id));
          CREATE TABLE ai_invoke_runs (run_id TEXT, provider_id TEXT REFERENCES ai_providers(provider_id));
          CREATE TABLE document_reviews (doc_id TEXT, provider_id TEXT REFERENCES ai_providers(provider_id), body TEXT);
          CREATE TABLE ai_evaluations (
            evaluation_id TEXT PRIMARY KEY, target_provider_id TEXT, target_provider_name TEXT,
            judge_provider_id TEXT, judge_provider_name TEXT, lifecycle TEXT, active_dedupe_key TEXT UNIQUE,
            evaluation_set_id TEXT, evaluation_set_name TEXT, created_by TEXT, created_at TEXT, updated_at TEXT,
            task_title TEXT, task_summary TEXT, total_score INTEGER, artifacts TEXT, review_summary TEXT,
            failure_stage TEXT, reason_code TEXT, diagnostic_summary TEXT, scratch_ref TEXT,
            started_at TEXT, finished_at TEXT, human_reviewed_at TEXT, task_instruction TEXT, target_report TEXT);
          CREATE TABLE ai_evaluation_scores (evaluation_id TEXT, criterion_key TEXT, position INTEGER,
            criterion_description TEXT, score INTEGER, rationale TEXT);
        """)
        migration = Path(__file__).parents[1] / 'sql/migrations/sqlite/165_ai_provider_tombstones.sql'
        self.conn.executescript(migration.read_text(encoding='utf-8'))

    def _execute(self, sql, params=None):
        with self.lock:
            self.conn.execute(sql, params or [])

    def _fetch_all(self, sql, params=None):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params or []).fetchall()]

    def _fetch_one(self, sql, params=None):
        with self.lock:
            row = self.conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None

    def _execute_affected(self, sql, params=None):
        with self.lock:
            return self.conn.execute(sql, params or []).rowcount

    @contextmanager
    def transaction(self):
        # Explicit real rollback; catches domain exceptions raised from a driver error.
        with self.lock:
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
    from modules.flow_gate.db import ai_evaluations
    from modules.flow_gate.services.ai_invoke import runtime
    monkeypatch.setattr(svc._user_default_db, 'get_store', lambda: s)
    monkeypatch.setattr(ai_evaluations, 'get_store', lambda: s)
    monkeypatch.setattr(runtime._svc(), '_runs', {})
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
    assert store._fetch_one('SELECT COUNT(*) AS n FROM ai_providers WHERE deleted_at IS NULL')['n'] == 2 * (count - 1) + 1
    assert store._fetch_one('SELECT COUNT(*) AS n FROM ai_providers')['n'] == 2 * count + 1


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
    store._execute('INSERT INTO user_ai_defaults VALUES (?,?)', ['alice', pid])
    second = system(first['providers'][1:])
    assert second['default_provider_id'] == second['providers'][0]['id']
    assert svc.get_user_settings('alice')['default_provider_id'] is None
    assert system([])['default_provider_id'] is None
    assert svc.get_system_settings()['providers'] == []
    mine = svc.save_user_settings('alice', [cli()])
    store._execute('INSERT INTO user_ai_defaults VALUES (?,?)', ['bob', mine['providers'][0]['id']])
    assert svc.save_user_settings('alice', [])['providers'] == []
    assert store._fetch_one('SELECT provider_id FROM user_ai_defaults WHERE user_id=?', ['bob'])['provider_id'] is None


@pytest.mark.parametrize('owner', ['system', 'user'])
@pytest.mark.parametrize('reference', ['reviewer_provider_id', 'rework_provider_id'])
@pytest.mark.parametrize('stage', ['review', 'rework', 'stopped'])
def test_history_delete_and_active_loop_contract(store, owner, reference, stage):
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
    # Historical references survive deletion; only active system work protects the row.
    store._execute(f'INSERT INTO ai_invoke_document_review_loops(run_id,current_stage,stop_reason,{reference}) VALUES (?,?,?,?)',
                   ['run', stage, 'review_passed' if stage == 'stopped' else None, pid])
    remaining = [dict(first['providers'][0])]  # drop a restricted and an unrelated row together
    remaining[0]['name'] = 'should roll back'
    response = put(remaining)
    blocked = owner == 'system' and stage != 'stopped'
    assert response.status_code == (409 if blocked else 200)
    after = svc.get_system_settings() if owner == 'system' else svc.get_user_settings('alice')
    if blocked:
        assert response.json() == {'detail': {'code': 'provider_in_use', 'reason': 'active_loop'}}
        assert after['providers'] == first['providers']
        assert after['default_provider_id'] == first['default_provider_id']
        store._execute("UPDATE ai_invoke_document_review_loops SET current_stage='stopped', stop_reason='review_passed'")
        assert put(remaining).status_code == 200
    else:
        assert after['providers'][0]['name'] == 'should roll back'
    assert store._fetch_one('SELECT * FROM ai_invoke_document_review_loops')[reference] == pid
    assert db.get_name_any(pid) == 'two'
    assert db.get_row(None, pid) is None


def test_storage_errors_are_not_reported_as_in_use():
    class Reject:
        def _fetch_all(self, sql, params):
            raise RuntimeError('connection lost')
    with pytest.raises(RuntimeError, match='connection lost'):
        db._delete_missing(Reject(), "owner_scope = 'system'", [], [], set())


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
