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

def test_G_damaged_preservation_ref_never_reaches_the_remote(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    git(['update-ref', f'refs/flowgate/preserve/{mid}', r.B, m], r.work)
    out = reconcile(mid)
    assert out['result']['reconciliation_kind'] == 'preserve_failed'
    assert count(env, 'ls-remote') == 0 and pushes(env) == []
    assert saved(mid)['last_error']['stage'] == 'reconcile_gate'
    assert_consistent(mid, r.work)
def test_L_spent_cycle_budget_parks_without_remote_contact(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    ctx = saved(mid)
    ctx['reconcile_cycle_count'] = 5
    gs.db_git.set_session_context(mid, ctx)
    out = reconcile(mid)
    assert out['result'] == user_intervention('reconcile_retry_exhausted', 'reconcile_retry_exhausted')
    assert count(env, 'ls-remote') == 0 and pushes(env) == [] and count(env, 'reset') == 0
    assert count(env, 'fetch') == 0
    after = assert_consistent(mid, r.work)
    e = after['last_error']
    assert {k: e[k] for k in ('cycles', 'integrations', 'last_outcome')} == {
        'cycles': 5, 'integrations': 0, 'last_outcome': 'cycle_budget_spent'}
    assert e['prior_push_error'] == before['last_error']
    assert after['merge_commit'] == m and after['publish_commit'] == m
    assert approval_intent.intent_of_context(after) == INTENT
    assert git(['rev-parse', 'HEAD'], r.work) == m and origin_main(env) == r.B
def test_Q_query_failure_keeps_the_retry_with_backoff(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    seconds = []
    env.monkeypatch.setattr(gs, '_seconds_from_now_iso', lambda s: seconds.append(s) or f'due+{s}')
    env.hooks.append(on('ls-remote', _network_failure))
    out = reconcile(mid)
    assert out == {'ok': True, 'result': {'status': 'retry_scheduled'}}
    assert count(env, 'ls-remote') == 3 and pushes(env) == []
    after = assert_consistent(mid, r.work)
    assert after['reconciliation_kind'] == 'push_remote_unknown'
    assert after['reconcile_next_at'] == 'due+60' and seconds == [60]
    assert after['reconcile_cycle_count'] == 1 and after['reconcile_last_outcome'] == 'ls_remote_failed'
    assert after['last_error'] == before['last_error']
    # The next cycles back off 120, 240, 480 seconds.
    for expected in (120, 240, 480):
        reconcile(mid)
        assert saved(mid)['reconcile_next_at'] == f'due+{expected}'
def test_Q5_query_failure_in_the_fifth_cycle_is_exhausted(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    ctx = saved(mid)
    ctx['reconcile_cycle_count'] = 4
    gs.db_git.set_session_context(mid, ctx)
    env.hooks.append(on('ls-remote', _network_failure))
    out = reconcile(mid)
    assert out['result'] == user_intervention('reconcile_retry_exhausted', 'reconcile_retry_exhausted')
    after = assert_consistent(mid, r.work)
    assert after['reconcile_next_at'] is None
    assert after['last_error']['cycles'] == 5 and after['last_error']['last_outcome'] == 'ls_remote_failed'
    assert gs.reconcile_push_session(mid, trigger='server_startup') is None  # not auto-retried
def test_E1_ls_remote_auth_failure_stops_after_one_probe(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    env.hooks.append(on('ls-remote', lambda a: subprocess.CompletedProcess(
        ['git', *a], 128, '', 'remote: denied\nfatal: Authentication failed for origin')))
    out = reconcile(mid)
    assert out['result'] == user_intervention('reconcile_auth_required', 'reconcile_auth_rejected')
    assert count(env, 'ls-remote') == 1 and pushes(env) == [] and count(env, 'fetch') == 0
    after = assert_consistent(mid, r.work)
    e = after['last_error']
    assert {k: e[k] for k in ('code', 'cause', 'stage', 'returncode', 'detail')} == {
        'code': 'reconcile_auth_rejected', 'cause': 'auth', 'stage': 'ls_remote', 'returncode': 128,
        'detail': 'fatal: Authentication failed for origin'}
    assert e['prior_push_error'] == before['last_error']
    assert (e['at'], e['instance_id']) == (AT, 'inst_A')
def test_A1_group_unchanged_remote_repushes_the_same_commit(env, common):
    r = env.repo
    mid, m, before = unknown_session(env)
    out = reconcile(mid)
    assert out['result']['status'] == 'merged' and out['result']['approval']['approved'] is True
    assert origin_main(env) == m
    assert pushes(env) == [['push', f'--force-with-lease=refs/heads/main:{r.B}', 'origin',
                            f'{m}:refs/heads/main']]
    assert_lease_only(env)
    assert count(env, 'reset') == 0 and count(env, 'fetch') == 0
    assert common['approvals'] == [(env.group, mid, INTENT)] and len(common['finished']) == 1
    after = assert_consistent(mid, r.work, r.bare)
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED and after['published_commit'] == m
    assert reasons(mid)[-1] == 'reconcile_repush'
    assert after['ref_operations'][-1]['instance_id'] == 'inst_A'
