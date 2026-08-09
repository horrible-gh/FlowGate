"""Inbound endpoint (D020 §3).

POST /api/v1/inbox
Auth: Authorization: Bearer {raw_token}
action: "new" | "edit"
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import uuid
from typing import Optional

import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, model_validator

from modules.flow_gate import linter as _linter
from modules.flow_gate import process_service
from modules.flow_gate import template_provision
from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db import workflow_events as db_events
from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.db import projects as db_projects
from modules.flow_gate.db import document_revisions as db_revisions
from modules.flow_gate.db import system_settings as db_settings
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.numbering import numbering_service
from modules.flow_gate.numbering.id_formatter import parse_doc_code
from modules.flow_gate.documents import document_service
from modules.flow_gate.documents.constants import (
    HEAD_TYPE_GUARD_EXEMPT_TYPES,
    WORK_PLAN_TYPE,
)
from modules.flow_gate.rbac.decorators import _has_permission, require_permission
from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import token_service
from modules.flow_gate.services import tool_registry
from modules.flow_gate.services import tr_scope_service
from modules.flow_gate.services import work_plan_service
from modules.flow_gate.storage.paths import (
    get_storage_root,
    group_path,
    document_path,
    to_storage_relative,
    resolve_storage_path,
    resolve_storage_dir,
)
from modules.flow_gate.utils.help_url import help_url as _help_url
from modules.flow_gate.utils.id_validators import (
    validate_doc_id,
    validate_group_id,
)

router = APIRouter(prefix="/api/v1", tags=["Inbox"])

# ── Environment variables ──────────────────────────────────────────────────────────────────

_CONTENT_MAX_DEFAULT = 10 * 1024 * 1024  # 10 MB

_DRYRUN_MAX_DEFAULT = 5  # max dry-run attempts per token (R0001 dry-run, group 0050)


def _content_max() -> int:
    try:
        return int(os.environ.get("FLOWGATE_INBOX_CONTENT_MAX", _CONTENT_MAX_DEFAULT))
    except (ValueError, TypeError):
        return _CONTENT_MAX_DEFAULT


def _dryrun_max() -> int:
    """Per-token dry-run attempt limit (L0007 §6.1). Env-tunable, default 5.

    Mirrors _content_max(): a policy value lives in config, not the DB schema (DB0008 §7).
    """
    try:
        return int(os.environ.get("FLOWGATE_INBOX_DRYRUN_MAX", _DRYRUN_MAX_DEFAULT))
    except (ValueError, TypeError):
        return _DRYRUN_MAX_DEFAULT


def _truthy(v) -> bool:
    """Normalize a dry_run flag defensively (L0007 §2.1).

    `bool(body.get("dry_run"))` would treat the string "false" as truthy. JSON booleans
    are the normal path, but a client may send a string/int, so accept those explicitly.
    None / missing / anything else → False (backward-compatible: existing callers unaffected).
    """
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "on")
    return False


_DRY_RUN_COPY = {
    "ko": {
        "limit": "Dry-run 한도({limit}회) 도달. 실제 제출하거나 새 토큰을 요청하세요.",
        "ok": "Dry-run OK. 이대로 제출하면 등록됩니다. 아무것도 등록되지 않았습니다.",
    },
    "en": {
        "limit": "The dry-run limit ({limit}) has been reached. Submit for real or request a new token.",
        "ok": "Dry-run OK. Submitting this payload will register it; this check registered nothing.",
    },
    "ja": {
        "limit": "Dry-runの上限({limit}回)に達しました。実際に提出するか、新しいトークンを要求してください。",
        "ok": "Dry-run OK。このペイロードを提出すると登録されます。この確認では何も登録されていません。",
    },
}


def _maybe_dry_run(
    body: dict, token_rec: dict, would_register: dict
) -> Optional[JSONResponse]:
    """Shared dry-run short-circuit for all three inbox handlers (L0007 §3, P0006).

    Returns:
      - None when this is not a dry-run → caller proceeds to the real side-effect path,
        byte-for-byte unchanged (backward compatibility).
      - HTTP 200 dry-run response (validation passed; every side effect skipped except the
        per-token counter +1) when under the limit.
      - HTTP 429 when the per-token dry-run limit is already reached (no counter bump).

    MUST be invoked only after Step 1~5 validation has fully passed and immediately before
    the first side effect, so a dry-run can never reach numbering/storage/DB/consume/SSE.
    This ordering is the structural guarantee behind the side-effect-zero invariant (L0007 §7).
    Validation *failures* never reach here (handlers already returned _fail), so a failed
    dry-run carries the exact same status/message as a real submission and is not counted
    (L0007 §5.1).
    """
    if not _truthy(body.get("dry_run")):
        return None

    limit = _dryrun_max()
    locale = token_rec.get("continuation_locale")
    copy = _DRY_RUN_COPY.get(locale) or _DRY_RUN_COPY["ko"]
    cnt = int(token_rec.get("dry_run_count") or 0)
    if cnt >= limit:
        # P0006 §3.5: limit-exceeded body carries the counters alongside the _fail shape.
        return JSONResponse(status_code=429, content={
            "ok": False,
            "http_status": 429,
            "error_message": copy["limit"].format(limit=limit),
            "help_url": _help_url(),
            "dry_run_count": cnt,
            "dry_run_remaining": 0,
        })

    # The only side effect a dry-run is allowed to have (L0007 §5.1 / DB0008 §3).
    token_service.increment_dry_run(token_rec["token_id"])
    return JSONResponse(status_code=200, content={
        "ok": True,
        "dry_run": True,
        "would_register": would_register,
        "dry_run_count": cnt + 1,
        "dry_run_remaining": limit - (cnt + 1),
        "message": copy["ok"],
    })


# ── Permission mapping (D020 §6) ───────────────────────────────────────────────────────

# new action: doc_type → required permission
_NEW_PERM_MAP: dict[str, str] = {
    "AC": "perm_document_approve",
    "RJ": "perm_document_reject",
}
_NEW_PERM_DEFAULT = "perm_document_create"


def _permission_for_new(doc_type: str) -> str:
    return _NEW_PERM_MAP.get(doc_type.upper(), _NEW_PERM_DEFAULT)


# ── Path validation (D020 §7-5) ─────────────────────────────────────────────────────

def validate_doc_path(doc_path: str, scratch_dir: str) -> bool:
    """Verify that doc_path is within the scratch_dir prefix."""
    p = pathlib.Path(doc_path)
    if not p.is_absolute():
        return False
    if str(p).startswith("\\\\"):  # UNC
        return False
    real = os.path.realpath(doc_path)
    if real != os.path.abspath(doc_path):
        return False  # symlink
    return real.lower().startswith(os.path.realpath(scratch_dir).lower())


# ── Pydantic schema ───────────────────────────────────────────────────────────
# NF-01: InboxNewRequest/InboxEditRequest are not used directly by the current route handler.
# Because the handler uses manual JSON parsing, validation errors return 400 instead of
# 422 (Pydantic) to preserve TSR021 SC-06-A/B expectations. Cleanup (B) adopted to prevent response-code regression.

class InboxNewRequest(BaseModel):
    project: str
    module: str
    group_name: str
    action: str  # "new"
    prev_doc_id: str
    doc_type: str
    title: Optional[str] = None
    doc_path: Optional[str] = None
    content: Optional[str] = None

    @model_validator(mode="after")
    def check_xor(self) -> "InboxNewRequest":
        has_path = self.doc_path is not None
        has_content = self.content is not None
        if has_path == has_content:
            raise ValueError("Exactly one of doc_path or content must be provided")
        return self


class InboxEditRequest(BaseModel):
    project: str
    module: str
    group_name: str
    action: str  # "edit"
    doc_id: str
    edit_reason: str
    linked_doc_id: Optional[str] = None
    doc_path: Optional[str] = None
    content: Optional[str] = None

    @model_validator(mode="after")
    def check_xor(self) -> "InboxEditRequest":
        has_path = self.doc_path is not None
        has_content = self.content is not None
        if has_path == has_content:
            raise ValueError("Exactly one of doc_path or content must be provided")
        return self

    @model_validator(mode="after")
    def check_edit_reason(self) -> "InboxEditRequest":
        valid = {"rejected", "qna_followup", "user_comment", "worker_self"}
        if self.edit_reason not in valid:
            raise ValueError("edit_reason value is invalid")
        return self


# ── Editable source content ─────────────────────────────────────────────────
#
# This router is registered before the legacy tree/git routers. Keeping the
# compatibility URL here lets source-file editing gain a group-aware contract
# without changing base-checkout callers: no group_id means the established base
# checkout, while a supplied group_id is resolved strictly to that group's live
# worktree and can never fall back to the base checkout.

class EditableSourceUpdate(BaseModel):
    content: str


class DeletedGroupFileRestore(BaseModel):
    path: str


def _editable_source_root(project_id: str, group_id: Optional[str]) -> tuple[pathlib.Path, Optional[str]]:
    from modules.flow_gate.services import git_service

    if group_id:
        group = db_groups.get_by_id(group_id)
        if group is None or group.get("project_id") != project_id:
            raise HTTPException(status_code=404, detail="Group not found in this project")
        root, reason = git_service.effective_src_root_ex(project_id, group_id)
        if root is None:
            raise HTTPException(
                status_code=409,
                detail=f"Group worktree is unavailable ({reason}); base checkout was not used",
            )
        return root.resolve(), group_id

    project = db_projects.get_by_id(project_id)
    project_name = (project.get("project_name") or "").strip() if project else ""
    settings = db_projects.get_settings(project_id)
    branch = (settings.get("branch") or "main").strip() if settings else "main"
    if not project_name:
        raise HTTPException(status_code=404, detail="Not found")
    # Resolve through the storage module at call time so deployments/tests that
    # redirect the configured source root keep the same compatibility seam as
    # the legacy src-content handler.
    from modules.flow_gate.storage import paths as storage_paths
    base_branch = git_service.base_branch_for(project_id) or branch
    return storage_paths.src_root(project_name, base_branch).resolve(), None


def _editable_source_path(
    project_id: str, path: str, group_id: Optional[str]
) -> tuple[pathlib.Path, pathlib.Path, Optional[str]]:
    root, resolved_group_id = _editable_source_root(project_id, group_id)
    normalized = path.replace("\\", "/")
    parts = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or (len(normalized) >= 2 and normalized[1] == ":")
        or ".." in parts
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    candidate = root.joinpath(*parts)
    current = root
    try:
        candidate.relative_to(root)
        for part in candidate.relative_to(root).parts:
            current = current / part
            if current.is_symlink():
                raise HTTPException(status_code=400, detail="Symbolic links are not allowed")
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except HTTPException:
        raise
    except (ValueError, OSError):
        raise HTTPException(status_code=403, detail="Forbidden")
    return resolved, root, resolved_group_id


def _source_etag(raw: bytes) -> str:
    return f'"{hashlib.sha256(raw).hexdigest()}"'


def _source_group_meta(project_id: str, group_id: str) -> dict:
    from modules.flow_gate.services import git_service

    try:
        _base_root, branch, commit = git_service.resolve_group_ref(project_id, group_id)
    except Exception:
        branch, commit = None, None
    return {"group_id": group_id, "branch": branch, "commit": commit}


def _emit_source_edit_refresh(project_id: str, group_id: Optional[str], path: str) -> None:
    """Best-effort refresh for other tabs/browsers after a direct source edit."""
    try:
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )
        from modules.flow_gate.api.v1.events.event_types import EventType

        broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.FILE_EXPLORER_REFRESH,
            payload={"operation": "updated", "source": "direct_edit", "path": path},
            audience="*",
            project=project_id,
            group_id=group_id,
            doc_id=None,
        ))
    except Exception:
        pass


@router.api_route(
    "/projects/{project_id}/files/src-content",
    methods=["GET", "HEAD"],
    response_class=PlainTextResponse,
)
def get_editable_source_content(
    request: Request,
    project_id: str,
    path: str = Query(..., description="relative path from source root"),
    group_id: Optional[str] = Query(None, description="target group worktree"),
):
    """Read current source content from the base checkout or an exact group worktree."""
    full_path, _root, resolved_group_id = _editable_source_path(project_id, path, group_id)
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    raw = full_path.read_bytes()
    headers = {
        "Content-Length": str(len(raw)),
        "ETag": _source_etag(raw),
    }
    if resolved_group_id:
        headers["X-FlowGate-Group-Id"] = resolved_group_id
    if request.method == "HEAD":
        return PlainTextResponse("", headers=headers)
    return PlainTextResponse(raw.decode("utf-8", errors="replace"), headers=headers)


@router.patch("/projects/{project_id}/files/src-content", response_class=JSONResponse)
def update_editable_source_content(
    request: Request,
    project_id: str,
    body: EditableSourceUpdate,
    path: str = Query(..., description="relative path from source root"),
    group_id: Optional[str] = Query(None, description="target group worktree"),
):
    """Update an existing source file, fail-closed when a group worktree is requested."""
    full_path, _root, resolved_group_id = _editable_source_path(project_id, path, group_id)
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    before = full_path.read_bytes()
    if_match = (request.headers.get("if-match") or "").strip()
    if if_match and if_match != _source_etag(before):
        raise HTTPException(
            status_code=409,
            detail="File changed since it was opened; reload before saving",
        )

    encoded = body.content.encode("utf-8")
    full_path.write_bytes(encoded)
    _emit_source_edit_refresh(project_id, resolved_group_id, path)

    response: dict = {
        "path": path,
        "content_length": len(body.content),
        "etag": _source_etag(encoded),
    }
    if resolved_group_id:
        response["group_git"] = _source_group_meta(project_id, resolved_group_id)
    else:
        from modules.flow_gate.services import git_service
        base_git = git_service.base_checkout_dirty_status(project_id)
        # Preserve the established src-content response shape. Untracked files
        # are refreshed through the project status endpoint, not this existing-file edit.
        base_git.pop("untracked", None)
        response["base_git"] = base_git
    return response


def _group_path_is_deleted(project_id: str, group_id: str, path: str) -> bool:
    """Best-effort deletion check for stale/forged group blob requests."""
    from modules.flow_gate.services import git_service

    normalized = path.replace("\\", "/")
    try:
        payload = git_service.read_group_changes(project_id, group_id)
    except Exception:
        return False
    return any(
        change.get("path", "").replace("\\", "/") == normalized
        and change.get("status") == "D"
        for change in payload.get("data", {}).get("changes", [])
    )


@router.post("/projects/{project_id}/git/groups/{group_id}/restore")
def restore_deleted_group_file(
    project_id: str,
    group_id: str,
    body: DeletedGroupFileRestore,
    user=Depends(require_permission("project.settings.edit", "project_id")),
):
    """Restore one deleted tracked file from the group's current HEAD."""
    from modules.flow_gate.services import git_service

    normalized = body.path.replace("\\", "/")
    full_path, root, _resolved_group_id = _editable_source_path(
        project_id, normalized, group_id
    )
    if not _group_path_is_deleted(project_id, group_id, normalized):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "FILE_NOT_DELETED",
                "message": "File is not deleted in this group",
            },
        )

    proc = git_service._run_git(
        ["checkout", "HEAD", "--", normalized],
        cwd=root,
    )
    if proc.returncode != 0 or not full_path.is_file():
        raise HTTPException(
            status_code=500,
            detail={
                "code": "RESTORE_FAILED",
                "message": "Failed to restore the deleted file",
            },
        )

    _emit_source_edit_refresh(project_id, group_id, normalized)
    return {
        "ok": True,
        "data": {
            "group_id": group_id,
            "path": normalized,
            "restored": True,
        },
    }


