"""0640 corrective integration: actual API, canonical writer, source jail and order."""
import copy
import os

import pytest

from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.documents import tr2_service as service, tr2_source_import as sources
from test_tr2_batch_import_0640 import proposal, env, store, repo, unchanged_state, entries, EDIT
from test_tr2_source_import_0640 import file, preview, save, source_state, seed
from test_tr2_connected_e2e_0565 import git, approve, view


@pytest.mark.parametrize('mode', ['create', 'case_alias', 'existing_alias'])
def test_all_incoming_path_conflicts_are_reported_and_never_partially_saved(proposal, mode):
    environment, doc_id, client, base = proposal
    if mode == 'create':
        first = {'collection': 'edits', 'item': {'id': 'ca', 'kind': 'create_file', 'file': 'src/new.txt', 'content': 'new', 'confidence': 'high', 'rationale': 'add'}}
        second = copy.deepcopy(first); second['item']['id'] = 'cb'
    else:
        first = {'collection': 'edits', 'item': {**EDIT, 'id': 'ea', 'file': 'src/CASE.txt' if mode == 'case_alias' else 'src/APP.txt'}}
        second = {'collection': 'edits', 'item': {**EDIT, 'id': 'eb', 'file': 'src/case.txt' if mode == 'case_alias' else 'src/app.txt'}}
    incoming = [first, second, entries()[2]]
    before = unchanged_state(doc_id, environment['repo'])
    result = client.post(base+'/items/batch/preview', json={'expected_revision': 1, 'items': incoming})
    assert result.status_code == 200, result.text
    assert [row['valid'] for row in result.json()['items']] == [False, False, True]
    assert all('target' in row['error']['details']['reason'] for row in result.json()['items'][:2])
    result = client.post(base+'/items/batch', json={'expected_revision': 1, 'items': incoming})
    assert result.status_code == 422, result.text
    assert unchanged_state(doc_id, environment['repo']) == before


def test_hard_link_alias_is_review_required_without_reading_or_saving_it(proposal):
    environment, doc_id, client, base = proposal
    root = environment['repo']
    seed(root, 'src/linked.txt', b'old\n')
    os.link(root/'src/linked.txt', root/'src/alias.txt')
    incoming = [file('src/linked.txt', b'new\n'), file('src/alias.txt', b'new\n'), file('src/safe-new.txt')]
    before = unchanged_state(doc_id, root), source_state(root)
    result = preview(client, base, incoming)
    assert [row['status'] for row in result['files']] == ['review_required', 'review_required', 'CREATE']
    assert all(row['reason'] == 'tr2_path_unsafe' for row in result['files'][:2])
    assert save(client, base, incoming, result, [0, 2]).status_code == 422
    assert (unchanged_state(doc_id, root), source_state(root)) == before


def test_mixed_52_sources_preserve_input_order_and_use_existing_approval(proposal):
    environment, doc_id, client, base = proposal
    root = environment['repo']
    for index in range(22):
        (root/f'src/baseline{index}.txt').write_bytes(b'head\nold unique\ntail\n')
    git(root, 'add', '--', 'src')
    git(root, 'commit', '-m', 'mixed comparison baselines')
    incoming = [file(f'src/baseline{i}.txt', b'head\nnew unique\ntail\n') for i in range(12)]
    incoming += [file(f'src/baseline{i}.txt', b'head\nold unique\ntail\n') for i in range(12, 22)]
    incoming += [file(f'src/create{i}.txt') for i in range(30)]
    incoming += [file('../outside'), file('src/binary.txt', b'\x00data'), file('src/missing/new.txt')]
    before = unchanged_state(doc_id, root), source_state(root)
    result = preview(client, base, incoming)
    assert [row['status'] for row in result['files']] == ['EDIT']*12 + ['unchanged']*10 + ['CREATE']*30 + ['review_required']*3
    assert (unchanged_state(doc_id, root), source_state(root)) == before
    chosen = [row['index'] for row in result['files'] if row['valid']]
    saved = save(client, base, incoming, result, list(reversed(chosen)))
    assert saved.status_code == 200, saved.text
    assert saved.json()['new_revision'] == 2 and saved.json()['imported_count'] == 42
    current = view(client, doc_id)
    assert [item['file'] for item in current['body']['edit_spec']['edits'][1:]] == [incoming[i]['file'] for i in chosen]
    assert source_state(root) == before[1]
    assert current['approval']['attempts'] == [] and current['history']['ledger'] == []
    (root/'gate.ok').write_text('ok')
    applied = approve(client, doc_id, 2)
    assert applied.status_code == 200, applied.text
    assert (root/'src/baseline0.txt').read_bytes() == b'head\nnew unique\ntail\n'
    assert (root/'src/create29.txt').read_bytes() == b'new content\n'
    assert (root/'src/baseline21.txt').read_bytes() == b'head\nold unique\ntail\n'
    final = view(client, doc_id)
    assert len(final['approval']['attempts']) == len(final['history']['ledger']) == 1


