"""0126 T#4 A1/A2/A5: settings-only capability, actual HTTP/SQLite and durable history.

AI dispatch/JWT/SSE transport use the existing isolated recorders. No deployed flow is edited.
Display reasons, verdicts and document statuses are deliberately not node statuses.
"""
import json
import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401
    FA, X, B, _sql, command, db_path, decided, env, flow, ledger, line_doc, node_run, roles, start,
    starts, wired, assignments,
)
from test_fg_card_settings import _h, get_settings, patch, node, edge, put_draft


def url(key):
    return f"/api/v1/flows/{FA}/nodes/{key}/settings"


def snapshot(env):
    tables = ['documents', 'document_revisions', 'graph_revisions', 'graph_revision_nodes', 'graph_revision_edges',
              'graph_node_attempts', 'graph_stage_assignments', 'document_reviews', 'graph_run_events', 'graph_attempt_runs',
              'graph_card_completions', 'graph_card_completion_decisions', 'graph_propagations', 'graph_document_decisions']
    present = {r['name'] for r in _sql(env['db'], "SELECT name FROM sqlite_master WHERE type='table'")}
    return {t: _sql(env['db'], f'SELECT * FROM {t}') for t in tables if t in present}


def save(env, key, field, value, before=None, expect=200):
    before = before or get_settings(env, FA, key)
    return patch(env, FA, key, {'field': field, 'value': value,
                              'expected_settings_version': before['settings_version'],
                              'expected_context': before['context']}, expect=expect)


@pytest.mark.parametrize('label,status,doc_status', [
    ('needs_intervention', 'review', 'pending_review'),
    ('verdict_hold', 'review', 'pending_review'),
    ('review_count_exhausted', 'review', 'pending_review'),
    ('revised', 'review', 'revised'), ('rejected', 'review', 'rejected'),
    ('failed', 'open', None), ('system_error', 'open', None),
    ('human_wait', 'open', None), ('not_started', 'pending', None),
    ('done_reentry_preparation', 'done', 'approved'),
])
def test_a1_inactive_current_execution_revision_saves_roles_counts_and_allowed_settings(
        env, decided, starts, label, status, doc_status, tmp_path):
    key = flow(env)
    roles(key, 'not_used', 'not_used', 'not_used')
    run = start(env)
    # These are independent presentation/document facts, not status enum extensions.
    _sql(env['db'], 'UPDATE graph_node_runs SET status = ? WHERE node_key = ?', (status, key))
    report = line_doc(env, key, 'report')
    _sql(env['db'], 'UPDATE documents SET doc_review_status = ? WHERE doc_id = ?', (doc_status, report))
    from flowgate.card_settings import legacy_adapter
    if label in ('needs_intervention', 'verdict_hold', 'review_count_exhausted'):
        reason = {'needs_intervention': 'completion_missing', 'verdict_hold': 'review_not_passed:hold',
                  'review_count_exhausted': 'review_count_exhausted'}[label]
        legacy_adapter.record_approval_wait(FA, key, report, reason)
    if label in ('failed', 'system_error'):
        _sql(env['db'], 'UPDATE graph_node_runs SET dispatch_error_code = ? WHERE node_key = ?', (label, key))
    before = get_settings(env, FA, key)
    assert before['context'] == {'run_id': run['run_id'], 'graph_revision_no': 1, 'draft_version': None}
    assert before['locked'] is before['node_running'] is False
    assert before['settings_capability'] == {'editable': True, 'reason': None}
    history = snapshot(env)
    version = before['settings_version']
    for field, value in [('instruction_role', {'kind':'assigned', 'provider_id':X}),
                         ('execution_role', {'kind':'assigned', 'provider_id':B}),
                         ('review_role', {'kind':'assigned', 'provider_id':X}),
                         ('review_count', 3), ('instruction_review_count', 2),
                         ('restart', 1), ('time_limit', 120), ('review_basis', 'last')]:
        after = save(env, key, field, value)
        version += 1
        assert after['settings_version'] == version and after['locked'] is False
    # A new GET represents a new resource/session; it never dispatches or rewrites history.
    reloaded = env['client'].get(url(key), params={'context': json.dumps(before['context'])}, headers=_h()).json()
    assert reloaded['settings_version'] == version
    assert reloaded['fields']['review_count']['value'] == 3
    assert reloaded['fields']['instruction_review_count']['value'] == 2
    assert reloaded['ai_roles']['roles']['execution']['selected']['provider_id'] == B
    assert snapshot(env) == history and ledger(env) == [] and starts == []
    assert node_run(env, key)['status'] == status
    events = [e for e in env['events'] if e.payload.get('reason') == 'settings']
    assert events and events[-1].payload['node_keys'] == [key]
    (tmp_path / 'settings-evidence.json').write_text(json.dumps({
        'case': label, 'before': before, 'after': reloaded, 'history_unchanged': True}, ensure_ascii=False, indent=2), encoding='utf-8')


