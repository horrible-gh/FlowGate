"""Independently selectable scenarios; shared fixtures remain in test_git_approval_recovery_crashes_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_approval_recovery_crashes_0153 import (
    approval_intent,
    approve,
    cleanup,
    copy,
    env,
    fail_push,
    git,
    gs,
    names,
    patch_store,
    pending,
    project,
    pytest,
    ready,
    real,
    recovery,
    ref_value,
    repo,
    request,
    saved,
    session_claim,
    subprocess,
    terminal,
    tmp_db,
    uuid,
)

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
