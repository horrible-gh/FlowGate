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
