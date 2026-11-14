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
LIGHT = "SELECT doc_id, project_id, branch, file_path FROM documents WHERE file_path IS NOT NULL AND TRIM(file_path) <> ''"
FULL = "SELECT * FROM documents WHERE doc_id IN ("
CH = "documents/p/main/m/0001/0002-CH_document.md"


class Rows:
    """The three reads of a walk: every full row, the four columns a filtered walk decides on, and full rows by id."""

    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def _fetch_all(self, sql, params=None):
        sql = " ".join(sql.split())
        self.sql.append((sql, params))
        if sql == SQL:
            return [dict(r) for r in self.rows]
        if sql == LIGHT:
            return [{k: r.get(k) for k in ('doc_id', 'project_id', 'branch', 'file_path')} for r in self.rows]
        if sql.startswith(LIGHT + " AND ("):  # files.document_using_file narrowed by name (v02gui.0012)
            pattern, *chars = params
            return [{k: r.get(k) for k in ('doc_id', 'project_id', 'branch', 'file_path')} for r in self.rows
                    if like(r['file_path'].lower(), pattern) or any(like(r['file_path'], c) for c in chars)]
        assert sql.startswith(FULL) and sql.count('?') == len(params), sql
        return [dict(r) for r in self.rows if r['doc_id'] in params]


def like(value, pattern):
    """SQL ``value LIKE pattern`` without an escape character."""
    import re
    regex = ''.join('.*' if ch == '%' else '.' if ch == '_' else re.escape(ch) for ch in pattern)
    return re.fullmatch(regex, value, re.DOTALL) is not None


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
    db = Rows(rows)
    monkeypatch.setattr(gate.connection, 'get_store', lambda: db)
    return {'roots': roots, 'target': target, 'reads': reads, 'rows': rows, 'db': db}


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


# ── 0001-B A (flowgate.v02gui.0012): only rows that can match are walked ────────────────────────────────────────────

SOLO = "documents/p/main/m/0003/0005-R_document.md"


def _solo(store):
    """A target with one link count (no hard link anywhere), the rows that can reach it and the rows that cannot."""
    root = store['roots']['p']
    target = root / SOLO
    target.parent.mkdir(parents=True)
    target.write_text('solo', encoding='utf-8')
    (root / 'solo_links').mkdir()
    try:
        os.symlink(target, root / 'solo_links' / 'renamed.md')
        os.symlink(target.parent, root / 'solo_dir', target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'symbolic links are not available here: {exc}')
    store['rows'] += [
        {'doc_id': 'solo.same', 'project_id': 'p', 'branch': 'main', 'file_path': SOLO},
        {'doc_id': 'solo.legacy', 'project_id': 'p', 'branch': 'main', 'file_path': 'documents/p/m/0003/0005-R_document.md'},
        {'doc_id': 'solo.case', 'project_id': 'p', 'branch': 'main', 'file_path': SOLO.replace('0005-R_document', '0005-r_DOCUMENT')},
        {'doc_id': 'solo.abs', 'project_id': 'p', 'branch': None, 'file_path': str(target)},
        {'doc_id': 'solo.storage', 'project_id': 'p', 'branch': 'main', 'file_path': '/storage/' + SOLO},
        {'doc_id': 'solo.backslash', 'project_id': 'p', 'branch': 'main', 'file_path': ' ' + SOLO.replace('/', '\\') + ' '},
        {'doc_id': 'solo.symlink_file', 'project_id': 'p', 'branch': 'main', 'file_path': 'solo_links/renamed.md'},
        {'doc_id': 'solo.symlink_dir', 'project_id': 'p', 'branch': 'main', 'file_path': 'solo_dir/0005-R_document.md'},
        {'doc_id': 'solo.dotdot', 'project_id': 'p', 'branch': 'main', 'file_path': 'documents/p/main/m/0003/x/..'},
        {'doc_id': 'solo.neighbour', 'project_id': 'p', 'branch': 'main', 'file_path': 'documents/p/main/m/0003/0006-R_document.md'},
    ]
    return target


