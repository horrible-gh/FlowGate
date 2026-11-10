"""0153 T#5: durable approval/recovery journal with real Git and sqlite.

Only remote command failures are injected. Locks, claims, preservation refs and the
deferred AC/root/consume transaction are real. Fixtures reuse T#2 and 0555.
"""
import copy
import json
import subprocess
import uuid
from types import SimpleNamespace

import pytest

from tests.modules.flow_gate.test_git_integration_0115 import patch_store, tmp_db  # noqa: F401
from tests.modules.flow_gate.test_git_preserve_ref_0153 import (  # noqa: F401
    env, project, repo, git, git_rc, saved, names, ref_value, parents_of, preserve_refs,
    open_session, approve_ready, approve, merge_commit, approved, on, push_timeout, PROJECT,
)
from tests.modules.flow_gate import test_final_approval_conflict_intent_0555 as fa
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import recovery, approval_intent, branch_merge_publish as bmp
from modules.flow_gate.services.git import merge_target, session_claim, lock_manager, instance_registry
from modules.flow_gate.db import documents, git_concurrency as gc
from modules.flow_gate.db.connection import get_store

REAL_OWNER = gs._review_session_of_owner
REAL_VALIDATE = gs._validate_review_changed_paths
REAL_COMPLETE = gs._complete_merge_review
REAL_LOCK, REAL_UNLOCK = bmp.session_lock, bmp.session_unlock


@pytest.fixture
def real(env, monkeypatch):
    session_claim._reset_thread_state()
    for iid in ('inst_A', 'inst_B'):
        get_store()._execute('DELETE FROM server_instance WHERE instance_id = ?', [iid])
        gc.insert_instance(iid, '0153-other-node', 4242, None, env.clock['now'])
    monkeypatch.setattr(bmp, 'session_lock', REAL_LOCK)
    monkeypatch.setattr(bmp, 'session_unlock', REAL_UNLOCK)
    monkeypatch.setattr(gs, '_complete_merge_review', REAL_COMPLETE)
    monkeypatch.setattr(gs, '_base_root_of', lambda p: env.repo.work)
    monkeypatch.setattr(gs, '_cleanup_group_slot', lambda *a, **kw: None)
    monkeypatch.setattr(gs, '_emit_pending_changed', lambda *a, **kw: None)
    monkeypatch.setattr(merge_target, 'resolve_session_target', lambda row: merge_target.MergeTargetContext(
        project_id=PROJECT, base_branch='main', target_branch='main', is_project_base=True,
        root=env.repo.work, merge_id=int(row['merge_id']), legacy=True, managed_workspace=False))
    monkeypatch.setattr(fa, 'PROJECT', PROJECT)
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports
    from flowgate.card_document_edit import integration
    monkeypatch.setattr(ports, '_handlers', dict(ports._handlers))
    integration.register()
    root, ac = fa._seed_final_approval_group(env.group)
    env.root_id, env.ac_id = root, ac
    env.intent = approval_intent.build_intent(group_id=env.group, ac_doc_id=ac,
        requested_by=fa.USER['user_id'], git_action='merge', approval_intent_id=str(uuid.uuid4()))
    yield env
    session_claim._reset_thread_state()


def ready(real):
    mid, fp = approve_ready(real)
    real.monkeypatch.setattr(gs, '_review_session_of_owner', REAL_OWNER)
    real.monkeypatch.setattr(gs, '_validate_review_changed_paths', REAL_VALIDATE)
    ctx = saved(mid)
    ctx[approval_intent.INTENT_KEY] = real.intent
    gs.db_git.set_session_context(mid, ctx)
    gs._set_status(real.group, 'conflict', merge_id=mid)
    return mid, fp


def request(real, mid, action='retry', body=None):
    if body is None:
        view = recovery.get(real.group, mid)['result']
        body = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
                'approval_attempt_id': view['approval_attempt_id']}
    return gs.mutate_merge_recovery(real.group, mid, action, actor='reviewer', **body), body


# T0013 section 5: shared postconditions, checked against real rows below.
TRANSITIONS = {
    'pending': ('conflict', 'open', 'pending_review', 'wf_in_progress', True),
    'approval_pending': ('merged', 'done', 'pending_review', 'wf_in_progress', True),
    'approved': ('merged', 'done', 'approved', 'wf_done', False),
    'aborted': ('waiting', 'aborted', 'pending_review', 'wf_in_progress', False),
}


