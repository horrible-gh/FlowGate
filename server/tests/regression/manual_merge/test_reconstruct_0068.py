"""Reconstruction preserves work commits through success, conflicts and recovery."""
import pytest
from test_fg_gate_intake_0085 import product, env, db_path, FA
from test_fg_gate_publish_0085 import git_env, _work, _rev, _git


def remote(r, name, content):
    (r['seed'] / name).write_text(content, encoding='utf-8')
    _git(['add', name], cwd=r['seed'])
    _git(['commit', '-m', 'base change'], cwd=r['seed'])
    _git(['push', 'origin', 'main'], cwd=r['seed'])
    return _rev(r['seed'], 'HEAD')


def rebuild():
    from modules.flow_gate.services.git import finalize
    return finalize.update_from_base(FA, require_clean=True, reconstruct=True)['result']


def test_clean_reconstruction_keeps_both_histories(git_env):
    r = git_env
    original = _work(r, 'work.txt', 'work\n')
    base = remote(r, 'base.txt', 'base\n')
    assert rebuild()['status'] == 'updated'
    assert _git(['rev-list', '--parents', '-n', '1', 'HEAD'], cwd=r['wt']).split()[1:] == [base, original]
    assert (r['wt'] / 'work.txt').read_text() == 'work\n'
    assert (r['wt'] / 'base.txt').read_text() == 'base\n'


def test_conflict_abort_restores_original_head(git_env):
    from modules.flow_gate.services.git import conflict
    r = git_env
    original = _work(r, 'same.txt', 'work\n')
    remote(r, 'same.txt', 'base\n')
    out = rebuild()
    assert out['status'] == 'conflict'
    assert _rev(r['wt'], f"refs/flowgate/reconstruction/{out['merge_id']}") == original
    conflict.abort_merge(FA, out['merge_id'])
    assert _rev(r['wt'], 'HEAD') == original
    assert (r['wt'] / 'same.txt').read_text() == 'work\n'
    assert not _git(['status', '--porcelain'], cwd=r['wt'])


def test_dirty_files_are_not_absorbed(git_env):
    from modules.flow_gate.services.git.credentials import GitServiceError
    r = git_env
    original = _work(r, 'work.txt', 'work\n')
    (r['wt'] / 'dirty.txt').write_text('keep', encoding='utf-8')
    with pytest.raises(GitServiceError) as exc:
        rebuild()
    assert exc.value.code == 'uncommitted_changes'
    assert _rev(r['wt'], 'HEAD') == original
    assert (r['wt'] / 'dirty.txt').read_text() == 'keep'


def test_record_failure_restores_work(git_env, monkeypatch):
    from modules.flow_gate.services.git import scope_base
    r = git_env
    original = _work(r, 'work.txt', 'work\n')
    remote(r, 'base.txt', 'base\n')
    def fail(*args, **kwargs):
        raise RuntimeError('record unavailable')
    monkeypatch.setattr(scope_base, 'record_update', fail)
    with pytest.raises(RuntimeError, match='record unavailable'):
        rebuild()
    assert _rev(r['wt'], 'HEAD') == original
    assert (r['wt'] / 'work.txt').read_text() == 'work\n'


def test_expired_conflict_sweep_restores_original(git_env, monkeypatch):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import cleanup
    r = git_env
    original = _work(r, 'same.txt', 'work\n')
    remote(r, 'same.txt', 'base\n')
    out = rebuild()
    monkeypatch.setattr(cleanup, '_ttl_expired', lambda value: True)
    cleanup._sweep_group_update_session(gs.db_git.get_session(out['merge_id']), 'flowgate')
    assert _rev(r['wt'], 'HEAD') == original
    assert gs.db_git.get_session(out['merge_id'])['status'] == 'aborted'


def test_live_reconstruction_is_not_swept(git_env, monkeypatch):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import cleanup
    r = git_env
    original = _work(r, 'work.txt', 'work\n')
    source = remote(r, 'base.txt', 'base\n')
    _git(['fetch', 'origin'], cwd=r['wt'])
    mid = gs.db_git.create_session(FA, [], kind=gs.db_git.SESSION_KIND_GROUP_UPDATE,
        context={'reconstruct_head': original, 'source_sha': source, 'branch': r['branch']})
    _git(['reset', '--keep', source], cwd=r['wt'])
    monkeypatch.setattr(cleanup, '_ttl_expired', lambda value: False)
    cleanup._sweep_group_update_session(gs.db_git.get_session(mid), 'flowgate')
    assert _rev(r['wt'], 'HEAD') == source
    assert gs.db_git.get_session(mid)['status'] == 'open'
    monkeypatch.setattr(cleanup, '_ttl_expired', lambda value: True)
    cleanup._sweep_group_update_session(gs.db_git.get_session(mid), 'flowgate')
    assert _rev(r['wt'], 'HEAD') == original


def test_session_close_failure_rolls_back_floor_and_head(git_env, monkeypatch):
    from modules.flow_gate.services import git_service as gs
    r = git_env
    original = _work(r, 'work.txt', 'work\n')
    remote(r, 'base.txt', 'base\n')
    before = gs.db_git.get_state(FA).get('work_base_sync_sha')
    close = gs.db_git.close_session
    def fail(mid, status, *args, **kwargs):
        if status == 'done':
            raise RuntimeError('close unavailable')
        return close(mid, status, *args, **kwargs)
    monkeypatch.setattr(gs.db_git, 'close_session', fail)
    with pytest.raises(RuntimeError, match='close unavailable'):
        rebuild()
    assert _rev(r['wt'], 'HEAD') == original
    assert gs.db_git.get_state(FA).get('work_base_sync_sha') == before


def test_resolved_reconstruction_records_both_parents(git_env):
    from modules.flow_gate.services import git_service as gs
    from modules.flow_gate.services.git import conflict
    r = git_env
    original = _work(r, 'same.txt', 'work\n')
    source = remote(r, 'same.txt', 'base\n')
    out = rebuild()
    resolved = conflict.resolve_conflicts(FA, out['merge_id'],
        [{'path': 'same.txt', 'content': 'base\nwork\n'}], True)
    assert resolved['result']['status'] == 'updated'
    assert _git(['rev-list', '--parents', '-n', '1', 'HEAD'], cwd=r['wt']).split()[1:] == [source, original]
    assert gs.db_git.get_state(FA)['work_base_sync_sha'] == source
    assert gs.db_git.get_session(out['merge_id'])['status'] == 'done'
