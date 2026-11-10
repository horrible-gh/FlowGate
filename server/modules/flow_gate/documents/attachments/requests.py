"""AI attachment upload requests — intake, preview, decision, expiry and recovery.

flowgate.chat.0017 CH0002 (final design, approved at seq 11). One structure for chat and
card AI runs::

    AI run --(file)--> intake: authorize, validate, stage + sha256, row(pending) --> {request_id}
                                                         (the AI never waits and keeps working)
    person (chat UI / card attachments tab) --decision--> approve: re-resolve the card's live
                                                          document, register (keep both / overwrite)
                                                          reject / expire: staged file removed

Lifecycle separation (the most important rule of the design)
    A request is bound to its AI run only through the audit columns ``ai_run_id`` /
    ``graph_run_id`` / ``token_id``. No run hook (finalize, cancel, startup command recovery)
    touches these rows, a finished run never closes a pending request, and a decision never
    reads the run's token or scratch: the staged copy lives in a server-owned area and every
    fact the decision needs is copied into the row at intake. Requests only end by a person's
    decision or by their own retention period.

Target
    Always a card (Flow node): ``(target_flow_id, target_node_key)``. A card worker cannot
    choose it — the server takes the node its attempt is bound to. A chat names a card of the
    Flow the chat belongs to (the chat token's group). ``target_doc_id`` is the card's live
    document when the request was made; an approval sends the document the screen showed
    (``expected_doc_id``) and is refused with 409 ``attachment_target_changed`` unless that is
    still the card's live document *inside the document guard*. Nothing ever follows a changed
    target on its own, so a file can never land in a document nobody looked at.

Group lock
    The decision route is exempt from the group AI-run lease (``mutation_policy``) and this
    module never calls the lease check, so a person can approve while an AI works in the same
    group. The other guards stay: archived / disposed group, the document guard an R<->B
    conversion holds, and the attachment-room identity. Overwriting is refused while an AI run
    is active in the group (it may be reading that file); keeping both is always possible.
"""
from __future__ import annotations

import codecs
import hashlib
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from modules.flow_gate.db import attachment_upload_requests as db
from modules.flow_gate.db.connection import now_iso

from . import request_settings
from .constants import (
    ATTACH_COPY_CHUNK_BYTES,
    ATTACH_DEFAULT_CONTENT_TYPE,
    ATTACH_DOWNLOAD_FORCED_OCTET_TYPES,
    ATTACH_MAX_COUNT_PER_DOC,
    ATTACH_READ_MAX_BYTES,
    ATTACH_STREAM_CHUNK_BYTES,
    ATTACH_TEXT_SNIFF_BYTES,
)
from .errors import AttachmentError
from .naming import (
    is_executable_extension,
    original_display_name,
    resolve_content_type,
    rfc5987_disposition,
    sanitize_attachment_name,
    sanitize_path_component,
)

logger = logging.getLogger("flow_gate.attachment_requests")

ID_PREFIX = "aur_"
STAGING_DIR = "_attachment_requests"
BLOB_NAME = "blob"
BACKUP_NAME = "previous"
EVENT = "attachment_request_updated"
NOTE_MAX_CHARS = 500
RESERVED_PREFIX = "__wp_pre_instruction__"
CARD_SCOPES = ("new", "edit", "review")
REQUEST_KINDS = ("read", "read_write")
ORPHAN_GRACE_SEC = 3600
SWEEP_INTERVAL_SEC = 600
DECISIONS = ("approve", "reject")
CONFLICT_MODES = ("keep_both", "overwrite")
# Raster images a browser renders as an image and never as a document; recognised by their
# magic bytes, not by the extension the AI chose.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_JST = timezone(timedelta(hours=9))
_O_BINARY = getattr(os, "O_BINARY", 0)
_sweep_lock = threading.Lock()


class RequestError(Exception):
    """One refused request operation: HTTP status + stable code + message (+ details)."""

    def __init__(self, status: int, code: str, message: Optional[str] = None, **details: Any):
        super().__init__(message or code)
        self.status = int(status)
        self.code = code
        self.message = message or code
        self.details = {k: v for k, v in details.items() if v is not None}

    def body(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"ok": False, "error": error}


# ── small helpers ────────────────────────────────────────────────────────────

def _later(days: int) -> str:
    return (datetime.now(_JST) + timedelta(days=int(days))).isoformat(timespec="seconds")


def _new_id() -> str:
    return f"{ID_PREFIX}{uuid.uuid4().hex[:24]}"


def _valid_id(request_id: Any) -> bool:
    return (isinstance(request_id, str) and request_id.startswith(ID_PREFIX)
            and len(request_id) == len(ID_PREFIX) + 24
            and all(ch in "0123456789abcdef" for ch in request_id[len(ID_PREFIX):]))


def _emit(row: Optional[dict]) -> None:
    """Invalidation signal for screens (never an authoritative payload)."""
    if not row:
        return
    try:
        from modules.flow_gate.services import git_service

        git_service._emit(EVENT, row["project_id"], row["group_id"], {
            "project_id": row["project_id"], "group_id": row["group_id"],
            "doc_id": row.get("source_doc_id") if row.get("origin") == "chat" else None,
            "flow_id": row.get("target_flow_id"), "node_key": row.get("target_node_key"),
            "target_doc_id": row.get("target_doc_id"), "request_id": row["request_id"],
            "status": row["status"], "origin": row.get("origin"), "ai_run_id": row.get("ai_run_id"),
            "graph_run_id": row.get("graph_run_id"),
        })
    except Exception:
        logger.debug("attachment request event failed", exc_info=True)


def has_permission(user: Optional[dict], permission: str, project_id: Optional[str]) -> bool:
    if not user:
        return False
    try:
        from modules.flow_gate.rbac.decorators import _has_permission
    except Exception:
        return bool(user.get("is_admin"))
    return bool(_has_permission(user, permission, project_id))


def require_permission(user: Optional[dict], permission: str, project_id: Optional[str]) -> None:
    if not has_permission(user, permission, project_id):
        raise RequestError(403, "attachment_request_forbidden",
                           f"{permission} is required on this project", permission=permission)


def _user_id(user: Optional[dict]) -> Optional[str]:
    return (user or {}).get("user_id") or (user or {}).get("id")


# ── staging area (server-owned, outside every token scratch) ────────────────

def staging_root(project_id: str) -> Path:
    from modules.flow_gate.storage import paths as storage_paths

    return storage_paths.get_storage_root(project_id) / "work" / STAGING_DIR / sanitize_path_component(project_id)


def staging_dir(project_id: str, request_id: str) -> Path:
    if not _valid_id(request_id):
        raise RequestError(422, "attachment_request_invalid", "invalid request id")
    path = staging_root(project_id) / request_id
    from .locator import within_storage_jail

    if not within_storage_jail(path, project_id):
        raise RequestError(403, "attachment_request_storage_outside_root",
                           "the staging location resolves outside the storage root")
    return path


def _no_links(path: Path, stop: Path) -> bool:
    """No component between ``stop`` (exclusive) and ``path`` is a link."""
    current = path
    while True:
        if current.is_symlink():
            return False
        if current == stop or current.parent == current:
            return True
        current = current.parent


def staged_file(row: dict) -> Path:
    """The staged blob of a row, recomputed from its id (the stored path is only cross-checked)."""
    path = staging_dir(row["project_id"], row["request_id"]) / BLOB_NAME
    root = staging_root(row["project_id"])
    if not _no_links(path, root.parent):
        raise RequestError(403, "attachment_request_storage_outside_root", "the staged file path is a link")
    return path


def _remove_staging(project_id: str, request_id: str, *, keep_backup: bool = False) -> None:
    try:
        directory = staging_dir(project_id, request_id)
    except RequestError:
        return
    if not directory.exists() or directory.is_symlink():
        return
    for child in list(directory.iterdir()):
        if keep_backup and child.name == BACKUP_NAME:
            continue
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            logger.warning("staged file removal failed: %s", child, exc_info=True)
    try:
        directory.rmdir()
    except OSError:
        pass


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(ATTACH_COPY_CHUNK_BYTES)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


