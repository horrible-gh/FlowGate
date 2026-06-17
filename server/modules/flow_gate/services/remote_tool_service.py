"""Project-control remote tool — processing pipeline (L0006).

Orchestrates the 7-step pipeline (L0006 §2) for the remote tool API defined by
P0005 (message format) and DB0007 (storage):

  ① Authentication → 401 unauthorized        (not logged: subject unidentified)
  ② Permission     → 403 forbidden            (logged as denied)
  ③ Path validation → 422 invalid_request     (logged as error)
  ④ Request validity → 422 invalid_request     (logged as error)
  ⑤ Execute operation → 404 / 409 / 413 / 503  (logged per result)
  ⑥ History logging (both success and failure, after authentication passes)
  ⑦ Completion ment (on write/remove success) → 200 response

The router is thin and delegates everything here. This module owns the P0005
common envelope, the fail-fast error ordering (L0006 §7), op-logging timing
(L0006 §5.1), and the completion-ment condition (L0006 §6).

Branch resolution note (NR0009 §8.3): the operation target is the project
*source root* `src_root(project_name, branch)`. P0005 carries no branch in the
request and DB0007's grant has no branch column, so branch is taken from
`project_settings.branch` (default 'main') — the only spec-available source,
matching file_transfer_routes._get_src_root.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import remote_tool_grants as db_grants
from modules.flow_gate.db import remote_tool_op_log as db_oplog
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import token_service
from modules.flow_gate.storage.paths import src_root
from modules.flow_gate.storage.safe_path import (
    is_safe_relative,
    resolve_in_root,
    _under_root,
)

# ── Constants ────────────────────────────────────────────────────────────────

OPS = ("read", "write", "grep", "glob", "remove")

# operation → required scope (P0005 §3.2 / L0006 §3.2 / DB0007 §5). glob shares the grep scope.
OP_SCOPE = {
    "read": "read",
    "write": "write",
    "grep": "grep",
    "glob": "grep",
    "remove": "remove",
}

# HTTP status → P0005 §6 error code.
ERROR_CODE_BY_STATUS = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    422: "invalid_request",
    503: "unavailable",
}

# HTTP status → op_log result value (DB0007 §7.3·§7.5). 200 → success, 401 not logged.
RESULT_BY_STATUS = {
    403: "denied",
    422: "error",
    404: "not_found",
    409: "conflict",
    413: "too_large",
    503: "unavailable",
}

_MAX_WRITE_BYTES = 100 * 1024 * 1024   # 100 MB — write payload guard (413)
_MAX_READ_BYTES = 100 * 1024 * 1024    # 100 MB — unbounded read guard (413)
_GREP_FILE_SKIP_BYTES = 1024 * 1024    # 1 MB — skip large files when scanning (NR0011 §2)


class _OpError(Exception):
    """Signals a fail-fast pipeline stop with an HTTP status (L0006 §7)."""

    def __init__(self, status: int):
        super().__init__(f"remote tool op error: {status}")
        self.status = status


# ── ① Authentication ───────────────────────────────────────────────────────

def _authenticate(raw_token: Optional[str]) -> Optional[dict]:
    """Return the active grant for the token, or None (→ 401).

    Reuses token_service hashing/pepper/constant-time compare (NR0009 §4.2 — no
    duplicate crypto). A grant is valid when its token_hash matches and
    status='active' and (expires_at IS NULL OR in the future) (DB0007 §4).
    """
    if not raw_token:
        return None
    try:
        _pepper_id, pepper = token_service._active_pepper()
    except RuntimeError:
        return None
    candidate_hash = token_service._hash_token(raw_token, pepper)
    grant = db_grants.get_by_token_hash(candidate_hash)
    if grant is None:
        return None
    if not token_service._verify_hash(grant["token_hash"], candidate_hash):
        return None
    if grant.get("status") != "active":
        return None
    expires_at = grant.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                return None
        except ValueError:
            return None
    return grant


# ── ③ Path safety / ④ request validity ───────────────────────────────────────

def _present_path_fields(op: str, body: dict) -> list[str]:
    """Path-bearing input values that are *present* (L0006 §4.1) — for ③.

    Missing required fields are ④'s concern, so only values that exist (the key
    is present and not None) are returned here.
    """
    out: list[str] = []
    if op in ("read", "write", "remove"):
        if body.get("path") is not None:
            out.append(body["path"])
    elif op == "grep":
        if body.get("path") is not None:
            out.append(body["path"])
        if body.get("glob") is not None:
            out.append(body["glob"])
    elif op == "glob":
        if body.get("path") is not None:
            out.append(body["path"])
        if body.get("pattern") is not None:
            out.append(body["pattern"])
    return out


def _validate_paths(op: str, body: dict) -> None:
    """③ — every present path-bearing value must be root-relative & escape-free."""
    for value in _present_path_fields(op, body):
        if not is_safe_relative(value):
            raise _OpError(422)


def _require_str(body: dict, key: str, *, allow_empty: bool = False) -> str:
    val = body.get(key)
    if not isinstance(val, str):
        raise _OpError(422)
    if not allow_empty and val == "":
        raise _OpError(422)
    return val


def _validate_required(op: str, body: dict) -> None:
    """④ — required fields present & well-typed (P0005 per-operation field table)."""
    if op == "read":
        _require_str(body, "path")
        _validate_max_int(body, "max_bytes")
    elif op == "write":
        _require_str(body, "path")
        # content is required but an empty body (empty file) is legal.
        if not isinstance(body.get("content"), str):
            raise _OpError(422)
        mode = body.get("mode")
        if mode is not None and mode not in ("create", "overwrite", "append"):
            raise _OpError(422)
        _validate_encoding(body)
    elif op == "remove":
        _require_str(body, "path")
    elif op == "grep":
        _require_str(body, "pattern")
        _validate_max_int(body, "max_results")
        if body.get("ignore_case") is not None and not isinstance(body["ignore_case"], bool):
            raise _OpError(422)
        # invalid regex → request form error (422)
        try:
            re.compile(body["pattern"])
        except re.error:
            raise _OpError(422)
    elif op == "glob":
        _require_str(body, "pattern")


def _validate_max_int(body: dict, key: str) -> None:
    val = body.get(key)
    if val is None:
        return
    if not isinstance(val, int) or isinstance(val, bool) or val < 0:
        raise _OpError(422)


def _validate_encoding(body: dict) -> None:
    enc = body.get("encoding")
    if enc is None:
        return
    if not isinstance(enc, str):
        raise _OpError(422)
    try:
        "".encode(enc)
    except LookupError:
        raise _OpError(422)


# ── Source-root resolution ────────────────────────────────────────────────────

def _resolve_src_root(grant: dict) -> Optional[Path]:
    """Resolve the project source root for the grant, or None (→ 503).

    project_name from the grant's project_id, branch from project_settings
    (NR0009 §8.3). Mirrors file_transfer_routes._get_src_root.
    """
    project_id = grant.get("project")
    row = db_projects.get_by_id(project_id)
    project_name = (row.get("project_name") or "").strip() if row else ""
    if not project_name:
        return None
    settings = db_projects.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"
    return src_root(project_name, branch).resolve()


# ── ⑤ Execution ───────────────────────────────────────────────────────────────

def _execute(op: str, body: dict, root: Path) -> tuple[dict, Optional[int]]:
    """Run the operation. Returns (response_extra_fields, bytes_processed)."""
    if op == "read":
        return _exec_read(body, root)
    if op == "write":
        return _exec_write(body, root)
    if op == "remove":
        return _exec_remove(body, root)
    if op == "grep":
        return _exec_grep(body, root)
    if op == "glob":
        return _exec_glob(body, root)
    raise _OpError(422)  # unreachable (op validated earlier)


def _exec_read(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    target = resolve_in_root(root, body["path"])
    if target is None:
        raise _OpError(422)  # symlink/realpath escape (③ category)
    if not target.is_file():
        raise _OpError(404)
    size = target.stat().st_size
    max_bytes = body.get("max_bytes")
    encoding = body.get("encoding") or "utf-8"
    if max_bytes is None and size > _MAX_READ_BYTES:
        raise _OpError(413)
    truncated = False
    with open(target, "rb") as fh:
        if max_bytes is not None and size > max_bytes:
            raw = fh.read(max_bytes)
            truncated = True
        else:
            raw = fh.read()
    content = raw.decode(encoding, errors="replace")
    return (
        {
            "path": body["path"],
            "content": content,
            "encoding": encoding,
            "size": size,
            "truncated": truncated,
        },
        size,
    )


def _exec_write(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    target = resolve_in_root(root, body["path"])
    if target is None:
        raise _OpError(422)
    mode = body.get("mode") or "overwrite"
    encoding = body.get("encoding") or "utf-8"
    try:
        data = body["content"].encode(encoding, errors="strict")
    except UnicodeError:
        # content cannot be represented in the requested narrow encoding (e.g. ascii) → request validity error (④).
        # Surface as 422 to preserve the P0005 envelope + ⑥ history contract (prevents a bare 500 leak).
        raise _OpError(422)
    if len(data) > _MAX_WRITE_BYTES:
        raise _OpError(413)

    existed = target.exists()
    if mode == "create" and existed:
        raise _OpError(409)

    target.parent.mkdir(parents=True, exist_ok=True)
    file_mode = "ab" if mode == "append" else "wb"
    with open(target, file_mode) as fh:
        fh.write(data)

    created = not existed
    return (
        {
            "path": body["path"],
            "bytes_written": len(data),
            "created": created,
        },
        len(data),
    )


def _exec_remove(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    target = resolve_in_root(root, body["path"])
    if target is None:
        raise _OpError(422)
    if not target.is_file():
        raise _OpError(404)  # missing, or not a regular file (single-file removal, P0005 §4.4)
    os.remove(target)
    return ({"path": body["path"], "removed": True}, None)


def _iter_files(root: Path, base: Path, glob_filter: Optional[str]):
    """Yield (posix_relpath_from_root, abspath) for files under base, jailed to root."""
    root_real = os.path.realpath(str(root))
    candidates = base.glob(glob_filter) if glob_filter else base.rglob("*")
    for cand in candidates:
        real = os.path.realpath(str(cand))
        if not _under_root(real, root_real):
            continue  # symlink escape — skip
        if not os.path.isfile(real):
            continue
        rel = os.path.relpath(real, root_real).replace("\\", "/")
        yield rel, real


def _exec_grep(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    base = resolve_in_root(root, body.get("path") or "")
    if base is None:
        raise _OpError(422)
    flags = re.IGNORECASE if body.get("ignore_case") else 0
    pattern = re.compile(body["pattern"], flags)
    max_results = body.get("max_results")
    glob_filter = body.get("glob")

    matches: list[dict] = []
    total = 0
    truncated = False
    # Note: iteration does not stop after max_results is reached. This is intentional
    # so that `total` reflects the exact full match count; even when only one result is
    # requested, the entire source root is scanned (after skipping large/binary files) —
    # which costs accordingly on large roots.
    for rel, real in _iter_files(root, base, glob_filter):
        try:
            if os.path.getsize(real) > _GREP_FILE_SKIP_BYTES:
                continue
            with open(real, "rb") as fh:
                blob = fh.read()
            if b"\x00" in blob:
                continue  # binary — skip
            text = blob.decode("utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                total += 1
                if max_results is not None and len(matches) >= max_results:
                    truncated = True
                else:
                    matches.append({"file": rel, "line": lineno, "text": line})
    return ({"matches": matches, "total": total, "truncated": truncated}, None)


def _exec_glob(body: dict, root: Path) -> tuple[dict, Optional[int]]:
    base = resolve_in_root(root, body.get("path") or "")
    if base is None:
        raise _OpError(422)
    root_real = os.path.realpath(str(root))
    paths: list[str] = []
    for cand in sorted(base.glob(body["pattern"])):
        real = os.path.realpath(str(cand))
        if not _under_root(real, root_real):
            continue
        rel = os.path.relpath(real, root_real).replace("\\", "/")
        paths.append(rel)
    return ({"paths": paths, "total": len(paths)}, None)


# ── Envelope / logging / continuation ─────────────────────────────────────────

def _envelope(
    ok: bool,
    op: str,
    *,
    extra: Optional[dict] = None,
    error: Optional[dict] = None,
    continuation: Optional[dict] = None,
) -> dict:
    """P0005 §3.3 common response envelope."""
    env: dict = {"ok": ok, "op": op, "server_ts": now_iso()}
    if error is not None:
        env["error"] = error
    if continuation is not None:
        env["continuation"] = continuation
    if extra:
        env.update(extra)
    return env


_ERROR_MESSAGES = {
    401: "토큰이 없거나 유효하지 않습니다.",
    403: "해당 작업을 수행할 권한이 없습니다.",
    404: "대상 경로를 찾을 수 없습니다.",
    409: "대상 파일이 이미 존재합니다 (mode=create).",
    413: "요청 또는 대상 크기가 허용치를 초과했습니다.",
    422: "요청 형식이 올바르지 않습니다.",
    503: "서버가 일시적으로 요청을 처리할 수 없습니다.",
}


def _fail_envelope(op: str, status: int) -> dict:
    code = ERROR_CODE_BY_STATUS[status]
    return _envelope(
        False,
        op,
        error={"code": code, "message": _ERROR_MESSAGES.get(status, code)},
    )


def _log_targets(op: str, body: dict) -> tuple[Optional[str], Optional[str]]:
    """(target_path, target_pattern) for op_log (DB0007 §6)."""
    path = body.get("path") if isinstance(body.get("path"), str) else None
    if op == "grep":
        pattern = body.get("glob") if isinstance(body.get("glob"), str) else None
    elif op == "glob":
        pattern = body.get("pattern") if isinstance(body.get("pattern"), str) else None
    else:
        pattern = None
    return path, pattern


def _log(
    grant: dict,
    op: str,
    target_path: Optional[str],
    target_pattern: Optional[str],
    *,
    status: int,
    bytes_processed: Optional[int] = None,
) -> None:
    """⑥ — record one op_log row (every attempt after authentication passes). Never raises."""
    if status == 200:
        result, error_code = "success", None
    else:
        result = RESULT_BY_STATUS[status]
        error_code = ERROR_CODE_BY_STATUS[status]
    try:
        db_oplog.insert(
            grant_id=grant["grant_id"],
            op=op,
            result=result,
            error_code=error_code,
            target_path=target_path,
            target_pattern=target_pattern,
            bytes_processed=bytes_processed,
        )
    except Exception:
        # A history-write failure must not block the user response (best-effort, L0006 §5).
        pass


def _continuation(grant: dict) -> dict:
    """⑦ — completion ment for state-changing success (P0005 §5 / L0006 §6.2)."""
    report_doc_id = grant.get("report_doc_id")
    if report_doc_id:
        ment = (
            f"작업을 완료했습니다. 변경 내용을 레포트({report_doc_id})에 이어 "
            "정리해 제출을 계속해 주세요."
        )
    else:
        ment = "작업을 완료했습니다. 변경 내용을 작업 레포트(TR)로 정리해 제출을 이어가 주세요."
    return {"ment": ment, "report_doc_id": report_doc_id, "next_action": "write_report"}


# ── Pipeline entry ─────────────────────────────────────────────────────────────

def handle(operation: str, raw_token: Optional[str], body: Optional[dict]) -> tuple[int, dict]:
    """Run the full pipeline. Returns (http_status, response_envelope).

    Fail-fast order (L0006 §7): 401 ▶ 403 ▶ 422 ▶ 404 ▶ 409 ▶ 413 ▶ 503.
    """
    op = operation
    body = body if isinstance(body, dict) else {}

    # ① Authentication — on failure the subject is unidentified, so no history is logged (L0006 §3.1).
    grant = _authenticate(raw_token)
    if grant is None:
        return 401, _fail_envelope(op, 401)

    # Unknown operation name: no scope mapping + cannot be stored in the op enum, so not logged → 422.
    if op not in OP_SCOPE:
        return 422, _fail_envelope(op, 422)

    log_path, log_pattern = _log_targets(op, body)
    try:
        # ② Permission scope
        if OP_SCOPE[op] not in db_grants.get_scopes(grant["grant_id"]):
            raise _OpError(403)
        # ③ Path safety (existing path-like values)
        _validate_paths(op, body)
        # ④ Request validity (required fields)
        _validate_required(op, body)
        # Resolve the target source root
        root = _resolve_src_root(grant)
        if root is None:
            raise _OpError(503)
        # ⑤ Execute the operation
        extra, nbytes = _execute(op, body, root)
    except _OpError as exc:
        _log(grant, op, log_path, log_pattern, status=exc.status)
        return exc.status, _fail_envelope(op, exc.status)
    except OSError:
        # Unexpected I/O failure → 503 unavailable (logged to history).
        _log(grant, op, log_path, log_pattern, status=503)
        return 503, _fail_envelope(op, 503)
    except Exception:
        # Trap every other unexpected exception in the envelope so it cannot leak to the
        # router as a bare 500 — since the attempt passed authentication, ⑥ history is also
        # recorded ('every response = P0005 envelope' contract).
        _log(grant, op, log_path, log_pattern, status=503)
        return 503, _fail_envelope(op, 503)

    # ⑥ Success history
    _log(grant, op, log_path, log_pattern, status=200, bytes_processed=nbytes)
    # ⑦ Completion ment — only on successful state-changing operations (write/remove) (L0006 §6.1).
    continuation = _continuation(grant) if op in ("write", "remove") else None
    return 200, _envelope(True, op, extra=extra, continuation=continuation)
