"""0153 T#3: the non-destructive reconcile state machine.

``TRANSITIONS`` is the decision table of T0009 §5.1 as module data. The pure
``reconcile_plan.decide`` is asserted row by row without Git; the rows marked ``real``
are also driven end to end against a real local-path bare ``origin`` (no file://
capability gate). Remote states are made by pushing real commits to that origin —
``ls-remote`` is never faked; only the probe delays are patched to zero and, in the
named fault rows, one command's result is replaced.

The sqlite/session/Git fixtures are the ones of ``test_git_preserve_ref_0153.py``.
"""
from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

import pytest

from tests.modules.flow_gate.test_git_integration_0115 import patch_store, tmp_db  # noqa: F401
from tests.modules.flow_gate.test_git_preserve_ref_0153 import (  # noqa: F401
    AT, IDENTITY, INTENT, LATER, NEXT_AT, PROJECT, _only, approved, env, git, git_rc,
    merge_commit, names, on, open_session, parents_of, preserve_refs, project, publish,
    push_timeout, ref_value, reflog, repo, saved, unknown_session,
)
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import approval_intent, preserve
from modules.flow_gate.services.git import reconcile_plan as rp

pytestmark = pytest.mark.skipif(__import__('shutil').which('git') is None, reason='git binary unavailable')

REAL_COMPLETE = gs._complete_merge_review
SHA_RE = re.compile(r'^[0-9a-f]{40}$')
LEASE_ARG_RE = re.compile(r'^--force-with-lease=refs/heads/main:[0-9a-f]{40}$')

# Test-fixture SHAs for the pure table (not operational values).
M, LEASE, F, I, X = ('1' * 40, '2' * 40, '3' * 40, '4' * 40, '5' * 40)


def P(sha, cause=None):
    return rp.Probe(sha, cause)


BASE = dict(cycle_count=1, merge_commit=M, publish_commit=M, publish_lease=LEASE,
            local=rp.LOCAL_OK)
FETCHED = dict(BASE, probe=P(F), fetch=P(F))

# ── the decision table (T0009 §5.1) ──────────────────────────────────────────
# id, inputs (None: decided before decide(), i.e. by the T#2 gate), expected action,
# expected reason/stage/cause, real-Git scenario.
TRANSITIONS = [
    dict(id='G', inputs=None, action='preserve_failed', real=True),
    dict(id='L', inputs=dict(BASE, cycle_count=5, local=None), action=rp.EXHAUSTED,
         reason=rp.OUT_CYCLE_BUDGET, real=True),
    dict(id='Q', inputs=dict(BASE, probe=P(None, 'network')), action=rp.TRANSIENT,
         reason=rp.OUT_LS_REMOTE_FAILED, real=True),
    dict(id='Q5', inputs=dict(BASE, cycle_count=4, probe=P(None, 'timeout')), action=rp.EXHAUSTED,
         reason=rp.OUT_LS_REMOTE_FAILED, real=True),
    dict(id='E1', inputs=dict(BASE, probe=P(None, 'auth')), action=rp.AUTH, stage='ls_remote',
         cause='auth', real=True),
    dict(id='E1p', inputs=dict(BASE, probe=P(None, 'permission')), action=rp.AUTH,
         stage='ls_remote', cause='permission', real=False),
    dict(id='A1', inputs=dict(BASE, probe=P(LEASE)), action=rp.REPUSH, real=True),
    dict(id='A2', inputs=dict(BASE, probe=P(LEASE)), action=rp.REPUSH, real=True),
    dict(id='A3', inputs=dict(BASE, probe=P(LEASE)), action=rp.REPUSH, real=True),
    dict(id='A4', inputs=dict(BASE, probe=P(LEASE)), action=rp.REPUSH, real=True),
    dict(id='B1', inputs=dict(BASE, probe=P(M)), action=rp.COMPLETE, real=True),
    dict(id='B1i', inputs=dict(BASE, publish_commit=I, probe=P(I)), action=rp.COMPLETE, real=False),
    dict(id='B2', inputs=dict(BASE, probe=P(X), fetch=P(X), merge_in_remote=rp.YES),
         action=rp.COMPLETE, real=True),
    dict(id='C1', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.YES),
         action=rp.INTEGRATE, real=True),
    dict(id='C2', inputs=dict(BASE, publish_commit=I, publish_lease=F, integrations=1, probe=P(F)),
         action=rp.REPUSH, real=True),
    dict(id='C3', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.YES, integrations=2),
         action=rp.EXHAUSTED, reason=rp.OUT_INTEGRATION_LIMIT, real=False),
    dict(id='C4', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.YES),
         action=rp.INTEGRATE, real=True),
    dict(id='C5', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.YES),
         action=rp.INTEGRATE, real=True),
    dict(id='D1', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.YES),
         action=rp.INTEGRATE, real=True),
    dict(id='R', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.NO),
         action=rp.REWRITTEN, reason='remote_rewritten', real=True),
    dict(id='X3', inputs=dict(BASE, local=rp.LOCAL_MISMATCH), action=rp.LOCAL_STATE_MISMATCH, real=True),
    dict(id='restore', inputs=dict(BASE, local=rp.LOCAL_RESTORABLE), action=rp.RESTORE_LOCAL, real=False),
    dict(id='moving', inputs=dict(BASE, probe=P(F), fetch=P(X)), action=rp.TRANSIENT,
         reason=rp.OUT_REMOTE_MOVING, real=False),
    dict(id='moving5', inputs=dict(BASE, cycle_count=4, probe=P(F), fetch=P(X)), action=rp.EXHAUSTED,
         reason=rp.OUT_REMOTE_MOVING, real=False),
    dict(id='fetch_auth', inputs=dict(BASE, probe=P(F), fetch=P(None, 'auth')), action=rp.AUTH,
         stage='fetch', cause='auth', real=False),
    dict(id='fetch_fail', inputs=dict(BASE, probe=P(F), fetch=P(None, 'network')), action=rp.TRANSIENT,
         reason=rp.OUT_FETCH_FAILED, real=False),
    dict(id='ancestry_err', inputs=dict(FETCHED, merge_in_remote=rp.ERROR), action=rp.TRANSIENT,
         reason=rp.OUT_ANCESTRY_FAILED, real=False),
    dict(id='need_local', inputs=dict(BASE, local=None), action=rp.NEED_LOCAL, real=False),
    dict(id='need_probe', inputs=dict(BASE), action=rp.NEED_PROBE, real=False),
    dict(id='need_fetch', inputs=dict(BASE, probe=P(F)), action=rp.NEED_FETCH, real=False),
    dict(id='need_merge', inputs=dict(FETCHED), action=rp.NEED_MERGE_ANCESTRY, real=False),
    dict(id='need_lease', inputs=dict(FETCHED, merge_in_remote=rp.NO), action=rp.NEED_LEASE_ANCESTRY,
         real=False),
    dict(id='P', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.YES),
         action=rp.INTEGRATE, real=True),
    dict(id='X1', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.YES),
         action=rp.INTEGRATE, real=True),
    dict(id='X2', inputs=dict(FETCHED, merge_in_remote=rp.NO, lease_in_remote=rp.YES),
         action=rp.INTEGRATE, real=True),
]