# ── who is asking (token -> requester) ──────────────────────────────────────

@dataclass
class Requester:
    origin: str
    project_id: str
    flow_id: str
    token_id: str
    ai_run_id: Optional[str]
    issued_to: Optional[str]
    provider_name: Optional[str]
    source_doc_id: Optional[str]
    graph_run_id: Optional[str] = None
    node_key: Optional[str] = None
    run_start_seq: Optional[int] = None
    run: dict = field(default_factory=dict)
    token: dict = field(default_factory=dict)


def _provider_name(run: dict) -> Optional[str]:
    provider = run.get("provider")
    if isinstance(provider, dict):
        provider = provider.get("name") or provider.get("id")
    value = provider or run.get("provider_name") or run.get("provider_id")
    return str(value)[:200] if value else None


def _graph_error(exc: Exception) -> RequestError:
    status = int(getattr(exc, "status", 403) or 403)
    code = str(getattr(exc, "code", "") or "forbidden")
    if code in ("run_finished", "chat_run_finished"):
        return RequestError(409, "attachment_request_run_finished",
                            "this AI run has already finished; requests come from a live run")
    if code in ("run_cancelled", "chat_run_cancelled"):
        return RequestError(409, "attachment_request_run_cancelled", "this AI run is being cancelled")
    if status == 401 or code == "unauthorized":
        return RequestError(401, "attachment_request_unauthorized", "a live AI run token is required")
    return RequestError(403, "attachment_request_forbidden", "this token may not request attachment uploads")


def _require_kind(token: dict) -> None:
    from modules.flow_gate.services import tool_registry

    kind, _reason = tool_registry.kind_for_token(token)
    if kind not in REQUEST_KINDS:
        raise RequestError(403, "attachment_request_forbidden",
                           "a token without source access cannot request attachment uploads")


def _chat_requester(run: dict, token: dict) -> Requester:
    return Requester(
        origin="chat", project_id=str(run.get("project_id") or token.get("project") or ""),
        flow_id=str(token.get("group_id") or run.get("group_id") or ""), token_id=str(token.get("token_id") or ""),
        ai_run_id=run.get("run_id"), issued_to=token.get("issued_to") or run.get("issued_to"),
        provider_name=_provider_name(run), source_doc_id=run.get("doc_ref") or token.get("doc_ref"),
        run_start_seq=run.get("chat_start_seq"), run=run, token=token,
    )


def _card_requester(run: dict, token: dict) -> Requester:
    from flowgate.graph_read import authz as graph_authz

    graph_authz._live(run)
    mapped = graph_authz._worker_binding(token, run)
    return Requester(
        origin="card", project_id=str(token.get("project") or token.get("project_id") or run.get("project_id") or ""),
        flow_id=str(mapped.get("group_id") or token.get("group_id") or ""), token_id=str(token.get("token_id") or ""),
        ai_run_id=run.get("run_id"), issued_to=token.get("issued_to") or run.get("issued_to"),
        provider_name=_provider_name(run), source_doc_id=token.get("doc_ref"),
        graph_run_id=mapped.get("run_id"), node_key=mapped.get("node_key"), run=run, token=token,
    )


def requester_from_raw_token(raw: Optional[str]) -> Requester:
    """HTTP transport (CLI providers, remote Agents): the Bearer of a live chat or card run."""
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    if not raw:
        raise RequestError(401, "attachment_request_unauthorized", "a live AI run token is required")
    try:
        from flowgate.card_document_edit import chat_authz
        from flowgate.graph_read import authz as graph_authz

        token = graph_authz.verify(raw)
        scope = token.get("action_scope")
        if scope == "chat":
            run, token = chat_authz.authorize_chat_run(chat_authz.CLI, raw, None)
            _require_kind(token)
            return _chat_requester(run, token)
        if scope in CARD_SCOPES:
            run = chat_authz._get_run_record(token.get("ai_run_id"))
            if run is None:
                raise RequestError(409, "attachment_request_run_finished", "this AI run has already finished")
            _require_kind(token)
            return _card_requester(run, token)
    except GraphApiError as exc:
        raise _graph_error(exc) from exc
    raise RequestError(403, "attachment_request_forbidden", "only chat and card AI runs can request attachment uploads")


def requester_from_run(run: dict) -> Requester:
    """API transport: the worker hands over its own live run record (in-process tool)."""
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError

    try:
        from flowgate.card_document_edit import chat_authz
        from modules.flow_gate.db import tokens as db_tokens

        if run.get("action_scope") == "chat":
            run, token = chat_authz.authorize_chat_run(chat_authz.API, None, run)
            _require_kind(token)
            return _chat_requester(run, token)
        if run.get("action_scope") in CARD_SCOPES:
            token_id = run.get("current_token_id") or run.get("token_id")
            token = db_tokens.get_by_id(token_id) if token_id else None
            if token is None or chat_authz.token_live(token) != chat_authz.LIVE:
                raise RequestError(401, "attachment_request_unauthorized", "the run token is no longer live")
            _require_kind(token)
            return _card_requester(run, token)
    except GraphApiError as exc:
        raise _graph_error(exc) from exc
    raise RequestError(403, "attachment_request_forbidden", "only chat and card AI runs can request attachment uploads")


# ── the card a request is aimed at ──────────────────────────────────────────

@dataclass
class TargetView:
    flow_id: str
    node_key: str
    exists: bool
    doc_id: Optional[str]
    title: Optional[str]
    doc_type: Optional[str] = None


def _project_card(flow_id: str, node_key: str) -> TargetView:
    """The card exactly as the Designer's Canvas read draws it (same projection, default view),
    read-only: no identity save, no reconcile, no job dispatch. Joins an open guard transaction."""
    from modules.flow_gate.graph_workflow.projection.flow_snapshot import protected_ids, read_snapshot
    from modules.flow_gate.graph_workflow.services import canvas_service
    from modules.flow_gate.graph_workflow.services.flow_guard import with_read_snapshot
    from modules.flow_gate.graph_workflow.services.identity_keeper import (
        IdentityResult,
        keys_from_record,
        sequence_tag_of,
    )
    from modules.flow_gate.graph_workflow.store import layout_store

    group = canvas_service.load_flow(flow_id)

    def read() -> TargetView:
        snap = read_snapshot(flow_id)
        project_id = group.get("project_id")
        allowed = {d["doc_id"] for d in snap.documents
                   if d.get("group_id") == flow_id and d.get("project_id") == project_id}
        protected = protected_ids(snap.rows)
        ident = IdentityResult(snap, keys_from_record(snap.rows, layout_store.identity_record(flow_id), protected),
                               protected, sequence_tag_of(snap))
        raw = canvas_service._read(group, None, ident, None, None,
                                   lookup=lambda ids: {k: v for k, v in snap.docs_by_id.items()
                                                       if k in ids and k in allowed})
        for node in raw.get("nodes") or []:
            if node.get("node_key") != node_key:
                continue
            document = node.get("document") or None
            doc_id = document.get("doc_id") if isinstance(document, dict) else None
            if doc_id not in allowed:
                doc_id = None
            return TargetView(flow_id, node_key, True, doc_id, node.get("title"),
                              (document or {}).get("type_code") if isinstance(document, dict) else node.get("doc_type"))
        return TargetView(flow_id, node_key, False, None, None)

    return with_read_snapshot(read)


# Seam for tests: the real resolver reads the Canvas projection.
card_resolver: Callable[[str, str], TargetView] = _project_card


def resolve_card(flow_id: str, node_key: str) -> TargetView:
    try:
        return card_resolver(flow_id, node_key)
    except RequestError:
        raise
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == "flow_not_found":
            raise RequestError(404, "attachment_target_flow_not_found", "the target Flow does not exist") from exc
        logger.warning("attachment request card resolve failed flow=%s node=%s", flow_id, node_key, exc_info=True)
        raise RequestError(409, "attachment_target_unavailable",
                           "the target card could not be read right now; try again") from exc