@pytest.mark.parametrize('status', ['requested', 'running'])
@pytest.mark.parametrize('display_status', ['open', 'review', 'done'])
def test_a2_real_execution_locks_exact_card_even_if_display_status_is_stale(env, decided, starts, status, display_status):
    key = flow(env)
    roles(key, X, 'not_used', 'not_used')
    start(env)
    [execution] = ledger(env)
    _sql(env['db'], 'UPDATE graph_attempt_runs SET status = ? WHERE exec_id = ?', (status, execution['exec_id']))
    _sql(env['db'], 'UPDATE graph_node_runs SET status = ? WHERE node_key = ?', (display_status, key))
    before = get_settings(env, FA, key)
    assert before['locked'] is before['node_running'] is True
    assert before['settings_capability']['editable'] is False
    history = snapshot(env)
    for field,value in [('review_count', 2), ('execution_role', {'kind':'not_used'}), ('instruction', 'omit')]:
        refused = save(env, key, field, value, before, expect=409)
        assert refused['error']['code'] == 'node_settings_locked'
    assert get_settings(env, FA, key)['settings_version'] == before['settings_version']
    assert snapshot(env) == history


@pytest.mark.parametrize('source', ['handoff', 'instruction_review', 'result_review', 'lease'])
def test_a2_valid_handoff_loop_and_card_mapped_lease_lock_only_while_live(env, decided, starts, monkeypatch, source):
    from flowgate.card_settings import legacy_adapter as legacy, review_stage
    key = flow(env)
    roles(key, 'not_used', 'not_used', 'not_used')
    start(env)
    if source == 'handoff':
        import time
        review_stage._handoffs[(FA,key)] = ('t4-handoff', time.monotonic() + 60)
    elif source == 'lease':
        from datetime import datetime, timedelta, timezone
        row = _sql(env['db'], 'SELECT t.ai_run_id FROM graph_run_token_map m JOIN tokens t ON t.token_id=m.token_id WHERE m.node_key=?', (key,))
        _sql(env['db'], 'UPDATE tokens SET ai_run_id=? WHERE token_id IN (SELECT token_id FROM graph_run_token_map WHERE node_key=?)', ('t4-lease',key))
        monkeypatch.setattr(legacy.db_leases, 'get', lambda f: {'run_id':'t4-lease', 'expires_at':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()})
    else:
        doc = line_doc(env, key, 'instruction' if source == 'instruction_review' else 'report')
        monkeypatch.setattr(legacy, 'review_loop_active', lambda d: d == doc)
    before = get_settings(env, FA, key)
    assert before['node_running'] is before['locked'] is True
    assert save(env, key, 'review_count', 2, before, expect=409)['error']['code'] == 'node_settings_locked'
    if source == 'handoff':
        import time
        review_stage._handoffs[(FA,key)] = ('t4-handoff', time.monotonic() - 1)
    elif source == 'lease':
        monkeypatch.setattr(legacy.db_leases, 'get', lambda f: {'run_id':'t4-lease', 'expires_at':'2000-01-01T00:00:00+00:00'})
    else:
        monkeypatch.setattr(legacy, 'review_loop_active', lambda d: False)
    assert get_settings(env, FA, key)['locked'] is False
    assert save(env, key, 'review_count', 2)['fields']['review_count']['value'] == 2
