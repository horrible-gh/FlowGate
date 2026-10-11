"""0640 T#3: real API/DB/Git plus conservative comparison boundary checks."""
import base64
import copy
import os

import pytest

from modules.flow_gate.documents import tr2_source_import as sources, tr2_service as service
from modules.flow_gate.db import documents as db_docs
from test_tr2_batch_import_0640 import proposal, env, store, repo, unchanged_state
from test_tr2_connected_e2e_0565 import git, approve, view


def file(path, content=b'new content\n'):
    if isinstance(content, str):
        content = content.encode()
    return {'file': path, 'content_base64': base64.b64encode(content).decode()}


def preview(client, base, files, revision=1):
    response = client.post(base + '/sources/preview', json={'expected_revision': revision, 'files': files})
    assert response.status_code == 200, response.text
    return response.json()


def save(client, base, files, result, selected=None, revision=1):
    return client.post(base + '/sources', json={'expected_revision': revision, 'files': files,
        'selected': selected if selected is not None else [r['index'] for r in result['files'] if r['valid']],
        'preview_fingerprint': result['preview_fingerprint']})


def seed(repo, name, content):
    (repo / name).write_bytes(content)
    git(repo, 'add', '--', name)
    git(repo, 'commit', '-m', 'comparison fixture')


def source_state(repo):
    return (git(repo, 'status', '--porcelain'), git(repo, 'rev-parse', 'HEAD'),
            {p.relative_to(repo).as_posix(): p.read_bytes() for p in (repo / 'src').rglob('*') if p.is_file()})


def test_mixed_compare_atomic_save_and_actual_existing_approval(proposal):
    env, doc_id, client, base = proposal
    root = env['repo']
    seed(root, 'src/compare.txt', b'first\nold unique line\nlast\n')
    incoming = [file('src/compare.txt', 'first\nnew unique line\nlast\n'),
                file('src/new.txt'), file('src/app.txt', (root/'src/app.txt').read_bytes()),
                file('../outside'), file('src/binary.txt', b'\x00x'), file('src/absent/new.txt')]
    before = unchanged_state(doc_id, root), source_state(root)
    result = preview(client, base, incoming)
    assert [r['status'] for r in result['files']] == ['EDIT', 'CREATE', 'unchanged'] + ['review_required'] * 3
    assert (unchanged_state(doc_id, root), source_state(root)) == before
    rejected = save(client, base, incoming, result, [0, 1, 3])
    assert rejected.status_code == 422
    assert (unchanged_state(doc_id, root), source_state(root)) == before
    response = save(client, base, incoming, result)
    assert response.status_code == 200, response.text
    assert response.json()['new_revision'] == 2 and response.json()['imported_count'] == 2
    assert response.json()['operation'] == 'import_sources'
    assert source_state(root) == before[1]
    current = view(client, doc_id)
    assert current['approval']['attempts'] == [] and current['history']['ledger'] == []
    assert len(unchanged_state(doc_id, root)[3]) == len(before[0][3]) + 1
    assert current['body']['edit_spec']['gate'] == __import__('json').loads(before[0][1])['edit_spec']['gate']
    stale_state = unchanged_state(doc_id, root)
    replay = save(client, base, incoming, result)
    assert replay.status_code == 409
    assert unchanged_state(doc_id, root) == stale_state
    (root / 'gate.ok').write_text('ok')
    applied = approve(client, doc_id, 2)
    assert applied.status_code == 200, applied.text
    assert (root / 'src/compare.txt').read_text() == 'first\nnew unique line\nlast\n'
    assert (root / 'src/new.txt').read_bytes() == b'new content\n'
    final = view(client, doc_id)
    assert len(final['approval']['attempts']) == len(final['history']['ledger']) == 1


def test_55_files_normal_and_error_subset(proposal):
    env, doc_id, client, base = proposal
    incoming = [file(f'src/new{i}.txt') for i in range(55)] + [file('src/../bad'), file('src/bad', b'\xff')]
    before = source_state(env['repo'])
    result = preview(client, base, incoming)
    assert sum(r['valid'] for r in result['files']) == 55
    saved = save(client, base, incoming, result, list(range(54)))
    assert saved.status_code == 200 and saved.json()['imported_count'] == 54
    assert saved.json()['new_revision'] == 2 and source_state(env['repo']) == before
    assert len(service.load_current(db_docs.get_by_id(doc_id))['edit_spec']['edits']) == 55