def _assert_flow_open(flow_id: str) -> None:
    from modules.flow_gate import process_service
    from modules.flow_gate.db import groups as db_groups

    group = db_groups.get_by_id(flow_id)
    if not group or group.get("deleted_at"):
        raise RequestError(404, "attachment_target_flow_not_found", "the target Flow does not exist")
    if db_groups.is_archived(flow_id):
        raise RequestError(409, "attachment_target_not_mutable", "the target Flow is archived", reason="flow_archived")
    if process_service.is_group_disposed(flow_id):
        raise RequestError(409, "attachment_target_not_mutable", "the target Flow is disposed", reason="group_disposed")


def _target_for(requester: Requester, target: Any) -> TargetView:
    if requester.origin == "card":
        if target not in (None, {}) and not (
            isinstance(target, dict) and set(target) <= {"flow_id", "node_key"}
            and target.get("flow_id") in (None, requester.flow_id)
            and target.get("node_key") in (None, requester.node_key)
        ):
            raise RequestError(422, "attachment_request_target_fixed",
                               "a card worker's attachment always goes to its own card; omit target")
        _assert_flow_open(requester.flow_id)
        view = resolve_card(requester.flow_id, str(requester.node_key or ""))
        if not view.exists:
            raise RequestError(409, "attachment_target_unavailable", "this run's card is no longer on the Flow")
        return view
    if not isinstance(target, dict) or not isinstance(target.get("node_key"), str) or not target["node_key"].strip():
        raise RequestError(422, "attachment_request_target_required",
                           "target.node_key (a card of this chat's Flow) is required")
    if set(target) - {"flow_id", "node_key"}:
        raise RequestError(422, "attachment_request_invalid", "target accepts flow_id and node_key only")
    flow_id = target.get("flow_id") or requester.flow_id
    if flow_id != requester.flow_id:
        raise RequestError(403, "attachment_target_outside_flow",
                           "a chat can only attach to cards of the Flow it belongs to")
    _assert_flow_open(flow_id)
    view = resolve_card(flow_id, target["node_key"].strip())
    if not view.exists:
        raise RequestError(404, "attachment_target_card_not_found", "no such card on this chat's Flow",
                           node_key=target["node_key"].strip())
    if not view.doc_id:
        raise RequestError(409, "attachment_target_document_missing",
                           "that card has no document yet; attachments belong to a card's document")
    return view


# ── intake ───────────────────────────────────────────────────────────────────

@dataclass
class Intake:
    filename: Optional[str]
    note: Optional[str]
    target: Any


def parse_meta(meta: Any) -> Intake:
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise RequestError(422, "attachment_request_invalid", "meta must be a JSON object")
    unknown = set(meta) - {"filename", "note", "target"}
    if unknown:
        raise RequestError(422, "attachment_request_invalid", f"unknown field: {sorted(unknown)[0]}")
    filename = meta.get("filename")
    if filename is not None and not isinstance(filename, str):
        raise RequestError(422, "attachment_request_invalid", "filename must be a string")
    note = meta.get("note")
    if note is not None:
        if not isinstance(note, str):
            raise RequestError(422, "attachment_request_invalid", "note must be a string")
        note = "".join(ch for ch in note if ch == "\n" or ord(ch) >= 32)[:NOTE_MAX_CHARS] or None
    return Intake(filename, note, meta.get("target"))


def _checked_name(raw: Optional[str], policy: dict) -> str:
    safe = sanitize_attachment_name(raw)
    if safe.startswith(RESERVED_PREFIX):
        raise RequestError(422, "attachment_request_filename_reserved",
                           "this filename prefix is reserved", filename=safe)
    if len(safe.encode("utf-8")) > int(policy["max_filename_bytes"]):
        raise RequestError(422, "attachment_request_filename_too_long",
                           f"the filename is longer than {policy['max_filename_bytes']} bytes", filename=safe,
                           limit=int(policy["max_filename_bytes"]))
    ext = Path(safe).suffix.lstrip(".").lower()
    if ext and ext in set(policy.get("blocked_extensions") or []):
        raise RequestError(422, "attachment_request_extension_blocked",
                           f"files with the .{ext} extension cannot be requested in this project", filename=safe)
    return safe


def _check_quotas(requester: Requester, view: TargetView, policy: dict, size_hint: Optional[int]) -> None:
    if requester.ai_run_id and db.count_for_run(requester.ai_run_id) >= int(policy["max_requests_per_run"]):
        raise RequestError(429, "attachment_request_run_limit",
                           f"this run already made {policy['max_requests_per_run']} attachment requests",
                           limit=int(policy["max_requests_per_run"]))
    if db.count_open_for_node(view.flow_id, view.node_key) >= int(policy["max_pending_per_target"]):
        raise RequestError(429, "attachment_request_pending_limit",
                           f"this card already has {policy['max_pending_per_target']} requests waiting for a decision",
                           limit=int(policy["max_pending_per_target"]))
    if size_hint is not None:
        total = db.pending_bytes_for_project(requester.project_id) + int(size_hint)
        if total > int(policy["max_pending_bytes_per_project"]):
            raise RequestError(507, "attachment_request_storage_limit",
                               "the project's waiting attachment requests use up their storage allowance",
                               limit_bytes=int(policy["max_pending_bytes_per_project"]))


