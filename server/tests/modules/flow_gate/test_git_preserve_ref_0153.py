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
        # 0153 T#3: reconcile reads the remote through the detailed variant; only the
        # patch target changed (None still means "the query failed", here as network).
        def query(*a):
            probes.append(a)
            sha = observed(a) if callable(observed) else observed
            return {'sha': sha or None, 'cause': None if sha else 'network',
                    'returncode': 0 if sha else 128, 'detail': None if sha else 'network',
                    'stderr': ''}
        monkeypatch.setattr(gs, '_query_remote_ref_detail', query)
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


def assert_parked_approval_response(result, mid, kind):
    context = saved(mid)
    approval = {'approved': False, 'stage': 'pending', 'error': None,
                'approval_intent_id': 'intent-0153'}
    assert {key: value for key, value in result.items() if key != 'recovery'} == {
        'status': 'reconciling', 'review_state': 'reconciling',
        'reconciliation_kind': kind, 'merge_commit': context['merge_commit'][:7],
        'git_terminal': False, 'pushed': False, 'approval': approval,
        'approval_intent_id': 'intent-0153', 'approval_attempt_id': 'att-1',
        'error': None, 'deferred': True,
    }
    recovery = result['recovery']
    assert recovery['merge_id'] == mid and recovery['session_state'] == 'open'
    assert recovery['reconciliation_kind'] == kind
    assert recovery['requires_recovery'] is True and recovery['git_terminal'] is False
    assert recovery['approval'] == approval and recovery['approval_attempt_id'] == 'att-1'
    assert recovery['merge_commit'] == context['merge_commit']
    assert recovery['preserve_ref'] == context.get('preserve_ref')
    assert recovery['preserve_sha'] == context.get('preserve_sha')
    assert len(recovery['recovery_version']) == 64


def test_same_attempt_replay_returns_the_parked_state(env):
    r = env.repo
    mid, fp = approve_ready(env)
    git(['update-ref', f'refs/flowgate/preserve/{mid}', r.B], r.work)
    out = approve(env, mid, 'att-1', fp)
    assert out['result'] == PRESERVE_FAILED
    env.commands.clear()
    again = approve(env, mid, 'att-1', fp)
    assert_parked_approval_response(again['result'], mid, 'preserve_failed')
    assert env.commands == []


# ── real creation failure ────────────────────────────────────────────────────



# ── validation failures ──────────────────────────────────────────────────────

def _read_error(args):
    if args[:3] == ['rev-parse', '--verify', '--quiet'] and args[3].startswith('refs/flowgate/preserve/'):
        return subprocess.CompletedProcess(['git', *args], 128, '', 'fatal: unable to read ref\nlast line')
    return None






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










# ── reset gate ───────────────────────────────────────────────────────────────





def _remote_breaks_json(env):
    """0153 T#3: another group's commit on origin that makes the integration fail its
    syntax verification, so reconcile hands the merge back through the verified reset."""
    r = env.repo
    other = r.tmp / 'other_json'
    git(['clone', '-q', str(r.bare), str(other)], r.tmp)
    git(['config', 'user.name', 'other'], other)
    git(['config', 'user.email', 'other@local'], other)
    (other / 'data.json').write_text('{"broken": \n', encoding='utf-8')
    git(['add', '.'], other)
    git(['commit', '-q', '-m', 'broken json'], other)
    git(['push', '-q', 'origin', 'main'], other)
    return git(['rev-parse', 'HEAD'], other)






# ── re-approval round ────────────────────────────────────────────────────────





# ── process restart re-entry ─────────────────────────────────────────────────

class _Crash(BaseException):
    """A process death between the ref write and the context write."""




def _only(env, mid):
    env.monkeypatch.setattr(gs.db_git, 'list_open_sessions',
                            lambda: [s for s in [gs.db_git.get_session(mid)] if s and s['status'] == 'open'])
    env.monkeypatch.setattr(gs, '_start_sweep_daemon', lambda: None)






# ── lifetime ─────────────────────────────────────────────────────────────────











# ── gc defence ───────────────────────────────────────────────────────────────

def _expire_and_gc(repo):
    git(['reflog', 'expire', '--expire=now', '--all'], repo)
    git(['gc', '--prune=now', '--quiet'], repo)


def _exists(repo, sha):
    return git_rc(['cat-file', '-e', f'{sha}^{{commit}}'], repo).returncode == 0






# ── unchanged contracts ──────────────────────────────────────────────────────
