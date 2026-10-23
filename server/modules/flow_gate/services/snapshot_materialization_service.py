"""Approved current-worktree snapshot materialization, freshness, and cleanup."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
import threading
import uuid
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import snapshot_requests as db
from modules.flow_gate.db import workflow_events
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.services import git_service, token_service
from modules.flow_gate.services.snapshot_request_service import SnapshotRequestError
from modules.flow_gate.storage import filesystem as storage_fs
from modules.flow_gate.storage import paths as storage_paths
# 0672 T0004 stage 3: the capture exclusion policy and worker-facing locator redaction moved to
# source_common (shared with the source fingerprint); re-exported here for the legacy Snapshot code and tests.
from modules.flow_gate.services.source_common import (  # noqa: F401
    EXCLUDED_DIR_NAMES, EXCLUDED_DIR_PREFIXES, EXCLUDED_FILE_NAMES,
    PATH_REDACTION, SCRATCH_REDACTION, SERVER_REDACTION, STORAGE_REDACTION, WORKTREE_REDACTION,
    _ABSOLUTE_PATH_RE, _SERVER_ROOT, locator_roots, redact_error_text, redact_locators,
)

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA = 1
SNAPSHOT_OWNER = "flowgate.source-snapshot"
SNAPSHOT_NAMESPACE = "source-snapshots"
JOURNAL_OWNER = "snapshot"  # delete journal owner (storage_fs.JOURNAL_OWNERS)
# Reserved system user seeded by migration 038 (see services/q_service.py AI_SYSTEM_USER);
# every users(user_id) FK in this environment is guaranteed to resolve it. Snapshot audit
# events with no real human/AI-run actor record this instead of a free-text label, so
# workflow_events.actor_user_id (NOT NULL REFERENCES users(user_id)) never rejects the insert.
SYSTEM_ACTOR_USER_ID = "u-system"
SNAPSHOT_TTL_HOURS = max(1, int(os.getenv("FLOWGATE_SNAPSHOT_TTL_HOURS", "24")))
SNAPSHOT_SWEEP_INTERVAL_SECONDS = max(
    60, int(os.getenv("FLOWGATE_SNAPSHOT_SWEEP_INTERVAL_SECONDS", "900"))
)
SNAPSHOT_MAX_FILES = max(1, int(os.getenv("FLOWGATE_SNAPSHOT_MAX_FILES", "20000")))
SNAPSHOT_MAX_FILE_BYTES = max(
    1, int(os.getenv("FLOWGATE_SNAPSHOT_MAX_FILE_BYTES", str(100 * 1024 * 1024)))
)
SNAPSHOT_MAX_TOTAL_BYTES = max(
    1, int(os.getenv("FLOWGATE_SNAPSHOT_MAX_TOTAL_BYTES", str(500 * 1024 * 1024)))
)
SNAPSHOT_CLEANUP_MAX_ATTEMPTS = max(
    1, int(os.getenv("FLOWGATE_SNAPSHOT_CLEANUP_MAX_ATTEMPTS", "5"))
)
ORPHAN_GRACE_SECONDS = max(
    60, int(os.getenv("FLOWGATE_SNAPSHOT_ORPHAN_GRACE_SECONDS", "3600"))
)

INTERNAL_GARBAGE_NAMES = frozenset({
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".cache", ".parcel-cache", ".turbo",
    "build", "dist", "htmlcov", "coverage", "coverage_html_report", "tmp", "temp",
})

_ID_RE = re.compile(r"\Asnap_[A-Za-z0-9_-]+\Z")
_operations_guard = threading.Lock()
_operations: dict[str, threading.RLock] = {}
_sweep_guard = threading.Lock()
_sweep_stop = threading.Event()
_sweep_started = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def public_failure_reason(exc: BaseException, row: dict | None) -> str:
    """The only form of an exception that may be stored in failure_reason/cleanup_last_error.

    Authored SnapshotRequestError messages keep their text; any other exception (the
    PermissionError/OSError a copy, scan, or rmtree raises embeds the live worktree or
    scratch path in str(exc)) is reduced to its type plus a path-scrubbed message.
    """
    if isinstance(exc, SnapshotRequestError):
        text = str(exc.message)
    else:
        text = f"{type(exc).__name__}: {exc}"
    return redact_error_text(text, locator_roots(row))[:1000]


def _operation_lock(snapshot_id: str) -> threading.RLock:
    with _operations_guard:
        return _operations.setdefault(snapshot_id, threading.RLock())


def _is_reparse_or_symlink(path: Path, st: os.stat_result | None = None) -> bool:
    try:
        if path.is_symlink():
            return True
        current = st or path.lstat()
        flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(getattr(current, "st_file_attributes", 0) & flag)
    except OSError:
        return True


def _safe_relative(raw: object) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if not value or value == "." or "\x00" in value or any(ord(ch) < 32 for ch in value):
        raise SnapshotRequestError(422, "invalid_paths", "snapshot paths must be normal relative paths")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or value.startswith("//")
        or any(part in ("", ".", "..") or ":" in part for part in value.split("/"))
        or ".." in posix.parts
    ):
        raise SnapshotRequestError(422, "invalid_paths", "snapshot paths must stay inside the worktree")
    return posix.as_posix().rstrip("/")


def _excluded_reason(relative: str) -> str | None:
    parts = PurePosixPath(relative).parts
    for part in parts:
        if part in EXCLUDED_DIR_NAMES or part.startswith(EXCLUDED_DIR_PREFIXES):
            return "excluded_directory"
    if parts and parts[-1] in EXCLUDED_FILE_NAMES:
        return "excluded_file"
    return None


def _resolve_source_root(row: dict) -> Path:
    group = db_groups.get_by_id(row["group_id"])
    if group is None or group.get("project_id") != row["project_id"]:
        raise SnapshotRequestError(409, "group_worktree_unavailable", "group does not belong to the requested project")
    root, reason = git_service.effective_src_root_ex(row["project_id"], row["group_id"])
    if root is None:
        raise SnapshotRequestError(
            409,
            "group_worktree_unavailable",
            f"exact group worktree is unavailable ({reason}); base checkout was not used",
        )
    try:
        resolved = Path(root).resolve(strict=True)
    except OSError as exc:
        raise SnapshotRequestError(409, "group_worktree_unavailable", "exact group worktree is unavailable") from exc
    if not resolved.is_dir() or not git_service._worktree_link_ok(resolved):
        raise SnapshotRequestError(409, "group_worktree_unavailable", "exact group worktree is not live")
    return resolved


def _snapshot_namespace(row: dict, *, create: bool) -> Path:
    if not _ID_RE.fullmatch(str(row.get("snapshot_id") or "")):
        raise SnapshotRequestError(409, "snapshot_identity_invalid", "snapshot id is not filesystem-safe")
    token_root = Path(token_service.scratch_dir_path(row["project_id"], row["token_id"]))
    try:
        token_resolved = token_root.resolve(strict=True)
    except OSError as exc:
        raise SnapshotRequestError(409, "snapshot_scratch_unavailable", "token scratch directory is unavailable") from exc
    if not token_resolved.is_dir() or _is_reparse_or_symlink(token_root):
        raise SnapshotRequestError(409, "snapshot_scratch_unavailable", "token scratch directory is not a normal directory")
    namespace = token_resolved / SNAPSHOT_NAMESPACE
    if create:
        try:
            namespace.mkdir(exist_ok=True)
        except OSError as exc:
            raise SnapshotRequestError(409, "snapshot_scratch_unavailable", "snapshot namespace could not be created") from exc
    if namespace.exists():
        if not namespace.is_dir() or _is_reparse_or_symlink(namespace):
            raise SnapshotRequestError(409, "snapshot_scratch_unavailable", "snapshot namespace is unsafe")
        if namespace.resolve(strict=True).parent != token_resolved:
            raise SnapshotRequestError(409, "snapshot_scratch_unavailable", "snapshot namespace escaped token scratch")
    return namespace


def snapshot_path(row: dict) -> Path:
    return _snapshot_namespace(row, create=False) / row["snapshot_id"]


def _ensure_inside(root: Path, candidate: Path) -> Path:
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SnapshotRequestError(422, "snapshot_path_escape", "resolved path leaves the group worktree") from exc
    return resolved


def _same_file_state(left: os.stat_result, right: os.stat_result) -> bool:
    if (
        int(left.st_size), int(left.st_mtime_ns), int(left.st_mode)
    ) != (
        int(right.st_size), int(right.st_mtime_ns), int(right.st_mode)
    ):
        return False
    left_identity = (int(getattr(left, "st_dev", 0)), int(getattr(left, "st_ino", 0)))
    right_identity = (int(getattr(right, "st_dev", 0)), int(getattr(right, "st_ino", 0)))
    # Some Windows filesystems report zero inode/device through DirEntry.stat().
    return not all(left_identity + right_identity) or left_identity == right_identity


def _assert_safe_chain(root: Path, relative: str) -> Path:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            st = current.lstat()
        except OSError as exc:
            raise SnapshotRequestError(422, "snapshot_path_missing", f"requested path does not exist: {relative}") from exc
        if _is_reparse_or_symlink(current, st):
            raise SnapshotRequestError(422, "snapshot_link_blocked", f"symlink/junction/reparse path is not allowed: {relative}")
    return _ensure_inside(root, current)


def _collect_tree(
    root: Path, start: Path, directories: list[str], files: list[tuple[str, os.stat_result]],
    excluded: list[dict],
) -> None:
    try:
        entries = sorted(os.scandir(start), key=lambda item: item.name.casefold())
    except OSError as exc:
        raise SnapshotRequestError(422, "snapshot_scan_failed", f"cannot scan requested directory: {start.name}") from exc
    for entry in entries:
        path = Path(entry.path)
        try:
            relative = path.relative_to(root).as_posix()
            st = entry.stat(follow_symlinks=False)
        except (OSError, ValueError) as exc:
            raise SnapshotRequestError(422, "snapshot_scan_failed", "source changed while snapshot paths were scanned") from exc
        if _is_reparse_or_symlink(path, st):
            excluded.append({"path": relative, "reason": "symlink_or_reparse"})
            continue
        reason = _excluded_reason(relative)
        if stat.S_ISDIR(st.st_mode):
            if reason:
                excluded.append({"path": relative, "reason": reason})
                continue
            _ensure_inside(root, path)
            directories.append(relative)
            _collect_tree(root, path, directories, files, excluded)
        elif stat.S_ISREG(st.st_mode):
            if reason:
                excluded.append({"path": relative, "reason": reason})
                continue
            _ensure_inside(root, path)
            files.append((relative, st))
        else:
            raise SnapshotRequestError(422, "snapshot_special_file", f"special filesystem entry is not allowed: {relative}")


def _collect_scope(row: dict, root: Path) -> tuple[list[str], list[tuple[str, os.stat_result]], list[dict]]:
    directories: list[str] = []
    files: list[tuple[str, os.stat_result]] = []
    excluded: list[dict] = []
    scope = row["scope"]
    raw_paths = row.get("requested_paths") or []
    paths = [_safe_relative(raw) for raw in raw_paths]
    if scope == "whole_source":
        _collect_tree(root, root, directories, files, excluded)
    elif scope == "directory":
        relative = paths[0]
        if _excluded_reason(relative):
            raise SnapshotRequestError(422, "snapshot_path_excluded", f"requested directory is excluded: {relative}")
        target = _assert_safe_chain(root, relative)
        if not target.is_dir():
            raise SnapshotRequestError(422, "snapshot_scope_mismatch", "directory scope requires a directory")
        directories.append(relative)
        _collect_tree(root, target, directories, files, excluded)
    else:
        for relative in paths:
            if _excluded_reason(relative):
                raise SnapshotRequestError(422, "snapshot_path_excluded", f"requested file is excluded: {relative}")
            target = _assert_safe_chain(root, relative)
            try:
                st = target.lstat()
            except OSError as exc:
                raise SnapshotRequestError(422, "snapshot_path_missing", f"requested path does not exist: {relative}") from exc
            if _is_reparse_or_symlink(target, st):
                raise SnapshotRequestError(422, "snapshot_link_blocked", f"linked files are not allowed: {relative}")
            if not stat.S_ISREG(st.st_mode):
                raise SnapshotRequestError(422, "snapshot_scope_mismatch", f"{scope} requires regular files")
            files.append((relative, st))
    files.sort(key=lambda item: item[0])
    directories[:] = sorted(set(directories))
    excluded.sort(key=lambda item: (item["path"], item["reason"]))
    if scope == "single_file" and len(files) != 1:
        raise SnapshotRequestError(422, "snapshot_scope_mismatch", "single_file requires exactly one regular file")
    if scope == "selected_files" and len(files) != len(paths):
        raise SnapshotRequestError(422, "snapshot_scope_mismatch", "every selected path must be a regular file")
    return directories, files, excluded


def _check_limits(files: list[tuple[str, os.stat_result]]) -> None:
    if len(files) > SNAPSHOT_MAX_FILES:
        raise SnapshotRequestError(413, "snapshot_file_limit", f"snapshot exceeds {SNAPSHOT_MAX_FILES} files")
    total = 0
    for relative, st in files:
        size = int(st.st_size)
        if size > SNAPSHOT_MAX_FILE_BYTES:
            raise SnapshotRequestError(413, "snapshot_file_size_limit", f"file exceeds snapshot byte limit: {relative}")
        total += size
        if total > SNAPSHOT_MAX_TOTAL_BYTES:
            raise SnapshotRequestError(413, "snapshot_total_size_limit", "snapshot exceeds total byte limit")


def _read_hash(path: Path, expected: os.stat_result) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    digest = hashlib.sha256()
    count = 0
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_state(opened, expected):
            raise SnapshotRequestError(409, "snapshot_source_changed", "source changed while snapshot was being built")
        with os.fdopen(fd, "rb", closefd=False) as source:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > SNAPSHOT_MAX_FILE_BYTES:
                    raise SnapshotRequestError(413, "snapshot_file_size_limit", f"file grew beyond snapshot byte limit: {path.name}")
                digest.update(chunk)
    finally:
        os.close(fd)
    try:
        after = path.lstat()
    except OSError as exc:
        raise SnapshotRequestError(409, "snapshot_source_changed", "source disappeared while snapshot was being built") from exc
    if _is_reparse_or_symlink(path, after) or not _same_file_state(after, expected):
        raise SnapshotRequestError(409, "snapshot_source_changed", "source changed while snapshot was being built")
    return digest.hexdigest(), count


def _copy_and_hash(source: Path, target: Path, expected: os.stat_result) -> tuple[str, int]:
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(source, flags)
    digest = hashlib.sha256()
    count = 0
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or not _same_file_state(opened, expected):
            raise SnapshotRequestError(409, "snapshot_source_changed", "source changed while snapshot was being copied")
        with os.fdopen(fd, "rb", closefd=False) as src, target.open("xb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > SNAPSHOT_MAX_FILE_BYTES:
                    raise SnapshotRequestError(413, "snapshot_file_size_limit", f"file grew beyond snapshot byte limit: {source.name}")
                dst.write(chunk)
                digest.update(chunk)
            dst.flush()
    finally:
        os.close(fd)
    try:
        after = source.lstat()
    except OSError as exc:
        raise SnapshotRequestError(409, "snapshot_source_changed", "source disappeared while snapshot was being copied") from exc
    if _is_reparse_or_symlink(source, after) or not _same_file_state(after, expected):
        raise SnapshotRequestError(409, "snapshot_source_changed", "source changed while snapshot was being copied")
    try:
        os.chmod(target, stat.S_IMODE(expected.st_mode))
    except OSError:
        pass
    return digest.hexdigest(), count


def _fingerprint(entries: Iterable[dict]) -> str:
    digest = hashlib.sha256()
    for item in sorted(entries, key=lambda value: value["path"]):
        digest.update(item["path"].encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["size"]).encode("ascii"))
        digest.update(b"\0")
        digest.update(item["sha256"].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _current_fingerprint(root: Path, row: dict) -> tuple[str, list[dict]]:
    """Content fingerprint of the requested scope in the live worktree.

    Every file is re-read and re-hashed on every call. Stat metadata (size, mtime) is not
    evidence of unchanged content: a same-length edit followed by an mtime restore keeps
    both identical, so reusing a baseline hash on a stat match would miss a real change
    and report a stale snapshot as active.
    """
    _, files, _ = _collect_scope(row, root)
    _check_limits(files)
    entries: list[dict] = []
    total = 0
    for relative, st in files:
        sha, size = _read_hash(root / PurePosixPath(relative), st)
        total += size
        if total > SNAPSHOT_MAX_TOTAL_BYTES:
            raise SnapshotRequestError(413, "snapshot_total_size_limit", "snapshot exceeds total byte limit")
        entries.append({"path": relative, "size": size, "mtime_ns": int(st.st_mtime_ns), "sha256": sha})
    return _fingerprint(entries), entries


def _git_identity(root: Path, row: dict) -> tuple[str, bool]:
    head = git_service._run_git(["rev-parse", "HEAD"], cwd=root)
    if head.returncode != 0 or not head.stdout.strip():
        raise SnapshotRequestError(409, "snapshot_revision_unavailable", "worktree HEAD could not be resolved")
    args = ["status", "--porcelain", "-z", "--untracked-files=all"]
    if row["scope"] != "whole_source":
        args += ["--", *(row.get("requested_paths") or [])]
    status_result = git_service._run_git(args, cwd=root)
    if status_result.returncode != 0:
        raise SnapshotRequestError(409, "snapshot_revision_unavailable", "worktree status could not be resolved")
    return head.stdout.strip(), bool(status_result.stdout)


def _excluded_summary(excluded: list[dict]) -> dict:
    return {
        "policy": {
            "directory_names": sorted(EXCLUDED_DIR_NAMES),
            "directory_prefixes": list(EXCLUDED_DIR_PREFIXES),
            "file_names": sorted(EXCLUDED_FILE_NAMES),
            "links": "all symlink/junction/reparse entries",
        },
        "count": len(excluded),
        "paths": excluded[:200],
        "paths_truncated": len(excluded) > 200,
    }


def _readme(row: dict, revision: str, fingerprint: str, created_at: str, excluded: list[dict]) -> str:
    requested = row.get("requested_paths") or []
    paths = "\n".join(f"  - {path}" for path in requested) or "  - (whole source)"
    return f"""# FlowGate Scratch Snapshot

