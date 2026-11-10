"""NR 0003 section 4 incident reproduction promoted to an expected-behavior regression.

The original scratch reproduction is absent, so this is rewritten from NR section
4.1's procedure. Reuse 0555's actual final-approval orchestrator, migrated SQLite,
local bare origin and live registry. Only push results are injected at _run_git;
all resolution, claims, sweeps, ref preservation and approval transactions are real.
Auth and remote-hook rejection intentionally require explicit recovery even after
the external cause disappears (T#5); automatic sweeps must never bypass that hold.
"""
import copy
import json
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.modules.flow_gate.test_final_approval_conflict_intent_0555 import (
    project, origin_repo, patch_store, tmp_db, live_instance, PROJECT, USER, _git,
    _seed_final_approval_group, _make_conflict_material, _final_approve, _resolve,
    _review_status, _intent, job_of,
)
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import approval_intent, instance_registry, recovery
from modules.flow_gate.db import documents, git_concurrency as gc
from modules.flow_gate.db.connection import get_store


FAILURES = {
    'unclassified': (128, 'RPC failed: connection reset by peer', 'unknown'),
    'timeout': (-1, 'timeout_expired', 'timeout'),
    'auth': (128, 'fatal: Authentication failed', 'auth'),
    'rejected': (1, ' ! [remote rejected] main -> main (pre-receive hook declined)', 'unknown'),
}
SECRET = 'ghp_' + 'A' * 36
PASSWORD = 'repro_password_never_persist'
APP_IDENT = 'FlowGate <flowgate@localhost>'
PAST = '2000-01-01T00:00:00+00:00'


def context(mid):
    return gs.db_git.session_context(gs.db_git.get_session(mid))


def rev(root, ref):
    proc = subprocess.run(['git', 'rev-parse', '--verify', ref], cwd=root,
                          capture_output=True, text=True, encoding='utf-8')
    return proc.stdout.strip() if proc.returncode == 0 else None


def due(mid):
    ctx = context(mid)
    ctx['reconcile_next_at'] = PAST
    gs.db_git.set_session_context(mid, ctx)


def snapshot(group, root):
    """Every unrelated row and the actual work branch, rather than selected fields."""
    store = get_store()
    return {
        'branch': rev(root, 'refs/heads/' + group.replace('.', '_')),
        'sessions': copy.deepcopy(gs.db_git.sessions_by_group(group)),
        'documents': copy.deepcopy(store._fetch_all(
            'SELECT * FROM documents WHERE group_id = ? ORDER BY doc_id', [group])),
        'jobs': copy.deepcopy(store._fetch_all(
            'SELECT * FROM operation_job WHERE group_id = ? ORDER BY job_id', [group])),
        'state': copy.deepcopy(gs.db_git.get_state(group)),
    }


