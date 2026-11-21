"""Independently selectable scenarios; shared fixtures remain in test_git_reconcile_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_reconcile_0153 import (
    AT,
    BASE,
    F,
    FETCHED,
    I,
    IDENTITY,
    INTENT,
    LATER,
    LEASE,
    LEASE_ARG_RE,
    M,
    MARKER_BLOCK,
    NEXT_AT,
    P,
    PROJECT,
    PUSH_CLASSES,
    Path,
    REAL_COMPLETE,
    REAL_SCENARIOS,
    RECONCILE_SOURCES,
    SHA_RE,
    TRANSITIONS,
    X,
    _network_failure,
    _only,
    _park,
    _reject,
    approval_intent,
    approved,
    assert_consistent,
    assert_lease_only,
    common,
    conflict_session,
    count,
    diverge_local,
    env,
    git,
    git_rc,
    gs,
    inspect,
    merge_commit,
    names,
    on,
    open_session,
    origin_main,
    other_clone,
    parents_of,
    patch_store,
    preserve,
    preserve_refs,
    project,
    publish,
    push_timeout,
    pushes,
    pytest,
    pytestmark,
    re,
    reasons,
    reconcile,
    ref_value,
    reflog,
    remote_commit,
    repo,
    rp,
    saved,
    subprocess,
    tmp_db,
    unknown_session,
    unmerged,
    user_intervention,
)

def test_A1_branch_unchanged_remote_repushes_the_same_commit(env):
    r = env.repo
    m = merge_commit(r)
    ctx = approved(r, m, review_state=gs.REVIEW_STATE_RECONCILING,
                   reconciliation_kind='push_remote_unknown', branch_merge={'push': True})
    mid = gs.db_git.create_branch_merge_session(PROJECT, finalize_action='merge', context=ctx)
    out = reconcile(mid)
    assert out['result']['status'] == 'merged' and env.completed == [True]
    assert origin_main(env) == m
    assert pushes(env) == [['push', f'--force-with-lease=refs/heads/main:{r.B}', 'origin',
                            f'{m}:refs/heads/main']]
    assert count(env, 'reset') == 0
    assert_consistent(mid, r.work, r.bare)
def test_A2_repush_timeout_keeps_everything_and_records_the_stage(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    env.clock.update(now=LATER, instance='inst_B')
    env.hooks.append(on('push', push_timeout))
    out = reconcile(mid)
    assert out == {'ok': True, 'result': {'status': 'retry_scheduled'}}
    after = assert_consistent(mid, r.work)
    e = after['last_error']
    assert e == dict(code='push_remote_unknown', returncode=-1, cause='timeout', detail='timeout_expired',
                     local_sha=m, expected_remote_head=r.B, at=LATER, instance_id='inst_B',
                     stage='reconcile_repush')
    assert git(['rev-parse', 'HEAD'], r.work) == m and count(env, 'reset') == 0
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert after['reconcile_last_outcome'] == 'push_result_unknown'
    assert_lease_only(env)
def test_A3_lease_rejection_is_transient_without_reset_or_refreeze(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    env.hooks.append(on('push', lambda a: subprocess.CompletedProcess(
        ['git', *a], 1, '', ' ! [rejected]        main -> main (stale info)')))
    out = reconcile(mid)
    assert out == {'ok': True, 'result': {'status': 'retry_scheduled'}}
    assert count(env, 'reset') == 0 and count(env, 'fetch') == 0
    after = assert_consistent(mid, r.work)
    assert after['reconcile_last_outcome'] == 'push_lease_rejected'
    assert after['last_error'] == before['last_error'] and after['review_state'] == 'reconciling'
def test_A4_hook_permission_rejection_is_auth_not_the_rejected_branch(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    env.hooks.append(on('push', lambda a: subprocess.CompletedProcess(
        ['git', *a], 1, '', 'remote: GitLab: You are not allowed to push code to protected branches\n'
                            ' ! [remote rejected] main -> main (pre-receive hook declined)')))
    out = reconcile(mid)
    assert out['result'] == user_intervention('reconcile_auth_required', 'reconcile_auth_rejected')
    after = assert_consistent(mid, r.work)
    assert (after['last_error']['cause'], after['last_error']['stage']) == ('permission', 'push')
    assert count(env, 'reset') == 0 and count(env, 'fetch') == 0
    assert after['last_error']['prior_push_error'] == before['last_error']
def test_B1_remote_already_has_the_merge_commit(env, common):
    r = env.repo
    mid, m, before = unknown_session(env)
    git(['push', '-q', 'origin', f'{m}:refs/heads/main'], r.work)
    env.commands.clear()
    out = reconcile(mid)
    assert out['result']['status'] == 'merged'
    assert pushes(env) == [] and count(env, 'commit') == 0 and count(env, 'commit-tree') == 0
    assert common['approvals'] == [(env.group, mid, INTENT)]
    after = assert_consistent(mid, r.work, r.bare)
    assert after['published_commit'] == m