This directory is a disposable snapshot.

- This is NOT the source of truth.
- The actual source of truth is the FlowGate current worktree.
- Changes made inside this directory are NOT persistent.
- Never copy modified scratch files back to the worktree/main.
- All persistent source/document changes MUST use FlowGate CRUD/tools
  (write_source_file, patch_source_file, or remove_source_file).
- Test/build results executed here only validate this snapshot.
- If the FlowGate worktree changes after this snapshot was created, this snapshot may be stale.
- This directory may be automatically deleted.
- Do not treat files here as final deliverables unless explicitly requested.
- Snapshot-to-worktree/main promotion, upload, commit, merge, and sync are prohibited.

## Provenance

- snapshot_id: {row["snapshot_id"]}
- created_at: {created_at}
- source_kind: current_worktree
- scope: {row["scope"]}
- source_revision: {revision}
- worktree_fingerprint: {fingerprint}
- excluded_entries: {len(excluded)}
- requested_paths:
{paths}
"""


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
    temporary.replace(path)


def _manifest(row: dict, revision: str, dirty: bool, fingerprint: str, entries: list[dict],
              copied_bytes: int, excluded: list[dict], created_at: str, expires_at: str) -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "owner": SNAPSHOT_OWNER,
        "snapshot_id": row["snapshot_id"],
        "project_id": row["project_id"],
        "group_id": row["group_id"],
        "run_id": row["run_id"],
        "token_id": row["token_id"],
        "provider_id": row["provider_id"],
        "source_kind": "current_worktree",
        "scope": row["scope"],
        "requested_paths": row.get("requested_paths") or [],
        "reason": row["reason"],
        "purpose": row["purpose"],
        "created_at": created_at,
        "expires_at": expires_at,
        "source_revision": revision,
        "source_dirty": dirty,
        "source_fingerprint": fingerprint,
        "worktree_fingerprint": fingerprint,
        "fingerprint": {
            "algorithm": "sha256(path NUL size NUL content-sha256 LF)",
            "entries": entries,
        },
        "copied_file_count": len(entries),
        "copied_byte_size": copied_bytes,
        "excluded": _excluded_summary(excluded),
        "stale": False,
        "stale_detected_at": None,
    }


def _manifest_owned(manifest: object, row: dict) -> bool:
    expected = {
        "schema": SNAPSHOT_SCHEMA,
        "owner": SNAPSHOT_OWNER,
        "snapshot_id": row["snapshot_id"],
        "project_id": row["project_id"],
        "group_id": row["group_id"],
        "run_id": row["run_id"],
        "token_id": row["token_id"],
        "source_kind": "current_worktree",
    }
    return isinstance(manifest, dict) and all(
        manifest.get(key) == value for key, value in expected.items())


def _load_manifest(path: Path, row: dict) -> dict:
    if _is_reparse_or_symlink(path):
        raise SnapshotRequestError(409, "snapshot_integrity_error", "snapshot directory is a link/reparse point")
    try:
        resolved = path.resolve(strict=True)
        if resolved.parent != path.parent.resolve(strict=True) or not resolved.is_dir():
            raise ValueError("not a direct child")
        manifest_path = resolved / "snapshot.json"
        if _is_reparse_or_symlink(manifest_path):
            raise ValueError("linked manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SnapshotRequestError(409, "snapshot_integrity_error", "snapshot manifest is missing or invalid") from exc
    if not _manifest_owned(manifest, row):
        raise SnapshotRequestError(409, "snapshot_integrity_error", "snapshot manifest ownership does not match")
    # The payload roots are checked without following links: a ``source`` swapped for a
    # symlink/junction to another directory would otherwise pass here (is_dir() follows
    # it) and let freshness/status report a tree that access/run reject as
    # ``snapshot_locator_invalid``.
    source = resolved / "source"
    readme = resolved / "README.md"
    if _is_reparse_or_symlink(source) or _is_reparse_or_symlink(readme):
        raise SnapshotRequestError(409, "snapshot_integrity_error", "snapshot payload root is a link/reparse point")
    if not source.is_dir() or not readme.is_file():
        raise SnapshotRequestError(409, "snapshot_integrity_error", "snapshot payload is incomplete")
    return manifest


def _audit_metadata(row: dict, **extra: object) -> str:
    data = {
        "snapshot_id": row.get("snapshot_id"),
        "project_id": row.get("project_id"),
        "group_id": row.get("group_id"),
        "run_id": row.get("run_id"),
        "token_id": row.get("token_id"),
        "provider": row.get("provider_id"),
        "provider_id": row.get("provider_id"),
        "requested_reason": row.get("reason"),
        "purpose": row.get("purpose"),
        "requested_scope": row.get("scope"),
        "requested_paths": row.get("requested_paths") or [],
        "source_kind": row.get("source_kind"),
        "source_revision": row.get("source_revision"),
        "created_at": row.get("created_at"),
        "approved_by": row.get("approved_by"),
    }
    data.update(extra)
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _record_event(event_type: str, row: dict, actor: str, from_state: str | None,
                  to_state: str | None, **extra: object) -> None:
    workflow_events.create({
        "event_type": event_type,
        "project_id": row["project_id"],
        "group_id": row["group_id"],
        "actor_user_id": actor,
        "from_state": from_state,
        "to_state": to_state,
        "metadata": _audit_metadata(row, **extra),
    })


def _record_create_failure(row: dict, actor: str, exc: Exception, *, source: str | None = None) -> dict:
    reason = public_failure_reason(exc, row)
    stage = getattr(exc, "code", type(exc).__name__)
    # Preserve the durable create-failure contract for existing consumers. Only a
    # changed source needs its own terminal code to guide a fresh request.
    code = "snapshot_source_changed" if stage == "snapshot_source_changed" else "snapshot_create_failed"
    with get_store().transaction():
        failed, changed = db.mark_failed(row["snapshot_id"], code, reason)
        if changed:
            _record_event(
                "state_changed", failed, actor, "approved", "failed",
                error_code=code, failure_stage=stage, failure_reason=reason,
                source=source,
            )
    return failed


def _fail_creation(row: dict, actor: str, exc: BaseException, *, source: str | None = None):
    """Persist approved->failed for a materialization that did not publish, then raise.

    Every pre-publish failure (namespace preparation, claim creation, scan, copy, verify)
    goes through here so the request never stays ``approved`` and non-request
    exceptions reach callers as the same scrubbed 409 ``snapshot_create_failed``.
    """
    try:
        _record_create_failure(row, actor, exc, source=source)
    except Exception:
        logger.exception("snapshot %s creation failure could not be persisted", row.get("snapshot_id"))
    if isinstance(exc, SnapshotRequestError):
        raise exc
    # The raw OSError text names the live worktree/scratch path; callers
    # (human approve, CLI materialize) get the same scrubbed reason as the DB.
    raise SnapshotRequestError(
        409, "snapshot_create_failed", public_failure_reason(exc, row),
    ) from exc


def _recover_published(row: dict, final: Path, actor: str) -> dict:
    """Finish the DB half of a tree that was published before its DB update.

    Only a valid published tree is recoverable. An existing ``final`` whose manifest or
    payload fails integrity (missing/corrupt ``snapshot.json``, link/reparse roots,
    ownership mismatch, unusable fields) can never become ``created``: it is an
    unrecoverable creation failure and goes through ``_fail_creation`` (approved->failed
    event + scrubbed error). A failing DB update stays outside so the retry is kept.
    """
    try:
        manifest = _load_manifest(final, row)
        created_at = str(manifest["created_at"])
        expires_at = str(manifest.get("expires_at") or (
            (_parse_time(created_at) or _utcnow()) + timedelta(hours=SNAPSHOT_TTL_HOURS)
        ).isoformat())
        published = (
            str(manifest["source_revision"]), str(manifest["worktree_fingerprint"]),
            int(manifest["copied_file_count"]), int(manifest["copied_byte_size"]),
        )
    except Exception as exc:
        _fail_creation(row, actor, exc, source="snapshot_recovery")
    with get_store().transaction():
        updated, changed = db.mark_created(
            row["snapshot_id"], created_at, expires_at, *published,
        )
        if changed:
            _record_event(
                "snapshot_created", updated, actor, "approved", "created",
                copied_file_count=manifest["copied_file_count"],
                copied_byte_size=manifest["copied_byte_size"],
                excluded=manifest.get("excluded"),
                recovery="published_before_db_update",
                source="snapshot_recovery",
            )
    return updated


def materialize(snapshot_id: str, actor: str) -> dict:
    raise SnapshotRequestError(410, "snapshot_feature_retired", "Legacy Snapshot materialization is retired")


def _mark_stale(row: dict, actor: str, current_revision: str | None, reason: str,
                *, source: str | None = None) -> dict:
    detected_at = now_iso()
    with get_store().transaction():
        updated, changed = db.mark_stale(row["snapshot_id"], detected_at)
        if changed:
            _record_event(
                "snapshot_stale", updated, actor, "created", "created",
                created_source_revision=row.get("source_revision"),
                current_source_revision=current_revision,
                stale_detected_at=detected_at,
                stale_reason=reason,
                source=source,
            )
    return updated


def refresh_stale(snapshot_id: str, actor: str = SYSTEM_ACTOR_USER_ID, *, source: str | None = None) -> dict:
    row = db.get(snapshot_id)
    if row is None:
        raise SnapshotRequestError(404, "not_found", "snapshot request not found")
    result = dict(row)
    if row["status"] != "created":
        result["available"] = False
        return result
    try:
        final = snapshot_path(row)
    except SnapshotRequestError:
        result["available"] = False
        result["integrity_error"] = "snapshot_scratch_unavailable"
        return result
    if not final.exists():
        result["available"] = False
        result["integrity_error"] = "snapshot_missing"
        return result
    try:
        manifest = _load_manifest(final, row)
    except SnapshotRequestError:
        updated = _mark_stale(row, actor, None, "manifest_unreadable", source=source)
        result = dict(updated)
        result["available"] = False
        result["integrity_error"] = "snapshot_integrity_error"
        return result
    result["available"] = True
    result["snapshot_path"] = str(final)
    if bool(row.get("stale")):
        if not manifest.get("stale"):
            try:
                manifest["stale"] = True
                manifest["stale_detected_at"] = row.get("stale_detected_at")
                _write_json(final / "snapshot.json", manifest)
            except Exception:
                logger.warning("snapshot %s stale manifest repair failed", snapshot_id, exc_info=True)
        return result
    try:
        root = _resolve_source_root(row)
        current_revision, _ = _git_identity(root, row)
        fingerprint_data = manifest.get("fingerprint") or {}
        if not isinstance(fingerprint_data.get("entries"), list):
            raise SnapshotRequestError(409, "snapshot_integrity_error", "fingerprint entries are missing")
        current_fingerprint, _ = _current_fingerprint(root, row)
        if current_fingerprint == row.get("source_fingerprint"):
            result["current_source_revision"] = current_revision
            return result
        updated = _mark_stale(row, actor, current_revision, "scope_fingerprint_changed", source=source)
    except Exception:
        logger.warning("snapshot %s freshness check failed closed", snapshot_id, exc_info=True)
        updated = _mark_stale(row, actor, None, "source_unavailable", source=source)
    try:
        manifest["stale"] = True
        manifest["stale_detected_at"] = updated.get("stale_detected_at")
        _write_json(final / "snapshot.json", manifest)
    except Exception:
        logger.warning("snapshot %s stale manifest update failed", snapshot_id, exc_info=True)
    result = dict(updated)
    result["available"] = True
    result["snapshot_path"] = str(final)
    return result


def _garbage_summary(final: Path) -> dict:
    categories: set[str] = set()
    paths = 0
    bytes_found = 0
    try:
        for base, dirs, files in os.walk(final, followlinks=False):
            rel_parts = Path(base).relative_to(final).parts
            categories.update(part for part in rel_parts if part in INTERNAL_GARBAGE_NAMES)
            paths += len(dirs) + len(files)
            for name in files:
                try:
                    bytes_found += (Path(base) / name).lstat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return {"categories": sorted(categories), "path_count": paths, "byte_size": bytes_found}


def _remaining_count(path: Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_symlink() or _is_reparse_or_symlink(path):
        return 1
    count = 1
    try:
        for _, dirs, files in os.walk(path, followlinks=False):
            count += len(dirs) + len(files)
    except OSError:
        pass
    return count


def cleanup(snapshot_id: str, actor: str, *, trigger: str = "explicit",
           source: str | None = None) -> dict:
    lock = _operation_lock(snapshot_id)
    with lock:
        row = db.get(snapshot_id)
        if row is None:
            raise SnapshotRequestError(404, "not_found", "snapshot request not found")
        if row["status"] == "deleted":
            return row
        try:
            recorded = _record_pending(row)
        except Exception as exc:
            _log_delete_failure("record_lookup", snapshot_id, exc)
            if row["status"] != "created":
                return _with_warning(row, exc)
            return _cleanup_failed(row, actor, trigger, exc, source=source)
        if recorded:
            # A started delete continues from its record, never from the payload
            # checks of _load_manifest that a partial delete may already defeat.
            try:
                return _resume_recorded(row, actor, trigger, source=source)
            except Exception as exc:
                _log_resume_stop(snapshot_id, "unintentional", exc)
                if row["status"] != "created":
                    return _with_warning(row, exc)
                return _cleanup_failed(row, actor, trigger, exc, source=source)
        try:
            final = snapshot_path(row)
        except SnapshotRequestError as exc:
            if row["status"] != "created":
                return row
            return _cleanup_failed(row, actor, trigger, exc, source=source)

        if row["status"] != "created":
            if not final.exists() and not final.is_symlink():
                return row
            try:
                manifest = _load_manifest(final, row)
                if manifest:
                    _delete_published(row, final, manifest, "terminal_orphan", trigger)
                    _drop_record(row["project_id"], row["snapshot_id"])
            except Exception as exc:
                _log_delete_failure("terminal_cleanup", snapshot_id, exc)
                return _with_warning(row, exc)
            return row

        garbage = _garbage_summary(final)
        try:
            present = storage_fs.path_present(final)
        except storage_fs.PresenceUnknown as exc:
            # A failed lookup is never "already gone": the row stays created.
            return _cleanup_failed(row, actor, trigger, exc, source=source)
        if not present:
            return _finish_deleted(row, actor, trigger, garbage, recovery="directory_already_absent", source=source)
        try:
            if _is_reparse_or_symlink(final):
                raise SnapshotRequestError(409, "snapshot_cleanup_failed", "snapshot directory became a link/reparse point")
            if final.resolve(strict=True).parent != final.parent.resolve(strict=True):
                raise SnapshotRequestError(409, "snapshot_cleanup_failed", "snapshot directory escaped its namespace")
            # Returns only once the directory is confirmed absent.
            _delete_published(row, final, _recorded_manifest(final, row), "created_cleanup", trigger)
        except Exception as exc:
            return _cleanup_failed(row, actor, trigger, exc, source=source)
        # The record outlives the DB update: a failed update is finished by the resume.
        updated = _finish_deleted(row, actor, trigger, garbage, source=source)
        _drop_record(row["project_id"], row["snapshot_id"])
        return updated


# ── Delete journal (flowgate.v02.0090 T#3, NR0003 §5.2) ─────────────────────
#
# A published snapshot is deleted only after its owner evidence (DB row,
# token namespace, snapshot.json when still readable) is recorded outside
# the token scratch at G/snapshot/<project-key>/<snapshot-id>.json, with the
# token as ``parent`` so the token row purge waits for it.  A delete that
# stops half way is continued from that record by ``_resume_recorded``, whose
# checks do not need snapshot.json, README.md or source to remain.


def _with_warning(row: dict, exc: BaseException) -> dict:
    result = dict(row)
    result["cleanup_warning"] = public_failure_reason(exc, row)
    return result


def _record_pending(row: dict) -> bool:
    """True while any record file — valid or not — exists for this snapshot."""
    try:
        return storage_fs.journal_exists(JOURNAL_OWNER, row["project_id"], row["snapshot_id"])
    except ValueError:
        return False  # an id the journal cannot name never had a record


def _recorded_manifest(final: Path, row: dict) -> dict | None:
    """snapshot.json for the record when it still proves this row's ownership."""
    path = final / "snapshot.json"
    try:
        if _is_reparse_or_symlink(path) or not path.is_file():
            return None
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if _manifest_owned(manifest, row) else None


