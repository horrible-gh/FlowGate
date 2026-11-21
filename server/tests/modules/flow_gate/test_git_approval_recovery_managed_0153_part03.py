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

@pytest.mark.parametrize('changed', ['worktree', 'head'])
def test_B3_abort_resume_does_not_remove_changes_created_after_git_cleanup(proj, monkeypatch, changed):
    group = proj.group(1)
    proj.local_branch('develop')
    mid = _conflict_on(proj, group, 'develop')['merge_id']
    ctx = _ctx(mid)
    ctx['recovery_hold'] = {'code': 'ttl'}
    gs.db_git.set_session_context(mid, ctx)
    view = recovery.get(group, mid)['result']
    body = dict(request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'], approval_attempt_id=None)
    release = merge_target.release_workspace
    monkeypatch.setattr(merge_target, 'release_workspace', lambda target: False)
    with pytest.raises(gs.GitServiceError) as exc:
        recovery.mutate(group, mid, 'abort', **body)
    assert exc.value.code == 'recovery_workspace_release_failed'
    assert _ctx(mid)['abort_progress']['phase'] == 'git_cleaned'
    monkeypatch.setattr(merge_target, 'release_workspace', release)
    ws = proj.workspace('develop') / 'tree'
    (ws / 'later.txt').write_text('new work after cleanup\n', encoding='utf-8')
    if changed == 'head':
        _git(['add', 'later.txt'], cwd=ws)
        _git(['commit', '-m', 'later work'], cwd=ws)
    head = _git(['rev-parse', 'HEAD'], cwd=ws)
    view = recovery.get(group, mid)['result']
    assert view['allowed_actions'] == ['refresh']
    assert 'recovery_local_mismatch' in view['blocked_reasons']
    with pytest.raises(gs.GitServiceError) as exc:
        recovery.mutate(group, mid, 'abort', **body)
    assert exc.value.code == 'recovery_local_mismatch'
    assert ws.exists() and (ws / 'later.txt').read_text() == 'new work after cleanup\n'
    assert _git(['rev-parse', 'HEAD'], cwd=ws) == head
    assert _session(mid)['status'] == 'open' and gs.db_git.get_state(group)['status'] == 'conflict'