@pytest.fixture
def flow(origin_repo, monkeypatch, caplog):
    group = f'{PROJECT}.default.repro_{uuid.uuid4().hex[:10]}'
    root_id, ac_id = _seed_final_approval_group(group)
    marker = uuid.uuid4().hex
    _make_conflict_material(group, origin_repo, 'shared.py',
                            f'"group {marker}"\n', f'"main {marker}"\n')
    status, out = _final_approve(ac_id)
    assert status == 200 and out['git']['result']['status'] == 'conflict', out
    mid = out['git']['result']['merge_id']
    root = gs.resolve_conflict_src_root(group, mid)
    assert rev(root, 'MERGE_HEAD') is not None
    # Reproduce the incident's repository identity, BEFORE submitting resolution.
    _git(['config', 'user.name', 'merge'], cwd=root)
    _git(['config', 'user.email', 'merge@local'], cwd=root)
    _git(['config', 'core.logAllRefUpdates', 'always'], cwd=root)
    for key in ('GIT_COMMITTER_NAME', 'GIT_COMMITTER_EMAIL', 'GIT_COMMITTER_DATE'):
        monkeypatch.delenv(key, raising=False)
    resolved = _resolve(group, mid, 'shared.py', f'"group {marker}"\n"main {marker}"\n')
    assert resolved['status'] == 'resolved_pending_review'
    intent = copy.deepcopy(_intent(mid))
    base = rev(origin_repo['bare'], 'main')
    branch = group.replace('.', '_')
    branch_tip = rev(root, branch)
    assert branch_tip and branch_tip != base
    # An unrelated group has its own real branch, documents, intent, session and
    # active operation job. The sweep's candidate list contains only our session;
    # downstream helpers still have access to the whole real database.
    other = group + '_other'
    other_root, other_ac = _seed_final_approval_group(other)
    assert gs.ensure_worktree(PROJECT, 'default', other) == 'ok'
    other_intent = approval_intent.build_intent(group_id=other, ac_doc_id=other_ac,
        requested_by=USER['user_id'], git_action='merge', approval_intent_id=str(uuid.uuid4()))
    other_mid = gs.db_git.create_session(other, [], finalize_action='merge', context={
        'review_state': 'reconciling', 'recovery_hold': {'code': 'test_other_owner'},
        approval_intent.INTENT_KEY: other_intent})
    gs._set_status(other, 'conflict', merge_id=other_mid)
    from modules.flow_gate.db import operation_job as jobs
    source_job = job_of(group)
    foreign_job = {key: source_job[key] for key in jobs._INSERT_COLUMNS if key in source_job}
    instance_a = instance_registry.current_instance_id()
    instance_b = 'inst_repro_' + uuid.uuid4().hex
    gc.insert_instance(instance_b, '0153-isolated-peer', 4242, None, '2099-01-01T00:00:00+00:00')
    foreign_job.update(job_id='opj_' + uuid.uuid4().hex[:24], group_id=other,
        status='freezing', phase='start', request_key='other_repro',
        request_key_hash=uuid.uuid4().hex * 2, lease_owner=instance_b,
        lease_token='isolated-other-lease', lease_until='2099-01-01T00:00:00+00:00')
    payload = json.loads(foreign_job['payload'])
    payload['doc_id'] = other_ac
    foreign_job['payload'] = json.dumps(payload)
    jobs.insert_job(foreign_job)
    assert jobs.set_group_freeze_claim(other, foreign_job['job_id'], 'freezing', gs.now_iso()) == 1
    before_other = snapshot(other, root)
    before_job = copy.deepcopy(job_of(group))
    commands, writes, consumes = [], [], []
    actual, save, consume = gs._run_git, gs.db_git.set_session_context, approval_intent.consume_intent
    fault = {'kind': None, 'enabled': False, 'race': None}
    def run(args, **kwargs):
        commands.append(list(args))
        if args[0] == 'push':
            if fault['race']:
                fault['race']()
            if fault['enabled']:
                rc, error, cause = FAILURES[fault['kind']]
                # timeout sentinel must stay exact; other types carry fake secrets.
                if fault['kind'] != 'timeout':
                    error += f' https://u:{PASSWORD}@example.invalid/repo {SECRET}'
                result = subprocess.CompletedProcess(args, rc, '', error)
                result.cause = cause
                return result
        return actual(args, **kwargs)
    def watched(mid_arg, ctx):
        result = save(mid_arg, ctx)
        if mid_arg == mid:
            writes.append(copy.deepcopy(context(mid)))
        return result
    def consumed(mid_arg, intent_id):
        result = consume(mid_arg, intent_id)
        if mid_arg == mid and result:
            consumes.append(copy.deepcopy(context(mid)))
        return result
    monkeypatch.setattr(gs, '_run_git', run)
    monkeypatch.setattr(gs.db_git, 'set_session_context', watched)
    monkeypatch.setattr(approval_intent, 'consume_intent', consumed)
    f = SimpleNamespace(group=group, mid=mid, root=root, root_id=root_id, ac_id=ac_id,
        origin=origin_repo, base=base, branch=branch, branch_tip=branch_tip,
        fingerprint=resolved['review_fingerprint'], intent=intent, other=other,
        other_before=before_other, job_before=before_job, commands=commands,
        writes=writes, consumes=consumes, fault=fault, instance_a=instance_a,
        instance_b=instance_b, caplog=caplog, original=actual)
    try:
        yield f
        assert snapshot(other, root) == before_other
        # Successful finalization intentionally removes its own completed slot;
        # every pending/failed path keeps its work branch exactly unchanged.
        if gs.db_git.get_session(mid)['status'] == 'done':
            assert rev(root, branch) is None
            assert _git(['merge-base', branch_tip, context(mid)['merge_commit']], cwd=root).strip() == branch_tip
        else:
            assert rev(root, branch) == branch_tip
        assert job_of(group) == before_job
    finally:
        gc.set_current_instance_id(instance_a)
        instance_registry.heartbeat_once()
        # Isolate successive parameters AFTER all preservation assertions. A held
        # candidate remains reachable through its real preserve ref, while the
        # shared 0555 base can host the next test's independent conflict.
        for test_mid in (mid, other_mid):
            if gs.db_git.get_session(test_mid)['status'] == 'open':
                gs.db_git.close_session(test_mid, 'aborted')
        _git(['fetch', 'origin'], cwd=root)
        _git(['reset', '--hard', 'origin/main'], cwd=root)


