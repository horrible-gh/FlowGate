"""0111 T#7: D-1a-1 invalidation entry points and D-1b catalog, using isolated Git only."""
import json
import time
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flowgate.graph_run import card_recovery, execution, fencing, stop, workspace, execution_store as ledger
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.graph_workflow.services import run_apply
from modules.flow_gate.services.git import branches
from modules.flow_gate.services.git.credentials import GitServiceError
from test_graph_attempt_workspaces_0061 import harness, repo, prepare, PROJECT, GROUP  # noqa: F401
from test_graph_attempt_cleanup_preserve_0111 import (
    KEPT, _preserved, _kept, _gone, _git, _integrated, _head_of,
)

PATHS = ['card_recovery', 'lease_expired', 'worktree_failed', 'stop']


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
    from modules.flow_gate.graph_workflow.services import flow_guard, graph_service, card_settings_ports, result_binder, run_service
    from flowgate.graph_time_machine import operation
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
    elif path == 'card_recovery':
        ledger.cancel(item.execution, None, 'requested')
        repo.db._execute("UPDATE graph_runs SET status='idle'")
        mp.setattr(run_store, 'active_run', lambda _: run_store.run_by_id('run'))
        mp.setattr(graph_service, 'require_writable', lambda _: None)
        mp.setattr(operation, 'require_no_operation', lambda _: None)
        mp.setattr(card_settings_ports, 'ai_working', lambda *args: False)
        mp.setattr(card_recovery, '_live_job', lambda *args: False)
        mp.setattr(result_binder, 'next_missing_event', lambda *args: None)
        mp.setattr(run_apply, 'read_run_state', lambda _: {})
        mp.setattr(card_recovery.settle, 'settle_run', lambda _: None)
        mp.setattr(run_service, 'run_payload', lambda *args, **kw: {})
        card_recovery.recover_card({'group_id': GROUP, 'project_id': PROJECT}, 'run', 'a', None, 'user')
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


@pytest.mark.parametrize('node_status', ['done', 'missing'])
def test_expired_cleanup_is_not_skipped_by_node_early_return(repo, monkeypatch, node_status):
    item = _item(repo)
    if node_status == 'done':
        repo.db._execute("UPDATE graph_node_runs SET status='done'")
    else:
        repo.db._execute('DELETE FROM graph_node_runs')
    _invalidate(repo, item, monkeypatch, 'lease_expired')
    assert workspace.attempt(item.attempt)['status'] == 'invalidated' and _gone(repo, item)


def test_failed_recovery_transaction_does_not_cleanup(repo, monkeypatch):
    item = _item(repo)
    def fail(*args):
        raise RuntimeError('rollback invalidation')
    monkeypatch.setattr(stop, 'reset_node', fail)
    cleanup = Mock(side_effect=AssertionError('must not clean a rolled back invalidation'))
    monkeypatch.setattr(workspace, 'cleanup', cleanup)
    with pytest.raises(RuntimeError, match='rollback'):
        _invalidate(repo, item, monkeypatch, 'card_recovery')
    assert workspace.attempt(item.attempt)['status'] == 'active'
    assert item.root.exists()
    cleanup.assert_not_called()


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