@router.get("/projects/{project_id}/git/groups/{group_id}/blob")
def get_editable_group_blob(
    project_id: str,
    group_id: str,
    path: str,
    ref: Optional[str] = None,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    """Return the latest group-worktree bytes so unstaged tracked edits stay visible."""
    if ref and not re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        raise HTTPException(status_code=400, detail="ref must be a full 40-hex commit sha")
    full_path, _root, _resolved_group_id = _editable_source_path(
        project_id, path, group_id
    )
    if not full_path.is_file():
        # 0340 T0004 (B0001 / NR0003 §3): a deleted path still exists in the
        # branch HEAD tree, so a stale editor tab can ask for it even though the
        # group worktree no longer has bytes. Distinguish that intentional deletion
        # from an unknown path and never fall back to the committed blob.
        if _group_path_is_deleted(project_id, group_id, path):
            raise HTTPException(
                status_code=410,
                detail={"code": "FILE_DELETED", "message": "File was deleted in this group"},
            )
        raise HTTPException(status_code=404, detail="Not found")

    from modules.flow_gate.services import git_service

    size = full_path.stat().st_size
    with full_path.open("rb") as handle:
        head = handle.read(git_service.BLOB_MAX_RETURN_BYTES)
    meta = _source_group_meta(project_id, group_id)
    if b"\x00" in head[:git_service.BLOB_BINARY_SNIFF_BYTES]:
        return {"ok": True, "data": {
            **meta,
            "path": path,
            "size": size,
            "binary": True,
            "truncated": False,
            "encoding": None,
            "content": None,
            "worktree": True,
        }}
    return {"ok": True, "data": {
        **meta,
        "path": path,
        "size": size,
        "binary": False,
        "truncated": size > git_service.BLOB_MAX_RETURN_BYTES,
        "encoding": "utf-8",
        "content": head.decode("utf-8", errors="replace"),
        "worktree": True,
    }}

# ── Reversible Git group archive (0339 R0001 / MirageGlass sqyjx6bt v4) ──────
#
# This router is registered before the legacy Git router.  The compatibility
# seam below adds the approved seventh finalize result without duplicating the
# mature merge/push implementation: named refs preserve the branch and every
# uncommitted file, the group is hidden only after cleanup, and restore/purge
# remain explicit archive-only operations.

_GIT_ARCHIVE_KEY_PREFIX = "git.archive."
_GIT_ARCHIVE_STATUSES = {"archiving", "archived"}


class GitArchiveBody(BaseModel):
    reason: Optional[str] = None


def _git_archive_key(group_id: str) -> str:
    return f"{_GIT_ARCHIVE_KEY_PREFIX}{group_id}"


def _git_archive_ref_segment(group_id: str) -> str:
    # Refs live in the project-wide base checkout, so the terminal number alone
    # is not unique (default.0001 and fileforge.0001 would overwrite each other).
    # Drop only the project prefix and retain module + group number.
    parts = (group_id or "").split(".")
    group_segment = "_".join(parts[1:] if len(parts) > 1 else parts)
    segment = re.sub(r"[^A-Za-z0-9._-]+", "_", group_segment).strip(".")
    while ".." in segment:
        segment = segment.replace("..", "._.")
    return segment.replace("@{", "_")


def _git_archive_record(group_id: str) -> Optional[dict]:
    raw = db_settings.get_value(_git_archive_key(group_id))
    if not raw:
        return None
    try:
        record = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(record, dict) or record.get("group_id") != group_id:
        return None
    return record


def _save_git_archive_record(record: dict, actor_user_id: Optional[str]) -> None:
    db_settings.set_value(
        _git_archive_key(record["group_id"]),
        json.dumps(record, ensure_ascii=False, separators=(",", ":")),
        value_type="json",
        description="FlowGate reversible Git group archive",
        updated_by=actor_user_id,
    )


def _project_git_archives(project_id: str) -> list[dict]:
    items: list[dict] = []
    for row in db_settings.list_settings():
        if not str(row.get("setting_key") or "").startswith(_GIT_ARCHIVE_KEY_PREFIX):
            continue
        try:
            record = json.loads(row.get("setting_value") or "")
        except (TypeError, ValueError):
            continue
        if (
            not isinstance(record, dict)
            or record.get("project_id") != project_id
            or record.get("status") not in _GIT_ARCHIVE_STATUSES
        ):
            continue
        group = db_groups.get_by_id(record.get("group_id") or "")
        enriched = dict(record)
        enriched["title"] = (group or {}).get("title") or record.get("group_id")
        items.append(enriched)
    items.sort(key=lambda item: item.get("archived_at") or "", reverse=True)
    return items


def _git_archive_error(
    status: int, code: str, message: str, details: Optional[dict] = None
):
    from modules.flow_gate.services.git_service import GitServiceError

    raise GitServiceError(status, code, message, details=details)


def _require_git_archive_group_permission(
    user: dict, group_id: str, permission: str
) -> Optional[JSONResponse]:
    project_id = (group_id or "").split(".", 1)[0]
    if _has_permission(user, permission, project_id):
        return None
    return JSONResponse(
        status_code=403,
        content={"ok": False, "error": {"code": "forbidden", "message": "Forbidden"}},
    )


def _git_archive_run(
    args: list[str], *, cwd: pathlib.Path, code: str, message: str
):
    from modules.flow_gate.services import git_service

    proc = git_service._run_git(args, cwd=cwd)
    if proc.returncode != 0:
        _git_archive_error(
            500, code, f"{message}: {git_service._last_line(proc.stderr)}"
        )
    return proc


def _git_archive_existing_ref(base_root: pathlib.Path, ref_name: str) -> Optional[str]:
    """Return an existing partial-archive ref without treating it as foreign data."""
    from modules.flow_gate.services import git_service

    proc = git_service._run_git(
        ["show-ref", "--verify", "--quiet", ref_name], cwd=base_root
    )
    if proc.returncode == 1:
        return None
    if proc.returncode != 0:
        _git_archive_error(
            500,
            "archive_ref_failed",
            f"could not validate archive ref '{ref_name}': "
            f"{git_service._last_line(proc.stderr)}",
        )
    resolved = _git_archive_run(
        ["rev-parse", "--verify", ref_name],
        cwd=base_root,
        code="archive_ref_failed",
        message=f"could not resolve partial archive ref '{ref_name}'",
    )
    return (resolved.stdout or "").strip() or None


def _emit_git_archive_refresh(project_id: str, group_id: str, operation: str) -> None:
    """Best-effort convergence for group tree, Git slot list, and open tabs."""
    try:
        from modules.flow_gate.api.v1.events.event_types import EventType
        from modules.flow_gate.api.v1.events.publisher import (
            FlowEvent,
            broadcast_event_threadsafe,
        )

        broadcast_event_threadsafe(FlowEvent(
            event_type=EventType.GROUP_VIEW_REFRESH,
            payload={
                "group_id": group_id,
                "reason": f"group_git_archive_{operation}",
            },
            audience="*",
            project=project_id,
            group_id=group_id,
            doc_id=None,
        ))
    except Exception:
        pass
    try:
        from modules.flow_gate.services import git_service

        git_service._emit_pending_changed(project_id, group_id, "none")
    except Exception:
        pass


def _archive_group_git(
    group_id: str,
    reason: Optional[str] = None,
    actor_user_id: Optional[str] = None,
) -> dict:
    """Pin branch/worktree state to named refs, then release and hide the slot."""
    from modules.flow_gate.services import git_service

    group = db_groups.get_by_id(group_id)
    if group is None:
        _git_archive_error(404, "not_found", f"group '{group_id}' not found")
    project_id = group.get("project_id") or (group_id.split(".", 1)[0] if group_id else "")
    cfg = git_service.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        _git_archive_error(
            409, "invalid_state",
            f"git integration is not enabled for project '{project_id}'",
        )

    existing = _git_archive_record(group_id)
    state = git_service.db_git.get_state(group_id)
    if existing and existing.get("status") == "archived":
        if state is not None and state.get("worktree_registered"):
            _git_archive_error(
                409,
                "archive_already_exists",
                f"group '{group_id}' already has a completed archive; "
                "restore or permanently delete it before archiving new work",
            )
        return {"ok": True, "result": {**existing, "idempotent": True}}
    if existing and existing.get("status") != "archiving":
        _git_archive_error(
            409,
            "invalid_state",
            f"archive for '{group_id}' has unsupported status "
            f"'{existing.get('status') or 'none'}'",
        )

    # A fresh request must be a real finalize choice.  An interrupted request
    # already has durable refs and resumes below without taking a second stash.
    if existing is None:
        finalized = _GIT_ARCHIVE_ORIGINAL_GET_FINALIZE(group_id)
        current = (finalized.get("state") or {}).get("status")
        if current not in ("awaiting_choice", "waiting"):
            _git_archive_error(
                409, "invalid_state",
                f"group '{group_id}' cannot be archived from git state '{current or 'none'}'",
            )

    holder = f"archive:{uuid.uuid4()}"
    if not git_service._acquire_lock(project_id, holder):
        _git_archive_error(409, "git_busy", "another Git operation is in progress")
    try:
        record = _git_archive_record(group_id)
        state = git_service.db_git.get_state(group_id)
        if record and record.get("status") == "archived":
            if state is not None and state.get("worktree_registered"):
                _git_archive_error(
                    409,
                    "archive_already_exists",
                    f"group '{group_id}' already has a completed archive; "
                    "restore or permanently delete it before archiving new work",
                )
            return {"ok": True, "result": {**record, "idempotent": True}}
        if record and record.get("status") != "archiving":
            _git_archive_error(
                409,
                "invalid_state",
                f"archive for '{group_id}' has unsupported status "
                f"'{record.get('status') or 'none'}'",
            )
        if record is None:
            if state is None or not state.get("worktree_registered"):
                _git_archive_error(
                    409, "invalid_state",
                    f"Git integration is not active for group '{group_id}'",
                )
            base_root, branch, head_sha = git_service.resolve_group_ref(project_id, group_id)
            worktree_root, root_reason = git_service.effective_src_root_ex(project_id, group_id)
            if worktree_root is None:
                _git_archive_error(
                    409, "invalid_state",
                    f"group worktree is unavailable ({root_reason})",
                )

            ref_segment = _git_archive_ref_segment(group_id)
            head_ref = f"refs/flowgate/archive/{ref_segment}/head"
            stash_ref = f"refs/flowgate/archive/{ref_segment}/stash"
            # A module-qualified ref with no archive record can only be this
            # group's interrupted transaction.  Re-pin head to the current
            # branch and carry a completed partial stash forward instead of
            # permanently blocking the group or deleting preserved bytes.
            partial_stash_sha = _git_archive_existing_ref(base_root, stash_ref)
            _git_archive_run(
                ["update-ref", head_ref, head_sha], cwd=base_root,
                code="archive_ref_failed", message="could not preserve the group branch",
            )

            status_proc = _git_archive_run(
                ["status", "--porcelain=v1", "-z"], cwd=worktree_root,
                code="archive_status_failed", message="could not inspect the group worktree",
            )
            changed_entries = [
                item for item in (status_proc.stdout or "").split("\0") if item
            ]
            stash_sha: Optional[str] = partial_stash_sha
            if partial_stash_sha and changed_entries:
                _git_archive_error(
                    409,
                    "archive_retry_conflict",
                    "a partial archive stash and newer worktree changes both exist; "
                    "commit or otherwise clear the newer changes before retrying",
                )
            if changed_entries:
                _git_archive_run(
                    [
                        "stash", "push", "--include-untracked",
                        "--message", f"flowgate archive {group_id}",
                    ],
                    cwd=worktree_root,
                    code="archive_snapshot_failed",
                    message="could not preserve uncommitted changes",
                )
                stash_proc = _git_archive_run(
                    ["rev-parse", "--verify", "refs/stash"], cwd=base_root,
                    code="archive_snapshot_failed",
                    message="could not resolve the preserved changes",
                )
                stash_sha = (stash_proc.stdout or "").strip()
                _git_archive_run(
                    ["update-ref", stash_ref, stash_sha], cwd=base_root,
                    code="archive_ref_failed", message="could not pin the preserved changes",
                )
                # The project mutex makes this shared-stack operation exclusive.
                _git_archive_run(
                    ["stash", "drop", "stash@{0}"], cwd=base_root,
                    code="archive_snapshot_failed",
                    message="could not release the temporary stash entry",
                )

            base_branch = (cfg.get("base_branch") or "main").strip() or "main"
            base_proc = git_service._run_git(
                ["merge-base", base_branch, branch], cwd=base_root
            )
            base_sha = (
                (base_proc.stdout or "").strip() if base_proc.returncode == 0 else None
            )
            commits_proc = git_service._run_git(
                ["rev-list", "--count", f"{base_branch}..{branch}"], cwd=base_root
            )
            try:
                commit_count = int((commits_proc.stdout or "0").strip())
            except ValueError:
                commit_count = 0

            record = {
                "status": "archiving",
                "project_id": project_id,
                "group_id": group_id,
                "branch": branch,
                "git_status": state.get("status") or "awaiting_choice",
                "base_branch": base_branch,
                "reason": re.sub(r"\s+", " ", reason or "").strip()[:500] or None,
                "actor_user_id": actor_user_id,
                "archived_at": now_iso(),
                "base_sha": base_sha,
                "head_sha": head_sha,
                "stash_sha": stash_sha,
                "head_ref": head_ref,
                "stash_ref": stash_ref if stash_sha else None,
                "commit_count": commit_count,
                "changed_file_count": len(changed_entries),
            }
            _save_git_archive_record(record, actor_user_id)

        # Existing cleanup handles live, missing, and half-removed worktrees.  It
        # is safe to force-discard because the named refs now own every byte.
        state = git_service.db_git.get_state(group_id)
        if state is not None and state.get("worktree_registered"):
            if not git_service._cleanup_group_slot(
                project_id, group_id, force_discard=True
            ):
                _git_archive_error(
                    500, "archive_cleanup_failed",
                    "the archive refs are safe, but the worktree could not be released; retry",
                )

        # An archived slot is inactive. Keeping awaiting_choice here made an AC
        # approval race a stale git_action into the precheck and fail with 422.
        # The pre-archive status is retained in the archive record for restore.
        if git_service.db_git.get_state(group_id) is not None:
            git_service.db_git.set_status(group_id, "none")

        # Do not soft-delete the group row. The document tree fetches project
        # documents independently of active groups, so deleting only the group
        # turned every retained document into a visible "Uncategorized" orphan.
        # Final-approved groups already use the explorer's normal hidden toggle;
        # the archive record and inactive Git slot are the archive source of truth.
        record["status"] = "archived"
        _save_git_archive_record(record, actor_user_id)
    finally:
        git_service.db_git.release_lock(project_id, holder)

    _emit_git_archive_refresh(project_id, group_id, "archived")
    return {"ok": True, "result": record}


def _restore_group_git_archive(group_id: str, actor_user_id: Optional[str]) -> dict:
    from modules.flow_gate.services import git_service

    record = _git_archive_record(group_id)
    if record is None or record.get("status") not in _GIT_ARCHIVE_STATUSES:
        _git_archive_error(404, "archive_not_found", f"archive for '{group_id}' not found")
    group = db_groups.get_by_id(group_id)
    if group is None:
        _git_archive_error(404, "not_found", f"group '{group_id}' not found")
    project_id = record.get("project_id") or group.get("project_id")
    cfg = git_service.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled"):
        _git_archive_error(409, "invalid_state", "git integration is not enabled")
    state = git_service.db_git.get_state(group_id)

    holder = f"archive-restore:{uuid.uuid4()}"
    if not git_service._acquire_lock(project_id, holder):
        _git_archive_error(409, "git_busy", "another Git operation is in progress")
    try:
        project_name = git_service._project_name(project_id)
        if not project_name:
            _git_archive_error(404, "not_found", f"project '{project_id}' not found")
        base_branch = record.get("base_branch") or cfg.get("base_branch") or "main"
        base_root = git_service.src_root(project_name, base_branch)
        if not (base_root / ".git").exists():
            _git_archive_error(409, "invalid_state", "base checkout is not provisioned")

        branch = str(record.get("branch") or "").strip()
        head_ref = str(record.get("head_ref") or "").strip()
        stash_ref = str(record.get("stash_ref") or "").strip()
        if not branch or not head_ref or not git_service._ref_exists(base_root, head_ref):
            _git_archive_error(409, "archive_corrupt", "the archived branch ref is missing")
        if git_service._ref_exists(base_root, f"refs/heads/{branch}"):
            _git_archive_error(409, "restore_conflict", f"local branch '{branch}' already exists")
        worktree_root = git_service.src_root(project_name, branch)
        if worktree_root.exists():
            _git_archive_error(
                409, "restore_conflict", f"worktree path already exists: {worktree_root}",
            )

        _git_archive_run(
            ["update-ref", f"refs/heads/{branch}", head_ref], cwd=base_root,
            code="restore_failed", message="could not recreate the archived branch",
        )
        added = git_service._run_git(
            ["worktree", "add", str(worktree_root), branch], cwd=base_root
        )
        if added.returncode != 0:
            git_service._run_git(["update-ref", "-d", f"refs/heads/{branch}"], cwd=base_root)
            _git_archive_error(
                500, "restore_failed",
                f"could not recreate the worktree: {git_service._last_line(added.stderr)}",
            )

        if stash_ref:
            applied = git_service._run_git(
                ["stash", "apply", "--index", stash_ref], cwd=worktree_root
            )
            if applied.returncode != 0:
                # Roll back only the worktree created from immutable archive refs.
                git_service._run_git(["reset", "--hard", "HEAD"], cwd=worktree_root)
                git_service._run_git(["clean", "-fd"], cwd=worktree_root)
                git_service._run_git(
                    ["worktree", "remove", "--force", str(worktree_root)],
                    cwd=base_root,
                    timeout=git_service.GIT_WORKTREE_RM_TIMEOUT_SEC,
                )
                git_service._run_git(
                    ["update-ref", "-d", f"refs/heads/{branch}"], cwd=base_root
                )
                _git_archive_error(
                    409, "restore_conflict",
                    "the archived working changes could not be applied; the archive is intact",
                    details={"git": git_service._last_line(applied.stderr)},
                )

        git_service.db_git.register_worktree(group_id, project_id, branch)
        restored_status = (
            record.get("git_status")
            or (state or {}).get("status")
            or "awaiting_choice"
        )
        git_service.db_git.set_status(group_id, restored_status)
        # Backward compatibility for archives created before group soft deletion
        # was removed: restoring an old record also repairs its tree membership.
        db_groups.update(group_id, {"deleted_at": None})
        git_service._run_git(["update-ref", "-d", head_ref], cwd=base_root)
        if stash_ref:
            git_service._run_git(["update-ref", "-d", stash_ref], cwd=base_root)
        db_settings.delete(_git_archive_key(group_id))
    finally:
        git_service.db_git.release_lock(project_id, holder)

    _emit_git_archive_refresh(project_id, group_id, "restored")
    return {
        "ok": True,
        "result": {
            "group_id": group_id,
            "status": "restored",
            "branch": record.get("branch"),
            "head_sha": record.get("head_sha"),
            "changed_file_count": record.get("changed_file_count", 0),
        },
    }


def _purge_group_git_archive(group_id: str, actor_user_id: Optional[str]) -> dict:
    from modules.flow_gate.services import git_service

    record = _git_archive_record(group_id)
    if record is None or record.get("status") != "archived":
        _git_archive_error(404, "archive_not_found", f"archive for '{group_id}' not found")
    group = db_groups.get_by_id(group_id)
    if group is None:
        _git_archive_error(
            409, "invalid_state", "the archived group no longer exists",
        )
    project_id = record.get("project_id") or group.get("project_id")
    state = git_service.db_git.get_state(group_id)
    if state is not None and state.get("worktree_registered"):
        _git_archive_error(409, "invalid_state", "an active group worktree cannot be purged")

    holder = f"archive-purge:{uuid.uuid4()}"
    if not git_service._acquire_lock(project_id, holder):
        _git_archive_error(409, "git_busy", "another Git operation is in progress")
    try:
        project_name = git_service._project_name(project_id)
        cfg = git_service.db_git.get_config(project_id)
        base_branch = record.get("base_branch") or (cfg or {}).get("base_branch") or "main"
        base_root = git_service.src_root(project_name, base_branch) if project_name else None
        if base_root is None or not (base_root / ".git").exists():
            _git_archive_error(409, "invalid_state", "base checkout is not provisioned")
        for ref_name in (record.get("stash_ref"), record.get("head_ref")):
            if ref_name:
                deleted = git_service._run_git(
                    ["update-ref", "-d", str(ref_name)], cwd=base_root
                )
                if deleted.returncode != 0:
                    _git_archive_error(
                        500, "purge_failed", f"could not remove archive ref '{ref_name}'",
                    )
        db_settings.delete(_git_archive_key(group_id))
    finally:
        git_service.db_git.release_lock(project_id, holder)

    _emit_git_archive_refresh(project_id, group_id, "purged")
    return {
        "ok": True,
        "result": {
            "group_id": group_id,
            "status": "purged",
            "purged_by": actor_user_id,
        },
    }


@router.get("/projects/{project_id}/git/archives")
def list_git_archives(
    project_id: str,
    user=Depends(require_permission("project.settings.read", "project_id")),
):
    items = _project_git_archives(project_id)
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/groups/{group_id}/git/archive")
def archive_group_git(
    group_id: str,
    body: Optional[GitArchiveBody] = None,
    user=Depends(get_current_user),
):
    denied = _require_git_archive_group_permission(user, group_id, "project.settings.edit")
    if denied is not None:
        return denied
    return _archive_group_git(
        group_id,
        reason=body.reason if body else None,
        actor_user_id=user.get("user_id"),
    )


@router.post("/groups/{group_id}/git/archive/restore")
def restore_group_git_archive(group_id: str, user=Depends(get_current_user)):
    denied = _require_git_archive_group_permission(user, group_id, "project.settings.edit")
    if denied is not None:
        return denied
    return _restore_group_git_archive(group_id, user.get("user_id"))


@router.delete("/groups/{group_id}/git/archive")
def purge_group_git_archive(group_id: str, user=Depends(get_current_user)):
    denied = _require_git_archive_group_permission(user, group_id, "project.settings.edit")
    if denied is not None:
        return denied
    return _purge_group_git_archive(group_id, user.get("user_id"))


def _git_finalize_state_with_archive(
    group_id: str, *, preview_ac: bool = False
) -> dict:
    """Publish archive beside, never inside, the approved two-axis control.

    MirageGlass sqyjx6bt v4 keeps ``stash`` outside scope×push because it is not
    a Git transmission result. The client receives a separate action plus the
    small preservation preview needed by the amber keep zone.
    """
    result = _GIT_ARCHIVE_ORIGINAL_GET_FINALIZE(group_id, preview_ac=preview_ac)
    state = result.get("state") if isinstance(result, dict) else None
    axes = state.get("action_axes") if isinstance(state, dict) else None
    if isinstance(axes, dict):
        project_id = (group_id or "").split(".", 1)[0]
        state["archive_action"] = "stash"
        state["archive_count"] = len(_project_git_archives(project_id))
        preview = {
            "commit_count": max(int(state.get("ahead_count") or 0), 0),
            "changed_file_count": 0,
            "head_ref": (
                f"refs/flowgate/archive/"
                f"{_git_archive_ref_segment(group_id)}/head"
            ),
        }
        try:
            from modules.flow_gate.services import git_service

            worktree_root, _ = git_service.effective_src_root_ex(
                project_id, group_id
            )
            if worktree_root is not None:
                proc = git_service._run_git(
                    ["status", "--porcelain=v1", "-z"], cwd=worktree_root
                )
                if proc.returncode == 0:
                    preview["changed_file_count"] = len([
                        entry
                        for entry in (proc.stdout or "").split("\0")
                        if entry
                    ])
        except Exception:
            # Preview is advisory. The archive transaction performs its own
            # authoritative status/ref validation under the project mutex.
            pass
        state["archive_preview"] = preview
    return result


def _git_finalize_with_archive(
    group_id: str,
    action: Optional[str],
    commit_message: Optional[str] = None,
) -> dict:
    if action == "stash":
        # Direct API callers may use commit_message as the optional archive reason.
        return _archive_group_git(group_id, reason=commit_message)
    return _GIT_ARCHIVE_ORIGINAL_FINALIZE(group_id, action, commit_message)


def _precheck_approve_git_action_with_archive(doc: Optional[dict], git_action: str) -> str:
    if git_action != "stash":
        return _GIT_ARCHIVE_ORIGINAL_PRECHECK(doc, git_action)
    from modules.flow_gate.services import git_service

    invalid = git_service.GitServiceError(
        422, "invalid_request",
        "git_action is only accepted on AC documents of a git-active group",
    )
    doc = doc or {}
    if doc.get("type_code") != "AC":
        raise invalid
    group_id = doc.get("group_id") or ""
    project_id = (group_id or "").split(".", 1)[0]
    cfg = git_service.db_git.get_config(project_id)
    state = git_service.db_git.get_state(group_id)
    if (
        cfg is None or not cfg.get("enabled")
        or state is None or not state.get("worktree_registered")
    ):
        raise invalid
    return group_id


def _install_git_archive_finalize_extension() -> None:
    """Install once at the shared service seam used by Git and AC approval routes."""
    from modules.flow_gate.services import git_service

    if getattr(git_service, "_flowgate_git_archive_installed", False):
        # Development reloads create fresh wrapper function objects while the
        # service module survives. Rebind those wrappers, but keep the saved true
        # originals, so neither stale code nor wrapper-to-wrapper recursion remains.
        git_service.finalize = _git_finalize_with_archive
        git_service.get_finalize_state = _git_finalize_state_with_archive
        git_service.precheck_approve_git_action = _precheck_approve_git_action_with_archive
        return
    git_service._flowgate_git_archive_installed = True
    git_service._flowgate_git_archive_original_finalize = git_service.finalize
    git_service._flowgate_git_archive_original_get_finalize = git_service.get_finalize_state
    git_service._flowgate_git_archive_original_precheck = git_service.precheck_approve_git_action
    git_service.finalize = _git_finalize_with_archive
    git_service.get_finalize_state = _git_finalize_state_with_archive
    git_service.precheck_approve_git_action = _precheck_approve_git_action_with_archive


from modules.flow_gate.services import git_service as _git_archive_service

# Re-import/reload safe: once installed, recover the true originals saved on the
# service module instead of treating our wrappers as their own delegates.
_GIT_ARCHIVE_ORIGINAL_FINALIZE = getattr(
    _git_archive_service,
    "_flowgate_git_archive_original_finalize",
    _git_archive_service.finalize,
)
_GIT_ARCHIVE_ORIGINAL_GET_FINALIZE = getattr(
    _git_archive_service,
    "_flowgate_git_archive_original_get_finalize",
    _git_archive_service.get_finalize_state,
)
_GIT_ARCHIVE_ORIGINAL_PRECHECK = getattr(
    _git_archive_service,
    "_flowgate_git_archive_original_precheck",
    _git_archive_service.precheck_approve_git_action,
)
_install_git_archive_finalize_extension()

# ── Failure response helper (D020 §3-5) ────────────────────────────────────────────────

def _extract_title_from_content(content: Optional[str]) -> Optional[str]:
    """Extract display title from document body.

    Priority:
      1. `title:` line inside a frontmatter (`---` block), excluding placeholder `<Title here>`
      2. First `# Heading` line in the body
    Returns None if extraction fails.
    """
    if not content:
        return None
    lines = content.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            stripped = line.strip()
            if stripped == "---":
                break
            if stripped.lower().startswith("title:"):
                value = stripped[len("title:"):].strip()
                if value and value != "<Title here>":
                    return value
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return None


# ── Duplicate-body guard (B0106 / NR0003) ──────────────────────────────────────
# The investigation proved the server faithfully writes whatever body the submitter
# sends; the contamination it surfaced — four separate NR documents in different
# groups all carrying 0082's 5 KB report body, each with its own correct title —
# happened in the submission (worker/client) layer, not here. Since that layer is an
# ad-hoc AI worker assembling a POST from scratch files rather than one fixed client,
# the durable defense lives at the gate: refuse to let a *substantial* body land
# byte-identical to an existing document in a *different* group (the contamination
# signature). Short bodies (approval stubs, one-line memos, boilerplate) legitimately
# repeat across groups and are exempt, so the guard fires only on the signature.

_DUP_MIN_CHARS_DEFAULT = 1024  # bodies shorter than this never trip the guard


def _dup_min_chars() -> int:
    """Minimum stripped body length for the cross-group duplicate guard (env-tunable).

    Mirrors _content_max()/_dryrun_max(): a policy threshold lives in config, not the
    DB schema. The observed contamination was 5397 bytes — far above any reasonable
    threshold — so 1024 leaves a wide margin while exempting approval stubs.
    """
    try:
        return int(os.environ.get("FLOWGATE_INBOX_DUP_MIN_CHARS", _DUP_MIN_CHARS_DEFAULT))
    except (ValueError, TypeError):
        return _DUP_MIN_CHARS_DEFAULT


def _content_fingerprint(text: Optional[str]) -> Optional[str]:
    """sha256 hex of a substantial body, else None.

    Returns None for non-str input and for short bodies (< _dup_min_chars stripped
    chars): approval stubs ("조사지시 가 승인되었습니다."), short memos and shared
    boilerplate legitimately repeat across groups and must never trip the cross-group
    duplicate guard. The contamination this catches is a full report copied verbatim,
    always well above the threshold.
    """
    if not isinstance(text, str):
        return None
    if len(text.strip()) < _dup_min_chars():
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_body(text: str) -> str:
    """Whitespace-insensitive canonical form for near-duplicate detection.

    Collapses every run of whitespace (spaces, tabs, newlines) to a single space
    and strips the ends. The byte-exact guard (`content_sha256`) misses a clone
    that only differs by reflow / indentation / trailing-newline churn — the
    natural next evasion once exact-match is enforced (NR0003 §5.1c). Normalizing
    away whitespace catches those while staying conservative: it does not touch
    word characters, so two genuinely different reports never collide.
    """
    return re.sub(r"\s+", " ", text).strip()


def _content_fingerprints(text: Optional[str]) -> dict:
    """Both body fingerprints for a substantial body, else an empty dict.

    Returns ``{"content_sha256": <exact>, "content_sha256_norm": <normalized>}``
    so the duplicate-body guard can match either a byte-exact clone or a
    whitespace-only near-duplicate (NR0003 §5.1c). The two keys are always
    present together or not at all, gated by the same `_dup_min_chars` threshold
    that exempts short approval stubs / boilerplate.
    """
    exact = _content_fingerprint(text)
    if exact is None or not isinstance(text, str):
        return {}
    return {
        "content_sha256": exact,
        "content_sha256_norm": hashlib.sha256(
            _normalize_body(text).encode("utf-8")
        ).hexdigest(),
    }


def _find_body_twin(
    fingerprints: dict, *, exclude_group_id: Optional[str]
) -> Optional[dict]:
    """Existing doc whose body matches by exact OR normalized fingerprint, else None.

    Exact match is tried first (names the true byte-identical original); the
    normalized match is the near-duplicate backstop. Both stay cross-group only
    (`exclude_group_id`) — the observed contamination signature is cross-group,
    and legitimate large bodies do recur within a single group's own thread.
    """
    if not fingerprints:
        return None
    exact = fingerprints.get("content_sha256")
    if exact:
        twin = db_docs.find_by_content_fingerprint(exact, exclude_group_id=exclude_group_id)
        if twin is not None:
            return twin
    norm = fingerprints.get("content_sha256_norm")
    if norm:
        twin = db_docs.find_by_content_fingerprint(
            norm, exclude_group_id=exclude_group_id, key="content_sha256_norm"
        )
        if twin is not None:
            return twin
    return None


def _submission_text(doc_path: Optional[str], content: Optional[str]) -> Optional[str]:
    if doc_path is not None:
        return pathlib.Path(doc_path).read_text(encoding="utf-8")
    return content


def _doc_id_suffix(doc_id: str) -> str:
    return doc_id.rsplit(".", 1)[-1] if "." in doc_id else doc_id.rsplit("-", 1)[-1]


def _group_short_code(group_id: str) -> str:
    return group_id.rsplit(".", 1)[-1] if "." in group_id else group_id.rsplit("-", 1)[-1]


def _doc_code_alternates(code: str) -> set[str]:
    code = code.strip()
    alts = {code}
    m = re.fullmatch(r"(\d+)-([A-Za-z]+)", code)
    if m:
        alts.add(f"{m.group(2)}{m.group(1)}")
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", code)
    if m:
        alts.add(f"{m.group(2)}-{m.group(1)}")
    return alts


def _doc_code_type(code: str) -> Optional[str]:
    code = code.strip()
    m = re.fullmatch(r"\d+-([A-Za-z]+)", code)
    if m:
        return m.group(1)
    m = re.fullmatch(r"([A-Za-z]+)\d+", code)
    if m:
        return m.group(1)
    return None


def _frontmatter_identity_mismatch(
    text: Optional[str],
    *,
    expected_project: str,
    expected_module: str,
    expected_group_id: str,
    expected_doc_type: str,
    expected_doc_id: Optional[str] = None,
    expected_target_id: Optional[str] = None,
) -> Optional[str]:
    """Return an identity mismatch description for a submitted frontmatter, if any.

    The duplicate-body guard catches exact body clones. This guard catches the more
    durable contamination signal: a submitted body whose own YAML frontmatter declares
    another document's identity. Missing identity fields are left to existing document
    validation; present conflicting fields are rejected.
    """
    if not isinstance(text, str) or not text.lstrip().startswith("---"):
        return None
    header, parse_error = _linter.parse_yaml_header(text)
    if parse_error or not isinstance(header, dict):
        return None

    mismatches: list[str] = []

    def _present(value: object) -> Optional[str]:
        if value is None:
            return None
        s = str(value).strip()
        if not s or s in {"<auto>", "<AUTO>", "<Title here>"}:
            return None
        return s

    declared_project = _present(header.get("project"))
    if declared_project and declared_project != expected_project:
        mismatches.append(f"project={declared_project!r} expected {expected_project!r}")

    declared_module = _present(header.get("module"))
    if declared_module and declared_module != expected_module:
        mismatches.append(f"module={declared_module!r} expected {expected_module!r}")

    for key in ("group_id", "group"):
        declared_group = _present(header.get(key))
        if declared_group and declared_group not in {expected_group_id, _group_short_code(expected_group_id)}:
            mismatches.append(f"{key}={declared_group!r} expected {expected_group_id!r}")

    declared_type = _present(header.get("type"))
    if declared_type and declared_type != expected_doc_type:
        mismatches.append(f"type={declared_type!r} expected {expected_doc_type!r}")

    declared_doc_number = _present(header.get("doc_number"))
    if declared_doc_number:
        if expected_doc_id:
            expected_code = _doc_id_suffix(expected_doc_id)
            if declared_doc_number not in _doc_code_alternates(expected_code):
                mismatches.append(
                    f"doc_number={declared_doc_number!r} expected {expected_code!r}"
                )
        else:
            declared_code_type = _doc_code_type(declared_doc_number)
            if declared_code_type and declared_code_type != expected_doc_type:
                mismatches.append(
                    f"doc_number={declared_doc_number!r} has type {declared_code_type!r} "
                    f"expected {expected_doc_type!r}"
                )

    declared_target_id = _present(header.get("target_id"))
    if declared_target_id and expected_target_id and declared_target_id != expected_target_id:
        # 0226 B0001 / NR0003 §3.1: the mention's §2 header instructs the SHORT form
        # ("target_id: B0001") while expected_target_id is the canonical id, so an
        # unmanned worker that followed the instruction verbatim was structurally
        # 409'd. Accept the same alternate spellings doc_number already gets.
        expected_code = _doc_id_suffix(expected_target_id)
        if declared_target_id not in _doc_code_alternates(expected_code):
            mismatches.append(f"target_id={declared_target_id!r} expected {expected_target_id!r}")

    return "; ".join(mismatches) if mismatches else None


def _fail(status: int, message: str, help_url: str | None = None) -> JSONResponse:
    help_url = help_url or _help_url()
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "http_status": status,
            "error_message": message,
            "help_url": help_url,
        },
    )


