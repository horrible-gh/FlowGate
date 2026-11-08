"""0153 T#1: persistent push evidence and application ref attribution.

Real sqlite migrations come from 0115; local-path Git repositories deliberately
avoid its Windows file:// capability skip. Reconcile policy is characterized,
including its existing destructive reset, rather than fixed by this work unit.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, quote_plus

import pytest

from tests.modules.flow_gate.test_git_integration_0115 import patch_store, tmp_db  # noqa: F401
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import branch_merge, command, credentials, branch_merge_publish
from modules.flow_gate.db import projects, groups

PROJECT = 'evidence0153'
LOCAL = 'a' * 40
EXPECTED = 'b' * 40
AT = '2026-11-08T10:00:00+09:00'
RESET_AT = '2026-11-08T10:05:00+09:00'


@pytest.fixture(scope='module', autouse=True)
def project(patch_store):
    projects.create({'project_id': PROJECT, 'project_name': 'Evidence0153'})


@pytest.fixture
def session(monkeypatch, project):
    group = PROJECT + '.default.' + uuid.uuid4().hex[:12]
    groups.create({'group_id': group, 'project_id': PROJECT, 'module': 'default', 'title': 'evidence'})
    context = {'review_state': gs.REVIEW_STATE_PENDING, 'merge_commit': LOCAL,
               'expected_remote_head': EXPECTED, 'apply_phase': 'committed_local',
               'approval_attempt_id': 'attempt', 'review_fingerprint': 'fp',
               'snapshot_tree': 'c' * 40, 'keep_existing': {'intent': 'untouched'}}
    gs.db_git.register_worktree(group, PROJECT, group.replace('.', '_'))
    mid = gs.db_git.create_session(group, [], finalize_action='merge', context=context)
    monkeypatch.setattr(gs, 'now_iso', lambda: AT)
    monkeypatch.setattr(gs, 'current_instance_id', lambda: 'inst_A')
    monkeypatch.setattr(gs, '_seconds_from_now_iso', lambda seconds: '2026-11-08T10:10:00+09:00')
    monkeypatch.setattr(gs, 'ensure_origin_matches_config', lambda *args: None)
    return mid, context, gs.db_git.get_session(mid)


def saved(mid):
    return gs.db_git.session_context(gs.db_git.get_session(mid))


def push_result(rc=128, err='connection reset by peer'):
    return subprocess.CompletedProcess(['git', 'push'], rc, '', err)


def publish(session, monkeypatch, result, branch=False, secret=None):
    mid, ctx, row = session
    calls = []
    loads = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return result
    monkeypatch.setattr(gs, '_run_git', run)
    monkeypatch.setattr(gs, '_load_secret_for', lambda cfg: loads.append(True) or secret)
    if branch:
        ctx['branch_merge'] = {'push': True}
        out = branch_merge.publish_reviewed(mid, row, PROJECT, Path('.'), {}, 'main', ctx)
    else:
        out = gs._conditionally_push_or_reconcile(row['group_id'], mid, row, PROJECT,
                                                 Path('.'), {}, 'main', ctx)
    assert len(loads) == 1
    return out, calls


@pytest.mark.parametrize('branch', [False, True], ids=['group', 'branch'])
@pytest.mark.parametrize('rc,err,cause', [
    (-1, 'timeout_expired', 'timeout'),
    (128, 'fatal: Authentication failed for host', 'auth'),
    (128, 'Permission denied (publickey).', 'auth'),
    (128, 'connection reset by peer', 'unknown'),
    (128, 'Could not resolve host: example.invalid', 'network'),
    (128, 'HTTP 401', 'unknown'), (128, 'permission denied', 'unknown'),
    (128, '', 'unknown'), (128, 'first\nlast useful\n\n', 'unknown'),
    (128, 'x' * 600, 'unknown'),
], ids=['timeout', 'auth', 'publickey', 'unknown', 'dns', 'http', 'permission',
        'empty', 'multiline', 'long'])
def test_push_evidence_is_persisted(session, monkeypatch, branch, rc, err, cause):
    mid, ctx, row = session
    touched = row['touched_at']
    out, calls = publish(session, monkeypatch, push_result(rc, err), branch)
    after = saved(mid)
    e = after['last_error']
    assert set(e) == {'code', 'returncode', 'cause', 'detail', 'local_sha',
                      'expected_remote_head', 'at', 'instance_id'}
    assert e == dict(code='push_remote_unknown', returncode=rc, cause=cause,
                     detail=([line.strip() for line in err.splitlines() if line.strip()] or
                             ['git command failed'])[-1][:300],
                     local_sha=LOCAL, expected_remote_head=EXPECTED, at=AT, instance_id='inst_A')
    assert datetime.fromisoformat(e['at']).utcoffset() is not None
    assert after['review_state'] == gs.REVIEW_STATE_RECONCILING
    assert after['reconciliation_kind'] == 'push_remote_unknown'
    assert after['reconcile_attempt_count'] == 0
    assert after['reconcile_next_at'] == '2026-11-08T10:10:00+09:00'
    assert after['keep_existing'] == ctx['keep_existing']
    assert gs.db_git.get_session(mid)['touched_at'] == touched
    assert out['result'] == dict(status='reconciling', review_state=gs.REVIEW_STATE_RECONCILING,
                                 reconciliation_kind='push_remote_unknown')
    assert len(calls) == 1 and calls[0][0][0] == 'push'
    lease = ('refs/heads/main:' if branch else 'main:') + EXPECTED
    assert calls[0][0][1] == '--force-with-lease=' + lease
    json.dumps(after)


def test_empty_lease_and_unregistered_instance(session, monkeypatch):
    mid, ctx, row = session
    ctx['expected_remote_head'] = ''
    monkeypatch.setattr(gs, 'current_instance_id', lambda: None)
    _, calls = publish(session, monkeypatch, push_result())
    assert saved(mid)['last_error']['expected_remote_head'] is None
    assert saved(mid)['last_error']['instance_id'] is None
    assert '--force-with-lease=main' in calls[0][0]


SECRET = 'configured:/ credential+value'
PAT = 'ghp_' + 'Z' * 36
SECRETS = [SECRET, quote(SECRET, safe=''), quote_plus(SECRET, safe=''),
           'url-user', 'url-password', 'url-only-token', 'bearer-value123',
           'YmFzaWM6dmFsdWU=', 'query-value123', PAT]
LEAKS = [SECRET, quote(SECRET, safe=''), quote_plus(SECRET, safe=''),
         'https://url-user:url-password@example.invalid/x',
         'https://url-only-token@example.invalid/x',
         'Authorization: Bearer bearer-value123',
         'Proxy-Authorization: Basic YmFzaWM6dmFsdWU=',
         'https://example.invalid/x?access_token=query-value123&ok=1', PAT]


@pytest.mark.parametrize('leak', LEAKS + ['\n'.join(LEAKS)],
    ids=['configured', 'encoded', 'encoded-plus', 'url-userinfo', 'url-token',
         'bearer', 'basic', 'query-token', 'pat', 'multiline'])
@pytest.mark.parametrize('branch', [False, True])
def test_secrets_never_reach_context_or_output(session, monkeypatch, caplog, leak, branch):
    mid, ctx, row = session
    events = []
    monkeypatch.setattr(gs, '_emit', lambda *args: events.append(args))
    # Put credentials across the 300-character boundary: masking must precede slicing.
    err = 'earlier diagnostic\n' + 'x' * 290 + leak + ' tail\n\n'
    out, _ = publish(session, monkeypatch, push_result(err=err), branch, SECRET)
    raw = gs.db_git.get_session(mid)['context']
    exposed = json.dumps(out) + raw + json.dumps(saved(mid)) + caplog.text + json.dumps(events)
    for value in SECRETS:
        assert value not in exposed
    assert 'configured' not in exposed
    assert len(saved(mid)['last_error']['detail']) <= 300


@pytest.mark.parametrize('err,cause,secret', [
    ('Authentication failed', 'auth', 'Authentication failed'),
    ('Could not resolve host', 'network', 'Could not resolve host'),
    ('cannot lock ref refs/heads/main', 'ref_lock', 'cannot lock ref'),
    ('timeout_expired', 'timeout', 'timeout_expired'),
    ('connection reset by peer', 'unknown', SECRET),
], ids=['auth-overlap', 'network-overlap', 'ref-lock-overlap', 'timeout-overlap', 'unknown'])
def test_runner_classifies_raw_then_scrubs(session, monkeypatch, tmp_path, caplog, err, cause, secret):
    mid, ctx, row = session
    calls = []
    rc = -1 if cause == 'timeout' else 128
    pattern_output = '\n'.join(LEAKS[3:] + [secret, quote(secret, safe='')])
    raw = pattern_output + '\n' + err
    # Keep timeout's exact diagnostic sentinel for its conservative classifier.
    raw_err = err if cause == 'timeout' else raw
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, rc, pattern_output, raw_err)
    monkeypatch.setattr(command.subprocess, 'run', run)
    monkeypatch.setattr(command, '_write_askpass', lambda: (tmp_path / 'askpass', None))
    proc = command._run_git(['push', 'origin', 'main'], secret=secret)
    assert proc.cause == cause
    assert command.git_failure_details(proc, 'push')['cause'] == cause
    gs._enter_reconciling(mid, ctx, 'push_remote_unknown', proc, schedule_retry=True,
                          expected_remote_head=EXPECTED, secret=SECRET)
    assert saved(mid)['last_error']['cause'] == cause
    exposed = proc.stdout + proc.stderr + repr(proc) + gs.db_git.get_session(mid)['context'] + caplog.text
    # The explicitly configured value and pattern-based credentials are both removed.
    assert secret not in exposed
    for value in SECRETS:
        assert value not in exposed
    assert len(calls) == 1


def test_scrub_full_text_overlap_encoded_and_bounded_gate_contract():
    from flowgate.gate_decisions.redact import scrub
    assert credentials._scrub(None, '', None) == ''
    assert credentials._scrub('long-secret short', 'short', 'long-secret') == '*** ***'
    assert credentials._scrub('abcdef', 'abc', 'abcdef') == '***'
    assert credentials._scrub(PAT, PAT[:20]) == '***'
    assert credentials._scrub(quote(SECRET, safe='').lower(), SECRET) == '***'
    assert len(credentials._scrub('x' * 1000)) == 1000
    assert len(scrub('x' * 1000)) == 300


@pytest.mark.parametrize('expired_budget', [False, True])
def test_runner_timeout_preserves_classification_and_budget(monkeypatch, tmp_path, expired_budget):
    calls = []
    def timeout(args, **kwargs):
        calls.append((args, kwargs))
        raise subprocess.TimeoutExpired(args, 1)
    monkeypatch.setattr(command.subprocess, 'run', timeout)
    monkeypatch.setattr(command, '_write_askpass', lambda: (tmp_path / 'askpass', None))
    with command.git_budget(0 if expired_budget else 30):
        proc = command._run_git(['push', 'origin', 'main'], secret='timeout_expired')
    assert proc.returncode == -1 and proc.cause == 'timeout'
    assert proc.stderr == '***'
    assert len(calls) == (0 if expired_budget else 1)


@pytest.mark.parametrize('required', [False, True])
@pytest.mark.parametrize('rc', [0, 128])
def test_reviewed_local_target_helper_audits_session(session, monkeypatch, tmp_path, required, rc):
    mid, ctx, row = session
    monkeypatch.setattr(gs, '_base_root_of', lambda project: tmp_path)
    calls = []
    monkeypatch.setattr(gs, '_run_git', lambda args, **kw: calls.append(args) or push_result(rc, ''))
    result = branch_merge._apply_local_target(PROJECT, {'target_is_base': True}, tmp_path,
        LOCAL, required=required, merge_id=mid, context=ctx)
    assert result is (True if rc == 0 else (False if required else None))
    assert calls == [['merge', '--ff-only', LOCAL]]
    assert saved(mid)['ref_operations'][-1]['reason'] == 'apply_local_target'
    assert saved(mid)['ref_operations'][-1]['returncode'] == rc


def reconcile_setup(session, monkeypatch, tmp_path, observed):
    mid, ctx, row = session
    publish(session, monkeypatch, push_result())
    monkeypatch.setattr(gs, 'now_iso', lambda: RESET_AT)
    monkeypatch.setattr(gs, 'current_instance_id', lambda: 'inst_B')
    monkeypatch.setattr(branch_merge_publish, 'session_lock', lambda *a, **kw: (None, None))
    monkeypatch.setattr(branch_merge_publish, 'session_unlock', lambda *a: None)
    monkeypatch.setattr(gs.merge_target, 'resolve_session_target',
                        lambda row: SimpleNamespace(root=tmp_path, target_branch='main'))
    probes = []
    monkeypatch.setattr(gs, '_query_remote_ref', lambda *a: probes.append(a) or observed)
    monkeypatch.setattr(gs, 'PUSH_RECONCILE_DELAYS_SEC', [0, 0, 0])
    monkeypatch.setattr(gs, '_emit', lambda *a: None)
    return probes


@pytest.mark.parametrize('observed,status,resets,probes_expected', [
    (LOCAL, 'merged', 0, 1), (EXPECTED, 'pending_review', 1, 1),
    ('d' * 40, 'manual_reconciliation_needed', 0, 1),
    (None, 'retry_scheduled', 0, 3),
])
@pytest.mark.parametrize('reset_code', [0, 128])
def test_reconcile_contract_and_cross_instance_audit(session, monkeypatch, tmp_path,
                                                    observed, status, resets, probes_expected, reset_code):
    mid, ctx, row = session
    probes = reconcile_setup(session, monkeypatch, tmp_path, observed)
    before = saved(mid)
    cmds = []
    monkeypatch.setattr(gs, '_run_git', lambda args, **kw: cmds.append(args) or push_result(reset_code, ''))
    def complete(group, mid, project, context, *, pushed):
        gs.db_git.set_session_context(mid, context)
        return {'ok': True, 'result': {'status': 'merged'}}
    monkeypatch.setattr(gs, '_complete_merge_review', complete)
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    after = saved(mid)
    assert out['result']['status'] == status
    assert len(probes) == probes_expected
    assert cmds == [['reset', '--hard', 'ORIG_HEAD']] * resets
    assert after['last_error'] == before['last_error']
    assert after['last_error']['instance_id'] == 'inst_A'
    assert after['last_error']['local_sha'] == LOCAL
    assert after['reconcile_attempt_count'] == 1
    if resets:
        assert after['ref_operations'][-1] == dict(operation='reset', reason='reconcile_remote_unchanged',
            at=RESET_AT, instance_id='inst_B', returncode=reset_code, status='completed')
        assert after['merge_commit'] is None and after['approval_attempt_id'] is None
        assert after['review_state'] == gs.REVIEW_STATE_PENDING
        assert 'reconcile_next_at' not in after
    elif observed == 'd' * 40:
        assert after['reconciliation_kind'] == 'push_remote_third'
        assert after['reconcile_next_at'] is None
    elif observed is None:
        assert after['merge_commit'] == LOCAL
        assert after['reconcile_next_at'] == '2026-11-08T10:10:00+09:00'


def test_ref_audit_bounds_and_exception_has_no_fabricated_code(session, monkeypatch):
    mid, ctx, row = session
    ctx['ref_operations'] = [dict(operation='old', reason=str(n)) for n in range(25)]
    monkeypatch.setattr(gs, '_run_git', lambda *a, **kw: push_result(129))
    gs._run_session_git(mid, ctx, 'reset', 'test_failure', ['reset', '--hard', 'ORIG_HEAD'])
    assert len(saved(mid)['ref_operations']) == 20
    assert saved(mid)['ref_operations'][0]['reason'] == '6'
    assert saved(mid)['ref_operations'][-1]['returncode'] == 129
    def fail(*a, **kw):
        raise RuntimeError('failed before a subprocess result')
    monkeypatch.setattr(gs, '_run_git', fail)
    with pytest.raises(RuntimeError):
        gs._run_session_git(mid, ctx, 'merge', 'test_exception', ['merge', '--abort'])
    e = saved(mid)['ref_operations'][-1]
    assert e['returncode'] is None and e['status'] == 'exception'


def test_success_and_merge_only_contract(session, monkeypatch):
    mid, ctx, row = session
    completed = []
    monkeypatch.setattr(gs, '_complete_merge_review', lambda *a, **kw: completed.append(kw) or {'ok': True})
    _, calls = publish(session, monkeypatch, push_result(0, ''))
    assert len(calls) == 1 and completed == [{'pushed': True}]
    assert 'last_error' not in saved(mid)
    row['finalize_action'] = 'merge_only'
    calls.clear()
    gs._conditionally_push_or_reconcile(row['group_id'], mid, row, PROJECT, Path('.'), {}, 'main', ctx)
    assert calls == [] and completed[-1] == {'pushed': False}


@pytest.mark.parametrize('branch', [False, True])
def test_rejected_preserves_rereview_commands_and_audits(session, monkeypatch, branch):
    mid, ctx, row = session
    calls = []
    def run(args, **kw):
        calls.append(args)
        return push_result(1, '[rejected] stale info') if args[0] == 'push' else push_result(0, '')
    monkeypatch.setattr(gs, '_run_git', run)
    monkeypatch.setattr(gs, '_load_secret_for', lambda cfg: '')
    monkeypatch.setattr(gs, '_merge_in_progress', lambda root: True)
    monkeypatch.setattr(gs, '_unmerged_paths', lambda root: [])
    monkeypatch.setattr(gs, '_freeze_commit_candidate', lambda *a: {'review_fingerprint': 'new', 'changes': []})
    if branch:
        ctx['branch_merge'] = {'push': True}
        out = branch_merge.publish_reviewed(mid, row, PROJECT, Path('.'), {}, 'main', ctx)
    else:
        out = gs._conditionally_push_or_reconcile(row['group_id'], mid, row, PROJECT, Path('.'), {}, 'main', ctx)
    assert out['result']['status'] == 're_review'
    after = saved(mid)
    assert after['last_error'] == {'code': 'push_rejected'}
    assert [c[0] for c in calls[:4]] == ['push', 'reset', 'fetch', 'merge']
    assert len(calls) == 5
    assert [e['operation'] for e in after['ref_operations']] == ['push', 'reset', 'fetch', 'merge', 'merge']
    assert after['ref_operations'][1]['reason'] == 'push_rejected'


def approve_setup(session, monkeypatch, tmp_path):
    mid, ctx, row = session
    monkeypatch.setattr(gs, '_review_session_of_owner', lambda *a: gs.db_git.get_session(mid))
    monkeypatch.setattr(branch_merge_publish, 'session_lock', lambda *a, **kw: (None, None))
    monkeypatch.setattr(branch_merge_publish, 'session_unlock', lambda *a: None)
    monkeypatch.setattr(gs.merge_target, 'resolve_session_target',
                        lambda row: SimpleNamespace(root=tmp_path, target_branch='main'))
    monkeypatch.setattr(gs, '_live_candidate_matches_snapshot', lambda *a: True)
    monkeypatch.setattr(gs, '_validate_review_changed_paths', lambda *a, **kw: [])
    monkeypatch.setattr(gs, '_rev_parse', lambda root, ref: ctx['snapshot_tree'] if ref == 'HEAD^{tree}' else LOCAL)
    # A fresh approval must get past the existing idempotence fast-path.
    ctx['approval_attempt_id'] = None
    gs.db_git.set_session_context(mid, ctx)


def test_push_exception_stays_separate(session, monkeypatch, tmp_path):
    mid, ctx, row = session
    approve_setup(session, monkeypatch, tmp_path)
    calls = []
    def run(args, **kw):
        calls.append(args)
        if 'commit' in args:
            return push_result(0, '')
        raise RuntimeError('unexpected push exception')
    monkeypatch.setattr(gs, '_run_git', run)
    out = gs.approve_merge_review(row['group_id'], mid, attempt_id='new', review_fingerprint='fp', authority='human')
    assert out['result']['status'] == 'reconciling'
    after = saved(mid)
    assert after['last_error'] == dict(code='push_exception', detail='unexpected_error')
    assert after['merge_commit'] == LOCAL
    assert after['ref_operations'][0]['operation'] == 'commit'
    assert len(calls) == 2


@pytest.mark.parametrize('reset_code', [0, 128])
def test_tree_mismatch_reset_keeps_existing_restore_policy(session, monkeypatch, tmp_path, reset_code):
    mid, ctx, row = session
    approve_setup(session, monkeypatch, tmp_path)
    ctx['base_head'] = EXPECTED
    gs.db_git.set_session_context(mid, ctx)
    monkeypatch.setattr(gs, '_rev_parse', lambda root, ref: 'd' * 40 if ref == 'HEAD^{tree}' else EXPECTED)
    monkeypatch.setattr(gs, '_run_git', lambda args, **kw: push_result(reset_code if args[0] == 'reset' else 0))
    out = gs.approve_merge_review(row['group_id'], mid, attempt_id='new', review_fingerprint='fp', authority='human')
    after = saved(mid)
    assert after['ref_operations'][-1]['reason'] == 'commit_tree_mismatch'
    assert after['ref_operations'][-1]['returncode'] == reset_code
    assert out['result']['status'] == ('commit_creation_failed' if reset_code == 0 else 'reconciling')


def git(args, cwd=None):
    proc = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.skipif(shutil.which('git') is None, reason='git binary unavailable')
def test_real_local_reset_reflog_identity_and_cross_instance_evidence(session, monkeypatch, tmp_path):
    mid, ctx, row = session
    repo, bare = tmp_path / 'work', tmp_path / 'origin.git'
    git(['init', '--bare', '-b', 'main', str(bare)])
    git(['init', '-b', 'main', str(repo)])
    git(['config', 'user.name', 'merge'], repo)
    git(['config', 'user.email', 'merge@local'], repo)
    f = repo / 'file.txt'
    f.write_text('before\n', encoding='utf-8')
    git(['add', '.'], repo)
    git(['commit', '-m', 'before'], repo)
    before = git(['rev-parse', 'HEAD'], repo)
    git(['remote', 'add', 'origin', str(bare)], repo)
    git(['push', 'origin', 'main'], repo)
    f.write_text('after\n', encoding='utf-8')
    git(['commit', '-am', 'after'], repo)
    local = git(['rev-parse', 'HEAD'], repo)
    git(['update-ref', 'ORIG_HEAD', before], repo)
    ctx.update(merge_commit=local, expected_remote_head=before)
    gs.db_git.set_session_context(mid, ctx)
    real_run = gs._run_git
    probes = reconcile_setup(session, monkeypatch, repo, before)
    monkeypatch.setattr(gs, '_run_git', real_run)
    monkeypatch.setenv('GIT_COMMITTER_NAME', 'operator')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 'operator@example.invalid')
    out = gs.reconcile_push_session(mid, trigger='server_startup')
    assert out['result']['status'] == 'pending_review'
    assert git(['rev-parse', 'HEAD'], repo) == before
    assert git(['rev-parse', 'main'], repo) == before
    for ref in ('HEAD', 'refs/heads/main'):
        assert git(['reflog', 'show', '-1', '--format=%gn <%ge>|%gs', ref], repo) == (
            'FlowGate <flowgate@localhost>|reset: moving to ORIG_HEAD')
    after = saved(mid)
    assert after['last_error']['local_sha'] == local
    assert after['last_error']['instance_id'] == 'inst_A'
    assert after['ref_operations'][-1]['instance_id'] == 'inst_B'
    assert after['ref_operations'][-1]['returncode'] == 0
    assert os.environ['GIT_COMMITTER_NAME'] == 'operator'
    assert git(['config', 'user.name'], repo) == 'merge'
    # Extra env also cannot reassign the app committer; project authors are preserved.
    proc = command._run_git([*gs._GIT_IDENT, 'commit', '--allow-empty', '-m', 'author'], cwd=repo,
        author_env={'GIT_AUTHOR_NAME': 'Project Author', 'GIT_AUTHOR_EMAIL': 'author@example.invalid'},
        extra_env={'GIT_COMMITTER_NAME': 'another', 'GIT_COMMITTER_EMAIL': 'another@example.invalid'})
    assert proc.returncode == 0
    assert git(['show', '-s', '--format=%an <%ae>|%cn <%ce>'], repo) == (
        'Project Author <author@example.invalid>|FlowGate <flowgate@localhost>')
    proc = command._run_git(['update-ref', '-m', 'audit', 'refs/heads/audited', 'HEAD'], cwd=repo)
    assert proc.returncode == 0
    assert git(['reflog', 'show', '-1', '--format=%gn <%ge>', 'refs/heads/audited'], repo) == (
        'FlowGate <flowgate@localhost>')
