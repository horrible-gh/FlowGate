"""Durable abort admission across released leases and lost database acknowledgements."""
import copy
import subprocess
import uuid

import pytest

from tests.modules.flow_gate.test_git_approval_recovery_0153 import (
    real, env, project, repo, patch_store, tmp_db, ready, request, saved, approve,
    fail_push, names, git, ref_value, pending, terminal,
)
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import recovery, approval_intent, cleanup, session_claim


@pytest.mark.parametrize('failure', ['reset', 'db_after_git'])
def test_B3_B4_incomplete_abort_survives_released_claim_sweep_and_startup(real, monkeypatch, failure):
    mid, fp = ready(real)
    fail_push(real)
    attempt = str(uuid.uuid4())
    approve(real, mid, attempt, fp)
    view = recovery.get(real.group, mid)['result']
    body = dict(request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'], approval_attempt_id=attempt)
    if failure == 'reset':
        hook = lambda args: subprocess.CompletedProcess(args, 1, '', 'reset refused') if args[0] == 'reset' else None
        real.hooks.append(hook)
    else:
        write = gs.db_git.set_session_context
        failed = []
        def lose(mid, ctx):
            if not failed and (ctx.get('abort_progress') or {}).get('phase') == 'git_cleaned':
                failed.append(True)
                raise RuntimeError('lost database acknowledgement')
            return write(mid, ctx)
        monkeypatch.setattr(gs.db_git, 'set_session_context', lose)
    with pytest.raises(gs.GitServiceError):
        request(real, mid, 'abort', body)
    # The failed request's claim/lock is gone. This is the dangerous next sweep,
    # rather than a competing call that is merely blocked by a still-live lock.
    before = copy.deepcopy(saved(mid))
    assert before['abort_progress']['phase'] == 'prepared'
    real.commands.clear()
    assert gs.reconcile_push_session(mid, trigger='server_startup') is None
    gs.merge_session_sweep(sessions=[gs.db_git.get_session(mid)])
    claim = session_claim.acquire(mid, session_claim.PURPOSE_STARTUP)
    assert claim is not None
    try:
        cleanup._startup_group_session(gs.db_git.get_session(mid), gs._session_project(gs.db_git.get_session(mid)))
    finally:
        session_claim.release(claim)
    assert saved(mid) == before and not real.commands
    pending(real, mid)
    view = recovery.get(real.group, mid)['result']
    assert view['allowed_actions'] == ['refresh', 'abort']
    assert view['next_action'] == 'resume_abort'
    assert {k: view['pending_request'][k] for k in body} == body
    if failure == 'reset':
        real.hooks.remove(hook)
    real.commands.clear()
    request(real, mid, 'abort', body)
    assert gs.db_git.get_session(mid)['status'] == 'aborted'
    assert gs.db_git.get_state(real.group)['status'] == 'waiting'
    assert names(real).count('reset') == int(failure == 'reset')
    assert 'push' not in names(real) and 'commit' not in names(real)


@pytest.mark.parametrize('phase', ['initial', 'prepared'])
def test_B2_staged_and_working_versions_cannot_be_silently_discarded(real, phase):
    mid, fp = ready(real)
    body = None
    if phase == 'prepared':
        hook = lambda args: subprocess.CompletedProcess(args, 1, '', 'abort interrupted') if args[:2] == ['merge', '--abort'] else None
        real.hooks.append(hook)
        view = recovery.get(real.group, mid)['result']
        body = dict(request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'], approval_attempt_id=None)
        with pytest.raises(gs.GitServiceError) as exc:
            request(real, mid, 'abort', body)
        assert exc.value.code == 'recovery_abort_failed'
        assert saved(mid)['abort_progress']['phase'] == 'prepared'
        real.hooks.remove(hook)
        # Keep the saved worktree bytes, but stage a new distinct version after
        # the durable snapshot. A replay must recheck index preservation too.
        working = (real.repo.work / 'f.txt').read_text(encoding='utf-8')
        (real.repo.work / 'f.txt').write_text('new staged version\n', encoding='utf-8')
        git(['add', 'f.txt'], real.repo.work)
        (real.repo.work / 'f.txt').write_text(working, encoding='utf-8')
    else:
        (real.repo.work / 'f.txt').write_text('unsaved working version\n', encoding='utf-8')
    working = (real.repo.work / 'f.txt').read_text(encoding='utf-8')
    staged = git(['show', ':f.txt'], real.repo.work)
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, 'abort', body)
    assert exc.value.code == 'recovery_unpreserved_index'
    assert git(['show', ':f.txt'], real.repo.work) == staged
    assert (real.repo.work / 'f.txt').read_text() == working
    assert ref_value(real.repo.work, 'MERGE_HEAD') == real.repo.F
    assert not any(c in names(real) for c in ('reset', 'merge', 'commit-tree', 'update-ref'))
    pending(real, mid)


def test_B3_a_new_merge_head_is_not_owned_by_an_already_committed_attempt(real):
    mid, fp = ready(real)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    git(['checkout', '-b', 'other-attempt', real.repo.B], real.repo.work)
    (real.repo.work / 'other.txt').write_text('other attempt\n', encoding='utf-8')
    git(['add', 'other.txt'], real.repo.work)
    git(['commit', '-m', 'other attempt'], real.repo.work)
    foreign = ref_value(real.repo.work, 'HEAD')
    git(['checkout', 'main'], real.repo.work)
    git(['merge', '--no-ff', '--no-commit', foreign], real.repo.work)
    tree = git(['write-tree'], real.repo.work)
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, 'abort')
    assert exc.value.code == 'recovery_local_mismatch'
    assert ref_value(real.repo.work, 'MERGE_HEAD') == foreign
    assert git(['write-tree'], real.repo.work) == tree
    assert not any(c in names(real) for c in ('reset', 'merge', 'commit-tree', 'update-ref'))
    pending(real, mid)


