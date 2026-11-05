"""Standalone CH creation for the Conversation [new chat] button (D0006 §3.10).

A standalone CH is a non-slot conversation document. It has nothing to do with the
workflow: no effective-head check, no sequence result slot, no parent auto-close, no
``prev_doc_id`` lineage (``target_id`` / ``triggered_by`` stay null), and no 423 from an
AI run's group lease (the lease middleware exempts this route explicitly). Filling a
pending CH head of a workflow stays the job of ``POST /documents/next-empty``.

Decision order (D0006 §3.10.2): input -> ``perm_document_create`` -> project -> group ->
group belongs to project -> group not disposed -> number reservation -> document file +
row -> CH auto-complete (approved) -> 201.

flowgate.v02.0047: ``"auto_title": true`` (optional) creates the chat owing its one AI title
on the title it is created with (``auto_title.owed_meta``); without it nothing is owed.

flowgate.v02.0135 T#3: ``"client_request_id"`` (optional) names one create intent of the caller. A
create whose answer was lost (the GUI's 30 s timeout while the server still made the chat, minutes
later on stg) is sent again with the same id: the chat that intent already made is answered (200,
``"replayed": true``) instead of a second one, and a retry that arrives while the first create is
still running waits for it. The id is kept per user/project/group as a digest in ``documents.meta``
(``chat_create_request``); without it the create is unchanged.
"""
from __future__ import annotations
from modules.flow_gate.graph_workflow.services import card_document_ports as document_write_ports

import hashlib
import json
import logging
import re
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from flowgate.conversation import auto_title
from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ("project_id", "group_id", "title")
CREATE_PERMISSION = "perm_document_create"
CLIENT_REQUEST_FIELD = "client_request_id"
CREATE_REQUEST_META_KEY = "chat_create_request"
ERR_CONVERSATION_CREATE_IN_PROGRESS = "conversation_create_in_progress"
_CLIENT_REQUEST_RE = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
# A retry of one intent waits this long for the create still running; the server's single process
# serializes it (stg runs one uvicorn worker, so an in-process lock covers every request).
CREATE_INTENT_WAIT_SEC = 120.0
_intent_guard = threading.Lock()
_intent_locks: dict[str, list] = {}


class ChatCreateForbidden(Exception):
    """No ``perm_document_create``; answered with the existing RBAC 403 response."""


def _invalid(message: str) -> c.ConversationError:
    return c.ConversationError(422, c.ERR_CONVERSATION_INVALID_REQUEST, message)


