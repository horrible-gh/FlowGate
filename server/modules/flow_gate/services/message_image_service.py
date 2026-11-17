"""Flow-owned temporary images and atomic conversation links (v02test.0029)."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import struct
import uuid
from datetime import datetime, timedelta
from urllib.parse import quote

from modules.flow_gate import process_service
from modules.flow_gate.db import groups
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.documents.attachments import requests as attachments
from modules.flow_gate.documents.attachments.locator import within_storage_jail
from modules.flow_gate.documents.attachments.naming import sanitize_path_component
from modules.flow_gate.storage import paths as storage_paths

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_DIMENSION = 1568
MAX_IMAGES_PER_MESSAGE = 5
MAX_WINDOW_IMAGES = 10
TEMP_RETENTION_HOURS = 24


class MessageImageError(attachments.RequestError):
    pass


def provider_accepts_images(provider_row) -> bool:
    return (provider_row.get("exec_type") == "cli"
            and provider_row.get("kind") in {"claude", "codex"}
            and (provider_row.get("exec_location") or "local") in {"local", "remote"})


def context(flow_id, user, write=False):
    group = groups.get_by_id(flow_id)
    if not group:
        raise MessageImageError(404, "message_image_flow_not_found")
    permission = "perm_document_update" if write else "perm_document_read"
    if not attachments.has_permission(user, permission, group["project_id"]):
        raise MessageImageError(403, "message_image_forbidden")
    if write and process_service.is_group_disposed(flow_id):
        raise MessageImageError(409, "message_image_flow_disposed")
    return group


def dimensions(data, media):
    """Read raster headers with bounded offsets; no image decoding dependency."""
    try:
        if media == "image/png":
            if len(data) < 33 or data[8:16] != b"\x00\x00\x00\x0dIHDR":
                raise ValueError()
            width, height = struct.unpack(">II", data[16:24])
        elif media == "image/gif":
            if len(data) < 13:
                raise ValueError()
            width, height = struct.unpack("<HH", data[6:10])
        elif media == "image/jpeg":
            offset = 2
            while offset < len(data):
                if data[offset] != 255:
                    raise ValueError()
                while offset < len(data) and data[offset] == 255:
                    offset += 1
                marker = data[offset]
                offset += 1
                if marker in (0xD9, 0xDA):
                    raise ValueError()
                if marker in (0x01, *range(0xD0, 0xD9)):
                    continue
                length = int.from_bytes(data[offset:offset + 2], "big")
                if length < 2 or offset + length > len(data):
                    raise ValueError()
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if length < 8:
                        raise ValueError()
                    height, width = struct.unpack(">HH", data[offset + 3:offset + 7])
                    break
                offset += length
            else:
                raise ValueError()
        elif media == "image/webp":
            if len(data) < 20 or int.from_bytes(data[4:8], "little") + 8 != len(data):
                raise ValueError()
            kind = data[12:16]
            length = int.from_bytes(data[16:20], "little")
            chunk = data[20:20 + length]
            if len(chunk) != length:
                raise ValueError()
            if kind == b"VP8X" and length == 10:
                width = 1 + int.from_bytes(chunk[4:7], "little")
                height = 1 + int.from_bytes(chunk[7:10], "little")
            elif kind == b"VP8L" and length >= 5 and chunk[0] == 0x2f:
                bits = int.from_bytes(chunk[1:5], "little")
                width, height = (bits & 0x3fff) + 1, ((bits >> 14) & 0x3fff) + 1
            elif kind == b"VP8 " and length >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
                width, height = struct.unpack("<HH", chunk[6:10])
                width, height = width & 0x3fff, height & 0x3fff
            else:
                raise ValueError()
        else:
            raise ValueError()
        if width <= 0 or height <= 0:
            raise ValueError()
        return width, height
    except (ValueError, IndexError, struct.error):
        raise MessageImageError(422, "message_image_invalid_dimensions") from None


def _path(project_id, flow_id, image_id):
    if not re.fullmatch(r"img_[0-9a-f]{32}", image_id):
        raise MessageImageError(404, "message_image_not_found")
    root = storage_paths.get_storage_root(project_id)
    path = (root / "work" / "_message_images" / sanitize_path_component(project_id)
            / sanitize_path_component(flow_id) / image_id)
    if not within_storage_jail(path, project_id) or not attachments._no_links(path, root):
        raise MessageImageError(403, "message_image_storage_outside_root")
    return path


def file_path(row):
    path = _path(row["project_id"], row["flow_id"], row["image_id"])
    if storage_paths.to_storage_relative(path, row["project_id"]) != row["file_path"]:
        raise MessageImageError(403, "message_image_storage_outside_root")
    return path


def metadata(row):
    from config import settings
    prefix = str(settings.CONTEXT or "").rstrip("/")
    return {**{key: row[key] for key in ("image_id", "media_type", "size", "width", "height")},
            "url": f"{prefix}/api/v1/flows/{quote(row['flow_id'], safe='')}/message-images/{row['image_id']}/content"}


def upload(flow_id, part, user):
    group = context(flow_id, user, write=True)
    owner = attachments._user_id(user)
    if not owner:
        raise MessageImageError(403, "message_image_forbidden")
    data = part.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise MessageImageError(413, "message_image_too_large")
    media = attachments._image_type(data[:32])
    if media is None:
        raise MessageImageError(415, "message_image_unsupported_type")
    width, height = dimensions(data, media)
    if max(width, height) > MAX_IMAGE_DIMENSION:
        raise MessageImageError(422, "message_image_too_large_dimension")
    image_id = "img_" + uuid.uuid4().hex
    path = _path(group["project_id"], flow_id, image_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = _path(group["project_id"], flow_id, image_id)
    row = dict(image_id=image_id, flow_id=flow_id, project_id=group["project_id"],
               uploaded_by=owner, sha256=hashlib.sha256(data).hexdigest(), media_type=media,
               size=len(data), width=width, height=height,
               file_path=storage_paths.to_storage_relative(path, group["project_id"]),
               state="temp", created_at=now_iso(), linked_at=None)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        columns = list(row)
        get_store()._execute("INSERT INTO message_images (" + ", ".join(columns)
                             + ") VALUES (" + ", ".join("?" for _ in columns) + ")", list(row.values()))
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"ok": True, "image": metadata(row)}


def content(flow_id, image_id, user):
    context(flow_id, user)
    row = get_store()._fetch_one("SELECT * FROM message_images WHERE flow_id = ? AND image_id = ?", [flow_id, image_id])
    if not row:
        raise MessageImageError(404, "message_image_not_found")
    if row["state"] == "temp" and row["uploaded_by"] != attachments._user_id(user):
        raise MessageImageError(403, "message_image_forbidden")
    path = file_path(row)
    if not path.is_file():
        raise MessageImageError(404, "message_image_not_found")
    return row, path


def remove(flow_id, image_id, user):
    context(flow_id, user, write=True)
    store = get_store()
    with store.transaction():
        row = store._fetch_one("SELECT * FROM message_images WHERE flow_id = ? AND image_id = ?", [flow_id, image_id])
        if not row or row["uploaded_by"] != attachments._user_id(user) or row["state"] != "temp":
            raise MessageImageError(403, "message_image_forbidden")
        path = file_path(row)
        if store._execute_affected("DELETE FROM message_images WHERE image_id = ? AND state = 'temp'", [image_id]) != 1:
            raise MessageImageError(403, "message_image_forbidden")
        path.unlink(missing_ok=True)
    return {"ok": True}


def validate_ids(images):
    ids = [] if images is None else images
    if (not isinstance(ids, list) or len(ids) > MAX_IMAGES_PER_MESSAGE
            or any(not isinstance(i, str) or not re.fullmatch(r"img_[0-9a-f]{32}", i) for i in ids)
            or len(set(ids)) != len(ids)):
        raise MessageImageError(422, "message_image_invalid_link")
    return ids


def link(doc, seq, user_id, images):
    """Caller owns the turn transaction. A CAS arbitrates against link/delete/sweep."""
    store = get_store()
    for position, image_id in enumerate(validate_ids(images)):
        changed = store._execute_affected(
            "UPDATE message_images SET state = 'linked', linked_at = ? "
            "WHERE image_id = ? AND flow_id = ? AND project_id = ? AND uploaded_by = ? AND state = 'temp'",
            [now_iso(), image_id, doc["group_id"], doc["project_id"], user_id])
        if changed != 1:
            raise MessageImageError(422, "message_image_invalid_link")
        store._execute("INSERT INTO message_image_links (flow_id, conversation_id, seq, image_id, position) "
                       "VALUES (?, ?, ?, ?, ?)", [doc["group_id"], doc["doc_id"], seq, image_id, position])


def for_turn(conversation_id, seq):
    if not conversation_id:
        return []
    return [metadata(row) for row in get_store()._fetch_all(
        "SELECT i.* FROM message_image_links l JOIN message_images i ON i.image_id = l.image_id "
        "WHERE l.conversation_id = ? AND l.seq = ? ORDER BY l.position", [conversation_id, seq])]


def for_range(conversation_id, first_seq, last_seq):
    """Fetch a page's images once, including pages consisting only of text."""
    grouped = {}
    rows = get_store()._fetch_all(
        "SELECT i.*, l.seq FROM message_image_links l JOIN message_images i ON i.image_id = l.image_id "
        "WHERE l.conversation_id = ? AND l.seq >= ? AND l.seq <= ? ORDER BY l.seq, l.position",
        [conversation_id, first_seq, last_seq])
    for row in rows:
        grouped.setdefault(int(row["seq"]), []).append(metadata(row))
    return grouped