def approve_failed(f, kind):
    f.fault.update(kind=kind, enabled=True)
    attempt = str(uuid.uuid4())
    response = gs.approve_merge_review(f.group, f.mid, attempt_id=attempt,
        review_fingerprint=f.fingerprint, authority='human')
    ctx = context(f.mid)
    sha = ctx['merge_commit']
    assert gs.db_git.get_session(f.mid)['status'] == 'open'
    assert rev(f.root, 'HEAD') == rev(f.root, ctx['preserve_ref']) == sha
    assert rev(f.root, 'MERGE_HEAD') is None
    assert _git(['rev-list', '--parents', '-n', '1', sha], cwd=f.root).split()[1:] == [f.base, f.branch_tip]
    assert rev(f.root, sha + '^{tree}') == ctx['snapshot_tree']
    assert rev(f.origin['bare'], 'main') == f.base
    assert ctx['approval_attempt_id'] == attempt
    held = kind in ('auth', 'rejected')
    assert ctx['review_state'] == 'reconciling'
    assert ctx['reconciliation_kind'] == ('reconcile_auth_required' if held else 'push_remote_unknown')
    error = ctx['last_error']['prior_push_error'] if held else ctx['last_error']
    assert set(error) == {'code','returncode','cause','detail','local_sha','expected_remote_head','at','instance_id'}
    assert error['code'] == 'push_remote_unknown'
    assert (error['returncode'], error['cause']) == (FAILURES[kind][0], FAILURES[kind][2])
    assert error['local_sha'] == sha and error['expected_remote_head'] == f.base
    assert error['instance_id'] == f.instance_a
    assert datetime.fromisoformat(error['at']).utcoffset() is not None
    assert error['detail']
    exposed = json.dumps([context(f.mid), response, recovery.get(f.group, f.mid)], ensure_ascii=False) + f.caplog.text
    assert SECRET not in exposed and PASSWORD not in exposed
    assert _intent(f.mid) == f.intent
    return sha, ctx['preserve_ref']


def sweep(f, instance):
    gc.set_current_instance_id(instance)
    assert instance_registry.heartbeat_once() is True
    assert instance_registry.instance_state(f.instance_a) == instance_registry.ALIVE
    assert instance_registry.instance_state(f.instance_b) == instance_registry.ALIVE
    gs.merge_session_sweep(sessions=[gs.db_git.get_session(f.mid)])


def invariants(f, sha, ref, *, done=False, integrated=False):
    ctx = context(f.mid)
    assert ctx['merge_commit'] == sha and rev(f.root, ref) == sha
    assert _git(['cat-file', '-t', ref], cwd=f.root).strip() == 'commit'
    assert rev(f.root, 'MERGE_HEAD') is None
    assert ctx.get('review_state') != 'resolved_pending_review'
    assert ctx.get(approval_intent.DISCARDED_KEY) is None
    assert gs.db_git.get_session(f.mid)['status'] == ('done' if done else 'open')
    assert gs.db_git.get_state(f.group)['status'] == ('merged' if done else 'conflict')
    assert _review_status(f.ac_id) == ('approved' if done else 'pending_review')
    assert _review_status(f.root_id) == ('wf_done' if done else 'wf_in_progress')
    assert len(f.consumes) == int(done)
    assert _intent(f.mid) == (None if done else f.intent)
    if done:
        assert ctx[approval_intent.CONSUMED_KEY]['approval_intent_id'] == f.intent['approval_intent_id']
        if not integrated:
            assert rev(f.origin['bare'], 'main') == rev(f.root, 'HEAD') == sha
            assert _git(['rev-list', '--count', f.base+'..main'], cwd=f.origin['bare']).strip() == '2'
    else:
        assert rev(f.root, 'HEAD') == sha
    assert not any(c[:3] == ['reset', '--hard', 'ORIG_HEAD'] for c in f.commands)
    assert not any(a in ('--force', '-f') or a.startswith('+') for c in f.commands if c[0] == 'push' for a in c)
    assert sum('commit' in c for c in f.commands) == 1
    assert not any(c[:2] == ['merge', '--abort'] for c in f.commands)
    assert not any(w.get('review_state') in ('resolved_pending_review','resolver','re_review') for w in f.writes)
    history = _git(['reflog', 'show', '--format=%gn <%ge>|%gs', 'HEAD'], cwd=f.root)
    assert 'reset: moving to ORIG_HEAD' not in history
    assert 'orphan_recovered' not in json.dumps(ctx) + f.caplog.text
    assert 'final_approval_intent_discarded' not in json.dumps(ctx) + f.caplog.text
    assert _git(['reflog', 'show', '-1', '--format=%gn <%ge>', ref], cwd=f.root).strip() == APP_IDENT
    for line in history.splitlines():
        if any(reason in line for reason in ('commit (merge):', 'flowgate:')):
            assert line.startswith(APP_IDENT + '|'), line