def _journal_extra_roots() -> list[Path]:
    # The record must stay outside the source tree too (NR0003 §5.2).
    return [storage_paths.get_storage_root() / "src"]


def _delete_published(row: dict, final: Path, manifest: dict | None, basis: str,
                      trigger: str) -> None:
    """Record the verified owner evidence, then delete *final* until it is absent.

    The caller has already checked the DB row and that *final* is a plain direct
    child of the token's snapshot namespace.  A record that cannot be written
    and re-read raises before anything is deleted; a failed delete keeps it.
    """
    snapshot_id, project_id = row["snapshot_id"], row["project_id"]
    with storage_fs.journal_lock(JOURNAL_OWNER, project_id, snapshot_id):
        resolved = final.resolve(strict=True)
        record = storage_fs.new_journal_record(
            JOURNAL_OWNER, "delete", project_id, snapshot_id,
            managed_root=resolved.parent, target=resolved, manifest=manifest,
            reason=f"snapshot_{basis}",
            eligibility={
                "basis": basis, "at": _utcnow().isoformat(), "trigger": trigger,
                "status": row["status"], "expires_at": row.get("expires_at"),
                "group_id": row.get("group_id"), "run_id": row.get("run_id"),
                "checks": ["db_row", "token_namespace", "direct_child", "no_link"]
                          + (["manifest_owner"] if manifest is not None else []),
            },
            parent={"owner": "token", "id": row["token_id"]})
        stored = storage_fs.write_journal(record, extra_roots=_journal_extra_roots())
        _remove_recorded(stored)