def test_B3_observed_publication_without_intent_still_offers_cleanup(real):
    mid, fp = ready(real)
    ctx = saved(mid)
    ctx.pop(approval_intent.INTENT_KEY)
    gs.db_git.set_session_context(mid, ctx)
    fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    git(['push', 'origin', 'main'], real.repo.work)
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid, 'abort')
    assert exc.value.code == 'recovery_already_published'
    view = recovery.get(real.group, mid)['result']
    assert view['git_terminal'] and view['requires_recovery']
    assert view['allowed_actions'] == ['refresh', 'retry']
    assert view['approval']['stage'] == 'not_required'
    out, _ = request(real, mid)
    assert out['result']['session_state'] == 'done' and not out['result']['requires_recovery']
    assert not any(c in names(real) for c in ('reset', 'merge', 'push', 'commit'))


def test_R3_hold_cannot_be_bypassed_by_another_approval_id(real):
    mid, fp = ready(real)
    ctx = saved(mid)
    ctx['recovery_hold'] = {'code': 'ttl'}
    gs.db_git.set_session_context(mid, ctx)
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        approve(real, mid, str(uuid.uuid4()), fp)
    assert exc.value.code == 'recovery_required'
    assert saved(mid) == ctx and not real.commands


def test_B1_auth_hold_abort_receipt_reports_success_and_retains_original_cause(real, monkeypatch):
    mid, fp = ready(real)
    fail_push(real, 'auth')
    approve(real, mid, str(uuid.uuid4()), fp)
    discard = approval_intent.discard_intent
    calls = []
    def counted(*args, **kwargs):
        calls.append(True)
        return discard(*args, **kwargs)
    monkeypatch.setattr(approval_intent, 'discard_intent', counted)
    result, body = request(real, mid, 'abort')
    assert result['ok'] and result['result']['session_state'] == 'aborted'
    assert saved(mid)['abort_progress']['cause'] == gs.RECONCILE_KIND_AUTH_REQUIRED
    assert saved(mid)['first_push_error']['cause'] == 'auth'
    assert not saved(mid).get('reconciliation_kind')
    real.commands.clear()
    replay, _ = request(real, mid, 'abort', body)
    assert replay['ok'] and replay['result']['session_state'] == 'aborted'
    assert not real.commands and len(calls) == 1


