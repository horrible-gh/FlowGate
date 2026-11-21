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