class _Sink:
    """Streams one upload into the staging directory: O_EXCL create, size cap, sha256."""

    def __init__(self, path: Path, limit: int):
        self.path = path
        self.limit = int(limit)
        self.size = 0
        self.digest = hashlib.sha256()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY)

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.limit:
            raise RequestError(413, "attachment_request_too_large",
                               f"the file exceeds the {self.limit} byte limit", limit_bytes=self.limit)
        self.digest.update(chunk)
        os.write(self.fd, chunk)

    def finish(self) -> tuple[int, str]:
        os.fsync(self.fd)
        os.close(self.fd)
        self.fd = None
        return self.size, self.digest.hexdigest()

    def abort(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


def _prepare(requester: Requester, intake: Intake, raw_name: Optional[str], size_hint: Optional[int]):
    policy = request_settings.effective(requester.project_id)
    safe = _checked_name(intake.filename or raw_name, policy)
    view = _target_for(requester, intake.target)
    if size_hint is not None and size_hint > int(policy["max_file_bytes"]):
        raise RequestError(413, "attachment_request_too_large",
                           f"the file exceeds the {policy['max_file_bytes']} byte limit",
                           limit_bytes=int(policy["max_file_bytes"]))
    _check_quotas(requester, view, policy, size_hint)
    return policy, safe, view


def _store(requester: Requester, intake: Intake, raw_name: Optional[str], safe: str, view: TargetView,
           policy: dict, request_id: str, size: int, sha256: str) -> dict:
    from modules.flow_gate.storage import paths as storage_paths

    # The real size is known now; the project allowance is checked again with it.
    total = db.pending_bytes_for_project(requester.project_id) + int(size)
    if total > int(policy["max_pending_bytes_per_project"]):
        raise RequestError(507, "attachment_request_storage_limit",
                           "the project's waiting attachment requests use up their storage allowance",
                           limit_bytes=int(policy["max_pending_bytes_per_project"]))
    blob = staging_dir(requester.project_id, request_id) / BLOB_NAME
    row = db.create(
        request_id=request_id, project_id=requester.project_id, group_id=view.flow_id, origin=requester.origin,
        source_doc_id=requester.source_doc_id, target_flow_id=view.flow_id, target_node_key=view.node_key,
        target_doc_id=view.doc_id, target_title=(view.title or None), ai_run_id=requester.ai_run_id,
        graph_run_id=requester.graph_run_id, token_id=requester.token_id, requested_by=requester.issued_to,
        provider_name=requester.provider_name, original_filename=original_display_name(intake.filename or raw_name),
        filename=safe, size=size, content_type=resolve_content_type(safe), content_sha256=sha256,
        staging_path=storage_paths.to_storage_relative(blob, requester.project_id), note=intake.note,
        run_start_seq=requester.run_start_seq if requester.origin == "chat" else None,
        expires_at=_later(int(policy["request_retention_days"])),
    )
    _emit(row)
    return row


def create_from_stream(requester: Requester, meta: Any, raw_name: Optional[str], read_chunk: Callable[[int], bytes],
                       size_hint: Optional[int] = None) -> dict:
    """Synchronous intake of one file whose bytes come from ``read_chunk(n)`` (b"" = end)."""
    intake = parse_meta(meta)
    policy, safe, view = _prepare(requester, intake, raw_name, size_hint)
    request_id = _new_id()
    blob = staging_dir(requester.project_id, request_id) / BLOB_NAME
    sink = _Sink(blob, int(policy["max_file_bytes"]))
    try:
        while True:
            chunk = read_chunk(ATTACH_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            sink.write(chunk)
        size, sha256 = sink.finish()
        return _store(requester, intake, raw_name, safe, view, policy, request_id, size, sha256)
    except BaseException:
        sink.abort()
        _remove_staging(requester.project_id, request_id)
        raise


async def acreate_from_upload(requester: Requester, meta: Any, upload) -> dict:
    """Async intake of a multipart part (``UploadFile``); blocking work runs off the event loop."""
    import anyio.to_thread

    intake = parse_meta(meta)
    raw_name = getattr(upload, "filename", None)
    policy, safe, view = await anyio.to_thread.run_sync(_prepare, requester, intake, raw_name, None)
    request_id = _new_id()
    blob = staging_dir(requester.project_id, request_id) / BLOB_NAME
    sink = await anyio.to_thread.run_sync(_Sink, blob, int(policy["max_file_bytes"]))
    try:
        while True:
            chunk = await upload.read(ATTACH_STREAM_CHUNK_BYTES)
            if not chunk:
                break
            await anyio.to_thread.run_sync(sink.write, chunk)
        size, sha256 = await anyio.to_thread.run_sync(sink.finish)
        return await anyio.to_thread.run_sync(
            _store, requester, intake, raw_name, safe, view, policy, request_id, size, sha256)
    except BaseException:
        sink.abort()
        await anyio.to_thread.run_sync(_remove_staging, requester.project_id, request_id)
        raise


def _source_root(requester: Requester) -> Path:
    """The worktree an API-provider run works in — the only place its files can come from."""
    from modules.flow_gate.services import graph_source_hooks

    run, token = requester.run, requester.token
    root = None
    try:
        root = graph_source_hooks.source_root(token, project_id=requester.project_id, group_id=run.get("group_id"))
    except graph_source_hooks.GraphSourceError as exc:
        raise RequestError(409, "attachment_request_source_unavailable", exc.message) from exc
    if root is None:
        recorded = run.get("source_root")
        if recorded:
            root = Path(str(recorded))
        else:
            from modules.flow_gate.services import git_service

            found, _reason = git_service.effective_src_root_ex(requester.project_id, run.get("group_id"))
            root = found
    if not root or not Path(root).is_dir():
        raise RequestError(409, "attachment_request_source_unavailable", "this run has no source tree to read the file from")
    return Path(root).resolve()


def create_from_source_path(requester: Requester, tool_input: Any) -> dict:
    """API providers: ``{source_path, filename?, note?, target?}`` — a file of the run's worktree."""
    from .service import _seal_under_root, _validate_target_path

    if not isinstance(tool_input, dict):
        raise RequestError(422, "attachment_request_invalid", "input must be an object")
    payload = dict(tool_input)
    source_path = payload.pop("source_path", None)
    try:
        segments = _validate_target_path(source_path)
    except AttachmentError as exc:
        raise RequestError(422, "attachment_request_invalid_path", "source_path must be a worktree-relative path",
                           reason=exc.details.get("reason")) from exc
    root = _source_root(requester)
    try:
        path = _seal_under_root(root, segments)
    except AttachmentError as exc:
        raise RequestError(403, "attachment_request_path_outside_root", "source_path resolves outside the worktree") from exc
    if not path.is_file() or path.is_symlink():
        raise RequestError(404, "attachment_request_source_not_found", "no regular file at source_path",
                           source_path="/".join(segments))
    size_hint = path.stat().st_size
    if "filename" not in payload or payload.get("filename") in (None, ""):
        payload["filename"] = segments[-1]
    with open(path, "rb") as fh:
        return create_from_stream(requester, payload, segments[-1], fh.read, size_hint=size_hint)


# ── views ────────────────────────────────────────────────────────────────────

def _previewable(row: dict) -> Optional[str]:
    """A hint for the screen; the preview call itself decides from the bytes."""
    ext = Path(row.get("filename") or "").suffix.lstrip(".").lower()
    return "image" if ext in ("png", "jpg", "jpeg", "gif", "webp") else "text"


def _conflict(row: dict) -> Optional[dict]:
    """Same-name attachment already on the request's target document (at request time)."""
    doc_id = row.get("target_doc_id")
    if row.get("status") not in db.OPEN_STATUSES or not doc_id:
        return None
    from .registry import registry_list

    try:
        folded = (row.get("filename") or "").casefold()
        for existing in registry_list(doc_id):
            name = existing.get("filename") or ""
            if name.casefold() == folded:
                return {"doc_id": doc_id, "filename": name, "case_only": name != row.get("filename"),
                        "size": int(existing.get("size") or 0), "content_sha256": existing.get("content_sha256"),
                        "uploaded_at": existing.get("uploaded_at")}
    except Exception:
        logger.debug("conflict lookup failed", exc_info=True)
    return None


def public(row: dict, *, with_conflict: bool = True) -> dict:
    return {
        "request_id": row["request_id"], "origin": row.get("origin"), "status": row.get("status"),
        "project_id": row.get("project_id"), "flow_id": row.get("target_flow_id"),
        "node_key": row.get("target_node_key"), "target_doc_id": row.get("target_doc_id"),
        "target_title": row.get("target_title"), "source_doc_id": row.get("source_doc_id"),
        "ai_run_id": row.get("ai_run_id"), "graph_run_id": row.get("graph_run_id"),
        "provider_name": row.get("provider_name"), "requested_by": row.get("requested_by"),
        "original_filename": row.get("original_filename"), "filename": row.get("filename"),
        "size": int(row.get("size") or 0), "content_type": row.get("content_type"),
        "content_sha256": row.get("content_sha256"), "note": row.get("note"),
        "decision": row.get("decision"), "decided_by": row.get("decided_by"), "decided_at": row.get("decided_at"),
        "final_doc_id": row.get("final_doc_id"), "final_filename": row.get("final_filename"),
        "error_code": row.get("error_code"), "expires_at": row.get("expires_at"),
        "backup_expires_at": row.get("backup_expires_at"),
        "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
        "preview": _previewable(row),
        "conflict": _conflict(row) if with_conflict else None,
    }


def ai_result(row: dict) -> dict:
    """What the requesting AI gets back: stored, waiting, and that it must not wait."""
    title = row.get("target_title") or row.get("target_node_key")
    return {
        "ok": True,
        "request_id": row["request_id"],
        "status": row["status"],
        "filename": row["filename"],
        "size": int(row.get("size") or 0),
        "content_sha256": row["content_sha256"],
        "target": {"flow_id": row["target_flow_id"], "node_key": row["target_node_key"],
                   "doc_id": row.get("target_doc_id"), "title": row.get("target_title")},
        "expires_at": row.get("expires_at"),
        "message": (f"Stored and waiting for a person's approval to attach it to '{title}'. "
                    "Do not wait for the decision and do not re-send the same file: continue your work. "
                    "The decision may come after this run ends; mention the request in your reply."),
    }


def get_row(request_id: str) -> dict:
    if not _valid_id(request_id):
        raise RequestError(404, "attachment_request_not_found", "attachment request not found")
    row = db.get(request_id)
    if row is None:
        raise RequestError(404, "attachment_request_not_found", "attachment request not found")
    return row


def readable_row(request_id: str, user: dict) -> dict:
    row = get_row(request_id)
    require_permission(user, "perm_document_read", row["project_id"])
    return row


def list_for_node(flow_id: str, node_key: str, user: dict, *, include_closed: bool = False) -> dict:
    from modules.flow_gate.db import groups as db_groups

    group = db_groups.get_by_id(flow_id)
    if not group:
        raise RequestError(404, "attachment_target_flow_not_found", "the Flow does not exist")
    require_permission(user, "perm_document_read", group.get("project_id"))
    statuses = None if include_closed else list(db.OPEN_STATUSES)
    rows = db.list_for_node(flow_id, node_key, statuses)
    return {"ok": True, "flow_id": flow_id, "node_key": node_key,
            "requests": [public(r) for r in rows], "pending": sum(1 for r in rows if r["status"] in db.OPEN_STATUSES)}


def pending_counts(flow_id: str, user: dict) -> dict:
    from modules.flow_gate.db import groups as db_groups

    group = db_groups.get_by_id(flow_id)
    if not group:
        raise RequestError(404, "attachment_target_flow_not_found", "the Flow does not exist")
    require_permission(user, "perm_document_read", group.get("project_id"))
    by_node = db.count_pending_by_node(flow_id)
    return {"ok": True, "flow_id": flow_id, "total": sum(by_node.values()), "by_node": by_node}


def chat_groups(doc_id: str) -> list[dict]:
    """The chat timeline block: requests of one CH grouped by run, each placed right after the
    run's (last) AI reply — the anchor a run change uses (``activity.compute(KIND_CHANGE)``)."""
    from flowgate.conversation import activity
    from flowgate.conversation import repository as repo

    rows = db.list_for_source_doc(doc_id)
    groups: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        key = row.get("ai_run_id") or row["request_id"]
        if key not in groups:
            groups[key] = {"run_id": row.get("ai_run_id"), "run_start_seq": row.get("run_start_seq"), "rows": []}
            order.append(key)
        groups[key]["rows"].append(row)
    out = []
    for key in order:
        group = groups[key]
        replies: list[int] = []
        if group["run_id"]:
            try:
                replies = repo.reply_seqs(doc_id, group["run_id"])
            except Exception:
                replies = []
        seq, position, state = activity.compute(activity.KIND_CHANGE, replies, group["run_start_seq"])
        items = [public(r) for r in group["rows"]]
        out.append({
            "id": key, "run_id": group["run_id"], "anchor_seq": seq, "anchor_position": position,
            "anchor_state": state, "created_at": group["rows"][0].get("created_at"),
            "pending": sum(1 for r in group["rows"] if r["status"] in db.OPEN_STATUSES),
            "requests": items,
        })
    return out


def list_for_chat(doc_id: str, user: dict) -> dict:
    from modules.flow_gate.db import documents as db_documents

    doc = db_documents.get_by_id(doc_id)
    if not doc:
        raise RequestError(404, "attachment_request_not_found", "chat document not found")
    require_permission(user, "perm_document_read", doc.get("project_id"))
    return {"ok": True, "doc_id": doc_id, "groups": chat_groups(doc_id)}


# ── preview / download before the decision ──────────────────────────────────

def _image_type(head: bytes) -> Optional[str]:
    for magic, media in _IMAGE_MAGIC:
        if head.startswith(magic):
            return media
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"),
)


def preview(request_id: str, user: dict) -> dict:
    """Text up to 1 MiB (flagged when cut), or the image kind, or ``binary`` (download only).
    Never HTML: the screen shows the text as text."""
    row = readable_row(request_id, user)
    if row["status"] not in db.OPEN_STATUSES:
        raise RequestError(410, "attachment_request_closed", "the request is closed; its staged file is gone")
    path = staged_file(row)
    if not path.is_file():
        raise RequestError(410, "attachment_request_file_missing", "the staged file is gone")
    with open(path, "rb") as fh:
        data = fh.read(ATTACH_READ_MAX_BYTES + 1)
    truncated = len(data) > ATTACH_READ_MAX_BYTES
    data = data[:ATTACH_READ_MAX_BYTES]
    base = {"ok": True, "request_id": row["request_id"], "filename": row["filename"], "size": int(row["size"])}
    image = _image_type(data[:16])
    if image is not None:
        return {**base, "kind": "image", "media_type": image, "content": None, "truncated": False}
    head = data[:ATTACH_TEXT_SNIFF_BYTES]
    encoding = next((name for bom, name in _BOMS if head.startswith(bom)), None)
    if encoding is None and b"\x00" in head:
        return {**base, "kind": "binary", "media_type": None, "content": None, "truncated": False}
    for candidate in ([encoding] if encoding else []) + ["utf-8", "cp949"]:
        try:
            text = data.decode(candidate, errors="strict" if not truncated else "ignore")
        except (UnicodeDecodeError, LookupError):
            continue
        return {**base, "kind": "text", "encoding": candidate, "content": text.lstrip("﻿"),
                "truncated": truncated, "media_type": None}
    return {**base, "kind": "binary", "media_type": None, "content": None, "truncated": False}


def content(request_id: str, user: dict, inline: bool = False) -> tuple[Path, dict]:
    """(path, response meta) for the staged bytes. Served like an attachment download:
    executable / markup kinds as octet-stream, ``attachment`` disposition and ``nosniff``.
    ``inline`` only for a verified raster image."""
    row = readable_row(request_id, user)
    if row["status"] not in db.OPEN_STATUSES:
        raise RequestError(410, "attachment_request_closed", "the request is closed; its staged file is gone")
    path = staged_file(row)
    if not path.is_file():
        raise RequestError(410, "attachment_request_file_missing", "the staged file is gone")
    media = row.get("content_type") or ATTACH_DEFAULT_CONTENT_TYPE
    if media in ATTACH_DOWNLOAD_FORCED_OCTET_TYPES or is_executable_extension(row["filename"]):
        media = ATTACH_DEFAULT_CONTENT_TYPE
    disposition = rfc5987_disposition(row["filename"])
    if inline:
        with open(path, "rb") as fh:
            image = _image_type(fh.read(16))
        if image is not None:
            media = image
            disposition = "inline"
    headers = {
        "Content-Disposition": disposition,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cache-Control": "no-store",
        "ETag": f'"sha256-{row["content_sha256"]}"',
    }
    return path, {"media_type": media, "headers": headers}


# ── decision ─────────────────────────────────────────────────────────────────

def _load_doc(doc_id: str) -> dict:
    from modules.flow_gate.db import documents as db_documents

    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise RequestError(409, "attachment_target_changed", "the target document no longer exists",
                           expected_doc_id=doc_id)
    return doc


def _assert_doc_mutable(doc: dict) -> None:
    from fastapi import HTTPException

    from modules.flow_gate.documents.routers import documents as _documents

    try:
        _documents._reject_if_group_disposed(doc)
    except HTTPException as exc:
        reason = exc.detail.get("code") if isinstance(exc.detail, dict) else "group_disposed"
        raise RequestError(409, "attachment_target_not_mutable", "the target document cannot be changed now",
                           reason=reason)


def _ai_active(group_id: str) -> bool:
    try:
        from modules.flow_gate.services import ai_invoke_service

        return bool((ai_invoke_service.get_active_status(group_id) or {}).get("active"))
    except Exception:
        return True       # unknown: be careful, refuse the overwrite (keep both stays possible)


def _target_check(row: dict, expected_doc_id: str) -> TargetView:
    view = resolve_card(row["target_flow_id"], row["target_node_key"])
    if not view.exists:
        raise RequestError(409, "attachment_target_unavailable", "the target card is no longer on the Flow",
                           expected_doc_id=expected_doc_id)
    if not view.doc_id:
        raise RequestError(409, "attachment_target_document_missing", "the target card has no document now",
                           expected_doc_id=expected_doc_id)
    if view.doc_id != expected_doc_id:
        raise RequestError(409, "attachment_target_changed",
                           "the card's document changed; check the new target and approve again",
                           expected_doc_id=expected_doc_id, current_doc_id=view.doc_id, current_title=view.title)
    return view


def _decision_input(body: Any) -> tuple[str, Optional[str], str, Optional[str]]:
    if not isinstance(body, dict):
        raise RequestError(422, "attachment_request_invalid", "the body must be an object")
    unknown = set(body) - {"decision", "expected_doc_id", "conflict", "expected_existing_sha256"}
    if unknown:
        raise RequestError(422, "attachment_request_invalid", f"unknown field: {sorted(unknown)[0]}")
    decision = body.get("decision")
    if decision not in DECISIONS:
        raise RequestError(422, "attachment_request_invalid_decision", "decision must be approve or reject")
    expected = body.get("expected_doc_id")
    conflict = body.get("conflict") or "keep_both"
    if conflict not in CONFLICT_MODES:
        raise RequestError(422, "attachment_request_invalid", "conflict must be keep_both or overwrite")
    existing_sha = body.get("expected_existing_sha256")
    if decision == "approve":
        if not isinstance(expected, str) or not expected:
            raise RequestError(422, "attachment_request_invalid", "expected_doc_id is required to approve")
        if conflict == "overwrite" and (not isinstance(existing_sha, str) or len(existing_sha) != 64):
            raise RequestError(422, "attachment_request_invalid",
                               "expected_existing_sha256 is required to overwrite")
    return decision, expected, conflict, existing_sha


def decide(request_id: str, user: dict, body: Any) -> dict:
    """A person's approve / reject. Any user with ``perm_document_update`` on the project may
    decide, whichever screen mode they are in and whoever started the run."""
    decision, expected_doc_id, conflict, existing_sha = _decision_input(body)
    row = get_row(request_id)
    require_permission(user, "perm_document_update", row["project_id"])
    if row["status"] != db.STATUS_PENDING:
        raise RequestError(409, "attachment_request_already_decided", f"the request is {row['status']}",
                           current_status=row["status"])
    actor = _user_id(user)
    if decision == "reject":
        updated = db.transition(request_id, (db.STATUS_PENDING,), db.STATUS_REJECTED,
                                decided_by=actor, decided_at=now_iso())
        if updated is None:
            current = db.get(request_id) or {}
            raise RequestError(409, "attachment_request_already_decided", f"the request is {current.get('status')}",
                               current_status=current.get("status"))
        _remove_staging(row["project_id"], request_id)
        _emit(updated)
        return updated
    return _approve(row, actor, expected_doc_id, conflict, existing_sha)


# ── bulk decision (approve all / reject all / the selected ones) ─────────────
#
# One call, one result per request: the screen never loops over single decisions, so a
# partial failure cannot leave it guessing. Every item goes through ``decide`` (permission,
# CAS, target re-check, document guard) exactly as a single decision does. A bulk approval
# is always "keep both" and never decides what needs a person's look: a same-name attachment
# on the target, or a target that changed, is skipped and left pending for its own row.

BULK_MAX_ITEMS = 100
BULK_SKIP_CODES = frozenset({
    "attachment_name_conflict",
    "attachment_target_changed",
    "attachment_target_unavailable",
    "attachment_target_document_missing",
    "attachment_request_already_decided",
})


def _bulk_input(body: Any) -> tuple[str, list[tuple[str, Optional[str]]]]:
    if not isinstance(body, dict):
        raise RequestError(422, "attachment_request_invalid", "the body must be an object")
    unknown = set(body) - {"decision", "items"}
    if unknown:
        raise RequestError(422, "attachment_request_invalid", f"unknown field: {sorted(unknown)[0]}")
    decision = body.get("decision")
    if decision not in DECISIONS:
        raise RequestError(422, "attachment_request_invalid_decision", "decision must be approve or reject")
    items = body.get("items")
    if not isinstance(items, list) or not items:
        raise RequestError(422, "attachment_request_invalid", "items must be a non-empty list")
    if len(items) > BULK_MAX_ITEMS:
        raise RequestError(422, "attachment_request_too_many", f"at most {BULK_MAX_ITEMS} requests at once",
                           max_items=BULK_MAX_ITEMS)
    out: list[tuple[str, Optional[str]]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or set(item) - {"request_id", "expected_doc_id"}:
            raise RequestError(422, "attachment_request_invalid", "each item is {request_id, expected_doc_id}")
        request_id = item.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise RequestError(422, "attachment_request_invalid", "request_id is required")
        expected = item.get("expected_doc_id")
        if decision == "approve" and (not isinstance(expected, str) or not expected):
            raise RequestError(422, "attachment_request_invalid", "expected_doc_id is required to approve")
        if request_id in seen:
            continue
        seen.add(request_id)
        out.append((request_id, expected if isinstance(expected, str) else None))
    return decision, out


def _same_name_on(doc_id: str, filename: str) -> Optional[str]:
    from .registry import registry_list

    folded = (filename or "").casefold()
    for existing in registry_list(doc_id):
        name = existing.get("filename") or ""
        if name.casefold() == folded:
            return name
    return None


def _bulk_one(request_id: str, user: dict, decision: str, expected_doc_id: Optional[str]) -> dict:
    if decision == "approve":
        row = get_row(request_id)
        require_permission(user, "perm_document_update", row["project_id"])
        existing = _same_name_on(expected_doc_id, row.get("filename") or "") if row["status"] == db.STATUS_PENDING else None
        if existing:
            raise RequestError(409, "attachment_name_conflict",
                               "a same-name attachment exists; choose keep both or overwrite on its own row",
                               existing_filename=existing)
        return decide(request_id, user, {"decision": "approve", "expected_doc_id": expected_doc_id,
                                         "conflict": "keep_both"})
    return decide(request_id, user, {"decision": "reject"})


def decide_many(user: dict, body: Any) -> dict:
    """Approve or reject several requests in one call; never all-or-nothing. Each result is
    ``done`` (with the request), ``skipped`` (left pending for a person's own look, or already
    decided) or ``failed`` (with the refusal)."""
    decision, items = _bulk_input(body)
    results: list[dict] = []
    for request_id, expected_doc_id in items:
        try:
            updated = _bulk_one(request_id, user, decision, expected_doc_id)
            results.append({"request_id": request_id, "outcome": "done", "request": public(updated)})
        except RequestError as exc:
            outcome = "skipped" if exc.code in BULK_SKIP_CODES else "failed"
            results.append({"request_id": request_id, "outcome": outcome, "status": exc.status, **exc.body()})
        except Exception:
            logger.error("attachment request bulk decision failed: %s", request_id, exc_info=True)
            results.append({"request_id": request_id, "outcome": "failed", "status": 500,
                            **RequestError(500, "attachment_request_decision_failed",
                                           "the result of this request could not be confirmed").body()})
    counts = {key: sum(1 for r in results if r["outcome"] == key) for key in ("done", "skipped", "failed")}
    return {"ok": True, "decision": decision, "results": results, "counts": counts}


def _approve(row: dict, actor: Optional[str], expected_doc_id: str, conflict: str,
             existing_sha: Optional[str]) -> dict:
    from .locator import resolve_attach_dir

    request_id = row["request_id"]
    # ① Pre-checks outside the guard (cheap refusals; everything is checked again inside it).
    doc = _load_doc(expected_doc_id)
    if doc.get("group_id") != row["target_flow_id"] or doc.get("project_id") != row["project_id"]:
        raise RequestError(409, "attachment_target_changed", "that document is not on the target card's Flow",
                           expected_doc_id=expected_doc_id)
    _assert_doc_mutable(doc)
    _target_check(row, expected_doc_id)
    if conflict == "overwrite":
        if _ai_active(row["group_id"]):
            raise RequestError(409, "attachment_overwrite_blocked_ai_running",
                               "an AI run is working in this Flow; keep both now, or overwrite after it ends")
        _check_overwrite_target(expected_doc_id, row["filename"], existing_sha)
    blob = staged_file(row)
    if not blob.is_file():
        raise RequestError(410, "attachment_request_file_missing", "the staged file is gone")

    # ② This decider owns the request from here on (CAS); the loser gets 409.
    temp_name = f".aur-{request_id}.part"
    claimed = db.transition(request_id, (db.STATUS_PENDING,), db.STATUS_APPROVING,
                            decided_by=actor, decided_at=now_iso(), decision=conflict,
                            final_doc_id=expected_doc_id, approve_phase="copying",
                            approve_temp_name=temp_name, error_code=None)
    if claimed is None:
        current = db.get(request_id) or {}
        raise RequestError(409, "attachment_request_already_decided", f"the request is {current.get('status')}",
                           current_status=current.get("status"))
    _emit(claimed)

    temp_path: Optional[Path] = None
    temp_identity = None
    placed: dict = {}
    try:
        # ③ Copy into the document's room under a temporary name, re-verifying the bytes.
        room = resolve_attach_dir(doc)
        room.mkdir(parents=True, exist_ok=True)
        temp_path = room / temp_name
        temp_identity = _copy_verified(blob, temp_path, row["content_sha256"], int(row["size"]))

        # ④ Commit under the document guard an R<->B conversion holds.
        updated = _commit_approval(row, actor, expected_doc_id, conflict, existing_sha, room, temp_path,
                                   temp_identity, placed)
    except BaseException as exc:
        _undo_files(row, temp_path, temp_identity, placed)
        reverted = db.transition(request_id, (db.STATUS_APPROVING,), db.STATUS_PENDING,
                                 decided_by=None, decided_at=None, decision=None, final_doc_id=None,
                                 approve_phase=None, approve_temp_name=None,
                                 error_code=getattr(exc, "code", None) or "attachment_request_approve_failed")
        _emit(reverted)
        if isinstance(exc, (RequestError, AttachmentError)):
            raise
        if isinstance(exc, PermissionError):
            raise RequestError(409, "attachment_file_busy",
                               "the existing file is in use (for example being downloaded); try again") from exc
        logger.error("attachment request approve failed: %s", request_id, exc_info=True)
        raise RequestError(500, "attachment_request_approve_failed",
                           "the attachment could not be registered; nothing was changed") from exc

    # ⑤ The staged copy is no longer needed (an overwrite's backup of the previous file stays).
    _remove_staging(row["project_id"], request_id, keep_backup=True)
    _emit(updated)
    return updated


def _check_overwrite_target(doc_id: str, filename: str, existing_sha: Optional[str]) -> dict:
    from .registry import registry_get, registry_list

    if filename.startswith(RESERVED_PREFIX):
        raise RequestError(409, "attachment_overwrite_refused", "reserved attachments cannot be overwritten")
    existing = registry_get(doc_id, filename)
    if existing is None:
        folded = filename.casefold()
        other = next((r for r in registry_list(doc_id) if (r.get("filename") or "").casefold() == folded), None)
        if other is not None:
            raise RequestError(409, "attachment_overwrite_case_mismatch",
                               "only the letter case differs from the existing name; keep both instead",
                               existing_filename=other.get("filename"))
        raise RequestError(409, "attachment_existing_changed",
                           "there is no attachment with that name any more; keep both instead")
    if existing.get("content_sha256") != existing_sha:
        raise RequestError(409, "attachment_existing_changed",
                           "the existing attachment changed since you looked; check it again",
                           current_sha256=existing.get("content_sha256"))
    return existing


def _copy_verified(src: Path, dest: Path, expected_sha: str, expected_size: int) -> tuple:
    from modules.flow_gate.storage.files import file_identity

    digest = hashlib.sha256()
    size = 0
    fd = os.open(str(dest), os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY)
    try:
        with open(src, "rb") as fh:
            while True:
                chunk = fh.read(ATTACH_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                os.write(fd, chunk)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    os.close(fd)
    if digest.hexdigest() != expected_sha or size != expected_size:
        try:
            dest.unlink()
        except OSError:
            pass
        raise RequestError(409, "attachment_request_file_changed",
                           "the staged file does not match what was requested; reject it")
    return file_identity(dest)


def _commit_approval(row: dict, actor: Optional[str], expected_doc_id: str, conflict: str,
                     existing_sha: Optional[str], room: Path, temp_path: Path, temp_identity, placed: dict) -> dict:
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.graph_workflow.services.flow_guard import GuardNotHeld, run_in_document_guard
    from modules.flow_gate.storage import paths as storage_paths
    from modules.flow_gate.storage.files import file_identity

    from .locator import resolve_attach_dir, resolve_registered_attachment
    from .naming import resolve_unique_name
    from .registry import registry_count, registry_has, registry_insert, registry_replace

    store = get_store()
    request_id = row["request_id"]
    project_id = row["project_id"]
    uploaded_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def body() -> dict:
        fresh = _load_doc(expected_doc_id)
        _assert_doc_mutable(fresh)
        if resolve_attach_dir(fresh).resolve(strict=False) != room.resolve(strict=False):
            raise RequestError(409, "attachment_target_changed", "the document's attachment folder changed",
                               expected_doc_id=expected_doc_id)
        _target_check(row, expected_doc_id)
        current = db.get(request_id) or {}
        if current.get("status") != db.STATUS_APPROVING or current.get("decided_by") != actor:
            raise RequestError(409, "attachment_request_already_decided", "the request changed meanwhile")
        policy = request_settings.effective(project_id)
        fields: dict = {"approve_phase": None, "approve_temp_name": None, "error_code": None}
        if conflict == "keep_both":
            limit = min(int(policy["max_attachments_per_doc"]), ATTACH_MAX_COUNT_PER_DOC)
            if registry_count(expected_doc_id) >= limit:
                raise RequestError(409, "attachment_doc_full",
                                   f"the document already holds {limit} attachments", limit=limit)
            final_name, fd = resolve_unique_name(room, expected_doc_id, row["filename"], set(), registry_has)
            os.close(fd)
            final_path = room / final_name
            placed["final_path"] = final_path
            placed["placeholder"] = file_identity(final_path)
            os.replace(temp_path, final_path)          # same directory: atomic
            placed["final_identity"] = temp_identity
            placed["placeholder"] = None
            registry_insert(
                doc_id=expected_doc_id, original_filename=row["original_filename"], filename=final_name,
                file_path=storage_paths.to_storage_relative(final_path, project_id), size=int(row["size"]),
                content_type=row["content_type"], content_sha256=row["content_sha256"], uploaded_by=actor,
                uploaded_at=uploaded_at, store=store,
            )
            fields["final_filename"] = final_name
        else:
            if _ai_active(row["group_id"]):
                raise RequestError(409, "attachment_overwrite_blocked_ai_running",
                                   "an AI run started in this Flow; keep both now, or overwrite after it ends")
            existing = _check_overwrite_target(expected_doc_id, row["filename"], existing_sha)
            _reg, existing_path = resolve_registered_attachment(fresh, existing["filename"])
            backup = staging_dir(project_id, request_id) / BACKUP_NAME
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                backup.unlink()
            sha_before, _size = _copy_plain(existing_path, backup)
            if sha_before != existing.get("content_sha256"):
                raise RequestError(409, "attachment_existing_changed",
                                   "the existing attachment's file does not match its record; nothing was changed")
            placed["backup"] = backup
            placed["existing_path"] = existing_path
            os.replace(temp_path, existing_path)       # same directory: atomic
            placed["replaced"] = True
            changed = registry_replace(
                doc_id=expected_doc_id, filename=existing["filename"], expected_sha256=existing_sha,
                original_filename=row["original_filename"], size=int(row["size"]),
                content_type=row["content_type"], content_sha256=row["content_sha256"],
                uploaded_by=actor, uploaded_at=uploaded_at, store=store,
            )
            if changed != 1:
                raise RequestError(409, "attachment_existing_changed", "the existing attachment changed meanwhile")
            fields["final_filename"] = existing["filename"]
            fields["backup_path"] = storage_paths.to_storage_relative(backup, project_id)
            fields["backup_expires_at"] = _later(int(policy["backup_retention_days"]))
        done = db.transition(request_id, (db.STATUS_APPROVING,), db.STATUS_APPROVED, **fields)
        if done is None:
            raise RequestError(409, "attachment_request_already_decided", "the request changed meanwhile")
        return done

    try:
        return run_in_document_guard(expected_doc_id, body)
    except GuardNotHeld as exc:
        raise RequestError(500, "attachment_request_approve_failed", "the document guard could not be taken") from exc


def _copy_plain(src: Path, dest: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    fd = os.open(str(dest), os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY)
    try:
        with open(src, "rb") as fh:
            while True:
                chunk = fh.read(ATTACH_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                digest.update(chunk)
                os.write(fd, chunk)
        os.fsync(fd)
    finally:
        os.close(fd)
    return digest.hexdigest(), size


def _restore_backup(backup: Path, existing_path: Path) -> None:
    """Put the previous bytes back at the existing name (copy next to it, then atomic replace)."""
    restore_tmp = existing_path.parent / f".aur-restore-{uuid.uuid4().hex[:12]}.part"
    _copy_plain(backup, restore_tmp)
    os.replace(restore_tmp, existing_path)


def _undo_files(row: dict, temp_path: Optional[Path], temp_identity, placed: dict) -> None:
    """Best effort, after a failed approval: never leaves a half-registered file behind."""
    from modules.flow_gate.storage.files import is_same_file_as

    try:
        if placed.get("replaced") and placed.get("backup") and placed.get("existing_path"):
            _restore_backup(placed["backup"], placed["existing_path"])
            placed["backup"].unlink()
        elif placed.get("backup") and Path(placed["backup"]).exists():
            Path(placed["backup"]).unlink()
    except Exception:
        logger.error("attachment overwrite restore failed for %s", row["request_id"], exc_info=True)
    try:
        final_path = placed.get("final_path")
        if final_path is not None:
            identity = placed.get("final_identity") or placed.get("placeholder")
            if identity is not None and is_same_file_as(final_path, identity):
                Path(final_path).unlink()
    except OSError:
        logger.warning("attachment approve cleanup failed", exc_info=True)
    try:
        if temp_path is not None and temp_identity is not None and is_same_file_as(temp_path, temp_identity):
            temp_path.unlink()
    except OSError:
        pass


# ── retention, recovery, orphan cleanup ─────────────────────────────────────

def expire_due(now: Optional[str] = None) -> int:
    now = now or now_iso()
    expired = 0
    for row in db.list_expired_pending(now):
        updated = db.transition(row["request_id"], (db.STATUS_PENDING,), db.STATUS_EXPIRED,
                                error_code="attachment_request_expired")
        if updated is None:
            continue
        expired += 1
        _remove_staging(row["project_id"], row["request_id"])
        _emit(updated)
    for row in db.list_expired_backups(now):
        try:
            backup = staging_dir(row["project_id"], row["request_id"]) / BACKUP_NAME
            if backup.exists() and not backup.is_symlink():
                backup.unlink()
            db.clear_backup(row["request_id"], row["backup_path"])
            _remove_staging(row["project_id"], row["request_id"])
        except Exception:
            logger.warning("attachment backup expiry failed for %s", row["request_id"], exc_info=True)
    return expired


def recover() -> int:
    """Startup: an approval a dead process left in ``approving`` never committed (the registry
    change and the status change are one transaction), so the request goes back to pending.
    An overwrite whose file was replaced before the crash gets its previous bytes back."""
    from .locator import resolve_attach_dir, resolve_registered_attachment
    from .registry import registry_get

    recovered = 0
    for row in db.list_by_status(db.STATUS_APPROVING):
        try:
            doc_id = row.get("final_doc_id")
            if doc_id:
                from modules.flow_gate.db import documents as db_documents

                doc = db_documents.get_by_id(doc_id)
                if doc is not None:
                    room = resolve_attach_dir(doc)
                    temp = room / (row.get("approve_temp_name") or f".aur-{row['request_id']}.part")
                    if temp.is_file() and not temp.is_symlink():
                        temp.unlink()
                    if row.get("decision") == "overwrite":
                        backup = staging_dir(row["project_id"], row["request_id"]) / BACKUP_NAME
                        existing = registry_get(doc_id, row["filename"])
                        if existing is not None and backup.is_file():
                            _reg, path = resolve_registered_attachment(doc, row["filename"], require_file=False)
                            current_sha = _hash_file(path)[0] if path.is_file() else None
                            backup_sha = _hash_file(backup)[0]
                            if current_sha != existing.get("content_sha256") and backup_sha == existing.get("content_sha256"):
                                _restore_backup(backup, path)
                        if backup.exists():
                            backup.unlink()
            reverted = db.transition(row["request_id"], (db.STATUS_APPROVING,), db.STATUS_PENDING,
                                     decided_by=None, decided_at=None, decision=None, final_doc_id=None,
                                     approve_phase=None, approve_temp_name=None,
                                     error_code="attachment_request_interrupted")
            if reverted:
                recovered += 1
                _emit(reverted)
        except Exception:
            logger.warning("attachment request recovery failed for %s", row.get("request_id"), exc_info=True)
    return recovered


def sweep_orphans(project_id: str, now_ts: Optional[float] = None) -> int:
    """Staged directories no open request owns (an intake cut off half-way, a row a Flow
    deletion cascaded away) are removed after a grace period; an approved overwrite's backup
    stays until its own retention ends."""
    root = staging_root(project_id)
    if not root.is_dir() or root.is_symlink():
        return 0
    now_ts = now_ts if now_ts is not None else time.time()
    keep = db.known_staging_ids(project_id)
    backups = db.known_backup_paths(project_id)
    removed = 0
    from modules.flow_gate.storage import paths as storage_paths

    for entry in list(root.iterdir()):
        if entry.is_symlink() or not entry.is_dir() or not _valid_id(entry.name) or entry.name in keep:
            continue
        backup_rel = storage_paths.to_storage_relative(entry / BACKUP_NAME, project_id)
        try:
            age = now_ts - entry.stat().st_mtime
        except OSError:
            continue
        if age < ORPHAN_GRACE_SEC:
            continue
        _remove_staging(project_id, entry.name, keep_backup=backup_rel in backups)
        removed += 1
    return removed


def sweep_all() -> dict:
    with _sweep_lock:
        expired = expire_due()
        orphans = 0
        try:
            from modules.flow_gate.db import projects as db_projects

            for project in db_projects.list_projects():
                try:
                    orphans += sweep_orphans(project["project_id"])
                except Exception:
                    logger.warning("attachment request orphan sweep failed for %s",
                                   project.get("project_id"), exc_info=True)
        except Exception:
            logger.warning("attachment request orphan sweep failed", exc_info=True)
        return {"expired": expired, "orphans": orphans}


_sweeper: Optional[threading.Thread] = None


def start_background(interval_sec: float = SWEEP_INTERVAL_SEC, *, force: bool = False) -> Optional[threading.Thread]:
    """Recovery once, then the retention sweep on a daemon thread (never blocks startup).
    Not started under the test runner (``TESTING=1``) unless ``force``: suites call the
    functions themselves on their own stores."""
    global _sweeper
    if os.environ.get("TESTING") == "1" and not force:
        return None
    if _sweeper is not None and _sweeper.is_alive():
        return _sweeper

    def loop() -> None:
        try:
            recover()
        except Exception:
            logger.warning("attachment request recovery failed", exc_info=True)
        while True:
            try:
                sweep_all()
            except Exception:
                logger.warning("attachment request sweep failed", exc_info=True)
            time.sleep(interval_sec)

    _sweeper = threading.Thread(target=loop, name="attachment-request-sweep", daemon=True)
    _sweeper.start()
    return _sweeper