def test_a_single_link_target_walks_only_rows_that_can_match_and_finds_the_same_rows(store, monkeypatch):
    target = _solo(store)
    assert os.stat(target).st_nlink == 1
    expected = oracle([target])
    walked = []
    real = gate._cannot_match

    def counting(doc, *args):
        skipped = real(doc, *args)
        if not skipped:
            walked.append(doc['doc_id'])
        return skipped

    monkeypatch.setattr(gate, '_cannot_match', counting)
    assert found([target]) == expected
    ids = {doc_id for doc_id, _ in expected}
    assert {'solo.same', 'solo.legacy', 'solo.case', 'solo.abs', 'solo.storage', 'solo.backslash',
            'solo.symlink_file', 'solo.symlink_dir'} <= ids
    assert 'solo.neighbour' not in ids and 'p.same' not in ids
    # the 120 other rows, the earlier fixture's rows and the neighbour are never resolved
    assert not any(doc_id.startswith(('p.other', 'q.other')) for doc_id in walked)
    assert 'solo.neighbour' not in walked and 'solo.symlink_file' in walked and 'solo.dotdot' in walked
    new = target.with_name('0007-R_document.md')
    assert found([new]) == oracle([new]) == []


def test_hard_links_symbolic_links_and_unplain_names_still_walk_every_row(store, tmp_path):
    target = _solo(store)
    assert gate._name_filter([target]) is not None
    os.link(target, tmp_path / 'solo-hard.md')  # now nlink 2: an unrelated name may share the file
    store['rows'].append({'doc_id': 'solo.hard', 'project_id': 'p', 'branch': None, 'file_path': str(tmp_path / 'solo-hard.md')})
    assert gate._name_filter([target]) is None
    assert found([target]) == oracle([target]) and 'solo.hard' in dict(found([target]))
    assert gate._name_filter([store['roots']['p'] / 'solo_links' / 'renamed.md']) is None
    for name in ('x.md.', 'x.md ', 'LONGNA~1.MD', 'x.md:stream', '한글.md', '..'):
        assert gate._name_filter([target.with_name(name)]) is None, name


# ── 0001-B A, second pass (flowgate.v02gui.0012 0004-CH turn 49): the fixed cost of each walk ──────────────────────

def _plain_target(store, rel='documents/p/main/m/0004/0007-R_document.md'):
    target = store['roots']['p'] / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('plain', encoding='utf-8')
    return target


def test_a_filtered_walk_reads_four_columns_and_the_full_rows_of_the_walked_rows_only(store):
    target = _plain_target(store)
    store['rows'] += [
        {'doc_id': 'f.same', 'project_id': 'p', 'branch': 'main', 'title': 'full',
         'file_path': 'documents/p/main/m/0004/0007-R_document.md'},
        {'doc_id': 'f.legacy', 'project_id': 'p', 'branch': 'main', 'title': 'full',
         'file_path': 'documents/p/m/0004/0007-R_document.md'},
    ]
    store['db'].sql.clear()
    result = gate.references([target])
    reads = list(store['db'].sql)
    assert [(d['doc_id'], inside) for d, inside in result] == oracle([target])
    assert {d['doc_id'] for d, _ in result} == {'f.same', 'f.legacy'}
    assert all(d['title'] == 'full' for d, _ in result)  # the callers still get every column of a matched row
    assert reads[0][0] == LIGHT and all(sql != SQL for sql, _ in reads)
    assert sorted(i for sql, params in reads if sql.startswith(FULL) for i in params) == ['f.legacy', 'f.same']