@pytest.mark.parametrize('kind', list(FAILURES))
@pytest.mark.parametrize('duration', ['transient', 'persistent'])
def test_final_approval_push_failure_survives_two_live_instance_sweeps(flow, kind, duration):
    f = flow
    sha, ref = approve_failed(f, kind)
    held = kind in ('auth', 'rejected')
    f.fault['enabled'] = duration == 'persistent'
    if not held:
        due(f.mid)
    pushes_before = sum(c[0] == 'push' for c in f.commands)
    sweep(f, f.instance_a)
    done = duration == 'transient' and not held
    invariants(f, sha, ref, done=done)
    if held:
        assert sum(c[0] == 'push' for c in f.commands) == pushes_before
        assert context(f.mid)['reconcile_next_at'] is None
        assert context(f.mid)['reconcile_attempt_count'] == 0
    elif not done:
        ctx = context(f.mid)
        assert ctx['reconcile_cycle_count'] == ctx['reconcile_attempt_count'] == 1
        assert datetime.fromisoformat(ctx['reconcile_next_at']) > datetime.fromisoformat(gs.now_iso())
        assert ctx['reconciliation_kind'] == 'push_remote_unknown'
        assert rev(f.origin['bare'], 'main') == f.base
    after_a = copy.deepcopy(context(f.mid))
    calls_a = len(f.commands)
    sweep(f, f.instance_b)
    invariants(f, sha, ref, done=done)
    # B must respect A's terminal result, auth hold or future backoff.
    assert context(f.mid) == after_a
    assert not any(c[0] == 'push' for c in f.commands[calls_a:])
    if held and duration == 'transient':
        view = recovery.get(f.group, f.mid)['result']
        recovery.mutate(f.group, f.mid, 'retry', actor=USER['user_id'],
            request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'],
            approval_attempt_id=view['approval_attempt_id'])
        invariants(f, sha, ref, done=True)


def advance_remote(f, label):
    """A second publisher actually advances the local bare origin, without mocks."""
    seed = f.origin['seedwt']
    _git(['pull', '--ff-only', 'origin', 'main'], cwd=seed)
    (seed / ('other_' + label + '.txt')).write_text(label + '\n', encoding='utf-8')
    _git(['add', '-A'], cwd=seed)
    _git(['commit', '-m', 'other publisher ' + label], cwd=seed)
    _git(['push', 'origin', 'main'], cwd=seed)
    return rev(seed, 'HEAD')


def test_remote_ahead_preserves_both_histories_and_other_group_rows(flow):
    f = flow
    sha, ref = approve_failed(f, 'unclassified')
    f.fault['enabled'] = False
    other = advance_remote(f, 'ahead')
    due(f.mid)
    sweep(f, f.instance_a)
    tip = rev(f.origin['bare'], 'main')
    for ancestor in (sha, other):
        assert _git(['merge-base', ancestor, tip], cwd=f.root).strip() == ancestor
    assert len(context(f.mid)['integration_commits']) == 1
    invariants(f, sha, ref, done=True, integrated=True)
    sweep(f, f.instance_b)
    invariants(f, sha, ref, done=True, integrated=True)


