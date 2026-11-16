"""Flow attachment namespace; legacy document paths and references stay intact.

All mutations share the existing Flow/document guard. Rows retain their provenance;
the UI uses opaque keys so a display-name collision never targets another file.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shutil
import uuid
from pathlib import Path
from tempfile import SpooledTemporaryFile
from zipfile import ZipFile, ZIP_DEFLATED

from modules.flow_gate.db import groups
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.graph_workflow.services.flow_guard import run_in_document_guard
from . import service, requests
from .constants import ATTACH_MAX_UPLOAD_BYTES, ATTACH_MAX_COUNT_PER_DOC
from .errors import AttachmentError
from .locator import resolve_attach_dir, resolve_registered_attachment, storage_relative
from .naming import sanitize_attachment_name, resolve_content_type, rfc5987_disposition
from .registry import registry_insert, registry_replace, registry_delete


def context(flow_id, user, write=False):
    group = groups.get_by_id(flow_id)
    if not group:
        raise AttachmentError(404, "FLOW_NOT_FOUND", "Flow not found.")
    requests.require_permission(user, "perm_document_update" if write else "perm_document_read", group["project_id"])
    docs = get_store()._fetch_all(
        "SELECT * FROM documents WHERE group_id = ? AND project_id = ? ORDER BY seq, doc_id",
        [flow_id, group["project_id"]])
    if not docs:
        raise AttachmentError(409, "FLOW_DOCUMENT_MISSING", "The Flow has no document yet.")
    owner = next((d for d in docs if d.get("type_code") in ("B", "R")), docs[0])
    if write:
        requests._assert_flow_open(flow_id)
        requests._assert_doc_mutable(owner)
    return owner


def key(row):
    return base64.urlsafe_b64encode((row["doc_id"] + "\n" + row["filename"]).encode()).decode().rstrip("=")


def rows(flow_id):
    data = get_store()._fetch_all(
        "SELECT a.*, d.type_code AS source_type, d.title AS source_title FROM attachments a "
        "JOIN documents d ON d.doc_id = a.doc_id JOIN groups g ON g.group_id = d.group_id "
        "WHERE d.group_id = ? AND d.project_id = g.project_id ORDER BY a.uploaded_at, a.doc_id, a.filename",
        [flow_id])
    data = [r for r in data if not r["filename"].startswith("__wp_pre_instruction__")]
    # Reserve real names first: a legacy duplicate must not steal an existing 'name (2)'.
    used = {r["filename"].casefold() for r in data}
    seen = set()
    for row in data:
        name = row["filename"]
        if name.casefold() in seen:
            name = unique_name(name, used)
        seen.add(row["filename"].casefold())
        used.add(name.casefold())
        row["display_name"] = name
        row["key"] = key(row)
    return data


def unique_name(name, used):
    stem, ext = os.path.splitext(name)
    candidate, n = name, 2
    while candidate.casefold() in used:
        candidate = f"{stem} ({n}){ext}"
        n += 1
    return candidate


def find(flow_id, attachment_key):
    row = next((r for r in rows(flow_id) if r["key"] == attachment_key), None)
    if row is None:
        raise AttachmentError(404, "ATTACHMENT_NOT_FOUND", "Attachment not found in this Flow.")
    return row


def pending_requests(flow_id, user):
    context(flow_id, user)
    data = get_store()._fetch_all(
        "SELECT * FROM attachment_upload_requests WHERE target_flow_id = ? AND status IN ('pending', 'approving') ORDER BY created_at",
        [flow_id])
    return {"requests": [requests.public(r) for r in data]}


def listing(flow_id, user):
    owner = context(flow_id, user)
    return {"flow_id": flow_id, "project_id": owner["project_id"], "attachments": [
        {**service._attachment_object(r), "key": r["key"], "display_name": r["display_name"],
         "source_type": r["source_type"], "source_title": r["source_title"]} for r in rows(flow_id)]}


def download(flow_id, attachment_key, user):
    context(flow_id, user)
    row = find(flow_id, attachment_key)
    path, meta = service.resolve_download(row["doc_id"], row["filename"])
    meta["headers"]["Content-Disposition"] = rfc5987_disposition(row["display_name"])
    return path, meta


def upload(flow_id, part, user, conflict="ask", expected_sha=None, attachment_key=None):
    owner = context(flow_id, user, True)
    if conflict not in ("ask", "keep_both", "overwrite"):
        raise AttachmentError(422, "INVALID_REQUEST", "Unknown conflict choice.")
    name = sanitize_attachment_name(part.filename)
    if name.startswith("__wp_pre_instruction__"):
        raise AttachmentError(400, "INVALID_FILENAME", "Reserved filename.")
    data = part.file.read(ATTACH_MAX_UPLOAD_BYTES + 1)
    if len(data) > ATTACH_MAX_UPLOAD_BYTES:
        raise AttachmentError(413, "ATTACHMENT_TOO_LARGE", "Attachment exceeds the upload limit.")
    placed = {}

    def commit():
        fresh = context(flow_id, user, True)
        current = rows(flow_id)
        same = next((r for r in current if r["display_name"].casefold() == name.casefold()), None)
        existing = find(flow_id, attachment_key) if attachment_key else same
        if conflict == "ask" and same:
            raise AttachmentError(409, "ATTACHMENT_NAME_CONFLICT", "A file with this name already exists.",
                                  key=same["key"], filename=same["display_name"], content_sha256=same["content_sha256"])
        if conflict == "overwrite":
            if not existing or not expected_sha or existing["content_sha256"] != expected_sha:
                raise AttachmentError(409, "ATTACHMENT_CHANGED", "The file changed. Check it again.")
            doc = service.load_document(existing["doc_id"])
            requests._assert_doc_mutable(doc)
            _, dest = resolve_registered_attachment(doc, existing["filename"])
            if hashlib.sha256(dest.read_bytes()).hexdigest() != expected_sha:
                raise AttachmentError(409, "ATTACHMENT_CHANGED", "The file changed. Check it again.")
            backup = dest.parent / ".history" / (uuid.uuid4().hex + "-" + dest.name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(dest, backup)
            placed.update(backup=backup, dest=dest)
            final_name = existing["filename"]
        else:
            if len(current) >= ATTACH_MAX_COUNT_PER_DOC:
                raise AttachmentError(409, "ATTACHMENT_EXISTS", "The Flow attachment limit was reached.")
            doc = fresh
            room = resolve_attach_dir(doc)
            room.mkdir(parents=True, exist_ok=True)
            used = {r["display_name"].casefold() for r in current} | {p.name.casefold() for p in room.iterdir()}
            final_name = unique_name(name, used)
            dest = room / final_name
            # Reserve before writing; never overwrite an unregistered file.
            fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            placed.update(dest=dest, created=True)
        temp = dest.parent / (".flow-upload-" + uuid.uuid4().hex)
        placed["temp"] = temp
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, dest)
        values = dict(doc_id=doc["doc_id"], filename=final_name, original_filename=part.filename,
                      size=len(data), content_type=resolve_content_type(name), content_sha256=hashlib.sha256(data).hexdigest(),
                      uploaded_by=user.get("user_id"), uploaded_at=service._now_rfc3339())
        if conflict == "overwrite":
            if registry_replace(**values, expected_sha256=expected_sha) != 1:
                raise AttachmentError(409, "ATTACHMENT_CHANGED", "The file changed. Check it again.")
        else:
            registry_insert(**values, file_path=storage_relative(dest, doc["project_id"]))
        return {"flow_id": flow_id, "filename": final_name}

    try:
        result = run_in_document_guard(owner["doc_id"], commit)
    except BaseException:
        if placed.get("backup"):
            os.replace(placed["backup"], placed["dest"])
        elif placed.get("created"):
            placed["dest"].unlink(missing_ok=True)
        if placed.get("temp"):
            placed["temp"].unlink(missing_ok=True)
        raise
    emit(owner)
    return result


def remove(flow_id, attachment_key, user, expected_sha):
    owner = context(flow_id, user, True)
    placed = {}
    def commit():
        context(flow_id, user, True)
        row = find(flow_id, attachment_key)
        if not expected_sha or row["content_sha256"] != expected_sha:
            raise AttachmentError(409, "ATTACHMENT_CHANGED", "The file changed. Check it again.")
        doc = service.load_document(row["doc_id"])
        _, path = resolve_registered_attachment(doc, row["filename"], require_file=False)
        if path.exists():
            backup = path.parent / ".history" / (uuid.uuid4().hex + "-" + path.name)
            backup.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, backup)
            placed.update(path=path, backup=backup)
        registry_delete(row["doc_id"], row["filename"])
        return {"flow_id": flow_id, "key": attachment_key, "deleted": True}
    try:
        result = run_in_document_guard(owner["doc_id"], commit)
    except BaseException:
        if placed:
            os.replace(placed["backup"], placed["path"])
        raise
    emit(owner)
    return result


def archive(flow_id, keys, user):
    context(flow_id, user)
    if len(keys) > ATTACH_MAX_COUNT_PER_DOC:
        raise AttachmentError(422, "INVALID_REQUEST", "Too many files.")
    output = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        with ZipFile(output, "w", ZIP_DEFLATED) as zip_file:
            for attachment_key in dict.fromkeys(keys):
                row = find(flow_id, attachment_key)
                path, _ = download(flow_id, attachment_key, user)
                zip_file.write(path, arcname=row["display_name"])
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise


def emit(owner):
    from modules.flow_gate.services import git_service
    # Same event envelope as AI approvals; a committed file never becomes a failure
    # merely because its refresh notification could not be delivered.
    try:
        git_service._emit("flow_attachments_updated", owner["project_id"], owner["group_id"],
                          {"flow_id": owner["group_id"], "group_id": owner["group_id"]})
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Attachment refresh notification failed", exc_info=True)