def assert_transition(real, mid, phase):
    group, session, ac, root, live_intent = TRANSITIONS[phase]
    assert gs.db_git.get_state(real.group)['status'] == group
    assert gs.db_git.get_session(mid)['status'] == session
    assert documents.get_by_id(real.ac_id)['doc_review_status'] == ac
    assert documents.get_by_id(real.root_id)['doc_review_status'] == root
    assert bool(approval_intent.intent_of_context(saved(mid))) is live_intent


def pending(real, mid):
    assert_transition(real, mid, 'approval_pending' if saved(mid).get('git_terminal') else 'pending')
    assert documents.get_by_id(real.ac_id)['doc_review_status'] == 'pending_review'
    assert documents.get_by_id(real.root_id)['doc_review_status'] == 'wf_in_progress'
    assert approval_intent.intent_of_context(saved(mid)) == real.intent


def terminal(real, mid):
    assert_transition(real, mid, 'approved')
    assert documents.get_by_id(real.ac_id)['doc_review_status'] == 'approved'
    assert documents.get_by_id(real.root_id)['doc_review_status'] == 'wf_done'
    assert approval_intent.intent_of_context(saved(mid)) is None
    assert gs.db_git.get_session(mid)['status'] == 'done'
    assert gs.db_git.get_state(real.group)['status'] == 'merged'


def fail_push(real, kind='timeout'):
    errors = {'timeout': (-1, 'timeout_expired'), 'auth': (128, 'fatal: Authentication failed'),
              'unknown': (128, 'opaque failure'), 'rejected': (1, ' ! [rejected] main -> main (stale info)')}
    rc, err = errors[kind]
    hook = on('push', lambda args: subprocess.CompletedProcess(args, rc, '', err))
    real.hooks.append(hook)
    return hook


@pytest.mark.parametrize('kind', ['timeout', 'auth', 'unknown', 'rejected'])
def test_A1_initial_failure_is_non_destructive(real, kind):
    mid, fp = ready(real)
    fail_push(real, kind)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    ctx = saved(mid)
    sha = ctx['merge_commit']
    assert ref_value(real.repo.work, 'HEAD') == sha == ref_value(real.repo.work, ctx['preserve_ref'])
    assert parents_of(real.repo.work, sha) == [real.repo.B, real.repo.F]
    assert git(['rev-parse', sha + '^{tree}'], real.repo.work) == ctx['snapshot_tree']
    assert ref_value(real.repo.bare, 'main') == real.repo.B
    assert 'reset' not in names(real)
    assert ctx['approval_attempt_id'] == attempt
    assert ctx['reconciliation_kind'] == ('reconcile_auth_required' if kind == 'auth' else 'push_remote_unknown')
    pending(real, mid)
    view = recovery.get(real.group, mid)['result']
    assert view['push_error']['returncode'] == (-1 if kind == 'timeout' else 1 if kind == 'rejected' else 128)
    assert view['preserved_refs'][0]['verification'] == 'verified'


def test_A2_same_attempt_replay_different_payload_and_id(real):
    mid, fp = ready(real)
    fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    real.commands.clear()
    out = approve(real, mid, attempt, fp)['result']
    assert not out['git_terminal'] and out['approval']['approved'] is False
    assert out['approval_attempt_id'] == attempt
    assert not real.commands
    for key, fingerprint, code in [(attempt, 'other', 'approval_attempt_reused'),
                                    (str(uuid.uuid4()), fp, 'approval_attempt_active')]:
        with pytest.raises(gs.GitServiceError) as exc:
            approve(real, mid, key, fingerprint)
        assert exc.value.code == code and exc.value.status == 409
    assert not real.commands


def test_A3_commit_audit_db_failure_recovers_exact_commit(real, monkeypatch):
    mid, fp = ready(real)
    original = gs.db_git.set_session_context
    lost = []
    def fail_once(merge_id, ctx):
        if not lost and ctx.get('apply_phase') == 'committing' and any(
            e.get('reason') == 'approve_merge_review' for e in ctx.get('ref_operations') or []):
            lost.append(True)
            raise RuntimeError('injected write loss after commit')
        return original(merge_id, ctx)
    monkeypatch.setattr(gs.db_git, 'set_session_context', fail_once)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    sha = ref_value(real.repo.work, 'HEAD')
    assert sha != real.repo.B and saved(mid)['apply_phase'] == 'commit_unknown'
    assert saved(mid)['approval_attempt_id'] == attempt
    real.commands.clear()
    result, body = request(real, mid)
    assert result['result']['approval']['approved']
    terminal(real, mid)
    assert saved(mid)['merge_commit'] == sha
    assert 'commit' not in names(real)
    real.commands.clear()
    again, _ = request(real, mid, body=body)
    assert again['result']['approval']['approved'] and not real.commands