def images_for_window(conversation_id, after_seq, limit=MAX_WINDOW_IMAGES):
    limit = max(0, min(int(limit), MAX_WINDOW_IMAGES))
    rows = get_store()._fetch_all(
        "SELECT i.*, l.seq, l.position FROM message_image_links l JOIN message_images i ON i.image_id = l.image_id "
        "WHERE l.conversation_id = ? AND l.seq > ? ORDER BY l.seq DESC, l.position DESC LIMIT ?",
        [conversation_id, after_seq, limit])
    return [{**{key: row[key] for key in ("seq", "position", "image_id", "media_type")},
             "abs_path": str(file_path(row).resolve())} for row in reversed(rows)]


def sweep_expired(now=None):
    cutoff = ((now or datetime.fromisoformat(now_iso())) - timedelta(hours=TEMP_RETENTION_HOURS)).isoformat(timespec="seconds")
    store = get_store()
    rows = store._fetch_all("SELECT * FROM message_images WHERE state = 'temp' AND created_at <= ?", [cutoff])
    removed = failed = 0
    logger = logging.getLogger(__name__)
    for row in rows:
        try:
            path = file_path(row)
            with store.transaction():
                changed = store._execute_affected(
                    "DELETE FROM message_images WHERE image_id = ? AND state = 'temp' AND created_at <= ?",
                    [row["image_id"], cutoff])
            # A failed commit must leave the file available with its restored row.
            # A successful CAS prevents any later link from claiming this image.
            if changed == 1:
                path.unlink(missing_ok=True)
                removed += 1
        except Exception:
            failed += 1
            logger.exception("message image sweep failed image_id=%s", row["image_id"])
    if failed:
        logger.warning("message image sweep removed=%d failed=%d", removed, failed)
    return removed
