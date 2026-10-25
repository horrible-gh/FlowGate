"""0083 WP-2: card recovery through HTTP, real DB/fencing and normal Resume dispatch.
Fixtures model stale/abnormal state; production repair never manipulates the DB directly.
"""
from __future__ import annotations

import pytest
from test_fg_card_settings import FA, _h, _sql, _start, db_path, decided, env
from test_fg_card_approval_0056 import LEE, _current, _run_id, card_approval_port
from test_fg_card_settings_review import _executions_ended, _node
from test_fg_wp_ai_run_0083 import (
    OPUS, _cards, _opus, _tokens, _attempt, _run_status, _inbox, _wp_json,
    _wp_documents, _grant_document_create, _admin_and_reviewer,
)


# A result id no other node holds: the root R card is bound to 0001-R (unique per run).
RESULT = f'{FA}.0090-WP'


def _recover(env, key, headers=None):
    return env['client'].post(f'/api/v1/runs/{_run_id(env)}/nodes/{key}/recover',
                              json={}, headers=_h() if headers is None else headers)


def _open_person(env, monkeypatch, *, work_ai=False):
    from modules.flow_gate.services import token_service
    from modules.flow_gate.graph_workflow.services import card_settings_ports as ports
    from flowgate.card_settings import settings
    issued = []
    issue = token_service.issue
    def capture(**kw):
        token = issue(**kw)
        issued.append(token)
        return token
    monkeypatch.setattr(token_service, 'issue', capture)
    _opus(env)
    [key] = _cards(env, [('WP', None if work_ai else LEE)],
                   {0: (('run_ai', OPUS),)} if work_ai else None)
    if work_ai:
        # The old PLAN judgement: recreate the reported open/active/no-job state.
        ports.register(ports.USES_AI, lambda flow_id, node: False)
    try:
        _start(env)
    finally:
        if work_ai:
            ports.register(ports.USES_AI, settings.uses_ai)
    assert _run_status(env)[0] == 'idle' and not env['jobs']
    return key, issued[-1]


def _snapshot(env):
    return {table: _sql(env['db'], f'SELECT * FROM {table}') for table in (
        'graph_runs', 'graph_node_runs', 'graph_node_attempts', 'graph_attempt_runs',
        'graph_run_token_map', 'tokens', 'graph_run_events', 'documents', 'graph_node_documents')}


@pytest.mark.parametrize('work_ai', [False, True], ids=['human_new_attempt', 'wp_work_ai'])
def test_recovery_then_resume_reassigns_with_new_attempt_and_tokens(env, decided, monkeypatch, work_ai):
    key, token = _open_person(env, monkeypatch, work_ai=work_ai)
    before = _snapshot(env)
    old_attempt = next(a for a in before['graph_node_attempts'] if a['node_key'] == key)
    response = _recover(env, key)
    assert response.status_code == 200, response.text
    assert _node(env, key) == ('pending', None)
    assert _attempt(env, key) == ['invalidated']
    after = _snapshot(env)
    invalid = next(a for a in after['graph_node_attempts'] if a['node_key'] == key)
    assert invalid['invalidated_reason'] == 'card_recovery' and invalid['active_node_key'] is None
    for field in ('opened_at', 'finished_at', 'dispatch_token_issued', 'dispatch_issued_to',
                  'dispatch_mention_id', 'dispatch_error_code', 'stage', 'instruction_doc_id', 'result_event_floor'):
        row = next(n for n in after['graph_node_runs'] if n['node_key'] == key)
        assert row[field] is None, field
    assert after['documents'] == before['documents']
    assert after['graph_node_documents'] == before['graph_node_documents']
    assert after['graph_attempt_runs'] == before['graph_attempt_runs']
    assert [n for n in after['graph_node_runs'] if n['node_key'] != key] == [
        n for n in before['graph_node_runs'] if n['node_key'] != key]
    assert len(after['graph_run_events']) > len(before['graph_run_events'])
    assert any(e['cause'] == 'card_recovery' and e['node_key'] == key for e in after['graph_run_events'])
    assert _run_status(env)[0] == 'idle' and not env['jobs']
    resumed = env['client'].post(f'/api/v1/runs/{_run_id(env)}/resume', json={'mode': 'full'}, headers=_h())
    assert resumed.status_code == 200, resumed.text
    assert _node(env, key) == ('open', None)
    attempts = _sql(env['db'], 'SELECT * FROM graph_node_attempts WHERE node_key = ?', (key,))
    active = next(a for a in attempts if a['status'] == 'active')
    assert active['attempt_id'] != old_attempt['attempt_id']
    tokens = _tokens(env, key)
    assert tokens[-1]['token_id'] != token['token_id']
    if work_ai:
        [job] = env['jobs']
        assert job['provider_id'] == OPUS and job['graph_attempt_id'] == active['attempt_id']
    else:
        assert not env['jobs'] and tokens[-1]['issued_to'] == 'u_lee'
        assert _run_status(env) == ('idle', 'waiting_approval')


