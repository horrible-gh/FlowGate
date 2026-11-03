"""0120 T#1: real migrated SQLite, transaction/concurrency/API regressions."""
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


@pytest.fixture
def world(migrated_sqlite_db, monkeypatch):
    from modules.flow_gate.db import _SqliteDbAdapter, connection, ai_groups, ai_group_role_profiles
    from modules.flow_gate.services import ai_group_service as groups, ai_group_role_profile_service as profiles
    from modules.flow_gate.api.v1 import ai_group_routes
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.rbac import decorators
    path = migrated_sqlite_db("role-profiles.db")
    store = connection.FlowGateStore()
    store._db = _SqliteDbAdapter(path)
    monkeypatch.setattr(connection, "STORE", store)
    for pid, name, active in [("p1", "Model One", 1), ("p2", "Model Two", 1), ("off", "Inactive", 0)]:
        store._execute("INSERT INTO ai_providers (provider_id,name,exec_type,kind,enabled,owner_scope,created_at,updated_at) "
                       "VALUES (?,?,'cli','codex',?,'system','t','t')", [pid, name, active])
    # Exercise real provider lookup/deletion and catalog; no executable/provider calls.
    monkeypatch.setattr(groups, "_available", lambda: {row['provider_id']: {**row, 'id': row['provider_id']}
        for row in store._fetch_all("SELECT * FROM ai_providers WHERE enabled = 1", [])})
    monkeypatch.setattr(decorators._permission_service, "has_permission", lambda *a: False)
    app = FastAPI()
    app.include_router(ai_group_routes.router, prefix="/api/v1")
    def current(request: Request):
        user = request.headers.get("X-User")
        if not user:
            raise HTTPException(401)
        return {"user_id": user, "is_admin": user == "admin"}
    app.dependency_overrides[get_current_user] = current
    client = TestClient(app, raise_server_exceptions=False)
    return dict(path=path, store=store, groups=groups, profiles=profiles, repo=ai_group_role_profiles,
                groups_repo=ai_groups, client=client)


def create(w, name="Roles", kind="role"):
    return w['groups'].create_group({'name': name, 'group_type': kind})['ai_group_id']


def test_create_default_general_single_common_and_immutable_type(world):
    g = world['groups'].create_group({'name': 'AI 역할', 'description': 'keep'})
    assert g['group_type'] == 'general'
    assert world['repo'].list_profiles(g['ai_group_id']) == []
    gid = create(world)
    assert world['profiles'].profiles(gid)['items'] == [{
        'type_key': 'common', 'position': 0,
        'roles': {r: {'kind': 'unset'} for r in ('instruction','execution','review')}}]
    with pytest.raises(world['groups'].AIGroupError) as exc:
        create(world, 'Another')
    assert (exc.value.code, exc.value.status) == ('ai_role_group_exists', 409)
    c = world['client']
    response = c.put('/api/v1/ai-groups/'+gid, headers={'X-User':'admin'}, json={'name':'Changed','group_type':'general'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'ai_group_type_immutable'
    updated = c.put('/api/v1/ai-groups/'+g['ai_group_id'], headers={'X-User':'admin'}, json={'name':'Renamed'})
    assert updated.status_code == 200 and updated.json()['description'] == 'keep'


def test_concurrent_role_creation_database_winner_and_409(world, monkeypatch):
    barrier = threading.Barrier(2)
    validate = world['groups']._validated
    def synchronized(*a, **kw):
        row = validate(*a, **kw)
        barrier.wait(timeout=10)
        return row
    monkeypatch.setattr(world['groups'], '_validated', synchronized)
    def attempt(name):
        try:
            return create(world, name)
        except world['groups'].AIGroupError as exc:
            return exc.code, exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ['Role A','Role B']))
    assert results.count(('ai_role_group_exists',409)) == 1
    assert len(world['groups_repo'].list_all()) == 1
    assert len(world['store']._fetch_all('SELECT * FROM ai_group_role_profiles', [])) == 1


def test_general_members_roles_binding_preserved_and_role_default_key_independent(world):
    g = create(world, 'General', 'general')
    groups = world['groups']
    member = groups.add_member(g, 'p1')['members'][0]['member_id']
    role = groups.create_role(g, {'name':'Custom'})['roles'][0]['role_id']
    groups.set_member_roles(g, member, [role])
    groups.update_group(g, {'name':'General','supervisor_member_id':member}, supervisor_given=True)
    groups.set_runtime_binding(g, {'work_executor_provider_id':'p1'})
    groups.set_state(g, True, True)
    before = groups.get_group(g)
    rid = create(world)
    world['profiles'].add(rid, 'T')
    world['profiles'].set_cell(rid, 'common', 'review', {'kind':'assigned','provider_id':'p2'})
    assert groups.get_group(g) == before
    assert world['groups_repo'].get(rid)['role_group_key'] == 'role'
    assert world['groups_repo'].get(g)['default_key'] == 'default'
    for action in [lambda: world['profiles'].add(g, 'T'),
                   lambda: world['profiles'].set_cell(g, 'common', 'review', {'kind':'not_used'}),
                   lambda: world['profiles'].remove(g, 'T')]:
        with pytest.raises(groups.AIGroupError, match='ai_role_group_required'):
            action()


