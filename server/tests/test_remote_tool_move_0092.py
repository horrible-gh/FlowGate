"""0092: remote move contract, filesystem refusal/rollback and managed Git routing."""
from __future__ import annotations

import errno
import os
import subprocess
from contextlib import contextmanager

import pytest

from test_remote_tool_0003_T0012 import RAW_TOKEN, _call, env  # noqa: F401
from test_remote_tool_group_worktree_routing_0671 import env as managed_env  # noqa: F401
from modules.flow_gate.services import remote_tool_service as svc
from modules.flow_gate.storage import safe_file_move as fs


def move(source='app/main.py', target='new/deep/renamed.py'):
    return _call('move', {'source_path': source, 'target_path': target})


@pytest.mark.parametrize('target', ['app/renamed.py', 'docs/main.py', 'new/deep/renamed.py'])
def test_move_bytes_source_target_and_audit(env, target):
    env.make_grant(['write'])
    before = (env.src / 'app/main.py').read_bytes()
    status, payload = move(target=target)
    assert status == 200, payload
    assert payload['moved'] and payload['source_path'] == 'app/main.py'
    assert payload['target_path'] == target and payload['size'] == len(before)
    assert not (env.src / 'app/main.py').exists()
    assert (env.src / target).read_bytes() == before
    assert payload['continuation']['next_action'] == 'write_report'
    log = env.oplogs()[-1]
    assert (log['op'], log['result'], log['target_path'], log['target_pattern']) == (
        'move', 'success', 'app/main.py', target)


@pytest.mark.parametrize('target', ['docs/readme.md', 'docs'])
def test_existing_file_or_directory_is_never_overwritten(env, target):
    env.make_grant(['write'])
    source = (env.src / 'app/main.py').read_bytes()
    original_target = (env.src / 'docs/readme.md').read_bytes()
    status, payload = move(target=target)
    assert status == 409, payload
    assert (env.src / 'app/main.py').read_bytes() == source
    assert (env.src / 'docs/readme.md').read_bytes() == original_target
    assert 'continuation' not in payload


@pytest.mark.parametrize('source,target,status', [
    ('missing.py', 'new/deep/missing.py', 404),
    ('missing/parent.py', 'new/deep/missing.py', 404),
    ('app', 'new/deep/app', 422),
    ('app/main.py', 'app/main.py', 422),
    ('app/main.py', './app/main.py', 422),
    ('app/main.py', 'docs/readme.md/invalid.py', 422),
    ('../outside.py', 'new.py', 422),
    ('app/main.py', '../outside.py', 422),
    ('C:/outside.py', 'new.py', 422),
    ('app/main.py', '/outside.py', 422),
    ('app/main.py', 'new.py:stream', 422),
    ('app/main.py', '.git/config', 422),
    ('app/main.py', 'NUL.txt', 422),
    ('app/main.py', 'folder./file.py', 422),
])
def test_invalid_paths_preserve_source(env, source, target, status):
    env.make_grant(['write'])
    before = (env.src / 'app/main.py').read_bytes()
    actual, payload = move(source, target)
    assert actual == status, payload
    assert (env.src / 'app/main.py').read_bytes() == before
    assert not (env.src / 'new').exists()


@pytest.mark.parametrize('body', [{}, {'source_path': 'app/main.py'},
    {'source_path': 'app/main.py', 'target_path': 'x', 'overwrite': True},
    {'source_path': None, 'target_path': 'x'},
    {'source_path': 'app/main.py', 'target_path': ''}])
def test_schema_validation(env, body):
    env.make_grant(['write'])
    assert _call('move', body)[0] == 422


def test_auth_scope_and_priority(env):
    env.make_grant(['read', 'grep'])
    assert _call('move', {}, token=None)[0] == 401
    assert move('../bad', '../bad')[0] == 403
    assert (env.src / 'app/main.py').exists()
    assert env.oplogs()[-1]['result'] == 'denied'


def test_binary_empty_and_mode_preservation(env):
    env.make_grant(['write'])
    for i, data in enumerate([b'', bytes(range(256)) + b'\r\n\x00\xff']):
        path = env.src / f'bin{i}'
        path.write_bytes(data)
        path.chmod(0o755)
        mode = path.stat().st_mode
        status, payload = move(f'bin{i}', f'dest/bin{i}')
        assert status == 200, payload
        assert (env.src / f'dest/bin{i}').read_bytes() == data
        assert (env.src / f'dest/bin{i}').stat().st_mode == mode


