"""Approved current-worktree snapshot materialization, freshness, and cleanup."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
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

logger = logging.getLogger(__name__)

SNAPSHOT_SCHEMA = 1
SNAPSHOT_OWNER = "flowgate.source-snapshot"
SNAPSHOT_NAMESPACE = "source-snapshots"
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

# Snapshot-copy policy is deliberately narrower than the TR change-list debris policy:
# dot-prefixed source such as .github remains source, while known VCS/tool/build trees do not.
EXCLUDED_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "env", "site-packages",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox",
    ".cache", ".parcel-cache", ".turbo",
    "build", "dist", "htmlcov", "coverage", "coverage_html_report",
    "tmp", "temp", SNAPSHOT_NAMESPACE,
})
EXCLUDED_FILE_NAMES = frozenset({".coverage"})
EXCLUDED_DIR_PREFIXES = (".test-tmp", "pytest-cache-files-")
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
        namespace.mkdir(exist_ok=True)
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


def _current_fingerprint(root: Path, row: dict, baseline: list[dict] | None = None) -> tuple[str, list[dict]]:
    _, files, _ = _collect_scope(row, root)
    _check_limits(files)
    known = {item["path"]: item for item in (baseline or [])}
    entries: list[dict] = []
    total = 0
    for relative, st in files:
        old = known.get(relative)
        if old and int(old.get("size", -1)) == int(st.st_size) and int(old.get("mtime_ns", -1)) == int(st.st_mtime_ns):
            sha = str(old["sha256"])
            size = int(st.st_size)
        else:
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
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise SnapshotRequestError(409, "snapshot_integrity_error", "snapshot manifest ownership does not match")
    if not (resolved / "source").is_dir() or not (resolved / "README.md").is_file():
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


def _record_create_failure(row: dict, actor: str, exc: Exception) -> dict:
    reason = getattr(exc, "message", str(exc))[:1000]
    stage = getattr(exc, "code", type(exc).__name__)
    with get_store().transaction():
        failed, changed = db.mark_failed(row["snapshot_id"], "snapshot_create_failed", reason)
        if changed:
            _record_event(
                "state_changed", failed, actor, "approved", "failed",
                error_code="snapshot_create_failed", failure_stage=stage, failure_reason=reason,
            )
    return failed


def _recover_published(row: dict, final: Path, actor: str) -> dict:
    manifest = _load_manifest(final, row)
    created_at = str(manifest["created_at"])
    expires_at = str(manifest.get("expires_at") or (
        (_parse_time(created_at) or _utcnow()) + timedelta(hours=SNAPSHOT_TTL_HOURS)
    ).isoformat())
    with get_store().transaction():
        updated, changed = db.mark_created(
            row["snapshot_id"], created_at, expires_at,
            str(manifest["source_revision"]), str(manifest["worktree_fingerprint"]),
            int(manifest["copied_file_count"]), int(manifest["copied_byte_size"]),
        )
        if changed:
            _record_event(
                "snapshot_created", updated, actor, "approved", "created",
                copied_file_count=manifest["copied_file_count"],
                copied_byte_size=manifest["copied_byte_size"],
                excluded=manifest.get("excluded"),
                recovery="published_before_db_update",
            )
    return updated


def materialize(snapshot_id: str, actor: str) -> dict:
    lock = _operation_lock(snapshot_id)
    with lock:
        row = db.get(snapshot_id)
        if row is None:
            raise SnapshotRequestError(404, "not_found", "snapshot request not found")
        if row["status"] == "created":
            return refresh_stale(snapshot_id, actor=actor)
        if row["status"] != "approved":
            raise SnapshotRequestError(409, "snapshot_not_approved", "only an approved snapshot can be materialized")

        namespace = _snapshot_namespace(row, create=True)
        final = namespace / snapshot_id
        if final.exists() or final.is_symlink():
            return _recover_published(row, final, actor)

        claim = namespace / f".{snapshot_id}.materializing"
        stage = namespace / f".{snapshot_id}.{uuid.uuid4().hex}.tmp"
        published = False
        try:
            claim.mkdir()
        except FileExistsError as exc:
            raise SnapshotRequestError(409, "snapshot_materialization_busy", "snapshot materialization is already running") from exc

        try:
            root = _resolve_source_root(row)
            revision, dirty = _git_identity(root, row)
            directories, files, excluded = _collect_scope(row, root)
            _check_limits(files)

            stage.mkdir()
            source_out = stage / "source"
            source_out.mkdir()
            for relative in directories:
                (source_out / PurePosixPath(relative)).mkdir(parents=True, exist_ok=True)

            entries: list[dict] = []
            copied_bytes = 0
            for relative, expected in files:
                sha, size = _copy_and_hash(
                    root / PurePosixPath(relative),
                    source_out / PurePosixPath(relative),
                    expected,
                )
                copied_bytes += size
                if copied_bytes > SNAPSHOT_MAX_TOTAL_BYTES:
                    raise SnapshotRequestError(413, "snapshot_total_size_limit", "snapshot exceeds total byte limit")
                entries.append({
                    "path": relative,
                    "size": size,
                    "mtime_ns": int(expected.st_mtime_ns),
                    "sha256": sha,
                })

            fingerprint = _fingerprint(entries)
            current_fingerprint, _ = _current_fingerprint(root, row, entries)
            if current_fingerprint != fingerprint:
                raise SnapshotRequestError(409, "snapshot_source_changed", "source changed while snapshot was being built")
            revision_after, _ = _git_identity(root, row)
            if revision_after != revision:
                raise SnapshotRequestError(409, "snapshot_source_changed", "worktree HEAD changed while snapshot was being built")

            created = _utcnow()
            created_at = created.isoformat()
            expires_at = (created + timedelta(hours=SNAPSHOT_TTL_HOURS)).isoformat()
            manifest = _manifest(
                row, revision, dirty, fingerprint, entries, copied_bytes, excluded,
                created_at, expires_at,
            )
            (stage / "README.md").write_text(
                _readme(row, revision, fingerprint, created_at, excluded),
                encoding="utf-8", newline="\n",
            )
            _write_json(stage / "snapshot.json", manifest)
            _load_manifest(stage, row)
            stage.replace(final)
            published = True

            with get_store().transaction():
                updated, changed = db.mark_created(
                    snapshot_id, created_at, expires_at, revision, fingerprint,
                    len(entries), copied_bytes,
                )
                if not changed:
                    raise SnapshotRequestError(409, "snapshot_state_conflict", "snapshot state changed before publish completed")
                _record_event(
                    "snapshot_created", updated, actor, "approved", "created",
                    copied_file_count=len(entries), copied_byte_size=copied_bytes,
                    excluded=_excluded_summary(excluded),
                    approval_latency_seconds=max(
                        0.0,
                        (created - (_parse_time(row.get("approved_at")) or created)).total_seconds(),
                    ),
                )
            result = dict(updated)
            result["snapshot_path"] = str(final)
            result["stale"] = False
            result["available"] = True
            return result
        except Exception as exc:
            if not published:
                if stage.exists() and not _is_reparse_or_symlink(stage):
                    shutil.rmtree(stage, ignore_errors=True)
                try:
                    _record_create_failure(row, actor, exc)
                except Exception:
                    logger.exception("snapshot %s creation failure could not be persisted", snapshot_id)
            raise
        finally:
            if claim.exists() and not _is_reparse_or_symlink(claim):
                try:
                    claim.rmdir()
                except OSError:
                    logger.warning("snapshot %s materialization claim could not be removed", snapshot_id, exc_info=True)


def _mark_stale(row: dict, actor: str, current_revision: str | None, reason: str) -> dict:
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
            )
    return updated


def refresh_stale(snapshot_id: str, actor: str = "snapshot-freshness") -> dict:
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
        updated = _mark_stale(row, actor, None, "manifest_unreadable")
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
        baseline = fingerprint_data.get("entries")
        if not isinstance(baseline, list):
            raise SnapshotRequestError(409, "snapshot_integrity_error", "fingerprint entries are missing")
        current_fingerprint, _ = _current_fingerprint(root, row, baseline)
        if current_fingerprint == row.get("source_fingerprint"):
            result["current_source_revision"] = current_revision
            return result
        updated = _mark_stale(row, actor, current_revision, "scope_fingerprint_changed")
    except Exception:
        logger.warning("snapshot %s freshness check failed closed", snapshot_id, exc_info=True)
        updated = _mark_stale(row, actor, None, "source_unavailable")
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


def cleanup(snapshot_id: str, actor: str, *, trigger: str = "explicit") -> dict:
    lock = _operation_lock(snapshot_id)
    with lock:
        row = db.get(snapshot_id)
        if row is None:
            raise SnapshotRequestError(404, "not_found", "snapshot request not found")
        if row["status"] == "deleted":
            return row
        try:
            final = snapshot_path(row)
        except SnapshotRequestError as exc:
            if row["status"] != "created":
                return row
            return _cleanup_failed(row, actor, trigger, exc)

        if row["status"] != "created":
            if not final.exists() and not final.is_symlink():
                return row
            try:
                manifest = _load_manifest(final, row)
                if manifest:
                    shutil.rmtree(final)
            except Exception as exc:
                logger.warning("terminal snapshot %s orphan cleanup failed", snapshot_id, exc_info=True)
                result = dict(row)
                result["cleanup_warning"] = str(exc)
                return result
            return row

        garbage = _garbage_summary(final)
        if not final.exists() and not final.is_symlink():
            return _finish_deleted(row, actor, trigger, garbage, recovery="directory_already_absent")
        try:
            if _is_reparse_or_symlink(final):
                raise SnapshotRequestError(409, "snapshot_cleanup_failed", "snapshot directory became a link/reparse point")
            if final.resolve(strict=True).parent != final.parent.resolve(strict=True):
                raise SnapshotRequestError(409, "snapshot_cleanup_failed", "snapshot directory escaped its namespace")
            shutil.rmtree(final)
            if final.exists() or final.is_symlink():
                raise OSError("snapshot directory remains after deletion")
        except Exception as exc:
            return _cleanup_failed(row, actor, trigger, exc)
        return _finish_deleted(row, actor, trigger, garbage)


def _finish_deleted(row: dict, actor: str, trigger: str, garbage: dict,
                    recovery: str | None = None) -> dict:
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
            )
    return updated


def _cleanup_failed(row: dict, actor: str, trigger: str, exc: Exception) -> dict:
    reason = getattr(exc, "message", str(exc))[:1000]
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
        )
    logger.warning(
        "snapshot cleanup failed snapshot_id=%s trigger=%s attempt=%s reason=%s",
        row["snapshot_id"], trigger, current_attempt, reason, exc_info=True,
    )
    return updated


def _close_unmaterialized(rows: list[dict], actor: str, trigger: str) -> None:
    for row in rows:
        _record_event(
            "snapshot_rejected", row, actor, "requested_or_approved", "rejected",
            trigger=trigger, reason="owner_lifecycle_finished",
        )


def _close_unmaterialized_locked(*, actor: str, trigger: str,
                                 run_id: str | None = None,
                                 group_id: str | None = None) -> list[dict]:
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
            _close_unmaterialized(closed, actor, trigger)
    return closed


def cleanup_for_run(run_id: str, actor: str = "snapshot-run-cleanup") -> dict:
    closed = _close_unmaterialized_locked(
        run_id=run_id, actor=actor, trigger="run_finished",
    )
    # Chain-bound capabilities outlive one hop; group cleanup/TTL remains authoritative.
    rows = [row for row in db.list_created(run_id=run_id) if not row.get("chain_id")]
    results = [cleanup(row["snapshot_id"], actor, trigger="run_finished") for row in rows]
    return {
        "matched": len(closed) + len(rows),
        "closed": len(closed),
        "deleted": sum(row.get("status") == "deleted" for row in results),
        "cleanup_failed": sum(bool(row.get("cleanup_failed")) for row in results),
    }


def cleanup_for_group(group_id: str, actor: str = "snapshot-group-cleanup") -> dict:
    closed = _close_unmaterialized_locked(
        group_id=group_id, actor=actor, trigger="group_finished",
    )
    rows = db.list_created(group_id=group_id)
    results = [cleanup(row["snapshot_id"], actor, trigger="group_finished") for row in rows]
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
                    shutil.rmtree(candidate)
                else:
                    candidate.unlink()
                removed += 1
                had_orphan = True
            except OSError:
                logger.warning("snapshot orphan staging cleanup failed", exc_info=True)
        final = namespace / row["snapshot_id"]
        if row["status"] == "approved" and final.exists():
            try:
                _recover_published(row, final, "snapshot-recovery")
            except Exception:
                logger.warning("snapshot published-directory recovery failed for %s", row["snapshot_id"], exc_info=True)
        elif startup and row["status"] == "approved" and had_orphan and not final.exists():
            try:
                _record_create_failure(
                    row, "snapshot-recovery",
                    SnapshotRequestError(500, "orphan_staging_recovered", "materialization stopped before atomic publish"),
                )
            except Exception:
                logger.warning("snapshot interrupted creation recovery failed for %s", row["snapshot_id"], exc_info=True)
        elif row["status"] == "created" and not final.exists():
            try:
                _finish_deleted(
                    row, "snapshot-recovery", "recovery",
                    {"categories": [], "path_count": 0, "byte_size": 0},
                    recovery="filesystem_deleted_before_db_update",
                )
            except Exception:
                logger.warning("snapshot deleted-state recovery failed for %s", row["snapshot_id"], exc_info=True)
    return removed


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
        result = cleanup(row["snapshot_id"], "snapshot-ttl-cleanup", trigger="ttl_expired")
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
