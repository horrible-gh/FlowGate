"""Transport independent Source Bundle read and disposable execution boundary."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from modules.flow_gate.db import source_bundles as db
from modules.flow_gate.services import process_runner
from modules.flow_gate.services import source_bundle_materializer as materializer
from modules.flow_gate.services import source_bundle_service as bundles
from modules.flow_gate.services.snapshot_materialization_service import locator_roots, redact_locators, redact_error_text

TASK_KINDS = frozenset({"build", "test", "lint", "typecheck", "dependency_analysis", "static_analysis", "temporary_experiment"})
READ_OPS = frozenset({"status", "read", "search", "glob", "stat"})
MAX_READ = 1024 * 1024
MAX_RESULTS = 500
_SCRATCH_LOCK = threading.Lock()


class BundleAccessError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)

    def payload(self, operation: str) -> dict:
        return {"ok": False, "op": operation, "error": {"code": self.code, "message": self.message}}


def _axis(run: dict, *names: str) -> str:
    for name in names:
        if run.get(name):
            return str(run[name])
    return ""


def _row(run: dict, bundle_id: str) -> dict:
    row = db.get(bundle_id)
    if row is None:
        raise BundleAccessError(404, "bundle_not_found", "Source Bundle does not exist")
    if row["project_id"] != _axis(run, "project_id", "project") or row["group_id"] != _axis(run, "group_id", "group"):
        raise BundleAccessError(403, "bundle_forbidden", "Source Bundle belongs to another project or group")
    if row["status"] == "deleted":
        raise BundleAccessError(410, "bundle_deleted", "Source Bundle was deleted")
    if row["status"] == "failed":
        raise BundleAccessError(409, "bundle_failed", "Source Bundle build failed")
    if row["status"] != "created":
        raise BundleAccessError(409, "bundle_not_ready", "Source Bundle is not ready")
    if not bundles._integrity(row):
        raise BundleAccessError(409, "bundle_integrity_failed", "Source Bundle integrity check failed")
    return row


def _resolve(run: dict, bundle_id: str | None) -> dict:
    if bundle_id:
        return _row(run, str(bundle_id))
    try:
        info = bundles.ensure(_axis(run, "project_id", "project"), _axis(run, "group_id", "group"))
    except materializer.SourceBundleError as exc:
        raise BundleAccessError(409, exc.code, exc.message) from exc
    row = _row(run, info["bundle_id"])
    row["_reused"] = bool(info.get("reused"))
    row["_fingerprint_duration_ms"] = info.get("metrics", {}).get("bundle_fingerprint_duration_ms", 0)
    return row


def _relative(raw: object, *, empty: bool = False) -> str:
    value = str(raw or "").replace("\\", "/")
    if empty and not value:
        return ""
    if (not value or value.startswith("/") or value.startswith("//") or
            PureWindowsPath(value).drive or any(p in ("", ".", "..") or ":" in p for p in value.split("/")) or
            any(ord(c) < 32 for c in value)):
        raise BundleAccessError(422, "bundle_path_invalid", "path must be relative to the Bundle")
    return value


def _member(root: Path, raw: object) -> Path:
    relative = _relative(raw)
    path = root
    for part in PurePosixPath(relative).parts:
        path = path / part
        try:
            st = path.lstat()
        except OSError as exc:
            raise BundleAccessError(404, "bundle_path_not_found", "Bundle path does not exist") from exc
        if materializer._linked(st):
            raise BundleAccessError(403, "bundle_path_escape", "linked Bundle path is forbidden")
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise BundleAccessError(403, "bundle_path_escape", "Bundle path escaped its source root") from exc
    return path


def _roots(run: dict, row: dict, scratch: Path | None = None) -> tuple:
    bundle = materializer.bundle_path(row["project_id"], row["bundle_id"])
    values = [(str(bundle), "<SOURCE_BUNDLE>"), (str(bundle / "source"), "<SOURCE_BUNDLE>")]
    if scratch:
        values.append((str(scratch), "<AI_SCRATCH>"))
    if run.get("source_root"):
        values.append((str(run["source_root"]), "<LIVE_WORKTREE>"))
    values.extend(locator_roots({"project_id": row["project_id"], "group_id": row["group_id"],
                                 "token_id": _axis(run, "token_id", "current_token_id")}))
    return tuple(values)


def _sanitize(value, roots, *, error=False):
    if isinstance(value, str):
        return redact_error_text(value, roots) if error else redact_locators(value, roots)
    if isinstance(value, dict):
        return {key: _sanitize(item, roots, error=error) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, roots, error=error) for item in value]
    return value


def _fresh(row: dict) -> bool:
    try:
        root = materializer.resolve_worktree(row["project_id"], row["group_id"])
        state = materializer.inspect_source(root, time.monotonic() + materializer.BUILD_SECONDS)
        row["_freshness_bytes_hashed"] = state.get("bytes_hashed", 0)
        return (state["source_revision"] == row["source_revision"] and
                bool(state["source_dirty"]) == bool(row["source_dirty"]) and
                state["content_fingerprint"] == row["content_fingerprint"])
    except Exception:
        return False


def _metadata(row: dict, current: bool | None = None) -> dict:
    result = bundles._public(row, reused=row.get("_reused", False),
                             fingerprint_ms=row.get("_fingerprint_duration_ms", 0))
    result["historical_result"] = True
    result["current_worktree_claim"] = current
    if current is False:
        result["warning"] = "ACTIVE SOURCE BUNDLE IS STALE"
    return result


def _usage(run, row, operation, success=True, **detail):
    try:
        db.record_usage(row["bundle_id"], operation, run_id=_axis(run, "run_id", "ai_run_id"),
                        token_id=_axis(run, "token_id", "current_token_id"),
                        success=success, current_worktree_claim=bool(detail.get("claim_current_worktree")),
                        detail=detail)
    except Exception as exc:
        raise BundleAccessError(500, "bundle_usage_failed", "Source Bundle usage could not be recorded") from exc


def access(run: dict, tool_input: dict) -> tuple[int, dict]:
    operation = str(tool_input.get("operation") or "status")
    if operation not in READ_OPS:
        raise BundleAccessError(422, "bundle_operation_invalid", "unsupported Bundle operation")
    row = _resolve(run, tool_input.get("bundle_id"))
    root = materializer.bundle_path(row["project_id"], row["bundle_id"]) / "source"
    claim = bool(tool_input.get("claim_current_worktree"))
    freshness_started = time.monotonic() if claim else None
    current = _fresh(row) if claim else None
    freshness_ms = int((time.monotonic() - freshness_started) * 1000) if claim else 0
    payload = {"ok": True, "op": operation, "bundle": _metadata(row, current),
               "freshness_check_duration_ms": freshness_ms,
               "freshness_bytes_hashed": row.get("_freshness_bytes_hashed", 0) if claim else 0}
    if operation == "read":
        target = _member(root, tool_input.get("path"))
        if not target.is_file():
            raise BundleAccessError(422, "bundle_path_not_file", "read requires a file")
        size = target.stat().st_size
        offset = max(0, int(tool_input.get("offset") or 0))
        limit = min(MAX_READ, max(0, int(tool_input.get("length", tool_input.get("max_bytes", MAX_READ)) or 0)))
        with target.open("rb") as stream:
            stream.seek(offset)
            raw = stream.read(limit)
        payload.update(path=target.relative_to(root).as_posix(), content=raw.decode(str(tool_input.get("encoding") or "utf-8"), errors="replace"),
                       size=size, offset=offset, returned_bytes=len(raw), truncated=offset + len(raw) < size)
    elif operation == "stat":
        target = _member(root, tool_input.get("path"))
        payload.update(path=target.relative_to(root).as_posix(), kind="directory" if target.is_dir() else "file",
                       size=target.stat().st_size)
    elif operation == "glob":
        pattern = _relative(tool_input.get("pattern"))
        paths = []
        for candidate in root.glob(pattern):
            try:
                path = _member(root, candidate.relative_to(root).as_posix())
                paths.append(path.relative_to(root).as_posix())
            except BundleAccessError:
                continue
            if len(paths) >= MAX_RESULTS:
                break
        payload.update(paths=sorted(set(paths)), truncated=len(paths) >= MAX_RESULTS)
    elif operation == "search":
        try:
            matcher = re.compile(str(tool_input.get("pattern") or ""), re.I if tool_input.get("ignore_case") else 0)
        except re.error as exc:
            raise BundleAccessError(422, "bundle_pattern_invalid", "invalid search pattern") from exc
        if not matcher.pattern:
            raise BundleAccessError(422, "bundle_pattern_required", "search requires a pattern")
        base = root if not tool_input.get("path") else _member(root, tool_input["path"])
        if not base.is_dir():
            raise BundleAccessError(422, "bundle_path_not_directory", "search path must be a directory")
        matches = []
        maximum = min(MAX_RESULTS, max(1, int(tool_input.get("max_results") or 100)))
        pattern = str(tool_input.get("glob") or "*")
        for directory, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [d for d in dirs if not materializer._linked((Path(directory) / d).lstat())]
            for name in files:
                path = Path(directory) / name
                rel = path.relative_to(root).as_posix()
                if not (fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern) or
                        (pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:]))):
                    continue
                if materializer._linked(path.lstat()) or path.stat().st_size > 2 * MAX_READ:
                    continue
                for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if matcher.search(line):
                        matches.append({"file": rel, "line": line_no, "text": line[:2000]})
                        if len(matches) >= maximum:
                            break
                if len(matches) >= maximum:
                    break
            if len(matches) >= maximum:
                break
        payload.update(matches=matches, total=len(matches), truncated=len(matches) >= maximum)
    _usage(run, row, operation, access_kind="bundle_read", claim_current_worktree=claim,
           freshness=current, bundle_reused=row.get("_reused", False),
           freshness_check_duration_ms=freshness_ms,
           freshness_bytes_hashed=row.get("_freshness_bytes_hashed", 0) if claim else 0)
    if claim and not current:
        payload["ok"] = False
        payload["error"] = {"code": "bundle_stale_claim_blocked", "message": "historical Bundle result cannot validate the current worktree"}
        return 409, _sanitize(payload, _roots(run, row))
    return 200, _sanitize(payload, _roots(run, row))


def _scratch(run: dict, row: dict) -> tuple[Path, bool]:
    run_id = _axis(run, "run_id", "ai_run_id")
    if not run_id:
        raise BundleAccessError(409, "scratch_run_missing", "AI run identity is required")
    key = hashlib.sha256((run_id + "\0" + row["bundle_id"]).encode()).hexdigest()
    started = time.monotonic()
    parent = materializer.bundle_path(row["project_id"], row["bundle_id"]).parent / "scratch"
    target = parent / key
    marker = target / ".flowgate-bundle-scratch.json"
    parent.mkdir(parents=True, exist_ok=True)
    lock = parent / ("." + key + ".lock")
    deadline = time.monotonic() + 30
    with _SCRATCH_LOCK:
        while True:
            try:
                lock.mkdir()
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise BundleAccessError(409, "scratch_build_timeout", "AI Scratch creation did not complete")
                time.sleep(.05)
        try:
            if target.exists():
                try:
                    data = json.loads(marker.read_text(encoding="utf-8"))
                    if data == {"run_id": run_id, "bundle_id": row["bundle_id"]} and (target / "source").is_dir():
                        try:
                            if not db.scratch_reused(key):
                                db.scratch_created(key, row["bundle_id"], run_id,
                                                   _axis(run, "token_id", "current_token_id"),
                                                   int(row.get("byte_size") or 0), 0,
                                                   (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat())
                        except Exception as exc:
                            raise BundleAccessError(500, "scratch_record_failed",
                                                    "AI Scratch ownership could not be recorded") from exc
                        return target, True
                except (OSError, ValueError):
                    pass
                raise BundleAccessError(409, "scratch_integrity_failed", "existing AI Scratch is invalid")
            stage = parent / ("." + key + "." + str(os.getpid()))
            try:
                if stage.exists():
                    raise BundleAccessError(409, "scratch_build_conflict", "AI Scratch creation is already in progress")
                stage.mkdir()
                shutil.copytree(materializer.bundle_path(row["project_id"], row["bundle_id"]) / "source", stage / "source")
                for copied in (stage / "source").rglob("*"):
                    copied.chmod(copied.stat().st_mode | (0o700 if copied.is_dir() else 0o600))
                (stage / ".flowgate-tmp").mkdir()
                (stage / ".flowgate-cache").mkdir()
                (stage / ".flowgate-bundle-scratch.json").write_text(
                    json.dumps({"run_id": run_id, "bundle_id": row["bundle_id"]}), encoding="utf-8")
                stage.rename(target)
                try:
                    db.scratch_created(key, row["bundle_id"], run_id,
                                       _axis(run, "token_id", "current_token_id"),
                                       int(row.get("byte_size") or 0),
                                       int((time.monotonic() - started) * 1000),
                                       (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat())
                except Exception as exc:
                    shutil.rmtree(target, ignore_errors=True)
                    raise BundleAccessError(500, "scratch_record_failed", "AI Scratch ownership could not be recorded") from exc
            except BundleAccessError:
                raise
            except Exception as exc:
                raise BundleAccessError(409, "scratch_create_failed", "AI Scratch creation failed") from exc
            finally:
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
            return target, False
        finally:
            lock.rmdir()


def guard_self_check_canonical(run: dict) -> None:
    """0652 T0002: a TR edit run verifies only through run_self_check, never a Bundle/Scratch."""
    from modules.flow_gate.services import tr_self_check_service
    if tr_self_check_service.is_canonical_run(run):
        raise BundleAccessError(409, "self_check_required",
                                "TR edit runs execute tests only through run_self_check (legacy_test_execution_disabled_for_tr)")


def execute(run: dict, tool_input: dict, remaining_sec: float) -> tuple[int, dict]:
    guard_self_check_canonical(run)
    task_kind = str(tool_input.get("task_kind") or "")
    if task_kind not in TASK_KINDS:
        raise BundleAccessError(422, "bundle_task_invalid", "unsupported Bundle task kind")
    command = str(tool_input.get("command") or "").strip()
    if not command or any(c in command for c in "\r\n\0"):
        raise BundleAccessError(422, "bundle_command_invalid", "command must be one non-empty line")
    row = _resolve(run, tool_input.get("bundle_id"))
    scratch_started = time.monotonic()
    scratch, reused = _scratch(run, row)
    scratch_build_ms = 0 if reused else int((time.monotonic() - scratch_started) * 1000)
    timeout = max(.01, min(300.0, float(tool_input.get("timeout_seconds") or 300), remaining_sec))
    env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
           "TEMP": str(scratch / ".flowgate-tmp"), "TMP": str(scratch / ".flowgate-tmp"),
           "TMPDIR": str(scratch / ".flowgate-tmp"), "XDG_CACHE_HOME": str(scratch / ".flowgate-cache"),
           "FLOWGATE_SOURCE_BUNDLE_ID": row["bundle_id"]}
    started = time.monotonic()
    try:
        proc = subprocess.Popen(command, cwd=scratch / "source", shell=True, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False,
                                start_new_session=os.name != "nt")
        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process_runner.kill_process_tree(proc)
            stdout, stderr = proc.communicate(timeout=5)
    except Exception as exc:
        _usage(run, row, "execute", success=False, access_kind="scratch_execution",
               task_kind=task_kind, scratch_key=hashlib.sha256(str(scratch).encode()).hexdigest()[:16],
               failure="execution_failed")
        raise BundleAccessError(409, "scratch_execution_failed", "AI Scratch execution failed") from exc
    out = (stdout or b"")[-MAX_READ:].decode("utf-8", errors="replace")
    err = (stderr or b"")[-MAX_READ:].decode("utf-8", errors="replace")
    freshness_started = time.monotonic() if tool_input.get("claim_current_worktree") else None
    current_after = _fresh(row) if freshness_started is not None else None
    freshness_ms = int((time.monotonic() - freshness_started) * 1000) if freshness_started is not None else 0
    success = proc.returncode == 0 and not timed_out
    detail = {"access_kind": "scratch_execution", "task_kind": task_kind, "scratch_key": hashlib.sha256(str(scratch).encode()).hexdigest()[:16],
              "bundle_reused": row.get("_reused", False),
              "scratch_reused": reused, "scratch_build_duration_ms": scratch_build_ms,
              "scratch_byte_size": int(row.get("byte_size") or 0),
              "freshness_check_duration_ms": freshness_ms,
              "freshness_bytes_hashed": row.get("_freshness_bytes_hashed", 0) if freshness_started is not None else 0,
              "exit_code": proc.returncode, "timed_out": timed_out,
              "freshness_after": current_after,
              "claim_current_worktree": bool(tool_input.get("claim_current_worktree"))}
    _usage(run, row, "execute", success=success, **detail)
    payload = {"ok": True, "op": "execute", "bundle": _metadata(row, current_after),
               "task_kind": task_kind, "scratch_reused": reused,
               "scratch_build_duration_ms": scratch_build_ms,
               "scratch_byte_size": int(row.get("byte_size") or 0),
               "freshness_check_duration_ms": freshness_ms,
               "freshness_bytes_hashed": row.get("_freshness_bytes_hashed", 0) if freshness_started is not None else 0,
               "exit_code": proc.returncode,
               "stdout": out, "stderr": err, "timed_out": timed_out, "success": success,
               "truncated": len(stdout or b"") > MAX_READ or len(stderr or b"") > MAX_READ,
               "duration_ms": int((time.monotonic() - started) * 1000),
               "historical_result": True, "current_worktree_validation_allowed": current_after is True}
    if tool_input.get("claim_current_worktree") and not current_after:
        payload.update(ok=False, claim_blocked=True,
                       error={"code": "bundle_stale_claim_blocked", "message": "historical Bundle result cannot validate the current worktree"})
        return 409, _sanitize(payload, _roots(run, row, scratch))
    return 200, _sanitize(payload, _roots(run, row, scratch))


def guard_promotion(run: dict, tool_name: str, tool_input: dict) -> None:
    if tool_name not in {"write_source_file", "patch_source_file", "remove_source_file"}:
        return
    raw = str(tool_input.get("path") or "").replace("\\", "/")
    if re.search(r"(?:^|/)(?:source-bundles|scratch)/(?:sb_[0-9a-f]{32}|[0-9a-f]{64})(?:/|$)", raw):
        raise BundleAccessError(403, "bundle_promotion_blocked", "Bundle and AI Scratch cannot be promoted to worktree")


def provenance_for_run(run_id: str) -> list[dict]:
    result = []
    seen = set()
    for usage in db.list_usage_for_run(run_id):
        bundle_id = usage["bundle_id"]
        if bundle_id in seen:
            continue
        seen.add(bundle_id)
        row = db.get(bundle_id)
        if row is None:
            continue
        result.append({"bundle_id": bundle_id, "source_revision": row["source_revision"],
                       "content_fingerprint": row["content_fingerprint"],
                       "bundle_sha256": row["bundle_sha256"], "status": row["status"],
                       "created_at": row["created_at"],
                       "current_worktree_at_completion": _fresh(row) if row["status"] == "created" else False})
    return result


def inject_tr_provenance(body: str, run_id: str) -> tuple[str, list[dict]]:
    rows = provenance_for_run(run_id) if run_id else []
    if not rows:
        return body, []
    heading = "## Source Bundle Provenance"
    start = body.find(heading)
    if start >= 0:
        next_heading = re.search(r"(?m)^## (?!#).+$", body[start + len(heading):])
        end = start + len(heading) + next_heading.start() if next_heading else len(body)
        body = body[:start].rstrip() + "\n\n" + body[end:].lstrip()
    lines = [heading, ""]
    for row in rows:
        lines.extend([f"- Bundle id: {row['bundle_id']}",
                      f"  - Source revision: {row['source_revision']}",
                      f"  - Content fingerprint: {row['content_fingerprint']}",
                      f"  - Bundle SHA-256: {row['bundle_sha256']}",
                      f"  - Created at: {row['created_at']}",
                      f"  - Status at completion: {row['status']}",
                      "  - Current worktree at completion: " + ("Yes" if row["current_worktree_at_completion"] else "No")])
    section = "\n".join(lines) + "\n\n"
    changed = re.search(r"(?m)^## (?:변경 파일|Changed Files)\s*$", body)
    return (body[:changed.start()].rstrip() + "\n\n" + section + body[changed.start():], rows) if changed else (body.rstrip() + "\n\n" + section, rows)


def attach_tr(run_id: str, document_id: str) -> int:
    return db.attach_document(run_id, document_id)
