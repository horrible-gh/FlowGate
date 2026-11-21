"""Real settings routes, SQLite assignments/ledger, AI admission recorder.
No external model, deployed server, or existing user data is used.
"""
import json
import pytest

from test_fg_ai_roles_l2_assignment import (  # noqa: F401
    A, Y, X, B, FA, _sql, assignments, command, db_path, decided, env, events,
    flow, ledger, node_run, one, roles, starts, stored, wired,
)
from test_fg_command_review_0125 import (  # noqa: F401
    _spent, _pass, _loops, _reviews, _doc, _read, _run, completions, fork_join,
)
from test_fg_command_rerequest_0124 import _reason
from test_fg_settings_capability_0126 import save
from test_fg_card_settings import _h, get_settings

pytestmark = pytest.mark.usefixtures('fork_join', 'completions')


from settings_retry_support_0126 import (history, new_settings, _submitted_ai_instruction, _instruction_reviews, _event_seqs)

@pytest.mark.parametrize('mode', ['step'])
@pytest.mark.parametrize('evidence', ['asked'])
def test_pre_t5_assignment_without_version_keeps_q544_revision_guard(
        env, decided, monkeypatch, starts, mode, evidence):
    from flowgate.card_settings import stage_assignment as sa
    from test_fg_command_review_0125 import _loop_row, _loop_end, _review_row, _revised

    key, _, result, revision = _spent(env, monkeypatch, starts, mode, follow=False)
    # This existing card saved settings before versioning: its version is already positive.
    _, settings = new_settings(env, key)
    if evidence == 'asked':
        command(env, mode)
        current = one(env, key, 'result_review', 2)
        _loop_row(result, 'air_pre_t5_asked', 'rework')
        _loop_end(env, current['intent_exec_id'], result, 'air_pre_t5_asked', 'review_count_exhausted')
    else:
        current = one(env, key, 'result_review', 1)
        _review_row(result, revision, 'issues', 'air_pre_t5_reviewed')
    # Only the isolated fixture's event loses the newly introduced field; assignment/history stay intact.
    rows = _sql(env['db'], "SELECT event_seq, payload_json FROM graph_run_events WHERE cause='stage_assigned'")
    matched = 0
    for row in rows:
        payload = json.loads(row['payload_json'])
        if (payload.get('attempt_id'), payload.get('stage'), payload.get('seq')) == (
                current['attempt_id'], current['stage'], current['seq']):
            payload.pop('settings_version')
            _sql(env['db'], 'UPDATE graph_run_events SET payload_json=? WHERE event_seq=? AND run_id=?',
                 (json.dumps(payload), row['event_seq'], current['run_id']))
            matched += 1
    assert matched == 1 and sa.assigned_settings_version(current) is None
    assert settings['settings_version'] > 0
    frozen, calls = history(env), len(_loops(starts))
    for _ in range(2):
        _read(env)
        command(env, mode)
        assert history(env) == frozen and len(_loops(starts)) == calls
    # Unknown version preserves the old guard, but does not block a new unreviewed revision.
    _revised(result, revision + 1)
    _read(env)
    command(env, mode)
    assert len(_reviews(env, key)) == len([r for r in frozen['graph_stage_assignments']
                                        if r['stage'] == 'result_review']) + 1
    assert len(_loops(starts)) == calls + 1
    assert _reviews(env, key)[-1]['basis_revision_no'] == revision + 1