def test_R1_retry_preserves_id_budget_and_completed_receipt_precedes_stale(real):
    mid, fp = ready(real)
    hook = fail_push(real, 'auth')
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    old = saved(mid)
    real.hooks.remove(hook)
    out, body = request(real, mid)
    assert out['result']['approval']['approved']
    assert saved(mid)['approval_attempt_id'] == attempt
    assert saved(mid)['preserve_ref'] == old['preserve_ref']
    assert saved(mid)['merge_commit'] == old['merge_commit']
    assert len(saved(mid)['recovery_retry_history']) == 1
    terminal(real, mid)
    real.commands.clear()
    request(real, mid, body=body)
    assert not real.commands and len(saved(mid)['recovery_retry_history']) == 1
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, body={**body, 'recovery_version': 'different'})
    assert exc.value.code == 'recovery_request_reused'


def test_R2_preserve_failure_retry_is_create_only(real):
    mid, fp = ready(real)
    ref = f'refs/flowgate/preserve/{mid}'
    git(['update-ref', ref, real.repo.B], real.repo.work)
    approve(real, mid, str(uuid.uuid4()), fp)
    sha = saved(mid)['merge_commit']
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid)
    assert exc.value.code == 'preserve_failed'
    assert ref_value(real.repo.work, ref) == real.repo.B
    assert ref_value(real.repo.work, 'HEAD') == sha
    assert 'push' not in names(real)
    pending(real, mid)


@pytest.mark.parametrize('missing', ['legacy_sha', 'workspace', 'snapshot', 'merge_head_probe'])
def test_R3_hold_readonly_and_evidence_admission(real, monkeypatch, missing):
    mid, fp = ready(real)
    hook = fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    real.hooks.remove(hook)
    ctx = saved(mid)
    ctx['review_state'] = None
    ctx['recovery_hold'] = {'code': 'recovery_hold', 'resume_missing': ['merge_commit']}
    ctx['merge_commit'] = None
    if missing == 'snapshot':
        ctx['snapshot_tree'] = None
    if missing == 'workspace':
        original = merge_target.resolve_session_target
        from dataclasses import replace
        monkeypatch.setattr(merge_target, 'resolve_session_target', lambda row: replace(original(row), root=None))
    if missing == 'merge_head_probe':
        real.hooks.append(lambda args: subprocess.CompletedProcess(args, 128, '', 'probe failed')
                          if args[-1:] == ['MERGE_HEAD'] else None)
    gs.db_git.set_session_context(mid, ctx)
    before = copy.deepcopy(saved(mid))
    view = recovery.get(real.group, mid)['result']
    assert saved(mid) == before
    assert view['review_state'] is None and view['recovery_hold']
    if missing == 'legacy_sha':
        request(real, mid)
        terminal(real, mid)
        assert saved(mid)['recovery_hold_history']
    else:
        with pytest.raises(gs.GitServiceError):
            request(real, mid)
        assert saved(mid)['recovery_hold'] == before['recovery_hold']
        pending(real, mid)


def test_R4_precommit_hold_returns_same_review_without_commit(real):
    mid, fp = ready(real)
    ctx = saved(mid)
    ctx['recovery_hold'] = {'code': 'recovery_hold', 'review_state_at_hold': 'resolved_pending_review'}
    gs.db_git.set_session_context(mid, ctx)
    real.commands.clear()
    out, _ = request(real, mid)
    assert out['result']['review_state'] == 'resolved_pending_review'
    assert 'commit' not in names(real) and 'push' not in names(real)
    assert saved(mid)['review_fingerprint'] == fp and not saved(mid).get('recovery_hold')
    pending(real, mid)


