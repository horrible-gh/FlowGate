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
