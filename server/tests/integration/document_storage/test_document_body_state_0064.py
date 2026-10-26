"""0064 T#2 body read states, guarded saves and per-line error isolation."""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import _flowgate_test_imports  # noqa: F401
from test_fg_card_documents import FB, _doc, _h, _sql, db_path, env, node, put_draft
from modules.flow_gate.db import connection, documents
from modules.flow_gate.storage import body as body_storage, paths
from modules.flow_gate.documents.routers import documents as console
from modules.flow_gate.api.v1 import document_routes as token
from flowgate.card_documents import legacy_adapter as legacy


@pytest.fixture(autouse=True)
def body_routes(env, monkeypatch):
    app = env['client'].app
    app.include_router(console.router, prefix='/api/v1')
    app.include_router(token.router, prefix='/flowgate')
    app.dependency_overrides[console.get_current_user] = lambda: {'user_id': 'u_kim'}
    monkeypatch.setattr(token, 'verify_bearer', lambda request: {'_is_user_jwt': True})
    monkeypatch.setattr(console, '_broadcast_document_refresh', lambda doc: None)
    env['client'] = TestClient(app, raise_server_exceptions=False)


def saved_events(doc_id):
    return connection.get_store()._fetch_all(
        "SELECT * FROM events WHERE doc_id=? AND event_type='doc_body_saved'", [doc_id])


def make_doc(env, kind, seq=1):
    target = env['storage'] / f'body-{seq}.md'
    if kind == 'file_path_missing':
        stored = '  '
    elif kind == 'path_unresolved':
        stored = str(env['storage'].parent / f'outside-{seq}.md')
    else:
        stored = str(target)
        if kind in ('readable', 'empty'):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('---\ntitle: body\n---\n' + ('# Body\ntext\n' if kind == 'readable' else ''), encoding='utf-8')
    return _doc(FB, seq, 'R', status='draft', file_path=stored), target


@pytest.mark.parametrize('kind', ['file_path_missing', 'file_not_found', 'path_unresolved', 'empty', 'readable'])
def test_read_contracts_and_empty_document(env, kind):
    """V-B1c-1/2, V-P-1/2: the old envelopes and messages survive."""
    doc_id, target = make_doc(env, kind)
    expected = 'readable' if kind == 'empty' else kind
    state, text = body_storage.body_read_state(documents.get_by_id(doc_id))
    assert state == expected
    client = env['client']
    base = f'/flowgate/api/v1/document/{doc_id}'
    response = client.get(base)
    assert response.status_code == 200, response.text
    assert response.json()['content_state'] == expected
    assert response.json()['content'] == text
    meta = client.get(base + '/meta').json()
    assert meta['body']['state'] == expected
    assert meta['body']['present'] == (expected == 'readable')
    for suffix in ('/outline', '/section?lines=1-1'):
        response = client.get(base + suffix)
        assert response.status_code == (200 if expected == 'readable' else 404), response.text
        assert response.json()['content_state'] == expected
        if expected != 'readable':
            assert response.json()['error_message'] == f'document body is not readable: {doc_id}'
    for url in (f'/api/v1/documents/{doc_id}/content', f'/api/v1/documents/content?doc_id={doc_id}'):
        response = client.get(url)
        assert response.status_code == (200 if expected == 'readable' else 404)
        if expected == 'readable':
            assert response.json() == {'content': text}
        else:
            assert response.json() == {'reason': expected, 'detail': {
                'file_path_missing': 'Document file path is missing.',
                'file_not_found': 'Document file not found.', 'path_unresolved': 'Not found',
            }[expected]}


def test_missing_document_and_auth_keep_existing_errors(env, monkeypatch):
    client = env['client']
    doc_id = FB + '.0999-R'
    response = client.get(f'/flowgate/api/v1/document/{doc_id}')
    assert response.status_code == 404 and 'content_state' not in response.json()
    response = client.get(f'/api/v1/documents/content?doc_id={doc_id}')
    assert response.json() == {'detail': f'Document not found: {doc_id}'}
    monkeypatch.setattr(token, 'verify_bearer', lambda request: JSONResponse(status_code=403, content={'ok': False}))
    assert client.get(f'/flowgate/api/v1/document/{doc_id}').json() == {'ok': False}


