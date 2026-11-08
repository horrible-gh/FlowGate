"""0153 T#2: preservation refs for human-approved resolution merge commits.

Every scenario runs real Git on local-path repositories (a bare ``origin`` plus a work
checkout; no file:// capability gate) and re-reads the real sqlite session row from the
0115 migrations. The shared runner is wrapped only to record the command order and, in
the named fault scenarios, to replace one specific command's result.
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.modules.flow_gate.test_git_integration_0115 import patch_store, tmp_db  # noqa: F401
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import approval_intent, branch_merge, branch_merge_publish, preserve
from modules.flow_gate.db import groups, projects

pytestmark = pytest.mark.skipif(shutil.which('git') is None, reason='git binary unavailable')

PROJECT = 'preserve0153'
AT = '2026-11-08T10:00:00+09:00'
LATER = '2026-11-08T11:00:00+09:00'
RETAIN = '2026-12-08T10:00:00+09:00'
NEXT_AT = '2026-11-08T10:10:00+09:00'
INTENT = {'approval_intent_id': 'intent-0153', 'ac_doc_id': 'flowgate.v02.0153.0007-T', 'git_action': 'merge'}
IDENTITY = 'FlowGate <flowgate@localhost>'


# ── real Git helpers ─────────────────────────────────────────────────────────

def git_rc(args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=60)


def git(args, cwd):
    proc = git_rc(args, cwd)
    assert proc.returncode == 0, (args, proc.stderr)
    return proc.stdout.strip()


def ref_value(repo, ref):
    proc = git_rc(['rev-parse', '--verify', '--quiet', ref], repo)
    return proc.stdout.strip() if proc.returncode == 0 else None


def reflog(repo, ref):
    return git(['reflog', 'show', '--format=%gn <%ge>|%gs', ref], repo).splitlines()


def parents_of(repo, rev):
    return git(['rev-list', '--parents', '-n', '1', rev], repo).split()[1:]


def preserve_refs(repo):
    out = git(['for-each-ref', '--format=%(refname) %(objectname)', 'refs/flowgate/preserve/'], repo)
    return dict(line.split(' ', 1) for line in out.splitlines() if line)


def done(cmd):
    return subprocess.CompletedProcess(['git', *cmd], 0, '', '')


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module', autouse=True)
def project(patch_store):
    projects.create({'project_id': PROJECT, 'project_name': 'Preserve0153'})


@pytest.fixture
def repo(tmp_path):
    """origin(bare)@B, work@B on main, feature@F off A — ``merge --no-ff feature`` on
    main yields the resolution commit with parents [B, F]."""
    bare, work = tmp_path / 'origin.git', tmp_path / 'work'
    git(['init', '--bare', '-b', 'main', str(bare)], tmp_path)
    git(['init', '-b', 'main', str(work)], tmp_path)
    git(['config', 'user.name', 'merge'], work)
    git(['config', 'user.email', 'merge@local'], work)
    (work / 'a.txt').write_text('base\n', encoding='utf-8')
    git(['add', '.'], work)
    git(['commit', '-m', 'base'], work)
    a = git(['rev-parse', 'HEAD'], work)
    git(['remote', 'add', 'origin', str(bare)], work)
    git(['push', '-u', 'origin', 'main'], work)
    git(['checkout', '-b', 'feature'], work)
    (work / 'f.txt').write_text('feature\n', encoding='utf-8')
    git(['add', '.'], work)
    git(['commit', '-m', 'feature'], work)
    f = git(['rev-parse', 'HEAD'], work)
    git(['checkout', 'main'], work)
    (work / 'b.txt').write_text('main\n', encoding='utf-8')
    git(['add', '.'], work)
    git(['commit', '-m', 'main'], work)
    b = git(['rev-parse', 'HEAD'], work)
    git(['push', 'origin', 'main'], work)
    return SimpleNamespace(tmp=tmp_path, bare=bare, work=work, A=a, F=f, B=b)


@pytest.fixture
def env(monkeypatch, project, repo):
    group = PROJECT + '.default.' + uuid.uuid4().hex[:12]
    groups.create({'group_id': group, 'project_id': PROJECT, 'module': 'default', 'title': 'preserve'})
    gs.db_git.register_worktree(group, PROJECT, group.replace('.', '_'))
    clock = {'now': AT, 'instance': 'inst_A'}
    commands, hooks, completed, probes = [], [], [], []
    monkeypatch.setattr(gs, 'now_iso', lambda: clock['now'])
    monkeypatch.setattr(gs, 'current_instance_id', lambda: clock['instance'])
    monkeypatch.setattr(gs, '_seconds_from_now_iso', lambda seconds: NEXT_AT)
    monkeypatch.setattr(gs, 'ensure_origin_matches_config', lambda *a: commands.append(['<origin-sync>']))
    monkeypatch.setattr(gs, '_load_secret_for', lambda cfg: commands.append(['<secret-load>']) or None)
    monkeypatch.setattr(branch_merge_publish, 'session_lock', lambda *a, **kw: (None, None))
    monkeypatch.setattr(branch_merge_publish, 'session_unlock', lambda *a: None)
    monkeypatch.setattr(gs.merge_target, 'resolve_session_target',
                        lambda row: SimpleNamespace(root=repo.work, target_branch='main'))
    monkeypatch.setattr(gs, '_emit', lambda *a, **kw: None)
    monkeypatch.setattr(gs, 'PUSH_RECONCILE_DELAYS_SEC', [0, 0, 0])
    real = gs._run_git

    def run(args, **kw):
        commands.append(list(args))
        for hook in list(hooks):
            result = hook(list(args))
            if result is not None:
                return result
        return real(args, **kw)
    monkeypatch.setattr(gs, '_run_git', run)

    def complete(group_id, mid, project_id, context, *, pushed):
        context['review_state'] = gs.REVIEW_STATE_COMPLETED
        context['apply_phase'] = 'completed'
        gs.db_git.set_session_context(mid, context)
        completed.append(pushed)
        return {'ok': True, 'result': {'status': 'merged', 'review_state': gs.REVIEW_STATE_COMPLETED,
                                       'pushed': pushed}}
    monkeypatch.setattr(gs, '_complete_merge_review', complete)
    monkeypatch.setattr(branch_merge, 'complete_reviewed',
                        lambda mid, context, *, pushed: complete(None, mid, PROJECT, context, pushed=pushed))

    def remote(observed):
        def query(*a):
            probes.append(a)
            return observed(a) if callable(observed) else observed
        monkeypatch.setattr(gs, '_query_remote_ref', query)
    return SimpleNamespace(group=group, repo=repo, clock=clock, commands=commands, hooks=hooks,
                           completed=completed, probes=probes, remote=remote, monkeypatch=monkeypatch)


def saved(mid):
    return gs.db_git.session_context(gs.db_git.get_session(mid))


def names(env):
    return [c[0] if c[0] != '-c' else next(x for x in c if not x.startswith('-c') and '=' not in x)
            for c in env.commands]


def merge_commit(r):
    git(['merge', '--no-ff', '--no-commit', 'feature'], r.work)
    git(['commit', '-m', 'merge feature'], r.work)
    return git(['rev-parse', 'HEAD'], r.work)


def approved(r, sha, **extra):
    ctx = {'review_state': gs.REVIEW_STATE_APPLYING, 'merge_commit': sha, 'base_head': r.B,
           'merge_head': r.F, 'expected_remote_head': r.B, 'apply_phase': 'committed_local',
           'approval_attempt_id': 'attempt-1', 'review_fingerprint': 'fp', 'target_branch': 'main',
           approval_intent.INTENT_KEY: dict(INTENT)}
    ctx.update(extra)
    return ctx


def open_session(env, context, action='merge'):
    return gs.db_git.create_session(env.group, [], finalize_action=action, context=context)


def publish(env, mid, *, branch=False, push=True):
    row = gs.db_git.get_session(mid)
    ctx = saved(mid)
    if branch:
        ctx['branch_merge'] = {'push': push}
        gs.db_git.set_session_context(mid, ctx)
        return branch_merge.publish_reviewed(mid, row, PROJECT, env.repo.work, {}, 'main', ctx)
    return gs._conditionally_push_or_reconcile(env.group, mid, row, PROJECT, env.repo.work, {}, 'main', ctx)


def on(name, result):
    """Hook: replace the result of the first-word command ``name``."""
    def hook(args):
        if args and args[0] == name:
            return result(args) if callable(result) else result
        return None
    return hook


def push_timeout(args):
    return subprocess.CompletedProcess(['git', *args], -1, '', 'timeout_expired')


def push_rejected(args):
    return subprocess.CompletedProcess(['git', *args], 1, '', ' ! [rejected] main -> main (stale info)')


PRESERVE_FAILED = {'status': 'reconciling', 'review_state': gs.REVIEW_STATE_RECONCILING,
                   'reconciliation_kind': 'preserve_failed', 'error': 'preserve_failed'}


def assert_parked(env, mid, sha, *, reason, stage, observed=None, ref=None):
    after = saved(mid)
    e = after['last_error']
    assert e['code'] == 'preserve_failed' and e['reason'] == reason and e['stage'] == stage
    assert e['expected_sha'] == sha and e['observed_sha'] == observed
    assert e['preserve_ref'] == (ref or f'refs/flowgate/preserve/{mid}')
    assert e['at'] == env.clock['now'] and e['instance_id'] == env.clock['instance']
    assert after['review_state'] == gs.REVIEW_STATE_RECONCILING
    assert after['reconciliation_kind'] == 'preserve_failed'
    assert 'preserve_failed' not in gs.RECONCILE_AUTO_RETRY_KINDS
    assert after['reconcile_next_at'] is None
    assert after['merge_commit'] == sha
    assert after['approval_attempt_id'] == 'attempt-1'
    assert after['apply_phase'] == 'committed_local'
    assert approval_intent.intent_of_context(after) == INTENT
    assert gs.db_git.get_session(mid)['status'] == 'open'
    return after


# ── normal creation ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('branch', [False, True], ids=['group', 'branch'])
def test_created_before_push_with_parents_context_reflog_and_audit(env, branch):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    out = publish(env, mid, branch=branch)
    assert out['result']['status'] == 'merged' and env.completed == [True]
    seq = names(env)
    assert seq.index('update-ref') < seq.index('<origin-sync>') < seq.index('<secret-load>') < seq.index('push')
    ref = f'refs/flowgate/preserve/{mid}'
    assert ref_value(r.work, ref) == m
    assert parents_of(r.work, ref) == [r.B, r.F]
    after = saved(mid)
    assert {k: after[k] for k in ('preserve_ref', 'preserve_sha', 'preserve_parents', 'preserved_at',
                                  'preserve_round', 'preserve_retain_until')} == {
        'preserve_ref': ref, 'preserve_sha': m, 'preserve_parents': [r.B, r.F],
        'preserved_at': AT, 'preserve_round': 0, 'preserve_retain_until': RETAIN}
    assert reflog(r.work, ref) == [f'{IDENTITY}|flowgate: preserve merge {mid}']
    audit = [e for e in after['ref_operations'] if e['operation'] == 'update-ref']
    assert audit == [dict(operation='update-ref', reason='preserve_merge_commit', at=AT,
                          instance_id='inst_A', returncode=0, status='completed')]
    assert git(['rev-parse', 'main'], r.bare) == m
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED


def approve_ready(env):
    r = env.repo
    git(['merge', '--no-ff', '--no-commit', 'feature'], r.work)
    snap = gs._freeze_commit_candidate(r.work, 'main')
    ctx = {'review_state': gs.REVIEW_STATE_PENDING, 'target_branch': 'main',
           approval_intent.INTENT_KEY: dict(INTENT), **snap}
    mid = open_session(env, ctx)
    env.monkeypatch.setattr(gs, '_review_session_of_owner', lambda *a, **kw: gs.db_git.get_session(mid))
    env.monkeypatch.setattr(gs, '_validate_review_changed_paths', lambda *a, **kw: [])
    return mid, snap['review_fingerprint']


def approve(env, mid, attempt, fingerprint):
    return gs.approve_merge_review(env.group, mid, attempt_id=attempt, review_fingerprint=fingerprint,
                                   authority='human')


def test_approval_end_to_end_and_same_attempt_replay_writes_nothing(env):
    r = env.repo
    mid, fp = approve_ready(env)
    out = approve(env, mid, 'att-1', fp)
    assert out['result']['status'] == 'merged'
    m = git(['rev-parse', 'HEAD'], r.work)
    ref = f'refs/flowgate/preserve/{mid}'
    assert ref_value(r.work, ref) == m and parents_of(r.work, m) == [r.B, r.F]
    seq = names(env)
    assert seq.index('commit') < seq.index('update-ref') < seq.index('push')
    env.commands.clear()
    again = approve(env, mid, 'att-1', fp)
    assert again['result']['status'] == 'already_applied'
    assert env.commands == []
    assert reflog(r.work, ref) == [f'{IDENTITY}|flowgate: preserve merge {mid}']
    assert git(['rev-parse', 'main'], r.bare) == m


# ── duplicate call ───────────────────────────────────────────────────────────

def test_second_call_is_same_without_write_and_keeps_preserved_at(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    ctx = saved(mid)
    assert gs._preserve_gate(mid, ctx, r.work) is None
    env.commands.clear()
    env.clock['now'] = LATER
    second = preserve.ensure_preserved(r.work, mid, ctx)
    assert second['ok'] and second['result'] == 'same'
    assert 'update-ref' not in names(env)
    assert gs._preserve_gate(mid, ctx, r.work) is None
    after = saved(mid)
    assert after['preserved_at'] == AT and after['preserve_retain_until'] == RETAIN
    assert 'update-ref' not in names(env)
    assert len(reflog(r.work, f'refs/flowgate/preserve/{mid}')) == 1
    assert [e['operation'] for e in after['ref_operations']] == ['update-ref']


# ── an existing ref at another SHA ───────────────────────────────────────────

@pytest.mark.parametrize('branch', [False, True], ids=['group', 'branch'])
def test_other_sha_is_never_overwritten_and_nothing_moves(env, branch):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    ref = f'refs/flowgate/preserve/{mid}'
    git(['update-ref', ref, r.B], r.work)
    out = publish(env, mid, branch=branch)
    assert out['result'] == PRESERVE_FAILED
    assert ref_value(r.work, ref) == r.B
    seq = names(env)
    for forbidden in ('push', '<origin-sync>', '<secret-load>', 'reset', 'update-ref', 'fetch'):
        assert forbidden not in seq
    assert_parked(env, mid, m, reason='ref_conflict', stage='create', observed=r.B)
    assert git(['rev-parse', 'HEAD'], r.work) == m == git(['rev-parse', 'main'], r.work)
    assert git(['rev-parse', 'main'], r.bare) == r.B
    assert env.completed == []


def test_same_attempt_replay_returns_the_parked_state(env):
    r = env.repo
    mid, fp = approve_ready(env)
    git(['update-ref', f'refs/flowgate/preserve/{mid}', r.B], r.work)
    out = approve(env, mid, 'att-1', fp)
    assert out['result'] == PRESERVE_FAILED
    env.commands.clear()
    again = approve(env, mid, 'att-1', fp)
    assert again['result'] == {'status': 'reconciling', 'review_state': 'reconciling',
                               'reconciliation_kind': 'preserve_failed'}
    assert env.commands == []


# ── real creation failure ────────────────────────────────────────────────────

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


# ── validation failures ──────────────────────────────────────────────────────

def _read_error(args):
    if args[:3] == ['rev-parse', '--verify', '--quiet'] and args[3].startswith('refs/flowgate/preserve/'):
        return subprocess.CompletedProcess(['git', *args], 128, '', 'fatal: unable to read ref\nlast line')
    return None


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


# ── reconcile gate ───────────────────────────────────────────────────────────

def unknown_session(env):
    """preserve → push timeout: the T#1 reconciling/push_remote_unknown state."""
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    env.hooks.append(on('push', push_timeout))
    out = publish(env, mid)
    env.hooks.clear()
    assert out['result']['reconciliation_kind'] == 'push_remote_unknown'
    before = saved(mid)
    assert before['last_error']['code'] == 'push_remote_unknown'
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    env.commands.clear()
    return mid, m, before


