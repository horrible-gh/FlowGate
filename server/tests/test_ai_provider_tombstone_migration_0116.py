"""0116 migration application, data preservation, name reuse and restart ledger."""
from pathlib import Path
import os
import shutil
import sqlite3
import uuid

import pytest

SQL = Path(__file__).parents[1] / 'sql/migrations'
NAME = '165_ai_provider_tombstones.sql'


def test_sqlite_existing_rows_and_restart_with_real_migrator(tmp_path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper
    db_path = tmp_path / 'existing.db'
    conn = sqlite3.connect(db_path)
    conn.executescript("""
      CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY, name TEXT NOT NULL,
        owner_scope TEXT, owner_user_id TEXT, api_key TEXT, cli_command TEXT, api_base_url TEXT);
      CREATE UNIQUE INDEX ux_ai_providers_system_name ON ai_providers(name) WHERE owner_scope='system';
      CREATE UNIQUE INDEX ux_ai_providers_user_name ON ai_providers(owner_user_id,name) WHERE owner_scope='user';
      INSERT INTO ai_providers VALUES ('old','original','system',NULL,'cipher','command','url');
      INSERT INTO ai_providers VALUES ('mine','original','user','alice','cipher','command','url');
    """)
    before = conn.execute('SELECT * FROM ai_providers ORDER BY provider_id').fetchall()
    conn.close()
    seed = tmp_path / 'migrations'
    seed.mkdir()
    shutil.copyfile(SQL / 'sqlite' / NAME, seed / NAME)
    DatabaseMigrator(SQLiteWrapper(str(db_path)),str(seed),auto_run=True)
    conn = sqlite3.connect(db_path)
    assert conn.execute('SELECT provider_id,name,owner_scope,owner_user_id,api_key,cli_command,api_base_url '
                        'FROM ai_providers ORDER BY provider_id').fetchall() == before
    assert conn.execute('SELECT deleted_at FROM ai_providers').fetchall() == [(None,),(None,)]
    # Live uniqueness is retained, while tombstones free the same owner's name.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO ai_providers(provider_id,name,owner_scope) VALUES ('duplicate','original','system')")
    conn.execute("UPDATE ai_providers SET deleted_at='time' WHERE provider_id='old'")
    conn.execute("INSERT INTO ai_providers(provider_id,name,owner_scope) VALUES ('new','original','system')")
    conn.execute("UPDATE ai_providers SET deleted_at='time' WHERE provider_id='mine'")
    conn.execute("INSERT INTO ai_providers(provider_id,name,owner_scope,owner_user_id) VALUES ('new-mine','original','user','alice')")
    conn.commit()
    saved = conn.execute('SELECT * FROM ai_providers ORDER BY provider_id').fetchall()
    conn.close()
    # Production reapplication means another startup with the same filename ledger.
    DatabaseMigrator(SQLiteWrapper(str(db_path)),str(seed),auto_run=True)
    conn = sqlite3.connect(db_path)
    assert conn.execute('SELECT * FROM ai_providers ORDER BY provider_id').fetchall() == saved
    conn.close()


def test_supported_dialects_keep_secrets_nullable_and_live_name_policy():
    for dialect in ['postgres','sqlite','mysql']:
        original=(SQL/dialect/'059_ai_provider_settings.sql').read_text(encoding='utf-8')
        for name in ['api_key','api_base_url','cli_command']:
            column=next(line for line in original.splitlines() if line.strip().startswith(name+' '))
            assert 'NOT NULL' not in column
        migration=(SQL/dialect/NAME).read_text(encoding='utf-8')
        assert 'deleted_at' in migration
        if dialect!='mysql':
            assert migration.count('AND deleted_at IS NULL') == 2
        else:
            assert 'service' in migration and '131' in migration


@pytest.mark.postgres
def test_postgres_migration_on_live_engine():
    dsn=os.environ.get('FLOWGATE_PG_TEST_DSN') or os.environ.get('FLOWGATE_POSTGRES_TEST_DSN')
    if not dsn: pytest.skip('PostgreSQL test DSN is not configured')
    psycopg=pytest.importorskip('psycopg')
    schema='fg_provider_0116_'+uuid.uuid4().hex[:12]
    with psycopg.connect(dsn,autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA "{schema}"')
            conn.execute(f'SET search_path TO "{schema}"')
            conn.execute('CREATE TABLE ai_providers(provider_id TEXT PRIMARY KEY,name TEXT NOT NULL,owner_scope TEXT,owner_user_id TEXT)')
            conn.execute("INSERT INTO ai_providers VALUES ('old','name','system',NULL)")
            migration=(SQL/'postgres'/NAME).read_text(encoding='utf-8')
            conn.execute(migration)
            conn.execute(migration)  # PostgreSQL DDL also supports direct reapplication.
            assert conn.execute('SELECT deleted_at FROM ai_providers').fetchone()==(None,)
            conn.execute("UPDATE ai_providers SET deleted_at='time' WHERE provider_id='old'")
            conn.execute("INSERT INTO ai_providers(provider_id,name,owner_scope) VALUES ('new','name','system')")
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("INSERT INTO ai_providers(provider_id,name,owner_scope) VALUES ('duplicate','name','system')")
        finally:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.mysql
def test_mysql_migration_on_live_engine():
    dsn=os.environ.get('FLOWGATE_MYSQL_TEST_DSN')
    if not dsn: pytest.skip('MySQL test DSN is not configured')
    from urllib.parse import urlparse
    pymysql=pytest.importorskip('pymysql')
    parsed=urlparse(dsn)
    conn=pymysql.connect(host=parsed.hostname,port=parsed.port or 3306,user=parsed.username,
                         password=parsed.password or '',database=parsed.path.lstrip('/'),autocommit=True)
    name='fg_provider_0116_'+uuid.uuid4().hex[:12]
    try:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE `{name}`')
            cur.execute(f'USE `{name}`')
            cur.execute('CREATE TABLE ai_providers(provider_id VARCHAR(191) PRIMARY KEY,name TEXT NOT NULL,owner_scope VARCHAR(10),owner_user_id VARCHAR(191))')
            cur.execute("INSERT INTO ai_providers VALUES ('old','name','system',NULL)")
            script=(SQL/'mysql'/NAME).read_text(encoding='utf-8')
            script='\n'.join(line for line in script.splitlines() if not line.startswith('--'))
            for statement in script.split(';'):
                if statement.strip():cur.execute(statement)
            cur.execute('SELECT deleted_at FROM ai_providers')
            assert cur.fetchone()==(None,)
    finally:
        with conn.cursor() as cur:cur.execute(f'DROP DATABASE IF EXISTS `{name}`')
        conn.close()
