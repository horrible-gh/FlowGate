"""Preservation abort on an owned managed target, with release failures."""
import uuid
import pytest
from tests.modules.flow_gate.test_git_merge_target_0594 import (
    storage_dir, patch_store, proj, _conflict_on, _git, _ctx, _session,
)
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import recovery, merge_target


@pytest.mark.parametrize('failure', [None, 'release'])
def test_B2_B3_managed_pending_bytes_are_preserved_before_workspace_release(proj, monkeypatch, failure):
    group = proj.group(1)
    proj.local_branch('develop')
    base_sha = proj.base_sha()
    out = _conflict_on(proj, group, 'develop')
    mid = out['merge_id']
    ws = proj.workspace('develop') / 'tree'
    remote_sha = _git(['rev-parse', 'develop'], cwd=proj.bare).strip()
    before = _ctx(mid)
    before['recovery_hold'] = {'code': 'ttl', 'resume_missing': []}
    gs.db_git.set_session_context(mid, before)
    (ws / 'shared.txt').write_text('new pending user bytes\n', encoding='utf-8')
    original_release = merge_target.release_workspace
    if failure:
        monkeypatch.setattr(merge_target, 'release_workspace', lambda target: False)
    view = recovery.get(group, mid)['result']
    body = dict(request_id=str(uuid.uuid4()), recovery_version=view['recovery_version'], approval_attempt_id=None)
    if failure:
        with pytest.raises(gs.GitServiceError) as exc:
            recovery.mutate(group, mid, 'abort', **body)
        assert exc.value.code == 'recovery_workspace_release_failed'
        assert _session(mid)['status'] == 'open'
        assert _ctx(mid)['abort_progress']['phase'] == 'git_cleaned'
        assert gs.db_git.get_state(group)['status'] == 'conflict' and ws.exists()
        monkeypatch.setattr(merge_target, 'release_workspace', original_release)
    result = recovery.mutate(group, mid, 'abort', **body)
    assert result['result']['session_state'] == 'aborted'
    assert not proj.workspace('develop').exists()
    snapshot = _ctx(mid)['abort_snapshot']
    assert _git(['show', snapshot['ref'] + ':shared.txt'], cwd=proj.base) == 'new pending user bytes\n'
    assert _git(['rev-list', '--parents', '-n', '1', snapshot['sha']], cwd=proj.base).split()[1:] == snapshot['parents']
    assert proj.base_sha() == base_sha and proj.base_clean()
    assert _git(['rev-parse', 'develop'], cwd=proj.bare).strip() == remote_sha
    assert gs.db_git.get_state(group)['status'] == 'waiting'
    # Another group can subsequently use the same owned target.
    other = proj.group(2)
    assert other != group and gs.base_merge_in_progress(proj.pid) is None
    (proj.wt(other) / 'next.txt').write_text('next group work\n', encoding='utf-8')
    proj.ready(other)
    next_result = gs.finalize(other, 'merge', target_branch='develop')
    assert next_result['result']['status'] == 'merged'
    assert proj.origin_show('develop', 'next.txt') == 'next group work\n'
    assert proj.base_sha() == base_sha and proj.base_clean()