@pytest.mark.parametrize('failure', ['permission', 'unicode'])
def test_line_read_error_isolated_and_token_500(env, monkeypatch, failure):
    """V-B1c-9/V-P-4: Canvas continues; ordinary body reads retain 500."""
    draft = put_draft(env, FB, 0, [node('tmp_n', 'N')])
    key = draft['key_map']['tmp_n']
    first, second = draft['card_documents'][key]
    assert first['body_state'] == second['body_state'] == 'readable'
    doc = documents.get_by_id(first['doc_id'])
    target = paths.resolve_storage_path(doc['file_path'], 'flowgate')
    if failure == 'unicode':
        target.write_bytes(b'\xff\xfeinvalid')
    original = Path.read_text
    reads = []
    def read(path, *args, **kwargs):
        if path == target:
            reads.append(path)
            if failure == 'permission':
                raise PermissionError('injected')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    documents.update(second['doc_id'], {'status': 'cancelled'})
    response = env['client'].get(f'/api/v1/flows/{FB}/canvas', headers=_h())
    assert response.status_code == 200, response.text
    lines = next(n['card_documents'] for n in response.json()['nodes'] if n.get('node_key') == key)
    assert lines[0]['body_state'] == 'read_error' and lines[0]['has_body'] is False
    assert lines[1]['body_state'] is None and lines[1]['doc_id'] is None
    assert len(reads) == 1
    for suffix in ('', '/outline', '/section?lines=1-1', '/meta'):
        response = env['client'].get(f"/flowgate/api/v1/document/{doc['doc_id']}" + suffix)
        assert response.status_code == 500
        assert 'content_state' not in response.json()


@pytest.mark.parametrize('card', [False, True])
@pytest.mark.parametrize('rpc', [False, True])
def test_missing_path_save_records_standard_path_exact_bytes_and_event(env, card, rpc):
    """V-B1c-5/V-P-3: save, then read with a worker token."""
    doc_id, _ = make_doc(env, 'file_path_missing')
    if card:
        documents.update(doc_id, {'meta': json.dumps({'graph_card': {'slot': 'requirement', 'created_empty': True}})})
    doc = documents.get_by_id(doc_id)
    target = console._save_target(doc)
    assert target.name == ('0001-R_requirement.md' if card else '0001-R_document.md')
    text = '# 본문\r\n다음\n'
    url = '/api/v1/documents/content' if rpc else f'/api/v1/documents/{doc_id}/content'
    response = env['client'].patch(url, json={'doc_id': doc_id, 'content': text})
    assert response.status_code == 200, response.text
    assert target.read_bytes() == text.encode('utf-8')
    row = documents.get_by_id(doc_id)
    assert row['file_path'] == paths.to_storage_relative(target, 'flowgate')
    assert json.loads(saved_events(doc_id)[0]['note']) == {
        'source': 'console_save', 'file_path': row['file_path'], 'size': len(text.encode('utf-8'))}
    assert env['client'].get(f'/flowgate/api/v1/document/{doc_id}').json()['content_state'] == 'readable'
    events = [ev for ev in env['events'] if ev.payload.get('reason') == 'documents']
    assert len(events) == 1 and events[0].payload['origin_client_id'] is None


def test_missing_path_collision_never_overwrites(env):
    """V-B1c-6."""
    doc_id, _ = make_doc(env, 'file_path_missing')
    before = documents.get_by_id(doc_id)
    target = console._save_target(before)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'existing unrecorded')
    response = env['client'].patch('/api/v1/documents/content', json={'doc_id': doc_id, 'content': 'new'})
    assert response.status_code == 409 and response.json()['detail'] == 'file_exists_unrecorded'
    assert target.read_bytes() == b'existing unrecorded'
    assert documents.get_by_id(doc_id) == before and saved_events(doc_id) == []


@pytest.mark.parametrize('recorded', [False, True])
@pytest.mark.parametrize('failure', ['update', 'commit'])
def test_first_file_removed_on_failed_save_and_retry_succeeds(env, monkeypatch, recorded, failure):
    """V-B1c-7: rollback leaves neither file nor save evidence."""
    doc_id, _ = make_doc(env, 'file_not_found' if recorded else 'file_path_missing')
    before = documents.get_by_id(doc_id)
    target = console._save_target(before)
    with monkeypatch.context() as patcher:
        if failure == 'update':
            def fail(*args, **kwargs):
                raise ValueError('injected update failure')
            patcher.setattr(console.document_service, 'update_document', fail)
        else:
            adapter = connection.get_store()._db
            original = adapter.begin_transaction
            @contextmanager
            def fail_commit():
                with original() as tx:
                    yield tx
                    raise OSError('injected commit failure')
            patcher.setattr(adapter, 'begin_transaction', fail_commit)
        response = env['client'].patch('/api/v1/documents/content', json={'doc_id': doc_id, 'content': 'new'})
        assert response.status_code == 500
    assert not target.exists() and documents.get_by_id(doc_id) == before
    assert saved_events(doc_id) == [] and not env['events']
    response = env['client'].patch('/api/v1/documents/content', json={'doc_id': doc_id, 'content': 'retry'})
    assert response.status_code == 200, response.text
    assert target.read_bytes() == b'retry'