# §3.4 push classification (order matters: a hook rejection also says "rejected").
PUSH_CLASSES = [
    (0, None, '', rp.PUSH_SUCCESS),
    (128, 'auth', 'fatal: Authentication failed for host', rp.PUSH_AUTH),
    (1, 'unknown', 'ERROR: Permission to org/repo.git denied to user.', rp.PUSH_PERMISSION),
    (1, 'unknown', 'remote: HTTP 403', rp.PUSH_PERMISSION),
    (1, 'unknown', ' ! [remote rejected] main -> main (pre-receive hook declined)', rp.PUSH_PERMISSION),
    (1, 'unknown', 'remote: You are not allowed to push code to protected branches', rp.PUSH_PERMISSION),
    (1, 'unknown', ' ! [rejected] main -> main (stale info)', rp.PUSH_LEASE_REJECTED),
    (1, 'unknown', ' ! [rejected] main -> main (fetch first)', rp.PUSH_LEASE_REJECTED),
    (1, 'unknown', 'Updates were rejected because of a non-fast-forward', rp.PUSH_LEASE_REJECTED),
    (-1, 'timeout', 'timeout_expired', rp.PUSH_UNKNOWN),
    (128, 'network', 'Could not resolve host: example.invalid', rp.PUSH_UNKNOWN),
    (128, 'unknown', '', rp.PUSH_UNKNOWN),
]


@pytest.mark.parametrize('row', [r for r in TRANSITIONS if r['inputs'] is not None],
                         ids=lambda r: r['id'])
def test_transition_table(row):
    decision = rp.decide(rp.Inputs(**row['inputs']))
    assert decision.action == row['action']
    for key in ('reason', 'stage', 'cause'):
        if key in row:
            assert getattr(decision, key) == row[key]


@pytest.mark.parametrize('rc,cause,stderr,expected', PUSH_CLASSES)
def test_push_classification(rc, cause, stderr, expected):
    assert rp.classify_push(rc, cause, stderr) == expected


def test_kind_sets_are_disjoint_and_complete():
    assert set(gs.RECONCILE_USER_INTERVENTION_KINDS) == {
        'preserve_failed', 'push_remote_third', 'reconcile_auth_required',
        'reconcile_retry_exhausted', 'reconcile_reset_failed', 'reconcile_local_state_mismatch'}
    assert not set(gs.RECONCILE_USER_INTERVENTION_KINDS) & set(gs.RECONCILE_AUTO_RETRY_KINDS)
    assert gs.RECONCILE_AUTO_RETRY_KINDS == ('push_remote_unknown', 'post_push_cleanup')


def test_constants_and_backoff_sequence():
    assert (gs.RECONCILE_MAX_CYCLES, gs.RECONCILE_MAX_INTEGRATIONS, gs.RECONCILE_BACKOFF_MAX_SEC) == (5, 2, 900)
    assert [gs._reconcile_backoff_seconds(c) for c in range(1, 7)] == [60, 120, 240, 480, 900, 900]
    assert [rp.backoff_seconds(c) for c in range(1, 6)] == [60, 120, 240, 480, 900]


def test_every_real_row_has_a_real_scenario():
    real = {r['id'] for r in TRANSITIONS if r['real']}
    assert real == {'G', 'L', 'Q', 'Q5', 'E1', 'A1', 'A2', 'A3', 'A4', 'B1', 'B2', 'C1', 'C2', 'C4',
                    'C5', 'D1', 'R', 'X3', 'P', 'X1', 'X2'}
    assert real <= set(REAL_SCENARIOS)


# ── real Git helpers ─────────────────────────────────────────────────────────

def other_clone(env, name='other'):
    r = env.repo
    path = r.tmp / name
    git(['clone', '-q', str(r.bare), str(path)], r.tmp)
    git(['config', 'user.name', 'other'], path)
    git(['config', 'user.email', 'other@local'], path)
    return path


def remote_commit(env, path, rel, content, *, name='other'):
    """Another group's commit pushed to origin on top of its current main."""
    other = other_clone(env, name)
    (other / rel).write_text(content, encoding='utf-8')
    git(['add', '.'], other)
    git(['commit', '-q', '-m', f'remote change of {rel}'], other)
    git(['push', '-q', 'origin', 'main'], other)
    return git(['rev-parse', 'HEAD'], other)