def test_B1_preserving_abort_no_merge_abort_and_no_late_approval(real):
    mid, fp = ready(real)
    fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    before = saved(mid)
    real.commands.clear()
    out, body = request(real, mid, 'abort')
    assert out['result']['session_state'] == 'aborted'
    assert_transition(real, mid, 'aborted')
    assert ['merge', '--abort'] not in real.commands
    assert ref_value(real.repo.work, 'HEAD') == real.repo.B
    assert ref_value(real.repo.work, before['preserve_ref']) == before['merge_commit']
    assert parents_of(real.repo.work, before['merge_commit']) == [real.repo.B, real.repo.F]
    assert documents.get_by_id(real.ac_id)['doc_review_status'] == 'pending_review'
    assert gs.db_git.get_state(real.group)['status'] == 'waiting'
    assert saved(mid)[approval_intent.DISCARDED_KEY]['approval_intent_id'] == real.intent['approval_intent_id']
    real.commands.clear()
    request(real, mid, 'abort', body)
    approve(real, mid, attempt, fp)
    assert gs.reconcile_push_session(mid, trigger='server_startup') is None
    assert not real.commands


def test_B2_pending_merge_snapshot_preserved_before_abort(real):
    mid, fp = ready(real)
    ctx = saved(mid)
    ctx['recovery_hold'] = {'code': 'ttl'}
    gs.db_git.set_session_context(mid, ctx)
    request(real, mid, 'abort')
    snap = saved(mid)['abort_snapshot']
    assert ref_value(real.repo.work, snap['ref']) == snap['sha']
    assert git(['rev-parse', snap['sha'] + '^{tree}'], real.repo.work) == ctx['snapshot_tree']
    assert parents_of(real.repo.work, snap['sha']) == [real.repo.B, real.repo.F]
    assert ref_value(real.repo.work, 'MERGE_HEAD') is None
    assert names(real).index('update-ref') < real.commands.index(['merge', '--abort'])


@pytest.mark.parametrize('fault', ['dirty', 'reset', 'db_after_git'])
def test_B3_abort_failure_keeps_pending_and_resumes_phase(real, monkeypatch, fault):
    mid, fp = ready(real)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    if fault == 'dirty':
        (real.repo.work / 'a.txt').write_text('new edit\n')
    elif fault == 'reset':
        real.hooks.append(on('reset', subprocess.CompletedProcess([], 1, '', 'reset failed')))
    else:
        original = gs.db_git.set_session_context
        fired = []
        def fail(mid, ctx):
            if not fired and (ctx.get('abort_progress') or {}).get('phase') == 'git_cleaned':
                fired.append(True)
                raise RuntimeError('db loss')
            return original(mid, ctx)
        monkeypatch.setattr(gs.db_git, 'set_session_context', fail)
    body = {'request_id': str(uuid.uuid4()), 'recovery_version': recovery.get(real.group, mid)['result']['recovery_version'],
            'approval_attempt_id': saved(mid)['approval_attempt_id']}
    with pytest.raises(gs.GitServiceError):
        request(real, mid, 'abort', body)
    assert gs.db_git.get_state(real.group)['status'] == 'conflict'
    pending(real, mid)
    if fault == 'db_after_git':
        real.commands.clear()
        request(real, mid, 'abort', body)
        assert 'reset' not in names(real) and ['merge', '--abort'] not in real.commands
        assert gs.db_git.get_state(real.group)['status'] == 'waiting'


def test_B4_live_claim_and_claim_loss_preserve_context(real, monkeypatch):
    mid, fp = ready(real)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    held = session_claim.acquire(mid, 'reconcile')
    assert held is not None
    real.clock['instance'] = 'inst_B'
    before = saved(mid)
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid)
    assert exc.value.code == 'recovery_busy' and saved(mid) == before
    real.clock['instance'] = 'inst_A'
    session_claim.release(held)
    actual = bmp.session_lock
    def steal(*a, **kw):
        lock = actual(*a, **kw)
        get_store()._execute('UPDATE git_merge_session_claim SET claim_token = ? WHERE merge_id = ?', ['stolen', mid])
        return lock
    monkeypatch.setattr(bmp, 'session_lock', steal)
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid)
    assert exc.value.code == 'recovery_claim_lost' and saved(mid) == before


def test_C1_lost_push_response_completes_without_repush(real):
    mid, fp = ready(real)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    sha = saved(mid)['merge_commit']
    git(['push', 'origin', 'main'], real.repo.work)
    real.commands.clear()
    request(real, mid)
    terminal(real, mid)
    assert 'push' not in names(real)
    assert ref_value(real.repo.bare, 'main') == sha


