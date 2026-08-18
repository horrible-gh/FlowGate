"""Upload, list, download, delete, read and copy — the six attachment paths.

flowgate.default.0060 L0012 §2-5 (A1~A9), §2-6~§2-10; P0011 §2~§7 for the wire shapes.

Every blocking call — file read/write, mkdir, unlink, hashing, git worktree lookup — is
pushed through ``anyio.to_thread``. L0012 §5 and ``file_transfer_routes.py:118-121`` give the
same reason: a 100 MiB request handled on the event loop freezes every other request,
SSE heartbeats included, for the whole transfer.
"""
from __future__ import annotations

import base64
import codecs
import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import anyio.to_thread
from fastapi import HTTPException

from .constants import (
    ATTACH_COPY_CHUNK_BYTES,
    ATTACH_DEFAULT_CONTENT_TYPE,
    ATTACH_DOWNLOAD_FORCED_OCTET_TYPES,
    ATTACH_MAX_COUNT_PER_DOC,
    ATTACH_MAX_FILES_PER_REQUEST,
    ATTACH_MAX_PATH_SEGMENT_BYTES,
    ATTACH_MAX_REQUEST_BYTES,
    ATTACH_MAX_TARGET_PATH_CHARS,
    ATTACH_MAX_UPLOAD_BYTES,
    ATTACH_READ_MAX_BYTES,
    ATTACH_STREAM_CHUNK_BYTES,
    ATTACH_TEXT_SNIFF_BYTES,
    WINDOWS_RESERVED_NAMES,
)
from .errors import AttachmentError, unexpected
from .locator import (
    project_branch,
    resolve_attach_dir,
    resolve_registered_attachment,
    storage_relative,
)
from .naming import (
    check_request_size,
    is_executable_extension,
    original_display_name,
    resolve_content_type,
    resolve_unique_name,
    rfc5987_disposition,
    sanitize_attachment_name,
)
from .registry import (
    registry_count,
    registry_delete,
    registry_has,
    registry_insert,
    registry_list,
)

_O_BINARY = getattr(os, "O_BINARY", 0)


