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
