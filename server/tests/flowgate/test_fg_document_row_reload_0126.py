"""0126 T#6 B10: HTTP reload after a committed decision whose SSE delivery was lost.

Uses real migrated SQLite/doc storage/Flow guard and HTTP routes, stub worker/JWT/SSE
from the existing decision fixture. This does not claim browser F5/UI acceptance.
"""
import pytest
from fastapi.testclient import TestClient
from test_fg_document_decisions_0126 import (
    FA, _h, _sql, db_path, decided, env, legacy_cards, decisions_env,
    _card, _get, _request, _post, _rows, _url,
)

pytestmark = pytest.mark.usefixtures('legacy_cards')


@pytest.mark.parametrize('action,slot', [('approve', 'report'), ('reject', 'instruction')])
def test_b10_fresh_http_read_recovers_both_rows_after_missing_commit_events(decisions_env, monkeypatch, action, slot):
    env = decisions_env
    key = _card(env)
    if action == 'reject':
        _post(env, key, _request(env, key, request_key='initial'))
    from modules.flow_gate.graph_workflow.services import canvas_events
    from modules.flow_gate.db.connection import in_transaction
    dropped = []

    def drop(event):
        assert not in_transaction()
        dropped.append(event)

    monkeypatch.setattr(canvas_events, 'broadcast_event_threadsafe', drop)
    body = _request(env, key, slot, action, 'lost-events')
    answer = _post(env, key, body, slot)
    assert answer['changed_slots'] == ['instruction', 'report']
    assert len([e for e in dropped if e.event_type == 'doc_review_status_changed']) == 2
    assert any(e.event_type == 'graph_canvas_changed' for e in dropped)
    before = _rows(env)
    receipts = _rows(env, 'graph_document_decision_requests')
    # New client has no row state or event cache; GET rebuilds both rows from committed storage.
    with TestClient(env['client'].app) as fresh:
        response = fresh.get(_url(key) + '/decisions', headers=_h())
        assert response.status_code == 200
        read = response.json()
    expected = 'approved' if action == 'approve' else 'rejected'
    assert {doc['doc_review_status'] for doc in read['documents'].values() if doc} == {expected}
    assert read['decision_version'] == answer['decision_version']
    assert read['context'] == _get(env, key)['context']
    assert read['context']['run_id'] is None
    assert all(doc['human_decision']['actor_id'] == 'u_kim' and doc['human_decision']['valid']
               for doc in read['documents'].values() if doc)
    for _ in range(3):
        assert _get(env, key)['decision_version'] == read['decision_version']
    replay = _post(env, key, body, slot)
    assert replay['replayed'] and replay['current']['decision_version'] == read['decision_version']
    assert _rows(env) == before
    assert _rows(env, 'graph_document_decision_requests') == receipts
    assert _rows(env, 'graph_runs') == []
    assert _rows(env, 'document_reviews') == []