def assert_pending_safety(f):
    """Failure paths keep the same real rows/branch and never reopen the resolver."""
    ctx = context(f.mid)
    assert snapshot(f.other, f.root) == f.other_before
    assert rev(f.root, f.branch) == f.branch_tip
    assert job_of(f.group) == f.job_before
    assert gs.db_git.get_state(f.group)['status'] == 'conflict'
    assert gs.db_git.get_session(f.mid)['status'] == 'open'
    assert _review_status(f.ac_id) == 'pending_review'
    assert _review_status(f.root_id) == 'wf_in_progress'
    assert _intent(f.mid) == f.intent and not f.consumes
    assert ctx.get(approval_intent.DISCARDED_KEY) is None
    assert rev(f.root, 'MERGE_HEAD') is None
    assert not any(w.get('review_state') in ('resolved_pending_review','resolver','re_review')
                   for w in f.writes)
    assert not any(c[:3] == ['reset', '--hard', 'ORIG_HEAD'] or c[:2] == ['merge', '--abort']
                   for c in f.commands)
    assert not any(a in ('--force', '-f') or a.startswith('+')
                   for c in f.commands if c[0] == 'push' for a in c)
    history = _git(['reflog', 'show', '--format=%gn <%ge>|%gs', 'HEAD'], cwd=f.root)
    assert 'reset: moving to ORIG_HEAD' not in history
    for line in history.splitlines():
        if any(reason in line for reason in ('commit (merge):', 'flowgate:')):
            assert line.startswith(APP_IDENT + '|'), line
    evidence = json.dumps([ctx, gs.db_git.get_session(f.mid)]) + f.caplog.text
    assert 'orphan_recovered' not in evidence
    assert 'final_approval_intent_discarded' not in evidence


def test_remote_advances_again_at_repush_until_bounded_user_intervention(flow):
    f = flow
    sha, ref = approve_failed(f, 'unclassified')
    f.fault['enabled'] = False
    tips = [advance_remote(f, 'first')]
    def race():
        tips.append(advance_remote(f, str(len(tips))))
    f.fault['race'] = race
    for cycle in range(gs.RECONCILE_MAX_CYCLES):
        due(f.mid)
        sweep(f, f.instance_a if cycle % 2 == 0 else f.instance_b)
        assert_pending_safety(f)
        if context(f.mid)['reconciliation_kind'] in gs.RECONCILE_USER_INTERVENTION_KINDS:
            break
    ctx = context(f.mid)
    assert len(tips) >= 3  # initial advance, then actual lease races after integration
    assert ctx['reconciliation_kind'] == 'reconcile_retry_exhausted'
    assert ctx['reconcile_cycle_count'] <= gs.RECONCILE_MAX_CYCLES
    assert len(ctx['integration_commits']) == gs.RECONCILE_MAX_INTEGRATIONS
    assert ctx['reconcile_next_at'] is None
    assert rev(f.origin['bare'], 'main') == tips[-1]
    assert rev(f.root, ref) == sha and ctx['merge_commit'] == sha
    for entry in ctx['integration_commits']:
        assert rev(f.root, entry['ref']) == entry['sha']
        assert _git(['merge-base', sha, entry['sha']], cwd=f.root).strip() == sha
    calls = len(f.commands)
    sweep(f, f.instance_b)
    assert not any(c[0] == 'push' for c in f.commands[calls:])
    assert _intent(f.mid) == f.intent and _review_status(f.ac_id) == 'pending_review'
    assert gs.db_git.get_session(f.mid)['status'] == 'open'
    assert not any(a in ('--force','-f') or a.startswith('+') for c in f.commands if c[0] == 'push' for a in c)


def test_real_preserve_ref_lock_keeps_commit_intent_and_other_group(flow):
    f = flow
    gitdir = Path(_git(['rev-parse', '--git-common-dir'], cwd=f.root).strip())
    if not gitdir.is_absolute():
        gitdir = f.root / gitdir
    lock = gitdir / 'refs' / 'flowgate' / 'preserve' / (str(f.mid) + '.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text('actual ref lock\n', encoding='utf-8')
    try:
        gs.approve_merge_review(f.group, f.mid, attempt_id=str(uuid.uuid4()),
            review_fingerprint=f.fingerprint, authority='human')
        ctx = context(f.mid)
        sha = ctx['merge_commit']
        assert rev(f.root, 'HEAD') == sha and rev(f.root, 'MERGE_HEAD') is None
        assert ctx['reconciliation_kind'] == 'preserve_failed'
        assert rev(f.root, f'refs/flowgate/preserve/{f.mid}') is None
        assert not any(c[0] in ('push','reset') for c in f.commands)
        assert rev(f.origin['bare'], 'main') == f.base
        sweep(f, f.instance_a)
        assert_pending_safety(f)
        sweep(f, f.instance_b)
        assert_pending_safety(f)
        assert context(f.mid)['merge_commit'] == sha
        assert gs.db_git.get_session(f.mid)['status'] == 'open'
        assert _intent(f.mid) == f.intent
        assert _review_status(f.ac_id) == 'pending_review'
        assert _review_status(f.root_id) == 'wf_in_progress'
        assert not f.consumes
        assert not any(c[0] in ('push','reset') for c in f.commands)
    finally:
        lock.unlink()