def _remove_recorded(record: dict) -> None:
    """Delete the recorded target unless it is already gone (a real ENOENT)."""
    target, managed_root = Path(record["target"]), Path(record["managed_root"])
    if not storage_fs.path_present(target):
        return
    if not (storage_fs.same_identity(managed_root, record["root_identity"])
            and storage_fs.same_identity(target, record["target_identity"])):
        raise storage_fs.DeleteJournalError("identity_mismatch")
    storage_fs.remove_managed_tree(target, managed_root)


def _drop_record(project_id: str, snapshot_id: str) -> None:
    try:
        storage_fs.remove_journal(JOURNAL_OWNER, project_id, snapshot_id)
    except Exception as exc:
        # The target is gone; the record stays pending and the next sweep drops it.
        _log_delete_failure("record_remove", snapshot_id, exc)


def _recorded_target(project_id: str, snapshot_id: str, record: dict) -> Path | None:
    """Resume-only check of the recorded paths; None when the target is already gone.

    Unlike _load_manifest this needs no snapshot.json, README.md or source: the
    record must name a direct child of the snapshot namespace of its parent
    token, and every path from the token's work root down to the target must be
    a plain directory.  Only a real ENOENT on that chain counts as "gone".
    """
    parent = record["parent"] or {}
    target, managed_root = Path(record["target"]), Path(record["managed_root"])
    token_root = Path(token_service.scratch_dir_path(project_id, parent["id"])).resolve(strict=False)
    namespace = token_root / SNAPSHOT_NAMESPACE
    if (not _ID_RE.fullmatch(snapshot_id) or target.name != snapshot_id
            or not _same_path(target.parent, managed_root)
            or not _same_path(managed_root, namespace)):
        raise storage_fs.DeleteJournalError("record_target_mismatch")
    for part in (token_root.parent, token_root, namespace, target):
        if not storage_fs.path_present(part):
            return None
        if _is_reparse_or_symlink(part) or not part.is_dir():
            raise storage_fs.DeleteJournalError("unsafe_path")
    if not (storage_fs.same_identity(managed_root, record["root_identity"])
            and storage_fs.same_identity(target, record["target_identity"])):
        raise storage_fs.DeleteJournalError("identity_mismatch")
    return target


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


