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

@pytest.mark.parametrize('node_status', ['done', 'missing'])
def test_expired_cleanup_is_not_skipped_by_node_early_return(repo, monkeypatch, node_status):
    item = _item(repo)
    if node_status == 'done':
        repo.db._execute("UPDATE graph_node_runs SET status='done'")
    else:
        repo.db._execute('DELETE FROM graph_node_runs')
    _invalidate(repo, item, monkeypatch, 'lease_expired')
    assert workspace.attempt(item.attempt)['status'] == 'invalidated' and _gone(repo, item)
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
