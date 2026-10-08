"""One AI-made title for a new standalone CH (flowgate.v02.0047 R0001 §2.2).

``POST /conversations/{doc_id}/auto-title`` with ``{"provider_id"}``. The composer asks once,
right after the first message of a chat it created on the first send was accepted; the send
itself never waits for this.

Whether a title is owed is the server's own record, not the caller's word: a chat created
with ``"auto_title": true`` carries ``meta.chat_auto_title = {"state": "owed", "title":
<provisional title>}``. Only an owed chat whose title still is that provisional title is asked
for, so:

- once: the debt is claimed (compare-and-set on ``meta``) before the AI is called and marked
  ``done`` with the title write. A second request -- or two at once -- gets 409
  ``conversation_auto_title_not_owed`` without an AI call. Only a provider that could not be
  used at all (nothing was asked) hands the debt back.
- a manual title wins: any title written through the document update (``PATCH
  /documents/{id}``) ends the debt (``document_service``); a title that differs from the
  provisional one ends it here, and a rename that lands while the model answers makes the
  final compare-and-set fail -- 409 ``conversation_title_changed``, nothing overwritten. A
  chat created with a title the user typed owes nothing.

The model call is the run-less one-prompt path of the AI connection test
(``resolve_subject`` + ``execute_prompt``) on the project's effective AI settings and the
provider selected in the composer. No model or provider is fixed here.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from flowgate.conversation import activity
from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter

UPDATE_PERMISSION = "perm_document_update"
META_KEY = "chat_auto_title"
OWED, CLAIMED, DONE, FAILED, MANUAL = "owed", "claimed", "done", "failed", "manual"
TITLE_MAX_CHARS = 40
MESSAGE_MAX_CHARS = 2000
TIMEOUT_SEC = 90
MAX_TOKENS = 60

_PREFIX = re.compile(r"^(?:#+\s*|title\s*[:：]\s*|제목\s*[:：]\s*|タイトル\s*[:：]\s*)", re.IGNORECASE)
_QUOTES = "\"'`*“”‘’「」『』«»"


def _invalid(message: str) -> c.ConversationError:
    return c.ConversationError(422, c.ERR_CONVERSATION_INVALID_REQUEST, message)


def validate_input(body: Any) -> str:
    """provider_id or a 422 ``conversation_invalid_request``."""
    if not isinstance(body, dict):
        raise _invalid("the request body must be an object")
    value = body.get("provider_id")
    if not isinstance(value, str) or not value.strip():
        raise _invalid("provider_id is required")
    return value.strip()


def owed_meta(title: str) -> str:
    """``documents.meta`` of a chat created owing its AI title on ``title``."""
    return json.dumps({META_KEY: {"state": OWED, "title": title}}, ensure_ascii=False)


def _owed(doc: dict) -> Optional[tuple[dict, str]]:
    """(meta, provisional title) while the chat owes its AI title, else None."""
    try:
        meta = json.loads(doc.get("meta") or "null")
    except ValueError:
        return None
    marker = meta.get(META_KEY) if isinstance(meta, dict) else None
    if not isinstance(marker, dict) or marker.get("state") != OWED or not isinstance(marker.get("title"), str):
        return None
    return meta, marker["title"]


def _with_state(meta: dict, state: str) -> str:
    return json.dumps({**meta, META_KEY: {**meta[META_KEY], "state": state}}, ensure_ascii=False)


def build_prompt(message: str) -> str:
    text = message.strip()
    if len(text) > MESSAGE_MAX_CHARS:
        text = text[:MESSAGE_MAX_CHARS]
    return (
        "Write a short title for a conversation that begins with the user message below.\n"
        "- Use the same language as the message.\n"
        f"- At most {TITLE_MAX_CHARS} characters on one line, no quotes, no trailing period.\n"
        "- Reply with the title only. Do not call tools, read or modify files, or run commands.\n"
        "\n"
        "User message:\n"
        "<<<\n"
        f"{text}\n"
        ">>>"
    )


def clean_title(text: Optional[str], limit: int) -> Optional[str]:
    """The first non-empty answer line as a frontmatter-safe title, or None."""
    line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    line = _PREFIX.sub("", line.strip(_QUOTES).strip()).strip().strip(_QUOTES).strip()
    line = re.sub(r"\s+", " ", line)
    line = "".join(ch for ch in line if ord(ch) >= 32 and ord(ch) != 127).rstrip(".。").strip()
    if len(line) > limit:
        line = line[: limit - 1].rstrip() + "…"
    return line or None


def _first_user_message(doc_id: str) -> Optional[str]:
    for turn in legacy_adapter.first_turns(doc_id, 20):
        if turn.get("speaker") == "user" and str(turn.get("body") or "").strip():
            return str(turn["body"])
    return None


def _title_changed() -> c.ConversationError:
    return c.ConversationError(409, c.ERR_CONVERSATION_TITLE_CHANGED, "the chat title was changed")


def _not_owed() -> c.ConversationError:
    return c.ConversationError(409, c.ERR_CONVERSATION_AUTO_TITLE_NOT_OWED, "the chat owes no AI title")


def generate(doc_id: str, user: dict, body: Any) -> dict:
    provider_id = validate_input(body)
    doc = activity.readable_chat_document(doc_id, user)
    project_id = str(doc.get("project_id") or "")
    if not legacy_adapter.has_permission(user, UPDATE_PERMISSION, project_id):
        raise c.ConversationError(403, c.ERR_ACTIVITY_FORBIDDEN, "no update permission for this document")
    if legacy_adapter.is_group_disposed(doc.get("group_id")):
        raise c.ConversationError(409, c.ERR_CONVERSATION_GROUP_DISPOSED, "the group has been disposed")
    owed = _owed(doc)
    if owed is None:
        raise _not_owed()
    meta, provisional = owed
    owed_text = doc["meta"]
    if (doc.get("title") or "") != provisional:
        legacy_adapter.update_meta_cas(doc_id, owed_text, _with_state(meta, MANUAL))
        raise _title_changed()
    message = _first_user_message(doc_id)
    if message is None:
        raise c.ConversationError(409, c.ERR_CONVERSATION_AUTO_TITLE_NO_MESSAGE, "the chat has no user message yet")

    claimed = _with_state(meta, CLAIMED)
    if not legacy_adapter.update_meta_cas(doc_id, owed_text, claimed):
        raise _not_owed()
    answer, reason = legacy_adapter.complete_prompt(
        project_id, provider_id, build_prompt(message), timeout=TIMEOUT_SEC, max_tokens=MAX_TOKENS)
    title = clean_title(answer, min(TITLE_MAX_CHARS, legacy_adapter.title_max_len())) if answer is not None else None
    if title is None:
        # Nothing was asked when the provider could not be used: the debt goes back.
        unavailable = answer is None and reason in legacy_adapter.PROMPT_UNAVAILABLE_REASONS
        legacy_adapter.update_meta_cas(doc_id, claimed, owed_text if unavailable else _with_state(meta, FAILED))
        raise c.ConversationError(
            409 if unavailable else 502,
            c.ERR_CONVERSATION_AUTO_TITLE_UNAVAILABLE if unavailable else c.ERR_CONVERSATION_AUTO_TITLE_FAILED,
            (reason or "the AI did not answer") if answer is None else "empty_response")

    user_id = str((user or {}).get("user_id") or "")
    with legacy_adapter.store_transaction():
        current = legacy_adapter.get_document(doc_id) or {}
        kept = (current.get("title") or "") == provisional
        # Paid before the write, so the title update does not count as a manual one; a
        # manual rename meanwhile already ended the debt and fails this compare-and-set.
        paid = kept and legacy_adapter.update_meta_cas(doc_id, claimed, _with_state(meta, DONE))
        if not kept:
            legacy_adapter.update_meta_cas(doc_id, claimed, _with_state(meta, MANUAL))
        saved = (legacy_adapter.rename_document(doc_id, title, user_id) or current) if paid else None
    if saved is None:
        raise _title_changed()
    return {
        "ok": True,
        "doc_id": doc_id,
        "title": saved.get("title") or title,
        "updated_at": saved.get("updated_at"),
    }