@pytest.mark.parametrize('case', ['review', 'done', 'pending', 'result', 'running', 'paused', 'stopping',
                                  'stopped', 'tm', 'requested', 'execution_running', 'live_job',
                                  'provider_active', 'legacy_job', 'no_attempt', 'root_bound'])
def test_recovery_refuses_outside_contract_without_mutation(env, decided, monkeypatch, case):
    from modules.flow_gate.graph_workflow.store import run_store
    from modules.flow_gate.services import ai_invoke_service
    from flowgate.graph_time_machine import operation
    from flowgate.graph_run import execution_store as ledger
    from flowgate.card_settings import legacy_adapter
    key, token = _open_person(env, monkeypatch)
    rid = _run_id(env)
    if case in ('review', 'done', 'pending'):
        _sql(env['db'], 'UPDATE graph_node_runs SET status = ? WHERE node_key = ?', (case, key))
    elif case == 'result':
        _sql(env['db'], 'UPDATE graph_node_runs SET result_doc_id = ? WHERE node_key = ?', (RESULT, key))
    elif case in ('running', 'paused', 'stopping'):
        _sql(env['db'], 'UPDATE graph_runs SET status = ?', (case,))
    elif case == 'stopped':
        _sql(env['db'], "UPDATE graph_runs SET idle_reason = 'stopped'")
    elif case == 'tm':
        monkeypatch.setattr(operation, '_unfinished', lambda flow: [{'operation_id': 'tm_in_progress'}])
    elif case in ('requested', 'execution_running'):
        _sql(env['db'], "UPDATE graph_runs SET status = 'running'")
        from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
        from flowgate.graph_run import execution
        from modules.flow_gate.graph_workflow.services import run_apply, node_dispatch
        def dispatch():
            ctx = run_apply.load_context(run_store.run_by_id(rid), 'flowgate', 'u_kim')
            node = ctx.node_rows[key]
            job = node_dispatch._ai_job(ctx, node, {}, OPUS, 'u_kim', token, 'result')
            execution.request_write(job)
        with_flow_guard(FA, dispatch)
        if case == 'execution_running':
            eid = _sql(env['db'], 'SELECT exec_id FROM graph_attempt_runs')[0]['exec_id']
            ledger.acquire(eid, 'air_running')
        _sql(env['db'], "UPDATE graph_runs SET status = 'idle'")
    elif case in ('live_job', 'provider_active'):
        _sql(env['db'], 'UPDATE tokens SET ai_run_id = ? WHERE token_id = ?', ('air_live', token['token_id']))
        live = {'status': 'running'} if case == 'live_job' else {'status': 'finished', '_provider_active': True}
        monkeypatch.setattr(ai_invoke_service, 'get_run_record', lambda _: live)
    elif case == 'legacy_job':
        monkeypatch.setattr(legacy_adapter, 'node_ai_run_active', lambda *args: True)
    elif case == 'no_attempt':
        _sql(env['db'], "UPDATE graph_node_attempts SET status='invalidated', active_node_key=NULL WHERE node_key=?", (key,))
    elif case == 'root_bound':
        _sql(env['db'], 'UPDATE graph_node_runs SET root_bound = 1 WHERE node_key = ?', (key,))
    before = _snapshot(env)
    response = _recover(env, key)
    assert response.status_code == 409, response.text
    assert _snapshot(env) == before


def test_stop_still_refuses_idle_and_leaves_cards_intact(env, decided, monkeypatch):
    key, _ = _open_person(env, monkeypatch)
    before = _snapshot(env)
    response = env['client'].post(f'/api/v1/runs/{_run_id(env)}/stop', json={}, headers=_h())
    assert response.status_code == 409 and 'run_not_running' in response.text
    assert _snapshot(env) == before


