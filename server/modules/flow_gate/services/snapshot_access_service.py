"""AI access to human-approved disposable source snapshots.

There is deliberately no write-back or promotion operation. Every path is resolved
below the approved snapshot source directory, while permanent changes continue to use
the existing FlowGate mutation tools.
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from modules.flow_gate.db import snapshot_requests as request_db
from modules.flow_gate.db import snapshot_usages as usage_db
from modules.flow_gate.db import workflow_events
from modules.flow_gate.services import process_runner
from modules.flow_gate.services import snapshot_materialization_service as materialization

STALE_WARNING = "ACTIVE SNAPSHOT IS STALE"
STALE_EXPLANATION = (
    "This result is based on a stale snapshot and cannot be treated as current "
    "worktree validation."
)
ACCESS_OPERATIONS = frozenset({"status", "read", "search", "glob", "stat"})
TASK_KINDS = frozenset({
    "build", "test", "lint", "typecheck", "dependency_analysis",
    "static_analysis", "temporary_experiment",
})
MUTATION_TOOL_NAMES = frozenset({
    "write_source_file", "patch_source_file", "remove_source_file",
})
MAX_READ_BYTES = 1024 * 1024
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
MAX_RESULTS = 500
SNAPSHOT_PATH_REDACTION = materialization.SCRATCH_REDACTION


def _public_roots(row: dict | None) -> tuple[tuple[str, str], ...]:
    """Every server-internal root this snapshot's text may name: token scratch (final,
    source, and .flowgate-tmp all nest under it), live group worktree, storage root, and
    server root. Derived from project/token/group ids, not a materialized path, so a
    failed snapshot's stored reason is covered too."""
    return materialization.locator_roots(row)


def sanitize_public(value: Any, roots: tuple[tuple[str, str], ...], *, error: bool = False) -> Any:
    """Recursively redact server-internal absolute paths from a worker-facing payload.

    This is the single sanitizer both the API tool-call path (api_server_tools.py) and
    the CLI HTTP path (snapshot_routes.py ``/cli/...``) rely on: ``access()``, ``execute()``,
    and ``SnapshotAccessError.payload()`` all route their return value through here before
    it leaves this module. Content/stdout keep unrelated text and only lose the known
    roots; ``error=True`` (messages/details built from exception text) also removes any
    other absolute path, since that text is diagnostic and never file content.
    """
    if isinstance(value, str):
        if error:
            return materialization.redact_error_text(value, roots)
        return materialization.redact_locators(value, roots)
    if isinstance(value, dict):
        return {key: sanitize_public(item, roots, error=error) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_public(item, roots, error=error) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_public(item, roots, error=error) for item in value)
    return value


def _row_or_none(snapshot_id: str | None) -> dict | None:
    if not snapshot_id:
        return None
    try:
        return request_db.get(snapshot_id)
    except Exception:
        return None


def public_error_text(text: str, snapshot_id: str | None = None) -> str:
    """Scrub one error message for a worker-facing boundary outside SnapshotAccessError
    (e.g. a SnapshotRequestError the CLI materialize route renders)."""
    return materialization.redact_error_text(str(text), _public_roots(_row_or_none(snapshot_id)))


class SnapshotAccessError(ValueError):
    def __init__(
        self, status: int, code: str, message: str, *,
        state: str | None = None, snapshot_id: str | None = None,
        details: dict | None = None,
    ):
        self.status = status
        self.code = code
        self.message = message
        self.state = state
        self.snapshot_id = snapshot_id
        self.details = details or {}
        super().__init__(message)

    def payload(self, operation: str) -> dict:
        error_details = dict(self.details)
        if self.state:
            error_details["state"] = self.state
        result = {
            "ok": False,
            "op": operation,
            "snapshot_id": self.snapshot_id,
            "state": self.state,
            "error": {
                "code": self.code,
                "message": self.message,
                "details": error_details,
            },
        }
        # message/details can carry a stored failure_reason or exception text that leaked
        # a raw scratch/worktree path (e.g. an OSError str()); this is the one place every
        # error this module raises funnels through before an API or CLI caller renders it,
        # so it must redact even when the raise site itself never touched a path. Error
        # mode also drops absolute paths under no known root.
        return sanitize_public(result, _public_roots(_row_or_none(self.snapshot_id)), error=True)


