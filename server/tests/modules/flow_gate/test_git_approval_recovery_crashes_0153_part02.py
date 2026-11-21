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
