"""0129 T#1: target Agent storage contract; no execution or GUI changes."""
import os
import re
import sqlite3
import uuid
from pathlib import Path

import pytest
from test_system_agents_0076 import env, API, REMOTE_BODY  # noqa: F401
from modules.flow_gate.settings.routers import ai_settings as routes

SQL = Path(__file__).resolve().parents[3] / 'sql' / 'migrations'
MIGRATION = '171_ai_provider_target_agent.sql'


@pytest.fixture
def settings_env(env):
    env.client.app.include_router(routes.router, prefix=API)
    return env


def provider(**changes):
    return dict(name='test connection', exec_type='cli', kind='custom', enabled=True,
                cli_command='test-cli', exec_location='remote', **changes)


def save(e, items, who='M'):
    path = '/system/ai-settings' if who == 'A' else '/me/ai-providers'
    return e.client.put(API + path, headers=e.h(who), json={'providers': items})


def get(e, who='M'):
    path = '/system/ai-settings' if who == 'A' else '/me/ai-providers'
    return e.client.get(API + path, headers=e.h(who)).json()['providers']


def agent(e, who='M', registered=True):
    base = API + ('/system/agents' if who == 'A' else '/me/agents')
    body = {**REMOTE_BODY, 'name': 'agent-' + uuid.uuid4().hex[:8],
            'configured_capabilities': {'ai_cli': False, 'ai_api': False, 'storage': False}}
    r = e.client.post(base, headers=e.h(who), json=body)
    assert r.status_code == 201, r.text
    aid = r.json()['agent_id']
    if registered:
        issue = e.client.post(base + '/' + aid + '/enrollments', headers=e.h(who), json={})
        assert issue.status_code == 201, issue.text
        enrolled = e.enroll(issue.json()['enrollment_token'])
        assert enrolled.status_code == 201, enrolled.text
    return aid, base + '/' + aid


def error(response, reason):
    assert response.status_code == 422, response.text
    assert response.json()['detail'] == {'code': 'validation_failed', 'errors': [
        {'index': 0, 'field': 'target_agent_id', 'reason': reason}]}


@pytest.mark.parametrize('who', ['M', 'A'])
def test_remote_requires_target_and_save_is_atomic(settings_env, who):
    e = settings_env
    local = provider()
    local['exec_location'] = 'local'
    assert save(e, [local], who).status_code == 200
    before = get(e, who)
    error(save(e, [provider()], who), 'required_for_remote_cli')
    assert get(e, who) == before


@pytest.mark.parametrize('who', ['M', 'A'])
def test_registered_offline_no_capability_target_roundtrips_and_can_change(settings_env, who):
    e = settings_env
    first, _ = agent(e, who)
    second, _ = agent(e, who)
    result = save(e, [provider(target_agent_id=first)], who)
    assert result.status_code == 200, result.text
    row = get(e, who)[0]
    assert row['target_agent_id'] == first
    assert save(e, [dict(row, target_agent_id=second)], who).status_code == 200
    assert get(e, who)[0]['target_agent_id'] == second
    # The DB read path is independent of the save response.
    stored = e.store._fetch_one('SELECT target_agent_id FROM ai_providers WHERE provider_id=?', [row['id']])
    assert stored['target_agent_id'] == second


def test_unknown_foreign_and_system_agents_share_non_disclosing_error(settings_env):
    e = settings_env
    foreign, _ = agent(e, 'N')
    system, _ = agent(e, 'A')
    for aid in ['agt_missing', foreign, system]:
        error(save(e, [provider(target_agent_id=aid)]), 'invalid_target_agent')
    assert get(e) == []


def test_shared_connection_rejects_personal_agent(settings_env):
    aid, _ = agent(settings_env)
    error(save(settings_env, [provider(target_agent_id=aid)], 'A'), 'invalid_target_agent')


@pytest.mark.parametrize('mode', ['local', 'api'])
def test_local_and_api_force_null_even_for_foreign_target(settings_env, mode):
    e = settings_env
    aid, _ = agent(e, 'N')
    item = provider(target_agent_id=aid)
    if mode == 'local':
        item['exec_location'] = 'local'
    else:
        item.update(exec_type='api', kind='custom', api_model='test-model')
    r = save(e, [item])
    assert r.status_code == 200, r.text
    row = get(e)[0]
    assert row['target_agent_id'] is None
    assert row['exec_location'] == item['exec_location']
    if mode == 'local':
        assert row['cli_command'] == item['cli_command']


