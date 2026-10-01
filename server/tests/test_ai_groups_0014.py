"""AI Group CRUD, logical role references, and provenance regression tests."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

os.environ['TESTING'] = '1'
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-testing-only-32c')
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))


class Store:
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE projects(project_id TEXT PRIMARY KEY);
            CREATE TABLE users(user_id TEXT PRIMARY KEY);
            CREATE TABLE ai_invoke_runs(run_id TEXT PRIMARY KEY);
            INSERT INTO projects VALUES ('p'), ('other');
        ''')
        self.db.executescript((SERVER / 'sql/migrations/sqlite/124_ai_evaluations_and_groups.sql').read_text())

    def _fetch_one(self, sql, params=None):
        row = self.db.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(row) for row in self.db.execute(sql, params or []).fetchall()]

    def _execute(self, sql, params=None):
        self.db.execute(sql, params or [])
        self.db.commit()


@pytest.fixture
def world(monkeypatch):
    from modules.flow_gate.db import ai_groups as repo
    from modules.flow_gate.services import ai_group_service as svc
    store = Store()
    monkeypatch.setattr(repo, 'get_store', lambda: store)
    monkeypatch.setattr(repo, 'now_iso', lambda: '2026-09-29T00:00:00Z')
    monkeypatch.setattr(svc.projects, 'get_by_id', lambda p: {'project_id': p} if p in ('p', 'other') else None)
    providers = {'a': {'name': 'A'}, 'sys': {'name': 'System'}}
    enabled = {'a', 'sys'}
    monkeypatch.setattr(svc, '_registry_provider', lambda _project, provider_id: providers.get(provider_id))
    monkeypatch.setattr(svc, '_available', lambda _project: {p: providers[p] for p in enabled})
    yield svc, repo, store, providers, enabled
    store.db.close()


def _input(name='Primary', executor='a', reviewer=None, reworker=None):
    return {'name': name, 'description': 'group', 'work_executor_provider_id': executor,
            'reviewer_provider_id': reviewer, 'reworker_provider_id': reworker}


def test_crud_canonical_roles_and_project_ownership(world):
    svc, repo, store, providers, enabled = world
    created = svc.create_group('p', _input(' Primary ', reviewer='sys'))
    group_id = created['ai_group_id']
    assert created['name'] == 'Primary'
    assert created['work_executor'] == {'provider_id': 'a', 'provider_name': 'A', 'is_available': True}
    assert created['reviewer']['provider_id'] == 'sys'
    assert created['reworker'] is None
    assert 'work_executor_provider_id' not in created
    assert svc.list_groups('p')['items'][0] == created
    with pytest.raises(svc.AIGroupError) as missing:
        svc.get_group('other', group_id)
    assert missing.value.code == 'ai_group_not_found'
    assert svc.create_group('other', _input())['name'] == 'Primary'
    updated = svc.update_group('p', group_id, _input('Changed', reworker='sys'))
    assert updated['reviewer'] is None and updated['reworker']['provider_id'] == 'sys'
    svc.delete_group('p', group_id)
    assert repo.get('p', group_id) is None


def test_name_limits_duplicates_and_collision_guard(world, monkeypatch):
    svc, repo, *_ = world
    svc.create_group('p', _input('Ａbc'))
    with pytest.raises(svc.AIGroupError) as duplicate:
        svc.create_group('p', _input('abc'))
    assert duplicate.value.code == 'ai_group_name_conflict'
    for name in ('x' * 201, '\ufdfa' * 200):
        with pytest.raises(svc.AIGroupError):
            svc.create_group('p', _input(name))
    with pytest.raises(svc.AIGroupError):
        svc.create_group('p', {**_input(), 'description': 'x' * 2001})
    original = svc._name
    monkeypatch.setattr(svc, '_name', lambda name: (name, name, original('abc')[2]))
    with pytest.raises(svc.AIGroupError) as collision:
        svc.create_group('p', _input('different'))
    assert collision.value.code == 'ai_group_hash_collision'


def test_broken_references_and_start_provenance_survive_delete(world):
    svc, repo, store, providers, enabled = world
    created = svc.create_group('p', _input(reviewer='sys'))
    group_id = created['ai_group_id']
    store.db.execute('INSERT INTO ai_invoke_runs(run_id, requested_ai_group_id) VALUES (?, ?)', ('run', group_id))
    enabled.remove('sys')
    assert svc.get_group('p', group_id)['reviewer'] == {
        'provider_id': 'sys', 'provider_name': 'System', 'is_available': False}
    del providers['sys']
    assert svc.get_group('p', group_id)['reviewer'] == {
        'provider_id': 'sys', 'provider_name': None, 'is_available': False}
    svc.validate_start('p', group_id)
    svc.delete_group('p', group_id)
    assert store.db.execute('SELECT requested_ai_group_id FROM ai_invoke_runs WHERE run_id=?', ('run',)).fetchone()[0] == group_id
    with pytest.raises(svc.AIGroupError) as deleted:
        svc.validate_start('p', group_id)
    assert (deleted.value.code, deleted.value.status) == ('ai_group_not_found', 409)


def test_save_rejects_unavailable_and_requires_executor(world):
    svc, repo, store, providers, enabled = world
    enabled.remove('sys')
    with pytest.raises(svc.AIGroupError) as unavailable:
        svc.create_group('p', _input(reviewer='sys'))
    assert unavailable.value.code == 'ai_group_provider_unavailable'
    with pytest.raises(svc.AIGroupError) as required:
        svc.create_group('p', _input(executor=None))
    assert required.value.code == 'ai_group_work_executor_required'


def test_finished_run_binds_requested_group_without_fk(monkeypatch):
    from modules.flow_gate.db import ai_invoke_runs
    captured = {}
    class Capture:
        def _execute(self, sql, params):
            captured['sql'] = sql
            captured['params'] = params
    monkeypatch.setattr(ai_invoke_runs, 'get_store', lambda: Capture())
    row = {'run_id': 'run', 'group_id': 'p.m.g', 'project_id': 'p', 'doc_ref': 'doc',
           'mode': 'continuous', 'requested_ai_group_id': 'deleted-preset',
           'started_at': 't', 'finished_at': 't', 'created_at': 't', 'updated_at': 't'}
    ai_invoke_runs.upsert(row)
    index = ai_invoke_runs._BOUND_COLUMNS.index('requested_ai_group_id')
    assert captured['params'][index] == 'deleted-preset'
    assert 'requested_ai_group_id' in captured['sql']


def test_routes_reads_open_writes_administrator_only(monkeypatch):
    """CH0006 #48: a group of system AI is read by every signed-in user and edited by an
    administrator only (system.settings.manage, no project)."""
    from fastapi import HTTPException
    from starlette.requests import Request
    from modules.flow_gate.api.v1.ai_group_routes import router
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.rbac import decorators
    seen = []
    monkeypatch.setattr(decorators, '_has_permission',
                        lambda user, permission, project: seen.append((permission, project)) or False)
    request = Request({'type': 'http', 'method': 'POST', 'path': '/',
                       'headers': [], 'query_string': b'', 'path_params': {}})
    for route in router.routes:
        checker = route.dependant.dependencies[0].call
        if 'GET' in route.methods:
            assert checker is get_current_user
            continue
        with pytest.raises(HTTPException) as denied:
            checker(request, user={'user_id': 'u', 'is_admin': False})
        assert denied.value.status_code == 403
        assert seen[-1] == ('system.settings.manage', None)


def test_pause_row_preserves_group_and_reworker_snapshot(monkeypatch):
    from modules.flow_gate.db import ai_invoke_paused_chains as paused
    db = sqlite3.connect(':memory:')
    columns = [
        'group_id TEXT PRIMARY KEY', 'doc_ref TEXT', 'mode TEXT', 'paused_by TEXT',
        'paused_at TEXT', 'continuation_target_seq INTEGER', 'docs_target INTEGER',
        'docs_reached INTEGER', 'chain_id TEXT', 'chain_docs_target INTEGER',
        'chain_docs_reached INTEGER', 'stop_kind TEXT', 'stop_code TEXT',
        'stop_run_id TEXT', 'stop_last_message_excerpt TEXT',
        'continuation_base_provider_id TEXT', 'continuation_provider_pinned INTEGER',
        'continuation_provider_overrides TEXT',
        'continuation_work_executor_provider_id TEXT', 'continuation_default_note TEXT',
        'continuation_note_overrides TEXT', 'continuation_instruction_mode TEXT',
        'continuation_auto_approve_item_seqs TEXT', 'continuation_step_timeout_sec INTEGER',
        'continuation_restart_max_attempts INTEGER',
        'continuation_review_count_overrides TEXT', 'continuation_reviewer_overrides TEXT',
        'created_at TEXT', 'updated_at TEXT',
    ]
    db.execute('CREATE TABLE ai_invoke_paused_chains (' + ', '.join(columns) + ')')
    db.execute("INSERT INTO ai_invoke_paused_chains(group_id) VALUES ('legacy')")
    db.executescript((SERVER / 'sql/migrations/sqlite/125_ai_group_pause_roles.sql').read_text())
    assert db.execute('SELECT requested_ai_group_id, continuation_reworker_provider_id '
                      "FROM ai_invoke_paused_chains WHERE group_id='legacy'").fetchone() == (None, None)
    class PauseStore:
        def _execute(self, sql, params):
            db.execute(sql, params)
        def _fetch_one(self, sql, params):
            row = db.execute(sql, params).fetchone()
            return dict(zip([column[0] for column in db.execute(sql, params).description], row)) if row else None
    monkeypatch.setattr(paused, 'get_store', lambda: PauseStore())
    kwargs = dict(group_id='chain', doc_ref='doc', paused_by='user', paused_at='t',
                  continuation_target_seq=2, docs_target=1, docs_reached=0)
    paused.upsert(**kwargs, requested_ai_group_id='aig_deleted',
                  continuation_reworker_provider_id='aip_reworker')
    row = paused.get_by_group('chain')
    assert row['requested_ai_group_id'] == 'aig_deleted'
    assert row['continuation_reworker_provider_id'] == 'aip_reworker'
    paused.upsert(**kwargs, requested_ai_group_id='aig_deleted',
                  continuation_reworker_provider_id='aip_override')
    assert paused.get_by_group('chain')['continuation_reworker_provider_id'] == 'aip_override'
    db.close()
