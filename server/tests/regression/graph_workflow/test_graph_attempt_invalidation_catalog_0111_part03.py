"""Independently selectable scenarios; shared fixtures remain in test_graph_attempt_invalidation_catalog_0111.py."""
from __future__ import annotations

from tests.regression.graph_workflow.test_graph_attempt_invalidation_catalog_0111 import (
    FastAPI,
    GROUP,
    GitServiceError,
    KEPT,
    PATHS,
    PROJECT,
    SimpleNamespace,
    TestClient,
    _git,
    _gone,
    _head_of,
    _integrated,
    _invalidate,
    _item,
    _kept,
    _preserved,
    branch_layout,
    branches,
    catalog,
    execution,
    fencing,
    harness,
    json,
    ledger,
    nullcontext,
    prepare,
    pytest,
    repo,
    run_apply,
    run_store,
    stop,
    time,
    workspace,
)

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