# ── Corrupted-body guard + body-fingerprint match (0391 B0001 제안3+4, T0005) ──────
# Runs at the real-registration sites (new/edit/review — always *before* the shared
# dry-run short-circuit, same L0007 rule the other Step-5 guards already follow) so a
# rejection never reserves a doc number, consumes a token, or writes to disk.
#
# Two layers over the same text, either can reject:
#   1. fingerprint (제안4): if the sender attached body_sha256/body_chars for the
#      fingerprint_field, a mismatch rejects immediately — the question-mark heuristic
#      is skipped for that field once the fingerprint itself is being trusted/checked.
#   2. line-based corruption (제안3): every other text field (and the fingerprint field
#      when no fingerprint was sent) goes through
#      workflow_decision_service._text_is_corrupted() — kept in that module only, per
#      test_conversation_dry_run_0360.py:196-204's single-definition constraint.
#
# force_encoding_reason is the one escape hatch shared by every path: any non-trivial
# reason (>=10 non-whitespace chars) bypasses both layers unconditionally.
def _encoding_guard(
    *,
    fields: dict[str, Optional[str]],
    fingerprint_field: Optional[str],
    body_sha256: Optional[str],
    body_chars,
    force_encoding_reason: Optional[str],
) -> Optional[JSONResponse]:
    reason = (force_encoding_reason or "").strip()
    if len(reason.replace(" ", "")) >= 10:
        return None

    from modules.flow_gate.services import workflow_decision_service as _wf_decision

    check_fields = dict(fields)
    if fingerprint_field and (body_sha256 or body_chars is not None):
        text = check_fields.pop(fingerprint_field, None) or ""
        actual_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        actual_chars = len(text)
        mismatches = []
        if body_sha256 and str(body_sha256).strip().lower() != actual_sha256:
            mismatches.append(f"sha256 기대={body_sha256} 실제={actual_sha256}")
        if body_chars is not None:
            try:
                if int(body_chars) != actual_chars:
                    mismatches.append(f"글자수 기대={body_chars} 실제={actual_chars}")
            except (TypeError, ValueError):
                mismatches.append(f"body_chars 형식 오류: {body_chars!r}")
        if mismatches:
            return _fail(
                422,
                "본문 지문이 어긋납니다: " + "; ".join(mismatches) + ". 본문을 UTF-8 "
                "파일로 먼저 쓰고 그 파일에서 글자 수와 해시를 구해 다시 보내세요. "
                "정말 이대로 보내야 하면 force_encoding_reason에 사유(공백 제외 10자 "
                "이상)를 적어 다시 보내세요.",
            )

    for name, value in check_fields.items():
        if _wf_decision._text_is_corrupted(value):
            return _fail(
                422,
                f"{name} 항목이 깨진 글자(예: ??????)로 보입니다. 본문을 UTF-8 파일로 "
                "먼저 쓰고, 그 파일에서 글자 수와 해시(body_chars/body_sha256)를 구한 "
                "다음 다시 보내세요. 정말 이대로 보내야 하면 force_encoding_reason에 "
                "사유(공백 제외 10자 이상)를 적어 다시 보내세요.",
            )
    return None


_DESIGN_TEMPLATE_SUBMISSION_COPY = {
    "ko": {
        "mismatch": "설계 문서가 활성 {type_code} 템플릿의 문서 구조와 맞지 않습니다. {details} 작성 전에 {help_path}에서 템플릿을 받고 그 절 이름과 순서를 따르세요.",
        "unavailable": "활성 {type_code} 템플릿을 확인할 수 없어 설계 문서를 안전하게 검증하지 못했습니다. 잠시 후 {help_path} 조회부터 다시 시도하세요.",
        "missing": "누락된 절: {items}.",
        "order": "순서가 어긋난 절: {items}.",
    },
    "en": {
        "mismatch": "The design document does not match the active {type_code} template structure. {details} Fetch the template from {help_path} before writing and follow its section names and order.",
        "unavailable": "The active {type_code} template could not be checked, so the design document cannot be validated safely. Retry by fetching {help_path}.",
        "missing": "Missing sections: {items}.",
        "order": "Sections out of order: {items}.",
    },
    "ja": {
        "mismatch": "設計文書が有効な {type_code} テンプレートの文書構造と一致しません。{details} 作成前に {help_path} からテンプレートを取得し、節名と順序に従ってください。",
        "unavailable": "有効な {type_code} テンプレートを確認できず、設計文書を安全に検証できません。{help_path} の取得から再試行してください。",
        "missing": "不足している節: {items}。",
        "order": "順序が異なる節: {items}。",
    },
}


def _design_template_submission_error(
    *, project: str, doc_type: str, locale: str, content: str
) -> Optional[JSONResponse]:
    """Reject a design body that does not follow the active help template outline."""
    normalized_locale = template_provision.normalize_locale(locale)
    help_path = f"/help/items/design_template/{doc_type}"
    copy = _DESIGN_TEMPLATE_SUBMISSION_COPY[normalized_locale]
    try:
        if not template_provision.is_design_type(doc_type):
            return None
        result = template_provision.validate_design_document_structure(
            project, doc_type, normalized_locale, content
        )
    except Exception:
        import LogAssist.log as logger
        logger.warning(
            f"[inbox] design template validation unavailable for {project}/{doc_type}"
        )
        return _fail(
            503,
            copy["unavailable"].format(type_code=doc_type, help_path=help_path),
            help_url=help_path,
        )
    if result.get("valid"):
        return None
    details: list[str] = []
    missing = list(result.get("missing") or [])
    out_of_order = list(result.get("out_of_order") or [])
    if missing:
        details.append(copy["missing"].format(items=", ".join(missing[:8])))
    if out_of_order:
        details.append(copy["order"].format(items=", ".join(out_of_order[:8])))
    return _fail(
        422,
        copy["mismatch"].format(
            type_code=doc_type, details=" ".join(details), help_path=help_path
        ),
        help_url=help_path,
    )


_TR_SCOPE_META_MAX_PATHS = 50


def _tr_scope_meta(result: dict) -> dict:
    """Trim a tr_scope verdict down to what documents.meta should carry (0299 D0004 §6).

    Keeps the verdict, stage, codes and assignment, plus a bounded slice of each path
    list with its true count so the UI can render "n건" honestly while the stored blob
    stays small. ``reported`` is also an input to later TR evaluations, so it keeps the
    parser's full supported maximum instead of the display-only 50-path limit. `notice`
    is never persisted — it only exists for a rejection, and a rejection has no document
    to persist onto.
    """
    def _slice(key: str, limit: int = _TR_SCOPE_META_MAX_PATHS) -> dict:
        values = list(result.get(key) or [])
        return {"count": len(values), "items": values[:limit]}

    return {
        "verdict": result.get("verdict"),
        "stage": result.get("stage"),
        "codes": result.get("codes") or [],
        "branch": result.get("branch"),
        "scope_reason": result.get("scope_reason"),
        "reported": _slice("reported", tr_scope_service.MAX_ITEMS),
        "detected": _slice("detected"),
        "unconfirmed": _slice("unconfirmed"),
        "unreported": _slice("unreported"),
        "out_of_scope": _slice("out_of_scope"),
        "format_errors": _slice("format_errors"),
    }


def _prior_tr_declared(group_id: str, exclude_doc_id: Optional[str] = None) -> list[str]:
    """Return the union of paths already reported by earlier mutating-type docs in this group.

    0390 TR0005 rev1: this used to hard-compare ``type_code == "TR"``, so a TS
    document's own declared changed-files were invisible to every other document's
    evaluate() call -- the same file could come back flagged "unconfirmed" for a
    sibling TR/TS submission even though a TS already reported it. Widened to the
    same tool_registry.MUTATING_STEP_TYPES membership used everywhere else in this
    gate, so T/TR/TSR/TS all feed and read the same declared-paths pool.
    """
    declared: set[str] = set()
    for document in db_docs.get_documents_by_group_id(group_id):
        if str(document.get("type_code") or "").upper() not in tool_registry.MUTATING_STEP_TYPES:
            continue
        if exclude_doc_id and document.get("doc_id") == exclude_doc_id:
            continue
        verdict = tr_scope_service.verdict_from_meta(document.get("meta"))
        if not verdict:
            continue
        reported = verdict.get("reported")
        if not isinstance(reported, dict):
            continue
        for path in reported.get("items") or []:
            if isinstance(path, str) and path:
                declared.add(path)
    return sorted(declared)


def _disposed_group_fail(group_id: Optional[str], action: str) -> Optional[JSONResponse]:
    """Return a 409 _fail response when group_id's group has been disposed, else None.

    TR0079.0003 rework: the action-bar hiding (rev1) and the workflow/review/decision
    409 guards (rev2) left the INBOX ingestion path open, so a document in a disposed
    group could still be created/edited via a direct inbox submission ("documents in a
    disposed group still get edited fine"). The action bar is UX-only and depends on SSE arriving; a
    stale tab or a worker holding a live token bypasses it entirely. This makes a
    disposed group's documents inert at the ingestion source — the authoritative
    block. Shares process_service.is_group_disposed (the same file-less DC marker the
    dashboard exclusion and the document-detail group_disposed flag key off), so it
    fails open (None) for live groups and on any lookup failure: legitimate work on a
    live group is never blocked.
    """
    if group_id and process_service.is_group_disposed(group_id):
        return _fail(409, f"{action} not allowed: the group has been disposed.")
    return None


# ── Bearer token extraction ──────────────────────────────────────────────────────────

def _extract_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


# ── Group resolution ────────────────────────────────────────────────────────────────

_LEGACY_GROUP_SEQ_RE = re.compile(r"^\d{4}$")
_LEGACY_DOC_CODE_RE = re.compile(r"^[A-Z]+\d{4}$")
_LEGACY_GROUP_ID_RE = re.compile(r"^[a-z0-9_\-]+-__ALL__-\d{4}$")
_LEGACY_DOC_ID_RE = re.compile(r"^[a-z0-9_\-]+-__ALL__-\d{4}-[A-Z]+\d{4}$")


def _normalize_group_name(project_id: str, module: str, group_name: str) -> str:
    if _LEGACY_GROUP_ID_RE.fullmatch(group_name):
        return group_name
    try:
        validate_group_id(group_name)
        return group_name
    except ValueError:
        if _LEGACY_GROUP_SEQ_RE.fullmatch(group_name):
            return f"{project_id}-{module}-{group_name}"
        raise ValueError(f"Invalid group_id format: {group_name!r}")


