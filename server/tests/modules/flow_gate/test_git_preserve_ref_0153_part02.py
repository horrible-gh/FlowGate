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

def test_real_ref_lock_makes_update_ref_fail_without_push(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    gitdir = Path(git(['rev-parse', '--absolute-git-dir'], r.work))
    lock = gitdir / 'refs' / 'flowgate' / 'preserve' / f'{mid}.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('', encoding='utf-8')
    out = publish(env, mid)
    assert out['result'] == PRESERVE_FAILED
    after = assert_parked(env, mid, m, reason='ref_write_failed', stage='create')
    assert after['last_error']['detail'] and len(after['last_error']['detail']) <= 300
    audit = [e for e in after['ref_operations'] if e['operation'] == 'update-ref']
    assert len(audit) == 1 and audit[0]['returncode'] not in (0, None)
    assert 'push' not in names(env) and '<secret-load>' not in names(env)
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') is None
    assert git(['rev-parse', 'main'], r.work) == m
    assert git(['rev-parse', 'main'], r.bare) == r.B
@pytest.mark.parametrize('case,reason,stage', [
    ('base_head', 'parent_mismatch', 'verify'),
    ('merge_head', 'parent_mismatch', 'verify'),
    ('remote', 'remote_not_ancestor', 'verify'),
    ('object', 'commit_missing', 'verify'),
    ('missing', 'merge_commit_missing', 'verify'),
    ('read', 'ref_read_failed', 'create'),
])
def test_validation_failures_stop_before_any_remote_contact(env, case, reason, stage):
    r = env.repo
    m = merge_commit(r)
    ctx = approved(r, m)
    sha = m
    if case == 'base_head':
        ctx['base_head'] = r.A
    elif case == 'merge_head':
        ctx['merge_head'] = r.A
    elif case == 'remote':
        ctx['expected_remote_head'] = r.F
    elif case == 'object':
        sha = ctx['merge_commit'] = 'e' * 40
    elif case == 'missing':
        sha = ctx['merge_commit'] = None
    elif case == 'read':
        env.hooks.append(_read_error)
    mid = open_session(env, ctx)
    out = publish(env, mid)
    assert out['result'] == PRESERVE_FAILED
    after = assert_parked(env, mid, sha, reason=reason, stage=stage)
    if case == 'read':
        assert after['last_error']['detail'] == 'last line'
    for forbidden in ('push', '<origin-sync>', '<secret-load>', 'update-ref', 'reset'):
        assert forbidden not in names(env)
    assert preserve_refs(r.work) == {}
def test_helper_exception_is_preserve_failed_not_push_exception(env):
    r = env.repo
    mid, fp = approve_ready(env)

    def explode(args):
        if args[0] == 'rev-list' and any('commit' in c for c in env.commands):
            raise RuntimeError('helper blew up')
        return None
    env.hooks.append(explode)
    out = approve(env, mid, 'att-1', fp)
    assert out['result'] == PRESERVE_FAILED
    after = saved(mid)
    assert after['last_error']['code'] == 'preserve_failed'
    assert after['last_error']['reason'] == 'exception' and after['last_error']['stage'] == 'create'
    assert after['reconciliation_kind'] == 'preserve_failed'
    assert after['merge_commit'] == git(['rev-parse', 'HEAD'], r.work)
    assert 'push' not in names(env)
def test_reconcile_gate_recreates_a_deleted_ref_then_keeps_the_existing_branch(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    ref = f'refs/flowgate/preserve/{mid}'
    git(['update-ref', '-d', ref, m], r.work)
    env.remote(r.B)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    # 0153 T#3: an unchanged remote now gets the same preserved commit re-pushed.
    assert out['result']['status'] == 'merged'
    assert ref_value(r.work, ref) == m
    seq = names(env)
    assert seq.index('update-ref') < seq.index('push') and seq.count('reset') == 0
    assert len(env.probes) == 1
    after = saved(mid)
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED and after['merge_commit'] == m
    assert after['preserve_sha'] == m and 'preserve_history' not in after
    assert git(['rev-parse', 'HEAD'], r.work) == m == git(['rev-parse', 'main'], r.bare)
@pytest.mark.parametrize('damage', ['other_sha', 'commit_missing', 'no_merge_commit'])
def test_reconcile_gate_failure_never_queries_or_resets(env, damage):
    r = env.repo
    mid, m, before = unknown_session(env)
    ref = f'refs/flowgate/preserve/{mid}'
    ctx = saved(mid)
    expected, observed, reason = m, None, None
    if damage == 'other_sha':
        git(['update-ref', ref, r.B, m], r.work)
        observed, reason = r.B, 'ref_conflict'
    elif damage == 'commit_missing':
        # A session from before T#2 (no preserve_* yet) whose commit object is gone.
        for key in ('preserve_ref', 'preserve_sha', 'preserve_parents', 'preserved_at',
                    'preserve_retain_until'):
            ctx.pop(key, None)
        expected = ctx['merge_commit'] = 'e' * 40
        reason = 'commit_missing'
    else:
        for key in ('preserve_ref', 'preserve_sha'):
            ctx.pop(key, None)
        expected = ctx['merge_commit'] = None
        reason = 'merge_commit_missing'
    gs.db_git.set_session_context(mid, ctx)
    env.clock.update(now=LATER, instance='inst_B')
    env.remote(r.B)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result'] == PRESERVE_FAILED
    assert env.probes == []
    assert 'reset' not in names(env) and 'update-ref' not in names(env)
    after = saved(mid)
    e = after['last_error']
    assert (e['code'], e['stage'], e['reason']) == ('preserve_failed', 'reconcile_gate', reason)
    assert e['expected_sha'] == expected and e['observed_sha'] == observed
    assert e['at'] == LATER and e['instance_id'] == 'inst_B'
    # T#1 evidence survives verbatim.
    assert e['prior_push_error'] == before['last_error']
    assert e['prior_push_error']['instance_id'] == 'inst_A' and e['prior_push_error']['local_sha'] == m
    assert after['reconciliation_kind'] == 'preserve_failed' and after['reconcile_next_at'] is None
    assert after['reconcile_attempt_count'] == 1
    assert git(['rev-parse', 'HEAD'], r.work) == m
    # A repeated failure keeps carrying the same push evidence.
    out2 = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out2 is None  # preserve_failed is not an auto-retry kind
    assert saved(mid)['last_error'] == e
def test_preserve_failed_parked_while_waiting_for_the_lock_is_left_untouched(env):
    """§3.4 race: call B reads reconciling/push_remote_unknown before the lock; while it
    waits, call A parks the session at preserve_failed and releases the lock. B must
    re-check the kind on the fresh row and return before any context write,
    preservation call, remote query or reset — even though the cause of A's failure
    has meanwhile gone away and the remote is unchanged (a non-blocked B would reset)."""
    r = env.repo
    mid, m, before = unknown_session(env)
    ref = f'refs/flowgate/preserve/{mid}'
    git(['update-ref', ref, r.B, m], r.work)  # A will find another SHA in the ref
    env.remote(r.B)
    b_now = '2026-11-08T12:00:00+09:00'
    state = {'a_ran': False}
    parked = {}

    def lock(*a, **kw):
        if not state['a_ran']:
            state['a_ran'] = True  # A's own lock call below returns at once
            env.clock.update(now=LATER, instance='inst_A2')
            out_a = gs.reconcile_push_session(mid, trigger='server_startup')
            assert out_a['result'] == PRESERVE_FAILED
            parked.update(saved(mid))
            git(['update-ref', ref, m, r.B], r.work)  # the failure cause was transient
            env.commands.clear()
            env.probes.clear()
            env.clock.update(now=b_now, instance='inst_B')
        return (None, None)
    env.monkeypatch.setattr(branch_merge_publish, 'session_lock', lock)

    out = gs.reconcile_push_session(mid, trigger='server_startup')  # call B
    assert state['a_ran']
    assert out == {'ok': True, 'result': {'status': 'reconciling', 'review_state': 'reconciling',
                                          'reconciliation_kind': 'preserve_failed'}}
    assert env.probes == [] and env.commands == []  # no Git and no remote query at all
    after = saved(mid)
    assert after == parked  # no context write by B
    e = after['last_error']
    assert (e['code'], e['stage'], e['reason']) == ('preserve_failed', 'reconcile_gate', 'ref_conflict')
    assert e['at'] == LATER and e['instance_id'] == 'inst_A2' and e['observed_sha'] == r.B
    assert e['prior_push_error'] == before['last_error']
    assert after['reconciliation_kind'] == 'preserve_failed' and after['reconcile_next_at'] is None
    assert after['reconcile_attempt_count'] == 1  # only A counted
    assert after['preserve_ref'] == ref and after['preserve_sha'] == m
    assert after['preserved_at'] == before['preserved_at']
    assert after['preserve_retain_until'] == before['preserve_retain_until']
    assert after['merge_commit'] == m and after['approval_attempt_id'] == 'attempt-1'
    assert git(['rev-parse', 'HEAD'], r.work) == m
    assert git(['rev-parse', 'main'], r.bare) == r.B
    assert ref_value(r.work, ref) == m