def validate_input(body: Any) -> tuple[str, str, str]:
    """(project_id, group_id, title) or a 422 ``conversation_invalid_request``."""
    if not isinstance(body, dict):
        raise _invalid("the request body must be an object")
    values: dict[str, str] = {}
    for field in REQUIRED_FIELDS:
        value = body.get(field)
        if not isinstance(value, str) or not value.strip():
            raise _invalid(f"{field} is required")
        values[field] = value.strip()
    title = values["title"]
    if len(title) > legacy_adapter.title_max_len():
        raise _invalid(f"title must be at most {legacy_adapter.title_max_len()} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
        # The title is written into the document's frontmatter line.
        raise _invalid("title must not contain control characters")
    return values["project_id"], values["group_id"], title


def owes_auto_title(body: Any) -> bool:
    value = body.get("auto_title", False) if isinstance(body, dict) else False
    if not isinstance(value, bool):
        raise _invalid("auto_title must be a boolean")
    return value


def client_request_id(body: Any) -> Optional[str]:
    """The caller's create intent id (0135 T#3), or None when the body has none."""
    value = body.get(CLIENT_REQUEST_FIELD) if isinstance(body, dict) else None
    if value is None:
        return None
    if not isinstance(value, str) or not _CLIENT_REQUEST_RE.match(value.strip()):
        raise _invalid("client_request_id must be 8..200 letters, digits, '.', '_', ':' or '-'")
    return value.strip()


def _intent_digest(user_id: str, project_id: str, group_id: str, key: str) -> str:
    return hashlib.sha256(f"{user_id}\n{project_id}\n{group_id}\n{key}".encode("utf-8")).hexdigest()


@contextmanager
def _intent_lock(digest: Optional[str]) -> Iterator[None]:
    """One create per intent at a time; a create without an intent is not serialized."""
    if digest is None:
        yield
        return
    with _intent_guard:
        entry = _intent_locks.setdefault(digest, [threading.Lock(), 0])
        entry[1] += 1
    try:
        if not entry[0].acquire(timeout=CREATE_INTENT_WAIT_SEC):
            raise c.ConversationError(409, ERR_CONVERSATION_CREATE_IN_PROGRESS,
                                      "the same chat create is still running; try again")
        try:
            yield
        finally:
            entry[0].release()
    finally:
        with _intent_guard:
            entry[1] -= 1
            if entry[1] == 0:
                _intent_locks.pop(digest, None)


def _intent_of(doc: dict) -> Optional[str]:
    try:
        meta = json.loads(doc.get("meta") or "null")
    except (TypeError, ValueError):
        return None
    value = meta.get(CREATE_REQUEST_META_KEY) if isinstance(meta, dict) else None
    return value if isinstance(value, str) else None


def _made_by_intent(project_id: str, group_id: str, user_id: str, digest: str) -> Optional[dict]:
    for doc in legacy_adapter.owned_chats(project_id, group_id, user_id):
        if _intent_of(doc) == digest:
            return doc
    return None


def _content(*, project_id: str, module: str, group_id: str, doc_code: str, title: str) -> str:
    lines = [
        "---",
        f"project: {project_id}",
        f"module: {module}",
        f"group: {group_id.rsplit('.', 1)[-1]}",
        f"group_id: {group_id}",
        f"type: {c.CHAT_TYPE_CODE}",
        f"doc_number: {doc_code}",
        f"title: {title}",
        "target_id:",
        "---",
        "",
    ]
    return "\n".join(lines)


def public(doc: dict) -> dict:
    """The 201 body (D0006 §5.4). ``status`` is the review status a CH is created with."""
    return {
        "ok": True,
        "doc_id": doc["doc_id"],
        "project_id": doc.get("project_id"),
        "group_id": doc.get("group_id"),
        "type_code": c.CHAT_TYPE_CODE,
        "title": doc.get("title"),
        "status": doc.get("doc_review_status") or "approved",
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


def create_chat(user: dict, body: Any) -> dict:
    project_id, group_id, title = validate_input(body)
    owes_title = owes_auto_title(body)
    intent = client_request_id(body)
    if not legacy_adapter.has_permission(user, CREATE_PERMISSION, project_id):
        raise ChatCreateForbidden()
    if legacy_adapter.get_project(project_id) is None:
        raise c.ConversationError(404, c.ERR_CONVERSATION_PROJECT_NOT_FOUND, f"project '{project_id}' not found")
    group = legacy_adapter.get_group(group_id)
    if group is None:
        raise c.ConversationError(404, c.ERR_CONVERSATION_GROUP_NOT_FOUND, f"group '{group_id}' not found")
    if str(group.get("project_id") or "") != project_id:
        raise _invalid("group does not belong to the project")
    if legacy_adapter.is_group_disposed(group_id):
        raise c.ConversationError(409, c.ERR_CONVERSATION_GROUP_DISPOSED, "the group has been disposed")

    user_id = str((user or {}).get("user_id") or "")
    digest = _intent_digest(user_id, project_id, group_id, intent) if intent else None
    with _intent_lock(digest):
        if digest is not None:
            made = _made_by_intent(project_id, group_id, user_id, digest)
            if made is not None:
                return {**public(made), "replayed": True}
        module = str(group.get("module") or "").strip() or "none"
        try:
            doc_code = legacy_adapter.reserve_document(group_id, c.CHAT_TYPE_CODE, module)
        except Exception as exc:
            logger.warning("standalone CH number reservation failed for %s: %s", group_id, exc)
            raise c.ConversationError(409, c.ERR_CONVERSATION_NUMBER_RESERVATION_FAILED,
                                      "could not reserve a document number; try again") from exc

        doc_id = f"{group_id}.{doc_code}"
        file_path = legacy_adapter.document_file_path(project_id, group_id, doc_code, module)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            document_write_ports.write_gate("paths", [file_path], lambda: file_path.write_text(
                _content(project_id=project_id, module=module, group_id=group_id, doc_code=doc_code, title=title),
                encoding="utf-8",
            ))
        except OSError as exc:
            raise c.ConversationError(500, "conversation_file_write_failed", "could not write the document file") from exc

        data = {
            "doc_id": doc_id,
            "project_id": project_id,
            "module": module,
            "group_id": group_id,
            "type_code": c.CHAT_TYPE_CODE,
            "seq": legacy_adapter.doc_code_seq(doc_code),
            "title": title,
            "status": "draft",
            "target_id": None,
            "triggered_by": None,
            "owner_id": user_id,
            "file_path": legacy_adapter.to_storage_relative(file_path, project_id),
        }
        if owes_title:
            data["meta"] = auto_title.owed_meta(title)
        if digest is not None:
            meta = json.loads(data.get("meta") or "{}")
            meta[CREATE_REQUEST_META_KEY] = digest
            data["meta"] = json.dumps(meta, ensure_ascii=False)
        doc: Optional[dict] = None
        try:
            with legacy_adapter.store_transaction():
                legacy_adapter.create_document(data, actor_user_id=user_id)
                if c.CHAT_TYPE_CODE in legacy_adapter.auto_complete_types():
                    # The existing CH auto-complete rule: created already approved.
                    doc = legacy_adapter.update_document(doc_id, {"doc_review_status": "approved"})
        except Exception:
            try:
                document_write_ports.write_gate("paths", [file_path], lambda: file_path.unlink(missing_ok=True))
            except OSError:
                pass
            raise
        doc = doc or legacy_adapter.get_document(doc_id) or data
        return public(doc)