def test_a_filtered_walk_skips_a_row_deleted_or_emptied_since_the_light_read(store, monkeypatch):
    target = _plain_target(store)
    store['rows'] += [{'doc_id': 'f.gone', 'project_id': 'p', 'branch': 'main',
                       'file_path': 'documents/p/main/m/0004/0007-R_document.md'},
                      {'doc_id': 'f.emptied', 'project_id': 'p', 'branch': 'main',
                       'file_path': 'documents/p/main/m/0004/0007-R_document.md'}]
    db, real = store['db'], store['db']._fetch_all

    def racing(sql, params=None):
        rows = real(sql, params)
        if ' '.join(sql.split()).startswith(FULL):
            rows = [dict(r, file_path='  ') if r['doc_id'] == 'f.emptied' else r
                    for r in rows if r['doc_id'] != 'f.gone']
        return rows
    monkeypatch.setattr(db, '_fetch_all', racing)
    assert gate.references([target]) == []


def test_link_listings_are_shared_by_the_walks_of_one_transaction(store, monkeypatch):
    target = _solo(store)
    expected = oracle([target])
    tx, ended = object(), []
    monkeypatch.setattr(gate, 'transaction', lambda: tx)
    monkeypatch.setattr(gate.connection, 'after_commit', ended.append)
    monkeypatch.setattr(gate.connection, 'after_rollback', ended.append)
    listed, real = [], gate._links_in
    monkeypatch.setattr(gate, '_links_in', lambda directory: listed.append(directory) or real(directory))
    assert found([target]) == expected and 'solo.symlink_file' in dict(expected)
    first = len(listed)
    assert first > 0
    assert found([target]) == expected  # the link with another name is still found from the shared listing
    assert len(listed) == first  # no directory listed again within the transaction
    for clear in ended:
        clear()
    assert found([target]) == expected
    assert len(listed) == 2 * first  # the next transaction lists afresh
    other = target.with_name('0008-R_document.md')
    assert found([other]) == oracle([other])  # another target in the same transaction: the same rows as before


def test_paths_scope_walks_once_when_the_namespace_mutex_is_already_held(store, monkeypatch):
    from contextlib import contextmanager
    tx = object()
    monkeypatch.setattr(gate, 'transaction', lambda: tx)
    monkeypatch.setattr(gate, 'internal_only', lambda *a, **k: False)
    monkeypatch.setattr(gate, 'lock_path_namespace', lambda: None)

    @contextmanager
    def no_guard(docs):
        assert docs == []
        yield
    monkeypatch.setattr(gate, 'guard_scope', no_guard)
    walks, real = [], gate.references
    monkeypatch.setattr(gate, 'references', lambda *a, **k: walks.append(a) or real(*a, **k))
    new = store['roots']['p'] / 'documents/p/main/m/0005/0009-X_document.md'
    mutex = gate._namespace_tx.set(None)
    checked = gate._checked_paths.set(None)
    try:
        with gate.paths_scope([new]):
            pass
        assert len(walks) == 2  # the mutex taken here: walked again under it
        walks.clear()
        gate._namespace_tx.set(tx)
        with gate.paths_scope([new]):
            pass
        assert len(walks) == 1  # already held before the first walk: nothing can attach a path in between
    finally:
        gate._checked_paths.reset(checked)
        gate._namespace_tx.reset(mutex)


def test_a_file_usage_check_reads_each_projects_storage_root_once(store, monkeypatch):
    from modules.flow_gate.storage import files
    monkeypatch.setattr(files, 'get_store', lambda: store['db'])
    missing = store['roots']['p'] / 'documents/p/main/m/0006/0002-CH_document.md'
    store['reads'].clear()
    assert files.document_using_file(missing) is None
    assert sorted(store['reads']) == ['p', 'q']  # six same-name rows, one root read per project
    used = _plain_target(store, 'documents/p/main/m/0006/0002-CH_document.md')
    store['rows'].append({'doc_id': 'u.same', 'project_id': 'p', 'branch': 'main',
                          'file_path': 'documents/p/m/0006/0002-CH_document.md'})  # legacy form, found by fallback
    assert files.document_using_file(used) == {'doc_id': 'u.same', 'project_id': 'p'}
    assert files.document_using_file(used, ('u.same',)) is None