@pytest.mark.parametrize('mode', ['changed', 'deleted', 'created', 'pending', 'input'])
def test_source_drift_cannot_silently_rebase(proposal, mode):
    env, doc_id, client, base = proposal
    root = env['repo']
    seed(root, 'src/compare.txt', b'old\n')
    incoming = [file('src/compare.txt', b'new\n'), file('src/new.txt')]
    result = preview(client, base, incoming)
    if mode == 'changed': (root / 'src/compare.txt').write_bytes(b'foreign\n')
    elif mode == 'deleted': (root / 'src/compare.txt').unlink()
    elif mode == 'created': (root / 'src/new.txt').write_bytes(b'foreign\n')
    elif mode == 'pending': (root / 'src/app.txt').write_bytes(b'foreign\n')
    else: incoming[0] = file('src/compare.txt', b'tampered\n')
    before = unchanged_state(doc_id, root), source_state(root)
    response = save(client, base, incoming, result)
    assert response.status_code == 409 and response.json()['code'] == 'tr2_source_drift', response.text
    assert (unchanged_state(doc_id, root), source_state(root)) == before


def test_repreview_does_not_rebase_prior_pending_edits(proposal):
    env, doc_id, client, base = proposal
    root = env['repo']
    (root/'src/app.txt').write_bytes(b'foreign\n')
    incoming = [file('src/new.txt')]
    result = preview(client, base, incoming)
    before = unchanged_state(doc_id, root)
    response = save(client, base, incoming, result)
    assert response.status_code == 409 and response.json()['code'] == 'tr2_source_drift'
    assert unchanged_state(doc_id, root) == before


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'C:\\escape', 'src/.git/config',
    'src/.GIT/config', 'src/CON.txt', 'src/ads:stream', 'src/trailing.', 'src/new\x01.txt', 'src/absent/a'])
def test_unsafe_paths_are_per_file_review_and_no_writes(proposal, path):
    env, doc_id, client, base = proposal
    before = unchanged_state(doc_id, env['repo'])
    result = preview(client, base, [file(path), file('src/valid.txt')])
    assert [r['valid'] for r in result['files']] == [False, True]
    assert unchanged_state(doc_id, env['repo']) == before


def test_duplicates_normalized_casefold_and_existing_target_are_review(proposal):
    _env, _id, client, base = proposal
    incoming = [file('src/new.txt'), file('src/./new.txt'), file('src/A.txt'), file('src/a.txt'), file('src/app.txt', 'changed')]
    result = preview(client, base, incoming)
    assert all(not r['valid'] for r in result['files'])


def test_symlink_target_and_parent_rejected_including_inside_root(proposal):
    env, doc_id, client, base = proposal
    root = env['repo']
    try:
        (root/'src/link.txt').symlink_to(root/'src/app.txt')
        (root/'linked').symlink_to(root/'src', target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'host cannot create symlinks: {exc.__class__.__name__}')
    result = preview(client, base, [file('src/link.txt'), file('linked/new.txt')])
    assert all(not r['valid'] for r in result['files'])
    assert (root/'src/app.txt').read_bytes() == b"greeting = 'hello'\ncount = 1\n"


@pytest.mark.parametrize('content', [b'\xff', b'\x00binary', b'\x01control', b'x' * (sources.MAX_BYTES + 1), b''], ids=['invalid_utf8','nul','control','oversize','empty'])
def test_text_encoding_size_and_empty_create(proposal, content):
    _env, _id, client, base = proposal
    result = preview(client, base, [file('src/new.txt', content)])
    assert not result['files'][0]['valid']