def test_i_k_http_catalog_and_user_branch_set_do_not_mix(catalog, monkeypatch):
    from modules.flow_gate.api.v1 import git_routes
    from modules.flow_gate.auth.middleware import get_current_user
    item = _item(catalog)
    users = {'feature/one', 'release/two', 'graph_attempt_user-named'}
    for name in users:
        workspace.git.create_branch(PROJECT, name, 'group')
    catalog.git('branch', 'registered-group')
    external = catalog.group.parent / '.claude' / 'worktrees' / 'agent-fixture'
    external.parent.mkdir(parents=True)
    catalog.git('worktree', 'add', '-b', 'worktree-agent-fixture', str(external), 'group')
    (external / 'draft.txt').write_text('external changes', encoding='utf-8')
    refs = catalog.git('for-each-ref', '--format=%(refname):%(objectname)')
    registry = catalog.git('worktree', 'list', '--porcelain')
    app = FastAPI()
    app.include_router(git_routes.router)
    app.dependency_overrides[get_current_user] = lambda: {'user_id': 'fixture', 'is_admin': True}
    started = time.perf_counter()
    response = TestClient(app).get('/api/v1/projects/p/git/branches')
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    rows = {r['name']: r for r in response.json()['branches']}
    assert rows[item.branch]['kind'] == 'internal_attempt'
    assert rows['worktree-agent-fixture']['kind'] == 'external_worktree'
    for name in (item.branch, 'worktree-agent-fixture'):
        assert rows[name]['can_be_create_source'] is False and rows[name]['can_delete'] is False
        assert rows[name]['create_source_blocked_reason'] == rows[name]['kind']
    assert rows['registered-group']['kind'] == 'internal_slot'
    assert {r['name'] for r in rows.values() if r['kind'] == 'local'} == users
    assert catalog.git('for-each-ref', '--format=%(refname):%(objectname)') == refs
    assert catalog.git('worktree', 'list', '--porcelain') == registry
    assert (external / 'draft.txt').read_text() == 'external changes'
    print(f'CATALOG_HTTP_SECONDS={elapsed:.6f}; USER_LOCAL_SET_EQUAL=True')


@pytest.mark.parametrize('evidence', ['ledger_only', 'marker_only', 'broken_marker', 'wrong_project_marker'])
def test_ownership_evidence_does_not_turn_user_names_into_attempts(catalog, evidence):
    item = _item(catalog)
    marker_path = item.directory / workspace.merge_target.OWNER_MARKER
    if evidence == 'ledger_only':
        marker_path.unlink()
    elif evidence == 'broken_marker':
        marker_path.write_text('{broken', encoding='utf-8')
    elif evidence == 'marker_only':
        catalog.db._execute('DELETE FROM graph_attempt_runs')
        catalog.db._execute('DELETE FROM graph_run_token_map')
        catalog.db._execute('DELETE FROM graph_node_attempts')
    else:
        marker = json.loads(marker_path.read_text())
        marker['project_id'] = 'other'
        marker_path.write_text(json.dumps(marker), encoding='utf-8')
        catalog.db._execute('DELETE FROM graph_attempt_runs')
        catalog.db._execute('DELETE FROM graph_run_token_map')
        catalog.db._execute('DELETE FROM graph_node_attempts')
    rows = {r['name']: r for r in branches.list_branches(PROJECT)['branches']}
    assert rows[item.branch]['kind'] == ('external_worktree' if evidence == 'wrong_project_marker' else 'internal_attempt')


@pytest.mark.parametrize('kind', ['internal_attempt', 'external_worktree'])
def test_locked_resources_cannot_be_create_merge_or_explorer_inputs(catalog, kind):
    if kind == 'internal_attempt':
        item = _item(catalog)
        name = item.branch
        # Detached from a linked worktree: the durable ledger must still protect it.
        catalog.git('worktree', 'remove', str(item.root))
        with pytest.raises(GitServiceError) as error:
            branches.delete_branch(PROJECT, name)
        assert error.value.code == 'branch_is_internal_attempt'
    else:
        name = 'external-fixture'
        catalog.git('worktree', 'add', '-b', name, str(catalog.group.parent / 'external'), 'group')
    before = catalog.git('for-each-ref', '--format=%(refname):%(objectname)')
    with pytest.raises(GitServiceError, match='resource branch'):
        workspace.git.create_branch(PROJECT, 'should-not-exist', name)
    for role in ['source', 'target']:
        with pytest.raises(GitServiceError, match='resource branch'):
            branches._validate_merge_branch(PROJECT, catalog.group, name, role)
    with pytest.raises(GitServiceError, match='resource branch'):
        branches.read_local_branch_tree(PROJECT, name)
    assert catalog.git('for-each-ref', '--format=%(refname):%(objectname)') == before


def test_registered_merge_target_workspace_keeps_its_ordinary_branch_kind(catalog, monkeypatch):
    from modules.flow_gate.services.git import merge_target
    name = 'user/merge-target'
    root = merge_target.workspace_dir(PROJECT, name) / merge_target.WORKSPACE_TREE
    root.parent.mkdir(parents=True)
    catalog.git('worktree', 'add', '-b', name, str(root), 'group')
    monkeypatch.setattr(merge_target, 'inspect_workspace', lambda *args: ('busy', {'merge_id': 1}))
    row = next(r for r in branches.list_branches(PROJECT)['branches'] if r['name'] == name)
    assert row['kind'] == 'local' and row['can_be_create_source'] is True


