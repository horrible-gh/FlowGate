"""flowgate.v02.0135 T#3/T#4: the card document write gate's document walk is linear and finds the same rows.

``write_gate.references()`` walks every document row twice per gated write -- once before and once inside the
path namespace mutex -- and every document *creation* (a standalone chat, the document of a placed card) goes
through it. It used to read the storage settings 4-8 times and resolve each path ~14 times per row. On the stg
store (9,090 rows on PostgreSQL) one walk took 237 s / 54,541 queries (29 s with the request cache), so a chat
create or a new Flow's seed card save outlived the GUI's 30 s timeout: the GUI showed 새 채팅을 만들지 못했습니다
or re-read the Flow without its seed card, and the server committed minutes later.

These tests pin both halves of the fix: the rows found are exactly those of the previous walk (kept below as the
oracle) for name, legacy-branch, case, absolute-path, hard-link and tree matches, and a walk reads each project's
storage root once instead of once per row.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from flowgate.card_document_edit import write_gate as gate
from modules.flow_gate.storage import paths as storage_paths

SQL = "SELECT * FROM documents WHERE file_path IS NOT NULL AND TRIM(file_path) <> ''"
CH = "documents/p/main/m/0001/0002-CH_document.md"


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def _fetch_all(self, sql, params=None):
        assert " ".join(sql.split()) == SQL
        return [dict(r) for r in self.rows]


def oracle(paths=(), trees=()):
    """The walk as it was before flowgate.v02.0135 (same matches, ~14 resolves and 4-8 settings reads per row)."""
    paths = tuple(Path(p) for p in paths if p is not None)
    trees = tuple(Path(p).resolve() for p in trees if p is not None)
    names, identities = gate.path_index(paths)
    tree_names = {gate.path_key(t) for t in trees}
    for root in trees:
        if root.is_dir():
            for path in root.rglob('*'):
                if path.is_file():
                    identity = gate.file_key(path)
                    if identity is not None:
                        identities.add(identity)
    found = []
    for doc in gate.connection.get_store()._fetch_all(SQL):
        for path, inside in storage_paths.storage_path_candidates(doc.get('file_path'), doc['project_id'],
                                                                  doc.get('branch') or 'main'):
            resolved = Path(path).resolve()
            if (gate.path_key(resolved) in names or
                    any(os.path.normcase(str(parent)) in tree_names for parent in (resolved, *resolved.parents)) or
                    gate.file_key(resolved) in identities):
                found.append((doc['doc_id'], inside))
                break
    return found


@pytest.fixture
def store(tmp_path, monkeypatch):
    roots = {'p': tmp_path / 'storage_p', 'q': tmp_path / 'storage_q'}
    for root in roots.values():
        root.mkdir()
    reads = []

    def root_of(project_id=None, create=False):
        reads.append(project_id)
        return roots[project_id]

    monkeypatch.setattr(storage_paths, 'get_storage_root', root_of)
    target = roots['p'] / CH
    target.parent.mkdir(parents=True)
    target.write_text('chat', encoding='utf-8')
    (roots['p'] / 'elsewhere').mkdir()
    alias = roots['p'] / 'elsewhere' / 'alias.md'
    outside = tmp_path / 'outside.md'
    os.link(target, alias)
    os.link(target, outside)
    rows = [
        {'doc_id': 'p.same', 'project_id': 'p', 'branch': 'main', 'file_path': CH},
        {'doc_id': 'p.legacy', 'project_id': 'p', 'branch': 'main', 'file_path': 'documents/p/m/0001/0002-CH_document.md'},
        {'doc_id': 'p.case', 'project_id': 'p', 'branch': 'main', 'file_path': CH.upper().replace('.MD', '.md')},
        {'doc_id': 'p.abs', 'project_id': 'p', 'branch': None, 'file_path': str(target)},
        {'doc_id': 'p.storage', 'project_id': 'p', 'branch': 'main', 'file_path': '/storage/' + CH},
        {'doc_id': 'p.hardlink', 'project_id': 'p', 'branch': 'main', 'file_path': 'elsewhere/alias.md'},
        {'doc_id': 'p.outside', 'project_id': 'p', 'branch': 'main', 'file_path': str(outside)},
        {'doc_id': 'q.same_name', 'project_id': 'q', 'branch': 'main', 'file_path': 'documents/q/main/m/0001/0002-CH_document.md'},
    ]
    rows += [{'doc_id': f'{pid}.other{i}', 'project_id': pid, 'branch': 'main',
              'file_path': f'documents/{pid}/main/m/{i:04d}/0001-R_document.md'} for i in range(60) for pid in 'pq']
    monkeypatch.setattr(gate.connection, 'get_store', lambda: Rows(rows))
    return {'roots': roots, 'target': target, 'reads': reads, 'rows': rows}


def found(paths=(), trees=()):
    return [(doc['doc_id'], inside) for doc, inside in gate.references(paths, trees)]


def test_an_existing_target_finds_the_same_rows_as_before(store):
    expected = oracle([store['target']])
    assert found([store['target']]) == expected
    ids = {doc_id for doc_id, _ in expected}
    assert {'p.same', 'p.legacy', 'p.abs', 'p.storage', 'p.hardlink', 'p.outside'} <= ids
    assert 'q.same_name' not in ids and not any(i.startswith(('p.other', 'q.other')) for i in ids)
    assert dict(expected)['p.outside'] is False  # outside the jail: still found, marked not inside


def test_a_new_file_finds_the_same_rows_as_before(store):
    new = store['roots']['p'] / 'documents/p/main/m/0001/0003-CH_document.md'
    assert found([new]) == oracle([new]) == []
    store['target'].unlink()  # a name match needs no file; the identity matches go with it
    assert found([store['target']]) == oracle([store['target']])
    assert {'p.same', 'p.legacy', 'p.abs', 'p.storage'} <= {doc_id for doc_id, _ in found([store['target']])}


def test_a_tree_finds_the_same_rows_as_before(store):
    tree = store['roots']['p'] / 'documents/p/main/m'
    assert found(trees=[tree]) == oracle(trees=[tree])
    assert {doc_id for doc_id, _ in found(trees=[tree])} >= {'p.same', 'p.legacy', 'p.hardlink'}


def test_one_walk_reads_each_projects_storage_root_once(store):
    store['reads'].clear()
    matched = gate.references([store['target']])
    walk_reads = list(store['reads'])
    # one root per project for the whole walk, plus the jail check of each matched row (all of project p)
    assert walk_reads.count('q') == 1
    assert walk_reads.count('p') == 1 + len(matched)
    store['reads'].clear()
    oracle([store['target']])
    assert len(store['reads']) > 2 * 128  # the previous walk: two or three reads for every one of the 128 rows


# ── review rework: one resolve per directory (storage_paths.memo_resolver) ─────────────────────────────────────────

def _links(store):
    """A file symlink, a directory symlink and a '..' row towards the target; skipped where links cannot be made."""
    root, target = store['roots']['p'], store['target']
    (root / 'links').mkdir()
    try:
        os.symlink(target, root / 'links' / 'link.md')
        os.symlink(target.parent, root / 'dirlink', target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symbolic links are not available here: {exc}')
    store['rows'] += [
        {'doc_id': 'p.symlink_file', 'project_id': 'p', 'branch': 'main', 'file_path': 'links/link.md'},
        {'doc_id': 'p.symlink_dir', 'project_id': 'p', 'branch': 'main', 'file_path': 'dirlink/0002-CH_document.md'},
        {'doc_id': 'p.dotdot', 'project_id': 'p', 'branch': 'main', 'file_path': 'documents/p/main/m/0002/../0001/0002-CH_document.md'},
        {'doc_id': 'p.dotdot_miss', 'project_id': 'p', 'branch': 'main', 'file_path': 'documents/p/main/m/0001/../0002/0002-CH_document.md'},
    ]


def test_links_and_dot_segments_find_the_same_rows_as_before(store):
    _links(store)
    expected = oracle([store['target']])
    assert found([store['target']]) == expected
    ids = {doc_id for doc_id, _ in expected}
    assert {'p.symlink_file', 'p.symlink_dir', 'p.dotdot'} <= ids and 'p.dotdot_miss' not in ids
    new = store['roots']['p'] / 'dirlink' / '0009-CH_document.md'  # a new file reached through the linked directory
    assert found([new]) == oracle([new])
    tree = store['roots']['p'] / 'documents/p/main/m'
    assert found(trees=[tree]) == oracle(trees=[tree])


def test_the_memo_resolver_answers_as_path_resolve(store, tmp_path):
    _links(store)
    root = store['roots']['p']
    resolve = storage_paths.memo_resolver()
    probes = [root / r['file_path'] for r in store['rows'] if not Path(r['file_path']).is_absolute()]
    probes += [root / 'missing' / 'deeper' / 'x.md', root / 'links' / 'link.md', root / 'dirlink' / 'nothing.md',
               root / 'documents' / '..' / 'documents' / 'p', root / 'trailing.', tmp_path / 'outside.md']
    for path in probes + probes:  # the second pass answers from the memo
        assert os.path.normcase(str(resolve(path))) == os.path.normcase(str(path.resolve(strict=False))), path


def test_one_walk_resolves_each_directory_once(store, monkeypatch):
    calls = []
    real = os.path.realpath

    def counting(path, *args, **kwargs):
        calls.append(path)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os.path, 'realpath', counting)
    before = len(gate.references([store['target']]))
    walk = len(calls)
    # 300 more rows in two directories cost at most one resolve per directory and candidate kind (primary and
    # legacy-branch fallback): 4, where the previous walk resolved every candidate of every row (600 more).
    store['rows'] += [{'doc_id': f'p.bulk{i}', 'project_id': 'p', 'branch': 'main',
                       'file_path': f'documents/p/main/m/{9000 + i % 2}/{i:04d}-R_document.md'} for i in range(300)]
    calls.clear()
    assert len(gate.references([store['target']])) == before
    assert len(calls) - walk <= 4