@pytest.mark.parametrize('which', ['source', 'target', 'leaf', 'dangling'])
def test_symlink_refusal(env, tmp_path, which):
    env.make_grant(['write'])
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'main.py'
    sentinel.write_bytes(b'outside')
    alias = env.src / 'alias'
    try:
        alias.symlink_to(outside if which != 'dangling' else outside / 'absent', target_is_directory=True)
        (env.src / 'leaf').symlink_to(sentinel)
    except OSError:
        pytest.skip('symlink creation unavailable on this host')
    source, target = 'app/main.py', 'new/main.py'
    if which == 'source':
        source = 'alias/main.py'
    elif which == 'leaf':
        source = 'leaf'
    else:
        target = 'alias/main.py'
    status, payload = move(source, target)
    assert status == 422, payload
    assert sentinel.read_bytes() == b'outside'
    assert (env.src / 'app/main.py').exists()
    assert not (env.src / 'new').exists()


def native_name():
    return '_rename_windows' if os.name == 'nt' else '_rename_linux'


def test_native_failure_rolls_back_created_parents(env, monkeypatch):
    env.make_grant(['write'])
    def fail(*args):
        raise OSError(errno.EBUSY, 'injected rename failure')
    monkeypatch.setattr(fs, native_name(), fail)
    before = (env.src / 'app/main.py').read_bytes()
    status, payload = move()
    assert status == 409, payload
    assert (env.src / 'app/main.py').read_bytes() == before
    assert not (env.src / 'new').exists()


def test_destination_created_at_native_rename_is_not_clobbered(env, monkeypatch):
    env.make_grant(['write'])
    original = getattr(fs, native_name())
    def race(*args):
        (env.src / 'new/deep/renamed.py').write_bytes(b'racing writer')
        return original(*args)
    monkeypatch.setattr(fs, native_name(), race)
    before = (env.src / 'app/main.py').read_bytes()
    status, payload = move()
    assert status == 409, payload
    assert (env.src / 'app/main.py').read_bytes() == before
    assert (env.src / 'new/deep/renamed.py').read_bytes() == b'racing writer'


@pytest.mark.skipif(os.name != 'nt', reason='Windows directory handle pinning')
def test_windows_parent_cannot_be_swapped_during_rename(env, monkeypatch):
    env.make_grant(['write'])
    original = fs._rename_windows
    def race(*args):
        with pytest.raises(PermissionError):
            (env.src / 'new').rename(env.src / 'swapped')
        return original(*args)
    monkeypatch.setattr(fs, '_rename_windows', race)
    assert move()[0] == 200
    assert not (env.src / 'swapped').exists()


@pytest.mark.parametrize('field', ['source_path', 'target_path'])
def test_tr2_ownership_checks_both_paths(env, monkeypatch, field):
    from modules.flow_gate.services import tr2_file_policy as policy
    from modules.flow_gate.db import git_integration as db_git
    env.make_grant(['write'])
    grant = svc._authenticate(RAW_TOKEN)
    grant['group_id'] = 'test-group'
    monkeypatch.setattr(svc, '_authenticate', lambda _: grant)
    monkeypatch.setattr(svc, '_resolve_root_for_mutation', lambda *a: env.src)
    monkeypatch.setattr(db_git, 'get_config', lambda _: {'enabled': True})
    paths = {'source_path': 'app/main.py', 'target_path': 'new/deep/renamed.py'}
    @contextmanager
    def reject(*a, **kw):
        assert kw['exact_paths'] == list(paths.values())
        raise policy.Tr2FilePolicyError(policy.TR2_MANAGED_FILE, paths[field])
        yield
    monkeypatch.setattr(policy, 'general_source_mutation', reject)
    assert move()[0] == 409
    assert (env.src / 'app/main.py').exists()
    assert not (env.src / 'new').exists()


@pytest.mark.parametrize('target', ['renamed.txt', 'nested/seed.txt', 'nested/renamed.txt'])
def test_real_managed_worktree_and_git_index(managed_env, target):
    ctx = managed_env
    token = 'move-real-token'
    ctx.make_worker_token(token, 'g_rt1', token_id='move-real-worker')
    root = ctx.roots['g_rt1']
    other = ctx.roots['g_rt2']
    before = (root / 'seed.txt').read_bytes()
    (root / 'unrelated.txt').write_bytes(b'staged')
    def git(*args):
        result = subprocess.run(['git', *args], cwd=root, capture_output=True, check=True)
        return result.stdout
    git('add', 'unrelated.txt')
    index = git('ls-files', '--stage', '-z')
    head = git('rev-parse', 'HEAD')
    status, payload = svc.handle('move', token, {'source_path': 'seed.txt', 'target_path': target})
    assert status == 200, payload
    assert not (root / 'seed.txt').exists() and (root / target).read_bytes() == before
    assert (other / 'seed.txt').read_bytes() == before and not (other / target).exists()
    assert git('ls-files', '--stage', '-z') == index
    assert git('rev-parse', 'HEAD') == head
    changes = git('status', '--porcelain', '--untracked-files=all').decode()
    assert ' D seed.txt' in changes and '?? ' + target in changes
    assert 'A  unrelated.txt' in changes


