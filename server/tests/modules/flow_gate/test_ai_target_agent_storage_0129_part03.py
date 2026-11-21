"""Independently selectable scenarios; shared fixtures remain in test_ai_target_agent_storage_0129.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_ai_target_agent_storage_0129 import (
    API,
    MIGRATION,
    Path,
    REMOTE_BODY,
    SQL,
    agent,
    env,
    error,
    get,
    os,
    provider,
    pytest,
    re,
    routes,
    save,
    settings_env,
    sqlite3,
    uuid,
)

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
