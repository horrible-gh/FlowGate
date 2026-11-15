"""flowgate.v02gui.0023: a person can approve or reject a card document an ended attempt left behind.

A Stop (or a lost lease) leaves the previous attempt's report as ``stale`` history. The card still shows it as
``pending_review``; a person must be able to decide it. Approval completes the card through the normal human
completion; rejection takes the normal rework path. Real migrated SQLite/HTTP/Graph (0126 fixtures); only the
stale classification is pinned, because producing it needs a worker token of an invalidated attempt.
"""
import pytest

from test_fg_human_completion_0126 import (  # noqa: F401
    FA, _h, _sql, db_path, decided, edge, env, legacy_cards, node, put_draft,
    decisions_env, _body, _get, _request, _post, _rows, completions, _command, _current,
    graph, approve, confirm, state, proof,
)

pytestmark = pytest.mark.usefixtures('legacy_cards', 'completions')


def _open_with_stale_report(e, monkeypatch):
    from flowgate.card_completion import legacy_adapter
    k = graph(e, names=('a',)); key = k['a']
    report = _get(e, key)['documents']['report']['doc_id']; _body(report, '')
    confirm(e); _command(e)
    assert state(e, key)['status'] == 'open'
    _body(report)
    monkeypatch.setattr(legacy_adapter, 'stale_documents', lambda flow_id, node_key: {report} if node_key == key else set())
    return key, report


def test_stale_report_is_decidable_by_a_person(decisions_env, decided, monkeypatch):
    e = decisions_env; key, report = _open_with_stale_report(e, monkeypatch)
    caps = _get(e, key)['capabilities']['report']
    assert caps['approve'] == {'allowed': True, 'reason': None}
    assert caps['reject'] == {'allowed': True, 'reason': None}


def test_approving_a_stale_report_completes_the_card(decisions_env, decided, monkeypatch):
    from flowgate.card_completion import human
    from flowgate.card_documents import decision_context
    e = decisions_env; key, report = _open_with_stale_report(e, monkeypatch)
    n, _r, _c, _d = decision_context.current(FA, key)
    assert human.evidence(FA, n) is None              # unapproved stale output is no completion evidence
    approve(e, key); _current(e)
    assert _get(e, key)['documents']['report']['doc_review_status'] == 'approved'
    assert state(e, key)['status'] == 'done'
    assert proof(e, key)['result_doc_id'] == report


def test_rejecting_a_stale_report_reopens_rework(decisions_env, decided, monkeypatch):
    e = decisions_env; key, report = _open_with_stale_report(e, monkeypatch)
    _post(e, key, _request(e, key, action='reject', request_key='stale-reject')); _current(e)
    assert _get(e, key)['documents']['report']['doc_review_status'] == 'rejected'
    assert state(e, key)['status'] != 'done'
    assert not [r for r in _rows(e, 'graph_card_completions') if r['node_key'] == key]