def _normalize_doc_id(group_id: str, doc_id: str) -> str:
    if _LEGACY_DOC_ID_RE.fullmatch(doc_id):
        return doc_id
    try:
        validate_doc_id(doc_id)
        return doc_id
    except ValueError:
        if _LEGACY_DOC_CODE_RE.fullmatch(doc_id):
            return f"{group_id}-{doc_id}"
        raise ValueError(f"Invalid doc_id format: {doc_id!r}")


def _build_doc_id(group_id: str, doc_code: str) -> str:
    return f"{group_id}.{doc_code}" if "." in group_id else f"{group_id}-{doc_code}"


def _resolve_group(project_id: str, group_name: str) -> Optional[dict]:
    """Return groups record for group_name. group_name is a canonical group_id (T261).

    Title-matching fallback removed (T261 §4 — direct lookup only after validate_group_id passes).
    """
    grp = db_groups.get_by_id(group_name)
    if grp and grp.get("project_id") == project_id:
        return grp
    return None


# ── document_types validation ────────────────────────────────────────────────────────

def _is_valid_doc_type(doc_type: str, project_id: str) -> bool:
    store = get_store()
    row = store._fetch_one(
        "SELECT 1 FROM document_types "
        "WHERE type_code = ? AND (project_id IS NULL OR project_id = ?) AND is_active = 1",
        [doc_type, project_id],
    )
    return row is not None


# ── resolve_storage_path ──────────────────────────────────────────────────────

def _resolve_storage_path(
    project_id: str,
    module: str,
    group: dict,
    doc_id: str,
    branch: str = "main",
    doc_type: Optional[str] = None,
) -> pathlib.Path:
    """Return canonical storage path using the D013 §5 pattern.

    The filename is composed of a short doc_code (`{seq}-{type}`) prefix plus `document.md`
    (consistent with other callers, e.g. `0002-M_document.md`).
    If a canonical full ID is given, the group prefix is stripped to use only the short code.

    A work plan (WP) is the one type whose canonical body is not prose: it is stored as
    `..._document.json` (P0009 §2.6 결정 2). Giving a JSON body a `.md` name would make the
    viewer, search and backup all treat it as text.
    """
    group_code = group["group_id"]
    if doc_id.startswith(group_code + "."):
        doc_code = doc_id[len(group_code) + 1:]
    elif doc_id.startswith(group_code + "-"):
        doc_code = doc_id[len(group_code) + 1:]
    else:
        doc_code = doc_id
    filename = "document.md"
    if (doc_type or "").upper() == WORK_PLAN_TYPE:
        filename = work_plan_service.DOCUMENT_FILENAME
    return document_path(
        project_id=project_id,
        group_code=group_code,
        doc_code=doc_code,
        filename=filename,
        module=module,
        branch=branch,
    )


# ── Main endpoint ────────────────────────────────────────────────────────────

@router.post("/inbox")
async def inbox(request: Request):
    """Inbound endpoint (D020 §3).

    Step 1: Form validation
    Step 2: Authentication (token verification)
    Step 3: Context binding validation
    Step 4: Permission check
    Step 5: Referential integrity + body validation
    Step 6: Storage processing
    Step 7: DB registration/update
    Step 8: Token consumption
    Step 9: Screen push [Phase 2 deferred — leave hook empty]
    Step 10: Return response
    """
    # ── Step 1: Form validation ─────────────────────────────────────────────────────
    raw = _extract_bearer(request)
    if raw is None:
        return _fail(401, "Authorization header is required")

    try:
        body = await request.json()
    except Exception:
        return _fail(400, "Request body is not valid JSON")

    action = body.get("action", "")
    # "test_run" was dispatched below but missing from this allowlist, so the inbox
    # rejected every action:test_run POST with 400 before reaching _handle_test_run
    # (group 0150 NR0003 §4 — the chain entrance was dead code).
    if action not in ("new", "edit", "review", "test_run"):
        return _fail(400, "action must be new, edit, review, or test_run")

    if action == "new":
        return await anyio.to_thread.run_sync(_handle_new, request, raw, body)
    elif action == "review":
        return await anyio.to_thread.run_sync(_handle_review, request, raw, body)
    elif action == "test_run":
        return await anyio.to_thread.run_sync(_handle_test_run, request, raw, body)
    else:
        return await anyio.to_thread.run_sync(_handle_edit, request, raw, body)


def _handle_test_run(request: Request, raw_token: str, body: dict) -> JSONResponse:
    """Start a TS test run through a test_run-scoped worker token."""
    from modules.flow_gate.services import test_run_service

    project = body.get("project")
    doc_id = body.get("doc_id") or body.get("doc_ref") or body.get("target_id")
    if not project:
        return _fail(400, "Required field missing: project")
    if not doc_id:
        return _fail(400, "Required field missing: doc_id")

    try:
        token_rec = token_service.verify(raw_token)
    except HTTPException as exc:
        return _fail(exc.status_code, str(exc.detail))

    if token_rec.get("action_scope") != "test_run":
        return _fail(403, "Context binding mismatch. Use the correct token.")
    if token_rec.get("project") != project or token_rec.get("doc_ref") != doc_id:
        return _fail(403, "Context binding mismatch. Use the correct token.")
    # Same semantics as the UI routes (is_admin bypass OR RBAC): a raw has_permission
    # here re-created the 0086 trap — live RBAC tables are unpopulated, so the admin
    # issuer's chain token 403'd at the very entrance it was minted for (group 0150).
    if not test_run_service.token_can_run_tests(token_rec["issued_to"], project):
        return _fail(403, "Permission denied: perm_test_run required")

    doc = db_docs.get_by_id(str(doc_id))
    if doc is None:
        return _fail(404, f"Document {doc_id} does not exist")

    dry_resp = _maybe_dry_run(
        body,
        token_rec,
        {
            "action": "test_run",
            "doc_id": doc_id,
            "checks_passed": ["auth", "context_binding", "permission", "referential_integrity"],
        },
    )
    if dry_resp is not None:
        return dry_resp

    try:
        result = test_run_service.validate_and_create_run(
            doc_id=str(doc_id),
            runner_id=token_rec["issued_to"],
            triggered_via="token",
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {"error_message": str(exc.detail)}
        return JSONResponse(status_code=exc.status_code, content=detail)

    token_service.consume(
        token_id=token_rec["token_id"],
        project_id=project,
        doc_id=str(doc_id),
    )
    # Unmanned-chain hand-off (group 0150): a continuation-carrying test_run token was
    # minted by advance_workflow's TSR-head wiring, not the manned test-run-request path.
    # The run is async, so no next token can ride on this 202 — the worker's part of the
    # chain ends at this POST. Server side takes over: all-green auto-assembles the TSR
    # and passes the gate itself (test_run_service._maybe_chain_auto_approve_tsr); on
    # failure the chain pauses for a human. Say so explicitly instead of leaving the
    # chain dangling on a bare run receipt.
    if token_rec.get("continuation_target_seq") is not None:
        result = {
            **result,
            "continuation": True,
            "continuation_async": True,
            "continuation_target_seq": token_rec.get("continuation_target_seq"),
            "message": (
                f"{result.get('message', '')} Continuous chain hand-off complete: FlowGate "
                "now executes the TS server-side. On all-green the TSR is auto-assembled "
                "and auto-approved; on failure the chain pauses for a human. Do NOT write "
                "the TSR yourself — your chain step ends here."
            ).strip(),
        }
    return JSONResponse(status_code=202, content=result)


def _handle_review(request: Request, raw_token: str, body: dict) -> JSONResponse:
    """Store an AI review result for action: review (document_reviews child record).

    A review belongs to its target document rather than being a document itself.
    Collection is automatic, while a person makes the approval or rejection decision.
    Payload: {project, doc_id, verdict(pass|issues|hold), findings:[{locus,note}], comment?}
    The server derives the finding count from findings; the AI does not provide the number.
    """
    from modules.flow_gate.db import document_reviews as db_reviews

    # ── Step 1: Field validation ──
    project = body.get("project")
    doc_id_raw = body.get("doc_id") or body.get("doc_ref") or body.get("target_id")
    verdict = body.get("verdict")
    findings = body.get("findings", [])
    comment = body.get("comment")

    # 0393 T0005 §2-7: a verdict may now arrive as a FILE. The overall comment is long
    # Korean prose, and squeezing it through a command line is how it gets mangled into the
    # ???? the encoding guard then rejects. Document registration has had `doc_path` since
    # D020 §7-5; review is the last submission that did not.
    #
    # Deliberate scope note: only the doc_path × content pair is exclusive. Sending NEITHER
    # stays legal and keeps meaning "the verdict is inline in this request" — that is the
    # shape the [멘트 복사] review path uses, and T0005 §1 puts it out of bounds ("지금도
    # 정상이므로 손대지 말 것"). A request with neither a source nor an inline verdict still
    # fails, on the verdict check below.
    review_doc_path: Optional[str] = body.get("doc_path")
    review_content: Optional[str] = body.get("content")
    if review_doc_path is not None and review_content is not None:
        return _fail(400, "Exactly one of doc_path or content must be provided")
    external_source = review_doc_path is not None or review_content is not None

    if not project:
        return _fail(400, "Required field missing: project")
    if not doc_id_raw:
        return _fail(400, "Required field missing: doc_id")
    if not external_source:
        if verdict not in ("pass", "issues", "hold"):
            return _fail(400, "verdict must be one of: pass, issues, hold")
        if not isinstance(findings, list):
            return _fail(400, "findings must be a list")
    project = str(project)
    doc_id = str(doc_id_raw)

    # ── Step 2: Authentication ──
    try:
        token_rec = token_service.verify(raw_token)
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)

    # ── Step 3: Context binding (scope + project + target document) ──
    # Scope guard mirrors _handle_new/_handle_edit: a review submission requires a
    # review-scoped token. This is what stops an "edit" token from being replayed as
    # a review (and, conversely, a review token from editing) — B0057.0001/NR0057.0003.
    if token_rec["action_scope"] != "review":
        return _fail(403, "Context binding mismatch. Use the correct token.")
    if token_rec["project"] != project:
        return _fail(403, "Context binding mismatch. Use the correct token.")
    if token_rec.get("doc_ref") not in (None, "", doc_id):
        return _fail(403, "Context binding mismatch. Use the correct token.")

    # ── Step 3.5: load the verdict from its file / inline blob (0393 T0005 §2-7) ──
    # Same three failure messages as _handle_new/_handle_edit, and the same two reusable
    # pieces (validate_doc_path + _submission_text) — a second implementation of "is this
    # path inside the scratch dir" is exactly how one of them drifts open.
    if external_source:
        if review_doc_path is not None:
            scratch_dir = token_rec.get("scratch_dir") or _token_scratch_dir(
                token_rec["project"], token_rec["token_id"]
            )
            if not validate_doc_path(review_doc_path, scratch_dir):
                return _fail(422, f"doc_path is not accessible: {review_doc_path}")
            if not os.path.isfile(review_doc_path):
                return _fail(422, f"doc_path file does not exist: {review_doc_path}")
        try:
            raw_payload = _submission_text(review_doc_path, review_content)
        except OSError as exc:
            return _fail(422, f"doc_path is not readable: {review_doc_path} ({exc})")
        try:
            parsed_payload = json.loads(raw_payload or "")
        except ValueError as exc:
            return _fail(400, f"Review payload is not valid JSON: {exc}")
        if not isinstance(parsed_payload, dict):
            return _fail(400, "Review payload must be a JSON object")
        # The file wins for the verdict itself; project/doc_id stay bound to what the
        # request declared, because the token was already checked against those two.
        merged = {k: v for k, v in body.items() if k not in ("doc_path", "content")}
        merged.update(parsed_payload)
        body = merged
        verdict = body.get("verdict")
        findings = body.get("findings")
        comment = body.get("comment")
        if findings is None:
            findings = []
        if verdict not in ("pass", "issues", "hold"):
            return _fail(400, "verdict must be one of: pass, issues, hold")
        if not isinstance(findings, list):
            return _fail(400, "findings must be a list")

    actor_user_id: str = token_rec["issued_to"]

    # ── Step 4: Permission ──
    if not has_permission(actor_user_id, project, "perm_document_update"):
        return _fail(403, "Insufficient permissions for this operation")

    # ── Step 5: Referential integrity ──
    doc = db_docs.get_by_id(doc_id)
    if doc is None:
        return _fail(422, f"Referenced document {doc_id} does not exist")

    # Disposed-group guard (TR0079.0003 rework): a review is a forward action on the
    # target document; reject it when the group has been discarded.
    disposed = _disposed_group_fail(doc.get("group_id"), "Review")
    if disposed is not None:
        return disposed

    # ── Step 5.9: 깨진 글자 실등록 차단 + 본문 지문 대조 (0391 B0001 제안3+4, T0005 §5-3/§6) ──
    _review_encoding_fields: dict[str, Optional[str]] = {}
    if isinstance(comment, str) and comment:
        _review_encoding_fields["comment"] = comment
    for _fi, _finding in enumerate(findings):
        if isinstance(_finding, dict):
            if _finding.get("note"):
                _review_encoding_fields[f"findings[{_fi}].note"] = _finding.get("note")
            if _finding.get("locus"):
                _review_encoding_fields[f"findings[{_fi}].locus"] = _finding.get("locus")
    _review_encoding_fail = _encoding_guard(
        fields=_review_encoding_fields,
        fingerprint_field="comment",
        body_sha256=body.get("body_sha256"),
        body_chars=body.get("body_chars"),
        force_encoding_reason=body.get("force_encoding_reason"),
    )
    if _review_encoding_fail is not None:
        return _review_encoding_fail

    # ── Dry-run short-circuit (R0001 dry-run, L0007 §3/§4.3) ──
    # All validation has passed; bail out before any side effect (insert_review/consume/SSE).
    dry_resp = _maybe_dry_run(body, token_rec, {
        "action": "review",
        "doc_id": doc_id,
        "verdict": verdict,
        "finding_count": len(findings),
        "checks_passed": ["auth", "context_binding", "permission", "referential_integrity"],
    })
    if dry_resp is not None:
        return dry_resp

    # ── Step 6: Insert review record ──
    revision_no = int(doc.get("revision_no") or 0)
    findings_json = json.dumps(findings, ensure_ascii=False)
    try:
        db_reviews.insert_review(
            doc_id=doc_id,
            revision_no=revision_no,
            reviewer_id=actor_user_id,
            verdict=verdict,
            findings_json=findings_json,
            comment=comment if (comment is None or isinstance(comment, str)) else str(comment),
            reviewed_at=now_iso(),
        )
    except Exception as exc:
        return _fail(500, f"DB registration error: {exc}")

    # ── Step 7: Token consumption ──
    token_service.consume(
        token_id=token_rec["token_id"],
        project_id=project,
        doc_id=doc_id,
    )

    # ── Step 8: Screen push — notify reviewers an AI verdict arrived ──
    # Without this, a review lands silently: the human's open tab shows no "AI review arrived"
    # pill and no refresh (review submission is not a doc_review_status change, so it was
    # emitting nothing). Broadcast (audience="*") since the reviewer may be a different user.
    try:
        from modules.flow_gate.api.v1.events.publisher import broadcast_event_threadsafe, FlowEvent
        from modules.flow_gate.api.v1.events.event_types import EventType
        group_id_val = doc.get("group_id")

        def _push():
            broadcast_event_threadsafe(FlowEvent(
                event_type=EventType.AI_REVIEW_ARRIVED,
                payload={
                    "doc_id": doc_id,
                    "title": doc.get("title"),
                    "verdict": verdict,
                    "finding_count": len(findings),
                },
                audience="*",
                doc_id=doc_id,
                project=project,
                group_id=group_id_val,
            ))
            # Refresh open document tabs / group view so the pill surfaces without a
            # manual reload (rides the fg:open_docs_refresh path on the client).
            broadcast_event_threadsafe(FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": group_id_val, "reason": "review_added"},
                audience="*",
                doc_id=doc_id,
                project=project,
                group_id=group_id_val,
            ))

        # 0279 T0007: _push is now synchronous and the *_threadsafe publishers
        # schedule onto the captured main loop themselves, so the old
        # get_event_loop/ensure_future/asyncio.run dance is gone. That dance was
        # also unsound from a worker thread: it would have built a NEW loop and
        # published into queues owned by the main loop, delivering nothing —
        # exactly the failure publish_event_threadsafe was written to fix.
        _push()
    except Exception as _push_exc:
        import LogAssist.log as logger
        logger.warning(f"[inbox review] Step 9 SSE publish failed (ignored): {_push_exc}")

    # ── Step 9: Response (finding count computed server-side) ──
    return JSONResponse(content={
        "ok": True,
        "doc_id": doc_id,
        "verdict": verdict,
        "finding_count": len(findings),
        "message": f"Review for {doc_id} registered.",
    }, status_code=201)


def _inbox_api_base(request: Request) -> str:
    """API base URL for self-chain mentions (mirrors token_routes._build_api_base)."""
    from config import settings
    base = str(request.base_url).rstrip("/")
    context = settings.CONTEXT.rstrip("/")
    return f"{base}{context}/api/v1"


def _normalize_continuation_target(
    target_seq: int,
    instruction_mode: Optional[str],
    completed_item: Optional[dict],
    wfseq,
) -> int:
    """Return the effective continuous boundary for the selected instruction mode.

    In ``auto_approved`` mode N/T are generated and approved by the server without an AI
    worker. A target on either instruction therefore means its paired AI report (NR/TR);
    otherwise the automatic instruction advance necessarily crosses the displayed boundary
    before the first worker can submit anything. ``ai_direct`` keeps N/T as independent AI
    targets. Any legacy/incomplete sequence data falls back to the original boundary.
    """
    if (instruction_mode or "auto_approved") != "auto_approved":
        return target_seq
    sequence_id = (completed_item or {}).get("sequence_id")
    if sequence_id is None:
        return target_seq

    try:
        items = wfseq.get_sequence_items(sequence_id)
    except Exception:
        return target_seq

    target_idx = next(
        (idx for idx, item in enumerate(items) if item.get("item_seq") == target_seq),
        None,
    )
    if target_idx is None:
        return target_seq

    instruction_type = str(items[target_idx].get("type") or "").upper()
    report_type = {"N": "NR", "T": "TR"}.get(instruction_type)
    if report_type is None or target_idx + 1 >= len(items):
        return target_seq

    paired = items[target_idx + 1]
    if str(paired.get("type") or "").upper() != report_type:
        return target_seq
    try:
        return int(paired["item_seq"])
    except (KeyError, TypeError, ValueError):
        return target_seq


# ── Continuous-chain stop signalling (0359 L0007 §2.11 / §2.12) ────────────────────────
#
# Every stop below used to say WHY in one place only: the HTTP response body, whose only reader
# is a worker about to exit — which is how NR0003 §4's chains died with the explanation still
# inside them. The code, the tag on the live run and the human's notification are now stamped
# by ai_invoke_service.stamp_chain_stop; what stays here is the sentence the worker reads.
#
# L0007 §2.12 — the fixed English sentences A~D. These OVERRIDE the mention:
# the mention still says "do not stop, continue with the enclosed token", and that instruction
# is only valid when a token was in fact enclosed. When the response says the chain ends here,
# the response wins — which is the contradiction these sentences exist to settle.
_CHAIN_END_TRIAGE_MESSAGE = (
    "{doc_id} registered, but the continuous chain STOPPED here. A human must triage. "
    "Do NOT continue and do NOT retry — end this session now."
)
_CHAIN_END_MESSAGES = {
    "hop_handoff": (
        "{doc_id} registered. Your chain step ends here: FlowGate starts the next step with a "
        "fresh worker. Do NOT wait for a next token and do NOT write the next document "
        "yourself — end this session now."
    ),
    "chain_completed": (
        "{doc_id} registered. The continuous run reached its target step and is COMPLETE. "
        "No further token will be issued — do NOT continue, do NOT write another document. "
        "End this session now."
    ),
    "head_slot_mismatch": _CHAIN_END_TRIAGE_MESSAGE,
    "approve_denied": _CHAIN_END_TRIAGE_MESSAGE,
    "approve_failed": _CHAIN_END_TRIAGE_MESSAGE,
    "advance_blocked": _CHAIN_END_TRIAGE_MESSAGE,
    "review_hold": (
        "{doc_id} registered. Review mode: the run waits for the human go before advancing. "
        "Do NOT continue — end this session now."
    ),
    # Not in §2.12's table, which lists only the branches that stop on their own account. A
    # user pause stops the worker just as hard, and leaving it on the default "You may end the
    # session." would leave the mention's "keep going" standing unopposed.
    "user_paused": (
        "{doc_id} registered. The continuous run is PAUSED by the user at this step boundary. "
        "No token is issued — do NOT continue and do NOT write another document. End this "
        "session now; a human resumes the chain from the miniplayer."
    ),
}