def origin_main(env):
    return git(['rev-parse', 'main'], env.repo.bare)


def count(env, name):
    return names(env).count(name)


def pushes(env):
    return [c for c in env.commands if c and c[0] == 'push']


def assert_lease_only(env):
    """§5.2 dynamic: every recorded push carries exactly one exact-SHA lease and no force."""
    for cmd in pushes(env):
        assert sum(1 for a in cmd if LEASE_ARG_RE.match(a)) == 1, cmd
        assert not any(a in ('-f', '--force') or a.startswith('+') for a in cmd), cmd
        assert not any(':' in a and a.split(':', 1)[0].startswith('+') for a in cmd), cmd


def reasons(mid):
    return [e['reason'] for e in saved(mid).get('ref_operations') or []]


def reconcile(mid):
    return gs.reconcile_push_session(mid, trigger='server_startup')


def unmerged(root):
    """The real unmerged path strings (NUL-separated, never Git's C-quoted line form)."""
    return sorted(p for p in git(['diff', '--name-only', '-z', '--diff-filter=U'], root).split('\0') if p)


def assert_consistent(mid, root, bare=None):
    """T0009 §4 invariants: the re-read sqlite context against the real checkout/refs."""
    ctx = saved(mid)
    state, kind = ctx.get('review_state'), ctx.get('reconciliation_kind')
    head, mh = ref_value(root, 'HEAD'), ref_value(root, 'MERGE_HEAD')
    refs = preserve_refs(root)
    lost = unmerged(root)
    assert state != gs.REVIEW_STATE_PENDING
    integrations = preserve.round_integrations(mid, ctx)
    if state == gs.REVIEW_STATE_RECONCILING:
        assert SHA_RE.match(ctx.get('merge_commit') or '')
        if kind in gs.RECONCILE_USER_INTERVENTION_KINDS:
            assert ctx.get('reconcile_next_at') is None
        if kind == 'reconcile_reset_failed':
            assert head == ctx['last_error']['observed_head']
            assert refs.get(ctx['preserve_ref']) == ctx['merge_commit']
            return ctx
        assert mh is None and lost == []
        if kind == 'preserve_failed':
            return ctx
        if kind == 'reconcile_local_state_mismatch':
            assert head == ctx['last_error']['observed_head']
            return ctx
        assert ctx['publish_commit'] in [ctx['merge_commit'], *[e['sha'] for e in integrations]]
        assert refs.get(ctx['preserve_ref']) == ctx['merge_commit']
        for entry in integrations:
            assert refs.get(entry['ref']) == entry['sha']
        assert ctx['apply_phase'] in ('committed_local', 'integrated_local')
        assert head == ctx['publish_commit']
    elif state in (gs.REVIEW_STATE_RE_REVIEW, None):
        assert ctx.get('merge_commit') is None and ctx.get('publish_commit') is None
        assert ctx.get('apply_phase') is None
        last = ctx['preserve_history'][-1]
        assert refs.get(last['ref']) == last['sha'] and mh == last['sha']
        if state is None:
            if ctx['resolver_baseline'].get('clean'):
                # a rejected CLEAN hand-off: nothing is unmerged, the rows are to revisit
                assert lost == []
            else:
                assert lost == sorted(f['path'] for f in gs.db_git.session_files(mid))
            assert head == ctx['resolver_baseline']['base_head']
        else:
            assert lost == [] and head == ctx['base_head']
            assert gs.db_git.remaining_conflicts(mid) == []
            assert ctx['resolver_baseline']['base_head'] == head
            assert ctx['resolver_baseline']['merge_ref'] == mh
    elif state == gs.REVIEW_STATE_COMPLETED:
        assert ctx['apply_phase'] == 'completed' and mh is None and lost == []
        assert refs.get(ctx['preserve_ref']) == ctx['merge_commit']
        for entry in integrations:
            assert refs.get(entry['ref']) == entry['sha']
        if bare is not None:
            assert git_rc(['merge-base', '--is-ancestor', ctx['published_commit'], 'main'],
                          bare).returncode == 0
    else:  # pragma: no cover
        raise AssertionError(f'unexpected review_state {state!r}')
    return ctx


@pytest.fixture
def common(env):
    """The real common completer (intent consumption and the AC decision are reached
    through it); only its ledger/slot/broadcast collaborators are recorded."""
    calls = {'approvals': [], 'finished': []}
    env.monkeypatch.setattr(gs, '_complete_merge_review', REAL_COMPLETE)
    env.monkeypatch.setattr(gs, '_finish_reviewed_attempt',
                            lambda *a, **kw: calls['finished'].append(a))
    env.monkeypatch.setattr(gs, '_cleanup_group_slot', lambda *a, **kw: None)
    env.monkeypatch.setattr(gs, '_emit_pending_changed', lambda *a, **kw: None)

    def approve(group_id, mid, intent):
        calls['approvals'].append((group_id, mid, intent))
        return {'approved': True, 'stage': 'complete'}
    env.monkeypatch.setattr(approval_intent, 'commit_deferred_approval', approve)
    return calls


def user_intervention(kind, code):
    return {'status': 'user_intervention_required', 'review_state': 'reconciling',
            'reconciliation_kind': kind, 'error': code}


# ── real scenarios ───────────────────────────────────────────────────────────

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


def _network_failure(args):
    return subprocess.CompletedProcess(['git', *args], 128, '',
                                       'fatal: unable to access: Could not resolve host: origin.invalid')


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