def test_reconcile_gate_recreates_a_deleted_ref_then_keeps_the_existing_branch(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    ref = f'refs/flowgate/preserve/{mid}'
    git(['update-ref', '-d', ref, m], r.work)
    env.remote(r.B)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'pending_review'
    assert ref_value(r.work, ref) == m
    seq = names(env)
    assert seq.index('update-ref') < seq.index('reset') and seq.count('reset') == 1
    assert len(env.probes) == 1
    after = saved(mid)
    assert after['review_state'] == gs.REVIEW_STATE_PENDING and after['merge_commit'] is None
    assert after['preserve_history'][-1]['sha'] == m
    assert after['preserve_history'][-1]['closed_reason'] == 'reconcile_remote_unchanged'
    assert git(['rev-parse', 'HEAD'], r.work) == r.B


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


# ── reset gate ───────────────────────────────────────────────────────────────

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
    assert out['result'] == PRESERVE_FAILED
    assert 'reset' not in names(env) and 'fetch' not in names(env)
    assert_parked(env, mid, m, reason='ref_conflict', stage='reset_gate', observed=r.B)
    assert git(['rev-parse', 'HEAD'], r.work) == m


@pytest.mark.parametrize('branch', [False, True], ids=['group', 'branch'])
def test_rejected_reset_keeps_existing_flow_and_the_ref(env, branch):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    env.hooks.append(on('push', push_rejected))
    out = publish(env, mid, branch=branch)
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
    r = env.repo
    mid, m, before = unknown_session(env)
    ref = f'refs/flowgate/preserve/{mid}'

    def damage_then_unchanged(a):
        git(['update-ref', ref, r.B, m], r.work)
        return r.B
    env.remote(damage_then_unchanged)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result'] == PRESERVE_FAILED
    assert 'reset' not in names(env)
    after = saved(mid)
    assert after['last_error']['stage'] == 'reset_gate' and after['last_error']['reason'] == 'ref_conflict'
    assert after['last_error']['prior_push_error'] == before['last_error']
    assert after['merge_commit'] == m and git(['rev-parse', 'HEAD'], r.work) == m


def test_remote_unchanged_reset_keeps_the_ref_and_history(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    env.remote(r.B)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out == {'ok': True, 'result': {'status': 'pending_review'}}
    after = saved(mid)
    assert names(env).count('reset') == 1 and 'update-ref' not in names(env)
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m
    assert [h['sha'] for h in after['preserve_history']] == [m] and after['preserve_round'] == 1


# ── re-approval round ────────────────────────────────────────────────────────

def test_rejected_then_reapproved_pins_round_one_with_previous_commit_as_parent(env):
    r = env.repo
    other = r.tmp / 'other'
    git(['clone', str(r.bare), str(other)], r.tmp)
    git(['config', 'user.name', 'other'], other)
    git(['config', 'user.email', 'other@local'], other)
    (other / 'c.txt').write_text('concurrent\n', encoding='utf-8')
    git(['add', '.'], other)
    git(['commit', '-m', 'concurrent'], other)
    git(['push', 'origin', 'main'], other)
    c = git(['rev-parse', 'HEAD'], other)
    mid, fp = approve_ready(env)
    out = approve(env, mid, 'att-1', fp)
    assert out['result']['status'] == 're_review', out
    first = saved(mid)
    r0 = f'refs/flowgate/preserve/{mid}'
    m0 = first['preserve_history'][0]['sha']
    assert ref_value(r.work, r0) == m0 and parents_of(r.work, m0) == [r.B, r.F]
    out = approve(env, mid, 'att-2', out['result']['review_fingerprint'])
    assert out['result']['status'] == 'merged', out
    m1 = git(['rev-parse', 'HEAD'], r.work)
    r1 = f'refs/flowgate/preserve/{mid}-r1'
    assert ref_value(r.work, r1) == m1
    assert ref_value(r.work, r0) == m0
    assert parents_of(r.work, m1) == [c, m0]
    after = saved(mid)
    assert after['preserve_ref'] == r1 and after['preserve_round'] == 1 and after['preserve_sha'] == m1
    assert after['preserve_parents'] == [c, m0]
    assert len(after['preserve_history']) == 1 and after['preserve_history'][0]['ref'] == r0
    assert git(['rev-parse', 'main'], r.bare) == m1


def test_a_different_commit_for_the_open_round_is_a_conflict_not_a_round(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m, preserve_sha='d' * 40,
                                     preserve_ref='refs/flowgate/preserve/1'))
    out = publish(env, mid)
    assert out['result'] == PRESERVE_FAILED
    after = saved(mid)
    assert after['last_error']['reason'] == 'ref_conflict' and after.get('preserve_round', 0) == 0
    assert preserve_refs(r.work) == {}


# ── process restart re-entry ─────────────────────────────────────────────────

class _Crash(BaseException):
    """A process death between the ref write and the context write."""


def test_crash_after_update_ref_before_context_write_is_recovered_as_same(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m, review_state=gs.REVIEW_STATE_RECONCILING,
                                     reconciliation_kind='push_remote_unknown'))
    real_record = preserve.record_preserved

    def crash(*a, **kw):
        raise _Crash()
    env.monkeypatch.setattr(preserve, 'record_preserved', crash)
    env.remote(None)
    with pytest.raises(_Crash):
        gs.reconcile_push_session(mid, trigger='server_startup')
    ref = f'refs/flowgate/preserve/{mid}'
    assert ref_value(r.work, ref) == m
    assert 'preserve_sha' not in saved(mid)
    env.monkeypatch.setattr(preserve, 'record_preserved', real_record)
    env.commands.clear()
    env.clock.update(now=LATER, instance='inst_B')
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'retry_scheduled'
    assert 'update-ref' not in names(env)
    after = saved(mid)
    assert after['preserve_sha'] == m and after['preserve_ref'] == ref and after['preserved_at'] == LATER
    assert len(reflog(r.work, ref)) == 1