def test_recovery_uses_stop_resume_authentication_and_write_permission(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.api import envelope
    key, _ = _open_person(env, monkeypatch)
    before = _snapshot(env)
    assert _recover(env, key, {}).status_code == 401
    calls = []
    monkeypatch.setattr(envelope, 'has_permission', lambda *args: calls.append(args) or False)
    assert _recover(env, key).status_code == 403
    assert calls and _snapshot(env) == before


@pytest.mark.parametrize('verification_in_flight', [False, True], ids=['revoked_token', 'attempt_fence'])
def test_old_token_cannot_submit_to_inbox_after_recovery_and_resume(env, decided, monkeypatch, verification_in_flight):
    from modules.flow_gate.services import ai_invoke_service
    key, token = _open_person(env, monkeypatch)
    _admin_and_reviewer(env)
    _grant_document_create()
    if verification_in_flight:
        # A request already authenticated its token before recovery committed:
        # leave authentication valid to exercise the independent attempt fence.
        monkeypatch.setattr(ai_invoke_service, '_reclaim_orphan_lease_token', lambda *args: None)
    assert _recover(env, key).status_code == 200
    assert env['client'].post(f'/api/v1/runs/{_run_id(env)}/resume', json={'mode': 'full'}, headers=_h()).status_code == 200
    documents = _wp_documents(env)
    response = _inbox(token['raw_token'], _wp_json())
    assert response.status_code == (409 if verification_in_flight else 401), response.text
    if verification_in_flight:
        assert 'graph_attempt_inactive' in response.text
    assert _wp_documents(env) == documents and _node(env, key) == ('open', None)


def test_revised_0061_t2_idle_reason_human_wait_and_dispatch_failure_are_distinct(env, decided, monkeypatch):
    """0061 T#2 개정 (idle reason only): a human writer is waiting_approval, never no_targets."""
    key, _ = _open_person(env, monkeypatch)
    assert _current(env)['idle_reason'] == 'waiting_approval'
    _sql(env['db'], "UPDATE graph_node_runs SET dispatch_error_code='token_issue_failed' WHERE node_key=?", (key,))
    assert _current(env)['idle_reason'] == 'error'


def test_recovery_rolls_back_if_reset_fails(env, decided, monkeypatch):
    from flowgate.graph_run import stop, card_recovery
    from modules.flow_gate.db import groups
    key, _ = _open_person(env, monkeypatch)
    before = _snapshot(env)
    def fail(*args):
        raise RuntimeError('reset failed')
    monkeypatch.setattr(stop, 'reset_node', fail)
    with pytest.raises(RuntimeError, match='reset failed'):
        card_recovery.recover_card(groups.get_by_id(FA), _run_id(env), key, None, 'u_kim')
    assert _snapshot(env) == before


def test_recovery_refuses_registered_result_even_if_contact_was_lost(env, decided, monkeypatch):
    from test_fg_wp_ai_run_0083 import _wp_result
    from modules.flow_gate.services import token_service
    key, token = _open_person(env, monkeypatch)
    result = _wp_result(81)
    token_service.consume(token['token_id'], 'flowgate', result)
    assert _node(env, key) == ('open', None)
    before = _snapshot(env)
    response = _recover(env, key)
    assert response.status_code == 409 and 'result_exists' in response.text
    assert _snapshot(env) == before


def test_recovery_preserves_another_cards_review_result(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.store import run_store
    _opus(env)
    first, second = _cards(env, [('WP', LEE), ('WP', LEE)])
    _start(env)
    # An unrelated card already in review must not be reset as an idle Stop would do.
    _sql(env['db'], "UPDATE graph_node_runs SET status='review', result_doc_id=? WHERE node_key=?",
         (RESULT, second))
    before = next(n for n in run_store.node_runs(_run_id(env)) if n['node_key'] == second)
    response = _recover(env, first)
    assert response.status_code == 200, response.text
    after = next(n for n in run_store.node_runs(_run_id(env)) if n['node_key'] == second)
    assert after == before
    assert _node(env, first) == ('pending', None)


def test_stop_of_a_running_run_still_invalidates_and_resets_through_the_shared_parts(env, decided):
    """Stop (running) after the 0083 WP-2 extraction of ``stop.invalidate_attempt`` / ``stop.reset_node``: the same
    user_stop invalidation, token reclaim, node reset and events as before; the card is pending for the next command."""
    _opus(env)
    [key] = _cards(env, [('WP', None)], {0: (('run_ai', OPUS),)})
    _start(env)
    assert _run_status(env)[0] == 'running' and len(env['jobs']) == 1
    [old] = _sql(env['db'], 'SELECT attempt_id FROM graph_node_attempts WHERE node_key = ?', (key,))
    response = env['client'].post(f'/api/v1/runs/{_run_id(env)}/stop', json={}, headers=_h())
    assert response.status_code == 200, response.text
    assert _run_status(env) == ('idle', 'stopped')
    [attempt] = _sql(env['db'], 'SELECT * FROM graph_node_attempts WHERE attempt_id = ?', (old['attempt_id'],))
    assert attempt['status'] == 'invalidated' and attempt['invalidated_reason'] == 'user_stop'
    assert attempt['active_node_key'] is None
    assert _node(env, key) == ('pending', None)
    row = _sql(env['db'], 'SELECT * FROM graph_node_runs WHERE node_key = ?', (key,))[0]
    assert row['dispatch_token_issued'] is None and row['stage'] is None and row['opened_at'] is None
    assert [r['status'] for r in _sql(env['db'], 'SELECT status FROM graph_attempt_runs')] == ['cancelled']
    events = _sql(env['db'], 'SELECT cause, node_key FROM graph_run_events WHERE run_id = ?', (_run_id(env),))
    assert any(e['cause'] == 'user_stop' and e['node_key'] == key for e in events)
