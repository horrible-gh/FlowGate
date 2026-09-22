"""WorkPlan pre-instruction attachment lifecycle.

Reserved attachments reuse the document attachment registry and jail, but only this module
may create or remove names in the reserved namespace.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anyio.to_thread

from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.documents.attachments.constants import (
    ATTACH_DEFAULT_CONTENT_TYPE,
    ATTACH_MAX_COUNT_PER_DOC,
    ATTACH_MAX_UPLOAD_BYTES,
    ATTACH_STREAM_CHUNK_BYTES,
)
from modules.flow_gate.documents.attachments.errors import AttachmentError
from modules.flow_gate.documents.attachments.locator import (
    resolve_attach_dir,
    resolve_registered_attachment,
    storage_relative,
)
from modules.flow_gate.documents.attachments.naming import (
    check_request_size,
    original_display_name,
    resolve_content_type,
    sanitize_attachment_name,
)
from modules.flow_gate.documents.attachments.registry import (
    registry_count,
    registry_delete,
    registry_get,
    registry_insert,
    registry_list,
)
from modules.flow_gate.documents.attachments.service import assert_mutable

RESERVED_PREFIX = "__wp_pre_instruction__"
_O_BINARY = getattr(os, "O_BINARY", 0)


def is_reserved_name(filename: object) -> bool:
    return isinstance(filename, str) and filename.startswith(RESERVED_PREFIX)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(ATTACH_STREAM_CHUNK_BYTES)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def validate_reference(doc_id: Optional[str], reference: dict) -> Optional[str]:
    """Return the stable WorkPlan validation code, or None when the reference is live."""

    if not doc_id or reference.get("doc_id") != doc_id:
        return "pre_instruction_attachment_doc_mismatch"
    filename = reference.get("filename")
    if not is_reserved_name(filename):
        return "pre_instruction_attachment_reserved_name_required"
    row = registry_get(doc_id, filename)
    if row is None:
        return "pre_instruction_attachment_registry_missing"
    if row.get("original_filename") != reference.get("original_filename"):
        return "pre_instruction_attachment_original_name_mismatch"
    if row.get("content_sha256") != reference.get("content_sha256"):
        return "pre_instruction_attachment_digest_mismatch"
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        return "pre_instruction_attachment_registry_missing"
    try:
        _, path = resolve_registered_attachment(doc, filename)
    except AttachmentError as exc:
        if exc.code == "STORAGE_PATH_OUTSIDE_ROOT":
            return "pre_instruction_attachment_outside_storage"
        return "pre_instruction_attachment_file_missing"
    try:
        actual = _digest(path)
    except OSError:
        return "pre_instruction_attachment_file_missing"
    if actual != reference.get("content_sha256"):
        return "pre_instruction_attachment_digest_mismatch"
    return None


def reference_status(doc_id: str, reference: Optional[dict]) -> dict:
    if reference is None:
        return {"available": False, "digest_matches": False, "code": None}
    code = validate_reference(doc_id, reference)
    return {
        "available": code is None,
        "digest_matches": code is None,
        "code": code,
    }


class PreInstructionAttachmentError(Exception):
    """A sequence item's pre-instruction attachment reference failed validation.

    0554 T0014 §5: the hop must stop before the AI is invoked rather than run without it —
    unlike a stored step note, a bad reference here is never silently dropped.
    """

    def __init__(self, code: str, source_doc_id: Optional[str] = None):
        self.code = code
        self.source_doc_id = source_doc_id
        super().__init__(code)


def resolve_pre_instruction(item: Optional[dict]) -> Optional[dict]:
    """Return one sequence item's pre-instruction ``{"text", "attachment"}``, or None.

    None means the row carries neither a text nor an attachment reference — the ordinary
    case for every step that is not the exact worker-executed row a WorkPlan projected
    pre-instruction onto.  In auto-approved instruction/result pairs, that execution
    snapshot lives on the paired result row while the instruction step remains canonical.

    Raises :class:`PreInstructionAttachmentError` when an attachment reference is stored
    but does not validate (missing file, wrong document, digest mismatch, stale reference,
    reserved-namespace violation, ...) — see T0014 §5's fail-closed list.
    """
    if not item:
        return None
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    text = (item.get("pre_instruction_text") or "").strip() or None
    raw_json = item.get("pre_instruction_attachment_json")
    if db_wfseq.pre_instruction_attachment_json_malformed(raw_json):
        # 0554 T0014 §5 (review rej_01M334Z5Y72GK6BW finding 1): a non-empty column that
        # cannot decode to a reference dict is a corrupted reference, not "no attachment".
        # decode_pre_instruction_attachment alone cannot tell the two apart (both return
        # None), which used to let this fall through to the text-only/no-instruction path
        # below and start the AI without ever surfacing the corruption — the exact silent
        # drop §5's fail-closed list forbids.
        raise PreInstructionAttachmentError(
            "pre_instruction_attachment_decode_failed", item.get("source_doc_id")
        )
    raw_attachment = db_wfseq.decode_pre_instruction_attachment(raw_json)
    if not text and raw_attachment is None:
        return None
    attachment: Optional[dict] = None
    if raw_attachment is not None:
        source_doc_id = item.get("source_doc_id")
        code = validate_reference(source_doc_id, raw_attachment)
        if code is not None:
            raise PreInstructionAttachmentError(code, source_doc_id)
        attachment = raw_attachment
    return {"text": text, "attachment": attachment}


def _stored_reference(doc_id: str, row: dict) -> dict:
    return {
        "doc_id": doc_id,
        "filename": row["filename"],
        "original_filename": row["original_filename"],
        "content_sha256": row["content_sha256"],
    }


def _prepare_upload(doc_id: str, actor: Optional[dict]) -> tuple[dict, Path]:
    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise AttachmentError(404, "DOCUMENT_NOT_FOUND", "Document was not found.")
    if str(doc.get("type_code") or "").upper() != "WP":
        raise AttachmentError(
            422, "WORK_PLAN_REQUIRED", "Pre-instruction attachments require a WorkPlan."
        )
    assert_mutable(doc, actor, "work plan pre-instruction attachment upload")
    if registry_count(doc_id) + 1 > ATTACH_MAX_COUNT_PER_DOC:
        raise AttachmentError(
            409, "ATTACHMENT_EXISTS",
            "This document already holds the maximum number of attachments.",
            reason="per_document_limit", limit=ATTACH_MAX_COUNT_PER_DOC,
        )
    room = resolve_attach_dir(doc)
    room.mkdir(parents=True, exist_ok=True)
    return doc, room


async def upload_pre_instruction_attachment(
    doc_id: str,
    step_key: str,
    part,
    actor: Optional[dict],
    content_length: Optional[str] = None,
) -> dict:
    """Store exactly one server-named reserved attachment and its registry row."""

    check_request_size(content_length)
    doc, room = await anyio.to_thread.run_sync(_prepare_upload, doc_id, actor)
    raw_name = getattr(part, "filename", None)
    safe_name = sanitize_attachment_name(raw_name)
    extension = Path(safe_name).suffix
    safe_step = step_key.replace("#", "-")
    filename = f"{RESERVED_PREFIX}{safe_step}__{uuid.uuid4().hex}{extension}"
    destination = room / filename
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY
    fd = await anyio.to_thread.run_sync(os.open, str(destination), flags, 0o600)
    digest = hashlib.sha256()
    received = 0
    try:
        try:
            while True:
                chunk = await part.read(ATTACH_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > ATTACH_MAX_UPLOAD_BYTES:
                    raise AttachmentError(
                        413, "ATTACHMENT_TOO_LARGE",
                        "Attachment exceeds the upload size limit.",
                        filename=safe_name, size=received,
                        limit_bytes=ATTACH_MAX_UPLOAD_BYTES,
                    )
                digest.update(chunk)
                await anyio.to_thread.run_sync(os.write, fd, chunk)
            await anyio.to_thread.run_sync(os.fsync, fd)
        finally:
            os.close(fd)

        row = {
            "doc_id": doc_id,
            "original_filename": original_display_name(raw_name),
            "filename": filename,
            "file_path": storage_relative(destination, doc.get("project_id")),
            "size": received,
            "content_type": resolve_content_type(safe_name) or ATTACH_DEFAULT_CONTENT_TYPE,
            "content_sha256": digest.hexdigest(),
            "uploaded_by": (actor or {}).get("user_id") or (actor or {}).get("id"),
            "uploaded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        await anyio.to_thread.run_sync(_insert_row, row)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            destination.unlink()
        except OSError:
            pass
        raise

    return {
        "reference": _stored_reference(doc_id, row),
        "size": received,
        "content_type": row["content_type"],
    }


def _insert_row(row: dict) -> None:
    registry_insert(
        doc_id=row["doc_id"],
        original_filename=row["original_filename"],
        filename=row["filename"],
        file_path=row["file_path"],
        size=row["size"],
        content_type=row["content_type"],
        content_sha256=row["content_sha256"],
        uploaded_by=row["uploaded_by"],
        uploaded_at=row["uploaded_at"],
    )


def referenced_reserved_names(body: Optional[dict]) -> set[str]:
    names: set[str] = set()
    for step in (body or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        reference = step.get("pre_instruction_attachment")
        if isinstance(reference, dict) and is_reserved_name(reference.get("filename")):
            names.add(reference["filename"])
    return names


def cleanup_unreferenced(
    doc: dict,
    body: Optional[dict],
    *,
    strict: bool = False,
) -> list[dict]:
    """Delete only unreferenced reserved registry rows inside this document's jail."""

    doc_id = doc.get("doc_id") or ""
    keep = referenced_reserved_names(body)
    targets = [
        row for row in registry_list(doc_id)
        if is_reserved_name(row.get("filename")) and row.get("filename") not in keep
    ]
    resolved: list[tuple[dict, Path]] = []
    warnings: list[dict] = []
    for row in targets:
        try:
            resolved.append(resolve_registered_attachment(
                doc, row["filename"], require_file=False,
            ))
        except AttachmentError as exc:
            warning = {
                "code": "wp_pre_instruction_cleanup_failed",
                "filename": row.get("filename"),
                "reason": exc.code,
            }
            if strict:
                raise
            warnings.append(warning)

    for row, path in resolved:
        try:
            path.unlink(missing_ok=True)
            registry_delete(doc_id, row["filename"])
        except OSError as exc:
            warning = {
                "code": "wp_pre_instruction_cleanup_failed",
                "filename": row.get("filename"),
                "reason": str(exc),
            }
            if strict:
                raise AttachmentError(
                    500, "ATTACHMENT_DELETE_FAILED",
                    "Could not delete a reserved WorkPlan attachment.",
                    filename=row.get("filename"),
                ) from exc
            warnings.append(warning)
        except Exception as exc:
            warning = {
                "code": "wp_pre_instruction_cleanup_failed",
                "filename": row.get("filename"),
                "reason": str(exc),
            }
            if strict:
                raise AttachmentError(
                    500, "ATTACHMENT_METADATA_FAILED",
                    "Could not remove reserved WorkPlan attachment metadata.",
                    filename=row.get("filename"),
                ) from exc
            warnings.append(warning)
    return warnings
