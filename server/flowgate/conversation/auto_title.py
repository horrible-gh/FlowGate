"""One AI-made title for a new standalone CH.

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
import logging
import re
from typing import Any, Optional

from flowgate.conversation import activity
from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter

logger = logging.getLogger(__name__)

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
    try:
        answer, reason = legacy_adapter.complete_prompt(
            project_id, provider_id, build_prompt(message), timeout=TIMEOUT_SEC, max_tokens=MAX_TOKENS)
    except Exception as exc:
        # Execution may already have happened. Leave claimed intact; never log prompt,
        # raw exception text, command arguments, credentials or the model's answer.
        logger.error("auto_title stage=exception doc=%s project=%s provider=%s exception_type=%s",
                     doc_id, project_id, provider_id, type(exc).__name__)
        raise
    title = clean_title(answer, min(TITLE_MAX_CHARS, legacy_adapter.title_max_len())) if answer is not None else None
    if title is None:
        # Nothing was asked when the provider could not be used: the debt goes back.
        unavailable = answer is None and reason in legacy_adapter.PROMPT_UNAVAILABLE_REASONS
        safe_reasons = legacy_adapter.PROMPT_UNAVAILABLE_REASONS | {
            "timeout", "transport_failed", "provider_launch_failed", "execution_failed", "internal_error",
            "remote_cli_unsupported", "cancelled_by_user",
        }
        logger.warning("auto_title stage=%s doc=%s project=%s provider=%s reason=%s answer_present=%s valid_title=false",
                       "provider_unavailable" if unavailable else "model_result" if answer is None else "empty_title",
                       doc_id, project_id, provider_id,
                       reason if reason in safe_reasons else "unknown" if reason else "none", answer is not None)
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


# One first-run title shared by the Flow, its start card and the existing chat.
# Reply and card submission ports call this after their own commit.
FIRST_NAMES_LEASE_SEC = 180


def generate_flow_names(flow_id: str, user: dict, provider_id: str, message: str = "",
                        chat_doc_id: Optional[str] = None, title: Optional[str] = None,
                        allow_fallback: bool = True) -> dict:
    """One durable candidate per flow, shared by every eligible target."""
    result = _generate_names(flow_id, user, provider_id, message, chat_doc_id,
                             seed=clean_title(title, min(TITLE_MAX_CHARS, legacy_adapter.title_max_len()))
                                  if isinstance(title, str) else None,
                             allow_fallback=allow_fallback)
    changed = result.get("changed", [])
    if changed:
        try:
            from .first_names_triggers import notify
            notify(flow_id, changed, chat_doc_id if "chat" in changed else None)
        except Exception as exc:
            logger.warning("flow naming notification exception_type=%s", type(exc).__name__)
    return {k: v for k, v in result.items() if k in ("status", "targets")}


def _generate_names(flow_id: str, user: dict, provider_id: str, message: str = "",
                        chat_doc_id: Optional[str] = None, seed: Optional[str] = None,
                        allow_fallback: bool = True) -> dict:
    """Best effort, durable, retryable per target. Call outside an open transaction.

    The caller supplies actual root/request content, or the existing chat's first
    user message is read. Blank content never invokes the model. A cached candidate
    is reused for failed saves, including after a process restart.
    """
    import time
    import uuid
    from . import first_names_ports as p
    key = flow_id
    ticket = uuid.uuid4().hex
    changed = []
    try:
        def claim():
            group, targets = p.targets(flow_id, chat_doc_id)
            if not legacy_adapter.has_permission(user, UPDATE_PERMISSION, group["project_id"]):
                raise PermissionError("no_update_permission")
            record = p.ledger.load(key)
            # A later conversation reuses the flow candidate; its own done/manual
            # marker protects it after the ledger moves on to another chat.
            if record.get("candidate"):
                # A root created/missed after the first attempt still uses the saved candidate.
                for kind, target in targets.items():
                    previous = record.setdefault("targets", {}).get(kind, {})
                    if (previous.get("id") != target["id"] or previous.get("state") != "done") and p.eligible(kind, target):
                        record["targets"][kind] = {**target, "state": "pending"}
                p.ledger.save(key, record)
                return group, record
            if not seed and record.get("lease_until", 0) > time.time():
                return group, {"busy": True}
            text = (_first_user_message(chat_doc_id) or "") if chat_doc_id else str(message or "").strip()
            if not text and chat_doc_id:
                text = _first_user_message(chat_doc_id) or ""
            if not seed and not allow_fallback:
                return group, None
            if not seed and record.get("fallback_requested"):
                logger.warning("flow_auto_title no_title flow=%s reason=title_missing", flow_id)
                return group, None
            if not seed and not any(ch.isalnum() for ch in text):
                return group, None
            pending = {kind: {**target, "state": "pending" if p.eligible(kind, target) else "skipped"}
                       for kind, target in targets.items()}
            if not any(t["state"] == "pending" for t in pending.values()):
                return group, {"ineligible": pending}
            record.update(ticket=ticket, lease_until=time.time() + FIRST_NAMES_LEASE_SEC,
                          targets=pending, chat_doc_id=chat_doc_id)
            if seed:
                record["candidate"] = seed
                record["lease_until"] = 0
            p.ledger.save(key, record)
            return group, {**record, "message": text}
        group, record = p.guard(flow_id, claim)
        if record is None:
            return {"status": "unchanged"}
        if record.get("busy"):
            return {"status": "unchanged", "busy": True}
        if "ineligible" in record:
            return {"status": "unchanged", "targets": {k: "skipped" for k in record["ineligible"]}}
        if not record.get("candidate"):
            prompt = build_prompt(record["message"]) + (
                "\nThis title also names the workflow and its start card. "
                "Use only the supplied task facts. If no concrete task can be identified, reply NO_TITLE.")
            if legacy_adapter.naming_remote_provider(group["project_id"], provider_id):
                answer, reason = None, "title_missing"
            else:
                # Persist the one supplementary-call budget before execution. Even a
                # timeout/process loss cannot spend it again; a later reply supplies title.
                def spend_fallback():
                    current = p.ledger.load(key)
                    if current.get("ticket") != ticket or current.get("fallback_requested"):
                        return False
                    current["fallback_requested"] = True
                    p.ledger.save(key, current)
                    return True
                if not p.guard(flow_id, spend_fallback):
                    return {"status": "unchanged"}
                answer, reason = legacy_adapter.complete_prompt(group["project_id"], provider_id, prompt,
                                                                timeout=TIMEOUT_SEC, max_tokens=MAX_TOKENS)
            title = clean_title(answer, min(TITLE_MAX_CHARS, legacy_adapter.title_max_len()))
            if title == "NO_TITLE":
                title = None
            def remember():
                current = p.ledger.load(key)
                if current.get("ticket") != ticket:
                    return None
                current["lease_until"] = 0
                if title:
                    current["candidate"] = title
                p.ledger.save(key, current)
                return current if title else None
            record = p.guard(flow_id, remember)
            if record is None:
                safe_reasons = legacy_adapter.PROMPT_UNAVAILABLE_REASONS | {
                    "remote_cli_unsupported", "title_missing", "timeout", "transport_failed",
                    "provider_launch_failed", "execution_failed", "internal_error", "cancelled_by_user"}
                logger.warning("flow_auto_title no_title flow=%s reason=%s", flow_id,
                               reason if reason in safe_reasons else "title_missing" if not reason else "unknown")
                return {"status": "unchanged"}
        # Independent transactions: a failed target cannot roll back completed ones.
        for kind in record["targets"]:
            try:
                def save_one():
                    current = p.ledger.load(key)
                    target = current["targets"][kind]
                    if target["state"] != "pending":
                        return
                    _, live = p.targets(flow_id, chat_doc_id if kind == "chat" else None,
                                        chat_only=kind == "chat")
                    now = live.get(kind)
                    if (now is None or now["id"] != target["id"] or now["title"] != target["title"]
                            or not p.eligible(kind, now)):
                        target["state"] = "skipped"
                    else:
                        p.apply(flow_id, kind, now, current["candidate"], str(user.get("user_id") or ""))
                        target["state"] = "done"
                    p.ledger.save(key, current)
                    return target["state"] == "done"
                if p.guard(flow_id, save_one):
                    changed.append("card" if kind == "root" else kind)
            except Exception as exc:
                logger.warning("flow_auto_title save flow=%s target=%s exception_type=%s",
                               flow_id, kind, type(exc).__name__)
        final = p.guard(flow_id, lambda: p.ledger.load(key))
        states = {k: v["state"] for k, v in final["targets"].items()}
        return {"status": "pending" if "pending" in states.values() else "done", "targets": states,
                "changed": changed, "candidate": final.get("candidate")}

    except Exception as exc:
        # Release only our own generation lease; the next reply can retry immediately.
        def release():
            current = p.ledger.load(key)
            if current.get("ticket") == ticket:
                current["lease_until"] = 0
                p.ledger.save(key, current)
        try:
            p.guard(flow_id, release)
        except Exception:
            pass
        logger.warning("flow_auto_title flow=%s exception_type=%s", flow_id, type(exc).__name__)
        return {"status": "failed"}


# Bounded background entry point: no DB lock/model call on the sending thread.
# A full pool refuses best-effort naming immediately; a later trigger can retry.
import threading as _threading
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
_first_names_pool = _ThreadPoolExecutor(max_workers=2, thread_name_prefix="flow-names")
_first_names_slots = _threading.BoundedSemaphore(8)


def schedule_flow_names(flow_id: str, user: dict, provider_id: str, message: str = "",
                        chat_doc_id: Optional[str] = None) -> bool:
    """Queue after caller commit; False on saturation/shutdown, never blocks sending."""
    if not _first_names_slots.acquire(blocking=False):
        return False
    try:
        future = _first_names_pool.submit(generate_flow_names, flow_id, dict(user), provider_id, message, chat_doc_id)
        future.add_done_callback(lambda _: _first_names_slots.release())
        return True
    except Exception:
        _first_names_slots.release()
        return False