_RECORD_BASES = ("created_cleanup", "terminal_orphan")
_RECORD_ELIGIBILITY = frozenset({
    "basis", "at", "trigger", "status", "expires_at", "group_id", "run_id", "checks"})
_RECORD_CHECKS = ["db_row", "token_namespace", "direct_child", "no_link"]


def _check_record_basis(record: dict) -> str:
    """The recorded delete basis is complete and self-consistent; returns it.

    read_journal only checks that ``eligibility`` is a dict, so a lost or
    foreign basis must be refused here before the record authorizes a delete
    or a DB update.
    """
    eligibility = record["eligibility"]
    if (not _RECORD_ELIGIBILITY <= set(eligibility)
            or not all(isinstance(eligibility[key], str) and eligibility[key]
                       for key in ("basis", "trigger", "status"))
            or _parse_time(eligibility["at"]) is None
            or not isinstance(eligibility["checks"], list)):
        raise storage_fs.DeleteJournalError("eligibility_missing")
    basis = eligibility["basis"]
    if basis not in _RECORD_BASES:
        raise storage_fs.DeleteJournalError("basis_unknown")
    checks = _RECORD_CHECKS + (["manifest_owner"] if record["manifest"] is not None else [])
    if (set(eligibility) != _RECORD_ELIGIBILITY or record["kind"] != "delete"
            or record["reason"] != f"snapshot_{basis}" or eligibility["checks"] != checks
            or (basis == "created_cleanup") != (eligibility["status"] == "created")
            or eligibility["status"] == "deleted"):
        raise storage_fs.DeleteJournalError("eligibility_mismatch")
    return basis