def _run_axis(run: dict, key: str) -> str:
    aliases = {
        "project_id": ("project_id", "project"),
        "group_id": ("group_id", "group"),
        "run_id": ("run_id", "ai_run_id"),
        "chain_id": ("chain_id",),
        "token_id": ("token_id", "current_token_id"),
    }
    for alias in aliases[key]:
        value = run.get(alias)
        if value:
            return str(value)
    return ""


def _authorize(run: dict, snapshot_id: str) -> dict:
    row = request_db.get(snapshot_id)
    if row is None:
        raise SnapshotAccessError(
            404, "snapshot_not_found", "snapshot does not exist",
            state="missing", snapshot_id=snapshot_id,
        )
    for key in ("project_id", "group_id"):
        if str(row.get(key) or "") != _run_axis(run, key):
            raise SnapshotAccessError(
                403, "snapshot_forbidden",
                "snapshot belongs to a different project, group, or AI run lineage",
                state="forbidden", snapshot_id=snapshot_id,
            )
    owner_run = str(row.get("run_id") or "")
    consumer_run = _run_axis(run, "run_id")
    owner_chain = str(row.get("chain_id") or "")
    consumer_chain = _run_axis(run, "chain_id")
    if owner_run != consumer_run and not (
        owner_chain and consumer_chain and owner_chain == consumer_chain
    ):
        raise SnapshotAccessError(
            403, "snapshot_forbidden",
            "snapshot belongs to a different project, group, or AI run lineage",
            state="forbidden", snapshot_id=snapshot_id,
        )
    return row


def _public_state(row: dict) -> str:
    if row.get("status") == "created":
        return "stale" if bool(row.get("stale")) else "active"
    return str(row.get("status") or "failed")


def _metadata(row: dict) -> dict:
    """Public snapshot metadata. Never includes the raw filesystem locator or any

    scratch-directory path: this dict is returned verbatim to AI tool calls (API
    provider tool results) and CLI HTTP responses, both of which are untrusted
    consumers of the snapshot boundary.
    """
    state = _public_state(row)
    result = {
        "snapshot_id": row.get("snapshot_id"),
        "request_id": row.get("snapshot_id"),
        "project_id": row.get("project_id"),
        "group_id": row.get("group_id"),
        "run_id": row.get("run_id"),
        "token_id": row.get("token_id"),
        "provider_id": row.get("provider_id"),
        "source_kind": row.get("source_kind"),
        "source_revision": row.get("source_revision"),
        "scope": row.get("scope"),
        "requested_paths": row.get("requested_paths") or [],
        "created_at": row.get("created_at"),
        "stale": bool(row.get("stale")),
        "status": state,
    }
    if state == "stale":
        result["warning"] = STALE_WARNING
        result["validation_claim"] = STALE_EXPLANATION
        result["current_worktree_validation_allowed"] = False
    else:
        result["current_worktree_validation_allowed"] = state == "active"
    return result