@pytest.mark.parametrize('outside_exists', [False, True])
@pytest.mark.parametrize('standard_exists', [False, True])
@pytest.mark.parametrize('rpc', [False, True])
def test_unresolved_save_refuses_without_any_write(env, outside_exists, standard_exists, rpc):
    """V-B1c-10: outside-jail paths never fall back to the standard location."""
    doc_id, _ = make_doc(env, 'path_unresolved')
    before = documents.get_by_id(doc_id)
    outside = Path(before['file_path'])
    standard = console._regenerate_target_path({**before, 'file_path': None})
    for path, exists in [(outside, outside_exists), (standard, standard_exists)]:
        if exists:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'protected')
    url = '/api/v1/documents/content' if rpc else f'/api/v1/documents/{doc_id}/content'
    response = env['client'].patch(url, json={'doc_id': doc_id, 'content': 'new'})
    assert response.status_code == 404 and response.json() == {'detail': 'Not found', 'reason': 'path_unresolved'}
    assert documents.get_by_id(doc_id) == before and saved_events(doc_id) == []
    for path, exists in [(outside, outside_exists), (standard, standard_exists)]:
        assert path.exists() == exists
        if exists:
            assert path.read_bytes() == b'protected'


@pytest.mark.parametrize('change,expected', [('approval', 422), ('disposed', 409), ('lease', 409), ('ch', 409), ('path', 404)])
def test_guard_rechecks_latest_state_before_writing(env, monkeypatch, change, expected):
    """V-B1c-8/11: mutate after the fast check, just before lock acquisition."""
    from modules.flow_gate.graph_workflow.services import flow_guard
    doc_id, target = make_doc(env, 'readable')
    original = flow_guard.with_flow_guard
    waiting = {'inside': False}
    outside = env['storage'].parent / 'outside.md'
    def guard(flow, callback):
        waiting['inside'] = True
        if change == 'approval':
            documents.update(doc_id, {'doc_review_status': 'wf_done'})
        elif change == 'path':
            documents.update(doc_id, {'file_path': str(outside)})
        return original(flow, callback)
    monkeypatch.setattr(flow_guard, 'with_flow_guard', guard)
    for kind, name in [('disposed', '_reject_if_group_disposed'), ('lease', '_reject_if_group_ai_running')]:
        if change == kind:
            def reject(doc):
                if waiting['inside']:
                    raise HTTPException(status_code=409, detail='existing refusal')
            monkeypatch.setattr(console, name, reject)
    if change == 'ch':
        monkeypatch.setattr(console.conversation_turn_service, 'is_full_body_edit_blocked', lambda *args: waiting['inside'])
        monkeypatch.setattr(console.conversation_turn_service, 'full_body_edit_message', lambda *args: 'migrated')
    before = target.read_bytes()
    response = env['client'].patch('/api/v1/documents/content', json={'doc_id': doc_id, 'content': 'new'})
    assert response.status_code == expected, response.text
    assert target.read_bytes() == before and saved_events(doc_id) == [] and not outside.exists()
    if change == 'path':
        assert response.json() == {'detail': 'Not found', 'reason': 'path_unresolved'}
        assert documents.get_by_id(doc_id)['file_path'] == str(outside)


@pytest.mark.parametrize('alias', ['relative', 'absolute', 'storage_prefix', 'missing_branch', 'duplicate_branch'])
def test_resolver_candidates_keep_success_order(env, alias):
    """V-B1c-3: real path aliases continue returning exactly the existing file."""
    path = paths.document_path('flowgate', FB, '0001-R', 'document.md', module='v02')
    path.parent.mkdir(parents=True)
    path.write_bytes(b'body')
    relative = paths.to_storage_relative(path, 'flowgate')
    raw = {'relative': relative, 'absolute': str(path), 'storage_prefix': '/storage/' + relative,
           'missing_branch': relative.replace('/main/', '/'),
           'duplicate_branch': relative.replace('/main/', '/main/main/')}[alias]
    assert paths.resolve_storage_path(raw, 'flowgate') == path.resolve()
    assert paths.locate_storage_path(raw, 'flowgate') == (path.resolve(), 'readable')


def test_line_write_response_has_body_state(env, monkeypatch):
    from flowgate.card_documents import routes
    env['client'].app.include_router(routes.router)
    monkeypatch.setattr(legacy, 'has_permission', lambda *args: True)
    draft = put_draft(env, FB, 0, [node('tmp_r', 'R')])
    key = draft['key_map']['tmp_r']
    documents.update(draft['card_documents'][key][0]['doc_id'], {'status': 'cancelled'})
    response = env['client'].post(f'/api/v1/flows/{FB}/nodes/{key}/slots/requirement/document', headers=_h(), json={'content': '# Written'})
    assert response.status_code == 201, response.text
    assert response.json()['line']['body_state'] == 'readable'
    assert response.json()['line']['has_body'] is True


