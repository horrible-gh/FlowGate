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