def _check_recorded_owner(row: dict | None, record: dict, *, target_present: bool) -> None:
    """The record's basis, the DB row and any remaining snapshot.json still match.

    Also required when the recorded target is gone: its absence says nothing
    about a row that now names another token's snapshot.  Only a record whose
    row and target are both gone is left to be dropped.
    """
    basis = _check_record_basis(record)
    if row is None and not target_present:
        return
    eligibility = record["eligibility"]
    if (row is None or row["project_id"] != record["project_id"]
            or record["parent"] != {"owner": "token", "id": row["token_id"]}
            or eligibility["group_id"] != row.get("group_id")
            or eligibility["run_id"] != row.get("run_id")):
        raise storage_fs.DeleteJournalError("owner_evidence_lost")
    if row["status"] == "deleted" and not target_present:
        pass  # the delete finished; only the record removal failed
    elif (row["status"] == "deleted"
            or (basis == "created_cleanup") != (row["status"] == "created")):
        raise storage_fs.DeleteJournalError("owner_state_mismatch")
    if record["manifest"] is not None and not _manifest_owned(record["manifest"], row):
        raise storage_fs.DeleteJournalError("owner_evidence_lost")
    if not target_present:
        return
    manifest_path = Path(record["target"]) / "snapshot.json"
    if storage_fs.path_present(manifest_path) and _recorded_manifest(Path(record["target"]), row) is None:
        raise storage_fs.DeleteJournalError("manifest_conflict")


def _resume_recorded(row: dict, actor: str, trigger: str, *, source: str | None = None) -> dict:
    """Continue a recorded delete; the DB is finished only after the target is absent."""
    snapshot_id, project_id = row["snapshot_id"], row["project_id"]
    with storage_fs.journal_lock(JOURNAL_OWNER, project_id, snapshot_id):
        record = storage_fs.read_journal(JOURNAL_OWNER, project_id, snapshot_id)
        target = _recorded_target(project_id, snapshot_id, record)
        # Checked before the DB is touched, whether or not the recorded target remains.
        _check_recorded_owner(row, record, target_present=target is not None)
        garbage = {"categories": [], "path_count": 0, "byte_size": 0}
        if target is not None:
            garbage = _garbage_summary(target)
            try:
                _remove_recorded(record)
            except Exception as exc:
                _log_delete_failure("resume", snapshot_id, exc)
                if row["status"] != "created":
                    return _with_warning(row, exc)
                return _cleanup_failed(row, actor, trigger, exc, source=source)
        result = row
        if row["status"] == "created":
            result = _finish_deleted(row, actor, trigger, garbage,
                                     recovery="delete_record_resumed", source=source)
        _drop_record(row["project_id"], row["snapshot_id"])
        return result


def _recorded_project_ids() -> list[str]:
    """Projects with snapshot records: each directory is sha256(project_id).

    Only the candidate project id is taken from a record here; list_journals
    then validates every record of that project.
    """
    base = storage_fs.journal_storage_root() / storage_fs.GC_DIR_NAME / JOURNAL_OWNER
    try:
        if _is_reparse_or_symlink(base) or not base.is_dir():
            return []
        keys = sorted(entry.path for entry in os.scandir(base) if entry.is_dir(follow_symlinks=False))
    except OSError as exc:
        _log_delete_failure("resume_list", None, exc)
        return []
    found = []
    for key in keys:
        project_id = None
        try:
            for entry in sorted(os.scandir(key), key=lambda item: item.name):
                if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    candidate = json.loads(Path(entry.path).read_text(encoding="utf-8")).get("project_id")
                except (OSError, ValueError, AttributeError):
                    continue
                if isinstance(candidate, str) and storage_fs.project_key(candidate) == Path(key).name:
                    project_id = candidate
                    break
        except OSError as exc:
            _log_delete_failure("resume_list", None, exc)
            continue
        if project_id is None:
            logger.warning("snapshot delete records unusable: owner=%s project_key=%s code=%s",
                           JOURNAL_OWNER, Path(key).name, "project_unknown")
        else:
            found.append(project_id)
    return found