def test_target_cannot_be_cleared_but_omission_keeps_assignment(settings_env):
    e = settings_env
    aid, _ = agent(e)
    assert save(e, [provider(target_agent_id=aid)]).status_code == 200
    row = get(e)[0]
    error(save(e, [dict(row, target_agent_id=None)]), 'required_for_remote_cli')
    error(save(e, [dict(row, target_agent_id='')]), 'required_for_remote_cli')
    row.pop('target_agent_id')
    assert save(e, [dict(row, name='edited')]).status_code == 200
    assert get(e)[0]['target_agent_id'] == aid


@pytest.mark.parametrize('state', ['unregistered', 'disabled', 'revoked'])
def test_new_assignment_rejects_unavailable_agent(settings_env, state):
    e = settings_env
    aid, path = agent(e, registered=state != 'unregistered')
    if state == 'disabled':
        assert e.client.patch(path, headers=e.h('M'), json={'enabled': False}).status_code == 200
    if state == 'revoked':
        assert e.client.post(path + '/credential/revoke', headers=e.h('M'), json={}).status_code == 200
    error(save(e, [provider(target_agent_id=aid)]), 'target_agent_unavailable')


def test_revocation_preserves_reference_and_allows_other_edits(settings_env):
    e = settings_env
    aid, path = agent(e)
    assert save(e, [provider(target_agent_id=aid)]).status_code == 200
    assert e.client.post(path + '/credential/revoke', headers=e.h('M'), json={}).status_code == 200
    row = get(e)[0]
    assert row['target_agent_id'] == aid
    assert save(e, [dict(row, name='after revoke')]).status_code == 200
    assert e.client.patch(path, headers=e.h('M'), json={'enabled': False}).status_code == 200
    assert save(e, [dict(row, name='after disable')]).status_code == 200
    assert get(e)[0]['target_agent_id'] == aid
    r = e.client.delete(path, headers=e.h('M'))
    assert r.status_code == 409
    assert r.json()['error']['code'] == 'agent_in_use'


def test_legacy_null_survives_list_save_but_local_to_remote_requires_target(settings_env):
    e = settings_env
    item = provider()
    item['exec_location'] = 'local'
    assert save(e, [item]).status_code == 200
    row = get(e)[0]
    error(save(e, [dict(row, exec_location='remote')]), 'required_for_remote_cli')
    # Simulate an existing remote row carried forward with NULL by migration.
    e.store._execute("UPDATE ai_providers SET exec_location='remote' WHERE provider_id=?", [row['id']])
    legacy = get(e)[0]
    assert legacy['target_agent_id'] is None
    r = save(e, [dict(legacy, name='legacy edited'), dict(item, name='new local')])
    assert r.status_code == 200, r.text
    assert [p['target_agent_id'] for p in get(e)] == [None, None]


def test_registry_guard_handles_unregistered_reference_and_release_on_tombstone(settings_env):
    e = settings_env
    aid, path = agent(e, registered=False)
    item = provider()
    item['exec_location'] = 'local'
    assert save(e, [item]).status_code == 200
    row = get(e)[0]
    # Defense in depth for a reference inserted outside service validation.
    e.store._execute("UPDATE ai_providers SET exec_location='remote',target_agent_id=? WHERE provider_id=?", [aid, row['id']])
    r = e.client.delete(path, headers=e.h('M'))
    assert r.status_code == 409 and r.json()['error']['code'] == 'agent_in_use'
    assert save(e, []).status_code == 200
    assert e.store._fetch_one('SELECT target_agent_id FROM ai_providers WHERE provider_id=?', [row['id']])['target_agent_id'] is None
    assert e.client.delete(path, headers=e.h('M')).status_code == 204