def test_documents_canvas_event_only_after_commit(env):
    from modules.flow_gate.graph_workflow.services.canvas_events import publish_canvas_changed
    kwargs = dict(project_id='flowgate', flow_id=FB, reason='documents', actor_user_id='u_kim', origin_client_id=None)
    with pytest.raises(ValueError):
        with connection.get_store().transaction():
            publish_canvas_changed(**kwargs)
            assert env['events'] == []
            raise ValueError('rollback')
    assert env['events'] == []
    with connection.get_store().transaction():
        publish_canvas_changed(**kwargs)
        assert env['events'] == []
    assert len(env['events']) == 1
    assert env['events'][0].payload == {**{key: value for key, value in kwargs.items() if key != 'project_id'},
                                      'draft_version': None, 'revision_no': None, 'node_keys': []}

@pytest.mark.parametrize('alias', ['relative', 'absolute', 'storage_prefix'])
def test_missing_recorded_file_can_be_saved_at_the_same_normalized_location(env, alias):
    target = paths.document_path('flowgate', FB, '0001-R', 'document.md', module='v02')
    relative = paths.to_storage_relative(target, 'flowgate')
    raw = {'relative': relative, 'absolute': str(target), 'storage_prefix': '/storage/' + relative}[alias]
    doc_id = _doc(FB, 1, 'R', file_path=raw)
    response = env['client'].patch('/api/v1/documents/content', json={'doc_id': doc_id, 'content': 'restored'})
    assert response.status_code == 200, response.text
    assert target.read_bytes() == b'restored'
    assert json.loads(saved_events(doc_id)[0]['note'])['size'] == 8


@pytest.mark.parametrize('failure', [False, True])
def test_work_plan_save_evidence_shares_the_save_transaction(env, monkeypatch, failure):
    from modules.flow_gate.documents.routers import work_plan as route
    from modules.flow_gate.services import work_plan_service as wp
    env['client'].app.include_router(route.router, prefix='/api/v1')
    draft = put_draft(env, FB, 0, [node('tmp_wp', 'WP')])
    doc_id = draft['card_documents'][draft['key_map']['tmp_wp']][0]['doc_id']
    doc = documents.get_by_id(doc_id)
    path = paths.resolve_storage_path(doc['file_path'], 'flowgate')
    previous = path.read_bytes()
    plan = json.loads(previous)
    original = route.record_body_saved
    seen = []
    def record(*args, **kwargs):
        assert connection.in_transaction()
        seen.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(route, 'record_body_saved', record)
    if failure:
        def fail(*args):
            raise OSError('injected before write')
        monkeypatch.setattr(wp, 'write_body_atomically', fail)
    response = env['client'].put(f'/api/v1/documents/{doc_id}/work-plan',
                                json={'base_revision_no': 0, 'body': plan})
    assert response.status_code == (500 if failure else 200), response.text
    if failure:
        assert saved_events(doc_id) == [] and seen == []
        assert documents.get_by_id(doc_id)['revision_no'] == 0
        assert path.read_bytes() == previous
    else:
        note = json.loads(saved_events(doc_id)[0]['note'])
        assert note == {'source': 'work_plan_save', 'file_path': doc['file_path'], 'size': len(path.read_bytes())}
        assert seen == [note]


@pytest.mark.parametrize('failure', [False, True])
def test_tr2_save_evidence_and_rollback_in_real_transaction(env, monkeypatch, failure):
    from modules.flow_gate.documents import tr2_service as tr2
    from test_t2_tr2_domain_0565 import _body
    doc_id = _doc(FB, 1, 'TR2', review='pending_review')
    path = env['storage'] / 'tr2.json'
    root = env['storage'] / 'source-fixture'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'a.txt').write_bytes(b'a')
    monkeypatch.setattr(tr2, 'verify_pair', lambda *args: None)
    monkeypatch.setattr(tr2, 'mutation_block', lambda doc: None)
    monkeypatch.setattr(tr2, 'resolve_source_root', lambda *args: root)
    monkeypatch.setattr(tr2, 'canonical_path_for_doc', lambda doc: path)
    original = body_storage.record_body_saved
    def record(*args, **kwargs):
        assert connection.in_transaction()
        original(*args, **kwargs)
        if failure:
            raise RuntimeError('injected after evidence insert')
    monkeypatch.setattr(body_storage, 'record_body_saved', record)
    if failure:
        with pytest.raises(RuntimeError, match='injected'):
            tr2.save(doc_id, _body(), actor='u_kim', expected_revision=0)
        assert saved_events(doc_id) == [] and not path.exists()
        assert documents.get_by_id(doc_id)['revision_no'] == 0
    else:
        tr2.save(doc_id, _body(), actor='u_kim', expected_revision=0)
        assert json.loads(saved_events(doc_id)[0]['note']) == {
            'source': 'tr2_save', 'file_path': paths.to_storage_relative(path, 'flowgate'),
            'size': len(path.read_bytes())}