def test_persisted_inherit_independent_cells_not_used_delete_and_idempotence(world):
    gid = create(world)
    p = world['profiles']
    p.add(gid, 'T')
    before = world['repo'].get(gid, 'T')
    assert p.add(gid, 'T') == p.profiles(gid)
    assert world['repo'].get(gid, 'T') == before
    assert all(before[r+'_kind'] == 'inherit' and before[r+'_provider_id'] is None for r in world['repo'].ROLES)
    p.set_cell(gid,'common','instruction',{'kind':'assigned','provider_id':'p1'})
    p.set_cell(gid,'common','review',{'kind':'assigned','provider_id':'p2'})
    p.set_cell(gid,'T','execution',{'kind':'not_used'})
    row = world['repo'].get(gid, 'T')
    assert row['execution_kind'] == 'not_used' and row['instruction_kind'] == 'inherit'
    assert row['instruction_provider_id'] is None and row['review_provider_id'] is None
    p.set_cell(gid,'common','instruction',{'kind':'unset'})
    assert world['repo'].get(gid, 'T') == row
    with pytest.raises(world['groups'].AIGroupError, match='ai_role_profile_common_required'):
        p.remove(gid, 'common')
    assert len(p.remove(gid, 'T')['items']) == 1
    assert len(p.add(gid, 'T')['items']) == 2


@pytest.mark.parametrize('key,role,value', [
    ('common','instruction',{'kind':'inherit'}), ('T','execution',{'kind':'unset'}),
    ('T','unknown',{'kind':'inherit'}), ('T','review',{'kind':'assigned','provider_id':'off'}),
    ('T','review',{'kind':'assigned','provider_id':'missing'}),
    ('T','review',{'kind':'not_used','provider_id':'p1'}),
    ('T','review',{'kind':'assigned','provider_id':' p1'}),
    ('T','review',{'kind':'assigned','provider_id':True}),
    ('T','review',{'kind':'not_used','surprise':1}),
])
def test_api_strict_validation_leaves_all_rows_unchanged(world, key, role, value):
    gid = create(world)
    world['profiles'].add(gid, 'T')
    before = world['repo'].list_profiles(gid)
    response = world['client'].put(f'/api/v1/ai-groups/{gid}/role-profiles/{key}/roles/{role}',
                                    headers={'X-User':'admin'},json=value)
    assert response.status_code == 422, response.text
    assert world['repo'].list_profiles(gid) == before


def test_active_catalog_options_and_api_permissions(world):
    gid = create(world)
    c, base = world['client'], f'/api/v1/ai-groups/{gid}'
    assert c.get(base+'/role-profiles').status_code == 401
    assert c.get(base+'/role-profile-options',headers={'X-User':'user'}).status_code == 200
    choices = c.get(base+'/role-profile-options?locale=en',headers={'X-User':'user'}).json()
    assert [t['type_key'] for t in choices['types']] == ['D','P','L','DB','N','WP','T','TS']
    assert [t['label'] for t in choices['types']] == ['Basic design','Protocol','Logic','DB design','Research','Work plan','Task','Test']
    assert {p['provider_id'] for p in choices['providers']} == {'p1','p2'}
    assert all(set(p) == {'provider_id','provider_name'} for p in choices['providers'])
    for method, path, body in [('PUT','/role-profiles/T',{}),
                              ('PUT','/role-profiles/common/roles/review',{'kind':'not_used'}),
                              ('DELETE','/role-profiles/T',None)]:
        assert c.request(method,base+path,headers={'X-User':'user'},json=body).status_code == 403
    for key in ('상세 설계', 'run', 'invalid-key', 'confirm', 'R', 'B', 'NR', 'TR', 'CH'):
        assert c.put(base+'/role-profiles/'+key,headers={'X-User':'admin'},json={}).status_code == 422
    assert c.put(base+'/role-profiles/T',headers={'X-User':'admin'},json={}).status_code == 200
    response = c.put(base+'/role-profiles/T/roles/review',headers={'X-User':'admin'},json={'kind':'assigned','provider_id':'p1'})
    assert response.status_code == 200
    assert response.json() == c.get(base+'/role-profiles',headers={'X-User':'user'}).json()
    assert c.delete(base+'/role-profiles/T',headers={'X-User':'admin'}).status_code == 200


def test_inactive_deleted_provider_snapshot_is_preserved_without_reassignment(world):
    gid = create(world)
    p = world['profiles']
    p.set_cell(gid,'common','instruction',{'kind':'assigned','provider_id':'p1'})
    world['store']._execute("UPDATE ai_providers SET enabled=0 WHERE provider_id='p1'",[])
    value = p.profiles(gid)['items'][0]['roles']['instruction']
    assert not value['is_available'] and value['status'] == 'unavailable'
    p.set_cell(gid,'common','instruction',{'kind':'assigned','provider_id':'p1'})
    world['store']._execute("DELETE FROM ai_providers WHERE provider_id='p1'",[])
    value = p.profiles(gid)['items'][0]['roles']['instruction']
    assert value['provider_id'] == 'p1' and value['provider_name'] == 'Model One' and value['status'] == 'broken'
    p.set_cell(gid,'common','execution',{'kind':'not_used'})
    assert p.profiles(gid)['items'][0]['roles']['instruction'] == value
    with pytest.raises(world['groups'].AIGroupError, match='ai_group_provider_unavailable'):
        p.set_cell(gid,'common','review',{'kind':'assigned','provider_id':'p1'})