def _now_rfc3339() -> str:
    """P0011 §1-2 — RFC 3339 UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── shared preamble ─────────────────────────────────────────────────────────────

def load_document(doc_id: str) -> dict:
    """A1 — 404 DOCUMENT_NOT_FOUND, the second rung of the L0012 §4-1 ladder."""
    from modules.flow_gate.documents import document_service

    doc = document_service.get_document(doc_id)
    if doc is None:
        raise AttachmentError(
            404, "DOCUMENT_NOT_FOUND", "Document was not found.", doc_id=doc_id
        )
    return doc


def assert_mutable(doc: dict, actor: Optional[dict], operation: str) -> None:
    """A3 / X2 / C3 — the change-class guard, reusing the existing two.

    ``_reject_if_group_disposed`` and ``assert_group_mutation_allowed`` are the guards the
    current upload already runs (NR0015 §6-1); this only re-labels their failures into the
    P0011 ``DOCUMENT_NOT_MUTABLE`` envelope so every attachment error has one shape.
    Imported lazily — ``documents.py`` imports this module, so a module-level import here
    would close the cycle.
    """
    from fastapi import HTTPException

    from modules.flow_gate.documents.routers import documents as _documents
    from modules.flow_gate.services.mutation_policy import (
        MutationPolicyError,
        assert_group_mutation_allowed,
        human_principal,
    )

    not_mutable = lambda reason: AttachmentError(  # noqa: E731
        409,
        "DOCUMENT_NOT_MUTABLE",
        "Attachments cannot be changed in the current group state.",
        doc_id=doc.get("doc_id"),
        reason=reason,
    )

    try:
        _documents._reject_if_group_disposed(doc)
    except HTTPException:
        raise not_mutable("group_disposed")

    try:
        assert_group_mutation_allowed(
            doc.get("group_id"), human_principal(actor), operation
        )
    except MutationPolicyError:
        raise not_mutable("group_ai_running")


def _attachment_object(row: dict) -> dict:
    """P0011 §1-3 — the one attachment object every JSON API returns."""
    return {
        "doc_id": row.get("doc_id"),
        "original_filename": row.get("original_filename"),
        "filename": row.get("filename"),
        "size": int(row.get("size") or 0),
        "content_type": row.get("content_type") or ATTACH_DEFAULT_CONTENT_TYPE,
        "content_sha256": row.get("content_sha256"),
        "path": row.get("file_path"),
        "path_base": "storage",
        "uploaded_by": row.get("uploaded_by"),
        "uploaded_at": row.get("uploaded_at"),
    }


# ── §2-5 upload ─────────────────────────────────────────────────────────────────

def _prepare_upload(doc_id: str, part_count: int, actor: Optional[dict]) -> tuple[dict, Path]:
    """A1~A6 — everything before the first byte is written."""
    doc = load_document(doc_id)
    assert_mutable(doc, actor, "attachment upload")

    if part_count == 0:
        raise AttachmentError(
            422, "INVALID_REQUEST", "At least one file part is required.", field="file"
        )
    if part_count > ATTACH_MAX_FILES_PER_REQUEST:
        raise AttachmentError(
            422,
            "INVALID_REQUEST",
            "Too many file parts in one request.",
            field="file",
            limit=ATTACH_MAX_FILES_PER_REQUEST,
        )
    if registry_count(doc_id) + part_count > ATTACH_MAX_COUNT_PER_DOC:
        raise AttachmentError(
            409,
            "ATTACHMENT_EXISTS",
            "This document already holds the maximum number of attachments.",
            doc_id=doc_id,
            reason="per_document_limit",
            limit=ATTACH_MAX_COUNT_PER_DOC,
        )

    room = resolve_attach_dir(doc)
    try:
        room.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise unexpected(
            exc,
            operation="upload:mkdir",
            code="ATTACHMENT_STORE_FAILED",
            message="Could not create the attachment folder.",
            reason="storage",
            doc_id=doc_id,
        )
    return doc, room


def _rollback(done: list[dict]) -> None:
    """A9 — best effort. Never changes the error the caller is already returning.

    Files left behind are not in the registry, so they appear in no list, no download and
    no read, and §2-12 W3 keeps the numbering scan from calling them orphans.
    """
    for item in done:
        try:
            Path(item["abs_path"]).unlink()
        except OSError:
            pass


async def upload_attachments(
    doc_id: str,
    parts: list,
    actor: Optional[dict],
    content_length: Optional[str] = None,
) -> dict:
    """A1~A9. A ``201`` means every file is on disk AND every row is committed.

    A7 finishes for all parts before A8 starts. Registering each file as it lands would mean
    a part that fails at #3 has to unwind two kinds of state (files and rows); writing all
    the files first and then registering in one transaction keeps the undo to a single
    "delete the rows, delete the files" pass — and with the rows in one transaction, a
    failed commit leaves no rows at all, so the undo is really just the files
    (DB0013 §4, transaction boundary).
    """
    done: list[dict] = []
    reserved: set = set()
    request_total = 0

    # 0060 TR0017 rev2 — A1~A6 run INSIDE the try as well. They used to sit outside it, so
    # anything unexpected in the preamble (registry query, path resolution, mkdir) was left
    # for FastAPI to answer as a bare `500 Internal Server Error`: no P0011 envelope, no
    # error code the card can read, and — because this package logged nothing — no trace in
    # the server log either. That is exactly the symptom this document was rejected for.
    try:
        check_request_size(content_length)  # U1
        doc, room = await anyio.to_thread.run_sync(
            _prepare_upload, doc_id, len(parts), actor
        )
        project_id = doc.get("project_id")
        actor_id = (actor or {}).get("user_id") or (actor or {}).get("id")

        for part in parts:  # request order preserved
            raw_name = getattr(part, "filename", None)
            safe = sanitize_attachment_name(raw_name)
            # E1~E2. No extension is refused here — see constants §1-3 and TR0017 rev3.
            content_type = resolve_content_type(safe)

            final_name, fd = await anyio.to_thread.run_sync(
                resolve_unique_name, room, doc_id, safe, reserved, registry_has
            )
            dest = room / final_name
            # Recorded BEFORE the first byte is written. D5 already created the file, so from
            # here on it exists on disk and the rollback has to know about it — A9 says
            # "unlink every done item, the unfinished open part included". Filling the entry
            # in only after the stream completed is what left a 20 MiB carcass behind when
            # the part was cut at U2.
            item: dict = {"abs_path": str(dest)}
            done.append(item)
            digest = hashlib.sha256()
            received = 0
            try:
                while True:
                    chunk = await part.read(ATTACH_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > ATTACH_MAX_UPLOAD_BYTES:  # U2 — cut the moment it passes
                        raise AttachmentError(
                            413,
                            "ATTACHMENT_TOO_LARGE",
                            "Attachment exceeds the upload size limit.",
                            filename=safe,
                            size=received,
                            limit_bytes=ATTACH_MAX_UPLOAD_BYTES,
                        )
                    if request_total + received > ATTACH_MAX_REQUEST_BYTES:  # U3
                        raise AttachmentError(
                            413,
                            "ATTACHMENT_TOO_LARGE",
                            "Attachment request exceeds the total size limit.",
                            filename=safe,
                            limit_bytes=ATTACH_MAX_REQUEST_BYTES,
                        )
                    digest.update(chunk)
                    await anyio.to_thread.run_sync(os.write, fd, chunk)
                await anyio.to_thread.run_sync(os.fsync, fd)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass

            request_total += received
            item.update({
                "original_filename": original_display_name(raw_name),
                "filename": final_name,
                "file_path": storage_relative(dest, project_id),
                "size": received,
                "content_type": content_type,
                "content_sha256": digest.hexdigest(),
            })

        rows = await anyio.to_thread.run_sync(
            _commit_registry, doc_id, done, actor_id
        )
    except (AttachmentError, HTTPException):
        # HTTPException is the group-lock answer (423 GROUP_AI_RUN_LOCKED) the mutability
        # guard raises. Folding it into the 500 branch would hide "an AI run owns this
        # group" behind a crash message.
        await anyio.to_thread.run_sync(_rollback, done)
        raise
    except Exception as exc:
        await anyio.to_thread.run_sync(_rollback, done)
        raise unexpected(
            exc,
            operation="upload",
            code="ATTACHMENT_STORE_FAILED",
            message="Could not store the attachment.",
            reason="storage",
            doc_id=doc_id,
        )

    return {"doc_id": doc_id, "attachments": rows, "count": len(rows)}


def _commit_registry(doc_id: str, done: list[dict], actor_id: Optional[str]) -> list[dict]:
    """A8 — one transaction for the whole request."""
    from modules.flow_gate.db.connection import get_store

    store = get_store()
    uploaded_at = _now_rfc3339()
    try:
        with store.transaction():
            for item in done:
                registry_insert(
                    doc_id=doc_id,
                    original_filename=item["original_filename"],
                    filename=item["filename"],
                    file_path=item["file_path"],
                    size=item["size"],
                    content_type=item["content_type"],
                    content_sha256=item["content_sha256"],
                    uploaded_by=actor_id,
                    uploaded_at=uploaded_at,
                    store=store,
                )
    except Exception as exc:
        raise unexpected(
            exc,
            operation="upload:registry",
            code="ATTACHMENT_METADATA_FAILED",
            message="Could not record the attachment metadata.",
            reason="registry",
            doc_id=doc_id,
        )

    return [
        _attachment_object({
            "doc_id": doc_id,
            "original_filename": item["original_filename"],
            "filename": item["filename"],
            "file_path": item["file_path"],
            "size": item["size"],
            "content_type": item["content_type"],
            "content_sha256": item["content_sha256"],
            "uploaded_by": actor_id,
            "uploaded_at": uploaded_at,
        })
        for item in done
    ]


# ── §2-6 list (P0011 §3) ────────────────────────────────────────────────────────

def list_attachments(doc_id: str) -> dict:
    """A document with no attachments is a 200 and an empty array, never a 404."""
    load_document(doc_id)
    rows = registry_list(doc_id)
    return {
        "doc_id": doc_id,
        "attachments": [_attachment_object(r) for r in rows],
        "count": len(rows),
    }


# ── §2-7 download ───────────────────────────────────────────────────────────────

def resolve_download(doc_id: str, name: str) -> tuple[Path, dict]:
    """Returns (file path, response headers).

    Not a change-class path, so a disposed group or a running AI run does NOT block it —
    D0010 §6-1 keeps list and download alive while the document is read-only.
    """
    doc = load_document(doc_id)
    row, path = resolve_registered_attachment(doc, name)

    filename = row.get("filename") or name
    media = row.get("content_type") or ATTACH_DEFAULT_CONTENT_TYPE
    if media in ATTACH_DOWNLOAD_FORCED_OCTET_TYPES or is_executable_extension(filename):
        # Never hand back an attachment in a shape the browser will render or run. The
        # extension arm carries the weight now that §1-3 admits executable kinds: a row
        # written before that change, or by the legacy migration, can still say
        # ``text/javascript``, and this is the last place to say otherwise.
        media = ATTACH_DEFAULT_CONTENT_TYPE

    headers = {
        "Content-Disposition": rfc5987_disposition(filename),
        "ETag": f'"sha256-{row.get("content_sha256")}"',
        "X-Content-Type-Options": "nosniff",
    }
    return path, {"media_type": media, "headers": headers}


# ── §2-8 delete ─────────────────────────────────────────────────────────────────

def delete_attachment(doc_id: str, name: str, actor: Optional[dict]) -> dict:
    """X1~X6 — the file first, the row second.

    Of the two half-done states this can leave, this order picks the recoverable one. If the
    unlink fails nothing has changed yet, so "it failed and the state is untouched" is an
    honest answer; the reverse order cannot say that. If the row removal fails the row
    survives without a file (``ghost_row``) — visible in the list, 404 on click, and cleaned
    up by repeating the very same delete (X3 finds the row, X4 sees no file and continues,
    X5 removes the row).
    """
    doc = load_document(doc_id)
    assert_mutable(doc, actor, "attachment delete")
    # X3 — every jail check, but a missing file is not an error here: X4 treats it as the
    # target state, which is what lets the next identical delete clear a ghost_row.
    row, path = resolve_registered_attachment(doc, name, require_file=False)

    try:
        path.unlink()
    except FileNotFoundError:
        pass                                   # already the target state → keep going
    except OSError as exc:
        raise unexpected(
            exc,
            operation="delete:unlink",
            code="ATTACHMENT_DELETE_FAILED",
            message="Could not delete the attachment file.",
            reason=None,
            doc_id=doc_id,
            filename=row.get("filename"),
        )

    last_error: Optional[Exception] = None
    from .constants import ATTACH_DELETE_RETRY

    for _ in range(ATTACH_DELETE_RETRY + 1):
        try:
            registry_delete(doc_id, row.get("filename"))
            last_error = None
            break
        except Exception as exc:               # noqa: BLE001 — retried, then surfaced
            last_error = exc
    if last_error is not None:
        raise unexpected(
            last_error,
            operation="delete:registry",
            code="ATTACHMENT_METADATA_FAILED",
            message="Could not remove the attachment metadata.",
            reason=None,
            doc_id=doc_id,
            filename=row.get("filename"),
        )

    return {
        "doc_id": doc_id,
        "filename": row.get("filename"),
        "path": row.get("file_path"),
        "path_base": "storage",
        "file_deleted": True,
        "metadata_deleted": True,
        "deleted_at": _now_rfc3339(),
    }


# ── §2-9 read ───────────────────────────────────────────────────────────────────

_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _detect_bom(head: bytes) -> Optional[str]:
    for bom, name in _BOMS:
        if head.startswith(bom):
            return name
    return None


def read_attachment(doc_id: str, name: str, mode: str = "auto", encoding: str = "utf-8") -> dict:
    """R1~R8. The content is never truncated — over the ceiling the whole read is refused.

    ``truncated`` exists in the contract and is always ``false``: handing back a cut-down
    body as a success makes the caller treat a fragment as the whole file.
    """
    doc = load_document(doc_id)
    row, path = resolve_registered_attachment(doc, name)

    # R2
    if mode not in ("auto", "text", "base64"):
        raise AttachmentError(
            422, "INVALID_REQUEST", "mode must be one of auto, text, base64.",
            field="mode", value=mode,
        )
    try:
        codec = codecs.lookup(encoding or "utf-8")
    except LookupError:
        raise AttachmentError(
            422, "INVALID_REQUEST", "Unknown text encoding.", field="encoding", value=encoding
        )

    # R3 — gone. It used to answer 415 UNSUPPORTED_READ_TYPE for the §1-3 kinds, and its
    #      whole justification was "upload already refused these, so this only catches a
    #      legacy-migrated one". Upload accepts them now (TR0017 rev3), and refusing to read
    #      back a file the same screen just accepted is the same wall one door further in.
    #      ``mode=auto`` already answers correctly for these: R6 sniffs NUL bytes and hands
    #      an executable back base64-encoded, exactly like any other binary.
    filename = row.get("filename") or name

    # R4 — before R5, so an oversized file is never loaded. '>' so the exact limit passes.
    size = path.stat().st_size
    if size > ATTACH_READ_MAX_BYTES:
        raise AttachmentError(
            413, "READ_TOO_LARGE", "Attachment exceeds the read size limit.",
            filename=filename, size=size, limit_bytes=ATTACH_READ_MAX_BYTES,
        )

    data = path.read_bytes()                    # R5 — 1 MiB or less, guaranteed
    meta = _attachment_object(row)

    def _binary() -> dict:                      # R6
        return {
            "attachment": meta,
            "kind": "binary",
            "encoding": None,
            "content_encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
            "truncated": False,
        }

    def _text(decoded: str, enc_name: str) -> dict:
        return {
            "attachment": meta,
            "kind": "text",
            "encoding": enc_name,
            "content_encoding": "identity",
            "content": decoded.lstrip("\ufeff"),
            "truncated": False,
        }

    if mode == "base64":
        return _binary()

    if mode == "text":                          # R7
        try:
            return _text(data.decode(codec.name, errors="strict"), codec.name)
        except (UnicodeDecodeError, LookupError):
            raise AttachmentError(
                415, "INVALID_TEXT_ENCODING",
                "Attachment cannot be decoded with the requested encoding.",
                filename=filename, encoding=encoding,
            )

    # R8 — auto. Never answers 415: failing to decode IS the binary answer.
    head = data[:ATTACH_TEXT_SNIFF_BYTES]
    detected = _detect_bom(head)
    if detected:
        try:
            return _text(data.decode(detected, errors="strict"), detected)
        except UnicodeDecodeError:
            pass
    if b"\x00" in head:
        return _binary()
    try:
        return _text(data.decode(codec.name, errors="strict"), codec.name)
    except (UnicodeDecodeError, LookupError):
        return _binary()


# ── §2-10 copy ──────────────────────────────────────────────────────────────────

def _validate_target_path(target_path: Optional[str]) -> list[str]:
    """C2 — pure string checks. No disk, no DB; the cheapest rung runs first."""
    if target_path is None or not str(target_path).strip():
        raise AttachmentError(
            422, "INVALID_REQUEST", "target_path is required.", field="target_path"
        )
    raw = str(target_path)
    bad = lambda reason: AttachmentError(  # noqa: E731
        400, "INVALID_PATH", "Target path must be a source-root-relative POSIX path.",
        field="target_path", value=raw, reason=reason,
    )
    if len(raw) > ATTACH_MAX_TARGET_PATH_CHARS:
        raise bad("too_long")
    t = raw.replace("\\", "/")
    if t.startswith("//"):
        raise bad("unc")
    if t.startswith("/"):
        raise bad("absolute")
    if len(t) >= 2 and t[1] == ":":
        raise bad("drive")
    if any(ch in t for ch in ("\x00",)) or any(ord(ch) < 0x20 for ch in t):
        raise bad("control_chars")
    if t.endswith("/"):
        raise bad("no_filename")

    segments = t.split("/")
    for seg in segments:
        if seg in ("", ".", ".."):
            raise bad("empty_or_dot_segment")
        if len(seg.encode("utf-8")) > ATTACH_MAX_PATH_SEGMENT_BYTES:
            raise bad("segment_too_long")
        if seg != seg.rstrip(" ."):
            raise bad("trailing_dot_or_space")   # Windows renames it behind our back
        if Path(seg).stem.upper() in WINDOWS_RESERVED_NAMES:
            raise bad("windows_reserved_name")
    return segments


def resolve_copy_root(project_id: str, group_id: Optional[str]) -> Path:
    """C5 — refuse, never substitute.

    ``storage_paths.resolve_project_src_root`` is deliberately NOT used: it drops to the base
    checkout whenever a worktree cannot be resolved (``paths.py:192-225``). A copy the caller
    aimed at a group would then dirty the base checkout and jam every other group's finalize
    — which is exactly why ``file_transfer_routes.py:37-43`` went fail-closed. Same judgement
    here.
    """
    from modules.flow_gate.db import groups as _groups
    from modules.flow_gate.services import git_service

    if group_id:
        group = _groups.get_by_id(group_id)
        if group is None or group.get("project_id") != project_id:
            raise AttachmentError(
                404, "TARGET_GROUP_NOT_FOUND", "Group not found in this project.",
                group_id=group_id,
            )
        root, reason = git_service.effective_src_root_ex(project_id, group_id)
        if root is None:
            raise AttachmentError(
                409,
                "WORKTREE_UNAVAILABLE",
                "Group worktree is unavailable; the base checkout was not used.",
                group_id=group_id,
                reason=reason,
            )
        return root.resolve()

    from modules.flow_gate.db import projects as _projects

    project = _projects.get_by_id(project_id)
    project_name = ((project or {}).get("project_name") or "").strip()
    branch = project_branch(project_id)
    root = None
    if project_name:
        try:
            root = git_service.base_src_root(project_id, project_name, branch)
        except Exception:
            root = None
    if root is None or not Path(root).exists():
        raise AttachmentError(
            409,
            "WORKTREE_UNAVAILABLE",
            "Group worktree is unavailable; the base checkout was not used.",
            reason="base_checkout_unresolved",
        )
    return Path(root).resolve()


def _seal_under_root(root: Path, segments: list[str]) -> Path:
    """C6 — three passes, because each one misses something the next catches.

    ① string containment. ``..`` was already refused in C2, but a link leaves no trace in
       the string, so this alone is not enough.
    ② per-component symlink check (the ``tree_routes._seal_under_root`` pattern).
    ③ ``realpath`` of the deepest existing ancestor. Windows directory junctions do not
       always report ``is_symlink()``; this is what catches those.

    A link answers 403 SOURCE_PATH_OUTSIDE_ROOT rather than the 400
    ``tree_routes.py:163`` uses, because P0011 §8 defines this code as "after normalization
    and symlink checks the target is outside the root" — and a link can point outside
    without our being able to prove cheaply that it does not.
    """
    outside = AttachmentError(
        403, "SOURCE_PATH_OUTSIDE_ROOT", "Target resolves outside the selected source root.",
        target_path="/".join(segments), path_base="source",
    )
    root_abs = root.resolve(strict=False)
    dest = root_abs.joinpath(*segments)
    try:
        dest.relative_to(root_abs)                                   # ①
    except ValueError:
        raise outside
    current = root_abs
    for seg in segments:                                            # ②
        current = current / seg
        if current.exists() and current.is_symlink():
            raise outside
    ancestor = dest.parent
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    try:
        Path(os.path.realpath(ancestor)).relative_to(root_abs)      # ③
    except ValueError:
        raise outside
    return dest


def copy_to_source(
    doc_id: str,
    name: str,
    target_path: Optional[str],
    group_id: Optional[str],
    actor: Optional[dict],
) -> dict:
    """C1~C10. The only path that writes outside the storage tree, hence the longest guard.

    Copy does NOT apply the upload extension deny-list to its target (C4). The destination is
    a source tree, where ``.ps1``/``.sh``/``.js`` are ordinary files; blocking them would make
    "put the script I was sent into the source" impossible. The risk the deny-list addresses
    is a receiver clicking a download, and a copy result never goes to a browser.
    """
    doc = load_document(doc_id)
    segments = _validate_target_path(target_path)                    # C2
    assert_mutable(doc, actor, "attachment copy")                    # C3
    row, src_path = resolve_registered_attachment(doc, name)         # C4

    project_id = doc.get("project_id")
    root = resolve_copy_root(project_id, group_id)                   # C5
    dest = _seal_under_root(root, segments)                          # C6

    created_dirs: list[Path] = []
    parent = dest.parent
    if parent.exists() and not parent.is_dir():                      # C7
        raise AttachmentError(
            409, "TARGET_EXISTS", "Copy target already exists.",
            target_path="/".join(segments), path_base="source", group_id=group_id,
            reason="parent_is_file",
        )
    if not parent.exists():
        missing = []
        probe = parent
        while not probe.exists() and probe != probe.parent:
            missing.append(probe)
            probe = probe.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise unexpected(
                exc,
                operation="copy:mkdir",
                code="ATTACHMENT_COPY_FAILED",
                message="Could not create the target folder.",
                reason=None,
                target_path="/".join(segments), path_base="source",
            )
        created_dirs = missing

    _assert_target_absent(parent, segments[-1], segments, group_id)  # C8

    # C9 — existence check and creation must be one atomic act. C8 stays because it is what
    # tells apart `parent_is_file` and a case-only clash; O_EXCL closes the TOCTOU gap it
    # cannot. A temp file plus rename is wrong here: rename OVERWRITES, and refusing is the
    # contract.
    digest = hashlib.sha256()
    try:
        fd = os.open(str(dest), os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_BINARY)
    except FileExistsError:
        _cleanup_dirs(created_dirs)
        raise AttachmentError(
            409, "TARGET_EXISTS", "Copy target already exists.",
            target_path="/".join(segments), path_base="source", group_id=group_id,
            reason="race",
        )
    except OSError as exc:
        _cleanup_dirs(created_dirs)
        raise unexpected(
            exc,
            operation="copy:create",
            code="ATTACHMENT_COPY_FAILED",
            message="Could not create the copy target.",
            reason=None,
            target_path="/".join(segments), path_base="source",
        )

    copied = 0
    try:
        with open(src_path, "rb") as src:
            while True:
                chunk = src.read(ATTACH_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                copied += len(chunk)
                os.write(fd, chunk)
        os.fsync(fd)
    except OSError as exc:
        os.close(fd)
        _unlink_quiet(dest)
        _cleanup_dirs(created_dirs)
        raise unexpected(
            exc,
            operation="copy:write",
            code="ATTACHMENT_COPY_FAILED",
            message="Could not copy the attachment.",
            reason=None,
            target_path="/".join(segments), path_base="source",
        )
    else:
        os.close(fd)

    expected = row.get("content_sha256")
    if expected and digest.hexdigest() != expected:
        _unlink_quiet(dest)
        _cleanup_dirs(created_dirs)
        raise unexpected(
            None,
            operation="copy:verify",
            code="ATTACHMENT_COPY_FAILED",
            message="The attachment changed while it was copied.",
            reason="source_changed",
            target_path="/".join(segments), path_base="source",
        )

    return {
        "doc_id": doc_id,
        "filename": row.get("filename"),
        "source": {"path": row.get("file_path"), "path_base": "storage"},
        "destination": {
            "project_id": project_id,
            "group_id": group_id,
            "target_path": "/".join(segments),
            "path_base": "source",
        },
        "size": copied,
        "content_sha256": digest.hexdigest(),
        "copied_at": _now_rfc3339(),
    }


def _assert_target_absent(
    parent: Path, filename: str, segments: list[str], group_id: Optional[str]
) -> None:
    """C8 — refuse. Never overwrite, never auto-rename.

    Upload renames because the name came from a file and the result is visible in the list
    the user is looking at. A copy path came from the caller, and later edits, tests and
    commits key off it — quietly changing that key hands the caller a file they did not ask
    for. And the case-insensitive sweep runs on every platform, so the same request cannot
    overwrite on Windows and coexist on Linux.
    """
    target = parent / filename
    conflict = lambda reason, **extra: AttachmentError(  # noqa: E731
        409, "TARGET_EXISTS", "Copy target already exists.",
        target_path="/".join(segments), path_base="source", group_id=group_id,
        reason=reason, **extra,
    )
    # The directory listing comes FIRST. On a case-insensitive filesystem `lstat` succeeds
    # for `Notes.txt` when only `notes.txt` exists, so leading with it would report a plain
    # `exists` on Windows and `case_insensitive_match` on Linux for the very same request —
    # the platform split C8 exists to close. Reading the real entry name settles it the same
    # way everywhere.
    folded = filename.casefold()
    existing = None
    try:
        for entry in parent.iterdir():
            if entry.name.casefold() == folded:
                existing = entry.name
                break
    except OSError:
        existing = None
    if existing is not None:
        if existing != filename:
            raise conflict("case_insensitive_match", existing_name=existing)
        raise conflict("exists")

    # Belt and braces for anything the listing could not show.
    try:
        target.lstat()                       # lstat → a broken symlink also counts as present
    except FileNotFoundError:
        return
    except OSError:
        pass
    raise conflict("exists")


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _cleanup_dirs(created: list[Path]) -> None:
    """Remove the directories this call created, deepest first, only while empty."""
    for directory in created:
        try:
            directory.rmdir()
        except OSError:
            break


# ── async wrappers — the routes call these ──────────────────────────────────────

async def _in_thread(fn: Callable[..., Any], *args: Any) -> Any:
    return await anyio.to_thread.run_sync(fn, *args)


async def alist_attachments(doc_id: str) -> dict:
    return await _in_thread(list_attachments, doc_id)


async def aresolve_download(doc_id: str, name: str) -> tuple[Path, dict]:
    return await _in_thread(resolve_download, doc_id, name)


async def adelete_attachment(doc_id: str, name: str, actor: Optional[dict]) -> dict:
    return await _in_thread(delete_attachment, doc_id, name, actor)


async def aread_attachment(doc_id: str, name: str, mode: str, encoding: str) -> dict:
    return await _in_thread(read_attachment, doc_id, name, mode, encoding)


async def acopy_to_source(
    doc_id: str,
    name: str,
    target_path: Optional[str],
    group_id: Optional[str],
    actor: Optional[dict],
) -> dict:
    return await _in_thread(copy_to_source, doc_id, name, target_path, group_id, actor)