def test_R4_proven_precommit_interruption_reopens_candidate_once(real):
    mid, fp = ready(real)
    attempt = str(uuid.uuid4())
    ctx = saved(mid)
    recovery.bind_attempt(gs.db_git.get_session(mid), ctx, attempt, fp)
    ctx.update(review_state='applying', apply_phase='committing')
    gs.db_git.set_session_context(mid, ctx)
    real.commands.clear()
    out, body = request(real, mid)
    assert out['result']['review_state'] == gs.REVIEW_STATE_PENDING
    assert saved(mid)['approval_attempt_id'] is None
    assert saved(mid)['approval_attempts'][attempt]['phase'] == 'not_committed'
    assert saved(mid)['review_fingerprint'] == fp
    assert not any(c in names(real) for c in ('push', 'commit', 'reset'))
    real.commands.clear()
    request(real, mid, body=body)
    assert not real.commands
    assert approve(real, mid, attempt, fp)['result']['status'] == 'not_committed'
    assert not real.commands
    pending(real, mid)


def test_R1_prepared_retry_replays_one_budget_when_reconcile_lock_was_busy(real, monkeypatch):
    mid, fp = ready(real)
    hook = fail_push(real)
    approve(real, mid, str(uuid.uuid4()), fp)
    real.hooks.remove(hook)
    reconcile = gs.reconcile_push_session
    monkeypatch.setattr(gs, 'reconcile_push_session', lambda *a, **kw: None)
    first, body = request(real, mid)
    assert first['result']['deferred'] is True
    assert first['result']['receipt']['phase'] == 'reconcile_prepared'
    assert len(saved(mid)['recovery_retry_history']) == 1
    monkeypatch.setattr(gs, 'reconcile_push_session', reconcile)
    result, _ = request(real, mid, body=body)
    assert result['result']['approval']['approved']
    assert len(saved(mid)['recovery_retry_history']) == 1
    assert names(real).count('commit') == 1
    terminal(real, mid)


@pytest.mark.parametrize('failure', ['auth', 'preserve'])
def test_P1_initial_approve_failure_has_explicit_api_truth(real, monkeypatch, failure):
    from modules.flow_gate.api.v1 import git_routes as routes
    mid, fp = ready(real)
    if failure == 'auth':
        fail_push(real, 'auth')
    else:
        real.hooks.append(lambda args: subprocess.CompletedProcess(args, 1, '', 'ref write failed')
                          if args[0] == 'update-ref' else None)
    monkeypatch.setattr(routes, '_has_permission', lambda *a: True)
    attempt = str(uuid.uuid4())
    out = routes.post_merge_review_approve(real.group, mid,
        routes.ApproveBody(attempt_id=attempt, review_fingerprint=fp), {'user_id': 'reviewer'})
    assert out['ok'] is True  # legacy approve HTTP compatibility, explicit failure below
    assert out['result']['approval']['approved'] is False
    assert out['result']['git_terminal'] is False
    assert out['result']['approval_attempt_id'] == attempt
    assert isinstance(out['result']['error'], dict) and out['result']['error']['code']
    assert out['result']['recovery']['requires_recovery']
    pending(real, mid)


def test_C2_mismatched_intent_does_not_guess_an_ac_or_repeat_git(real, monkeypatch):
    from modules.flow_gate.workflow import pipeline_service
    mid, fp = ready(real)
    ctx = saved(mid)
    ctx[approval_intent.INTENT_KEY]['group_id'] = real.group + '-other'
    gs.db_git.set_session_context(mid, ctx)
    def forbidden(**kwargs):
        raise AssertionError('a mismatched intent must not enter the approval transaction')
    monkeypatch.setattr(pipeline_service, 'commit_final_approval', forbidden)
    out = approve(real, mid, str(uuid.uuid4()), fp)['result']
    assert out['git_terminal'] and not out['approval']['approved']
    assert out['approval']['error']['code'] == 'intent_group_mismatch'
    assert gs.db_git.get_state(real.group)['status'] == 'merged'
    real.commands.clear()
    with pytest.raises(gs.GitServiceError) as exc:
        request(real, mid)
    assert exc.value.code == 'intent_group_mismatch' and not real.commands
    from modules.flow_gate.db import documents
    assert documents.get_by_id(real.ac_id)['doc_review_status'] == 'pending_review'
    assert documents.get_by_id(real.root_id)['doc_review_status'] == 'wf_in_progress'
    assert saved(mid)[approval_intent.INTENT_KEY] == ctx[approval_intent.INTENT_KEY]