def test_worktree_list_failure_locks_catalog_and_all_branch_inputs(catalog, monkeypatch):
    item = _item(catalog)
    name = 'worktree-agent-unknown'
    external = catalog.group.parent / '.claude' / 'worktrees' / 'unknown-fixture'
    external.parent.mkdir(parents=True)
    catalog.git('worktree', 'add', '-b', name, str(external), 'group')
    catalog.git('branch', 'registered-group')
    catalog.git('branch', 'ordinary-user')
    (external / 'draft.txt').write_text('external changes', encoding='utf-8')
    before = catalog.git('for-each-ref', '--format=%(refname):%(objectname)')
    registry = catalog.git('worktree', 'list', '--porcelain')
    monkeypatch.setattr(branches, '_branch_context', lambda _: (
        {'enabled': True, 'default_merge_target': name}, catalog.group, 'group'))
    real_git = workspace.git._run_git
    probes = []
    def fail_worktree_list(args, **kwargs):
        if args == ['worktree', 'list', '--porcelain']:
            probes.append(args)
            return SimpleNamespace(returncode=1, stdout='', stderr='injected registry failure')
        return real_git(args, **kwargs)
    monkeypatch.setattr(workspace.git, '_run_git', fail_worktree_list)
    result = branches.list_branches(PROJECT)
    assert len(probes) == 1  # explicit None is reused, never treated as absent or empty
    rows = {row['name']: row for row in result['branches']}
    assert result['default_merge_target'] is None
    assert rows['group']['kind'] == 'base' and rows['group']['can_be_create_source'] is True
    assert rows[item.branch]['kind'] == 'internal_attempt'
    assert rows['registered-group']['kind'] == 'internal_slot'
    for branch in (name, 'ordinary-user'):
        row = rows[branch]
        assert row['kind'] == 'local'  # ownership is unknown; do not invent external ownership
        assert row['can_be_create_source'] is False and row['can_delete'] is False
        assert row['create_source_blocked_reason'] == 'worktree_state_unknown'
        delete_reason = ('branch_is_default_merge_target' if branch == name
                         else 'branch_worktree_state_unknown')
        assert row['delete_blocked_reason'] == delete_reason
        with pytest.raises(GitServiceError) as error:
            branches.create_branch(PROJECT, 'must-not-exist', branch)
        assert error.value.code == 'branch_source_worktree_state_unknown'
        for role in ('source', 'target'):
            with pytest.raises(GitServiceError) as error:
                branches._validate_merge_branch(PROJECT, catalog.group, branch, role)
            assert error.value.code == f'branch_merge_{role}_worktree_state_unknown'
        with pytest.raises(GitServiceError) as error:
            branches.resolve_local_branch_ref(PROJECT, branch)
        assert error.value.code == 'branch_read_worktree_state_unknown'
        with pytest.raises(GitServiceError) as error:
            branches.delete_branch(PROJECT, branch)
        assert error.value.code == delete_reason
    branches._validate_create_source(PROJECT, catalog.group, 'group')
    branches._validate_merge_branch(PROJECT, catalog.group, 'group', 'source')
    assert branches.resolve_local_branch_ref(PROJECT, 'group')[1]
    with pytest.raises(GitServiceError) as error:
        branches._validate_create_source(PROJECT, catalog.group, item.branch)
    assert error.value.code == 'branch_source_internal_attempt'
    with pytest.raises(GitServiceError) as error:
        branches._validate_create_source(PROJECT, catalog.group, 'registered-group')
    assert error.value.code == 'branch_source_internal_slot'
    monkeypatch.setattr(workspace.git, '_run_git', real_git)
    assert catalog.git('for-each-ref', '--format=%(refname):%(objectname)') == before
    assert catalog.git('worktree', 'list', '--porcelain') == registry
    assert (external / 'draft.txt').read_text() == 'external changes'