def _chain_message(chain: dict, doc_id: str) -> Optional[str]:
    """Which sentence the worker gets, in L0007 §2.12's exact order.

    A token in hand outranks everything — a semi-manned chain really does have to keep going.
    Otherwise, if the chain stopped, say so: the default "You may end the session." is far too
    mild to stand against a mention that spends four bullet points saying "do NOT stop".
    Returns None when neither applies, leaving that default in place.
    """
    if chain.get("next_token"):
        return (f"{doc_id} registered. Continuous run: proceed to the next "
                "step with next_token/next_mention.")
    sentence = _CHAIN_END_MESSAGES.get(chain.get("continuation_stop_code"))
    return sentence.format(doc_id=doc_id) if sentence else None


def _continuation_self_chain(
    request: Request,
    token_rec: dict,
    project: str,
    canonical_doc_id: str,
    doc_type: str,
) -> Optional[dict]:
    """Server-driven self-chaining for continuous (unmanned) work (group 0051 / NR0003 B안).

    After an inbox `new` submission consumes a *continuation* token, decide what the
    worker does next without a human re-issuing a token:

      • Ordinary token (no continuation_target_seq) → return None: nothing changes.
      • Review mode (group 0086 TR0004 rework rev4) → PAUSE. Review mode is the pre-flight
        Q-registration phase, not "go": it never auto-approves or advances. The worker
        registers clarifying Qs (or a "no blockers, confirm to proceed" Q) and the run waits
        for the human go (review mode off → non-review auto-run).
      • Non-review (go) → auto-approve the just-submitted document (NR0003 §6-①: the
        deliberate gate relaxation the FE warning dialog made the user accept) so the head
        advances, then, if the target is reached, stop with the last step already approved
        (point 2: "최종승인 전까지이므로 마지막 작업도 승인한 상태가 되어야 함"); otherwise advance_workflow
        mints the next token + continuous mention. Auto-approve uses the issuer's REAL
        document.approve permission (P0005 §4 — approve is never bypassed); if they genuinely
        lack it, pause honestly instead of forcing it.

    Returns an envelope merged into the inbox 201 response, or None for ordinary tokens.
    Any failure degrades to a paused envelope (the submitted document is already saved;
    only the *continuation* stops), never a 500.
    """
    target_seq = token_rec.get("continuation_target_seq")
    if target_seq is None:
        return None  # ordinary token — not a continuation chain

    review_mode = bool(token_rec.get("continuation_review_mode"))
    instruction_mode = token_rec.get("continuation_instruction_mode")
    # 0352 T0004 §3.4: db/tokens.py decodes this back to a list on every read, so the
    # consumed token record already carries a plain list (possibly empty) here.
    auto_approve_item_seqs = token_rec.get("continuation_auto_approve_item_seqs") or []
    spine_doc_ref = token_rec.get("doc_ref")
    actor_user_id = token_rec["issued_to"]
    chain_group = token_rec.get("group_id")

    envelope: dict = {
        "continuation": True,
        "continuation_review_mode": review_mode,
        "continuation_target_seq": target_seq,
        "continuation_instruction_mode": instruction_mode or "auto_approved",
        "continuation_auto_approve_item_seqs": auto_approve_item_seqs,
    }

    def _stop(stop_code: str, *, detail: Optional[str] = None,
              item_seq: Optional[int] = None) -> dict:
        """Every branch below that does not hand out a token ends through here (§2.12)."""
        from modules.flow_gate.services import ai_invoke_service as _ai_invoke
        return _ai_invoke.stamp_chain_stop(
            envelope,
            stop_code,
            project_id=project,
            group_id=chain_group,
            actor_user_id=actor_user_id,
            anchor_doc_id=canonical_doc_id,
            token_id=token_rec.get("token_id"),
            item_seq=item_seq,
            detail=detail,
        )

    # Review mode is the PRE-FLIGHT Q-registration phase, not "go" (group 0086 TR0004 rework
    # rev4: "검토모드=아직 go가 아니라 사전 질의등록 시간"). It must NOT auto-approve or advance:
    # the worker registers clarifying Qs (or a "no blockers, confirm to proceed" Q) and the run
    # waits for the human to give the explicit go (turn review mode off → non-review auto-run).
    # The review-phase mention already tells the worker not to create documents, so reaching
    # this self-chain in review mode is unexpected; pause honestly rather than producing work.
    if review_mode:
        envelope["continuation_paused"] = True
        envelope["continuation_reason"] = (
            "review mode (pre-flight Q phase): register clarifying questions; the run advances "
            "only after the human gives the go."
        )
        return _stop("review_hold")

    from modules.flow_gate.db import workflow_sequences as _wfseq
    completed_item = _wfseq.get_item_by_result_doc_id(canonical_doc_id)
    completed_seq = completed_item.get("item_seq") if completed_item else None
    target_seq = _normalize_continuation_target(
        target_seq,
        instruction_mode,
        completed_item,
        _wfseq,
    )
    envelope["continuation_target_seq"] = target_seq

    # 0226 B0001 / NR0003 §1.2 (§5-3): a submission that did NOT fill the current head
    # slot (e.g. a doc_type differing from the head) proves no progress toward the
    # target — the old flow skipped the target-reached check below entirely and
    # advanced anyway, minting another token every hop past the target (the actual
    # "endless run"). Pause honestly instead: the stray document stays saved (and
    # unapproved — this runs before the auto-approve) for the human to triage.
    if completed_seq is None:
        envelope["continuation_paused"] = True
        envelope["continuation_reason"] = (
            f"submitted document ({doc_type}) did not fill the current workflow head "
            "slot; progress toward the target cannot be verified, so the chain pauses "
            "instead of advancing."
        )
        return _stop("head_slot_mismatch")

    # Auto-approve the just-submitted document so the head can advance — and do it BEFORE the
    # target-reached check so the LAST step is left approved too (group 0086 TR0004 rework
    # rev4 point 2: "최종승인 전까지이므로 마지막 작업도 승인한 상태가 되어야 함"). The continuous run
    # runs up to the last sequence item; final approval (AC) remains a separate human gate.
    from modules.flow_gate.documents.routers.documents import AUTO_COMPLETE_TYPES
    if doc_type.upper() not in AUTO_COMPLETE_TYPES:
        # Permission source of truth = the SAME resolver the live approve button and
        # documents.create_next_approved use (workflow._get_user_permissions, the is_admin
        # stub). The real RBAC tables (user_project_roles / role_permissions) are
        # unpopulated on the live system, so permission_service.get_user_permissions
        # returns ∅ for an is_admin approver → this auto-approve ALWAYS paused with "lacks
        # document.approve" even though the human IS an approver. That is the reported
        # "continuous run stops at the work instruction" bug (group 0086 TR0004 rework).
        # documents.py:create_next_approved warns both approval paths must move together;
        # this is the second path, previously left on the wrong (∅) resolver.
        from modules.flow_gate.db import users as _db_users
        from modules.flow_gate.workflow.routers.workflow import (
            _get_user_permissions as _resolve_user_permissions,
        )
        actor_user = _db_users.get_by_id(actor_user_id) or {
            "user_id": actor_user_id, "is_admin": 0,
        }
        approver_perms = _resolve_user_permissions(actor_user)
        if "document.approve" not in approver_perms:
            envelope["continuation_paused"] = True
            envelope["continuation_reason"] = (
                "issuer lacks document.approve; awaiting human approval before continuing."
            )
            return _stop("approve_denied")
        from modules.flow_gate.workflow.pipeline_service import transition_document_review
        try:
            transition_document_review(
                doc_id=canonical_doc_id,
                action="approve",
                actor_user_id=actor_user_id,
                user_permissions=approver_perms,
            )
        except Exception as exc:  # noqa: BLE001 — never 500 the saved submission
            envelope["continuation_paused"] = True
            envelope["continuation_reason"] = f"auto-approve failed: {exc}"
            return _stop("approve_failed", detail=str(exc))

    # Target reached → stop the chain. Reached only AFTER the just-submitted document was
    # auto-approved above, so the last step ends approved (point 2), not left submitted.
    if completed_seq is not None and completed_seq >= target_seq:
        envelope["continuation_remaining"] = 0
        envelope["continuation_done"] = True
        # 0252 L0009 §3: a pause that never got its boundary (the target landed first)
        # must not survive chain termination — a leftover row would revive a ghost
        # "paused" card on the next miniplayer bootstrap. Best-effort, like the signal.
        try:
            from modules.flow_gate.db import ai_invoke_paused_chains as _db_paused
            if chain_group:
                _db_paused.delete_by_group(chain_group)
        except Exception:
            import LogAssist.log as logger
            logger.warning("[inbox] paused-row cleanup on chain end failed (ignored)")
        # R0001 group 0125 / NR0003 권고 1: record the explicit "연속작업 종료" signal that the
        # system previously lacked entirely (NR0003 §발견 3). State-board aggregation only —
        # never a notification-feed event. Best-effort: a logging failure must not turn the
        # already-saved continuous run's clean stop into an error.
        try:
            from modules.flow_gate.workflow import event_logger as _event_logger
            ended_doc = db_docs.get_by_id(canonical_doc_id) or {}
            ended_pk = ended_doc.get("id")
            if ended_pk is not None:
                _event_logger.log_continuous_work_ended(
                    project_id=project,
                    actor_user_id=actor_user_id,
                    document_id=ended_pk,
                    doc_id=canonical_doc_id,
                    group_id=ended_doc.get("group_id"),
                    target_seq=target_seq,
                )
        except Exception as _sig_exc:  # noqa: BLE001 — best-effort state signal
            import LogAssist.log as logger
            logger.warning(
                f"[inbox] continuous_work_ended signal failed (ignored): {_sig_exc}"
            )
        return _stop("chain_completed", item_seq=completed_seq)

    # Boundary pause check (group 0252 L0009 §2.2): evaluated once, right before the next
    # token would be minted, and BEFORE the advance-blocked pause below so a user pause is
    # never mis-reported as a generic block. Runs AFTER the auto-approve so the finished
    # step ends approved — "진행 중 단계는 끝까지" includes its approval (P0008 S4/S5). The
    # row is NOT deleted here; the resume path consumes it (L0009 §2.4). Fail-open on a
    # lookup error: a pause probe must never stall a healthy unmanned chain with a 500.
    try:
        from modules.flow_gate.services import ai_invoke_service as _ai_invoke
        _user_paused = bool(
            chain_group
            and _ai_invoke.mark_user_paused(chain_group, token_rec.get("ai_run_id"))
        )
    except Exception:
        import LogAssist.log as logger
        logger.warning("[inbox] boundary pause identity probe failed (ignored)")
        _user_paused = False
    if _user_paused:
        envelope["continuation_paused"] = True
        envelope["continuation_reason"] = (
            "paused by user at the step boundary; resume from the miniplayer "
            "(or the answer/ment-copy path) to continue."
        )
        return _stop("user_paused", item_seq=completed_seq)

    # 0317 TR0011 (Q153 opt-1): HOW the chain continues depends on who drives it.
    #  • Engine-driven unmanned run (a live start_run worker exists for this group): do NOT
    #    hand the next token to the running worker — that single provider process would write
    #    every remaining hop, pinning the whole chain to hop-1's provider (the exact rejection:
    #    3 documents, all "Anthropic Claude Sonnet 5"). Instead queue the next hop and withhold
    #    next_token so this hop's worker stops; the engine re-spawns a fresh worker for the next
    #    hop once this one settles, and that start_run re-resolves the hop's OWN provider.
    #  • Copy-mention semi-manned run (no engine worker to re-spawn): keep minting next_token so
    #    the human's external AI self-continues exactly as before.
    from modules.flow_gate.services import ai_invoke_service as _ai_invoke
    if _ai_invoke.has_active_run(chain_group):
        # Snapshot the current hop before queueing the next one. The worker will emit its
        # ordinary ai_invoke_finished event before the replacement worker can start, so the
        # browser needs this explicit handoff marker to keep the chain-level monitor live
        # across that intentional run-id boundary (0345 B0001).
        _handoff_status = _ai_invoke.get_active_status(chain_group)
        _ai_invoke.request_auto_resume(chain_group, {
            "doc_ref": spine_doc_ref,
            "target_seq": target_seq,
            "review_mode": review_mode,
            "instruction_mode": instruction_mode,
            # 0352 T0004 §3.4/§3.5: carry the selection forward the same way instruction_mode
            # already does, so a re-spawned hop (_spawn_auto_resume) re-applies it.
            "auto_approve_item_seqs": auto_approve_item_seqs,
            "locale": (
                token_rec.get("continuation_locale")
                or request.headers.get("x-locale")
                or "ko"
            ),
            "issued_to": actor_user_id,
            "api_base_url": _inbox_api_base(request),
        })
        # No next_token: the worker stops after this hop; the engine re-spawns the next hop's
        # worker (re-resolving its provider) once this one settles. The step just completed is
        # already auto-approved above, so advance_workflow at re-spawn finds the next head.
        envelope["continuation_pending"] = True
        envelope["continuation_respawn"] = True
        # L0007 §2.12: the next hop is BY DEFINITION the slot after the one just filled, so
        # this is stated rather than re-queried — and the worker can read where the chain went
        # from the response alone instead of inferring it from the mention.
        envelope["continuation_completed_item_seq"] = completed_seq
        envelope["continuation_next_item_seq"] = completed_seq + 1
        # Reuse the existing lifecycle channel so deployed clients that do not know the
        # marker simply refresh the same running run, while MainPanel can distinguish this
        # from a real start through continuation_pending. Best-effort: the queue above is the
        # source of truth and an SSE delivery failure must never stop unattended work.
        try:
            from modules.flow_gate.api.v1.events.publisher import (
                FlowEvent,
                broadcast_event_threadsafe,
            )
            from modules.flow_gate.api.v1.events.event_types import EventType

            _handoff_run_id = _handoff_status.get("run_id")
            if _handoff_run_id:
                broadcast_event_threadsafe(FlowEvent(
                    event_type=EventType.AI_INVOKE_STARTED,
                    payload={
                        "run_id": _handoff_run_id,
                        "group_id": chain_group,
                        "doc_ref": _handoff_status.get("doc_ref") or spine_doc_ref,
                        "mode": "continuous",
                        "status": "running",
                        "docs_target": _handoff_status.get("docs_target"),
                        "docs_reached_so_far": _handoff_status.get("docs_reached_so_far"),
                        "chain_id": _handoff_status.get("chain_id"),
                        "chain_docs_target": _handoff_status.get("chain_docs_target"),
                        "chain_docs_reached": _handoff_status.get("chain_docs_reached"),
                        "provider": _handoff_status.get("provider"),
                        "attempt_no": _handoff_status.get("attempt_no"),
                        "started_at": _handoff_status.get("started_at"),
                        "elapsed_ms": _handoff_status.get("elapsed_ms"),
                        "continuation_pending": True,
                        "continuation_completed_doc_id": canonical_doc_id,
                        "continuation_completed_item_seq": completed_seq,
                        "continuation_target_seq": target_seq,
                    },
                    audience="*",
                    project=project,
                    group_id=chain_group,
                    doc_id=spine_doc_ref,
                ))
        except Exception as _handoff_exc:  # noqa: BLE001 — presentation signal only
            import LogAssist.log as logger
            logger.warning(
                f"[inbox] continuation handoff signal failed (ignored): {_handoff_exc}"
            )
        return _stop("hop_handoff", item_seq=completed_seq)

    # Advance: mint the next step's token + continuous mention (carry the review flag so the
    # next step keeps its review latitude — R0001 [AI 검토 모드] stays on for the whole run).
    from modules.flow_gate.services import workflow_decision_service
    api_base_url = _inbox_api_base(request)
    # Prefer the locale persisted on the continuation token (group 0099 B0001): the unmanned
    # AI worker's inbox POST carries no x-locale header, so relying on the header alone always
    # folded to 'ko' and discarded the dialog-chosen locale on every hop. Legacy tokens
    # (NULL continuation_locale) fall back to the header / 'ko' — prior behavior preserved.
    locale = token_rec.get("continuation_locale") or request.headers.get("x-locale") or "ko"
    try:
        adv = workflow_decision_service.advance_workflow(
            doc_id=spine_doc_ref,
            issued_to=actor_user_id,
            api_base_url=api_base_url,
            locale=locale,
            continuous=True,
            continuation_target_seq=target_seq,
            continuation_review_mode=review_mode,
            continuation_instruction_mode=instruction_mode,
            continuation_auto_approve_item_seqs=auto_approve_item_seqs,
        )
    except (LookupError, ValueError) as exc:
        envelope["continuation_paused"] = True
        envelope["continuation_reason"] = f"advance blocked: {exc}"
        return _stop("advance_blocked", detail=str(exc), item_seq=completed_seq)

    envelope.update({
        "next_token": adv["token"],
        "next_token_id": adv["token_id"],
        "next_mention": adv["mention"],
        "next_expires_at": adv.get("expires_at"),
        "continuation_remaining": adv.get("continuation_remaining"),
    })
    return envelope


def _build_change_summary(**kwargs) -> dict:
    """저장 응답에 붙는 변경 요약 (0370 4세트, P0002 시나리오 14~16).

    **여기서 실패를 오류로 올리면 안 된다.** 이 함수가 불리는 시점에 저장은 이미 끝났고,
    요약은 곁다리다. 실패를 500 으로 되돌리면 작업자는 저장이 실패한 줄 알고 같은 문서를
    또 올린다 — 무인 연속 작업에서 실제로 벌어지는 일이다. 그래서 어떤 예외가 나든
    ``{"changed": null, "error": "summary unavailable"}`` 한 가지로 수렴한다.
    """
    try:
        from modules.flow_gate.services import change_summary_service

        return change_summary_service.build(**kwargs)
    except Exception as exc:  # noqa: BLE001
        import LogAssist.log as logger

        logger.warning(f"[inbox] change summary failed (ignored): {exc}")
        return {"changed": None, "error": "summary unavailable"}