@pytest.mark.parametrize('locale', ['ko', 'ja', 'en'])
def test_help_catalog_and_api_tool_schema(locale, monkeypatch):
    from modules.flow_gate.services import tool_registry as registry, api_server_tools as api
    detail = registry.build_tool_detail('move', locale, '/flowgate/api/v1')
    assert detail['path'] == '/remote/move' and detail['scope'] == 'write'
    assert [f['name'] for f in detail['request_fields']] == ['source_path', 'target_path']
    assert 'move' in registry.tool_names('read_write')
    assert 'move' not in registry.tool_names('read')
    monkeypatch.setattr(api, '_step_type', lambda _: 'TR')
    definitions = api.definitions_for_run({'action_scope': 'edit', 'doc_ref': 'tr1'})
    tool = next(t for t in definitions if t['name'] == 'move_source_file')
    assert tool['schema']['required'] == ['source_path', 'target_path']
    api.validate(tool['schema'], {'source_path': 'a', 'target_path': 'b'})
    assert 'move_source_file' not in {t['name'] for t in api.conflict_tool_definitions()}


@pytest.mark.parametrize('field', ['source_path', 'target_path'])
def test_api_snapshot_promotion_guard_checks_both_paths(field):
    from modules.flow_gate.services import source_common
    body = {'source_path': 'a', 'target_path': 'b'}
    body[field] = 'source-snapshots/snap_123/copy.py'
    with pytest.raises(source_common.PromotionBlocked):
        source_common.guard_artifact_promotion('move_source_file', body)


def test_native_filesystem_smoke(tmp_path):
    (tmp_path / 'before').write_bytes(b'raw\x00bytes')
    assert fs.move_file(tmp_path, 'before', 'nested/after') == 9
    assert not (tmp_path / 'before').exists()
    assert (tmp_path / 'nested/after').read_bytes() == b'raw\x00bytes'


@pytest.mark.parametrize('side', ['source', 'target'])
def test_nested_repository_is_outside_assigned_worktree(env, side):
    env.make_grant(['write'])
    repo = env.src / 'nested_repo'
    (repo / '.git').mkdir(parents=True)
    (repo / 'inside.py').write_bytes(b'nested repo')
    source, target = ('nested_repo/inside.py', 'moved.py') if side == 'source' else ('app/main.py', 'nested_repo/new.py')
    status, payload = move(source, target)
    assert status == 422, payload
    assert (repo / 'inside.py').read_bytes() == b'nested repo'
    assert (env.src / 'app/main.py').exists()
    assert not (env.src / 'moved.py').exists()
    assert not (repo / 'new.py').exists()


@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing security')
def test_windows_blocks_source_write_during_move(env, monkeypatch):
    import ctypes
    from ctypes import wintypes as w
    env.make_grant(['write'])
    original = fs._rename_windows
    def race(*args):
        with pytest.raises(PermissionError):
            (env.src / 'app/main.py').write_bytes(b'changed')
        return original(*args)
    monkeypatch.setattr(fs, '_rename_windows', race)
    before = (env.src / 'app/main.py').read_bytes()
    assert fs.move_file(env.src, 'app/main.py', 'new/deep/renamed.py') == len(before)
    assert (env.src / 'new/deep/renamed.py').read_bytes() == before


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction race')
def test_windows_junction_created_at_rename_is_refused(env, monkeypatch, tmp_path):
    import ctypes
    import struct
    from ctypes import wintypes as w
    env.make_grant(['write'])
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'sentinel').write_bytes(b'outside unchanged')
    original = fs._rename_windows
    def race(*args):
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        kernel.CreateFileW.restype = w.HANDLE
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
        kernel.DeviceIoControl.restype = w.BOOL
        handle = kernel.CreateFileW(str(env.src / 'new/deep'), 0x40000000, 7, None, 3, 0x02200000, None)
        if handle == w.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        substitute = ('\\??\\' + str(outside)).encode('utf-16-le')
        display = str(outside).encode('utf-16-le')
        names = substitute + b'\x00\x00' + display + b'\x00\x00'
        data = struct.pack('<LHHHHHH', 0xA0000003, 8 + len(names), 0, 0, len(substitute), len(substitute) + 2, len(display)) + names
        buffer = ctypes.create_string_buffer(data)
        returned = w.DWORD()
        try:
            if not kernel.DeviceIoControl(handle, 0x900A4, buffer, len(data), None, 0, ctypes.byref(returned), None):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(handle)
        return original(*args)
    monkeypatch.setattr(fs, '_rename_windows', race)
    before = (env.src / 'app/main.py').read_bytes()
    status, payload = move()
    assert status == 422, payload
    assert (env.src / 'app/main.py').read_bytes() == before
    assert (outside / 'sentinel').read_bytes() == b'outside unchanged'
    assert not (outside / 'renamed.py').exists()
    assert not (env.src / 'new').exists()