def _only(env, mid):
    env.monkeypatch.setattr(gs.db_git, 'list_open_sessions',
                            lambda: [s for s in [gs.db_git.get_session(mid)] if s and s['status'] == 'open'])
    env.monkeypatch.setattr(gs, '_start_sweep_daemon', lambda: None)


def test_push_timeout_then_restart_on_another_instance_reuses_the_ref(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    _only(env, mid)
    env.clock.update(now=LATER, instance='inst_B')
    env.remote(lambda a: git(['rev-parse', 'main'], r.bare))
    gs.startup_recovery()
    after = saved(mid)
    assert len(env.probes) == 1
    assert after['review_state'] == gs.REVIEW_STATE_PENDING  # the existing remote-unchanged branch
    assert names(env).count('reset') == 1 and 'update-ref' not in names(env)
    ref = f'refs/flowgate/preserve/{mid}'
    assert ref_value(r.work, ref) == m and len(reflog(r.work, ref)) == 1
    assert after['preserve_history'][0]['preserved_at'] == AT
    assert after['ref_operations'][-1]['instance_id'] == 'inst_B'


def test_preserve_failed_survives_startup_and_sweeps_untouched(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    git(['update-ref', f'refs/flowgate/preserve/{mid}', r.B], r.work)
    publish(env, mid)
    parked = saved(mid)
    _only(env, mid)
    env.remote(r.B)
    env.commands.clear()
    for _ in range(2):
        gs.startup_recovery()
        gs.merge_session_sweep()
    after = saved(mid)
    assert gs.db_git.get_session(mid)['status'] == 'open'
    assert after['review_state'] == gs.REVIEW_STATE_RECONCILING
    assert after['reconciliation_kind'] == 'preserve_failed'
    assert after['merge_commit'] == m and approval_intent.intent_of_context(after) == INTENT
    assert after['last_error'] == parked['last_error']
    assert env.probes == []
    for forbidden in ('reset', 'push', 'update-ref', 'merge'):
        assert forbidden not in names(env)
    assert git(['rev-parse', 'HEAD'], r.work) == m


# ── lifetime ─────────────────────────────────────────────────────────────────

def test_ref_outlives_push_success_reconcile_completion_and_close(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    publish(env, mid)
    gs.db_git.close_session(mid, 'done')
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m


def test_ref_outlives_reconcile_completion(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    env.remote(m)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'merged'
    gs.db_git.close_session(mid, 'done')
    assert ref_value(r.work, f'refs/flowgate/preserve/{mid}') == m


def test_release_is_explicit_and_guarded(env):
    r = env.repo
    m = merge_commit(r)
    mid = open_session(env, approved(r, m))
    ref = f'refs/flowgate/preserve/{mid}'
    publish(env, mid)
    late = '2026-12-09T10:00:00+09:00'
    # still open
    assert preserve.release_preserved_ref(r.work, ref, m, now=late) == {
        'released': False, 'reason': 'session_not_completed'}
    gs.db_git.close_session(mid, 'done')
    assert preserve.release_preserved_ref(r.work, ref, m, now='2026-12-01T10:00:00+09:00')['reason'] == \
        'retention_not_elapsed'
    git(['update-ref', 'refs/remotes/origin/main', r.B], r.work)
    assert preserve.release_preserved_ref(r.work, ref, m, now=late)['reason'] == 'remote_missing_commit'
    git(['update-ref', 'refs/remotes/origin/main', m], r.work)
    assert preserve.release_preserved_ref(r.work, ref, r.B, now=late)['reason'] == 'ref_conflict'
    assert ref_value(r.work, ref) == m
    assert preserve.release_preserved_ref(r.work, ref, m, now=late) == {'released': True, 'reason': 'released'}
    assert ref_value(r.work, ref) is None


@pytest.mark.parametrize('outcome', ['released', 'delete_failed'])
def test_release_delete_is_audited_on_the_session(env, outcome):
    """T#1 §5.3 / §3.1-4: the explicit delete is a session ref move. It runs through the
    session audit runner with the CAS kept, and the re-read sqlite row records it —
    success or a real nonzero failure — without disturbing the earlier push evidence.
    The caller's live context carries the same entry, so its later write keeps it."""
    r = env.repo
    mid, m, before = unknown_session(env)
    env.remote(m)
    assert gs.reconcile_push_session(mid, trigger='server_startup')['result']['status'] == 'merged'
    gs.db_git.close_session(mid, 'done')
    ref = f'refs/flowgate/preserve/{mid}'
    git(['update-ref', 'refs/remotes/origin/main', m], r.work)
    push_error = saved(mid)['last_error']
    assert push_error == before['last_error'] and push_error['code'] == 'push_remote_unknown'
    if outcome == 'delete_failed':
        gitdir = Path(git(['rev-parse', '--absolute-git-dir'], r.work))
        (gitdir / 'refs' / 'flowgate' / 'preserve' / f'{mid}.lock').write_text('', encoding='utf-8')
    late = '2026-12-09T10:00:00+09:00'
    env.clock.update(now=late, instance='inst_C')
    env.commands.clear()
    ctx = saved(mid)
    out = preserve.release_preserved_ref(r.work, ref, m, now=late, context=ctx)
    assert [c for c in env.commands if c[:2] == ['update-ref', '-d']] == [['update-ref', '-d', ref, m]]
    if outcome == 'released':
        assert out == {'released': True, 'reason': 'released'}
        assert ref_value(r.work, ref) is None
        rc = 0
    else:
        assert out == {'released': False, 'reason': 'ref_delete_failed'}
        assert ref_value(r.work, ref) == m
        rc = git_rc(['update-ref', '-d', ref, m], r.work).returncode  # same real failure
        assert rc != 0
    after = saved(mid)
    entry = after['ref_operations'][-1]
    assert entry == {'operation': 'update-ref', 'reason': 'release_preserve_ref', 'at': late,
                     'instance_id': 'inst_C', 'returncode': rc, 'status': 'completed'}
    assert after['last_error'] == push_error
    assert after['review_state'] == gs.REVIEW_STATE_COMPLETED
    assert ctx['ref_operations'][-1] == entry
    ctx['caller_note'] = 'written later by the caller'
    gs.db_git.set_session_context(mid, ctx)
    assert saved(mid)['ref_operations'][-1] == entry


def test_no_automatic_path_deletes_preserve_refs():
    """§5.2/§5.4: only preserve.release_preserved_ref deletes, and nothing calls it. No
    other product module deletes under the prefix or sweeps the whole refs/flowgate/."""
    server = Path(gs.__file__).resolve().parents[3]
    offenders = []
    for top in ('modules', 'flowgate'):
        for path in (server / top).rglob('*.py'):
            if path.name == 'preserve.py':
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
            if 'release_preserved_ref' in text or 'PRESERVE_REF_PREFIX' in text:
                offenders.append(str(path))
            for quote in ('"', "'"):
                if f'refs/flowgate/{quote}' in text or f'refs/flowgate/preserve/{quote}' in text:
                    offenders.append(str(path))
    assert offenders == []


# ── gc defence ───────────────────────────────────────────────────────────────

def _expire_and_gc(repo):
    git(['reflog', 'expire', '--expire=now', '--all'], repo)
    git(['gc', '--prune=now', '--quiet'], repo)


def _exists(repo, sha):
    return git_rc(['cat-file', '-e', f'{sha}^{{commit}}'], repo).returncode == 0


def test_preserve_ref_alone_keeps_commit_and_parents_through_external_gc(env):
    r = env.repo
    mid, m, before = unknown_session(env)
    env.remote(r.B)
    assert gs.reconcile_push_session(mid, trigger='server_startup')['result']['status'] == 'pending_review'
    assert git(['rev-parse', 'HEAD'], r.work) == r.B
    git(['update-ref', '-d', 'ORIG_HEAD'], r.work)
    git(['branch', '-D', 'feature'], r.work)
    _expire_and_gc(r.work)
    for sha in (m, r.B, r.F):
        assert _exists(r.work, sha)
    # Control: without the preservation ref the same history is collected.
    git(['update-ref', '-d', f'refs/flowgate/preserve/{mid}', m], r.work)
    _expire_and_gc(r.work)
    assert not _exists(r.work, m)
    assert not _exists(r.work, r.F)


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


# ── unchanged contracts ──────────────────────────────────────────────────────

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
    assert out['result'] == {'status': 'reconciling', 'review_state': gs.REVIEW_STATE_RECONCILING,
                             'reconciliation_kind': 'push_remote_unknown'}
    after = saved(mid)
    assert after['last_error'] == {'code': 'push_exception', 'detail': 'unexpected_error'}
    assert after['reconcile_attempt_count'] == 0 and after['reconcile_next_at'] == NEXT_AT
    m = git(['rev-parse', 'HEAD'], r.work)
    assert after['merge_commit'] == m and after['preserve_sha'] == m


@pytest.mark.parametrize('observed,status', [
    ('merge', 'merged'), ('expected', 'pending_review'),
    ('third', 'manual_reconciliation_needed'), (None, 'retry_scheduled'),
])
def test_reconcile_four_branches_are_unchanged_behind_the_gate(env, observed, status):
    r = env.repo
    mid, m, before = unknown_session(env)
    value = {'merge': m, 'expected': r.B, 'third': 'd' * 40, None: None}[observed]
    env.remote(value)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == status
    after = saved(mid)
    assert after['reconcile_attempt_count'] == 1
    assert len(env.probes) == (3 if observed is None else 1)
    assert names(env).count('reset') == (1 if observed == 'expected' else 0)
    assert 'update-ref' not in names(env)  # gate reused the ref (`same`)
    assert after['last_error'] == before['last_error']
    if observed == 'expected':
        assert after['merge_commit'] is None and after['review_state'] == gs.REVIEW_STATE_PENDING
        assert 'reconcile_next_at' not in after and 'reconciliation_kind' not in after
    elif observed == 'third':
        assert after['reconciliation_kind'] == 'push_remote_third' and after['reconcile_next_at'] is None
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
