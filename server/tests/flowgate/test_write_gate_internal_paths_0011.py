"""flowgate.v02gui.0011 (0004-CH): internal housekeeping paths skip the document walk of the write gate.

A scratch delete (lock open/unlink, journal write/remove, the tree delete) passed the card document write gate
5-6 times per tok_* folder, and every pass walked all document rows twice under the database-wide path mutex.
These light tests pin the fast path and its fail-closed edges with a fake store (no database, no live server),
and the boot sweep's per-project single flight.
"""
from __future__ import annotations

import os
import threading

import pytest

from flowgate.card_document_edit import write_gate as gate
from modules.flow_gate.services import token_scratch
from modules.flow_gate.storage import paths as storage_paths


class Rows:
    """Stands in for the store: applies the LIKE hints the way the SQL does (case-insensitive substring)."""

    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def _fetch_all(self, sql, params=None):
        self.queries.append(sql)
        hints = [p.strip('%') for p in params or []]
        return [dict(r) for r in self.rows if any(h in r['file_path'].lower() for h in hints)]


@pytest.fixture
def s0(tmp_path, monkeypatch):
    root = tmp_path / 'storage'
    (root / '.flowgate-gc' / 'token').mkdir(parents=True)
    (root / 'work' / 'P' / 'tok_20261114_000001').mkdir(parents=True)
    (root / 'documents' / 'p' / 'main').mkdir(parents=True)
    monkeypatch.setattr(storage_paths, 'get_storage_root', lambda project_id=None, create=False: root)
    return root


def use(monkeypatch, rows):
    store = Rows(rows)
    monkeypatch.setattr(gate.connection, 'get_store', lambda: store)
    return store


def doc(file_path, doc_id='p.0001'):
    return {'doc_id': doc_id, 'project_id': 'p', 'branch': 'main', 'file_path': file_path}


def test_journal_lock_and_scratch_tree_are_internal(s0, monkeypatch):
    use(monkeypatch, [doc('documents/p/main/m/0001/0001-R.md'), doc('documents/p/main/workflow/0002-R.md', 'p.0002')])
    lock = s0 / '.flowgate-gc' / 'token' / 'abc' / 'tok_20261114_000001.lock'
    assert gate.internal_only([lock])
    assert gate.internal_only([], [s0 / 'work' / 'P' / 'tok_20261114_000001'])


@pytest.mark.parametrize('target', ['documents/p/main/a.md', 'work', '.flowgate-gc', 'other/x.md'])
def test_outside_or_the_root_itself_takes_the_full_gate(s0, monkeypatch, target):
    use(monkeypatch, [])
    assert not gate.internal_only([s0 / target])
    assert not gate.internal_only([])


@pytest.mark.parametrize('stored', ['work/P/tok_20261114_000001/a.md', 'documents/p/../../.flowgate-gc/x.json',
                                    'scratch/P/aiv2_20261114_000001/r.md'])
def test_a_document_row_inside_an_internal_root_takes_the_full_gate(s0, monkeypatch, stored):
    use(monkeypatch, [doc(stored)])
    assert not gate.internal_only([s0 / 'work' / 'P' / 'tok_20261114_000001' / 'b.md'])


def test_absolute_row_inside_an_internal_root_takes_the_full_gate(s0, monkeypatch):
    use(monkeypatch, [doc(str(s0 / 'work' / 'P' / 'x.md'))])
    assert not gate.internal_only([s0 / '.flowgate-gc' / 'token' / 'a.lock'])


def test_hard_linked_target_takes_the_full_gate(s0, monkeypatch):
    use(monkeypatch, [])
    original = s0 / 'documents' / 'p' / 'main' / 'a.md'
    original.write_text('doc', encoding='utf-8')
    link = s0 / 'work' / 'P' / 'tok_20261114_000001' / 'a.md'
    try:
        os.link(original, link)
    except (OSError, NotImplementedError):
        pytest.skip('hard links unavailable')
    assert not gate.internal_only([link])


def test_store_failure_takes_the_full_gate(s0, monkeypatch):
    class Broken:
        def _fetch_all(self, *_a, **_k):
            raise RuntimeError('db down')
    monkeypatch.setattr(gate.connection, 'get_store', lambda: Broken())
    assert not gate.internal_only([s0 / 'work' / 'P' / 'x'])


def test_paths_scope_skips_the_walk_and_the_mutex_for_internal_targets(s0, monkeypatch):
    use(monkeypatch, [doc('documents/p/main/m/0001/0001-R.md')])
    walked = []
    monkeypatch.setattr(gate, 'references', lambda *a, **k: walked.append(a) or [])
    monkeypatch.setattr(gate, 'lock_path_namespace', lambda: walked.append('mutex'))
    lock = s0 / '.flowgate-gc' / 'token' / 'a.lock'
    assert gate.paths([lock], lambda: 'done') == 'done'
    with gate.paths_scope([], trees=[s0 / 'work' / 'P' / 'tok_20261114_000001']):
        pass
    assert walked == []


def test_boot_sweep_leaves_a_project_an_issue_sweep_holds(monkeypatch):
    calls = []
    monkeypatch.setattr(token_scratch, '_sweeping', {'busy'})
    monkeypatch.setattr(token_scratch, 'sweep', lambda project_id: calls.append(
        (project_id, project_id in token_scratch._sweeping)))
    monkeypatch.setattr(token_scratch.db_projects, 'list_projects',
                        lambda: [{'project_id': 'busy'}, {'project_id': 'free'}])
    assert token_scratch.startup_sweep() == 2
    assert calls == [('free', True)]               # claimed while it ran
    assert token_scratch._sweeping == {'busy'}     # released afterwards, the issue sweep's claim untouched


def test_issue_sweep_does_not_start_while_the_boot_sweep_holds_the_project(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow(project_id):
        calls.append(project_id)
        started.set()
        release.wait(5)
        return 0
    monkeypatch.setattr(token_scratch, '_sweeping', set())
    monkeypatch.setattr(token_scratch, '_last_sweep', {})
    monkeypatch.setattr(token_scratch, 'sweep', slow)
    monkeypatch.setattr(token_scratch.db_projects, 'list_projects', lambda: [{'project_id': 'p'}])
    boot = threading.Thread(target=token_scratch.startup_sweep, daemon=True)
    boot.start()
    try:
        assert started.wait(5)
        assert token_scratch.sweep_on_issue('p') is None
    finally:
        release.set()
    boot.join(5)
    assert calls == ['p'] and token_scratch._sweeping == set()
