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
