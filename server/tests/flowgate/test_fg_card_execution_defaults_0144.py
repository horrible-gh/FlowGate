"""0144 T0013: card defaults agree with dispatch without writing over stored choices."""
from copy import deepcopy

import pytest

from flowgate.card_settings import fields as f


@pytest.mark.parametrize("node_type,doc_type", [
    *(('document', doc) for doc in ('N', 'T', 'T2', 'D', 'P', 'L', 'DB', 'WP')),
    ('custom', None),
])
def test_unstored_execution_defaults_match_display_and_dispatch(node_type, doc_type):
    kind = f.card_kind(node_type, doc_type)
    stored = {}
    view = f.view(kind, stored, (-1, 0, 1, 2, 3))
    plan = f.run_plan(kind, stored, node_type, doc_type)
    assert view['restart']['value'] == view['restart']['default'] == plan['restart_max'] == 3
    assert view['time_limit']['value'] == view['time_limit']['default'] == 240
    assert plan['step_timeout_sec'] == view['time_limit']['value'] * 60 == 14400
    assert not view['restart']['stored'] and not view['time_limit']['stored']
    assert stored == plan['settings'] == {}


@pytest.mark.parametrize('kind', [f.RUN, f.PLAN])
@pytest.mark.parametrize('restart,minutes', [(-1, 30), (0, 60), (1, 90), (2, 120), (3, 240)])
def test_explicit_values_including_old_defaults_and_unlimited_are_preserved(kind, restart, minutes):
    stored = {'restart': restart, 'time_limit': minutes}
    original = deepcopy(stored)
    view = f.view(kind, stored, (-1, 0, 1, 2, 3))
    plan = f.run_plan(kind, stored)
    assert view['restart']['value'] == plan['restart_max'] == restart
    assert view['time_limit']['value'] * 60 == plan['step_timeout_sec'] == minutes * 60
    assert view['restart']['stored'] and view['time_limit']['stored']
    assert stored == original == plan['settings']


@pytest.mark.parametrize('stored,expected', [
    ({'restart': 0}, (0, 14400)),
    ({'time_limit': 60}, (3, 3600)),
    ({'restart': -1}, (-1, 14400)),
])
def test_each_missing_field_uses_its_own_default(stored, expected):
    plan = f.run_plan(f.RUN, stored)
    assert (plan['restart_max'], plan['step_timeout_sec']) == expected


def test_settings_port_reads_current_choices_without_persisting_defaults(monkeypatch):
    from flowgate.card_settings import settings

    stored = {}
    monkeypatch.setattr(settings.repository, 'available', lambda: True)
    monkeypatch.setattr(settings.repository, 'values', lambda *_: dict(stored))
    def unexpected_write(*_args, **_kwargs):
        pytest.fail('Reading execution defaults must not write settings')
    monkeypatch.setattr(settings.repository, 'put', unexpected_write)
    node = dict(node_key='card', node_type='document', doc_type='T')
    assert settings.run_settings('flow', node)['restart_max'] == 3
    stored.update(restart=0, time_limit=60)
    plan = settings.run_settings('flow', node)
    assert (plan['restart_max'], plan['step_timeout_sec']) == (0, 3600)
    stored.clear()  # PATCH null removes the row, restoring shared defaults.
    plan = settings.run_settings('flow', node)
    assert (plan['restart_max'], plan['step_timeout_sec']) == (3, 14400)


def test_engine_receives_card_defaults_as_explicit_overrides(monkeypatch):
    from modules.flow_gate.services.ai_invoke import admission

    monkeypatch.setattr(admission.ai_execution_policy_service, 'repeat_count_choices',
                        lambda **_: (-1, 0, 1, 2, 3))
    plan = f.run_plan(f.RUN, {})
    assert admission._resolve_timeout_sec('single', 1, False, plan['step_timeout_sec']) == 14400
    # The engine returns total attempts: one initial attempt plus three restarts.
    assert admission._resolve_restart_max_attempts(plan['restart_max']) == 4


def test_review_rework_defaults_stay_within_existing_loop_constraints():
    plan = f.review_loop({'review_count': 1}, reviewer='r', reworker='w')
    assert (plan['rework_timeout_sec'], plan['failure_restart_max_attempts']) == (7200, 2)
    explicit = f.review_loop({'review_count': 1, 'restart': 0, 'time_limit': 60}, reviewer='r', reworker='w')
    assert (explicit['rework_timeout_sec'], explicit['failure_restart_max_attempts']) == (3600, 0)


@pytest.mark.parametrize('kind', [f.TEST, f.REQUIREMENT, f.CHECK, None])
def test_cards_without_execution_fields_do_not_gain_execution_overrides(kind):
    plan = f.run_plan(kind, {})
    assert 'restart_max' not in plan and 'step_timeout_sec' not in plan