def test_C2_approval_only_failure_and_retry_never_replays_git(real, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service
    mid, fp = ready(real)
    original = pipeline_service.commit_final_approval
    monkeypatch.setattr(pipeline_service, 'commit_final_approval', lambda **kw: (_ for _ in ()).throw(RuntimeError('failure')))
    attempt = str(uuid.uuid4())
    result = approve(real, mid, attempt, fp)['result']
    assert result['git_terminal'] and result['pushed'] and not result['approval']['approved']
    assert result['approval']['error']['code'] == 'approval_commit_failed'
    pending(real, mid)
    assert gs.db_git.get_state(real.group)['status'] == 'merged'
    monkeypatch.setattr(pipeline_service, 'commit_final_approval', original)
    monkeypatch.setattr(gs, '_cleanup_group_slot', lambda *a, **k:
        (_ for _ in ()).throw(AssertionError('approval-only retry must not enter Git slot cleanup')))
    assert 'retry_approval' in recovery.get(real.group, mid)['result']['allowed_actions']
    real.commands.clear()
    request(real, mid)
    terminal(real, mid)
    assert not real.commands
    replay = approve(real, mid, attempt, fp)['result']
    assert replay['approval']['approved'] and not real.commands


def test_P1_route_permissions_uuid_stale_and_error_adapter(real, monkeypatch):
    from modules.flow_gate.api.v1 import git_routes as routes
    mid, fp = ready(real)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    monkeypatch.setattr(routes, '_has_permission', lambda *a: False)
    assert routes.get_merge_recovery(real.group, mid, {}).status_code == 403
    monkeypatch.setattr(routes, '_has_permission', lambda *a: True)
    body = routes.RecoveryBody(request_id='not-uuid', recovery_version='x', approval_attempt_id=None)
    assert routes.post_merge_recovery_retry(real.group, mid, body, {}).status_code == 400
    body = routes.RecoveryBody(request_id=str(uuid.uuid4()), recovery_version='stale',
                               approval_attempt_id=saved(mid)['approval_attempt_id'])
    response = routes.post_merge_recovery_retry(real.group, mid, body, {})
    assert response.status_code == 409
    data = json.loads(response.body)
    assert data['ok'] is False and data['error']['code'] == 'recovery_stale'
    assert data['error']['details']['recovery']['merge_id'] == mid
    with pytest.raises(gs.GitServiceError) as exc:
        recovery.get(real.group + '-other', mid)
    assert exc.value.status == 404


def test_P1_original_nested_evidence_scrub_and_stable_version(real):
    mid, fp = ready(real)
    ctx = saved(mid)
    ctx['last_error'] = {'code': 'new_error', 'prior_push_error': {'code': 'preserve_failed',
        'prior_push_error': {'code': 'push_remote_unknown', 'cause': 'unknown', 'returncode': 0,
                            'detail': 'https://user:password@example.invalid/a token=secret-token'}}}
    gs.db_git.set_session_context(mid, ctx)
    v1 = recovery.get(real.group, mid)['result']
    gs.db_git.touch_session(mid)
    v2 = recovery.get(real.group, mid)['result']
    assert v1['recovery_version'] == v2['recovery_version']
    assert v1['push_error']['returncode'] == 0
    assert 'password' not in json.dumps(v1)
    ctx = saved(mid)
    ctx['expected_remote_head'] = real.repo.A
    gs.db_git.set_session_context(mid, ctx)
    assert recovery.get(real.group, mid)['result']['recovery_version'] != v2['recovery_version']


@pytest.mark.parametrize('bound', [True, False])
def test_R2_post_push_cleanup_is_explicit_and_retry_never_repushes(real, monkeypatch, bound):
    mid, fp = ready(real)
    if not bound:
        context = saved(mid)
        context.pop(approval_intent.INTENT_KEY)
        gs.db_git.set_session_context(mid, context)
    finish = gs._finish_reviewed_attempt
    fault = {'enabled': True}
    def finish_then_fail_once(*args, **kwargs):
        result = finish(*args, **kwargs)
        if fault['enabled']:
            fault['enabled'] = False
            raise gs.GitServiceError(500, 'post_push_cleanup', 'injected failure after attempt closure')
        return result
    monkeypatch.setattr(gs, '_finish_reviewed_attempt', finish_then_fail_once)
    commits = []
    consume = approval_intent.commit_deferred_approval
    def counted(*args, **kwargs):
        commits.append(True)
        return consume(*args, **kwargs)
    monkeypatch.setattr(approval_intent, 'commit_deferred_approval', counted)
    attempt = str(uuid.uuid4())
    result = approve(real, mid, attempt, fp)['result']
    assert result['git_terminal'] is True and result['pushed'] is True
    assert result['error']['code'] == 'post_push_cleanup'
    assert result['approval']['approved'] is False
    assert result['approval']['stage'] == ('pending' if bound else 'not_required')
    context = saved(mid)
    assert context['git_cleanup_done'] is False
    assert gs.db_git.get_session(mid)['status'] == 'done'
    assert gs.db_git.get_state(real.group)['status'] == 'merged'
    assert ref_value(real.repo.bare, 'main') == context['merge_commit']
    assert ref_value(real.repo.work, context['preserve_ref']) == context['merge_commit']
    real.commands.clear()
    again = approve(real, mid, attempt, fp)['result']
    assert again['error']['code'] == 'post_push_cleanup' and real.commands == []
    view = recovery.get(real.group, mid)['result']
    assert view['requires_recovery'] is True and 'retry' in view['allowed_actions']
    assert 'retry_approval' not in view['allowed_actions']
    from modules.flow_gate.api.v1 import git_routes as routes
    monkeypatch.setattr(routes, '_has_permission', lambda *a: True)
    body = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
            'approval_attempt_id': attempt}
    fault['enabled'] = True
    failed = routes.post_merge_recovery_retry(real.group, mid, routes.RecoveryBody(**body), {'user_id': 'reviewer'})
    assert failed.status_code == 500
    failure = json.loads(failed.body)
    assert failure['ok'] is False and failure['error']['code'] == 'post_push_cleanup'
    assert failure['error']['details']['recovery']['requires_recovery'] is True
    real.commands.clear()
    done, body = request(real, mid, body=body)
    assert done['result']['requires_recovery'] is False and done['result']['last_error'] is None
    assert saved(mid)['git_cleanup_done'] is True
    assert not any(cmd in names(real) for cmd in ('merge', 'commit', 'push'))
    assert len(commits) == int(bound)
    if bound:
        terminal(real, mid)
    else:
        assert done['result']['approval']['stage'] == 'not_required'
        assert documents.get_by_id(real.ac_id)['doc_review_status'] == 'pending_review'
    real.commands.clear()
    request(real, mid, body=body)
    again = approve(real, mid, attempt, fp)['result']
    assert again['error'] is None and not real.commands
    assert len(commits) == int(bound)


