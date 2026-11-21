"""Independently selectable scenarios; shared fixtures remain in test_git_approval_recovery_managed_0153.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_git_approval_recovery_managed_0153 import (
    _conflict_on,
    _ctx,
    _git,
    _session,
    gs,
    merge_target,
    patch_store,
    proj,
    pytest,
    recovery,
    storage_dir,
    uuid,
)

def test_B3_workspace_release_response_loss_remains_reachable_and_resumable(proj, monkeypatch):
    group = proj.group(1)
    proj.local_branch('develop')
    mid = _conflict_on(proj, group, 'develop')['merge_id']
    context = _ctx(mid)
    context['recovery_hold'] = {'code': 'ttl'}
    gs.db_git.set_session_context(mid, context)
    view = recovery.get(group, mid)['result']
    body = dict(request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'], approval_attempt_id=None)
    write = gs.db_git.set_session_context
    lost = []
    def lose_after_release(merge_id, context):
        if not lost and (context.get('abort_progress') or {}).get('phase') == 'workspace_released':
            lost.append(True)
            raise RuntimeError('lost workspace release acknowledgement')
        return write(merge_id, context)
    monkeypatch.setattr(gs.db_git, 'set_session_context', lose_after_release)
    with pytest.raises(gs.GitServiceError) as exc:
        recovery.mutate(group, mid, 'abort', **body)
    assert exc.value.code == 'recovery_failed'
    assert not proj.workspace('develop').exists()
    assert _ctx(mid)['abort_progress']['phase'] == 'git_cleaned'
    view = recovery.get(group, mid)['result']
    assert view['allowed_actions'] == ['refresh', 'abort']
    assert view['next_action'] == 'resume_abort' and view['probe_error'] is None
    assert all(ref['verification'] == 'verified' for ref in view['preserved_refs'])
    assert {key: view['pending_request'][key] for key in body} == body
    result = recovery.mutate(group, mid, 'abort', **body)
    assert result['result']['session_state'] == 'aborted'
    assert gs.db_git.get_state(group)['status'] == 'waiting'
    assert _git(['rev-parse', _ctx(mid)['abort_snapshot']['ref']], cwd=proj.base).strip() == _ctx(mid)['abort_snapshot']['sha']