def _resume_pending_records() -> int:
    """Continue recorded snapshot deletes before the ordinary orphan scan.

    A created row whose snapshot still exists stays with the existing retry
    policy (sweep_expired's attempts/backoff through cleanup()); this pass only
    finishes such a row once its target is confirmed absent.
    """
    resumed = 0
    for project_id in _recorded_project_ids():
        try:
            entries = storage_fs.list_journals(JOURNAL_OWNER, project_id)
        except Exception as exc:
            _log_delete_failure("resume_list", None, exc)
            continue
        for entry in entries:
            if entry.error is not None:
                _log_resume_stop(entry.record_id, "unintentional",
                                 storage_fs.DeleteJournalError(entry.error))
                continue
            lock = _operation_lock(entry.record_id)
            if not lock.acquire(blocking=False):
                continue  # a cleanup of this snapshot is running; the next sweep retries
            try:
                if _resume_entry(project_id, entry.record_id):
                    resumed += 1
            except Exception as exc:
                _log_resume_stop(entry.record_id, "unintentional", exc)
            finally:
                lock.release()
    return resumed


def _resume_entry(project_id: str, snapshot_id: str) -> bool:
    row = db.get(snapshot_id)
    if row is not None and row["status"] == "created":
        with storage_fs.journal_lock(JOURNAL_OWNER, project_id, snapshot_id):
            record = storage_fs.read_journal(JOURNAL_OWNER, project_id, snapshot_id)
            target = _recorded_target(project_id, snapshot_id, record)
        if target is not None:
            attempts = int(row.get("cleanup_attempts") or 0)
            if attempts >= SNAPSHOT_CLEANUP_MAX_ATTEMPTS:
                _log_resume_stop(snapshot_id, "intentional",
                                 storage_fs.DeleteJournalError("retry_limit_reached"))
            return False  # cleanup() continues it within the retry policy
        result = _resume_recorded(row, SYSTEM_ACTOR_USER_ID, "recovery", source="snapshot_recovery")
        return result.get("status") == "deleted"
    with storage_fs.journal_lock(JOURNAL_OWNER, project_id, snapshot_id):
        record = storage_fs.read_journal(JOURNAL_OWNER, project_id, snapshot_id)
        target = _recorded_target(project_id, snapshot_id, record)
        _check_recorded_owner(row, record, target_present=target is not None)
        if target is not None:
            # A terminal row's leftover tree: no DB state to change.
            try:
                _remove_recorded(record)
            except Exception as exc:
                _log_delete_failure("resume", snapshot_id, exc)
                return False
        # Confirmed absent (also when the parent token scratch took it) and the
        # row, if any, still matches the record: only the record is left.
        _drop_record(project_id, snapshot_id)
    return True


def _log_delete_failure(stage: str, snapshot_id: str | None, exc: BaseException) -> None:
    """owner/id/stage/errno/winerror and a storage-relative path only."""
    logger.warning(
        "snapshot delete failed: owner=%s id=%s stage=%s code=%s errno=%s winerror=%s path=%s",
        JOURNAL_OWNER, snapshot_id, stage, getattr(exc, "code", None) or type(exc).__name__,
        getattr(exc, "errno", None), getattr(exc, "winerror", None),
        _storage_relative(getattr(exc, "filename", None)))


def _log_resume_stop(snapshot_id: str | None, kind: str, exc: BaseException) -> None:
    """``intentional`` = the existing retry policy stopped it; ``unintentional`` =
    the owner evidence could not be verified (the record is kept)."""
    logger.warning(
        "snapshot delete resume stopped: owner=%s id=%s stop=%s code=%s errno=%s winerror=%s path=%s",
        JOURNAL_OWNER, snapshot_id, kind, getattr(exc, "code", None) or type(exc).__name__,
        getattr(exc, "errno", None), getattr(exc, "winerror", None),
        _storage_relative(getattr(exc, "filename", None)))


def _storage_relative(filename: object) -> str:
    if not filename:
        return "-"
    try:
        storage_root = os.path.abspath(storage_paths.get_storage_root())
        path = os.path.abspath(str(filename))
        if os.path.normcase(os.path.commonpath([path, storage_root])) == os.path.normcase(storage_root):
            return Path(os.path.relpath(path, storage_root)).as_posix()
    except (OSError, TypeError, ValueError):
        pass
    return "<outside-storage>"


def _finish_deleted(row: dict, actor: str, trigger: str, garbage: dict,
                    recovery: str | None = None, *, source: str | None = None) -> dict:
    deleted_at = now_iso()
    with get_store().transaction():
        updated, changed = db.mark_deleted(row["snapshot_id"], deleted_at)
        if changed:
            created = _parse_time(row.get("created_at"))
            deleted = _parse_time(deleted_at)
            lifetime = max(0.0, (deleted - created).total_seconds()) if created and deleted else None
            _record_event(
                "snapshot_deleted", updated, actor, "created", "deleted",
                trigger=trigger, lifetime_seconds=lifetime, garbage=garbage, recovery=recovery,
                source=source,
            )
    return updated


def _cleanup_failed(row: dict, actor: str, trigger: str, exc: Exception,
                    *, source: str | None = None) -> dict:
    reason = public_failure_reason(exc, row)
    current_attempt = int(row.get("cleanup_attempts") or 0) + 1
    delay = min(3600, 60 * (2 ** max(0, current_attempt - 1)))
    next_at = (_utcnow() + timedelta(seconds=delay)).isoformat()
    try:
        remaining = _remaining_count(snapshot_path(row))
    except SnapshotRequestError:
        remaining = None
    with get_store().transaction():
        updated = db.mark_cleanup_failed(row["snapshot_id"], reason, next_at)
        attempt = int(updated.get("cleanup_attempts") or current_attempt)
        _record_event(
            "snapshot_delete_failed", updated, actor, "created", "created",
            error_code="snapshot_cleanup_failed",
            failure_reason=reason,
            retry_attempt=attempt,
            retry_limit_reached=attempt >= SNAPSHOT_CLEANUP_MAX_ATTEMPTS,
            remaining_path_count=remaining,
            trigger=trigger,
            source=source,
        )
    # No traceback: the raw exception text names live scratch paths.
    logger.warning(
        "snapshot cleanup failed snapshot_id=%s trigger=%s attempt=%s reason=%s "
        "code=%s errno=%s winerror=%s path=%s",
        row["snapshot_id"], trigger, current_attempt, reason,
        getattr(exc, "code", None) or type(exc).__name__, getattr(exc, "errno", None),
        getattr(exc, "winerror", None), _storage_relative(getattr(exc, "filename", None)),
    )
    return updated


def _close_unmaterialized(rows: list[dict], actor: str, trigger: str,
                          *, source: str | None = None) -> None:
    for row in rows:
        _record_event(
            "snapshot_rejected", row, actor, "requested_or_approved", "rejected",
            trigger=trigger, reason="owner_lifecycle_finished", source=source,
        )


