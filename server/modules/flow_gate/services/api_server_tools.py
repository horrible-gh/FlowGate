"""Server-mediated tools exposed to API providers.

All authority and filesystem context comes from the live run/token.  Model input
contains only operation arguments.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.services import git_service, process_runner, remote_tool_service, test_command_service, tool_registry

DOCUMENT_SCOPES = frozenset({"new", "edit", "review", "test_run"})

BASE_NAMES = ("read_document", "create_question", "register_document")
SOURCE_NAMES = ("read_source_file", "search_source", "glob_source", "stat_source", "diff_source", "log_source", "patch_source_file", "write_source_file", "remove_source_file", "run_test")
# Provider names are stable aliases; every source operation dispatches through the HTTP remote service.
SOURCE_OPS = {
    "read_source_file": "read", "search_source": "grep", "glob_source": "glob",
    "stat_source": "stat", "diff_source": "diff", "log_source": "log",
    "patch_source_file": "patch", "write_source_file": "write", "remove_source_file": "remove",
}


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


RANGE_SCHEMA = _obj({
    "start": {"type": "integer", "minimum": 0},
    "end": {"type": "integer", "minimum": 0},
}, ["start", "end"])
RANGE_SCHEMA["x-flowgate-range"] = True
LINE_RANGE_SCHEMA = _obj({
    "start": {"type": "integer", "minimum": 1},
    "end": {"type": "integer", "minimum": 1},
}, ["start", "end"])
LINE_RANGE_SCHEMA["x-flowgate-range"] = True
READ_DOCUMENT_SCHEMA = {
    "oneOf": [
        _obj({}),
        _obj({"section": {"type": "string"}}, ["section"]),
        _obj({"section_id": {"type": "string"}}, ["section_id"]),
        _obj({"lines": LINE_RANGE_SCHEMA}, ["lines"]),
        _obj({"chars": RANGE_SCHEMA}, ["chars"]),
    ],
}

SCHEMAS = {
    "read_source_file": _obj({"path": {"type": "string", "minLength": 1}, "max_bytes": {"type": "integer", "minimum": 0}, "offset": {"type": "integer", "minimum": 0}, "length": {"type": "integer", "minimum": 0}, "encoding": {"type": "string"}, "ref": {"type": "string"}}, ["path"]),
    "search_source": _obj({"pattern": {"type": "string", "minLength": 1}, "path": {"type": "string"}, "glob": {"type": "string"}, "ignore_case": {"type": "boolean"}, "max_results": {"type": "integer", "minimum": 0}, "ref": {"type": "string"}}, ["pattern"]),
    "glob_source": _obj({"pattern": {"type": "string", "minLength": 1}, "path": {"type": "string"}, "ref": {"type": "string"}}, ["pattern"]),
    "stat_source": _obj({"path": {"type": "string", "minLength": 1}, "ref": {"type": "string"}}, ["path"]),
    "diff_source": _obj({"path": {"type": "string"}, "target_ref": {"type": "string", "minLength": 1}}),
    "log_source": _obj({"path": {"type": "string"}, "target_ref": {"type": "string", "minLength": 1}, "max_count": {"type": "integer", "minimum": 1}}),
    "patch_source_file": _obj({"path": {"type": "string", "minLength": 1}, "old_string": {"type": "string", "minLength": 1}, "new_string": {"type": "string"}, "replace_all": {"type": "boolean"}, "encoding": {"type": "string"}}, ["path", "old_string", "new_string"]),
    "write_source_file": _obj({"path": {"type": "string", "minLength": 1}, "content": {"type": "string"}, "mode": {"type": "string", "enum": ["create", "overwrite", "append"]}, "encoding": {"type": "string"}}, ["path", "content"]),
    "remove_source_file": _obj({"path": {"type": "string", "minLength": 1}, "recursive": {"type": "boolean"}}, ["path"]),
    "run_test": _obj({"command": {"type": "string", "minLength": 1}}, ["command"]),
    "read_document": READ_DOCUMENT_SCHEMA,
    "create_question": _obj({"questions": {"type": "array", "minItems": 1, "items": _obj({"title": {"type": "string"}, "body": {"type": "string", "minLength": 1}, "options": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 200}, "maxItems": 10}}, ["body"])}}, ["questions"]),
}

REGISTER_SCHEMAS = {
    "new": _obj({"title": {"type": "string"}, "content": {"type": "string", "minLength": 1}, "doc_type": {"type": "string"}}, ["content", "doc_type"]),
    "edit": _obj({"content": {"type": "string", "minLength": 1}, "edit_reason": {"type": "string", "enum": ["rejected", "qna_followup", "user_comment", "worker_self"]}, "rejection_response": {"type": "string"}, "rejection_id": {"type": "string"}, "rejection_review_id": {"type": "integer"}}, ["content", "edit_reason"]),
    "review": _obj({"verdict": {"type": "string", "enum": ["pass", "issues", "hold"]}, "findings": {"type": "array", "items": _obj({"locus": {"type": "string"}, "note": {"type": "string"}})}, "comment": {"type": "string"}}, ["verdict"]),
    "test_run": _obj({}),
}

DESCRIPTIONS = {name: name.replace("_", " ") for name in (*BASE_NAMES, *SOURCE_NAMES)}


def ready() -> bool:
    """Static readiness: registry, strict binder and allowlisted runner are all present."""
    return set(SOURCE_NAMES) <= set(SCHEMAS) and callable(require_group_root) and callable(run_test)


class ToolError(Exception):
    def __init__(self, status: int, reason: str, message: str | None = None):
        self.status, self.reason, self.message = status, reason, message or reason
        super().__init__(self.message)


def _step_type(run: dict) -> str | None:
    doc = db_documents.get_by_id(run.get("doc_ref"))
    return str(doc.get("type_code") or doc.get("type") or "").upper() if doc else None


def definitions_for_run(run: dict) -> list[dict]:
    scope = run.get("action_scope")
    if scope not in DOCUMENT_SCOPES:
        raise ToolError(422, "invalid_action_scope")
    step_type = _step_type(run)
    if not step_type:
        raise ToolError(409, "toolset_unavailable")
    names = list(BASE_NAMES)
    kind, _reason = tool_registry.kind_for_step(scope, step_type)
    allowed_ops = set(tool_registry.tool_names(kind))
    names += [name for name, op in SOURCE_OPS.items() if op in allowed_ops]
    if kind == "read_write":
        names.append("run_test")
    result = []
    for name in names:
        schema = REGISTER_SCHEMAS[scope] if name == "register_document" else SCHEMAS[name]
        result.append({"name": name, "description": DESCRIPTIONS[name], "schema": schema, "completion": name == "register_document"})
    return result


def validate(schema: dict, value: Any, path: str = "input") -> None:
    alternatives = schema.get("oneOf")
    if alternatives is not None:
        valid_count = 0
        for alternative in alternatives:
            try:
                validate(alternative, value, path)
                valid_count += 1
            except ToolError:
                pass
        if valid_count != 1:
            raise ToolError(422, "schema_validation_failed", f"{path} has invalid selector or range")
        return
    typ = schema.get("type")
    valid = ((typ == "object" and isinstance(value, dict)) or (typ == "array" and isinstance(value, list)) or
             (typ == "string" and isinstance(value, str)) or (typ == "integer" and isinstance(value, int) and not isinstance(value, bool)) or
             (typ == "boolean" and isinstance(value, bool)))
    if typ and not valid:
        raise ToolError(422, "schema_validation_failed", f"{path} has invalid type")
    if typ == "object":
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(k not in props for k in value):
            raise ToolError(422, "schema_validation_failed", f"{path} has additional properties")
        for key in schema.get("required", []):
            if key not in value:
                raise ToolError(422, "schema_validation_failed", f"{path}.{key} is required")
        for key, item in value.items():
            if key in props:
                validate(props[key], item, f"{path}.{key}")
        if schema.get("x-flowgate-range") and value["start"] > value["end"]:
            raise ToolError(422, "schema_validation_failed", f"{path}.start must not exceed {path}.end")
    elif typ == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 10**9):
            raise ToolError(422, "schema_validation_failed", f"{path} has invalid length")
        for index, item in enumerate(value):
            validate(schema.get("items", {}), item, f"{path}[{index}]")
    elif typ == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 10**9) or ("enum" in schema and value not in schema["enum"]):
            raise ToolError(422, "schema_validation_failed", f"{path} has invalid value")
    elif typ == "integer" and value < schema.get("minimum", value):
        raise ToolError(422, "schema_validation_failed", f"{path} is below minimum")


def require_group_root(run: dict) -> Path:
    root = Path(str(run.get("source_root") or ""))
    project, group = run.get("project_id"), run.get("group_id")
    if not project or not group or not root.is_dir():
        raise ToolError(409, "group_worktree_unavailable")
    expected = git_service.effective_src_root(project, group)
    try:
        if expected is None or root.resolve(strict=True) != Path(expected).resolve(strict=True):
            raise ToolError(409, "group_worktree_unavailable")
    except (OSError, RuntimeError):
        raise ToolError(409, "group_worktree_unavailable")
    return root


def source_call(run: dict, raw_token: str, name: str, tool_input: dict) -> tuple[int, dict]:
    # remote_tool_service is the sole live-token/root authority.  In particular, it
    # preserves worktree fail-closed mutation gates while allowing approved base-root
    # fallback projects; the API adapter must not second-guess that selection.
    status, payload = remote_tool_service.handle(SOURCE_OPS[name], raw_token, dict(tool_input))
    payload.pop("continuation", None)
    return status, payload


def test_root(run: dict) -> Path:
    """Use a verified worktree for integrated projects, otherwise the server-selected root."""
    project = run.get("project_id")
    try:
        integrated = bool((git_service.db_git.get_config(project) or {}).get("enabled"))
    except Exception as exc:
        # Unknown integration state must never be treated as non-Git: an integrated project may execute tests only in its verified group worktree.
        raise ToolError(409, "git_integration_lookup_failed") from exc
    if integrated:
        return require_group_root(run)
    root = Path(str(run.get("source_root") or ""))
    if not root.is_dir():
        raise ToolError(409, "source_root_unavailable")
    return root


def run_test(run: dict, tool_input: dict, remaining_sec: float) -> tuple[int, dict]:
    root = test_root(run)
    normalized = test_command_service.normalize_command(tool_input["command"])
    host_os = test_command_service.current_os()
    allowed = [row for row in test_command_service.list_for_view(run["project_id"])
               if row.get("verified_os") in (None, "", host_os)]
    row = next((row for row in allowed if test_command_service.normalize_command(row.get("command") or row.get("command_raw") or "") == normalized), None)
    if row is None:
        raise ToolError(422, "not_verified")
    command = row.get("command_raw") or row.get("command")
    timeout = max(.01, min(300.0, remaining_sec))
    env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "TEMP": str(root / ".flowgate-tmp"), "TMP": str(root / ".flowgate-tmp")}
    started = time.monotonic()
    proc = subprocess.Popen(command, cwd=root, shell=True, executable=test_command_service.current_shell(), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, start_new_session=(os.name != "nt"))
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process_runner.kill_process_tree(proc)
        stdout, stderr = proc.communicate(timeout=5)
    def tail(raw: bytes) -> tuple[str, bool]:
        return raw[-1048576:].decode("utf-8", errors="replace"), len(raw) > 1048576
    out, out_cut = tail(stdout or b""); err, err_cut = tail(stderr or b"")
    payload = {"ok": True, "op": "run_test", "command": normalized, "exit_code": proc.returncode, "duration_ms": int((time.monotonic()-started)*1000), "stdout": out, "stderr": err, "truncated": out_cut or err_cut, "timed_out": timed_out}
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) > 16000:
        excess = len(encoded) - 16000
        payload["stdout"] = payload["stdout"][excess // 2:]
        payload["stderr"] = payload["stderr"][excess - excess // 2:]
        payload["truncated"] = True
    return 200, payload


def error_payload(name: str, exc: ToolError) -> tuple[int, dict]:
    code = "conflict" if exc.status == 409 else "invalid_request" if exc.status in (400, 422) else "unavailable"
    return exc.status, {"ok": False, "op": name, "error": {"code": code, "message": exc.message, "details": {"reason": exc.reason}}}
