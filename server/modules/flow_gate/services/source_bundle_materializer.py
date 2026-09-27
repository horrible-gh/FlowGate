"""Capture a whole source worktree into a durable immutable Source Bundle."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.services import git_service
from modules.flow_gate.storage import paths as storage_paths
from modules.flow_gate.services.snapshot_materialization_service import (
    EXCLUDED_DIR_NAMES, EXCLUDED_DIR_PREFIXES, EXCLUDED_FILE_NAMES,
)

POLICY_VERSION = "source-bundle-v1"
MAX_FILES = max(1, int(os.getenv("FLOWGATE_BUNDLE_MAX_FILES", "20000")))
MAX_FILE_BYTES = max(1, int(os.getenv("FLOWGATE_BUNDLE_MAX_FILE_BYTES", str(100 * 1024 * 1024))))
MAX_TOTAL_BYTES = max(1, int(os.getenv("FLOWGATE_BUNDLE_MAX_TOTAL_BYTES", str(500 * 1024 * 1024))))
BUILD_SECONDS = max(1, int(os.getenv("FLOWGATE_BUNDLE_BUILD_SECONDS", "120")))
TTL_HOURS = max(1, int(os.getenv("FLOWGATE_BUNDLE_TTL_HOURS", "24")))
_SECRET = re.compile(
    r"^(?:\.env(?:\..*)?|\.netrc|\.npmrc|\.pypirc|credentials|credentials\.json|"
    r"service-account\.json|service_account\.json|secrets|id_(?:rsa|dsa|ecdsa|ed25519))$"
    r"|(?:^|[._-])(?:credentials?|secrets?|private[-_]key|certificates?)(?:[._-]|$)"
    r"|\.(?:pem|key|p12|pfx|p8|crt|cer|jks|keystore)$", re.IGNORECASE,
)


class SourceBundleError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _safe_name(value: str) -> str:
    value = str(value)
    if "\\" in value:
        raise SourceBundleError("unsafe_path", "unsafe source path")
    if not value or value.startswith("/") or PureWindowsPath(value).drive or ":" in value:
        raise SourceBundleError("unsafe_path", "unsafe source path")
    if any(part in ("", ".", "..") or any(ord(c) < 32 for c in part) for part in value.split("/")):
        raise SourceBundleError("unsafe_path", "unsafe source path")
    return value


def _linked(st) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _same(a, b) -> bool:
    if (a.st_mode, a.st_size, a.st_mtime_ns) != (b.st_mode, b.st_size, b.st_mtime_ns):
        return False
    a_identity = (int(getattr(a, "st_dev", 0)), int(getattr(a, "st_ino", 0)))
    b_identity = (int(getattr(b, "st_dev", 0)), int(getattr(b, "st_ino", 0)))
    # Some Windows filesystems report zero inode/device through DirEntry.stat().
    return not all(a_identity + b_identity) or a_identity == b_identity


def _check_time(deadline):
    if time.monotonic() > deadline:
        raise SourceBundleError("build_timeout", "Source Bundle build exceeded its time ceiling")


def resolve_worktree(project_id: str, group_id: str) -> Path:
    group = db_groups.get_by_id(group_id)
    if group is None or group.get("project_id") != project_id:
        raise SourceBundleError("group_worktree_unavailable", "group is not in the requested project")
    root, reason = git_service.effective_src_root_ex(project_id, group_id)
    if root is None:
        raise SourceBundleError("group_worktree_unavailable", f"exact group worktree unavailable ({reason})")
    root = Path(root)
    try:
        st = root.lstat()
        if _linked(st) or not stat.S_ISDIR(st.st_mode) or not git_service._worktree_link_ok(root):
            raise SourceBundleError("group_worktree_unavailable", "exact group worktree is unsafe")
        return root.resolve(strict=True)
    except OSError as exc:
        raise SourceBundleError("group_worktree_unavailable", "exact group worktree unavailable") from exc


def _identity(root: Path, deadline):
    _check_time(deadline)
    timeout = max(1, min(30, int(deadline - time.monotonic())))
    head = git_service._run_git(["rev-parse", "HEAD"], cwd=root, timeout=timeout)
    _check_time(deadline)
    timeout = max(1, min(30, int(deadline - time.monotonic())))
    status = git_service._run_git(["status", "--porcelain", "-z", "--untracked-files=all"], cwd=root, timeout=timeout)
    _check_time(deadline)
    if head.returncode or status.returncode or not head.stdout.strip():
        raise SourceBundleError("revision_unavailable", "worktree revision unavailable")
    return head.stdout.strip(), bool(status.stdout)


def _excluded(relative: str, is_dir: bool) -> bool:
    name = PurePosixPath(relative).name
    if name in EXCLUDED_DIR_NAMES or (is_dir and name in {"source-bundles", "source-bundle-scratch"}) or name.startswith(EXCLUDED_DIR_PREFIXES):
        return True
    if is_dir:
        return bool(_SECRET.search(name))
    return name in EXCLUDED_FILE_NAMES or any(bool(_SECRET.search(part)) for part in PurePosixPath(relative).parts)


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
            raise SourceBundleError("source_changed", "source directory changed during scan") from exc
        for child in children:
            relative = _safe_name(f"{prefix}/{child.name}" if prefix else child.name)
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise SourceBundleError("source_changed", "source entry changed during scan") from exc
            is_dir = stat.S_ISDIR(st.st_mode)
            if _excluded(relative, is_dir):
                continue
            if _linked(st):
                raise SourceBundleError("unsafe_path", "source contains a symlink or reparse point")
            if is_dir:
                dirs.append((relative, st))
                pending.append((relative, Path(child.path)))
            elif stat.S_ISREG(st.st_mode):
                if st.st_size > MAX_FILE_BYTES:
                    raise SourceBundleError("file_limit", "Source Bundle single file limit exceeded")
                files.append((relative, st))
                total += st.st_size
                if len(files) > MAX_FILES or total > MAX_TOTAL_BYTES:
                    raise SourceBundleError("resource_limit", "Source Bundle resource limit exceeded")
            else:
                raise SourceBundleError("unsafe_path", "source contains a special file")
    return sorted(files), sorted(dirs)


def _safe_file(root: Path, relative: str, expected):
    path = root
    for part in PurePosixPath(_safe_name(relative)).parts:
        path = path / part
        try:
            st = path.lstat()
        except OSError as exc:
            raise SourceBundleError("source_changed", "source path disappeared") from exc
        if _linked(st):
            raise SourceBundleError("unsafe_path", "source path became a link")
    if not stat.S_ISREG(st.st_mode) or not _same(st, expected):
        raise SourceBundleError("source_changed", "source changed during capture")
    try:
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise SourceBundleError("unsafe_path", "source escaped worktree") from exc
    return path


def _hash_file(root, relative, expected, deadline, target=None):
    source = _safe_file(root, relative, expected)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(source, flags)
    except OSError as exc:
        raise SourceBundleError("source_changed", "source could not be opened safely") from exc
    digest = hashlib.sha256()
    count = 0
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not _same(opened, expected):
            raise SourceBundleError("source_changed", "source changed before read")
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
                        raise SourceBundleError("file_limit", "source file grew beyond limit")
                    digest.update(chunk)
                    if target is not None:
                        out.write(chunk)
                if target is not None:
                    out.flush()
                    os.fsync(out.fileno())
        if not _same(os.fstat(fd), expected) or not _same(source.lstat(), expected):
            raise SourceBundleError("source_changed", "source changed while reading")
    finally:
        os.close(fd)
    return {"path": relative, "size": count, "sha256": digest.hexdigest()}


def _fingerprint(entries, directories=()):
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
        raise SourceBundleError("source_changed", "worktree revision changed during capture")
    after_files, after_dirs = _scan(root, deadline)
    if [x[0] for x in files] != [x[0] for x in after_files] or [x[0] for x in dirs] != [x[0] for x in after_dirs]:
        raise SourceBundleError("source_changed", "source tree changed during capture")
    return {"source_revision": before[0], "source_dirty": before[1],
            "content_fingerprint": _fingerprint(entries, dirs), "entries": entries,
            "files": files, "dirs": dirs,
            "scan_duration_ms": scan_duration_ms,
            "fingerprint_duration_ms": fingerprint_duration_ms,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "bytes_hashed": sum(item["size"] for item in entries)}


def bundle_path(project_id: str, bundle_id: str) -> Path:
    if not re.fullmatch(r"sb_[0-9a-f]{32}", bundle_id):
        raise SourceBundleError("invalid_bundle", "invalid Bundle id")
    project_key = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:24]
    return storage_paths.get_storage_root(project_id) / "source-bundles" / project_key / bundle_id


def materialize(root: Path, project_id: str, bundle_id: str, baseline: dict, deadline):
    started = time.monotonic()
    final = bundle_path(project_id, bundle_id)
    parent = final.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = parent / ("." + bundle_id + ".building")
    if stage.exists() or final.exists():
        raise SourceBundleError("namespace_conflict", "Bundle namespace already exists")
    stage.mkdir()
    published = False
    try:
        source_dir = stage / "source"
        source_dir.mkdir()
        copied = []
        for name, st in baseline["dirs"]:
            (source_dir / PurePosixPath(name)).mkdir(parents=True, exist_ok=True)
        copied_bytes = 0
        copy_started = time.monotonic()
        for name, st in baseline["files"]:
            item = _hash_file(root, name, st, deadline, source_dir / PurePosixPath(name))
            copied_bytes += item["size"]
            if copied_bytes > MAX_TOTAL_BYTES:
                raise SourceBundleError("resource_limit", "Source Bundle total byte limit exceeded")
            copied.append(item)
        copy_duration_ms = int((time.monotonic() - copy_started) * 1000)
        if _fingerprint(copied, baseline["dirs"]) != baseline["content_fingerprint"]:
            raise SourceBundleError("source_changed", "source changed between scan and copy")
        verify_started = time.monotonic()
        verified = inspect_source(root, deadline)
        if verified["content_fingerprint"] != baseline["content_fingerprint"] or (
            verified["source_revision"], verified["source_dirty"]
        ) != (baseline["source_revision"], baseline["source_dirty"]):
            raise SourceBundleError("source_changed", "source changed after copy")
        manifest = {"schema": 1, "policy": POLICY_VERSION,
                    "revision": baseline["source_revision"], "dirty": baseline["source_dirty"],
                    "content_fingerprint": baseline["content_fingerprint"], "files": copied,
                    "dirs": [name for name, _st in baseline["dirs"]]}
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        manifest_path = stage / "manifest.json"
        with manifest_path.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        manifest_path.chmod(stat.S_IREAD)
        for path in source_dir.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IREAD)
        source_dir.chmod(stat.S_IREAD | stat.S_IEXEC)
        _check_time(deadline)
        stage.rename(final)
        published = True
        verify_duration_ms = int((time.monotonic() - verify_started) * 1000)
        now = datetime.now(timezone.utc)
        return {"source_revision": baseline["source_revision"], "source_dirty": baseline["source_dirty"],
                "content_fingerprint": baseline["content_fingerprint"],
                "bundle_sha256": hashlib.sha256(encoded).hexdigest(),
                "file_count": len(copied), "byte_size": copied_bytes,
                "created_at": now.isoformat(), "expires_at": (now + timedelta(hours=TTL_HOURS)).isoformat(),
                "metrics": {"bundle_scan_duration_ms": baseline.get("scan_duration_ms", 0),
                            "bundle_copy_duration_ms": copy_duration_ms,
                            "bundle_verify_duration_ms": verify_duration_ms,
                            "bundle_fingerprint_duration_ms": baseline.get("fingerprint_duration_ms", 0),
                            "bundle_file_count": len(copied), "bundle_byte_size": copied_bytes,
                            "bundle_reused": False,
                            "bundle_build_duration_ms": int((time.monotonic() - started) * 1000)}}
    finally:
        if not published and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
