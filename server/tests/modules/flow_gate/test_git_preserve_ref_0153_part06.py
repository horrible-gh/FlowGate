"""Independently selectable scenarios; shared fixtures remain in test_git_preserve_ref_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_preserve_ref_0153 import (
    AT,
    IDENTITY,
    INTENT,
    LATER,
    NEXT_AT,
    PRESERVE_FAILED,
    PROJECT,
    Path,
    RETAIN,
    SimpleNamespace,
    _Crash,
    _exists,
    _expire_and_gc,
    _only,
    _read_error,
    _remote_breaks_json,
    approval_intent,
    approve,
    approve_ready,
    approved,
    assert_parked,
    assert_parked_approval_response,
    branch_merge,
    branch_merge_publish,
    done,
    env,
    git,
    git_rc,
    groups,
    gs,
    merge_commit,
    names,
    on,
    open_session,
    parents_of,
    patch_store,
    preserve,
    preserve_refs,
    project,
    projects,
    publish,
    push_rejected,
    push_timeout,
    pytest,
    pytestmark,
    ref_value,
    reflog,
    repo,
    saved,
    shutil,
    subprocess,
    tmp_db,
    unknown_session,
    uuid,
)

def test_ref_made_in_a_linked_worktree_is_shared_with_the_base_repository(env):
    r = env.repo
    wt = r.tmp / 'linked'
    git(['worktree', 'add', '--detach', str(wt), r.B], r.work)
    git(['merge', '--no-ff', '--no-commit', 'feature'], wt)
    git(['commit', '-m', 'merge in linked worktree'], wt)
    m2 = git(['rev-parse', 'HEAD'], wt)
    mid = open_session(env, approved(r, m2))
    result = preserve.ensure_preserved(wt, mid, saved(mid))
    assert result['ok'] and result['result'] == 'created'
    assert preserve_refs(r.work) == {f'refs/flowgate/preserve/{mid}': m2}
    git(['worktree', 'remove', '--force', str(wt)], r.work)
    git(['branch', '-D', 'feature'], r.work)
    _expire_and_gc(r.work)
    assert _exists(r.work, m2) and _exists(r.work, r.F)
def test_merge_only_and_push_false_create_no_ref(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m), action='merge_only')
    out = publish(env, mid)
    assert out['result']['pushed'] is False
    gs.db_git.close_session(mid, 'done')  # one open session per group
    mid2 = open_session(env, approved(r, m))
    out = publish(env, mid2, branch=True, push=False)
    assert out['result']['pushed'] is False
    assert env.completed == [False, False]
    assert preserve_refs(r.work) == {}
    assert 'update-ref' not in names(env) and 'push' not in names(env)
def test_unknown_contract_is_t1_shape_plus_preserve_fields(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    e = before['last_error']
    assert set(e) == {'code', 'returncode', 'cause', 'detail', 'local_sha', 'expected_remote_head',
                      'at', 'instance_id'}
    assert e == dict(code='push_remote_unknown', returncode=-1, cause='timeout', detail='timeout_expired',
                     local_sha=m, expected_remote_head=r.B, at=AT, instance_id='inst_A')
    assert before['reconciliation_kind'] == 'push_remote_unknown'
    assert before['reconcile_attempt_count'] == 0 and before['reconcile_next_at'] == NEXT_AT
    assert before['merge_commit'] == m and before['apply_phase'] == 'committed_local'
def test_push_exception_contract_is_unchanged_after_preservation(env):
    r = env.repo
    mid, fp = approve_ready(env)

    def raise_on_push(args):
        if args[0] == 'push':
            raise RuntimeError('unexpected push exception')
        return None
    env.hooks.append(raise_on_push)
    out = approve(env, mid, 'att-1', fp)
    assert_parked_approval_response(out['result'], mid, 'push_remote_unknown')
    after = saved(mid)
    assert after['last_error'] == {'code': 'push_exception', 'detail': 'unexpected_error'}
    assert after['reconcile_attempt_count'] == 0 and after['reconcile_next_at'] == NEXT_AT
    m = git(['rev-parse', 'HEAD'], r.work)
    assert after['merge_commit'] == m and after['preserve_sha'] == m
@pytest.mark.parametrize('observed,status', [
    ('merge', 'merged'), ('expected', 'merged'),
    ('third', 'retry_scheduled'), (None, 'retry_scheduled'),
])
def test_reconcile_four_branches_are_unchanged_behind_the_gate(env, observed, status):
    # expected → the same commit is re-pushed and completed; a fake third SHA
    # that the real fetch does not confirm is "the remote is moving" (transient).
    r = env.repo
    mid, m, before = unknown_session(env)
    value = {'merge': m, 'expected': r.B, 'third': 'd' * 40, None: None}[observed]
    env.remote(value)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == status
    after = saved(mid)
    assert after['reconcile_attempt_count'] == 1
    assert len(env.probes) == (3 if observed is None else 1)
    assert names(env).count('reset') == 0
    assert names(env).count('push') == (1 if observed == 'expected' else 0)
    assert 'update-ref' not in names(env)  # gate reused the ref (`same`)
    assert after['last_error'] == before['last_error']
    if observed == 'expected':
        assert after['merge_commit'] == m and after['review_state'] == gs.REVIEW_STATE_COMPLETED
        assert 'reconcile_next_at' not in after and 'reconciliation_kind' not in after
        assert git(['rev-parse', 'main'], r.bare) == m and env.completed == [True]
    elif observed == 'third':
        assert after['reconciliation_kind'] == 'push_remote_unknown'
        assert after['reconcile_last_outcome'] == 'remote_moving'
        assert after['merge_commit'] == m and after['reconcile_next_at'] == NEXT_AT
    elif observed is None:
        assert after['merge_commit'] == m and after['reconcile_next_at'] == NEXT_AT
    else:
        assert env.completed == [True]
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
def test_ref_names_and_rounds():
    assert preserve.preserve_ref_name(12) == 'refs/flowgate/preserve/12'
    assert preserve.preserve_ref_name(12, 3) == 'refs/flowgate/preserve/12-r3'
    assert preserve.parse_preserve_ref('refs/flowgate/preserve/12-r3') == (12, 3)
    assert preserve.parse_preserve_ref('refs/flowgate/preserve/x') is None
    for bad in (0, -1, '12', True, 1.5):
        with pytest.raises(ValueError):
            preserve.preserve_ref_name(bad)
    with pytest.raises(ValueError):
        preserve.preserve_ref_name(1, -1)
    for name in (preserve.preserve_ref_name(7), preserve.preserve_ref_name(7, 2)):
        assert subprocess.run(['git', 'check-ref-format', name]).returncode == 0
