"""Single-file, no-replace moves anchored to verified source-root directories.

No copy/delete fallback: unsupported native atomic rename fails without changing
the source. The caller holds the normal group/attempt mutation lock throughout.
"""
from __future__ import annotations

import ctypes
import errno
import os
import re
import stat
import sys
from pathlib import Path

from .safe_path import (
    MutationPathUnsafeError, resolve_mutation_target_no_alias,
)


def canonical_move_path(root: Path, value: str) -> str:
    target = resolve_mutation_target_no_alias(root, value, allow_missing_leaf=True)
    parts = target.relative_to(root.resolve()).parts
    # Refuse Git metadata, NT streams/devices, and ambiguous Windows spellings
    # on every host, so the same request has the same meaning across platforms.
    for part in parts:
        if (part.lower() == '.git' or part.endswith((' ', '.'))
                or re.search(r'[\x00-\x1f:*?"<>|]', part)
                or re.fullmatch(r'(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', part)):
            raise MutationPathUnsafeError(value)
    if not parts or value.replace('\\', '/').endswith('/'):
        raise MutationPathUnsafeError(value)
    return '/'.join(parts)


def move_file(root: Path, source: str, destination: str) -> int:
    """Return moved byte count; preserve file bytes, mode, and Git index."""
    if os.name == 'nt':
        return _move_windows(root.resolve(), source, destination)
    if sys.platform.startswith('linux'):
        return _move_linux(root.resolve(), source, destination)
    raise OSError(errno.ENOTSUP, 'atomic no-replace move is unavailable')


def _has_git_marker_fd(fd):
    try:
        os.stat('.git', dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _rename_linux(source_fd, source_name, target_fd, target_name):
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    if rename is None:
        raise OSError(errno.ENOTSUP, 'renameat2 is unavailable')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(source_name), target_fd, os.fsencode(target_name), 1):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _move_linux(root, source, destination):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    handles = [os.open(root, flags)]
    created = []
    completed = False

    def parent(parts, create=False):
        fd = handles[0]
        for part in parts:
            made = False
            try:
                child = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, dir_fd=fd)
                    made = True
                except FileExistsError:
                    pass
                if made:
                    created.append((fd, part, os.stat(part, dir_fd=fd, follow_symlinks=False)))
                child = os.open(part, flags, dir_fd=fd)
            handles.append(child)
            if _has_git_marker_fd(child):
                raise MutationPathUnsafeError(part)
            # Do not cross a mount into a different repository/filesystem.
            if os.fstat(child).st_dev != os.fstat(handles[0]).st_dev:
                raise MutationPathUnsafeError(part)
            fd = child
        return fd

    try:
        src = source.split('/')
        dst = destination.split('/')
        src_fd = parent(src[:-1])
        before = os.stat(src[-1], dir_fd=src_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode):
            raise MutationPathUnsafeError(source)
        dst_fd = parent(dst[:-1], create=True)
        current = os.stat(src[-1], dir_fd=src_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise OSError(errno.EBUSY, 'source changed during move')
        _rename_linux(src_fd, src[-1], dst_fd, dst[-1])
        completed = True
        return before.st_size
    finally:
        if not completed:
            for fd, name, identity in reversed(created):
                try:
                    current = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                        os.rmdir(name, dir_fd=fd)
                except OSError:
                    pass  # Never remove a replaced directory or another writer's files.
        for fd in reversed(handles):
            os.close(fd)


def _move_windows(root, source, destination):
    from ctypes import wintypes as w

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                  w.DWORD, w.DWORD, w.HANDLE]
    kernel.CreateFileW.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    kernel.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.GetFileInformationByHandleEx.restype = w.BOOL
    kernel.SetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.SetFileInformationByHandle.restype = w.BOOL
    kernel.GetFileSizeEx.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
    kernel.GetFileSizeEx.restype = w.BOOL
    handles = []
    parents = {}
    completed = False

    def parent(parts, create=False):
        handle = root_handle
        for index, part in enumerate(parts):
            key = tuple(parts[:index + 1])
            if key in parents:
                handle = parents[key]
                continue
            child, made = _open_child_windows(handle, part, directory=True, create=create)
            handles.append((child, made))
            _check_windows_type(kernel, child, directory=True)
            # A nested repository is outside this assigned worktree.
            try:
                marker, _ = _open_child_windows(child, '.git', inspect=True)
            except FileNotFoundError:
                pass
            else:
                kernel.CloseHandle(marker)
                raise MutationPathUnsafeError(part)
            parents[key] = child
            handle = child
        return handle

    try:
        # Root comes from the existing authorized worktree resolver. Every child
        # lookup/creation below is relative to a held directory handle, never an
        # absolute path that could traverse an ancestor changed into a junction.
        root_handle = kernel.CreateFileW(str(root), 0x81, 3, None, 3, 0x02200000, None)
        if root_handle == w.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        handles.append((root_handle, False))
        _check_windows_type(kernel, root_handle, directory=True)
        src, dst = source.split('/'), destination.split('/')
        source_parent = parent(src[:-1])
        source_handle, _ = _open_child_windows(source_parent, src[-1])
        handles.append((source_handle, False))
        _check_windows_type(kernel, source_handle, directory=False)
        size = ctypes.c_longlong()
        if not kernel.GetFileSizeEx(source_handle, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        target_parent = parent(dst[:-1], create=True)
        try:
            existing, _ = _open_child_windows(target_parent, dst[-1], inspect=True)
        except FileNotFoundError:
            pass
        else:
            kernel.CloseHandle(existing)
            raise FileExistsError(errno.EEXIST, 'target exists')
        for directory_handle in [root_handle, *parents.values()]:
            _check_windows_type(kernel, directory_handle, directory=True)
        _rename_windows(kernel, source_handle, target_parent, dst[-1])
        completed = True
        return size.value
    finally:
        for handle, made in reversed(handles):
            if made and not completed:
                # Created directory handles have DELETE access. Delete only our
                # empty directories by handle; a junction swap cannot redirect
                # rollback to somebody else's path. Never delete nonempty dirs.
                delete = w.BOOLEAN(True)
                kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(delete), ctypes.sizeof(delete))
            kernel.CloseHandle(handle)