def using_file_oracle(abs_path, exclude_doc_ids=()):
    """``files.document_using_file`` as it was before flowgate.v02gui.0012 0004-CH turn 60: every row read, the
    target stat-ed and resolved again for each candidate (``files.same_file``)."""
    from modules.flow_gate.storage import files
    path = Path(abs_path)
    if path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
        return {"doc_id": None, "project_id": None}
    name = path.name.casefold()
    for row in files.get_store()._fetch_all(LIGHT):
        stored = row["file_path"].strip().replace("\\", "/")
        if row["doc_id"] in exclude_doc_ids or stored.rsplit("/", 1)[-1].casefold() != name:
            continue
        root = storage_paths.get_storage_root(row["project_id"]).resolve()
        for candidate in storage_paths.storage_candidate_paths(stored, root, row.get("branch") or "main"):
            if files.same_file(candidate, path):
                return {"doc_id": row["doc_id"], "project_id": row["project_id"]}
    return None


def test_a_file_usage_check_reads_only_rows_that_may_have_its_name_and_answers_as_before(store, monkeypatch):
    """flowgate.v02gui.0012 (0004-CH turn 60): an R<->B switch asks 4 times; each call read 4 columns of every row
    (9,454 on stg) to keep the ~50 with the same name. The database now drops the rest; the answer is the same."""
    from modules.flow_gate.storage import files
    monkeypatch.setattr(files, 'get_store', lambda: store['db'])
    root = store['roots']['p']
    store['rows'].extend([
        {'doc_id': 'p.upper', 'project_id': 'p', 'branch': 'main', 'file_path': 'documents/p/main/m/0007/FILE.MD'},
        {'doc_id': 'p.ligature', 'project_id': 'p', 'branch': 'main',
         'file_path': 'documents/p/main/m/0008/ﬁle.md'},             # 'ﬁle.md'.casefold() == 'file.md'
        {'doc_id': 'p.dotless', 'project_id': 'p', 'branch': 'main',
         'file_path': 'documents/p/main/m/0009/fıle.md'},            # a Turkish LOWER('I'): kept by the pattern
        {'doc_id': 'p.padded', 'project_id': 'p', 'branch': 'main', 'file_path': ' documents/p/main/m/0010/file.md  '},
    ])
    existing = root / 'documents/p/main/m/0010/file.md'
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text('x', encoding='utf-8')
    targets = [store['target'], root / 'documents/p/main/m/0006/0002-CH_document.md', root / 'documents/p/main/m/0007/file.md',
               existing, root / 'documents/p/main/m/0011/file.md', root / 'documents/p/main/m/0001/nothing.md']
    for target in targets:
        for excluded in ((), ('p.same',), ('p.padded',)):
            assert files.document_using_file(target, excluded) == using_file_oracle(target, excluded), (target, excluded)

    store['db'].sql.clear()
    files.document_using_file(root / 'documents/p/main/m/0007/file.md')  # the row found differs by OS (normcase)
    [(sql, params)] = store['db'].sql
    assert sql.startswith(LIGHT + " AND (") and params[0] == '%f_le.md%'
    narrowed = store['db']._fetch_all(sql, params)
    assert {r['doc_id'] for r in narrowed} == {'p.upper', 'p.ligature', 'p.dotless', 'p.padded'}  # of 132 rows


def test_a_file_usage_check_of_a_name_sql_cannot_narrow_reads_every_row(store, monkeypatch):
    from modules.flow_gate.storage import files
    monkeypatch.setattr(files, 'get_store', lambda: store['db'])
    assert files._same_name_filter('0002-CH_document.md') is not None
    for name in ('문서.md', 'a\\b.md', ''):
        assert files._same_name_filter(name) is None
    target = store['roots']['p'] / 'documents/p/main/m/0012/문서.md'
    store['db'].sql.clear()
    assert files.document_using_file(target) is None
    assert [sql for sql, _ in store['db'].sql] == [LIGHT]