def test_external_change_during_comparison_cannot_be_saved(proposal, monkeypatch):
    environment, doc_id, client, base = proposal
    root = environment['repo']
    seed(root, 'src/compare.txt', b'old\n')
    original = sources._text
    touched = []
    def text_with_external_change(raw):
        text = original(raw)
        if raw == b'old\n' and not touched:
            touched.append(True)
            (root/'src/compare.txt').write_bytes(b'external\n')
        return text
    monkeypatch.setattr(sources, '_text', text_with_external_change)
    before = unchanged_state(doc_id, root)
    incoming = [file('src/compare.txt', b'new\n')]
    result = preview(client, base, incoming)
    assert touched and not result['files'][0]['valid']
    assert unchanged_state(doc_id, root) == before
    response = save(client, base, incoming, result, [0])
    assert response.status_code == 409 and response.json()['code'] == 'tr2_source_drift'
    assert unchanged_state(doc_id, root) == before
    assert (root/'src/compare.txt').read_bytes() == b'external\n'


def test_decoded_total_limit_rejects_10_mib_plus_one_byte_atomically(proposal):
    environment, doc_id, client, base = proposal
    incoming = [file(f'src/large{i}.txt', b'x'*sources.MAX_BYTES) for i in range(10)] + [file('src/extra.txt', b'x')]
    before = unchanged_state(doc_id, environment['repo'])
    for route, extra in [('/sources/preview', {}), ('/sources', {'selected': [0], 'preview_fingerprint': 'invalid'})]:
        response = client.post(base+route, json={'expected_revision': 1, 'files': incoming, **extra})
        assert response.status_code == 422, response.text
        assert '10 MiB' in response.json()['details']['reason']
        assert unchanged_state(doc_id, environment['repo']) == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction contract')
def test_actual_windows_junction_is_excluded_and_target_is_preserved(proposal):
    import _winapi
    environment, doc_id, client, base = proposal
    root = environment['repo']
    target = root/'src/directory'
    target.mkdir(); (target/'file.txt').write_bytes(b'old\n')
    link = root/'src/junction'
    _winapi.CreateJunction(str(target), str(link))
    try:
        assert link.is_junction()
        before = unchanged_state(doc_id, root)
        result = preview(client, base, [file('src/junction/file.txt', b'new\n')])
        assert result['files'][0]['status'] == 'review_required'
        assert result['files'][0]['reason'] == 'tr2_path_unsafe'
        assert unchanged_state(doc_id, root) == before
        assert (target/'file.txt').read_bytes() == b'old\n'
    finally:
        link.rmdir()
    assert (target/'file.txt').read_bytes() == b'old\n'


def test_revision_recovery_then_rejection_preserves_source_and_approval_history(proposal):
    environment, doc_id, client, base = proposal
    root = environment['repo']
    before = source_state(root)
    response = client.post(base+'/items/batch', json={'expected_revision': 1, 'items': entries()})
    assert response.status_code == 200, response.text
    original = service.load_current(db_docs.get_by_id(doc_id))
    service.canonical_path_for_doc(db_docs.get_by_id(doc_id)).unlink()
    response = client.get(base+'/recovery')
    assert response.status_code == 200 and response.json()['recoverable']
    response = client.post(base+'/recovery/restore', json={'expected_revision': 2, 'revision_no': 2})
    assert response.status_code == 200 and response.json()['new_revision'] == 3, response.text
    restored = view(client, doc_id)
    assert restored['body']['edit_spec'] == original['edit_spec']
    assert restored['approval']['attempts'] == [] and restored['history']['ledger'] == []
    response = client.post('/api/v1/documents/review_transitions/reject', json={'doc_id': doc_id, 'comment': 'fixture rework'})
    assert response.status_code == 200, response.text
    assert db_docs.get_by_id(doc_id)['doc_review_status'] == 'rejected'
    assert source_state(root) == before
    assert service.load_current(db_docs.get_by_id(doc_id))['edit_spec'] == original['edit_spec']


