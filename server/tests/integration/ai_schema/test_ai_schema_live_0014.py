"""Opt-in execution of the v02 AI migrations against real database engines."""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[3] / 'sql' / 'migrations'


def _statements(dialect, number):
    text = (ROOT / dialect / f'{number}_ai_evaluations_and_groups.sql').read_text()
    return [statement.strip() for statement in text.split(';') if statement.strip()]


def _exercise_constraints(execute):
    def rejects(sql):
        try:
            execute(sql)
        except Exception as exc:
            state = getattr(exc, 'sqlstate', None)
            code = exc.args[0] if exc.args else None
            assert (state and state.startswith('23')) or code in (1062, 3819, 4025), exc
        else:
            pytest.fail('database accepted a value that violates the migration constraint')

    execute("INSERT INTO projects(project_id) VALUES ('p')")
    execute("INSERT INTO ai_evaluation_runs(evaluation_id,project_id,project_run_seq,provider_id,"
            "subject_fingerprint,active_dedupe_key,lifecycle,created_at,updated_at) VALUES "
            "('e1','p',1,'provider','" + 'a' * 64 + "','" + 'b' * 64 + "','pending','t','t')")
    rejects("INSERT INTO ai_evaluation_runs(evaluation_id,project_id,project_run_seq,provider_id,"
                "subject_fingerprint,active_dedupe_key,lifecycle,created_at,updated_at) VALUES "
                "('e2','p',2,'provider','" + 'a' * 64 + "','" + 'b' * 64 + "','pending','t','t')")
    rejects("INSERT INTO ai_evaluation_runs(evaluation_id,project_id,project_run_seq,provider_id,"
                "subject_fingerprint,lifecycle,created_at,updated_at) VALUES "
                "('short','p',3,'provider','short','pending','t','t')")
    rejects("INSERT INTO ai_evaluation_runs(evaluation_id,project_id,project_run_seq,provider_id,"
                "subject_fingerprint,lifecycle,created_at,updated_at) VALUES "
                "('zero','p',0,'provider','" + 'a' * 64 + "','pending','t','t')")
    execute("INSERT INTO ai_groups(ai_group_id,project_id,name,name_key,name_key_hash,"
            "work_executor_provider_id,created_at,updated_at) VALUES "
            "('g1','p','A','a','" + 'c' * 64 + "','provider','t','t')")
    rejects("INSERT INTO ai_groups(ai_group_id,project_id,name,name_key,name_key_hash,"
                "work_executor_provider_id,created_at,updated_at) VALUES "
                "('g2','p','B','b','" + 'c' * 64 + "','provider','t','t')")
    rejects("INSERT INTO ai_groups(ai_group_id,project_id,name,name_key,name_key_hash,"
                "work_executor_provider_id,created_at,updated_at) VALUES "
                "('short','p','C','c','short','provider','t','t')")
    execute("DELETE FROM projects WHERE project_id='p'")
    assert execute('SELECT COUNT(*) FROM ai_evaluation_runs', fetch=True)[0] == 0
    assert execute('SELECT COUNT(*) FROM ai_groups', fetch=True)[0] == 0


@pytest.mark.mysql
def test_mysql_migration_executes_with_constraints():
    dsn = os.environ.get('FLOWGATE_MYSQL_TEST_DSN')
    if not dsn:
        pytest.skip('FLOWGATE_MYSQL_TEST_DSN is not configured')
    pymysql = pytest.importorskip('pymysql')
    parsed = urlparse(dsn)
    kwargs = dict(host=parsed.hostname, port=parsed.port or 3306, user=parsed.username,
                  password=parsed.password or '', database=parsed.path.lstrip('/'), autocommit=True)
    name = 'fg_ai_0014_' + uuid.uuid4().hex[:12]
    admin = pymysql.connect(**kwargs)
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE `{name}`')
        conn = pymysql.connect(**{**kwargs, 'database': name})
        try:
            def execute(sql, fetch=False):
                with conn.cursor() as cur:
                    cur.execute(sql)
                    return cur.fetchone() if fetch else None
            execute('CREATE TABLE projects(project_id VARCHAR(191) PRIMARY KEY)')
            execute('CREATE TABLE users(user_id VARCHAR(191) PRIMARY KEY)')
            execute('CREATE TABLE ai_invoke_runs(run_id VARCHAR(191) PRIMARY KEY)')
            execute('CREATE TABLE ai_invoke_paused_chains(group_id VARCHAR(191) PRIMARY KEY)')
            for statement in _statements('mysql', '124'):
                execute(statement)
            for statement in (ROOT / 'mysql' / '125_ai_group_pause_roles.sql').read_text().split(';'):
                if statement.strip():
                    execute(statement)
            execute("INSERT INTO ai_invoke_paused_chains(group_id,requested_ai_group_id,"
                    "continuation_reworker_provider_id) VALUES ('chain','deleted','provider')")
            assert execute("SELECT requested_ai_group_id,continuation_reworker_provider_id "
                           "FROM ai_invoke_paused_chains WHERE group_id='chain'", True) == ('deleted', 'provider')
            _exercise_constraints(execute)
        finally:
            conn.close()
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS `{name}`')
        admin.close()


@pytest.mark.postgres
def test_postgres_migration_executes_with_constraints():
    dsn = os.environ.get('FLOWGATE_POSTGRES_TEST_DSN')
    if not dsn:
        pytest.skip('FLOWGATE_POSTGRES_TEST_DSN is not configured')
    psycopg = pytest.importorskip('psycopg')
    name = 'fg_ai_0014_' + uuid.uuid4().hex[:12]
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        def execute(sql, fetch=False):
            with conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchone() if fetch else None
        execute(f'CREATE SCHEMA "{name}"')
        execute(f'SET search_path TO "{name}"')
        execute('CREATE TABLE projects(project_id TEXT PRIMARY KEY)')
        execute('CREATE TABLE users(user_id TEXT PRIMARY KEY)')
        execute('CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY)')
        execute('CREATE TABLE ai_invoke_paused_chains(group_id TEXT PRIMARY KEY)')
        for statement in _statements('postgres', '124'):
            execute(statement)
        for statement in (ROOT / 'postgres' / '125_ai_group_pause_roles.sql').read_text().split(';'):
            if statement.strip():
                execute(statement)
        execute("INSERT INTO ai_invoke_paused_chains(group_id,requested_ai_group_id,"
                "continuation_reworker_provider_id) VALUES ('chain','deleted','provider')")
        assert execute("SELECT requested_ai_group_id,continuation_reworker_provider_id "
                       "FROM ai_invoke_paused_chains WHERE group_id='chain'", True) == ('deleted', 'provider')
        _exercise_constraints(execute)
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        conn.close()