def _handle_new(request: Request, raw_token: str, body: dict) -> JSONResponse:
    """Processing flow for action: new (D020 §3-3-2)."""

    # ── Step 1: Field validation ────────────────────────────────────────────────────
    project = body.get("project")
    module = body.get("module")
    group_raw = body.get("group_name") or body.get("group")
    prev_doc_raw = body.get("prev_doc_id") or body.get("target_id")
    doc_type = body.get("doc_type")

    required_pairs = {
        "project": project,
        "module": module,
        "group_name": group_raw,
        "prev_doc_id": prev_doc_raw,
        "doc_type": doc_type,
    }
    for field, value in required_pairs.items():
        if not value:
            return _fail(400, f"Required field missing: {field}")

    has_path = body.get("doc_path") is not None
    has_content = body.get("content") is not None
    if has_path == has_content:
        return _fail(400, "Exactly one of doc_path or content must be provided")

    project = str(project)
    module = str(module)
    doc_type = str(doc_type)
    try:
        group_name = _normalize_group_name(project, module, str(group_raw))
        prev_doc_id = _normalize_doc_id(group_name, str(prev_doc_raw))
    except ValueError as exc:
        return _fail(422, str(exc))
    doc_path: Optional[str] = body.get("doc_path")
    content: Optional[str] = body.get("content")
    related_doc_ids = body.get("related_doc_ids")
    title_override: Optional[str] = body.get("title")
    # Commit-message draft (flowgate.default.0173 P0003 §1): only meaningful for TR;
    # ignored for other doc types (forward-compat with old instructions). Normalized
    # here; length is validated in Step 5 (before the dry-run short-circuit).
    commit_message_draft: Optional[str] = None
    if doc_type.upper() == "TR":
        commit_message_draft = re.sub(r"\s+", " ", str(body.get("commit_message") or "")).strip() or None

    # NOTE (group 0022 §7.4): the Q document path is retired. Q is an inactive doc type
    # (migration 040) so _is_valid_doc_type blocks new Q docs at Step 5; AI queries are now
    # registered as document-bound data (POST /q/{doc_id}/questions), not Q documents.

    # ── Step 2: Authentication ─────────────────────────────────────────────────────────
    try:
        token_rec = token_service.verify(raw_token)
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)

    # ── Step 3: Context binding ───────────────────────────────────────────────
    if token_rec["project"] != project:
        return _fail(403, "Context binding mismatch. Use the correct token.")
    if token_rec["action_scope"] != "new":
        return _fail(403, "Context binding mismatch. Use the correct token.")
    if token_rec.get("doc_ref") != prev_doc_id:
        return _fail(403, "Context binding mismatch. Use the correct token.")

    actor_user_id: str = token_rec["issued_to"]

    # ── Step 4: Permission check ────────────────────────────────────────────────────
    required_perm = _permission_for_new(doc_type)
    if not has_permission(actor_user_id, project, required_perm):
        return _fail(403, "Insufficient permissions for this operation")

    # ── Step 5: Referential integrity + body validation ──────────────────────────────────────
    if not _is_valid_doc_type(doc_type, project):
        return _fail(400, f"Invalid doc_type: {doc_type}")

    if db_docs.get_by_id(prev_doc_id) is None:
        return _fail(422, f"Referenced document {prev_doc_id} does not exist")

    group = _resolve_group(project, group_name)
    if group is None:
        return _fail(422, f"Group not found: {group_name}")

    # Disposed-group guard (TR0079.0003 rework): no new documents in a discarded group.
    disposed = _disposed_group_fail(group["group_id"], "Creation")
    if disposed is not None:
        return disposed


    if doc_path is not None:
        scratch_dir = token_rec.get("scratch_dir") or _token_scratch_dir(
            token_rec["project"], token_rec["token_id"]
        )
        if not validate_doc_path(doc_path, scratch_dir):
            return _fail(422, f"doc_path is not accessible: {doc_path}")
        if not os.path.isfile(doc_path):
            return _fail(422, f"doc_path file does not exist: {doc_path}")

    # ── Step 5.5: Frontmatter identity + duplicate-body guards ───────────────────
    body_for_guards: Optional[str] = None
    try:
        body_for_guards = _submission_text(doc_path, content)
        identity_mismatch = _frontmatter_identity_mismatch(
            body_for_guards,
            expected_project=project,
            expected_module=module,
            expected_group_id=group["group_id"],
            expected_doc_type=doc_type,
            expected_target_id=prev_doc_id,
        )
        if identity_mismatch is not None:
            return _fail(
                409,
                "Frontmatter identity mismatch: submitted content declares another "
                f"document identity ({identity_mismatch}). Re-send with this "
                "document's own content.",
            )
    except Exception:  # noqa: BLE001 — identity guard is defense-in-depth
        body_for_guards = None

    # Design documents must follow the active template now served by the help item.
    # This is a Step 5 validation, so dry-run and real submission make the same
    # decision before numbering, storage, token consumption, or any other side effect.
    design_error = _design_template_submission_error(
        project=project,
        doc_type=doc_type.upper(),
        locale=token_rec.get("continuation_locale") or request.headers.get("x-locale") or "ko",
        content=body_for_guards or "",
    )
    if design_error is not None:
        return design_error

    # ── Step 5.6: 작업계획(WP) 본문 해석 (P0009 §5, D0007 §2.2) ────────────────
    # doc_type 이 WP 이면 본문은 글이 아니라 정본 JSON 이다. 사람 경로(PUT
    # /documents/{doc_id}/work-plan)와 **같은 검증기**를 부른다 — 두 경로가 각자 검사를
    # 가지면 규칙이 곧 갈라진다(D0007 §2.2). 인박스는 서식만 바꾸고 경로는 그대로 쓰므로
    # (P0009 §5 결정 8) 토큰 검사·번호 발급·워크플로 연결은 아래 흐름을 그대로 탄다.
    #
    # 자리가 중요하다. dry-run 분기보다 *앞*이라 미리보기와 실등록이 같은 판정을 받고,
    # 거부는 numbering/storage 이전이라 문서 번호도 토큰도 소비되지 않는다(P0009 §5.2).
    # 제목과 연결 대상은 인박스 메타데이터가 정본이며(결정 9), 본문에 title 이나
    # parent_doc_id 를 적으면 검증기의 모르는 항목 규칙(unknown_field)에 걸려 거절된다 —
    # 조용히 무시하면 AI 는 자기가 적은 제목이 반영된 줄 안다.
    wp_plan: Optional[dict] = None
    if doc_type.upper() == WORK_PLAN_TYPE:
        wp_locale = work_plan_service.normalize_locale(
            token_rec.get("continuation_locale") or request.headers.get("x-locale")
        )
        wp_raw = body_for_guards
        if wp_raw is None:
            wp_raw = _submission_text(doc_path, content) or ""
        wp_parsed: object = None
        try:
            wp_parsed = json.loads(wp_raw)
        except (json.JSONDecodeError, ValueError) as exc:
            return _fail(
                400,
                work_plan_service.inbox_not_json_message(wp_raw, exc, wp_locale),
                help_url=work_plan_service.HELP_TEMPLATE_PATH,
            )
        if not isinstance(wp_parsed, dict):
            return _fail(
                400,
                work_plan_service.inbox_not_json_message(wp_raw, None, wp_locale),
                help_url=work_plan_service.HELP_TEMPLATE_PATH,
            )
        try:
            wp_plan = work_plan_service.validate(
                wp_parsed, project_id=project, action="create",
            )
        except work_plan_service.WorkPlanValidationError as exc:
            return _fail(
                400,
                work_plan_service.inbox_error_message(exc, wp_locale),
                help_url=work_plan_service.HELP_TEMPLATE_PATH,
            )

    # Refuse a substantial body that is byte-identical to an existing document in a
    # *different* group — the submission-layer contamination signature (correct title,
    # stale/reused body). Runs in Step 5 so a validation *failure* (the 409 below) is
    # returned before the dry-run short-circuit, exactly like the other Step 5 checks
    # (L0007: failures never reach the dry-run path). fingerprint is reused at Step 7
    # to persist the body's hash into meta, making the guard effective going forward.
    # Fail-open on any read/lookup error — the guard must never 500 a real submission.
    fingerprints: dict = {}
    try:
        body_for_fp = body_for_guards if body_for_guards is not None else _submission_text(doc_path, content)
        fingerprints = _content_fingerprints(body_for_fp)
        # 작업계획은 이 가드에서 빠진다. P0009 DEFERRED 가 "계획을 다른 그룹으로 복사해
        # 오는 경로는 만들지 않는다 — 원문 보기의 [복사]와 인박스 생성으로 갈음한다"고
        # 정했으므로, 같은 계획을 다른 그룹에 그대로 넣는 것이 **지원되는 사용법**이다.
        # 같은 본문 판정으로 막으면 설계가 정한 유일한 복사 경로가 막힌다.
        twin = (
            None if wp_plan is not None
            else _find_body_twin(fingerprints, exclude_group_id=group["group_id"])
        )
        if twin is not None:
            return _fail(
                409,
                "Duplicate body: this content matches "
                f"{twin['doc_id']} in another group. The submitted body looks like "
                "stale/reused content (correct title, wrong body). Re-send with this "
                "document's own content.",
            )
    except Exception:  # noqa: BLE001 — guard is defense-in-depth; never 500 a real submission
        fingerprints = {}  # unreadable doc_path / lookup error → skip; Step 6 still proceeds

    # Commit-message draft length guard (flowgate.default.0173 P0003 §1-3): a
    # validation failure, so it returns before the dry-run short-circuit and before
    # any side effect (no document, no token consumed) — exactly like the checks above.
    if commit_message_draft is not None and len(commit_message_draft) > 200:
        return _fail(422, "commit_message must be a single line of at most 200 characters.")

    # ── Step 5.7: TR 작업범위 검증 (0299 D0004) ────────────────────────────────
    # TR 전용. 본문의 `## 변경 파일` 신고 목록을 이 그룹의 워크트리에서 서버가 실제로
    # 관측한 변경과 대조해, 배정 범위 밖에서 이루어진 작업을 제출 시점에 잡는다.
    #
    # 자리 선정이 중요하다. 본문을 다 읽은 뒤(Step 5.5 의 guards 와 같은 재료),
    # 그리고 dry-run 분기보다 *앞*이다. 앞이어야 dry-run 과 실등록이 같은 판정을
    # 받고, 작업자가 본 제출 전에 dry_run 으로 미리 확인할 수 있다(D0004 §3.1).
    # 거부는 numbering/storage/DB 이전이므로 문서 번호도 토큰도 소비되지 않는다.
    #
    # 검증 자체가 실패하면(예상 못 한 예외) 통과시킨다 — 위반 탐지 기능이 정상적인
    # 제출을 500 으로 떨구는 것이 원래 사고보다 나쁘다.
    tr_scope_result: Optional[dict] = None
    if doc_type.upper() in tool_registry.MUTATING_STEP_TYPES:
        try:
            from modules.flow_gate import template_provision as _template_provision

            scope_body = body_for_guards
            if scope_body is None:
                scope_body = _submission_text(doc_path, content)
            # T2/TR2 (NR0003 §1-4): 반려 안내문의 언어. 통로는 이미 있다 — 토큰의
            # continuation_locale(무인 작업자)이 헤더보다 앞선다(0355 L0007 §2-1과
            # 같은 순서, group 0099 B0001 과 같은 이유).
            scope_locale = _template_provision.normalize_locale(
                token_rec.get("continuation_locale") or request.headers.get("x-locale")
            )
            tr_scope_result = tr_scope_service.evaluate(
                project_id=project, group_id=group["group_id"], body=scope_body or "",
                locale=scope_locale,
                prior_declared=_prior_tr_declared(group["group_id"]),
            )
        except Exception:  # noqa: BLE001 — 검증 실패가 TR 접수를 막아선 안 된다
            tr_scope_result = None
        # 검증 비대상(git 연동 꺼짐)은 판정이 아니라 부재다. 이걸 문서 meta 와
        # dry-run 응답에까지 실으면 연동 없는 프로젝트의 모든 TR 에 "검증 안 함"
        # 카드가 붙는다 — 아무도 고칠 수 없는 표시는 남기지 않는다.
        if tr_scope_result and tr_scope_result.get("verdict") == tr_scope_service.VERDICT_SKIPPED:
            tr_scope_result = None
        if tr_scope_result and tr_scope_result.get("verdict") == tr_scope_service.VERDICT_REJECT:
            # 거부된 제출에는 TR 문서 ID 가 없다(번호 예약 전). 사후 조회를 위해
            # 그룹 타임라인에 이벤트로 남긴다 — tr_scope_service 모듈 docstring 참조.
            try:
                db_events.create({
                    "event_type": "action_taken",
                    "project_id": project,
                    "group_id": group["group_id"],
                    "document_id": None,
                    "actor_user_id": actor_user_id,
                    "from_state": None,
                    "to_state": None,
                    "metadata": json.dumps({
                        "action_code": "tr_scope_rejected",
                        "prev_doc_id": prev_doc_id,
                        "token_id": token_rec.get("token_id"),
                        "dry_run": bool(_truthy(body.get("dry_run"))),
                        "stage": tr_scope_result.get("stage"),
                        "codes": tr_scope_result.get("codes"),
                        "branch": tr_scope_result.get("branch"),
                        "out_of_scope": tr_scope_result.get("out_of_scope"),
                        "unconfirmed": tr_scope_result.get("unconfirmed"),
                        "unreported": tr_scope_result.get("unreported"),
                    }, ensure_ascii=False),
                })
            except Exception:  # noqa: BLE001 — 기록 실패로 반려 자체를 놓치지 않는다
                pass
            return _fail(422, tr_scope_result.get("notice") or "TR 작업범위 검증 반려")

    # ── Step 5.8: Workflow-head type guard (0374 T0004) ────────────────────
    # Compare before the shared dry-run branch and before numbering/storage so a
    # mismatched preview and real submission fail identically without an orphan doc.
    # Standalone auto-complete types (M/CH) intentionally bypass this workflow-slot
    # constraint, matching their existing Step 7.5 behavior.
    # 작업계획(WP)도 이 검사에서 빠진다. 워크플로 머리는 "다음에 올 단계 문서"를 못박는
    # 값이고 그것이 단계 문서에는 맞지만, 작업계획은 자문형이라 그룹의 어느 시점에서도
    # 쓸 수 있고 한 그룹에 여러 장을 둘 수 있다(D0007 §3.1 결정 5). 머리 타입에 묶으면
    # 설계가 정한 AI 생성 경로(P0009 §5.1)가 그룹이 진행 중일 때마다 409 로 막힌다.
    workflow_head: Optional[dict] = None
    if doc_type.upper() not in HEAD_TYPE_GUARD_EXEMPT_TYPES:
        try:
            workflow_head = db_wfseq.get_pending_head_by_group(group["group_id"], project)
        except Exception:
            # Step 7.5 is best-effort too; stores without workflow support retain the
            # legacy non-workflow creation path rather than failing unrelated submissions.
            workflow_head = None
        if workflow_head is not None:
            expected_head_type = str(workflow_head.get("type") or "").upper()
            submitted_type = doc_type.upper()
            if expected_head_type and expected_head_type != submitted_type:
                return _fail(
                    409,
                    f"이 그룹의 다음 단계는 {expected_head_type}입니다. "
                    f"받은 타입 {submitted_type}은 받을 수 없습니다. 문서는 등록되지 "
                    f"않았습니다. doc_type을 {expected_head_type}로 바꿔 다시 제출하세요.",
                )

    # ── Step 5.9: 깨진 글자 실등록 차단 + 본문 지문 대조 (0391 B0001 제안3+4, T0005 §5-1/§6) ──
    # 부작용(문서 번호 예약) 이전, dry-run 분기보다 앞이라 거부돼도 흔적이 안 남는다.
    _encoding_fields: dict[str, Optional[str]] = {"본문": body_for_guards}
    if title_override:
        _encoding_fields["title"] = title_override
    elif body_for_guards:
        _title_line = _extract_title_from_content(body_for_guards)
        if _title_line:
            _encoding_fields["title: 줄"] = _title_line
    for _qi, _q in enumerate(body.get("questions") or []):
        if isinstance(_q, dict):
            if _q.get("title"):
                _encoding_fields[f"questions[{_qi}].title"] = _q.get("title")
            if _q.get("body"):
                _encoding_fields[f"questions[{_qi}].body"] = _q.get("body")
    _encoding_fail = _encoding_guard(
        fields=_encoding_fields,
        fingerprint_field="본문",
        body_sha256=body.get("body_sha256"),
        body_chars=body.get("body_chars"),
        force_encoding_reason=body.get("force_encoding_reason"),
    )
    if _encoding_fail is not None:
        return _encoding_fail

    # ── Dry-run short-circuit (R0001 dry-run, L0007 §3/§4.1) ──
    # All validation has passed; bail out before the first side effect (reserve_document).
    # new is not numbered yet, so doc_id is null and only group_name is echoed (P0006 §3.1).
    # content_size is NOT a check here: _handle_new has no body-size validation (L0007 §1.1).
    new_checks = ["auth", "context_binding", "permission", "doc_type", "referential_integrity"]
    if workflow_head is not None:
        new_checks.append("workflow_head")
    if doc_path is not None:
        new_checks.append("doc_path")
    if body_for_guards is not None:
        new_checks.append("frontmatter_identity")
    if fingerprints:
        new_checks.append("dup_body")
    if commit_message_draft is not None:
        new_checks.append("commit_message")
    if wp_plan is not None:
        new_checks.append("work_plan_body")
    would_register: dict = {
        "action": "new",
        "doc_id": None,
        "group_name": group["group_id"],
        "doc_type": doc_type,
        "checks_passed": new_checks,
    }
    # 0299 D0004 §3.1: dry-run 과 실등록은 같은 판정을 낸다. 거부는 위에서 이미
    # 반환됐으므로 여기 실리는 것은 통과/경고/관측이며, 경고 사유가 있으면 작업자가
    # 본 제출 전에 목록을 고칠 수 있게 판정 내용을 그대로 돌려준다.
    if tr_scope_result is not None:
        new_checks.append("tr_scope")
        would_register["tr_scope"] = tr_scope_result
    dry_resp = _maybe_dry_run(body, token_rec, would_register)
    if dry_resp is not None:
        return dry_resp

    try:
        doc_code = numbering_service.reserve_document(
            group_id=group["group_id"],
            doc_type=doc_type,
            module=module,
        )
    except RuntimeError as exc:
        return _fail(503, f"Numbering lock timeout. Please retry shortly.: {exc}")

    canonical_doc_id = _build_doc_id(group["group_id"], doc_code)
    try:
        _, seq = parse_doc_code(doc_code)
    except ValueError:
        m = re.search(r"(\d+)$", doc_code)
        seq = int(m.group(1)) if m else 0
    project_settings = db_projects.get_settings(project)
    branch = (project_settings.get("branch") or "main").strip() if project_settings else "main"
    stored_path = _resolve_storage_path(
        project, module, group, canonical_doc_id, branch=branch, doc_type=doc_type,
    )

    try:
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        if wp_plan is not None:
            # 정본은 보내온 글자가 아니라 검증기가 돌려준 표준형으로 쓴다(P0009 §2.6
            # 결정 3·4). 키 순서와 공백이 저장할 때마다 흔들리면 리비전 사이의 차이가
            # 실제로 바뀐 값보다 커진다. 임시 파일에 쓴 뒤 통째로 갈아끼우므로 반쯤 쓰다
            # 끊긴 정본이 남지 않는다.
            work_plan_service.write_body_atomically(stored_path, wp_plan)
            if doc_path is not None:
                try:
                    os.unlink(doc_path)
                except OSError:
                    pass
        elif doc_path is not None:
            shutil.move(str(doc_path), str(stored_path))
        else:
            stored_path.write_text(content, encoding="utf-8")  # type: ignore[arg-type]
    except OSError as exc:
        return _fail(500, f"Storage error: {exc}")

    try:
        file_content_for_title = stored_path.read_text(encoding="utf-8")
    except OSError:
        file_content_for_title = content if isinstance(content, str) else ""
    if title_override:
        extracted_title = title_override
    else:
        extracted_title = _extract_title_from_content(file_content_for_title) or canonical_doc_id

    # ── Step 7: DB registration ──────────────────────────────────────────────────────
    now = now_iso()
    # D022 §2-3: related_doc_ids → meta JSON. B0106: also persist the body fingerprint
    # ("content_sha256") so a later cross-group duplicate submission can be detected by
    # find_by_content_fingerprint. Insertion order keeps the value a stable substring
    # ('"content_sha256": "<hash>"') regardless of related_doc_ids, which the LIKE match
    # relies on (no schema migration; dialect-portable).
    meta_payload: dict = {}
    if related_doc_ids:
        meta_payload["related_doc_ids"] = related_doc_ids
    if fingerprints:
        meta_payload.update(fingerprints)
    # 0299 D0004 §6: 통과/경고 판정은 생성된 TR 문서에 붙여 문서 상세에서 조회한다
    # (거부는 문서가 존재하지 않으므로 events 로 간다 — Step 5.7 참조). 화면에 그대로
    # 나열되는 값이라 목록 길이를 여기서 제한한다: 신고/감지 전체를 meta 에 넣으면
    # 큰 그룹에서 meta 한 칸이 수십 KB가 되고, 화면은 어차피 접어서 보여준다.
    if tr_scope_result is not None:
        meta_payload["tr_scope"] = _tr_scope_meta(tr_scope_result)
    # 0391 T0005 §5-6: 깨짐/지문 우회문을 감사 목적으로 남긴다(신규 컬럼/마이그레이션 없음).
    _force_encoding_reason = str(body.get("force_encoding_reason") or "").strip()
    if _force_encoding_reason:
        meta_payload["force_encoding_reason"] = _force_encoding_reason
    # 작성 경로 표시(D0007 §3.7 결정 7): 문서 정보 패널의 "작성 경로: 사람 / AI" 한 줄이
    # 이 값을 읽는다. 사람이 만든 것과 AI가 만든 것은 같은 검토를 받지만, 검토자는 둘을
    # 구별할 수 있어야 한다. 어느 실행이 만들었는지도 함께 남긴다.
    if wp_plan is not None:
        meta_payload["work_plan"] = {
            "origin": "ai",
            "origin_run_id": token_rec.get("ai_run_id"),
        }
    meta_value = json.dumps(meta_payload) if meta_payload else None
    try:
        db_docs.create({
            "doc_id": canonical_doc_id,
            "project_id": project,
            "module": module,
            "group_id": group["group_id"],
            "type_code": doc_type,
            "seq": seq,
            "title": extracted_title,
            "file_path": to_storage_relative(stored_path, project),
            "status": "open",
            "owner_id": actor_user_id,
            "triggered_by": prev_doc_id,
            "revision_no": 0,
            "created_at": now,
            "updated_at": now,
            "meta": meta_value,
            "commit_message": commit_message_draft,
        })
        # group 0022 §5 / D0005 §3.4 type ①: create document + query together. The AI
        # worker attaches low-confidence points as queries on that document
        # (asker_kind='ai'). On failure, roll back the document and file.
        new_questions = body.get("questions")
        if new_questions:
            from modules.flow_gate.services import q_service
            q_service.add_questions(
                doc_id=canonical_doc_id,
                questions=new_questions,
                asker_kind="ai",
                project_id=project,
                notify_audience=actor_user_id,
            )
    except Exception as exc:
        # storage rollback
        try:
            stored_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            db_docs.delete(canonical_doc_id)
        except Exception:
            pass
        return _fail(500, f"DB registration error: {exc}")

    db_events.create({
        "event_type": "action_taken",
        "project_id": project,
        "group_id": group["group_id"],
        "document_id": None,
        "actor_user_id": actor_user_id,
        "from_state": None,
        "to_state": "open",
        "metadata": json.dumps({"action_code": "doc_created", "doc_id": canonical_doc_id}),
    })

    # ── Step 7.5: Workflow head processing (M: auto-complete / others: pending-review transition) ──────
    # T823: head-slot registration is best-effort (inside try/except);
    #       doc_review_status transition is mandatory for non-M (outside try/except).
    # L0044.0008 §3.1/§3.2: auto-complete types (M memo, CH conversation) are the
    # single source of truth in AUTO_COMPLETE_TYPES; the literal "M" branches below
    # generalize to set membership so CH takes the same non-gate path as M.
    from modules.flow_gate.documents.routers.documents import AUTO_COMPLETE_TYPES
    try:
        from modules.flow_gate.db import workflow_sequences as _db_wfseq
        from modules.flow_gate.workflow.pipeline_service import register_workflow_result
        head_item = _db_wfseq.get_pending_head_by_group(group["group_id"], project)
        if head_item is not None and head_item.get("type") == doc_type.upper():
            if doc_type.upper() in AUTO_COMPLETE_TYPES:
                # M (memo) / CH (conversation): review step not needed — fill the
                # result slot. Approval is set unconditionally below (L-AUTO §3.3).
                _db_wfseq.set_item_result_doc_id(head_item["id"], canonical_doc_id)
                # NOTE: do NOT silently finalize here even when this memo fills the
                # last pending slot. AC (final approval) is an explicit review step
                # (M042 §3.1 — PM rejected silent wf_done). With no pending slots
                # left, head resolution surfaces AC/pending so the final-approval
                # screen appears; wf_done is set only when AC is approved.
                # NR0003: the inbox path previously called mark_sequence_done +
                # set the parent R to wf_done here, bypassing the AC gate and the
                # "all steps approved" guard (a memo as the final slot finalized the
                # whole workflow without review). Aligned with documents.py:819-827.
            else:
                # Non-auto-complete with a matching head: register result slot.
                # Status transition is handled unconditionally below (T823).
                register_workflow_result(
                    item_id=head_item["id"],
                    registered_path=to_storage_relative(stored_path, project),
                    registered_doc_id=canonical_doc_id,
                    registered_at=now,
                    actor_user_id=actor_user_id,
                )
    except Exception:
        pass

    # L0044.0008 §3.3 (L-AUTO): auto-complete types are created already-approved
    # regardless of whether a matching pending head slot existed above. A standalone-
    # opened conversation (CH) has no pre-seeded head, so without this its
    # doc_review_status would leak NULL — violating "approved on creation"
    # (D0044.0006 §4 / P0044.0007 §3). Idempotent for M (already approved above).
    if doc_type.upper() in AUTO_COMPLETE_TYPES:
        try:
            from modules.flow_gate.db import documents as _db_docs_ac
            _db_docs_ac.update(canonical_doc_id, {"doc_review_status": "approved"})
        except Exception:
            pass

    # T823: all non-auto-complete inbox docs must reach pending_review regardless of head.
    if doc_type.upper() not in AUTO_COMPLETE_TYPES:
        from modules.flow_gate.workflow.pipeline_service import transition_document_review
        transition_document_review(
            doc_id=canonical_doc_id,
            action="submit",
            actor_user_id=actor_user_id,
            user_permissions={"document.update"},
        )

    # ── Step 8: Token consumption ────────────────────────────────────────────────────
    token_service.consume(
        token_id=token_rec["token_id"],
        project_id=project,
        doc_id=canonical_doc_id,
    )

    # ── Step 9: Screen push (Phase 2) ──────────────────────────────────────────
    try:
        from modules.flow_gate.api.v1.events.publisher import publish_event_threadsafe, FlowEvent
        from modules.flow_gate.api.v1.events.event_types import EventType
        from modules.flow_gate.api.v1.group_routes import get_next_action_candidates

        def _push():
            base = dict(project=project, group_id=group["group_id"],
                        doc_id=canonical_doc_id, audience=actor_user_id)
            publish_event_threadsafe(FlowEvent(
                event_type=EventType.FILE_EXPLORER_REFRESH,
                payload={"operation": "created", "stored_path": str(stored_path)},
                **base,
            ))
            doc_rec = db_docs.get_by_id(canonical_doc_id)
            publish_event_threadsafe(FlowEvent(
                event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
                payload={"operation": "created", "doc_id": canonical_doc_id,
                         "type": doc_type, "title": extracted_title,
                         "status": "open", "revision_no": 0},
                **base,
            ))
            publish_event_threadsafe(FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": group["group_id"], "reason": "document_added"},
                **base,
            ))
            # M026 Fix-4: SSE broadcast on None → pending_review transition.
            # L0044.0008 §3.2: auto-complete types (M/CH) never reach pending_review,
            # so generalize the literal "M" guard to set membership.
            from modules.flow_gate.documents.routers.documents import AUTO_COMPLETE_TYPES
            if (
                doc_type.upper() not in AUTO_COMPLETE_TYPES
                and doc_rec
                and doc_rec.get("doc_review_status") == "pending_review"
            ):
                from modules.flow_gate.api.v1.events.publisher import broadcast_event_threadsafe
                broadcast_event_threadsafe(FlowEvent(
                    event_type=EventType.DOC_REVIEW_STATUS_CHANGED,
                    payload={
                        "doc_id": canonical_doc_id,
                        "prev_status": None,
                        "next_status": "pending_review",
                        "rejection_reason": None,
                    },
                    audience="*",
                    doc_id=canonical_doc_id,
                    project=project,
                    group_id=group["group_id"],
                ))
            candidates = get_next_action_candidates(group["group_id"])
            if candidates:
                publish_event_threadsafe(FlowEvent(
                    event_type=EventType.NOTIFICATION_NEW_ACTION_CANDIDATE,
                    payload={"doc_id": canonical_doc_id, "type": doc_type,
                             "title": extracted_title, "candidates": candidates},
                    **base,
                ))
            # D022 §3-3: Q-registration-only event (when doc_type='Q')
            if doc_type.upper() == "Q":
                publish_event_threadsafe(FlowEvent(
                    event_type=EventType.QNA_Q_REGISTERED,
                    payload={"q_doc_id": canonical_doc_id, "prev_doc_id": prev_doc_id,
                             "title": extracted_title, "status": "open"},
                    **base,
                ))

        # 0279 T0007: _push is now synchronous and the *_threadsafe publishers
        # schedule onto the captured main loop themselves, so the old
        # get_event_loop/ensure_future/asyncio.run dance is gone. That dance was
        # also unsound from a worker thread: it would have built a NEW loop and
        # published into queues owned by the main loop, delivering nothing —
        # exactly the failure publish_event_threadsafe was written to fix.
        _push()
    except Exception as _push_exc:
        import LogAssist.log as logger
        logger.warning(f"[inbox new] Step 9 SSE publish failed (ignored): {_push_exc}")

    # ── Step 10: Response ─────────────────────────────────────────────────────────
    resp_content: dict = {
        "ok": True,
        "doc_id": canonical_doc_id,
        "stored_path": str(stored_path),
        "message": f"{canonical_doc_id} registered. You may end the session.",
    }
    # 0370 4세트 (P0002 시나리오 15): 방금 만들어진 파일을 세어 변경 요약을 붙인다. 견줄
    # 옛 판이 없으므로 before 는 null 이고 전부 추가로 잡힌다. 요약은 어디에도 저장하지
    # 않고 응답으로만 나가므로 새 표도 새 컬럼도 필요 없다. 줄 번호는 **저장된 파일**
    # 기준이라 보낸 본문이 아니라 stored_path 를 다시 읽어 센다 — 그래야 목차 조회와
    # 저장 요약이 같은 줄 번호를 말한다.
    if wp_plan is not None:
        # 작업계획에는 절도 줄도 없다(P0009 §5 결정 10). 무인 작업자가 확인해야 하는 것은
        # "몇 줄이 어느 절에 들어갔는가"가 아니라 자기가 보낸 수량과 배정이 그대로
        # 저장됐는가이므로, kind 로 서식을 구별해 전용 요약을 돌려준다.
        resp_content["change_summary"] = work_plan_service.change_summary(wp_plan)
        resp_content.update({
            "doc_type": doc_type.upper(),
            "title": extracted_title,
            "revision_no": 0,
            "origin": "ai",
            "origin_run_id": token_rec.get("ai_run_id"),
        })
    else:
        resp_content["change_summary"] = _build_change_summary(
            doc_id=canonical_doc_id,
            after_path=stored_path,
            after_revision_no=0,
        )
    # Continuous work self-chain (group 0051 / NR0003 B안): for a continuation token,
    # embed next_token/next_mention/continuation_remaining so the worker proceeds to the
    # next step without a human re-issuing a token. No-op for ordinary tokens. Never
    # fails the (already-saved) submission — degrades to a paused envelope on any error.
    try:
        chain = _continuation_self_chain(
            request, token_rec, project, canonical_doc_id, doc_type
        )
        if chain:
            resp_content.update(chain)
            _chain_msg = _chain_message(chain, canonical_doc_id)
            if _chain_msg:
                resp_content["message"] = _chain_msg
    except Exception as _chain_exc:  # noqa: BLE001
        import LogAssist.log as logger
        logger.warning(f"[inbox new] continuation self-chain failed (ignored): {_chain_exc}")

    return JSONResponse(content=resp_content, status_code=201)