# ---------------------------------------------------------------------------
# TR0014 rev1 (review rej_01M71H9VTMH02NF3): receipt in error envelopes, the
# request's own first preservation, and unknown remote outcomes.
# ---------------------------------------------------------------------------

def route_call(routes, real, mid, action, body):
    handler = routes.post_merge_recovery_retry if action == 'retry' else routes.post_merge_recovery_abort
    response = handler(real.group, mid, routes.RecoveryBody(**body), {'user_id': 'reviewer'})
    if isinstance(response, dict):
        return 200, response
    return response.status_code, json.loads(response.body)


def test_R1_completed_failed_retry_receipt_survives_error_envelope_then_new_budget(real, monkeypatch):
    """First retry fails (auth still broken) -> completed receipt in the 409 envelope;
    a transport resend of that key replays without Git; after the cause is fixed the
    next explicit click is a new request_id with a new one-shot budget and succeeds."""
    from modules.flow_gate.api.v1 import git_routes as routes
    monkeypatch.setattr(routes, '_has_permission', lambda *a: True)
    mid, fp = ready(real)
    hook = fail_push(real, 'auth')
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    assert saved(mid)['reconciliation_kind'] == 'reconcile_auth_required'
    sha = saved(mid)['merge_commit']
    view = recovery.get(real.group, mid)['result']
    first = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
             'approval_attempt_id': attempt}
    real.commands.clear()
    status, data = route_call(routes, real, mid, 'retry', first)
    assert status == 409 and data['ok'] is False
    assert data['error']['code'] == 'reconcile_auth_required'
    envelope = data['error']['details']['recovery']
    assert envelope['request_id'] == first['request_id']
    assert envelope['receipt'] == {'phase': 'completed', 'action': 'retry'}
    assert 'push' in names(real)  # the one budget of this request was really spent
    assert [h['request_id'] for h in saved(mid)['recovery_retry_history']] == [first['request_id']]
    pending(real, mid)
    assert saved(mid)['merge_commit'] == sha and saved(mid)['approval_attempt_id'] == attempt
    # Response loss: the same key replays the completed receipt, no Git and no budget.
    real.commands.clear()
    status, again = route_call(routes, real, mid, 'retry', first)
    assert status == 409 and again['error']['details']['recovery']['receipt']['phase'] == 'completed'
    assert again['error']['details']['recovery']['request_id'] == first['request_id']
    assert not real.commands
    assert len(saved(mid)['recovery_retry_history']) == 1
    # Cause fixed: a new explicit retry with the envelope's version gets a new budget.
    real.hooks.remove(hook)
    second = {'request_id': str(uuid.uuid4()), 'recovery_version': envelope['recovery_version'],
              'approval_attempt_id': attempt}
    real.commands.clear()
    status, done = route_call(routes, real, mid, 'retry', second)
    assert status == 200 and done['ok'] is True
    assert done['result']['approval']['approved'] is True
    assert done['result']['receipt'] == {'phase': 'completed', 'action': 'retry'}
    history = saved(mid)['recovery_retry_history']
    assert [h['request_id'] for h in history] == [first['request_id'], second['request_id']]
    assert history[1]['kind'] == 'reconcile_auth_required'
    assert 'commit' not in names(real)
    terminal(real, mid)
    assert saved(mid)['merge_commit'] == sha and saved(mid)['approval_attempt_id'] == attempt
    assert ref_value(real.repo.bare, 'main') == sha