def test_B2_remote_contains_the_merge_commit_plus_more(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    git(['push', '-q', 'origin', f'{m}:refs/heads/main'], r.work)
    tip = remote_commit(env, r.tmp, 'later.txt', 'later\n')
    env.commands.clear()
    out = reconcile(mid)
    assert out['result']['status'] == 'merged'
    assert pushes(env) == [] and count(env, 'fetch') == 1
    assert git(['rev-parse', 'HEAD'], r.work) == m  # not moved to the remote tip
    assert origin_main(env) == tip
    assert_consistent(mid, r.work, r.bare)


def test_C1_remote_ahead_is_integrated_preserved_and_pushed(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    c = remote_commit(env, r.tmp, 'c.txt', 'concurrent\n')
    out = reconcile(mid)
    assert out['result']['status'] == 'merged'
    after = assert_consistent(mid, r.work, r.bare)
    i = after['publish_commit']
    assert i not in (m, c) and parents_of(r.work, i) == [m, c]
    assert git(['show', '-s', '--format=%an <%ae>|%cn <%ce>', i], r.work) == f'{IDENTITY}|{IDENTITY}'
    iref = f'refs/flowgate/preserve/{mid}-i1'
    assert ref_value(r.work, iref) == i and ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert origin_main(env) == i and git(['rev-parse', 'HEAD'], r.work) == i
    assert after['published_commit'] == i and after['publish_lease'] == c
    assert after['merge_commit'] == m and after['expected_remote_head'] == r.B
    assert after['integration_commits'] == [{
        'sha': i, 'ref': iref, 'parents': [m, c], 'onto': c, 'at': AT, 'instance_id': 'inst_A'}]
    assert pushes(env) == [['push', f'--force-with-lease=refs/heads/main:{c}', 'origin',
                            f'{i}:refs/heads/main']]
    assert_lease_only(env)
    assert count(env, 'reset') == 0 and count(env, 'fetch') == 1
    # §5.2 audit: every move is on the session with the §3.1 reason; reflogs are FlowGate's.
    assert [x for x in reasons(mid) if x.startswith(('reconcile', 'preserve_integration'))] == [
        'reconcile_fetch', 'preserve_integration_commit', 'reconcile_integrate', 'reconcile_repush']
    assert reflog(r.work, iref) == [f'{IDENTITY}|flowgate: preserve integration {mid}-i1']
    assert git(['reflog', 'show', '-1', '--format=%gn <%ge>', 'HEAD'], r.work) == IDENTITY


def test_C2_repush_timeout_after_integration_then_the_next_cycle_repushes_it(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    c = remote_commit(env, r.tmp, 'c.txt', 'concurrent\n')
    env.hooks.append(on('push', push_timeout))
    out = reconcile(mid)
    assert out == {'ok': True, 'result': {'status': 'retry_scheduled'}}
    first = assert_consistent(mid, r.work)
    i = first['publish_commit']
    assert first['apply_phase'] == 'integrated_local' and first['last_error']['local_sha'] == i
    assert first['last_error']['expected_remote_head'] == c and origin_main(env) == c
    env.hooks.clear()
    env.commands.clear()
    out = reconcile(mid)
    assert out['result']['status'] == 'merged'
    assert count(env, 'commit-tree') == 0 and count(env, 'fetch') == 0
    assert pushes(env) == [['push', f'--force-with-lease=refs/heads/main:{c}', 'origin',
                            f'{i}:refs/heads/main']]
    after = assert_consistent(mid, r.work, r.bare)
    assert len(after['integration_commits']) == 1 and origin_main(env) == i


def test_C4_clean_integration_that_fails_verification_goes_back_to_review(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    c = remote_commit(env, r.tmp, 'data.json', '{"broken": \n')
    out = reconcile(mid)
    assert out['result']['status'] == 're_review'
    assert out['result']['error'] == 'remote_integration_unverified'
    after = assert_consistent(mid, r.work)
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert after['last_error'] == {'code': 'remote_integration_unverified'}
    assert after['integration_verification'][0]['check'] == 'syntax'
    assert [h['sha'] for h in after['preserve_history']] == [m]
    assert after['preserve_history'][-1]['closed_reason'] == 'reconcile_remote_unverified'
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert origin_main(env) == c and pushes(env) == []
    assert after['approval_attempt_id'] is None and after['reconcile_cycle_count'] == 0
    assert 'reconciliation_kind' not in after and 'reconcile_next_at' not in after
    assert approval_intent.intent_of_context(after) == INTENT
    assert preserve_refs(r.work) == {f'refs/flowgate/preserve/{mid}': m}  # no integration pinned
    assert [x for x in reasons(mid) if x.startswith('reconcile')] == [
        'reconcile_fetch', 'reconcile_verified_reset', 'reconcile_handoff_merge']
    # review finding (P1): the clean hand-off re-bases the session on F like a conflict one
    assert after['resolver_baseline'] == {'base_head': c, 'merge_head': m, 'merge_ref': m,
                                          'source': 'reconcile_remote_unverified', 'clean': True}
    assert after['base_head'] == c and after['merge_head'] == m
    assert [(f['path'], f['resolved']) for f in gs.db_git.session_files(mid)] == [('data.json', 1)]


def _reject(env, mid, reason='again', runs=None):
    """reject_merge_review on the real checkout (only the session lookup is pinned)."""
    r = env.repo
    env.monkeypatch.setattr(gs, '_merge_review_session',
                            lambda *a, **kw: (gs.db_git.get_session(mid), saved(mid), PROJECT, r.work, 'main'))
    env.monkeypatch.setattr(gs, 'apply_eol_separation', lambda *a, **kw: None)
    calls = runs if runs is not None else []
    return gs.reject_merge_review(env.group, mid, reason=reason, provider_id=None,
                                  provider_pinned=False,
                                  start_run=lambda why: calls.append(why) or 'run-reject')


def test_C4R_reject_after_the_clean_hand_off_returns_the_same_candidate_to_the_resolver(env):
    """Review finding (P1): remote broken JSON → clean hand-off (re_review) → reject. The
    rejection restores the SAME clean candidate (HEAD = F, MERGE_HEAD = M) for the
    resolver, never restoration_verification_failed/base_head_moved, and the resolver's
    fix freezes on the same heads."""
    r = env.repo
    mid, m, before = unknown_session(env)
    original = {'base_head': r.B, 'merge_head': r.F}
    ctx = saved(mid)
    ctx['resolver_baseline'] = dict(original)  # what finalize records at session creation
    gs.db_git.set_session_context(mid, ctx)
    gs.db_git.add_session_files(mid, ['f.txt'])
    gs.db_git.mark_file_resolved(mid, 'f.txt')
    c = remote_commit(env, r.tmp, 'data.json', '{"broken": \n')
    out = reconcile(mid)
    assert out['result']['status'] == 're_review'
    handed = assert_consistent(mid, r.work)
    assert handed['resolver_baseline'] == {'base_head': c, 'merge_head': m, 'merge_ref': m,
                                           'source': 'reconcile_remote_unverified', 'clean': True}
    assert handed['resolver_baseline_history'] == [original]
    assert [(f['path'], f['resolved']) for f in gs.db_git.session_files(mid)] == [
        ('data.json', 1), ('f.txt', 1)]
    candidate = handed['snapshot_tree']
    runs = []
    out = _reject(env, mid, reason='fix the json', runs=runs)
    assert out['result']['status'] == 'returned_to_resolver' and runs == ['fix the json']
    after = saved(mid)
    assert after['review_state'] is None and 'last_error' not in after
    assert after.get('reconciliation_kind') != 'restoration_verification_failed'
    assert after['resolver_baseline'] == handed['resolver_baseline']
    assert git(['rev-parse', 'HEAD'], r.work) == c and ref_value(r.work, 'MERGE_HEAD') == m
    assert unmerged(r.work) == [] and git(['write-tree'], r.work) == candidate
    assert (r.work / 'data.json').read_text(encoding='utf-8') == '{"broken": \n'
    assert gs.db_git.remaining_conflicts(mid) == ['data.json', 'f.txt']
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m and origin_main(env) == c
    assert pushes(env) == []
    assert_consistent(mid, r.work)
    # the resolver's fix is an ordinary candidate on the same new heads
    (r.work / 'data.json').write_text('{"fixed": true}\n', encoding='utf-8')
    git(['add', 'data.json'], r.work)
    snap = gs._freeze_commit_candidate(r.work, 'main')
    assert (snap['base_head'], snap['merge_head']) == (c, m)


MARKER_BLOCK = 'top\n<<<<<<< ours\nmine\n=======\ntheirs\n>>>>>>> theirs\n'


def test_C5_conflict_markers_arriving_from_the_remote_are_handed_back_not_pushed(env):
    """Review finding (P1), T0009 §3.3 (c)-2-3: a clean integration whose remote side adds
    a marker block is not integrated, pinned or pushed; origin stays F. The approval
    validator's own exemption (markers inherited from either parent) is unchanged and
    alone would have let it through — the separate check is what catches it."""
    r = env.repo
    mid, m, before = unknown_session(env)
    c = remote_commit(env, r.tmp, 'remote.txt', MARKER_BLOCK)
    out = reconcile(mid)
    assert out['result']['status'] == 're_review'
    after = assert_consistent(mid, r.work)
    assert after['integration_verification'] == [{'check': 'conflict_markers', 'paths': ['remote.txt']}]
    assert origin_main(env) == c and pushes(env) == [] and count(env, 'commit-tree') == 0
    assert preserve_refs(r.work) == {f'refs/flowgate/preserve/{mid}': m}  # no integration pinned
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert [f['path'] for f in gs.db_git.session_files(mid)] == ['remote.txt']
    assert_lease_only(env)
    tree = git(['merge-tree', '--write-tree', m, c], r.work).splitlines()[0]
    probe = {
        'snapshot_manifest': gs._parse_ls_tree_z(gs._run_git(['ls-tree', '-r', '-z', tree], cwd=r.work).stdout),
        'changes': gs._parse_name_status_manifest(
            gs._run_git(['diff', '--name-status', '-M', '-z', m, tree, '--'], cwd=r.work).stdout),
        'base_head': m, 'merge_head': c,
    }
    assert gs._validate_review_changed_paths(r.work, probe, unregistered_extension='skip') == []
    assert gs._integration_marker_paths(r.work, m, probe) == ['remote.txt']


def conflict_session(env, x='x.txt', y='y.txt'):
    """A resolution merge of TWO originally conflicting files (x.txt, y.txt) whose push
    result was lost: reconciling/push_remote_unknown, origin at the pre-merge tip."""
    r = env.repo
    w = r.work
    (w / x).write_text('x base\n', encoding='utf-8')
    (w / y).write_text('y base\n', encoding='utf-8')
    git(['add', '.'], w)
    git(['commit', '-q', '-m', 'xy base'], w)
    git(['checkout', '-q', '-b', 'feature2'], w)
    (w / x).write_text('x feature\n', encoding='utf-8')
    (w / y).write_text('y feature\n', encoding='utf-8')
    git(['commit', '-q', '-am', 'feature xy'], w)
    f2 = git(['rev-parse', 'HEAD'], w)
    git(['checkout', '-q', 'main'], w)
    (w / x).write_text('x main\n', encoding='utf-8')
    (w / y).write_text('y main\n', encoding='utf-8')
    git(['commit', '-q', '-am', 'main xy'], w)
    b2 = git(['rev-parse', 'HEAD'], w)
    git(['push', '-q', 'origin', 'main'], w)
    assert git_rc(['merge', '--no-ff', '--no-commit', 'feature2'], w).returncode != 0
    (w / x).write_text('x resolved\n', encoding='utf-8')
    (w / y).write_text('y resolved\n', encoding='utf-8')
    git(['add', x, y], w)
    git(['commit', '-q', '-m', 'resolve xy'], w)
    m = git(['rev-parse', 'HEAD'], w)
    ctx = approved(r, m, base_head=b2, merge_head=f2, expected_remote_head=b2,
                   resolver_baseline={'base_head': b2, 'merge_head': f2})
    mid = gs.db_git.create_session(env.group, [x, y], finalize_action='merge', context=ctx)
    env.hooks.append(on('push', push_timeout))
    out = publish(env, mid)
    env.hooks.clear()
    assert out['result']['reconciliation_kind'] == 'push_remote_unknown'
    before = saved(mid)
    env.commands.clear()
    return mid, m, b2, before


def test_D1_real_conflict_returns_only_the_new_conflict_to_the_resolver(env):
    r = env.repo
    mid, m, b2, before = conflict_session(env)
    c = remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    out = reconcile(mid)
    assert out['result'] == {'status': 'returned_to_resolver', 'review_state': None,
                             'conflict_paths': ['y.txt'], 'error': 'remote_conflict'}
    after = assert_consistent(mid, r.work)
    assert unmerged(r.work) == ['y.txt']
    assert (r.work / 'x.txt').read_text(encoding='utf-8') == 'x resolved\n'  # resolution kept
    assert [f['path'] for f in gs.db_git.session_files(mid)] == ['y.txt']
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert after['resolver_baseline'] == {'base_head': c, 'merge_head': m, 'merge_ref': m,
                                          'source': 'reconcile_remote_conflict'}
    assert after['resolver_baseline_history'] == [before['resolver_baseline']]
    assert after['last_error'] == {'code': 'remote_conflict', 'paths': ['y.txt']}
    assert approval_intent.intent_of_context(after) == INTENT
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert after['preserve_history'][-1]['closed_reason'] == 'reconcile_remote_conflict'
    assert origin_main(env) == c and pushes(env) == [] and count(env, 'commit-tree') == 0


def test_D2_reject_after_the_hand_off_recreates_the_same_new_conflict(env):
    r = env.repo
    mid, m, b2, before = conflict_session(env)
    c = remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    reconcile(mid)
    (r.work / 'y.txt').write_text('y resolved again\n', encoding='utf-8')
    git(['add', 'y.txt'], r.work)
    ctx = saved(mid)
    ctx.update(gs._freeze_commit_candidate(r.work, 'main'))
    ctx['review_state'] = gs.REVIEW_STATE_PENDING  # the resolver submitted (test setup)
    gs.db_git.set_session_context(mid, ctx)
    env.monkeypatch.setattr(gs, '_merge_review_session',
                            lambda *a, **kw: (gs.db_git.get_session(mid), saved(mid), PROJECT, r.work, 'main'))
    env.monkeypatch.setattr(gs, 'apply_eol_separation', lambda *a, **kw: None)
    out = gs.reject_merge_review(env.group, mid, reason='again', provider_id=None,
                                 provider_pinned=False, start_run=lambda reason: 'run-d2')
    assert out['result']['status'] == 'returned_to_resolver'
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert unmerged(r.work) == ['y.txt']
    assert (r.work / 'x.txt').read_text(encoding='utf-8') == 'x resolved\n'
    after = saved(mid)
    assert after.get('reconciliation_kind') != 'restoration_verification_failed'
    assert after['review_state'] is None
    assert_consistent(mid, r.work)


def test_D1Q_quoted_conflict_paths_are_stored_and_restored_as_the_real_path(env):
    """Review finding (P2): with core.quotePath on (Git's default) a non-ASCII conflict
    path is C-quoted in ``diff --name-only`` lines. The hand-off stores, reports and
    restores the REAL path string — compared here with the literal name, not with another
    line-form parse."""
    r = env.repo
    git(['config', 'core.quotePath', 'true'], r.work)
    kept, changed = 'naïve.txt', '한글.txt'
    mid, m, b2, before = conflict_session(env, x=kept, y=changed)
    c = remote_commit(env, r.tmp, changed, 'y remote\n')
    out = reconcile(mid)
    assert out['result'] == {'status': 'returned_to_resolver', 'review_state': None,
                             'conflict_paths': [changed], 'error': 'remote_conflict'}
    line_form = git(['diff', '--name-only', '--diff-filter=U'], r.work)
    assert line_form.startswith('"') and line_form != changed  # the quoting is really on
    assert [f['path'] for f in gs.db_git.session_files(mid)] == [changed]
    assert all((r.work / f['path']).is_file() for f in gs.db_git.session_files(mid))
    assert gs._unmerged_paths_exact(r.work) == [changed]
    after = saved(mid)
    assert after['last_error'] == {'code': 'remote_conflict', 'paths': [changed]}
    assert (r.work / kept).read_text(encoding='utf-8') == 'x resolved\n'  # resolution kept
    assert ref_value(r.work, 'MERGE_HEAD') == m and git(['rev-parse', 'HEAD'], r.work) == c
    assert_consistent(mid, r.work)
    # D2 on the same paths: the rejection compares the rows with the same real-path query
    (r.work / changed).write_text('y resolved again\n', encoding='utf-8')
    git(['add', changed], r.work)
    ctx = saved(mid)
    ctx.update(gs._freeze_commit_candidate(r.work, 'main'))
    ctx['review_state'] = gs.REVIEW_STATE_PENDING  # the resolver submitted (test setup)
    gs.db_git.set_session_context(mid, ctx)
    out = _reject(env, mid)
    assert out['result']['status'] == 'returned_to_resolver'
    after = saved(mid)
    assert after['review_state'] is None
    assert after.get('reconciliation_kind') != 'restoration_verification_failed'
    assert unmerged(r.work) == [changed] and gs.db_git.remaining_conflicts(mid) == [changed]
    assert_consistent(mid, r.work)


def test_R_rewritten_remote_history_is_manual_without_push_or_reset(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    other = other_clone(env)
    git(['reset', '-q', '--hard', r.A], other)
    (other / 'rewrite.txt').write_text('rewritten\n', encoding='utf-8')
    git(['add', '.'], other)
    git(['commit', '-q', '-m', 'rewrite'], other)
    git(['push', '-q', '--force', 'origin', 'main'], other)  # the remote's own rewrite
    rewritten = git(['rev-parse', 'HEAD'], other)
    env.commands.clear()
    out = reconcile(mid)
    assert out == {'ok': True, 'result': {'status': 'manual_reconciliation_needed'}}
    after = assert_consistent(mid, r.work)
    assert after['reconciliation_kind'] == 'push_remote_third' and after['reconcile_next_at'] is None
    assert after['last_error']['code'] == 'remote_rewritten' and after['last_error']['observed'] == rewritten
    assert after['last_error']['prior_push_error'] == before['last_error']
    assert pushes(env) == [] and count(env, 'reset') == 0 and origin_main(env) == rewritten


def test_X1_handoff_reset_really_fails_on_index_lock(env):
    r = env.repo
    mid, m, b2, before = conflict_session(env)
    remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    lock = Path(git(['rev-parse', '--absolute-git-dir'], r.work)) / 'index.lock'

    def lock_index(args):
        if args and args[0] == 'reset':
            lock.write_text('', encoding='utf-8')
        return None
    env.hooks.append(lock_index)
    try:
        out = reconcile(mid)
    finally:
        lock.unlink(missing_ok=True)
    assert out['result'] == user_intervention('reconcile_reset_failed', 'reconcile_reset_failed')
    after = assert_consistent(mid, r.work)
    e = after['last_error']
    assert e['returncode'] not in (0, None) and e['observed_head'] == m and e['detail']
    assert after['merge_commit'] == m and after['apply_phase'] == 'committed_local'
    assert after['approval_attempt_id'] == 'attempt-1' and after['preserve_sha'] == m
    assert after.get('preserve_history') in (None, [])  # round not closed
    assert after['review_state'] == 'reconciling'


def test_X2_reset_reporting_success_with_the_wrong_head_is_reset_failed(env):
    r = env.repo
    mid, m, b2, before = conflict_session(env)
    remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
    env.hooks.append(on('reset', lambda a: subprocess.CompletedProcess(['git', *a], 0, '', '')))
    out = reconcile(mid)
    assert out['result'] == user_intervention('reconcile_reset_failed', 'reconcile_reset_failed')
    after = assert_consistent(mid, r.work)
    assert after['last_error']['returncode'] == 0 and after['last_error']['observed_head'] == m
    assert after['last_error']['detail'].startswith('post_condition:head')
    assert after['merge_commit'] == m and after.get('preserve_history') in (None, [])


def diverge_local(env):
    """Move the local branch to a commit that is NOT an ancestor of the merge commit."""
    r = env.repo
    git(['checkout', '-q', '-B', 'main', r.A], r.work)
    (r.work / 'side.txt').write_text('side\n', encoding='utf-8')
    git(['add', '.'], r.work)
    git(['commit', '-q', '-m', 'side'], r.work)
    return git(['rev-parse', 'HEAD'], r.work)


def test_X3_foreign_local_head_is_a_mismatch_without_reset(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    side = diverge_local(env)
    env.commands.clear()
    out = reconcile(mid)
    assert out['result'] == user_intervention('reconcile_local_state_mismatch', 'reconcile_local_state_mismatch')
    after = assert_consistent(mid, r.work)
    assert after['last_error']['observed_head'] == side and after['last_error']['expected_head'] == m
    assert count(env, 'reset') == 0 and count(env, 'ls-remote') == 0 and pushes(env) == []


def test_P_integration_ref_write_failure_is_preserve_failed(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    remote_commit(env, r.tmp, 'c.txt', 'concurrent\n')
    gitdir = Path(git(['rev-parse', '--absolute-git-dir'], r.work))
    lock = gitdir / 'refs' / 'flowgate' / 'preserve' / f'{mid}-i1.lock'
    lock.write_text('', encoding='utf-8')
    out = reconcile(mid)
    assert out['result']['reconciliation_kind'] == 'preserve_failed'
    after = assert_consistent(mid, r.work)
    e = after['last_error']
    assert (e['stage'], e['reason']) == ('integration', 'ref_write_failed')
    assert e['preserve_ref'] == f'refs/flowgate/preserve/{mid}-i1'
    assert e['prior_push_error'] == before['last_error']
    assert git(['rev-parse', 'HEAD'], r.work) == m and pushes(env) == []
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}-i1') is None


REAL_SCENARIOS = {
    'G': test_G_damaged_preservation_ref_never_reaches_the_remote,
    'L': test_L_spent_cycle_budget_parks_without_remote_contact,
    'Q': test_Q_query_failure_keeps_the_retry_with_backoff,
    'Q5': test_Q5_query_failure_in_the_fifth_cycle_is_exhausted,
    'E1': test_E1_ls_remote_auth_failure_stops_after_one_probe,
    'A1': test_A1_group_unchanged_remote_repushes_the_same_commit,
    'A2': test_A2_repush_timeout_keeps_everything_and_records_the_stage,
    'A3': test_A3_lease_rejection_is_transient_without_reset_or_refreeze,
    'A4': test_A4_hook_permission_rejection_is_auth_not_the_rejected_branch,
    'B1': test_B1_remote_already_has_the_merge_commit,
    'B2': test_B2_remote_contains_the_merge_commit_plus_more,
    'C1': test_C1_remote_ahead_is_integrated_preserved_and_pushed,
    'C2': test_C2_repush_timeout_after_integration_then_the_next_cycle_repushes_it,
    'C4': test_C4_clean_integration_that_fails_verification_goes_back_to_review,
    'C4R': test_C4R_reject_after_the_clean_hand_off_returns_the_same_candidate_to_the_resolver,
    'C5': test_C5_conflict_markers_arriving_from_the_remote_are_handed_back_not_pushed,
    'D1Q': test_D1Q_quoted_conflict_paths_are_stored_and_restored_as_the_real_path,
    'D1': test_D1_real_conflict_returns_only_the_new_conflict_to_the_resolver,
    'R': test_R_rewritten_remote_history_is_manual_without_push_or_reset,
    'X1': test_X1_handoff_reset_really_fails_on_index_lock,
    'X2': test_X2_reset_reporting_success_with_the_wrong_head_is_reset_failed,
    'X3': test_X3_foreign_local_head_is_a_mismatch_without_reset,
    'P': test_P_integration_ref_write_failure_is_preserve_failed,
}


# ── §5.2 additional required tests ───────────────────────────────────────────

def test_incident_regression_push_timeout_then_repush_completes_and_survives_recovery(env, common):
    r = env.repo
    states = []
    real_set = gs.db_git.set_session_context

    def watch(mid_, context):
        states.append(context.get('review_state'))
        return real_set(mid_, context)
    env.monkeypatch.setattr(gs.db_git, 'set_session_context', watch)
    mid, m, before = unknown_session(env)  # real Git, push result lost → push_remote_unknown
    assert origin_main(env) == r.B
    out = reconcile(mid)
    assert out['result']['status'] == 'merged' and origin_main(env) == m
    assert common['approvals'] == [(env.group, mid, INTENT)]
    assert gs.REVIEW_STATE_PENDING not in states
    after = assert_consistent(mid, r.work, r.bare)
    gs.db_git.close_session(mid, 'done')  # what the (recorded) attempt close does
    _only(env, mid)
    env.commands.clear()
    gs.merge_session_sweep()
    gs.startup_recovery()
    assert gs.db_git.get_session(mid)['status'] == 'done'
    assert saved(mid)['review_state'] == gs.REVIEW_STATE_COMPLETED
    assert saved(mid)['published_commit'] == after['published_commit'] == m
    assert pushes(env) == [] and count(env, 'reset') == 0
    assert gs.REVIEW_STATE_PENDING not in states


def _park(env, case):
    r = env.repo
    if case == 'reset_failed':
        mid, m, b2, before = conflict_session(env)
        remote_commit(env, r.tmp, 'y.txt', 'y remote\n')
        env.hooks.append(on('reset', lambda a: subprocess.CompletedProcess(['git', *a], 0, '', '')))
        reconcile(mid)
        env.hooks.clear()
        return mid, 'reconcile_reset_failed'
    mid, m, before = unknown_session(env)
    if case == 'auth':
        env.hooks.append(on('ls-remote', lambda a: subprocess.CompletedProcess(
            ['git', *a], 128, '', 'fatal: Authentication failed')))
        expected = 'reconcile_auth_required'
    elif case == 'exhausted':
        ctx = saved(mid)
        ctx['reconcile_cycle_count'] = 5
        gs.db_git.set_session_context(mid, ctx)
        expected = 'reconcile_retry_exhausted'
    else:
        diverge_local(env)
        expected = 'reconcile_local_state_mismatch'
    reconcile(mid)
    env.hooks.clear()
    return mid, expected


@pytest.mark.parametrize('case', ['auth', 'exhausted', 'reset_failed', 'local_mismatch'])
def test_user_intervention_survives_sweep_and_startup_untouched(env, case):
    r = env.repo
    mid, kind = _park(env, case)
    parked = saved(mid)
    assert parked['reconciliation_kind'] == kind
    head = git(['rev-parse', 'HEAD'], r.work)
    refs = preserve_refs(r.work)
    _only(env, mid)
    env.commands.clear()
    gs.merge_session_sweep()
    gs.startup_recovery()
    assert env.commands == []
    after = saved(mid)
    assert after == parked
    assert gs.db_git.get_session(mid)['status'] == 'open'
    assert after['review_state'] == 'reconciling' and SHA_RE.match(after['merge_commit'])
    assert approval_intent.intent_of_context(after) == INTENT
    assert preserve_refs(r.work) == refs and git(['rev-parse', 'HEAD'], r.work) == head
    assert_consistent(mid, r.work)


RECONCILE_SOURCES = [
    'reconcile_push_session', '_reconcile_cycle', '_reconcile_complete', '_reconcile_repush',
    '_reconcile_integrate', '_reconcile_rewritten', '_reconcile_transient', '_reconcile_fetch',
    '_reconcile_probe', '_reconcile_local_state', '_verified_reset', '_handoff_to_human',
    '_enter_user_intervention', '_enter_local_mismatch', '_enter_retry_exhausted',
    '_enter_auth_required', '_integration_tree_problems', '_integration_marker_paths',
    '_integration_problem_paths',
]


def test_static_reconcile_sources_have_no_forbidden_operations():
    texts = {name: inspect.getsource(getattr(gs, name)) for name in RECONCILE_SOURCES}
    texts['reconcile_plan'] = inspect.getsource(rp)
    for name, text in texts.items():
        assert not re.search(r'=\s*REVIEW_STATE_PENDING\b', text), name
        assert 'resolved_pending_review' not in text, name
        assert 'ORIG_HEAD' not in text, name
        assert not re.search(r'--force(?!-with-lease)', text), name
        assert not re.search(r'''["']-f["']''', text), name
        assert not re.search(r'''f?["']\+[\w{]''', text), name
    push_lines = [line for line in texts['_reconcile_repush'].splitlines() if 'force-with-lease' in line]
    assert push_lines and all('refs/heads/{run.branch}:{lease}' in line for line in push_lines)