def test_base64_count_total_and_unknown_fields(proposal):
    env, doc_id, client, base = proposal
    before = unchanged_state(doc_id, env['repo'])
    invalid = [{'file': 'src/a', 'content_base64': '!!!'}, {**file('src/b'), 'root': '/host'}]
    assert not any(r['valid'] for r in preview(client, base, invalid)['files'])
    for incoming in ([], [file(f'src/a{i}') for i in range(201)], [file(f'src/a{i}', b'a'*sources.MAX_BYTES) for i in range(11)]):
        result = client.post(base+'/sources/preview', json={'expected_revision': 1, 'files': incoming})
        assert result.status_code == 422, result.text
    assert unchanged_state(doc_id, env['repo']) == before


def test_multi_hunk_ambiguous_anchor_and_python_dry_apply(proposal):
    env, _id, client, base = proposal
    root = env['repo']
    old = 'old one\nkeep\nold two\n'
    seed(root, 'src/hunks.txt', old.encode())
    repeated = ('same\n' * 30)
    seed(root, 'src/repeated.txt', repeated.encode())
    seed(root, 'src/parse.py', b'number = 1\n')
    result = preview(client, base, [file('src/hunks.txt', 'new one\nkeep\nnew two\n'),
        file('src/repeated.txt', repeated[:len('same\n')*15]+'changed\n'+repeated[len('same\n')*16:]),
        file('src/parse.py', 'number = (\n')])
    assert all(not r['valid'] for r in result['files'])


def test_utf8_bom_crlf_preserved_through_existing_approval(proposal):
    env, doc_id, client, base = proposal
    root = env['repo']
    seed(root, 'src/eol.txt', b'\xef\xbb\xbffirst\r\nold\r\n')
    incoming = [file('src/eol.txt', b'first\nnew\n')]
    result = preview(client, base, incoming)
    assert result['files'][0]['status'] == 'EDIT'
    assert save(client, base, incoming, result).status_code == 200
    (root/'gate.ok').write_text('ok')
    assert approve(client, doc_id, 2).status_code == 200
    assert (root/'src/eol.txt').read_bytes() == b'\xef\xbb\xbffirst\r\nnew\r\n'


def test_snapshot_failure_rolls_back_all_source_items(proposal, monkeypatch):
    env, doc_id, client, base = proposal
    incoming = [file('src/a.txt'), file('src/b.txt')]
    result = preview(client, base, incoming)
    before = unchanged_state(doc_id, env['repo']), source_state(env['repo'])
    original = service._record_snapshot
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('injected snapshot failure')
    monkeypatch.setattr(service, '_record_snapshot', fail)
    response = save(client, base, incoming, result)
    assert response.status_code == 500
    assert (unchanged_state(doc_id, env['repo']), source_state(env['repo'])) == before


@pytest.mark.parametrize('reason', ['approved', 'applying', 'recovery_required', 'revert_pending'])
def test_domain_mutation_locks(proposal, monkeypatch, reason):
    env, doc_id, _client, _base = proposal
    monkeypatch.setattr(service, 'mutation_block', lambda _doc: reason)
    before = unchanged_state(doc_id, env['repo'])
    for action in (lambda: sources.preview(doc_id, [file('src/a')], expected_revision=1),
                   lambda: sources.save(doc_id, [file('src/a')], [0], 'invalid', actor='human', expected_revision=1)):
        with pytest.raises(service.Tr2ValidationError): action()
    assert unchanged_state(doc_id, env['repo']) == before


def test_source_lock_failure_and_stale_preview_never_write(proposal, monkeypatch):
    env, doc_id, client, base = proposal
    before = unchanged_state(doc_id, env['repo'])
    stale = client.post(base+'/sources/preview', json={'expected_revision': 0, 'files': [file('src/a')]})
    assert stale.status_code == 409
    def blocked(*args, **kwargs):
        raise service.Tr2ValidationError('tr2_source_locked', 'source_lock')
    monkeypatch.setattr(sources.tr2_precheck, '_acquire_group', blocked)
    response = client.post(base+'/sources/preview', json={'expected_revision': 1, 'files': [file('src/a')]})
    assert response.status_code == 409 and response.json()['code'] == 'tr2_source_locked'
    assert unchanged_state(doc_id, env['repo']) == before