def test_create_and_cell_rollback_and_read_errors_are_not_success(world, monkeypatch):
    def fail(*a, **kw): raise RuntimeError('db failure')
    with monkeypatch.context() as m:
        m.setattr(world['repo'],'create',fail)
        with pytest.raises(RuntimeError): create(world)
    assert world['groups_repo'].list_all() == []
    gid = create(world)
    before = world['repo'].list_profiles(gid)
    real = world['repo'].set_cell
    def write_then_fail(*a, **kw):
        real(*a, **kw)
        raise RuntimeError('rollback')
    with monkeypatch.context() as m:
        m.setattr(world['repo'],'set_cell',write_then_fail)
        with pytest.raises(RuntimeError):
            world['profiles'].set_cell(gid,'common','review',{'kind':'not_used'})
    assert world['repo'].list_profiles(gid) == before
    monkeypatch.setattr(world['repo'],'list_profiles',fail)
    assert world['client'].get(f'/api/v1/ai-groups/{gid}/role-profiles',headers={'X-User':'user'}).status_code == 500


def test_concurrent_idempotent_add_and_separate_cells_preserve_both_writes(world):
    gid = create(world)
    p = world['profiles']
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: p.add(gid,'T'), range(2)))
    assert len(p.profiles(gid)['items']) == 2
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda role: p.set_cell(gid,'T',role,{'kind':'not_used'}), ['instruction','review']))
    row = world['repo'].get(gid,'T')
    assert row['instruction_kind'] == row['review_kind'] == 'not_used' and row['execution_kind'] == 'inherit'


def test_sqlite_migration_preserves_existing_named_role_group_and_children(world):
    gid = create(world,'AI 역할','general')
    groups = world['groups']
    member = groups.add_member(gid,'p1')['members'][0]['member_id']
    role = groups.create_role(gid,{'name':'Existing'})['roles'][0]['role_id']
    groups.set_member_roles(gid,member,[role])
    groups.set_runtime_binding(gid,{'work_executor_provider_id':'p1'})
    tables = ['ai_group_members','ai_group_roles','ai_group_member_roles','ai_group_runtime_bindings']
    before = {t:world['store']._fetch_all(f'SELECT * FROM {t}',[]) for t in tables}
    with sqlite3.connect(world['path']) as db:
        # Remove only 169's empty additions, restoring the pre-169 schema with live rows.
        db.executescript('DROP TABLE ai_group_role_profiles; DROP INDEX uq_ai_groups_role_group_key; '
                         'ALTER TABLE ai_groups DROP COLUMN role_group_key; ALTER TABLE ai_groups DROP COLUMN group_type;')
        migration = Path(__file__).resolve().parents[3]/'sql/migrations/sqlite/169_ai_group_role_profiles.sql'
        db.executescript(migration.read_text(encoding='utf-8'))
    row = world['groups_repo'].get(gid)
    assert row['name'] == 'AI 역할' and row['group_type'] == 'general' and row['role_group_key'] is None
    assert world['repo'].list_profiles(gid) == []
    assert {t:world['store']._fetch_all(f'SELECT * FROM {t}',[]) for t in tables} == before
    with pytest.raises(sqlite3.IntegrityError):
        world['store']._execute("UPDATE ai_groups SET group_type='role' WHERE ai_group_id=?",[gid])
    create(world)
    with pytest.raises(sqlite3.IntegrityError):
        world['store']._execute("UPDATE ai_groups SET group_type='role',role_group_key='role' WHERE ai_group_id=?",[gid])


def test_group_delete_removes_only_its_profiles(world):
    gid = create(world)
    general = create(world,'General','general')
    world['profiles'].add(gid,'T')
    world['groups'].delete_group(gid)
    assert world['repo'].list_profiles(gid) == []
    assert world['groups'].get_group(general)['group_type'] == 'general'
    assert len(world['profiles'].profiles(create(world,'New roles'))['items']) == 1


def test_commit_failure_rolls_back_and_is_not_a_success_response(world, monkeypatch):
    from contextlib import contextmanager
    gid = create(world)
    before = world['repo'].list_profiles(gid)
    original = world['store']._db.begin_transaction
    @contextmanager
    def failed_commit():
        with original() as txn:
            yield txn
            raise RuntimeError('commit failed')
    monkeypatch.setattr(world['store']._db, 'begin_transaction', failed_commit)
    response = world['client'].put(f'/api/v1/ai-groups/{gid}/role-profiles/common/roles/review',
                                    headers={'X-User':'admin'},json={'kind':'not_used'})
    assert response.status_code == 500
    assert world['repo'].list_profiles(gid) == before
