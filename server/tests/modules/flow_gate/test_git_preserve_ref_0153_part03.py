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

def test_pre_t2_session_without_ref_is_preserved_by_the_gate(env):
    r = env.repo
    m = merge_commit(r)
    ctx = approved(r, m, review_state=gs.REVIEW_STATE_RECONCILING,
                   reconciliation_kind='push_remote_unknown', reconcile_attempt_count=0)
    mid = open_session(env, ctx)
    env.remote(None)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'retry_scheduled'
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert saved(mid)['preserve_sha'] == m and len(env.probes) == 3
@pytest.mark.parametrize('branch', [False, True], ids=['group', 'branch'])
def test_rejected_reset_is_blocked_when_the_ref_was_damaged(env, branch):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    ref = f'refs/flowgate/preserve/{mid}'

    def damage_then_reject(args):
        git(['update-ref', ref, r.B, m], r.work)
        return push_rejected(args)
    env.hooks.append(on('push', damage_then_reject))
    out = publish(env, mid, branch=branch)
    if not branch:
        assert out['result']['reconciliation_kind'] == 'push_remote_unknown'
        assert git(['rev-parse', 'HEAD'], r.work) == m
        assert 'reset' not in names(env) and 'fetch' not in names(env)
        out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result'] == PRESERVE_FAILED
    assert 'reset' not in names(env) and 'fetch' not in names(env)
    assert_parked(env, mid, m, reason='ref_conflict',
                  stage='reset_gate' if branch else 'reconcile_gate', observed=r.B)
    assert git(['rev-parse', 'HEAD'], r.work) == m
@pytest.mark.parametrize('branch', [False, True], ids=['group', 'branch'])
def test_rejected_reset_keeps_existing_flow_and_the_ref(env, branch):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    env.hooks.append(on('push', push_rejected))
    out = publish(env, mid, branch=branch)
    if not branch:
        assert out['result']['status'] == 'reconciling'
        after = saved(mid)
        assert after['merge_commit'] == m and after['preserve_round'] == 0
        assert after['preserve_ref'] == f'refs/flowgate/preserve/{mid}'
        assert ref_value(r.work, after['preserve_ref']) == m
        assert parents_of(r.work, m) == [r.B, r.F]
        assert git(['rev-parse', 'HEAD'], r.work) == m
        assert 'reset' not in names(env) and 'fetch' not in names(env)
        assert after['first_push_error']['returncode'] == 1
        assert after['last_error']['code'] == 'push_remote_unknown'
        return
    assert out['result']['status'] == 're_review'
    seq = names(env)
    assert seq.index('update-ref') < seq.index('push') < seq.index('reset') < seq.index('fetch')
    after = saved(mid)
    assert after['last_error'] == {'code': 'push_rejected'}
    assert after['merge_commit'] is None and after['review_state'] == gs.REVIEW_STATE_RE_REVIEW
    ref = f'refs/flowgate/preserve/{mid}'
    assert ref_value(r.work, ref) == m
    assert after['preserve_round'] == 1 and after['preserve_ref'] is None and after['preserve_sha'] is None
    assert after['preserve_history'] == [{
        'ref': ref, 'sha': m, 'round': 0, 'preserved_at': AT, 'retain_until': RETAIN,
        'closed_at': AT, 'closed_reason': 're_review'}]
def test_remote_unchanged_reset_gate(env):
    # 0153 T#3: the remote-unchanged reset is gone; the reset gate now guards the
    # hand-off reset (preserve.verify_round right before it).
    r = env.repo
    mid, m, before = unknown_session(env)
    ref = f'refs/flowgate/preserve/{mid}'
    c = _remote_breaks_json(env)

    def damage_before_the_handoff(args):
        if args and args[0] == 'merge-tree':
            git(['update-ref', ref, r.B, m], r.work)
        return None
    env.hooks.append(damage_before_the_handoff)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result'] == PRESERVE_FAILED
    assert 'reset' not in names(env)
    after = saved(mid)
    assert after['last_error']['stage'] == 'reset_gate' and after['last_error']['reason'] == 'ref_conflict'
    assert after['last_error']['prior_push_error'] == before['last_error']
    assert after['merge_commit'] == m and git(['rev-parse', 'HEAD'], r.work) == m
    assert git(['rev-parse', 'main'], r.bare) == c
def test_remote_unchanged_reset_keeps_the_ref_and_history(env):
    # 0153 T#3: the hand-off reset (the only reconcile reset) keeps the ref and closes
    # the round into the history.
    r = env.repo
    mid, m, before = unknown_session(env)
    _remote_breaks_json(env)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 're_review'
    after = saved(mid)
    assert names(env).count('reset') == 1 and 'update-ref' not in names(env)
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert [h['sha'] for h in after['preserve_history']] == [m] and after['preserve_round'] == 1