def _handle_edit(request: Request, raw_token: str, body: dict) -> JSONResponse:
    """Processing flow for action: edit (D020 §3-4-2)."""

    # ── Step 1: Field validation ────────────────────────────────────────────────────
    project = body.get("project")
    module = body.get("module")
    group_raw = body.get("group_name") or body.get("group")
    doc_id_raw = body.get("doc_id")
    edit_reason = body.get("edit_reason")

    required_pairs = {
        "project": project,
        "module": module,
        "group_name": group_raw,
        "doc_id": doc_id_raw,
        "edit_reason": edit_reason,
    }
    for field, value in required_pairs.items():
        if not value:
            return _fail(400, f"Required field missing: {field}")

    valid_reasons = {"rejected", "qna_followup", "user_comment", "worker_self"}
    if edit_reason not in valid_reasons:
        return _fail(400, "edit_reason value is invalid")

    has_path = body.get("doc_path") is not None
    has_content = body.get("content") is not None
    if has_path == has_content:
        return _fail(400, "Exactly one of doc_path or content must be provided")

    project = str(project)
    module = str(module)
    try:
        group_name = _normalize_group_name(project, module, str(group_raw))
        doc_id = _normalize_doc_id(group_name, str(doc_id_raw))
    except ValueError as exc:
        return _fail(422, str(exc))
    edit_reason = str(edit_reason)
    linked_doc_id: Optional[str] = body.get("linked_doc_id")
    doc_path: Optional[str] = body.get("doc_path")
    content: Optional[str] = body.get("content")
    # P0005/T0006: when re-submitting a rejected document, the AI may include its
    # response to the rejection alongside the body. The body stays the body; this
    # text is recorded against the latest rejection so reviewers can see how the
    # AI addressed their comment. Only meaningful for edit_reason="rejected".
    rejection_response: Optional[str] = body.get("rejection_response")

    # ── Step 2: Authentication ─────────────────────────────────────────────────────────
    try:
        token_rec = token_service.verify(raw_token)
    except HTTPException as exc:
        return _fail(exc.status_code, exc.detail)

    # ── Step 3: Context binding ───────────────────────────────────────────────
    if token_rec["project"] != project:
        return _fail(403, "Context binding mismatch. Use the correct token.")
    if token_rec["action_scope"] != "edit":
        return _fail(403, "Context binding mismatch. Use the correct token.")
    if token_rec.get("doc_ref") != doc_id:
        return _fail(403, "Context binding mismatch. Use the correct token.")

    actor_user_id: str = token_rec["issued_to"]

    # ── Step 4: Permission check (D020 §6-2) ────────────────────────────────────────
    if not has_permission(actor_user_id, project, "perm_document_update"):
        return _fail(403, "Insufficient permissions for this operation")

    # ── Step 5: Referential integrity + body validation ──────────────────────────────────────
    existing_doc = db_docs.get_by_id(doc_id)
    if existing_doc is None:
        return _fail(422, f"Referenced document {doc_id} does not exist")
    final_approved = document_service.is_final_approved(existing_doc)
    if not document_service.is_document_editable(
        existing_doc,
        final_approved=final_approved,
    ):
        if final_approved:
            return _fail(422, "Modification not allowed after final approval.")
        return _fail(
            422,
            f"Modification not allowed for status: {existing_doc.get('status')}",
        )

    if linked_doc_id and db_docs.get_by_id(linked_doc_id) is None:
        return _fail(422, f"Referenced document {linked_doc_id} does not exist")

    group = _resolve_group(project, group_name)
    if group is None:
        return _fail(422, f"Group not found: {group_name}")

    # Disposed-group guard (TR0079.0003 rework — the exact rejected symptom: "documents
    # in a disposed group still get edited fine"). Editing a document in a discarded group is a forward
    # action and must be rejected at the source, independent of any client state.
    disposed = _disposed_group_fail(group["group_id"], "Modification")
    if disposed is not None:
        return disposed

    if doc_path is not None:
        scratch_dir = token_rec.get("scratch_dir") or _token_scratch_dir(
            token_rec["project"], token_rec["token_id"]
        )
        if not validate_doc_path(doc_path, scratch_dir):
            return _fail(422, f"doc_path is not accessible: {doc_path}")
        if not os.path.isfile(doc_path):
            return _fail(422, f"doc_path file does not exist: {doc_path}")

    if content is not None:
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content
        if len(content_bytes) > _content_max():
            return _fail(422, f"content size exceeds the limit (max {_content_max()} bytes)")

    # ── Step 5.5: Frontmatter identity + duplicate-body guards ──────────────────────
    edit_body_for_guards: Optional[str] = None
    try:
        edit_body_for_guards = _submission_text(doc_path, content)
        identity_mismatch = _frontmatter_identity_mismatch(
            edit_body_for_guards,
            expected_project=project,
            expected_module=module,
            expected_group_id=group["group_id"],
            expected_doc_type=str(existing_doc.get("type_code") or ""),
            expected_doc_id=doc_id,
            expected_target_id=existing_doc.get("target_id") or existing_doc.get("triggered_by"),
        )
        if identity_mismatch is not None:
            return _fail(
                409,
                "Frontmatter identity mismatch: submitted content declares another "
                f"document identity ({identity_mismatch}). Re-send with this "
                "document's own content.",
            )
    except Exception:  # noqa: BLE001 — identity guard is defense-in-depth
        edit_body_for_guards = None

    # Rejected design-document edits are the same authoring operation and must not
    # bypass the template structure check that applies to new submissions.
    edit_doc_type = str(existing_doc.get("type_code") or existing_doc.get("type") or "").upper()
    design_error = _design_template_submission_error(
        project=project,
        doc_type=edit_doc_type,
        locale=token_rec.get("continuation_locale") or request.headers.get("x-locale") or "ko",
        content=edit_body_for_guards or "",
    )
    if design_error is not None:
        return design_error

    # ── 작업계획(WP) 본문 해석 — 수정 경로 (P0009 §5.4) ────────────────────────
    # new 에만 붙이면 edit 이 통째로 우회로가 된다. 반려된 작업계획은 edit 으로 다시
    # 올라오는데 거기에 검증이 없으면 규칙에 맞지 않는 정본이 그대로 저장되고, 그 문서는
    # 그 뒤로 표로 열리지 않는다. 판정과 문구는 new 와 같은 검증기에서 나온다.
    #
    # 결정 11: 수정에는 리비전 검사를 걸지 않는다. 인박스의 edit 은 지금도 판 번호를 받지
    # 않고, AI 작업자는 자기가 받은 토큰 하나로 한 문서를 고친다 — 두 사람이 같은 화면을
    # 열어 둔 상황이 아니다. 대신 응답의 change_summary.changed 로 무엇이 무엇으로 바뀌
    # 었는지 돌려주어, 남의 값을 덮었는지 작업자가 바로 알 수 있게 한다.
    wp_plan: Optional[dict] = None
    if edit_doc_type == WORK_PLAN_TYPE:
        wp_locale = work_plan_service.normalize_locale(
            token_rec.get("continuation_locale") or request.headers.get("x-locale")
        )
        wp_raw = edit_body_for_guards
        if wp_raw is None:
            wp_raw = _submission_text(doc_path, content) or ""
        wp_parsed: object = None
        try:
            wp_parsed = json.loads(wp_raw)
        except (json.JSONDecodeError, ValueError) as exc:
            return _fail(
                400,
                work_plan_service.inbox_not_json_message(wp_raw, exc, wp_locale),
                help_url=work_plan_service.HELP_TEMPLATE_PATH,
            )
        if not isinstance(wp_parsed, dict):
            return _fail(
                400,
                work_plan_service.inbox_not_json_message(wp_raw, None, wp_locale),
                help_url=work_plan_service.HELP_TEMPLATE_PATH,
            )
        try:
            wp_plan = work_plan_service.validate(
                wp_parsed, project_id=project, action="save",
            )
        except work_plan_service.WorkPlanValidationError as exc:
            return _fail(
                400,
                work_plan_service.inbox_error_message(exc, wp_locale),
                help_url=work_plan_service.HELP_TEMPLATE_PATH,
            )

    # Mirror _handle_new's cross-group duplicate guard on the edit path. B0106 only
    # defended `new`, so the same contamination (correct title, stale/reused body from
    # another group) recurred through inbox edit — most often on CH conversations, whose
    # body the worker rewrites wholesale every turn (NR0003 §2a). Refuse a substantial
    # body byte-identical to a document in a *different* group; the current group is
    # excluded, so an edit that legitimately keeps or extends its own body is never
    # blocked. The fingerprint is reused at Step 7.1 to (re)persist meta.content_sha256.
    # Fail-open on any read/lookup error — the guard must never 500 a real edit.
    edit_fingerprints: dict = {}
    try:
        body_for_fp = (
            edit_body_for_guards
            if edit_body_for_guards is not None
            else _submission_text(doc_path, content)
        )
        edit_fingerprints = _content_fingerprints(body_for_fp)
        # new 경로와 같은 이유로 작업계획은 이 가드에서 빠진다: 같은 계획을 다른 그룹에
        # 그대로 넣는 것이 설계가 정한 유일한 복사 경로다(P0009 DEFERRED).
        twin = (
            None if wp_plan is not None
            else _find_body_twin(edit_fingerprints, exclude_group_id=group["group_id"])
        )
        if twin is not None:
            return _fail(
                409,
                "Duplicate body: this content matches "
                f"{twin['doc_id']} in another group. The submitted body looks like "
                "stale/reused content (correct title, wrong body). Re-send with this "
                "document's own content.",
            )
    except Exception:  # noqa: BLE001 — guard is defense-in-depth; never 500 a real edit
        edit_fingerprints = {}  # unreadable doc_path / lookup error → skip; edit still proceeds

    # ── Step 5.7: TR 작업범위 검증 (0299 R0001) ─────────────────────────────────
    # _handle_new 에만 붙이면 edit 이 통째로 우회로가 된다. 사람 리뷰에서 반려된 TR 은
    # edit 으로 재제출되는데, 그 경로에 검증이 없으면 `## 변경 파일` 목록을 사후에
    # 아무렇게나 고쳐 넣어도 아무도 대조하지 않는다. new 의 가드를 edit 에 붙이지
    # 않아 같은 오염이 재발한 전례(위 dup-body 가드 주석의 B0106)와 같은 구조다.
    #
    # 판정 시점이 다르므로 결과 기록도 다르다. edit 에는 이미 문서가 있으므로 통과·경고는
    # Step 7.1 에서 그 문서의 meta 에 갱신하고, 거부는 문서를 바꾸지 않은 채 반환한다.
    edit_tr_scope: Optional[dict] = None
    if str(existing_doc.get("type_code") or "").upper() in tool_registry.MUTATING_STEP_TYPES:
        try:
            from modules.flow_gate import template_provision as _template_provision

            scope_body = edit_body_for_guards
            if scope_body is None:
                scope_body = _submission_text(doc_path, content)
            # T2/TR2 (NR0003 §1-4): 반려 안내문의 언어 — new 경로(위 Step 5.7)와 같은 규칙.
            scope_locale = _template_provision.normalize_locale(
                token_rec.get("continuation_locale") or request.headers.get("x-locale")
            )
            edit_tr_scope = tr_scope_service.evaluate(
                project_id=project, group_id=group["group_id"], body=scope_body or "",
                locale=scope_locale,
                prior_declared=_prior_tr_declared(group["group_id"], exclude_doc_id=doc_id),
            )
        except Exception:  # noqa: BLE001 — 검증 실패가 재제출을 막아선 안 된다
            edit_tr_scope = None
        if edit_tr_scope and edit_tr_scope.get("verdict") == tr_scope_service.VERDICT_SKIPPED:
            edit_tr_scope = None
        if edit_tr_scope and edit_tr_scope.get("verdict") == tr_scope_service.VERDICT_REJECT:
            try:
                db_events.create({
                    "event_type": "action_taken",
                    "project_id": project,
                    "group_id": group["group_id"],
                    "document_id": None,
                    "actor_user_id": actor_user_id,
                    "from_state": None,
                    "to_state": None,
                    "metadata": json.dumps({
                        "action_code": "tr_scope_rejected",
                        "action": "edit",
                        "doc_id": doc_id,
                        "token_id": token_rec.get("token_id"),
                        "dry_run": bool(_truthy(body.get("dry_run"))),
                        "stage": edit_tr_scope.get("stage"),
                        "codes": edit_tr_scope.get("codes"),
                        "branch": edit_tr_scope.get("branch"),
                        "out_of_scope": edit_tr_scope.get("out_of_scope"),
                        "unconfirmed": edit_tr_scope.get("unconfirmed"),
                        "unreported": edit_tr_scope.get("unreported"),
                    }, ensure_ascii=False),
                })
            except Exception:  # noqa: BLE001 — 기록 실패로 반려 자체를 놓치지 않는다
                pass
            return _fail(422, edit_tr_scope.get("notice") or "TR 작업범위 검증 반려")

    # ── Step 5.9: 깨진 글자 실등록 차단 + 본문 지문 대조 (0391 B0001 제안3+4, T0005 §5-2/§6) ──
    _edit_encoding_fields: dict[str, Optional[str]] = {"본문": edit_body_for_guards}
    if edit_reason:
        _edit_encoding_fields["edit_reason"] = edit_reason
    if rejection_response:
        _edit_encoding_fields["rejection_response"] = rejection_response
    _edit_encoding_fail = _encoding_guard(
        fields=_edit_encoding_fields,
        fingerprint_field="본문",
        body_sha256=body.get("body_sha256"),
        body_chars=body.get("body_chars"),
        force_encoding_reason=body.get("force_encoding_reason"),
    )
    if _edit_encoding_fail is not None:
        return _edit_encoding_fail

    # ── Dry-run short-circuit (R0001 dry-run, L0007 §3/§4.2) ──
    # All validation has passed; bail out before the first side effect (backup/CAS/consume/SSE).
    # checks_passed reflects checks actually run on this path (L0007 §1.1/§4 principle):
    # content_size only when content was supplied; linked/doc_path only when those inputs exist.
    edit_checks = ["auth", "context_binding", "permission", "referential_integrity", "editable"]
    if content is not None:
        edit_checks.append("content_size")
    if linked_doc_id:
        edit_checks.append("linked")
    if doc_path is not None:
        edit_checks.append("doc_path")
    if edit_body_for_guards is not None:
        edit_checks.append("frontmatter_identity")
    if edit_fingerprints:
        edit_checks.append("dup_body")
    if wp_plan is not None:
        edit_checks.append("work_plan_body")
    edit_would_register: dict = {
        "action": "edit",
        "doc_id": doc_id,
        "checks_passed": edit_checks,
    }
    # new 와 같이, 거부는 위에서 이미 반환됐으므로 여기 실리는 것은 통과/경고다.
    if edit_tr_scope is not None:
        edit_checks.append("tr_scope")
        edit_would_register["tr_scope"] = edit_tr_scope
    dry_resp = _maybe_dry_run(body, token_rec, edit_would_register)
    if dry_resp is not None:
        return dry_resp

    # ── Step 6a: Back up existing file (D020 §4) ────────────────────────────────────
    current_revision_no: int = existing_doc.get("revision_no", 0)
    stored_path_str: str = existing_doc.get("file_path", "")
    # file_path is persisted relative (L0054.0002) → resolve to an absolute Path for
    # the filesystem operations below. None when the file cannot be located, which
    # falls through to the recompute branch.
    existing_branch = (existing_doc.get("branch") or "main") or "main"
    stored_path = None
    if stored_path_str:
        # Prefer the jailed resolve (handles relative + branch drift, confirms the
        # file exists); fall back to a soft resolve for trusted legacy DB values
        # that live outside the storage jail (relative→root, absolute→passthrough).
        stored_path = (
            resolve_storage_path(stored_path_str, project, branch=existing_branch)
            or resolve_storage_dir(stored_path_str, project)
        )

    # backup_path_str stays absolute for the in-request shutil rollback (below);
    # backup_path_rel is the relative value persisted to document_revisions.
    backup_path_str: Optional[str] = None
    backup_path_rel: Optional[str] = None
    if stored_path and stored_path.exists():
        revisions_dir = stored_path.parent / "revisions"
        revisions_dir.mkdir(parents=True, exist_ok=True)
        # 백업은 원본과 같은 확장자를 쓴다. 작업계획 정본은 .json 이라 여기서 .md 로
        # 굳히면 되돌릴 판이 글 파일로 쌓인다(P0009 §2.6 결정 2와 같은 이유).
        backup_filename = f"{doc_id}.r{current_revision_no}{stored_path.suffix or '.md'}"
        backup_path = revisions_dir / backup_filename
        try:
            shutil.copy2(str(stored_path), str(backup_path))
            backup_path_str = str(backup_path)
            backup_path_rel = to_storage_relative(backup_path, project)
        except OSError as exc:
            return _fail(500, f"Storage error: {exc}")

    # ── Step 6b: Replace with new content ─────────────────────────────────────────────
    if stored_path is None:
        project_settings = db_projects.get_settings(project)
        branch = (project_settings.get("branch") or "main").strip() if project_settings else "main"
        stored_path = _resolve_storage_path(
            project, module, group, doc_id, branch=branch, doc_type=edit_doc_type,
        )

    try:
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        if wp_plan is not None:
            # new 와 같은 규칙: 보내온 글자가 아니라 검증기가 돌려준 표준형을 원자적으로
            # 갈아끼운다(P0009 §2.6 결정 3·4).
            work_plan_service.write_body_atomically(stored_path, wp_plan)
        elif doc_path is not None:
            shutil.copy2(str(doc_path), str(stored_path))
        else:
            stored_path.write_text(content, encoding="utf-8")  # type: ignore[arg-type]
    except OSError as exc:
            return _fail(500, f"Storage error: {exc}")

    # ── Step 7: DB update (CAS) ────────────────────────────────────────────────
    # Re-point file_path at the location we just wrote (NR0003 §3-A / B0001, T0004).
    # Step 6b writes the new body to `stored_path`, which may be a *recomputed*
    # canonical path when the DB's file_path was stale/unresolvable (e.g. after a
    # time-machine rollback, or a Windows absolute path that broke on host move).
    # Historically this UPDATE only bumped revision_no/updated_at, so file_path kept
    # pointing at the old, unresolvable value — the reader (_document_file_path) then
    # 404'd ("MD 파일이 없다") even though the AI had just rewritten the body. Persist
    # the actual write location, mirroring _handle_new which records file_path on
    # creation. to_storage_relative is idempotent and never raises, so this is safe
    # to fold into the CAS update (stays atomic with the revision bump).
    new_file_path_rel = to_storage_relative(stored_path, project)
    store = get_store()
    now = now_iso()
    store._execute(
        "UPDATE documents SET revision_no = revision_no + 1, updated_at = ?, "
        "file_path = ? WHERE doc_id = ? AND revision_no = ?",
        [now, new_file_path_rel, doc_id, current_revision_no],
    )
    refreshed = db_docs.get_by_id(doc_id)
    if refreshed is None or refreshed.get("revision_no") != current_revision_no + 1:
        # CAS conflict → rollback
        if backup_path_str:
            try:
                shutil.copy2(backup_path_str, str(stored_path))
            except OSError:
                pass
        return _fail(409, "Concurrent modification conflict. Please retry.")

    new_revision_no: int = refreshed["revision_no"]

    # ── Step 7.1: Persist body fingerprint (NR0003 §4-2) ─────────────────────────────
    # The dup-body guard can only catch a twin whose meta carries content_sha256.
    # _handle_new persists it on creation, but an edited/grown body — notably an
    # accumulating CH conversation — never updated it, so the *source* of a future
    # contamination stayed invisible to the guard (NR0003 §2b: "가드를 edit에 붙여도
    # 소스 핑거프린트가 없으면 매칭되지 않음"). Refresh it on every body change: write the
    # new hash for a substantial body, else drop a now-stale hash so the guard can never
    # match an outdated body. Best-effort: a failure here must not break the edit that
    # already committed (mirrors the other Step 7.x best-effort blocks).
    try:
        _existing_meta = existing_doc.get("meta")
        _meta_obj = json.loads(_existing_meta) if _existing_meta else {}
        if not isinstance(_meta_obj, dict):
            _meta_obj = {}
        if edit_fingerprints:
            _meta_obj.update(edit_fingerprints)
        else:
            _meta_obj.pop("content_sha256", None)
            _meta_obj.pop("content_sha256_norm", None)
        # 0299: 본문이 바뀌면 신고 목록도 바뀔 수 있으므로 판정을 새 것으로 덮는다.
        # 검증 비대상으로 바뀐 경우(연동 해제 등)에는 옛 판정을 남겨 두지 않는다 —
        # 화면에 지금 상태와 무관한 카드가 계속 떠 있는 것이 아무것도 없는 것보다 나쁘다.
        if edit_tr_scope is not None:
            _meta_obj["tr_scope"] = _tr_scope_meta(edit_tr_scope)
        else:
            _meta_obj.pop("tr_scope", None)
        # 0391 T0005 §5-6: 깨짐/지문 우회문을 감사 목적으로 남긴다.
        _edit_force_encoding_reason = str(body.get("force_encoding_reason") or "").strip()
        if _edit_force_encoding_reason:
            _meta_obj["force_encoding_reason"] = _edit_force_encoding_reason
        db_docs.update(doc_id, {"meta": json.dumps(_meta_obj) if _meta_obj else None})
    except Exception as _fp_exc:  # noqa: BLE001 — best-effort; edit already committed
        import LogAssist.log as logger
        logger.warning(f"[inbox edit] Step 7.1 fingerprint persist failed (ignored): {_fp_exc}")

    # document_revisions INSERT
    if backup_path_str:
        db_revisions.create({
            "doc_id": doc_id,
            "revision_no": current_revision_no,
            "backup_path": backup_path_rel,
            "edit_reason": edit_reason,
            "linked_doc_id": linked_doc_id,
            "created_by": actor_user_id,
            "created_at": now,
        })

    db_events.create({
        "event_type": "doc_edited",
        "project_id": project,
        "group_id": group["group_id"],
        "document_id": None,
        "actor_user_id": actor_user_id,
        "from_state": None,
        "to_state": None,
        "metadata": json.dumps({
            "doc_id": doc_id,
            "edit_reason": edit_reason,
            "linked_doc_id": linked_doc_id,
            "revision_no": new_revision_no,
        }),
    })

    # ── Step 7.5: Transition doc_review_status → revised when edit_reason=rejected ──────
    # B0046: the rejected→revised transition MUST NOT hang off the in-progress head
    # matching this doc's type. A time-machine reopen restores doc_review_status but not
    # sequence-head alignment, so the head can resolve to a trailing slot (or None). The
    # old code nested the transition inside that head guard, so on a time-machine resubmit
    # the transition was skipped, the doc stayed 'rejected', and — because the Step 9 SSE
    # below is gated on doc_review_status == 'revised' — no DOC_REVIEW_STATUS_CHANGED was
    # broadcast, leaving the reviewer's action bar stuck on the [Revision complete] rework toolbar
    # instead of flipping back to Approve/Reject. Separate the transition out of the head guard
    # so it always runs on a rejected resubmit (NR0003 candidate 2). register_workflow_result
    # still needs the head item id, so only IT stays head-gated.
    if edit_reason == "rejected":
        from modules.flow_gate.db import workflow_sequences as _db_wfseq
        from modules.flow_gate.workflow.pipeline_service import (
            TransitionError,
            record_rejection_response,
            register_workflow_result,
            transition_document_review,
        )
        import LogAssist.log as logger
        doc_type_code = existing_doc.get("type_code", "").upper()
        try:
            head_item = _db_wfseq.get_in_progress_head_by_group(group["group_id"], project)
            if head_item is not None and head_item.get("type") == doc_type_code:
                register_workflow_result(
                    item_id=head_item["id"],
                    registered_path=to_storage_relative(stored_path, project),
                    registered_doc_id=doc_id,
                    registered_at=now,
                    actor_user_id=actor_user_id,
                )
        except Exception as _rwr_exc:
            logger.warning(f"[inbox edit] Step 7.5 register_workflow_result failed (ignored): {_rwr_exc}")
        # DB004 §6.1: the doc_review_status transition is the caller's responsibility and is
        # INDEPENDENT of the workflow head — transition_document_review reads only the doc's
        # own doc_review_status. Run it unconditionally so a rejected resubmit always advances
        # rejected→revised (which fires the Step 9 SSE), even when the head is misaligned
        # (time-machine) or absent.
        try:
            transition_document_review(
                doc_id=doc_id,
                action="submit",
                actor_user_id=actor_user_id,
                user_permissions={"document.update"},
            )
        except (TransitionError, ValueError, PermissionError) as _tr_exc:
            logger.warning(f"[inbox edit] Step 7.5 transition skipped ({doc_id}): {_tr_exc}")
        # P0005/T0006: attach the AI's rejection response (if supplied) to the latest
        # rejection. Independent of the workflow head.
        if rejection_response:
            try:
                record_rejection_response(
                    doc_id=doc_id,
                    response_text=rejection_response,
                    recorded_by=actor_user_id,
                    revision_no=new_revision_no,
                )
            except Exception as _rr_exc:
                logger.warning(f"[inbox edit] Step 7.5 record_rejection_response failed (ignored): {_rr_exc}")

    # ── Step 8: Token consumption ────────────────────────────────────────────────────
    token_service.consume(
        token_id=token_rec["token_id"],
        project_id=project,
        doc_id=doc_id,
    )

    # ── Step 9: Screen push (Phase 2) ──────────────────────────────────────────
    try:
        from modules.flow_gate.api.v1.events.publisher import publish_event_threadsafe, FlowEvent
        from modules.flow_gate.api.v1.events.event_types import EventType

        def _push():
            base = dict(project=project, group_id=group["group_id"],
                        doc_id=doc_id, audience=actor_user_id)
            publish_event_threadsafe(FlowEvent(
                event_type=EventType.FILE_EXPLORER_REFRESH,
                payload={"operation": "updated", "stored_path": str(stored_path)},
                **base,
            ))
            refreshed_doc = db_docs.get_by_id(doc_id)
            publish_event_threadsafe(FlowEvent(
                event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
                payload={"operation": "updated", "doc_id": doc_id,
                         "type": refreshed_doc.get("type_code") if refreshed_doc else None,
                         "title": refreshed_doc.get("title") if refreshed_doc else doc_id,
                         "status": refreshed_doc.get("status") if refreshed_doc else None,
                         "revision_no": new_revision_no},
                **base,
            ))
            # L0044.0008 §8 (I-SSE): a conversation (CH) turn-append must reach the
            # human owner's live view even when the AI/service token that submitted
            # the edit is a different subject than the owner. The publishes above are
            # audience=actor_user_id only; additionally deliver an owner-targeted
            # DOCUMENT_EXPLORER_REFRESH so the chat view auto-updates regardless of
            # which subject holds the edit token. Skipped when owner == actor (already
            # delivered) and for non-conversation types (memo/M behaviour unchanged).
            from modules.flow_gate.documents.routers.documents import CONVERSATION_TYPE_CODES
            if refreshed_doc and (refreshed_doc.get("type_code") or "").upper() in CONVERSATION_TYPE_CODES:
                _owner_id = refreshed_doc.get("owner_id")
                if _owner_id and _owner_id != actor_user_id:
                    publish_event_threadsafe(FlowEvent(
                        event_type=EventType.DOCUMENT_EXPLORER_REFRESH,
                        payload={"operation": "updated", "doc_id": doc_id,
                                 "type": refreshed_doc.get("type_code"),
                                 "title": refreshed_doc.get("title"),
                                 "status": refreshed_doc.get("status"),
                                 "revision_no": new_revision_no},
                        project=project, group_id=group["group_id"],
                        doc_id=doc_id, audience=_owner_id,
                    ))
            publish_event_threadsafe(FlowEvent(
                event_type=EventType.GROUP_VIEW_REFRESH,
                payload={"group_id": group["group_id"], "reason": "document_updated"},
                **base,
            ))
            publish_event_threadsafe(FlowEvent(
                event_type=EventType.EDIT_MARKER_ADDED,
                payload={"doc_id": doc_id, "revision_no": new_revision_no,
                         "edit_reason": edit_reason, "linked_doc_id": linked_doc_id},
                **base,
            ))
            # M026 Fix-4: SSE broadcast on rejected → revised transition
            if edit_reason == "rejected":
                edited_doc = db_docs.get_by_id(doc_id)
                if edited_doc and edited_doc.get("doc_review_status") == "revised":
                    from modules.flow_gate.api.v1.events.publisher import broadcast_event_threadsafe
                    broadcast_event_threadsafe(FlowEvent(
                        event_type=EventType.DOC_REVIEW_STATUS_CHANGED,
                        payload={
                            "doc_id": doc_id,
                            "prev_status": "rejected",
                            "next_status": "revised",
                            "rejection_reason": None,
                        },
                        audience="*",
                        doc_id=doc_id,
                        project=project,
                        group_id=group["group_id"],
                    ))

        # 0279 T0007: _push is now synchronous and the *_threadsafe publishers
        # schedule onto the captured main loop themselves, so the old
        # get_event_loop/ensure_future/asyncio.run dance is gone. That dance was
        # also unsound from a worker thread: it would have built a NEW loop and
        # published into queues owned by the main loop, delivering nothing —
        # exactly the failure publish_event_threadsafe was written to fix.
        _push()
    except Exception as _push_exc:
        import LogAssist.log as logger
        logger.warning(f"[inbox edit] Step 9 SSE publish failed (ignored): {_push_exc}")

    # ── Step 10: Response ─────────────────────────────────────────────────────────
    resp_body = {
        "ok": True,
        "doc_id": doc_id,
        "stored_path": str(stored_path),
        "previous_revision_path": backup_path_str,
        "revision_no": new_revision_no,
        "message": f"{doc_id} registered. You may end the session.",
    }
    # 0370 4세트 (P0002 시나리오 14·16): 견줄 "저장 전"은 Step 6a 가 이미 떠 둔 백업
    # 파일이다. 그래서 요약을 위해 새로 저장할 것이 하나도 없다. 백업이 없으면(복사 실패
    # 등) 요약만 포기하고 저장은 성공으로 둔다.
    if wp_plan is not None:
        # 결정 11: 무엇이 무엇으로 바뀌었는지를 돌려준다. 견줄 "저장 전"은 Step 6a 가 떠
        # 둔 백업이다. 백업을 못 읽으면(복사 실패·깨진 옛 정본) 비교만 포기하고 저장은
        # 성공으로 둔다 — 요약 때문에 이미 끝난 저장을 실패로 되돌리지 않는다.
        wp_before: Optional[dict] = None
        if backup_path_str:
            try:
                with open(backup_path_str, encoding="utf-8") as _wp_fh:
                    _wp_prev = json.load(_wp_fh)
                if isinstance(_wp_prev, dict):
                    wp_before = _wp_prev
            except Exception as _wp_exc:  # noqa: BLE001
                import LogAssist.log as logger
                logger.warning(f"[inbox edit] work plan before-image unreadable (ignored): {_wp_exc}")
        resp_body["change_summary"] = work_plan_service.change_summary(wp_plan, wp_before)
        resp_body["doc_type"] = WORK_PLAN_TYPE
    else:
        resp_body["change_summary"] = _build_change_summary(
            doc_id=doc_id,
            after_path=stored_path,
            after_revision_no=new_revision_no,
            before_path=backup_path_str,
            before_revision_no=current_revision_no,
        )
    return JSONResponse(content=resp_body)


# ── Internal helpers ─────────────────────────────────────────────────────────────────

def _token_scratch_dir(project: str, token_id: str) -> str:
    """Return scratch directory path string (delegated to single source of truth, D10 fix).

    Delegates to token_service.scratch_dir_path() to consistently return a project_name-based path.
    Used as a fallback for legacy token records that lack a tokens.scratch_dir column.
    """
    return token_service.scratch_dir_path(project, token_id)
