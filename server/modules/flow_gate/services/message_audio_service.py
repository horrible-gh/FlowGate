"""Flow-owned audio uploads. Only the transcript becomes AI conversation input."""
from __future__ import annotations
from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote
import uuid

from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.documents.attachments import requests as attachments
from modules.flow_gate.documents.attachments.locator import within_storage_jail
from modules.flow_gate.documents.attachments.naming import sanitize_path_component
from modules.flow_gate.storage import paths
from . import message_image_service as images
from .voice_errors import VoiceError
from .voice_runtime import MAX_AUDIO_BYTES, convert
from .voice_service import manager


def context(flow_id, user, write=False):
    try:
        return images.context(flow_id, user, write)
    except images.MessageImageError as exc:
        raise VoiceError("message_audio_forbidden", "Flow unavailable or access denied", exc.status) from exc


def file_path(row):
    if not re.fullmatch(r"aud_[0-9a-f]{32}", row["audio_id"]):
        raise VoiceError("message_audio_invalid", "Invalid audio identifier", 422)
    root = paths.get_storage_root(row["project_id"])
    target = (root / "work" / "_message_audio" / sanitize_path_component(row["project_id"])
              / sanitize_path_component(row["flow_id"]) / (row["audio_id"] + ".m4a"))
    if not within_storage_jail(target, row["project_id"]) or not attachments._no_links(target, root):
        raise VoiceError("message_audio_forbidden", "Invalid storage path", 403)
    return target


def metadata(row):
    from config import settings
    prefix = str(settings.CONTEXT or "").rstrip("/")
    return {"audio_id": row["audio_id"], "media_type": "audio/mp4", "size": row["size"],
            "text": row["transcript"],
            "url": f"{prefix}/api/v1/flows/{quote(row['flow_id'], safe='')}/message-audio/{row['audio_id']}/content"}


def upload(flow_id, part, user, service=None, model=None):
    group = context(flow_id, user, True)
    owner = attachments._user_id(user)
    if not owner:
        raise VoiceError("message_audio_forbidden", "Authenticated owner required", 403)
    data = part.file.read(MAX_AUDIO_BYTES + 1)
    with manager.inference, tempfile.TemporaryDirectory(prefix="flowgate-audio-") as temporary:
        result = manager.transcribe(data, service, model, user["user_id"])
        if not result["text"].strip():
            raise VoiceError("voice_empty_transcription", "No speech recognized", 422)
        encoded = convert(manager.service_dir(result["service"]), data, Path(temporary), m4a=True).read_bytes()
    row = dict(audio_id="aud_" + uuid.uuid4().hex, flow_id=flow_id, project_id=group["project_id"],
               uploaded_by=owner, sha256=hashlib.sha256(encoded).hexdigest(), size=len(encoded),
               transcript=result["text"], state="temp", created_at=now_iso(), conversation_id=None, seq=None)
    target = file_path(row)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = file_path(row)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
        columns = list(row)
        get_store()._execute("INSERT INTO message_audio (" + ", ".join(columns) + ") VALUES (" +
                             ", ".join("?" for _ in columns) + ")", list(row.values()))
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"ok": True, "audio": metadata(row), "elapsed_ms": result["elapsed_ms"]}


def content(flow_id, audio_id, user):
    context(flow_id, user)
    row = get_store()._fetch_one("SELECT * FROM message_audio WHERE flow_id = ? AND audio_id = ?", [flow_id, audio_id])
    if not row:
        raise VoiceError("message_audio_not_found", "Audio not found", 404)
    if row["state"] == "temp" and row["uploaded_by"] != attachments._user_id(user):
        raise VoiceError("message_audio_forbidden", "Audio belongs to another user", 403)
    if row["state"] == "linked":
        turn = get_store()._fetch_one("SELECT deleted_at FROM conversation_turns WHERE doc_id = ? AND seq = ? AND audio_id = ?",
                                      [row["conversation_id"], row["seq"], audio_id])
        if not turn or turn.get("deleted_at"):
            raise VoiceError("message_audio_not_found", "Audio message is deleted", 404)
    target = file_path(row)
    if not target.is_file():
        raise VoiceError("message_audio_not_found", "Audio not found", 404)
    return target


def remove(flow_id, audio_id, user):
    context(flow_id, user, True)
    store = get_store()
    with store.transaction():
        row = store._fetch_one("SELECT * FROM message_audio WHERE flow_id = ? AND audio_id = ?", [flow_id, audio_id])
        if not row or row["uploaded_by"] != attachments._user_id(user) or row["state"] != "temp":
            raise VoiceError("message_audio_forbidden", "Only your unsent audio can be removed", 403)
        target = file_path(row)
        if store._execute_affected("DELETE FROM message_audio WHERE audio_id = ? AND state = 'temp'", [audio_id]) != 1:
            raise VoiceError("message_audio_conflict", "Audio was linked concurrently", 409)
        target.unlink(missing_ok=True)
    return {"ok": True}


def prepare(doc, actor, body, audio_id):
    if actor.get("kind") != "session" or not isinstance(audio_id, str) or not re.fullmatch(r"aud_[0-9a-f]{32}", audio_id):
        raise VoiceError("message_audio_invalid", "Invalid audio attachment", 422)
    row = get_store()._fetch_one(
        "SELECT * FROM message_audio WHERE audio_id = ? AND flow_id = ? AND project_id = ? AND uploaded_by = ?",
        [audio_id, doc["group_id"], doc["project_id"], actor.get("user_id")])
    if not row:
        raise VoiceError("message_audio_invalid", "Audio is not owned by this user and Flow", 422)
    # Include the server's transcript, independent of client-provided text.
    transcript = row["transcript"]
    return transcript if not body.strip() or body.strip() == transcript.strip() else body + "\n\n" + transcript


def link(doc, seq, user_id, audio_id):
    store = get_store()
    changed = store._execute_affected(
        "UPDATE message_audio SET state = 'linked', conversation_id = ?, seq = ? "
        "WHERE audio_id = ? AND flow_id = ? AND project_id = ? AND uploaded_by = ? AND state = 'temp'",
        [doc["doc_id"], seq, audio_id, doc["group_id"], doc["project_id"], user_id])
    if changed != 1:
        raise VoiceError("message_audio_invalid", "Audio is already linked or unavailable", 422)
    store._execute("UPDATE conversation_turns SET audio_id = ? WHERE doc_id = ? AND seq = ?", [audio_id, doc["doc_id"], seq])


def for_turn(row):
    if not row.get("audio_id") or row.get("deleted_at"):
        return None
    audio = get_store()._fetch_one("SELECT * FROM message_audio WHERE audio_id = ? AND conversation_id = ? AND seq = ?",
                                  [row["audio_id"], row["doc_id"], row["seq"]])
    return metadata(audio) if audio else None


def sweep_expired():
    cutoff = (datetime.now().astimezone() - timedelta(hours=24)).isoformat(timespec="seconds")
    store = get_store()
    for row in store._fetch_all("SELECT * FROM message_audio WHERE state = 'temp' AND created_at < ?", [cutoff]):
        with store.transaction():
            if store._execute_affected("DELETE FROM message_audio WHERE audio_id = ? AND state = 'temp'", [row["audio_id"]]) == 1:
                file_path(row).unlink(missing_ok=True)
