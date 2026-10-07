"""Source fingerprint: the one scan, exclusion and hash rule set for a Group worktree.

0684 T#2 (D#1 §2-1, §3-3, §3-6): moved here from the Source Bundle materializer so the
Test Basis no longer depends on Bundles. Three measurements share the rules:

* ``inspect_source`` -- Live Probe: hash the worktree in place (decision paths: run
  finalize, manual result submission, TSR gate).
* ``copy_measured`` -- copy the worktree into a run's disposable root and fingerprint the
  bytes actually copied, in one read (a spec run's preparing phase).
* ``memo_lookup`` -- display paths: the last measured fingerprint while every scanned path,
  size and mtime is unchanged. It never hashes; a miss is "not measured", not a measurement.

The materializer keeps importing these names for the Bundle code until T#4 removes it.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import time
from pathlib import Path, PurePosixPath, PureWindowsPath

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.services import git_service
from modules.flow_gate.services.source_common import (
    EXCLUDED_DIR_NAMES, EXCLUDED_DIR_PREFIXES, EXCLUDED_FILE_NAMES,
)

# The scan/exclusion/hash rules are the ones Source Bundle v1 captured with, so a
# fingerprint over the same tree is the same value either way.
POLICY_VERSION = "source-fingerprint-v1"


def _limit(name: str, legacy: str, default: int) -> int:
    """Neutral env name first; the Source Bundle name is still read (D#1 §7)."""
    raw = os.getenv(name) or os.getenv(legacy) or str(default)
    return max(1, int(raw))


MAX_FILES = _limit("FLOWGATE_SOURCE_MAX_FILES", "FLOWGATE_BUNDLE_MAX_FILES", 20000)
MAX_FILE_BYTES = _limit("FLOWGATE_SOURCE_MAX_FILE_BYTES", "FLOWGATE_BUNDLE_MAX_FILE_BYTES",
                        100 * 1024 * 1024)
MAX_TOTAL_BYTES = _limit("FLOWGATE_SOURCE_MAX_TOTAL_BYTES", "FLOWGATE_BUNDLE_MAX_TOTAL_BYTES",
                         500 * 1024 * 1024)
MEASURE_SECONDS = _limit("FLOWGATE_SOURCE_MEASURE_SECONDS", "FLOWGATE_BUNDLE_BUILD_SECONDS", 120)
_SECRET = re.compile(
    r"^(?:\.env(?:\..*)?|\.netrc|\.npmrc|\.pypirc|credentials|credentials\.json|"
    r"service-account\.json|service_account\.json|secrets|id_(?:rsa|dsa|ecdsa|ed25519))$"
    r"|(?:^|[._-])(?:credentials?|secrets?|private[-_]key|certificates?)(?:[._-]|$)"
    r"|\.(?:pem|key|p12|pfx|p8|crt|cer|jks|keystore)$", re.IGNORECASE,
)


class SourceFingerprintError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _safe_name(value: str) -> str:
    value = str(value)
    if "\\" in value:
        raise SourceFingerprintError("unsafe_path", "unsafe source path")
    if not value or value.startswith("/") or PureWindowsPath(value).drive or ":" in value:
        raise SourceFingerprintError("unsafe_path", "unsafe source path")
    if any(part in ("", ".", "..") or any(ord(c) < 32 for c in part) for part in value.split("/")):
        raise SourceFingerprintError("unsafe_path", "unsafe source path")
    return value


def _linked(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


# The execute bits a run's copy carries (``_keep_mode``) change what a Case can run, so on
# POSIX they are part of the fingerprint, the memo's scan signature and the unchanged-file
# check. Windows has no execute bit: it synthesizes one from the file extension
# (.bat/.cmd/.exe/.com) in Path.lstat()/os.fstat() but not in DirEntry.stat(), and the
# copy keeps no mode there, so the bits are ignored.
_EXEC_BITS = 0 if os.name == "nt" else 0o111


def _exec_bits(st) -> int:
    return stat.S_IMODE(st.st_mode) & _EXEC_BITS


def _mode(st) -> int:
    # Content is pinned by the hash; of the execute bits only the real ones count.
    return (st.st_mode & ~0o111) | _exec_bits(st)


def _same(a, b) -> bool:
    if (_mode(a), a.st_size, a.st_mtime_ns) != (_mode(b), b.st_size, b.st_mtime_ns):
        return False
    a_identity = (int(getattr(a, "st_dev", 0)), int(getattr(a, "st_ino", 0)))
    b_identity = (int(getattr(b, "st_dev", 0)), int(getattr(b, "st_ino", 0)))
    # Some Windows filesystems report zero inode/device through DirEntry.stat().
    return not all(a_identity + b_identity) or a_identity == b_identity


def _check_time(deadline):
    if time.monotonic() > deadline:
        raise SourceFingerprintError("build_timeout", "source measurement exceeded its time ceiling")


def resolve_worktree(project_id: str, group_id: str) -> Path:
    group = db_groups.get_by_id(group_id)
    if group is None or group.get("project_id") != project_id:
        raise SourceFingerprintError("group_worktree_unavailable", "group is not in the requested project")
    root, reason = git_service.effective_src_root_ex(project_id, group_id)
    if root is None:
        raise SourceFingerprintError("group_worktree_unavailable", f"exact group worktree unavailable ({reason})")
    root = Path(root)
    try:
        st = root.lstat()
        if _linked(st) or not stat.S_ISDIR(st.st_mode) or not git_service._worktree_link_ok(root):
            raise SourceFingerprintError("group_worktree_unavailable", "exact group worktree is unsafe")
        return root.resolve(strict=True)
    except OSError as exc:
        raise SourceFingerprintError("group_worktree_unavailable", "exact group worktree unavailable") from exc


def _identity(root: Path, deadline):
    _check_time(deadline)
    timeout = max(1, min(30, int(deadline - time.monotonic())))
    head = git_service._run_git(["rev-parse", "HEAD"], cwd=root, timeout=timeout)
    _check_time(deadline)
    timeout = max(1, min(30, int(deadline - time.monotonic())))
    status = git_service._run_git(["status", "--porcelain", "-z", "--untracked-files=all"], cwd=root, timeout=timeout)
    _check_time(deadline)
    if head.returncode or status.returncode or not head.stdout.strip():
        raise SourceFingerprintError("revision_unavailable", "worktree revision unavailable")
    return head.stdout.strip(), bool(status.stdout)


def _excluded(relative: str, is_dir: bool) -> bool:
    name = PurePosixPath(relative).name
    if name in EXCLUDED_DIR_NAMES or (is_dir and name in {"source-bundles", "source-bundle-scratch"}) or name.startswith(EXCLUDED_DIR_PREFIXES):
        return True
    if is_dir:
        return bool(_SECRET.search(name))
    return name in EXCLUDED_FILE_NAMES or any(bool(_SECRET.search(part)) for part in PurePosixPath(relative).parts)


def excluded(path: str) -> bool:
    """Whether the exclusion policy leaves the source-relative ``path`` out of every measure."""
    parts = path.split("/")
    return (any(_excluded("/".join(parts[:end]), True) for end in range(1, len(parts)))
            or _excluded(path, False))


def _scan(root: Path, deadline):
    files = []
    dirs = []
    pending = [("", root)]
    total = 0
    while pending:
        _check_time(deadline)
        prefix, directory = pending.pop()
        try:
            children = list(os.scandir(directory))
        except OSError as exc:
            raise SourceFingerprintError("source_changed", "source directory changed during scan") from exc
        for child in children:
            relative = _safe_name(f"{prefix}/{child.name}" if prefix else child.name)
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise SourceFingerprintError("source_changed", "source entry changed during scan") from exc
            is_dir = stat.S_ISDIR(st.st_mode)
            if _excluded(relative, is_dir):
                continue
            if _linked(st):
                raise SourceFingerprintError("unsafe_path", "source contains a symlink or reparse point")
            if is_dir:
                dirs.append((relative, st))
                pending.append((relative, Path(child.path)))
            elif stat.S_ISREG(st.st_mode):
                if st.st_size > MAX_FILE_BYTES:
                    raise SourceFingerprintError("file_limit", "source single file limit exceeded")
                files.append((relative, st))
                total += st.st_size
                if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise SourceFingerprintError("resource_limit", "source resource limit exceeded")
            else:
                raise SourceFingerprintError("unsafe_path", "source contains a special file")
    return sorted(files), sorted(dirs)


def _safe_file(root: Path, relative: str, expected):
    path = root
    for part in PurePosixPath(_safe_name(relative)).parts:
        path = path / part
        try:
            st = path.lstat()
        except OSError as exc:
            raise SourceFingerprintError("source_changed", "source path disappeared") from exc
        if _linked(st):
            raise SourceFingerprintError("unsafe_path", "source path became a link")
    if not stat.S_ISREG(st.st_mode) or not _same(st, expected):
        raise SourceFingerprintError("source_changed", "source changed during capture")
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise SourceFingerprintError("unsafe_path", "source escaped worktree") from exc
    return path


def _hash_file(root, relative, expected, deadline, target=None, *, durable=True):
    """Hash one scanned file; with ``target`` also copy the very bytes hashed.

    ``durable`` syncs the copy (a published Bundle). A run's disposable root is removed
    after the run, so it skips the fsync.
    """
    source = _safe_file(root, relative, expected)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise SourceFingerprintError("source_changed", "source could not be opened safely") from exc
    digest = hashlib.sha256()
    count = 0
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not _same(opened, expected):
            raise SourceFingerprintError("source_changed", "source changed before read")
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            with (target.open("xb") if target is not None else open(os.devnull, "wb")) as out:
                while True:
                    _check_time(deadline)
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > MAX_FILE_BYTES:
                        raise SourceFingerprintError("file_limit", "source file grew beyond limit")
                    digest.update(chunk)
                    if target is not None:
                        out.write(chunk)
                if target is not None and durable:
                    out.flush()
                    os.fsync(out.fileno())
        if not _same(os.fstat(fd), expected) or not _same(source.lstat(), expected):
            raise SourceFingerprintError("source_changed", "source changed while reading")
    finally:
        os.close(fd)
    item = {"path": relative, "size": count, "sha256": digest.hexdigest()}
    # The mode was checked unchanged around the read (``_same``), so it is the read's.
    if _exec_bits(expected):
        item["exec"] = format(_exec_bits(expected), "03o")
    return item


def _fingerprint(entries, directories=()):
    """Paths, sizes and byte hashes; a file's execute bits only when it has any, so a
    tree with no executable file keeps the value it always had."""
    h = hashlib.sha256()
    for name, _st in directories:
        h.update(b"D\\0")
        h.update(name.encode("utf-8"))
        h.update(b"\\n")
    for item in entries:
        h.update(item["path"].encode("utf-8"))
        h.update(b"\0")
        h.update(str(item["size"]).encode("ascii"))
        h.update(b"\0")
        h.update(item["sha256"].encode("ascii"))
        if item.get("exec"):
            h.update(b"\0x")
            h.update(item["exec"].encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def inspect_source(root: Path, deadline):
    started = time.monotonic()
    before = _identity(root, deadline)
    scan_started = time.monotonic()
    files, dirs = _scan(root, deadline)
    scan_duration_ms = int((time.monotonic() - scan_started) * 1000)
    hash_started = time.monotonic()
    entries = [_hash_file(root, name, st, deadline) for name, st in files]
    fingerprint_duration_ms = int((time.monotonic() - hash_started) * 1000)
    if _identity(root, deadline) != before:
        raise SourceFingerprintError("source_changed", "worktree revision changed during capture")
    after_files, after_dirs = _scan(root, deadline)
    if ([(x[0], _exec_bits(x[1])) for x in files] != [(x[0], _exec_bits(x[1])) for x in after_files]
            or [x[0] for x in dirs] != [x[0] for x in after_dirs]):
        # A chmod after a file was hashed changes neither its bytes nor its mtime.
        raise SourceFingerprintError("source_changed", "source tree changed during capture")
    return {"source_revision": before[0], "source_dirty": before[1],
            "content_fingerprint": _fingerprint(entries, dirs), "entries": entries,
            "files": files, "dirs": dirs,
            "scan_duration_ms": scan_duration_ms,
            "fingerprint_duration_ms": fingerprint_duration_ms,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "bytes_hashed": sum(item["size"] for item in entries)}


# ── measurements with a memo (D#1 §3-6) ──────────────────────────────────────

_MEMO: dict[str, tuple[str, dict]] = {}
_MEMO_GUARD = threading.Lock()
_MEMO_LIMIT = 64


def _scan_signature(files, dirs) -> str:
    h = hashlib.sha256()
    for name, st in files:
        # chmod leaves size and mtime alone, so the execute bits are signed too.
        h.update(f"F\0{name}\0{st.st_size}\0{st.st_mtime_ns}\0{_exec_bits(st):o}\n".encode("utf-8"))
    for name, _st in dirs:
        h.update(f"D\0{name}\n".encode("utf-8"))
    return h.hexdigest()


def _measured(inspected: dict) -> dict:
    return {"exclusion_policy_version": POLICY_VERSION,
            "content_fingerprint": inspected["content_fingerprint"],
            "hashes": {entry["path"]: entry["sha256"] for entry in inspected["entries"]},
            "source_revision": inspected["source_revision"],
            "source_dirty": inspected["source_dirty"]}


def _remember(root: Path, files, dirs, measured: dict) -> None:
    key = str(root)
    with _MEMO_GUARD:
        _MEMO.pop(key, None)
        if len(_MEMO) >= _MEMO_LIMIT:
            _MEMO.pop(next(iter(_MEMO)))
        _MEMO[key] = (_scan_signature(files, dirs), measured)


def forget(root: Path | None = None) -> None:
    with _MEMO_GUARD:
        if root is None:
            _MEMO.clear()
        else:
            _MEMO.pop(str(root), None)


def measure(root: Path) -> dict:
    """Live Probe of ``root`` (decision paths). Remembers the result for display paths."""
    inspected = inspect_source(root, time.monotonic() + MEASURE_SECONDS)
    measured = _measured(inspected)
    _remember(root, inspected["files"], inspected["dirs"], measured)
    return measured


def memo_lookup(root: Path) -> dict | None:
    """The last measurement of ``root`` if nothing scanned changed since; never hashes.

    A missing memo, a changed path list, size, mtime or execute bit, or a scan failure is ``None``:
    the caller shows "not measured" instead of measuring on a display request.
    """
    with _MEMO_GUARD:
        hit = _MEMO.get(str(root))
    if hit is None:
        return None
    try:
        files, dirs = _scan(root, time.monotonic() + MEASURE_SECONDS)
    except SourceFingerprintError:
        return None
    return hit[1] if hit[0] == _scan_signature(files, dirs) else None


def _keep_mode(copied: Path, st) -> None:
    """Give a run copy the source file's permission bits, so an executable helper the
    Case invokes still runs from the copy. Windows has no execute bit and a copied
    read-only flag would only stop the disposable root from being removed, so it is left."""
    if os.name != "nt":
        os.chmod(copied, stat.S_IMODE(st.st_mode) & 0o777)


def copy_measured(root: Path, target: Path) -> dict:
    """Copy ``root`` into ``target`` and fingerprint exactly the bytes copied (one read).

    The scan, exclusion, link/special refusal and limits are the Live Probe's, so dirty
    and untracked work is in the copy and in its fingerprint. Each file is hashed as it is
    copied and checked unchanged (size, mtime, mode -- on POSIX with the execute bits)
    around the read; a file changed or chmod-ed mid-copy is ``source_changed``. The copy
    keeps the scanned mode, whose execute bits are in the fingerprint. A file created after
    the scan is simply not in the copy -- the fingerprint describes what the run executes,
    not the live tree after it.
    The git revision and dirty flag are display facts read once before the copy.
    """
    deadline = time.monotonic() + MEASURE_SECONDS
    started = time.monotonic()
    revision, dirty = _identity(root, deadline)
    files, dirs = _scan(root, deadline)
    target.mkdir(parents=True, exist_ok=False)
    for name, _st in dirs:
        (target / PurePosixPath(name)).mkdir(parents=True, exist_ok=True)
    entries = []
    for name, st in files:
        copied = target / PurePosixPath(name)
        entries.append(_hash_file(root, name, st, deadline, copied, durable=False))
        _keep_mode(copied, st)
    inspected = {"source_revision": revision, "source_dirty": dirty,
                 "content_fingerprint": _fingerprint(entries, dirs), "entries": entries}
    measured = _measured(inspected)
    _remember(root, files, dirs, measured)
    return {**measured, "metrics": {"file_count": len(entries),
                                    "byte_size": sum(item["size"] for item in entries),
                                    "duration_ms": int((time.monotonic() - started) * 1000)}}