def test_P1_started_receipt_is_kept_in_error_envelope(real, monkeypatch):
    """An unfinished request reports a started receipt: the client keeps the key."""
    mid, fp = ready(real)
    git(['update-ref', f'refs/flowgate/preserve/{mid}', real.repo.B], real.repo.work)
    approve(real, mid, str(uuid.uuid4()), fp)
    view = recovery.get(real.group, mid)['result']
    body = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
            'approval_attempt_id': view['approval_attempt_id']}
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, body=body)
    assert exc.value.code == 'preserve_failed'
    assert exc.value.details['recovery']['request_id'] == body['request_id']
    assert exc.value.details['recovery']['receipt'] == {'phase': 'started', 'action': 'retry'}


def legacy_commit_without_round(real, monkeypatch):
    """A committed candidate whose DB write was lost after commit: no preserve round
    has been recorded yet (T0013 3.1 / A3)."""
    mid, fp = ready(real)
    original = gs.db_git.set_session_context
    lost = []
    def fail_once(merge_id, ctx):
        if not lost and ctx.get('apply_phase') == 'committing' and any(
                e.get('reason') == 'approve_merge_review' for e in ctx.get('ref_operations') or []):
            lost.append(True)
            raise RuntimeError('injected write loss after commit')
        return original(merge_id, ctx)
    monkeypatch.setattr(gs.db_git, 'set_session_context', fail_once)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    monkeypatch.setattr(gs.db_git, 'set_session_context', original)
    ctx = saved(mid)
    ctx.pop('preserve_round', None)
    gs.db_git.set_session_context(mid, ctx)
    assert saved(mid).get('preserve_round') is None
    return mid, fp, attempt, ref_value(real.repo.work, 'HEAD')


def crash_after_first_preservation(monkeypatch):
    """Stop the request right after _preserve_gate persisted round 0."""
    real_verify = recovery.verify_refs
    fired = []
    def crash(root, entries):
        if not fired:
            fired.append(True)
            raise RuntimeError('injected crash after the first preservation write')
        return real_verify(root, entries)
    monkeypatch.setattr(recovery, 'verify_refs', crash)
    return fired


@pytest.mark.parametrize('legacy_receipt', [False, True])
@pytest.mark.parametrize('action', ['retry', 'abort'])
def test_R5_first_preservation_does_not_make_the_same_request_stale(real, monkeypatch, action, legacy_receipt):
    mid, fp, attempt, sha = legacy_commit_without_round(real, monkeypatch)
    view = recovery.get(real.group, mid)['result']
    body = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
            'approval_attempt_id': view['approval_attempt_id']}
    # Installed after the read-only GET, whose probe would otherwise absorb it.
    fired = crash_after_first_preservation(monkeypatch)
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, action, body)
    assert fired and exc.value.status == 500
    assert exc.value.details['recovery']['receipt'] == {'phase': 'started', 'action': action}
    ctx = saved(mid)
    assert ctx['preserve_round'] == 0 and ctx['preserve_sha'] == sha
    assert ref_value(real.repo.work, ctx['preserve_ref']) == sha
    receipt = ctx['recovery_requests'][body['request_id']]
    assert receipt['phase'] == 'started'
    if legacy_receipt:
        # A receipt written before this fix stored the missing round as None.
        receipt['candidate']['preserve_round'] = None
        gs.db_git.set_session_context(mid, ctx)
    real.commands.clear()
    out, _ = request(real, mid, action, body)
    assert out['result']['receipt'] == {'phase': 'completed', 'action': action}
    assert 'commit' not in names(real)
    assert saved(mid)['merge_commit'] == sha
    assert ref_value(real.repo.work, saved(mid)['preserve_ref']) == sha
    if action == 'retry':
        terminal(real, mid)
        assert ref_value(real.repo.bare, 'main') == sha
    else:
        assert_transition(real, mid, 'aborted')
        assert ref_value(real.repo.bare, 'main') == real.repo.B
        assert parents_of(real.repo.work, sha) == [real.repo.B, real.repo.F]