def test_failed_validation_rollback_retry_and_replay_keep_one_commit(proposal):
    environment, doc_id, client, base = proposal
    root = environment['repo']
    before = source_state(root)
    response = approve(client, doc_id, 1, request_key='corrective-failure')
    assert response.status_code == 422 and response.json()['code'] == 'tr2_validation_failed', response.text
    assert source_state(root) == before
    failed = view(client, doc_id)
    assert len(failed['approval']['attempts']) == 1
    assert failed['approval']['retry']['allowed']
    key = failed['approval']['retry']['request_key']
    (root/'gate.ok').write_bytes(b'ok')
    response = approve(client, doc_id, 1, request_key=key)
    assert response.status_code == 200, response.text
    response = approve(client, doc_id, 1, request_key=key)
    assert response.status_code == 200, response.text
    final = view(client, doc_id)
    assert len(final['approval']['attempts']) == 2 and len(final['history']['ledger']) == 1
    assert final['document']['doc_review_status'] == 'approved'


def test_50_json_items_mixed_preview_one_atomic_revision_and_requery(proposal):
    environment, doc_id, client, base = proposal
    root = environment['repo']
    creates = [{'collection': 'edits', 'item': {'id': f'bulk-{i}', 'kind': 'create_file', 'file': f'src/bulk-{i}.txt', 'content': f'new {i}\n', 'confidence': 'high', 'rationale': 'bulk fixture'}} for i in range(36)]
    deferred = [{'collection': 'deferred', 'item': {'id': f'later-{i}', 'file': f'src/later-{i}.txt', 'reason': 'needs_runtime', 'rationale': 'deferred fixture'}} for i in range(10)]
    incoming = creates + deferred + [
        {'collection': 'edits', 'item': {'id': 'bad-shape'}},
        {'collection': 'edits', 'item': {**EDIT, 'id': 'e1'}},
        {'collection': 'edits', 'item': {**creates[0]['item'], 'id': 'duplicate', 'file': 'src/dup-a.txt'}},
        {'collection': 'edits', 'item': {**creates[0]['item'], 'id': 'duplicate', 'file': 'src/dup-b.txt'}},
    ]
    before = unchanged_state(doc_id, root), source_state(root)
    response = client.post(base+'/items/batch/preview', json={'expected_revision': 1, 'items': incoming})
    assert response.status_code == 200, response.text
    assert [row['valid'] for row in response.json()['items']] == [True]*46 + [False]*4
    assert (unchanged_state(doc_id, root), source_state(root)) == before
    rejected = client.post(base+'/items/batch', json={'expected_revision': 1, 'items': incoming})
    assert rejected.status_code == 422
    assert (unchanged_state(doc_id, root), source_state(root)) == before
    saved = client.post(base+'/items/batch', json={'expected_revision': 1, 'items': incoming[:46]})
    assert saved.status_code == 200 and saved.json()['new_revision'] == 2 and saved.json()['imported_count'] == 46
    current = view(client, doc_id)
    assert [item['id'] for item in current['body']['edit_spec']['edits'][1:]] == [entry['item']['id'] for entry in creates]
    assert [item['id'] for item in current['body']['edit_spec']['deferred'][1:]] == [entry['item']['id'] for entry in deferred]
    assert source_state(root) == before[1]
    assert len(unchanged_state(doc_id, root)[3]) == len(before[0][3]) + 1
    assert current['approval']['attempts'] == [] and current['history']['ledger'] == []
    stable = unchanged_state(doc_id, root)
    stale = client.post(base+'/items/batch', json={'expected_revision': 1, 'items': incoming[:46]})
    assert stale.status_code == 409 and unchanged_state(doc_id, root) == stable
    (root/'gate.ok').write_bytes(b'ok')
    applied = approve(client, doc_id, 2)
    assert applied.status_code == 200, applied.text
    assert (root/'src/bulk-35.txt').read_bytes() == b'new 35\n'
    final = view(client, doc_id)
    assert len(final['approval']['attempts']) == len(final['history']['ledger']) == 1