def _refresh_available(run: dict, snapshot_id: str) -> tuple[dict, Path, dict]:
    row = _authorize(run, snapshot_id)
    state = _public_state(row)
    if state == "deleted":
        raise SnapshotAccessError(
            410, "snapshot_deleted", "snapshot was cleaned up and its locator is invalid",
            state="deleted", snapshot_id=snapshot_id,
        )
    if state == "failed":
        raise SnapshotAccessError(
            409, "snapshot_failed",
            str(row.get("failure_reason") or "snapshot materialization failed"),
            state="failed", snapshot_id=snapshot_id,
            details={"failure_code": row.get("failure_code")},
        )
    if state not in {"active", "stale"}:
        raise SnapshotAccessError(
            409, "snapshot_not_ready",
            "snapshot is not materialized; human approval and materialization are required",
            state=state, snapshot_id=snapshot_id,
        )
    try:
        refreshed = materialization.refresh_stale(
            snapshot_id, actor=f"ai-run:{_run_axis(run, 'run_id')}"
        )
    except Exception as exc:
        raise SnapshotAccessError(
            409, "snapshot_failed", "snapshot freshness/read check failed",
            state="failed", snapshot_id=snapshot_id,
            details={"reason": getattr(exc, "code", type(exc).__name__)},
        ) from exc
    if not refreshed.get("available"):
        raise SnapshotAccessError(
            409, "snapshot_failed", "snapshot files are unavailable or invalid",
            state="failed", snapshot_id=snapshot_id,
            details={"reason": refreshed.get("integrity_error") or "snapshot_unavailable"},
        )
    final = Path(str(refreshed.get("snapshot_path") or ""))
    source = final / "source"
    try:
        final_resolved = final.resolve(strict=True)
        source_resolved = source.resolve(strict=True)
        source_resolved.relative_to(final_resolved)
    except (OSError, ValueError) as exc:
        raise SnapshotAccessError(
            409, "snapshot_failed", "snapshot locator failed its ownership jail",
            state="failed", snapshot_id=snapshot_id,
            details={"reason": "snapshot_locator_invalid"},
        ) from exc
    if not source_resolved.is_dir() or materialization._is_reparse_or_symlink(source):
        raise SnapshotAccessError(
            409, "snapshot_failed", "snapshot source root is unavailable or unsafe",
            state="failed", snapshot_id=snapshot_id,
            details={"reason": "snapshot_locator_invalid"},
        )
    meta = _metadata(refreshed)
    return refreshed, source_resolved, meta


def _safe_relative(raw: object, *, allow_empty: bool = False) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    if allow_empty and not value:
        return ""
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if (
        not value or value == "." or "\x00" in value
        or posix.is_absolute() or windows.is_absolute() or windows.drive
        or value.startswith("//")
        or any(part in ("", ".", "..") or ":" in part for part in value.split("/"))
        or any(ord(ch) < 32 for ch in value)
    ):
        raise SnapshotAccessError(
            422, "snapshot_path_invalid",
            "snapshot paths must be normal paths relative to the approved locator",
        )
    return posix.as_posix()


def _member(root: Path, raw: object, *, require_exists: bool = True) -> Path:
    relative = _safe_relative(raw)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if require_exists or current.exists() or current.is_symlink():
            try:
                entry_stat = current.lstat()
            except OSError as exc:
                raise SnapshotAccessError(
                    404, "snapshot_path_not_found",
                    f"snapshot path does not exist: {relative}",
                ) from exc
            if materialization._is_reparse_or_symlink(current, entry_stat):
                raise SnapshotAccessError(
                    403, "snapshot_path_escape",
                    "snapshot symlink/junction/reparse traversal is not allowed",
                )
    try:
        resolved = current.resolve(strict=require_exists)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SnapshotAccessError(
            403, "snapshot_path_escape",
            "resolved snapshot path leaves the approved locator",
        ) from exc
    return resolved


def _record_usage(
    run: dict, row: dict, *, access_kind: str, operation: str,
    success: bool = True, task_kind: str | None = None,
    current_claim: bool = False, detail: dict | None = None,
) -> dict:
    return usage_db.record({
        "snapshot_id": row["snapshot_id"],
        "run_id": _run_axis(run, "run_id"),
        "token_id": _run_axis(run, "token_id") or row.get("token_id"),
        "access_kind": access_kind,
        "operation": operation,
        "task_kind": task_kind,
        "success": success,
        "stale_at_use": bool(row.get("stale")),
        "current_worktree_claim": current_claim,
        "detail": detail,
    })