@pytest.mark.parametrize('change', ['next_round', 'new_candidate'])
def test_R5_started_request_is_stale_only_for_a_real_next_round_or_candidate(real, monkeypatch, change):
    mid, fp, attempt, sha = legacy_commit_without_round(real, monkeypatch)
    view = recovery.get(real.group, mid)['result']
    body = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
            'approval_attempt_id': view['approval_attempt_id']}
    fired = crash_after_first_preservation(monkeypatch)
    with pytest.raises(gs.GitServiceError):
        request(real, mid, 'retry', body)
    assert fired and saved(mid)['recovery_requests'][body['request_id']]['phase'] == 'started'
    assert saved(mid)['preserve_round'] == 0
    ctx = saved(mid)
    if change == 'next_round':
        ctx['preserve_round'] = 1
    else:
        ctx['review_fingerprint'] = 'another-candidate'
    gs.db_git.set_session_context(mid, ctx)
    before = copy.deepcopy(saved(mid))
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, 'retry', body)
    assert exc.value.code == 'recovery_stale' and exc.value.status == 409
    assert not real.commands and saved(mid) == before
    assert ref_value(real.repo.bare, 'main') == real.repo.B


def test_remote_outcome_requires_valid_observation_and_recorded_lease():
    a, b = 'a' * 40, 'b' * 40
    assert recovery.remote_outcome(a, a) == 'not_published'
    for observed, expected in [(None, None), (a, None), (None, a), (a, b), ('', ''), ('short', 'short')]:
        assert recovery.remote_outcome(observed, expected) == 'unknown'


@pytest.mark.parametrize('failure', ['timeout', 'auth', 'ref_missing'])
def test_B5_unrecorded_lease_and_failed_remote_read_abort_reports_unknown(real, monkeypatch, failure):
    from modules.flow_gate.api.v1 import git_routes as routes
    monkeypatch.setattr(routes, '_has_permission', lambda *a: True)
    mid, fp = ready(real)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    ctx = saved(mid)
    ctx.pop('expected_remote_head', None)  # legacy / pending session: lease never recorded
    gs.db_git.set_session_context(mid, ctx)
    results = {'timeout': (-1, '', 'timeout_expired'), 'auth': (128, '', 'fatal: Authentication failed'),
               'ref_missing': (0, '', '')}
    rc, out, err = results[failure]
    real.hooks.append(on('ls-remote', lambda args: subprocess.CompletedProcess(args, rc, out, err)))
    view = recovery.get(real.group, mid)['result']
    body = {'request_id': str(uuid.uuid4()), 'recovery_version': view['recovery_version'],
            'approval_attempt_id': view['approval_attempt_id']}
    status, data = route_call(routes, real, mid, 'abort', body)
    assert status == 200 and data['result']['session_state'] == 'aborted'
    progress = saved(mid)['abort_progress']
    assert progress['phase'] == 'completed'
    assert progress['remote_outcome'] == 'unknown'
    assert progress['remote_observation']['cause']
    assert data['result']['remote_outcome'] == 'unknown'
    api = routes.get_merge_recovery(real.group, mid, {})
    assert api['result']['remote_outcome'] == 'unknown'
    assert ref_value(real.repo.bare, 'main') == real.repo.B
    assert_transition(real, mid, 'aborted')


def test_B5_recorded_lease_equal_to_observed_remote_is_not_published(real, monkeypatch):
    mid, fp = ready(real)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    ctx = saved(mid)
    ctx['expected_remote_head'] = real.repo.B
    gs.db_git.set_session_context(mid, ctx)
    request(real, mid, 'abort')
    progress = saved(mid)['abort_progress']
    assert progress['remote_outcome'] == 'not_published'
    assert progress['remote_observation']['cause'] is None
