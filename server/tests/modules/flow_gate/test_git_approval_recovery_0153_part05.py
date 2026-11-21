"""Independently selectable scenarios; shared fixtures remain in test_git_approval_recovery_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_approval_recovery_0153 import (
    PROJECT,
    REAL_COMPLETE,
    REAL_LOCK,
    REAL_OWNER,
    REAL_UNLOCK,
    REAL_VALIDATE,
    SimpleNamespace,
    TRANSITIONS,
    approval_intent,
    approve,
    approve_ready,
    approved,
    assert_transition,
    bmp,
    copy,
    crash_after_first_preservation,
    documents,
    env,
    fa,
    fail_push,
    gc,
    get_store,
    git,
    git_rc,
    gs,
    instance_registry,
    json,
    legacy_commit_without_round,
    lock_manager,
    merge_commit,
    merge_target,
    names,
    on,
    open_session,
    parents_of,
    patch_store,
    pending,
    preserve_refs,
    project,
    push_timeout,
    pytest,
    ready,
    real,
    recovery,
    ref_value,
    repo,
    request,
    route_call,
    saved,
    session_claim,
    subprocess,
    terminal,
    tmp_db,
    uuid,
)

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