def _close_unmaterialized_locked(*, actor: str, trigger: str,
                                 run_id: str | None = None,
                                 group_id: str | None = None,
                                 source: str | None = None) -> list[dict]:
    candidates = db.list_unmaterialized(run_id=run_id, group_id=group_id)
    if run_id is not None:
        candidates = [row for row in candidates if not row.get("chain_id")]
        if not candidates:
            return []
    # Acquire in a stable order so overlapping run/group cleanup cannot deadlock.
    with ExitStack() as locks:
        for snapshot_id in sorted(row["snapshot_id"] for row in candidates):
            locks.enter_context(_operation_lock(snapshot_id))
        with get_store().transaction():
            if run_id is not None:
                closed = db.close_unmaterialized_for_run(run_id, actor)
            else:
                closed = db.close_unmaterialized_for_group(group_id, actor)
            _close_unmaterialized(closed, actor, trigger, source=source)
    return closed


def cleanup_for_run(run_id: str, actor: str = SYSTEM_ACTOR_USER_ID) -> dict:
    """``actor`` should be the run's real actor (e.g. the token's ``issued_to``) when the
    caller has one; it falls back to the reserved system user otherwise. Either way the
    run-cleanup nature is recorded as ``source``/``trigger`` metadata, never as the actor.
    """
    closed = _close_unmaterialized_locked(
        run_id=run_id, actor=actor, trigger="run_finished", source="snapshot_run_cleanup",
    )
    # Chain-bound capabilities outlive one hop; group cleanup/TTL remains authoritative.
    rows = [row for row in db.list_created(run_id=run_id) if not row.get("chain_id")]
    results = [
        cleanup(row["snapshot_id"], actor, trigger="run_finished", source="snapshot_run_cleanup")
        for row in rows
    ]
    return {
        "matched": len(closed) + len(rows),
        "closed": len(closed),
        "deleted": sum(row.get("status") == "deleted" for row in results),
        "cleanup_failed": sum(bool(row.get("cleanup_failed")) for row in results),
    }


def cleanup_for_group(group_id: str, actor: str = SYSTEM_ACTOR_USER_ID) -> dict:
    """``actor`` should be the real user closing the group when one is known (e.g. a
    human group-close); it falls back to the reserved system user otherwise. The
    group-close nature is recorded as ``trigger="group_finished"``/``source`` metadata.
    """
    closed = _close_unmaterialized_locked(
        group_id=group_id, actor=actor, trigger="group_finished", source="snapshot_group_cleanup",
    )
    rows = db.list_created(group_id=group_id)
    results = [
        cleanup(row["snapshot_id"], actor, trigger="group_finished", source="snapshot_group_cleanup")
        for row in rows
    ]
    return {
        "matched": len(closed) + len(rows),
        "closed": len(closed),
        "deleted": sum(row.get("status") == "deleted" for row in results),
        "cleanup_failed": sum(bool(row.get("cleanup_failed")) for row in results),
    }


def _orphan_candidates(namespace: Path, snapshot_id: str | None = None) -> list[Path]:
    if not namespace.is_dir() or _is_reparse_or_symlink(namespace):
        return []
    prefixes = (f".{snapshot_id}.",) if snapshot_id else (".snap_",)
    return [
        child for child in namespace.iterdir()
        if child.name.startswith(prefixes) and (
            child.name.endswith(".tmp") or child.name.endswith(".materializing")
        )
    ]


def cleanup_orphans(*, startup: bool = False) -> int:
    # Recorded deletes first: a partial delete may already have removed the
    # snapshot.json/README.md/source that the ordinary checks below depend on.
    _resume_pending_records()
    removed = 0
    for row in db.list_materialization_candidates():
        try:
            namespace = _snapshot_namespace(row, create=False)
        except SnapshotRequestError:
            continue
        candidates = _orphan_candidates(namespace, row["snapshot_id"])
        had_orphan = False
        for candidate in candidates:
            try:
                age = _utcnow().timestamp() - candidate.lstat().st_mtime
                if not startup and age < ORPHAN_GRACE_SECONDS:
                    continue
                if _is_reparse_or_symlink(candidate):
                    continue
                if candidate.is_dir():
                    storage_fs.remove_managed_tree(candidate, namespace)
                else:
                    candidate.unlink()
                removed += 1
                had_orphan = True
            except OSError as exc:
                _log_delete_failure("staging_orphan", row["snapshot_id"], exc)
        final = namespace / row["snapshot_id"]
        if row["status"] == "approved" and final.exists():
            try:
                _recover_published(row, final, SYSTEM_ACTOR_USER_ID)
            except Exception:
                logger.warning("snapshot published-directory recovery failed for %s", row["snapshot_id"], exc_info=True)
        elif startup and row["status"] == "approved" and had_orphan and not final.exists():
            try:
                _record_create_failure(
                    row, SYSTEM_ACTOR_USER_ID,
                    SnapshotRequestError(500, "orphan_staging_recovered", "materialization stopped before atomic publish"),
                    source="snapshot_recovery",
                )
            except Exception:
                logger.warning("snapshot interrupted creation recovery failed for %s", row["snapshot_id"], exc_info=True)
        elif row["status"] == "created" and _confirmed_absent(final):
            try:
                _finish_deleted(
                    row, SYSTEM_ACTOR_USER_ID, "recovery",
                    {"categories": [], "path_count": 0, "byte_size": 0},
                    recovery="filesystem_deleted_before_db_update",
                    source="snapshot_recovery",
                )
            except Exception:
                logger.warning("snapshot deleted-state recovery failed for %s", row["snapshot_id"], exc_info=True)
    return removed


def _confirmed_absent(path: Path) -> bool:
    """Only a real ENOENT lets the deleted state be recorded (a failed lookup is not)."""
    try:
        return not storage_fs.path_present(path)
    except storage_fs.PresenceUnknown:
        return False


def sweep_expired(now: datetime | None = None) -> dict:
    point = (now or _utcnow()).astimezone(timezone.utc)
    cleanup_orphans()
    matched = deleted = failed = skipped = 0
    for row in db.list_created():
        expires = _parse_time(row.get("expires_at"))
        if expires is None:
            created = _parse_time(row.get("created_at"))
            expires = created + timedelta(hours=SNAPSHOT_TTL_HOURS) if created else None
        if expires is None or expires > point:
            continue
        matched += 1
        attempts = int(row.get("cleanup_attempts") or 0)
        next_at = _parse_time(row.get("cleanup_next_at"))
        if attempts >= SNAPSHOT_CLEANUP_MAX_ATTEMPTS or (next_at and next_at > point):
            skipped += 1
            continue
        result = cleanup(
            row["snapshot_id"], SYSTEM_ACTOR_USER_ID,
            trigger="ttl_expired", source="snapshot_ttl_cleanup",
        )
        if result.get("status") == "deleted":
            deleted += 1
        elif result.get("cleanup_failed"):
            failed += 1
    return {"matched": matched, "deleted": deleted, "cleanup_failed": failed, "skipped": skipped}


def startup() -> None:
    global _sweep_started
    with _sweep_guard:
        if _sweep_started:
            return
        _sweep_started = True
        _sweep_stop.clear()
    from modules.flow_gate.services import snapshot_request_service
    snapshot_request_service.retire_unmaterialized(SYSTEM_ACTOR_USER_ID)
    cleanup_orphans(startup=True)
    sweep_expired()

    def _loop() -> None:
        while not _sweep_stop.wait(SNAPSHOT_SWEEP_INTERVAL_SECONDS):
            try:
                sweep_expired()
            except Exception:
                logger.warning("snapshot TTL sweep failed", exc_info=True)

    threading.Thread(target=_loop, name="snapshot-cleanup-sweep", daemon=True).start()


def shutdown() -> None:
    global _sweep_started
    _sweep_stop.set()
    with _sweep_guard:
        _sweep_started = False