@pytest.mark.parametrize('dialect', ['sqlite', 'mysql', 'postgres'])
def test_migration_contract_parity(dialect):
    text = (SQL / dialect / MIGRATION).read_text(encoding='utf-8')
    sql = re.sub(r'--[^\n]*', '', text).lower()
    assert 'add column target_agent_id' in sql
    assert 'not null' not in sql and 'default' not in sql
    assert 'references agents(agent_id) on delete restrict' in sql
    assert 'create index idx_ai_providers_target_agent on ai_providers(target_agent_id)' in sql
    assert not re.search(r'\b(update|insert|delete from)\b', sql)
    assert ('varchar(64)' if dialect == 'mysql' else 'text') in sql


def test_sqlite_migration_preserves_all_existing_modes_and_enforces_reference():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript('CREATE TABLE agents(agent_id TEXT PRIMARY KEY);'
                          'CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY,exec_type TEXT,exec_location TEXT);')
        before = [('local', 'cli', 'local'), ('remote', 'cli', 'remote'), ('api', 'api', 'local')]
        conn.executemany('INSERT INTO ai_providers VALUES(?,?,?)', before)
        conn.executescript((SQL / 'sqlite' / MIGRATION).read_text(encoding='utf-8'))
        assert conn.execute('SELECT * FROM ai_providers ORDER BY rowid').fetchall() == [r + (None,) for r in before]
        conn.execute("INSERT INTO agents VALUES('agent')")
        conn.execute("UPDATE ai_providers SET target_agent_id='agent' WHERE provider_id='remote'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM agents WHERE agent_id='agent'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE ai_providers SET target_agent_id='missing' WHERE provider_id='remote'")


@pytest.mark.postgres
def test_postgres_live_migration():
    dsn = os.environ.get('FLOWGATE_PG_TEST_DSN') or os.environ.get('FLOWGATE_POSTGRES_TEST_DSN')
    if not dsn:
        pytest.skip('PostgreSQL test DSN is not configured')
    psycopg = pytest.importorskip('psycopg')
    schema = 'fg_target_0129_' + uuid.uuid4().hex[:12]
    with psycopg.connect(dsn, autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(f'SET search_path TO "{schema}"')
            conn.execute('CREATE TABLE agents(agent_id TEXT PRIMARY KEY)')
            conn.execute('CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY)')
            conn.execute("INSERT INTO ai_providers VALUES('old')")
            conn.execute((SQL / 'postgres' / MIGRATION).read_text(encoding='utf-8'))
            assert conn.execute('SELECT target_agent_id FROM ai_providers').fetchone() == (None,)
            conn.execute("INSERT INTO agents VALUES('agent')")
            conn.execute("UPDATE ai_providers SET target_agent_id='agent'")
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute("DELETE FROM agents WHERE agent_id='agent'")
        finally:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.mysql
def test_mysql_live_migration():
    dsn = os.environ.get('FLOWGATE_MYSQL_TEST_DSN')
    if not dsn:
        pytest.skip('MySQL test DSN is not configured')
    from urllib.parse import urlparse
    pymysql = pytest.importorskip('pymysql')
    parsed = urlparse(dsn)
    conn = pymysql.connect(host=parsed.hostname, port=parsed.port or 3306, user=parsed.username,
                           password=parsed.password or '', database=parsed.path.lstrip('/'), autocommit=True)
    schema = 'fg_target_0129_' + uuid.uuid4().hex[:12]
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE `{schema}`')
            cur.execute(f'USE `{schema}`')
            cur.execute('CREATE TABLE agents(agent_id VARCHAR(64) PRIMARY KEY)')
            cur.execute('CREATE TABLE ai_providers(provider_id VARCHAR(64) PRIMARY KEY)')
            cur.execute("INSERT INTO ai_providers VALUES('old')")
            script = re.sub(r'--[^\n]*', '', (SQL / 'mysql' / MIGRATION).read_text(encoding='utf-8'))
            for statement in script.split(';'):
                if statement.strip():
                    cur.execute(statement)
            cur.execute('SELECT target_agent_id FROM ai_providers')
            assert cur.fetchone() == (None,)
            cur.execute("INSERT INTO agents VALUES('agent')")
            cur.execute("UPDATE ai_providers SET target_agent_id='agent'")
            with pytest.raises(pymysql.err.IntegrityError):
                cur.execute("DELETE FROM agents WHERE agent_id='agent'")
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS `{schema}`')
        conn.close()