def _audit(event_type: str, row: dict, run: dict, **extra: Any) -> None:
    metadata = {
        "snapshot_id": row.get("snapshot_id"),
        "run_id": _run_axis(run, "run_id"),
        "owner_run_id": row.get("run_id"),
        "chain_id": row.get("chain_id"),
        "group_id": row.get("group_id"),
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
    metadata.update(extra)
    workflow_events.create({
        "event_type": event_type,
        "project_id": row["project_id"],
        "group_id": row["group_id"],
        "document_id": extra.get("document_id"),
        "actor_user_id": f"ai-run:{_run_axis(run, 'run_id')}",
        "from_state": _public_state(row),
        "to_state": _public_state(row),
        "metadata": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    })


def _unexpected(exc: Exception, tool_input: dict, operation: str) -> SnapshotAccessError:
    """An exception no raise site authored (OSError from open/stat/mkdir/Popen, decoding,
    etc.) must not escape as raw text: its str() names the snapshot or worktree path."""
    snapshot_id = str(tool_input.get("snapshot_id") or "") or None
    if isinstance(exc, OSError):
        return SnapshotAccessError(
            409, "snapshot_io_failed",
            f"snapshot {operation} failed ({type(exc).__name__}: {exc})",
            snapshot_id=snapshot_id,
        )
    return SnapshotAccessError(
        500, "snapshot_operation_failed",
        f"snapshot {operation} failed ({type(exc).__name__}: {exc})",
        snapshot_id=snapshot_id,
    )


def access(run: dict, tool_input: dict) -> tuple[int, dict]:
    try:
        return _access(run, tool_input)
    except SnapshotAccessError:
        raise
    except Exception as exc:
        raise _unexpected(exc, tool_input, "access") from exc


def status_metadata(run: dict, snapshot_id: str) -> dict:
    """Metadata for the worker-facing CLI status route.

    A created snapshot is re-checked against the live source content (the same
    ``refresh_stale`` every access/run goes through) before its state is reported, so a
    disguised edit (same size, restored mtime) is ``stale`` here too instead of the DB's
    last verdict. Non-created requests (requested/approved/failed/...) report as stored.
    """
    row = _authorize(run, snapshot_id)
    if row.get("status") != "created":
        return _metadata(row)
    try:
        refreshed = materialization.refresh_stale(
            snapshot_id, actor=f"ai-run:{_run_axis(run, 'run_id')}"
        )
    except Exception as exc:
        raise SnapshotAccessError(
            409, "snapshot_failed", "snapshot freshness check failed",
            state="failed", snapshot_id=snapshot_id,
            details={"reason": getattr(exc, "code", type(exc).__name__)},
        ) from exc
    meta = _metadata(refreshed)
    meta["available"] = bool(refreshed.get("available"))
    if not meta["available"]:
        meta["integrity_error"] = refreshed.get("integrity_error") or "snapshot_unavailable"
        meta["current_worktree_validation_allowed"] = False
    return meta


def _access(run: dict, tool_input: dict) -> tuple[int, dict]:
    operation = str(tool_input.get("operation") or "")
    if operation not in ACCESS_OPERATIONS:
        raise SnapshotAccessError(422, "snapshot_operation_invalid", "unsupported snapshot access operation")
    snapshot_id = str(tool_input.get("snapshot_id") or "")
    row, root, meta = _refresh_available(run, snapshot_id)
    payload: dict = {"ok": True, "op": operation, "snapshot": meta}

    if operation == "status":
        pass
    elif operation == "read":
        target = _member(root, tool_input.get("path"))
        if not target.is_file():
            raise SnapshotAccessError(422, "snapshot_path_not_file", "read requires a regular file")
        size = target.stat().st_size
        offset = int(tool_input.get("offset") or 0)
        limit = tool_input.get("length")
        if limit is None:
            limit = tool_input.get("max_bytes", MAX_READ_BYTES)
        limit = min(MAX_READ_BYTES, max(0, int(limit)))
        with target.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(limit + 1)
        truncated = len(raw) > limit
        raw = raw[:limit]
        payload.update({
            "path": target.relative_to(root).as_posix(),
            "content": raw.decode(str(tool_input.get("encoding") or "utf-8"), errors="replace"),
            "size": size,
            "offset": offset,
            "returned_bytes": len(raw),
            "truncated": truncated or offset + len(raw) < size,
        })
    elif operation == "stat":
        target = _member(root, tool_input.get("path"))
        info = target.stat()
        payload.update({
            "path": target.relative_to(root).as_posix(),
            "kind": "directory" if target.is_dir() else "file" if target.is_file() else "other",
            "size": int(info.st_size),
            "mtime_ns": int(info.st_mtime_ns),
        })
    elif operation == "glob":
        pattern = _safe_relative(tool_input.get("pattern"))
        paths: list[str] = []
        for candidate in root.glob(pattern):
            try:
                relative = candidate.relative_to(root).as_posix()
                safe = _member(root, relative)
            except SnapshotAccessError:
                continue
            paths.append(safe.relative_to(root).as_posix())
            if len(paths) >= MAX_RESULTS:
                break
        payload.update({"paths": sorted(set(paths)), "truncated": len(paths) >= MAX_RESULTS})
    else:
        pattern = str(tool_input.get("pattern") or "")
        if not pattern:
            raise SnapshotAccessError(422, "snapshot_pattern_required", "search requires a pattern")
        try:
            matcher = re.compile(pattern, re.IGNORECASE if tool_input.get("ignore_case") else 0)
        except re.error as exc:
            raise SnapshotAccessError(422, "snapshot_pattern_invalid", str(exc)) from exc
        base_raw = tool_input.get("path")
        base = root if base_raw in (None, "") else _member(root, base_raw)
        if not base.is_dir():
            raise SnapshotAccessError(422, "snapshot_path_not_directory", "search path must be a directory")
        file_glob = str(tool_input.get("glob") or "*")
        maximum = min(MAX_RESULTS, max(1, int(tool_input.get("max_results") or 100)))
        matches: list[dict] = []
        for base_dir, dirs, files in os.walk(base, followlinks=False):
            safe_dirs = []
            for name in dirs:
                candidate = Path(base_dir) / name
                if not materialization._is_reparse_or_symlink(candidate):
                    safe_dirs.append(name)
            dirs[:] = safe_dirs
            for name in files:
                candidate = Path(base_dir) / name
                relative = candidate.relative_to(root).as_posix()
                glob_matches = (
                    fnmatch.fnmatch(relative, file_glob)
                    or fnmatch.fnmatch(name, file_glob)
                    or (
                        file_glob.startswith("**/")
                        and fnmatch.fnmatch(relative, file_glob[3:])
                    )
                )
                if not glob_matches:
                    continue
                try:
                    if materialization._is_reparse_or_symlink(candidate):
                        continue
                    if candidate.stat().st_size > MAX_SEARCH_FILE_BYTES:
                        continue
                    text = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if matcher.search(line):
                        matches.append({"file": relative, "line": line_no, "text": line[:2000]})
                        if len(matches) >= maximum:
                            break
                if len(matches) >= maximum:
                    break
            if len(matches) >= maximum:
                break
        payload.update({"matches": matches, "total": len(matches), "truncated": len(matches) >= maximum})

    _record_usage(run, row, access_kind="access", operation=operation)
    return 200, sanitize_public(payload, _public_roots(row))


def execute(
    run: dict, tool_input: dict, *, remaining_sec: float,
    source_tool_calls: int = 0, snapshot_reads: int = 0,
) -> tuple[int, dict]:
    try:
        return _execute(
            run, tool_input, remaining_sec=remaining_sec,
            source_tool_calls=source_tool_calls, snapshot_reads=snapshot_reads,
        )
    except SnapshotAccessError:
        raise
    except Exception as exc:
        raise _unexpected(exc, tool_input, "execute") from exc


def _execute(
    run: dict, tool_input: dict, *, remaining_sec: float,
    source_tool_calls: int = 0, snapshot_reads: int = 0,
) -> tuple[int, dict]:
    snapshot_id = str(tool_input.get("snapshot_id") or "")
    task_kind = str(tool_input.get("task_kind") or "")
    if task_kind not in TASK_KINDS:
        raise SnapshotAccessError(422, "snapshot_task_invalid", "unsupported snapshot task kind")
    command = str(tool_input.get("command") or "").strip()
    if not command or "\x00" in command or "\r" in command or "\n" in command:
        raise SnapshotAccessError(422, "snapshot_command_invalid", "command must be one non-empty line")
    row, root, before_meta = _refresh_available(run, snapshot_id)
    timeout = max(.01, min(float(tool_input.get("timeout_seconds") or 300), 300.0, remaining_sec))
    temp_dir = root.parent / ".flowgate-tmp"
    temp_dir.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "TEMP": str(temp_dir),
        "TMP": str(temp_dir),
        "FLOWGATE_SNAPSHOT_ID": snapshot_id,
        "FLOWGATE_SNAPSHOT_SOURCE_REVISION": str(row.get("source_revision") or ""),
    }
    started = time.monotonic()
    proc = subprocess.Popen(
        command, cwd=root, shell=True, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False,
        start_new_session=(os.name != "nt"),
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process_runner.kill_process_tree(proc)
        stdout, stderr = proc.communicate(timeout=5)

    def _tail(raw: bytes) -> tuple[str, bool]:
        return raw[-MAX_READ_BYTES:].decode("utf-8", errors="replace"), len(raw) > MAX_READ_BYTES

    out, out_cut = _tail(stdout or b"")
    err, err_cut = _tail(stderr or b"")
    after, _root, after_meta = _refresh_available(run, snapshot_id)
    success = proc.returncode == 0 and not timed_out
    claim = bool(tool_input.get("claim_current_worktree"))
    detail = {
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "source_tool_calls": int(source_tool_calls),
        "snapshot_reads": int(snapshot_reads),
    }
    _record_usage(
        run, after, access_kind="execution", operation="execute",
        success=success, task_kind=task_kind, current_claim=claim, detail=detail,
    )
    _audit(
        "snapshot_execution_reported", after, run,
        task_kind=task_kind, success=success,
        stale_at_execution=bool(before_meta.get("stale")),
        stale_at_report=bool(after_meta.get("stale")),
        source_tool_calls=int(source_tool_calls),
        snapshot_read_count=int(snapshot_reads),
        command_exit_code=proc.returncode,
        timed_out=timed_out,
    )
    payload = {
        "ok": True,
        "op": "execute",
        "snapshot": after_meta,
        "task_kind": task_kind,
        "command": command,
        "exit_code": proc.returncode,
        "duration_ms": detail["duration_ms"],
        "stdout": out,
        "stderr": err,
        "truncated": out_cut or err_cut,
        "timed_out": timed_out,
        "success": success,
        "stale_at_execution": bool(before_meta.get("stale")),
        "stale_at_report": bool(after_meta.get("stale")),
        "execution_boundary": (
            "FlowGate constrains cwd, temp files, timeout, and captured output, but cannot fully "
            "inspect every child process; commands must stay inside this snapshot and must not "
            "access live source paths."
        ),
    }
    if after_meta.get("stale"):
        payload["warning"] = STALE_WARNING
        payload["validation_claim"] = STALE_EXPLANATION
    roots = _public_roots(row)
    if claim and after_meta.get("stale"):
        _audit(
            "snapshot_misuse_blocked", after, run,
            misuse_kind="stale_result_as_current_worktree_validation",
            blocked_at="run_source_snapshot",
            task_kind=task_kind,
        )
        payload["ok"] = False
        payload["claim_blocked"] = True
        payload["error"] = {
            "code": "snapshot_stale_claim_blocked",
            "message": STALE_EXPLANATION,
            "details": {"state": "stale", "warning": STALE_WARNING},
        }
        return 409, sanitize_public(payload, roots)
    return 200, sanitize_public(payload, roots)


def guard_promotion(run: dict, tool_name: str, tool_input: dict) -> None:
    if tool_name not in MUTATION_TOOL_NAMES:
        return
    raw = str(tool_input.get("path") or "").replace("\\", "/")
    match = re.search(r"(?:^|/)source-snapshots/(snap_[A-Za-z0-9_-]+)(?:/|$)", raw)
    if match is None:
        return
    snapshot_id = match.group(1)
    try:
        row = _authorize(run, snapshot_id)
        _audit(
            "snapshot_misuse_blocked", row, run,
            misuse_kind="snapshot_to_worktree_promotion",
            blocked_at=tool_name,
            supplied_path=raw[:1000],
        )
    except Exception:
        pass
    raise SnapshotAccessError(
        403, "snapshot_promotion_blocked",
        "snapshot files cannot be promoted, uploaded, committed, merged, or synced to source; "
        "make persistent edits through canonical FlowGate mutation paths using worktree-relative paths",
        state="blocked", snapshot_id=snapshot_id,
    )


_PROVENANCE_HEADING = "## Scratch Snapshot Provenance"
_NEXT_H2 = re.compile(r"(?m)^## (?!#).+$")


def provenance_for_run(run_id: str) -> list[dict]:
    if not run_id:
        return []
    try:
        usages = usage_db.list_for_run(run_id)
    except Exception:
        return []
    seen: set[str] = set()
    result: list[dict] = []
    for usage in usages:
        snapshot_id = str(usage.get("snapshot_id") or "")
        if not snapshot_id or snapshot_id in seen:
            continue
        seen.add(snapshot_id)
        row = request_db.get(snapshot_id)
        if row is None:
            continue
        if row.get("status") == "created":
            try:
                row = materialization.refresh_stale(snapshot_id, actor="tr-provenance")
            except Exception:
                row = dict(row)
                row["stale"] = True
        result.append({
            "snapshot_id": snapshot_id,
            "source_kind": row.get("source_kind") or "current_worktree",
            "source_revision": row.get("source_revision"),
            "created_at": row.get("created_at"),
            "stale_at_completion": bool(row.get("stale")),
            "status_at_completion": _public_state(row),
        })
    return result


def render_tr_provenance(rows: list[dict]) -> str:
    lines = [_PROVENANCE_HEADING, ""]
    for index, row in enumerate(rows):
        if len(rows) > 1:
            lines.extend([f"### Snapshot {index + 1}", ""])
        lines.extend([
            "- Scratch snapshot used: Yes",
            f"- Snapshot id: {row.get('snapshot_id') or ''}",
            f"- Source kind: {row.get('source_kind') or 'current_worktree'}",
            f"- Source revision: {row.get('source_revision') or ''}",
            f"- Created at: {row.get('created_at') or ''}",
            "- Stale at completion: " + ("Yes" if row.get("stale_at_completion") else "No"),
            f"- Status at completion: {row.get('status_at_completion') or ''}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def inject_tr_provenance(body: str, run_id: str) -> tuple[str, list[dict]]:
    rows = provenance_for_run(run_id)
    if not rows:
        return body, []
    start = body.find(_PROVENANCE_HEADING)
    if start >= 0:
        next_heading = _NEXT_H2.search(body, start + len(_PROVENANCE_HEADING))
        end = next_heading.start() if next_heading else len(body)
        body = body[:start].rstrip() + "\n\n" + body[end:].lstrip()
    rendered = render_tr_provenance(rows)
    changed = re.search(r"(?m)^## (?:변경 파일|Changed Files)\s*$", body)
    if changed:
        output = body[:changed.start()].rstrip() + "\n\n" + rendered + "\n" + body[changed.start():]
    else:
        output = body.rstrip() + "\n\n" + rendered
    return output, rows


def attach_tr(run_id: str, document_id: str) -> int:
    return usage_db.attach_document(run_id, document_id)