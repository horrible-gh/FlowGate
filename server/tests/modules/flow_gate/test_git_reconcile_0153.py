"""the non-destructive reconcile state machine.

``TRANSITIONS`` is the decision table of the earlier implementation as module data. The pure
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

# ── the decision table ──────────────────────────────────────────
# id, inputs (None: decided before decide, i.e. by the the earlier implementation gate), expected action,
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
    """invariants: the re-read sqlite context against the real checkout/refs."""
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





def _network_failure(args):
    return subprocess.CompletedProcess(['git', *args], 128, '',
                                       'fatal: unable to access: Could not resolve host: origin.invalid')




























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




MARKER_BLOCK = 'top\n<<<<<<< ours\nmine\n=======\ntheirs\n>>>>>>> theirs\n'




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














def diverge_local(env):
    """Move the local branch to a commit that is NOT an ancestor of the merge commit."""
    r = env.repo
    git(['checkout', '-q', '-B', 'main', r.A], r.work)
    (r.work / 'side.txt').write_text('side\n', encoding='utf-8')
    git(['add', '.'], r.work)
    git(['commit', '-q', '-m', 'side'], r.work)
    return git(['rev-parse', 'HEAD'], r.work)






# Node IDs keep scenario coverage explicit without importing split modules cyclically.
REAL_SCENARIOS = {
    'G': 'tests/modules/flow_gate/test_git_reconcile_0153_part02.py::test_G_damaged_preservation_ref_never_reaches_the_remote',
    'L': 'tests/modules/flow_gate/test_git_reconcile_0153_part02.py::test_L_spent_cycle_budget_parks_without_remote_contact',
    'Q': 'tests/modules/flow_gate/test_git_reconcile_0153_part02.py::test_Q_query_failure_keeps_the_retry_with_backoff',
    'Q5': 'tests/modules/flow_gate/test_git_reconcile_0153_part02.py::test_Q5_query_failure_in_the_fifth_cycle_is_exhausted',
    'E1': 'tests/modules/flow_gate/test_git_reconcile_0153_part02.py::test_E1_ls_remote_auth_failure_stops_after_one_probe',
    'A1': 'tests/modules/flow_gate/test_git_reconcile_0153_part02.py::test_A1_group_unchanged_remote_repushes_the_same_commit',
    'A2': 'tests/modules/flow_gate/test_git_reconcile_0153_part03.py::test_A2_repush_timeout_keeps_everything_and_records_the_stage',
    'A3': 'tests/modules/flow_gate/test_git_reconcile_0153_part03.py::test_A3_lease_rejection_is_transient_without_reset_or_refreeze',
    'A4': 'tests/modules/flow_gate/test_git_reconcile_0153_part03.py::test_A4_hook_permission_rejection_is_auth_not_the_rejected_branch',
    'B1': 'tests/modules/flow_gate/test_git_reconcile_0153_part03.py::test_B1_remote_already_has_the_merge_commit',
    'B2': 'tests/modules/flow_gate/test_git_reconcile_0153_part04.py::test_B2_remote_contains_the_merge_commit_plus_more',
    'C1': 'tests/modules/flow_gate/test_git_reconcile_0153_part04.py::test_C1_remote_ahead_is_integrated_preserved_and_pushed',
    'C2': 'tests/modules/flow_gate/test_git_reconcile_0153_part04.py::test_C2_repush_timeout_after_integration_then_the_next_cycle_repushes_it',
    'C4': 'tests/modules/flow_gate/test_git_reconcile_0153_part04.py::test_C4_clean_integration_that_fails_verification_goes_back_to_review',
    'C4R': 'tests/modules/flow_gate/test_git_reconcile_0153_part04.py::test_C4R_reject_after_the_clean_hand_off_returns_the_same_candidate_to_the_resolver',
    'C5': 'tests/modules/flow_gate/test_git_reconcile_0153_part04.py::test_C5_conflict_markers_arriving_from_the_remote_are_handed_back_not_pushed',
    'D1Q': 'tests/modules/flow_gate/test_git_reconcile_0153_part05.py::test_D1Q_quoted_conflict_paths_are_stored_and_restored_as_the_real_path',
    'D1': 'tests/modules/flow_gate/test_git_reconcile_0153_part05.py::test_D1_real_conflict_returns_only_the_new_conflict_to_the_resolver',
    'R': 'tests/modules/flow_gate/test_git_reconcile_0153_part05.py::test_R_rewritten_remote_history_is_manual_without_push_or_reset',
    'X1': 'tests/modules/flow_gate/test_git_reconcile_0153_part05.py::test_X1_handoff_reset_really_fails_on_index_lock',
    'X2': 'tests/modules/flow_gate/test_git_reconcile_0153_part06.py::test_X2_reset_reporting_success_with_the_wrong_head_is_reset_failed',
    'X3': 'tests/modules/flow_gate/test_git_reconcile_0153_part06.py::test_X3_foreign_local_head_is_a_mismatch_without_reset',
    'P': 'tests/modules/flow_gate/test_git_reconcile_0153_part06.py::test_P_integration_ref_write_failure_is_preserve_failed',
}


# ── §5.2 additional required tests ───────────────────────────────────────────



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




RECONCILE_SOURCES = [
    'reconcile_push_session', '_reconcile_cycle', '_reconcile_complete', '_reconcile_repush',
    '_reconcile_integrate', '_reconcile_rewritten', '_reconcile_transient', '_reconcile_fetch',
    '_reconcile_probe', '_reconcile_local_state', '_verified_reset', '_handoff_to_human',
    '_enter_user_intervention', '_enter_local_mismatch', '_enter_retry_exhausted',
    '_enter_auth_required', '_integration_tree_problems', '_integration_marker_paths',
    '_integration_problem_paths',
]