def _check_windows_type(kernel, handle, *, directory):
    from ctypes import wintypes as w

    class AttributeTag(ctypes.Structure):
        _fields_ = [('attributes', w.DWORD), ('tag', w.DWORD)]

    tag = AttributeTag()
    if not kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
        raise ctypes.WinError(ctypes.get_last_error())
    if tag.attributes & 0x400 or bool(tag.attributes & 0x10) != directory:
        raise MutationPathUnsafeError('aliased or non-regular move path')


def _open_child_windows(parent, name, *, directory=False, create=False, inspect=False):
    """Open one leaf without following reparse points, relative to a pinned parent."""
    from ctypes import wintypes as w

    class UnicodeString(ctypes.Structure):
        _fields_ = [('length', w.USHORT), ('maximum', w.USHORT), ('buffer', w.LPWSTR)]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [('length', w.ULONG), ('root', w.HANDLE), ('name', ctypes.POINTER(UnicodeString)),
                    ('attributes', w.ULONG), ('security', ctypes.c_void_p), ('qos', ctypes.c_void_p)]

    class IOStatus(ctypes.Structure):
        _fields_ = [('status_or_pointer', ctypes.c_void_p), ('information', ctypes.c_size_t)]

    native = ctypes.WinDLL('ntdll', use_last_error=True)
    native.NtCreateFile.argtypes = [ctypes.POINTER(w.HANDLE), w.DWORD, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD]
    native.NtCreateFile.restype = w.LONG
    native.RtlNtStatusToDosError.argtypes = [w.LONG]
    native.RtlNtStatusToDosError.restype = w.DWORD
    name_buffer = ctypes.create_unicode_buffer(name)
    encoded_length = len(name.encode('utf-16-le'))
    if encoded_length > 65532:
        raise MutationPathUnsafeError('move path component too long')
    text = UnicodeString(encoded_length, encoded_length + 2, ctypes.cast(name_buffer, w.LPWSTR))
    attributes = ObjectAttributes(ctypes.sizeof(ObjectAttributes), parent, ctypes.pointer(text), 0x40, None, None)
    handle, status = w.HANDLE(), IOStatus()
    access = 0x100000 | 0x80 | (1 if directory else 0 if inspect else 0x10000)
    share = 7 if inspect else 3 if directory else 1
    options = 0x200020 | (1 if directory else 0)  # OPEN_REPARSE_POINT + synchronous

    def open_leaf(disposition, rights):
        result = native.NtCreateFile(ctypes.byref(handle), rights, ctypes.byref(attributes), ctypes.byref(status),
            None, 0, share, disposition, options, None, 0)
        if result < 0:
            raise ctypes.WinError(native.RtlNtStatusToDosError(result))

    if create:
        try:
            open_leaf(2, access | 0x10000)  # FILE_CREATE; DELETE only for our new directories
            return handle.value, True
        except FileExistsError:
            pass
    open_leaf(1, access)  # FILE_OPEN
    return handle.value, False


def _rename_windows(kernel, source_handle, target_parent, target_name):
    from ctypes import wintypes as w

    class RenameInfo(ctypes.Structure):
        _fields_ = [('replace', w.BOOLEAN), ('root', w.HANDLE),
                    ('length', w.DWORD), ('name', w.WCHAR * (len(target_name.encode('utf-16-le')) // 2 + 1))]

    class IOStatus(ctypes.Structure):
        _fields_ = [('status_or_pointer', ctypes.c_void_p), ('information', ctypes.c_size_t)]

    _check_windows_type(kernel, target_parent, directory=True)
    native = ctypes.WinDLL('ntdll', use_last_error=True)
    native.NtSetInformationFile.argtypes = [w.HANDLE, ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.c_int]
    native.NtSetInformationFile.restype = w.LONG
    native.RtlNtStatusToDosError.argtypes = [w.LONG]
    native.RtlNtStatusToDosError.restype = w.DWORD
    info = RenameInfo()
    info.root = target_parent
    info.length = len(target_name.encode('utf-16-le'))
    info.name = target_name
    status = IOStatus()
    result = native.NtSetInformationFile(source_handle, ctypes.byref(status), ctypes.byref(info), ctypes.sizeof(info), 10)
    if result < 0:
        raise ctypes.WinError(native.RtlNtStatusToDosError(result))
