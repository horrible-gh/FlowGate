"""0111 T#7: D-1a-1 invalidation entry points and D-1b catalog, using isolated Git only."""
import json
import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flowgate.graph_run import execution, fencing, stop, workspace, execution_store as ledger
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.services.git import branches
from modules.flow_gate.services.git.credentials import GitServiceError
from test_graph_attempt_workspaces_0061 import harness, repo, prepare, PROJECT, GROUP  # noqa: F401
from test_graph_attempt_cleanup_preserve_0111 import (
    KEPT, _preserved, _kept, _gone, _git, _integrated, _head_of,
)

PATHS = ['lease_expired', 'worktree_failed', 'stop']


@pytest.fixture(autouse=True)
def branch_layout(monkeypatch):
    """0111 T#8: these are the branch-layout (``graph_attempt_*``) catalog regressions -- an attempt prepared before
    T#8 keeps its branch and stays ``internal_attempt``. The ref layout is test_graph_attempt_ref_namespace_0111."""
    monkeypatch.setattr(workspace, 'NEW_LAYOUT', workspace.BRANCH_LAYOUT)


def _item(repo):
    item = repo.new()
    item.root = prepare(item)
    item.directory, _, item.branch = workspace.location(workspace.attempt(item.attempt), PROJECT)
    item.head = _head_of(item)
    item.at = lambda *args: _git(item.root, *args)
    return item


def _invalidate(repo, item, mp, path):
    from modules.flow_gate.services import ai_invoke_service
    from modules.flow_gate.graph_workflow.services import flow_guard
    guard = workspace.with_flow_guard
    mp.setattr(flow_guard, 'with_flow_guard', guard)
    mp.setattr(stop, 'with_flow_guard', guard)
    mp.setattr(execution.result_merge, 'recover', lambda: None)
    mp.setattr(stop, 'recover', lambda: None)
    mp.setattr(workspace, 'cleanup_finished', lambda: None)  # prove immediate cleanup, not the final sweep
    mp.setattr(run_apply, 'settle_run', lambda ctx: None)
    if path == 'worktree_failed':
        workspace._failed(item.execution, 'injected')
    elif path == 'lease_expired':
        ledger.acquire(item.execution, 'ai')
        repo.db._execute("UPDATE graph_attempt_runs SET lease_expires_at='2000-01-01' WHERE exec_id=?", [item.execution])
        mp.setattr(ai_invoke_service, 'get_run_record', lambda _: None)
        mp.setattr(execution.ai_invoke_runs, 'get', lambda _: {'status': 'finished'})
        execution.recover()
    else:
        repo.db._execute("UPDATE graph_runs SET status='stopping'")
        mp.setattr(stop, 'unconfirmed', lambda _: [])
        mp.setattr(stop.result_merge, 'abort_for_stop', lambda *args: [])
        mp.setattr(stop.result_merge, 'session_pending', lambda _: False)
        mp.setattr(stop.result_merge, 'integrating', lambda _: [])
        mp.setattr(stop, '_settle_gate_decisions', lambda _: (set(), {}))
        stop.advance(GROUP, 'run')


@pytest.mark.parametrize('path', PATHS)
@pytest.mark.parametrize('case,arrange,reason,judgements', KEPT, ids=[k[0] for k in KEPT])
def test_a_to_f_preserved_at_every_invalidation_entry(repo, monkeypatch, path, case, arrange, reason, judgements):
    item = _item(repo)
    original_cleanup, original_start = workspace.cleanup, workspace.cleanup_finished
    # A remaining requested/running record at cleanup time must still fence removal,
    # even if the invalidation entry point has just cancelled its original execution.
    if not case.startswith('d_'):
        arrange(repo, item, monkeypatch)
    calls = []
    def checked(attempt_id):
        assert not repo.db.db.in_transaction and not repo.held
        assert not fencing._gate(GROUP).closing
        assert workspace.attempt(attempt_id)['status'] == 'invalidated'
        if case.startswith('d_'):
            arrange(repo, item, monkeypatch)
        calls.append(attempt_id)
        return original_cleanup(attempt_id)
    monkeypatch.setattr(workspace, 'cleanup', checked)
    _invalidate(repo, item, monkeypatch, path)
    assert calls == [item.attempt]
    assert _kept(repo, item) and item.root.exists()
    monkeypatch.setattr(workspace, 'cleanup', original_cleanup)
    for _ in range(2):
        original_start()
        assert _kept(repo, item) and item.root.exists()
    assert _preserved(repo, item.attempt) == [reason] * judgements
    assert not repo.held


@pytest.mark.parametrize('path', PATHS)
def test_g_clean_attempt_removed_after_committed_invalidation(repo, monkeypatch, path):
    item = _item(repo)
    before = repo.git('for-each-ref', '--format=%(refname):%(objectname)', 'refs/heads/group')
    _invalidate(repo, item, monkeypatch, path)
    assert _gone(repo, item) and not item.root.exists()
    assert repo.git('for-each-ref', '--format=%(refname):%(objectname)', 'refs/heads/group') == before




@pytest.fixture
def catalog(repo, monkeypatch):
    repo.db._execute('ALTER TABLE groups ADD COLUMN project_id TEXT')
    repo.db._execute('INSERT INTO groups(group_id, project_id) VALUES(?, ?)', [GROUP, PROJECT])
    monkeypatch.setattr(branches, '_branch_context', lambda _: ({'enabled': True}, repo.group, 'group'))
    monkeypatch.setattr(workspace.git.db_git, 'list_states_of_project', lambda _: [
        {'branch': 'registered-group', 'group_id': GROUP, 'project_id': PROJECT}])
    monkeypatch.setattr(workspace.git.db_git, 'list_open_sessions', lambda: [])
    monkeypatch.setattr(workspace.git, 'open_merge_session_of_project', lambda _: None)
    monkeypatch.setattr(branches, '_open_branch_merge_summaries', lambda _: [])
    monkeypatch.setattr(branches, '_meta_lock', lambda _: nullcontext())
    from modules.flow_gate.services.git import group_work_base
    monkeypatch.setattr(group_work_base, 'groups_pinning_work_base', lambda *args: [])
    return repo
