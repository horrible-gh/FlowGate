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
import threading as _threading
from typing import Any, Optional

from flowgate.conversation import activity
from flowgate.conversation import contracts as c
from flowgate.conversation import legacy_adapter

logger = logging.getLogger(__name__)

UPDATE_PERMISSION = "perm_document_update"
META_KEY = "chat_auto_title"
OWED, CLAIMED, DONE, FAILED, MANUAL = "owed", "claimed", "done", "failed", "manual"
TITLE_MAX_CHARS = 30  # Shared names must fit graph_node_title_states (1..30).
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


def build_prompt(message: str, limit: int = TITLE_MAX_CHARS) -> str:
    text = message.strip()
    if len(text) > MESSAGE_MAX_CHARS:
        text = text[:MESSAGE_MAX_CHARS]
    return (
        "Write a short title for a conversation that begins with the user message below.\n"
        "- Use the same language as the message.\n"
        f"- At most {limit} characters on one line, no quotes, no trailing period.\n"
        "- Never include document IDs, flow IDs, card numbers, or internal identifiers.\n"
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


# Check before truncation: a long task prefix must not hide an identifier suffix.
_INTERNAL_ID = re.compile(
    r"(?<![\w])(?:[a-z][\w-]*\.[a-z][\w-]*\.(?:\d{4,}|F\d+)"
    r"(?:\.\d+-[a-z]+)?|\d{4,}-[a-z]{1,4}|F\d+(?:-\d+)?)(?![\w])",
    re.IGNORECASE,
)


def clean_flow_title(text: Optional[str]) -> Optional[str]:
    """Validate the shared candidate before any target or ledger is written."""
    if not isinstance(text, str):
        return None
    line = clean_title(text, max(len(text), 1))
    if not line or line.upper() == "NO_TITLE" or _INTERNAL_ID.search(line):
        return None
    from modules.flow_gate.graph_workflow.services.node_title_names import normalize_title, TITLE_MAX_CHARS as card_limit
    return clean_title(normalize_title(line), min(TITLE_MAX_CHARS, card_limit, legacy_adapter.title_max_len()))


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
        answer, reason = legacy_adapter.complete_naming(
            project_id, provider_id, build_prompt(message), str(user.get("user_id") or ""),
            timeout=TIMEOUT_SEC, max_tokens=MAX_TOKENS)
    except Exception as exc:
        # Execution may already have happened. Leave claimed intact; never log prompt,
        # raw exception text, command arguments, credentials or the model's answer.
        logger.error("auto_title stage=exception doc=%s project=%s provider=%s exception_type=%s",
                     doc_id, project_id, provider_id, type(exc).__name__)
        raise
    title = clean_title(answer, min(TITLE_MAX_CHARS, legacy_adapter.title_max_len())) if answer is not None else None
    if title is None:
        # Nothing was asked when the provider could not be used: the debt goes back.
        unavailable = answer is None and reason in (legacy_adapter.PROMPT_UNAVAILABLE_REASONS | {"title_disabled"})
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


# First-run names: the Flow, its start card and each chat are named separately, each from
# its own content (0080). Reply and card submission ports call this after their own commit.
#   flow  -- the whole task: the first request (first chat message / root body) or a
#            submitted ``flow_title``.
#   root  -- the start card: the body of the document bound to it.
#   chat  -- one chat: its own first user message or the ``title`` a reply in it submitted.
FIRST_NAMES_LEASE_SEC = 180
NAME_KINDS = ("chat", "flow", "root")
PARALLEL_NAMES = True  # name the targets of one request at the same time

def _safe_reason(reason: Optional[str]) -> str:
    safe = legacy_adapter.PROMPT_UNAVAILABLE_REASONS | {
        "remote_cli_unsupported", "title_missing", "timeout", "transport_failed",
        "provider_launch_failed", "execution_failed", "internal_error", "cancelled_by_user",
        "title_disabled"}
    return reason if reason in safe else "title_missing" if not reason else "unknown"


def _bounded(text: str) -> str:
    text = str(text or "").strip()
    return text[:MESSAGE_MAX_CHARS] if len(text) > MESSAGE_MAX_CHARS else text


_RULES = (
    "- Use the same language as the text.\n"
    f"- At most {TITLE_MAX_CHARS} characters on one line, no quotes, no trailing period.\n"
    "- Never include document IDs, flow IDs, card numbers, or internal identifiers.\n"
    "- Use only facts in the text. If no concrete subject can be identified, reply NO_TITLE.\n"
    "- Reply with the title only. Do not call tools, read or modify files, or run commands.\n"
)


def build_name_prompt(kind: str, text: str, start_document: str = "") -> str:
    """The naming prompt of one target; each target reads only its own content.

    A flow named from a chat's first message also gets ``start_document`` (the body of its
    start card): a first message such as "let's talk after you read it" names nothing, and
    without the document the flow kept its default name while its card was renamed.
    """
    if kind == "flow" and _bounded(start_document) and _bounded(start_document) != _bounded(text):
        return (
            "Write a short name for a workflow whose overall task is described below. "
            "It names the whole task, not a single step, card or document.\n"
            f"{_RULES}"
            "- Name the task from the first request. If the first request alone names no concrete "
            "subject, name it from the start document instead.\n"
            f"\nFirst request:\n<<<\n{_bounded(text)}\n>>>\n"
            f"\nStart document:\n<<<\n{_bounded(start_document)}\n>>>")
    if kind == "chat":
        return build_prompt(text) + "\nIf no concrete subject can be identified, reply NO_TITLE."
    intro, label = {
        "flow": ("Write a short name for a workflow whose overall task is described below. "
                 "It names the whole task, not a single step, card or document.", "Task"),
        "root": ("Write a short title for the document below that says what the document is about. "
                 "It is shown as the title of the card holding this document.", "Document"),
    }[kind]
    return f"{intro}\n{_RULES}\n{label}:\n<<<\n{_bounded(text)}\n>>>"


_LINE_MARKS = re.compile(r"^(?:#+|[-*+>]|\d+[.)])\s*")


def heading_title(kind: str, text: str, doc_type: Optional[str] = None) -> Optional[str]:
    """A model-free name: the first meaningful line of a document body (heading marks removed)."""
    from modules.flow_gate.graph_workflow.services.flow_default_names import is_default_title
    for raw in str(text or "").splitlines():
        line = _LINE_MARKS.sub("", raw.strip()).strip()
        if sum(ch.isalnum() for ch in line) < 2:
            continue
        title = clean_flow_title(line)
        checkable = kind != "root" or doc_type in ("R", "B")
        if title and not (checkable and is_default_title(title, kind, doc_type=doc_type)):
            return title
    return None


def _root_text(flow_id: str, provider_id: str) -> str:
    from modules.flow_gate.graph_workflow.store import definition_store
    from .first_names_triggers import run_context
    try:
        return run_context(flow_id, definition_store.latest_revision_no(flow_id), provider_id)[1] or ""
    except Exception as exc:
        logger.warning("flow_auto_title reason=root_read_failed flow=%s exception_type=%s",
                       flow_id, type(exc).__name__)
        return ""


def generate_flow_names(flow_id: str, user: dict, provider_id: str, message: str = "",
                        chat_doc_id: Optional[str] = None, title: Optional[str] = None,
                        allow_fallback: bool = True, flow_title: Optional[str] = None,
                        kinds: Optional[tuple] = None) -> dict:
    """Name every eligible target from its own content. Best effort, durable and retryable
    per target. Names come from the configured naming provider only: ``title`` and
    ``flow_title`` are accepted for caller compatibility but no longer seed anything,
    because executor replies do not supply names."""
    targets, changed = {}, []
    wanted = [kind for kind in (kinds or NAME_KINDS) if kind != "chat" or chat_doc_id]
    states = _name_targets(wanted, flow_id, user, provider_id, message, chat_doc_id, allow_fallback)
    for kind in wanted:
        state = states[kind]
        if state == "changed":
            changed.append("card" if kind == "root" else kind)
            state = "done"
        if state != "missing":
            targets[kind] = state
    if changed:
        try:
            from .first_names_triggers import notify
            notify(flow_id, changed, chat_doc_id if "chat" in changed else None)
        except Exception as exc:
            logger.warning("flow naming notification exception_type=%s", type(exc).__name__)
    states = set(targets.values())
    status = ("pending" if "pending" in states else "done" if "done" in states
              else "failed" if "failed" in states else "unchanged")
    return {"status": status, "targets": targets}


def _name_targets(kinds: list, flow_id: str, user: dict, provider_id: str, message: str,
                  chat_doc_id: Optional[str], allow_fallback: bool) -> dict:
    """{kind: state}. Targets are independent (own ledger slot, own model call), so they are
    named at the same time: one name costs a whole remote Agent job when the naming provider
    is a remote CLI, and three in a row made the last name arrive three jobs late."""
    def one(kind):
        try:
            return _name_target(flow_id, user, provider_id, kind, message, chat_doc_id,
                                seed=None, allow_fallback=allow_fallback)
        except Exception as exc:
            logger.warning("flow_auto_title flow=%s target=%s exception_type=%s",
                           flow_id, kind, type(exc).__name__)
            return "failed"

    states: dict = {}
    if not PARALLEL_NAMES or len(kinds) < 2:
        for kind in kinds:
            states[kind] = one(kind)
        return states
    workers = [_threading.Thread(target=lambda k=kind: states.__setitem__(k, one(k)),
                                 name=f"flow-names-{kind}", daemon=True) for kind in kinds[1:]]
    for worker in workers:
        worker.start()
    states[kinds[0]] = one(kinds[0])
    for worker in workers:
        worker.join()
    return states


def _name_target(flow_id: str, user: dict, provider_id: str, kind: str, message: str,
                 chat_doc_id: Optional[str], seed: Optional[str], allow_fallback: bool) -> str:
    """changed | done | skipped | pending | unchanged | failed | missing for one target.

    Each target has its own ledger slot (``names.flow``, ``names.root``, ``names.chat:<id>``)
    with its own candidate, lease and one supplementary model call. A saved candidate is
    reused after a failed save, including after a process restart.
    """
    import time
    import uuid
    from . import first_names_ports as p
    slot = f"chat:{chat_doc_id}" if kind == "chat" else kind
    ticket = uuid.uuid4().hex

    def live():
        group, found = p.targets(flow_id, chat_doc_id if kind == "chat" else None,
                                 chat_only=kind == "chat", root=kind == "root")
        return group, found.get(kind)

    def slot_of(record):
        return record.setdefault("names", {}).setdefault(slot, {})

    try:
        def claim():
            group, target = live()
            if not legacy_adapter.has_permission(user, UPDATE_PERMISSION, group["project_id"]):
                raise PermissionError("no_update_permission")
            if target is None:
                return group, None, "missing"
            record = p.ledger.load(flow_id)
            entry = slot_of(record)
            if entry.get("state") == "done" and entry.get("id") == target["id"]:
                logger.info("flow naming skipped reason=target_not_pending flow=%s target=%s state=%s",
                            flow_id, kind, "done")
                return group, target, "done"
            if not p.eligible(kind, target):
                logger.info("flow naming skipped reason=ineligible flow=%s target=%s", flow_id, kind)
                return group, target, "skipped"
            if entry.get("id") != target["id"]:
                # Another root document (or a first visit): its own name, its own budget.
                entry.clear()
                entry["id"] = target["id"]
            if seed and not entry.get("candidate"):
                entry["candidate"] = seed
            if entry.get("candidate"):
                entry["state"] = "pending"
                p.ledger.save(flow_id, record)
                return group, target, "apply"
            if entry.get("lease_until", 0) > time.time():
                return group, target, "busy"
            entry.update(state="pending", ticket=ticket, lease_until=time.time() + FIRST_NAMES_LEASE_SEC)
            p.ledger.save(flow_id, record)
            return group, target, "generate"

        group, target, step = p.guard(flow_id, claim)
        if step in ("missing", "done", "skipped"):
            return step
        if step in ("busy", "unchanged"):
            logger.info("flow naming skipped reason=%s flow=%s target=%s", step, flow_id, kind)
            return "unchanged"
        if step == "generate":
            title = _generate_candidate(flow_id, group, provider_id, kind, target, message,
                                        chat_doc_id, str((user or {}).get("user_id") or ""), allow_fallback)

            def remember():
                record = p.ledger.load(flow_id)
                entry = slot_of(record)
                if entry.get("ticket") != ticket:
                    return None
                entry["lease_until"] = 0
                if title and not entry.get("candidate"):
                    entry["candidate"] = title
                p.ledger.save(flow_id, record)
                return entry.get("candidate")
            if not p.guard(flow_id, remember):
                return "unchanged"

        def save_one():
            record = p.ledger.load(flow_id)
            entry = slot_of(record)
            if entry.get("state") == "done":
                logger.info("flow naming skipped reason=target_not_pending flow=%s target=%s state=%s",
                            flow_id, kind, "done")
                return "done"
            _, now = live()
            if (now is None or now["id"] != target["id"] or now["title"] != target["title"]
                    or not p.eligible(kind, now) or not entry.get("candidate")):
                entry["state"] = "skipped"
                logger.info("flow naming skipped reason=target_changed_or_ineligible flow=%s target=%s", flow_id, kind)
                p.ledger.save(flow_id, record)
                return "skipped"
            p.apply(flow_id, kind, now, entry["candidate"], str((user or {}).get("user_id") or ""))
            entry["state"] = "done"
            p.ledger.save(flow_id, record)
            return "changed"
        try:
            return p.guard(flow_id, save_one)
        except Exception as exc:
            # Independent transactions: one failed target never rolls back another.
            logger.warning("flow_auto_title save flow=%s target=%s exception_type=%s",
                           flow_id, kind, type(exc).__name__)
            return "pending"
    except Exception as exc:
        def release():
            record = p.ledger.load(flow_id)
            entry = slot_of(record)
            if entry.get("ticket") == ticket:
                entry["lease_until"] = 0
                p.ledger.save(flow_id, record)
        try:
            p.guard(flow_id, release)
        except Exception:
            pass
        logger.warning("flow_auto_title flow=%s target=%s exception_type=%s", flow_id, kind, type(exc).__name__)
        return "failed"


def _generate_candidate(flow_id, group, provider_id, kind, target, message, chat_doc_id,
                        user_id, allow_fallback) -> Optional[str]:
    """A candidate from the target's own content, named by the configured naming provider
    (``complete_naming``: off, the executor or an explicit provider, remote agents included)."""
    start_document = ""
    if kind == "chat":
        text = _first_user_message(chat_doc_id) or ""
    elif kind == "flow":
        chat_message = (_first_user_message(chat_doc_id) if chat_doc_id else None) or ""
        text = chat_message or str(message or "").strip() or _root_text(flow_id, provider_id)
        if chat_message.strip():
            # The chat's first message alone may name nothing; the start document can.
            start_document = _root_text(flow_id, provider_id)
    else:
        text = _root_text(flow_id, provider_id)
    if not any(ch.isalnum() for ch in text):
        logger.info("flow naming skipped reason=empty_message flow=%s target=%s", flow_id, kind)
        return None
    # The card shows its document: without a model answer, the document's own first line.
    heading = heading_title("root", text, target.get("doc_type")) if kind == "root" else None
    if not allow_fallback:
        return heading
    answer, reason = legacy_adapter.complete_naming(group["project_id"], provider_id,
                                                    build_name_prompt(kind, text, start_document), user_id,
                                                    timeout=TIMEOUT_SEC, max_tokens=MAX_TOKENS)
    title = clean_flow_title(answer)
    if not title:
        logger.warning("flow_auto_title no_title flow=%s target=%s reason=%s", flow_id, kind, _safe_reason(reason))
    return title or heading


# Bounded background entry point: no DB lock/model call on the sending thread.
# A full pool refuses best-effort naming immediately; a later trigger can retry.
from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
_first_names_pool = _ThreadPoolExecutor(max_workers=2, thread_name_prefix="flow-names")
_first_names_slots = _threading.BoundedSemaphore(8)


def schedule_flow_names(flow_id: str, user: dict, provider_id: str, message: str = "",
                        chat_doc_id: Optional[str] = None) -> bool:
    """Queue after caller commit; False on saturation/shutdown, never blocks sending."""
    if not _first_names_slots.acquire(blocking=False):
        logger.info("flow naming skipped reason=pool_full flow=%s", flow_id)
        return False
    try:
        future = _first_names_pool.submit(generate_flow_names, flow_id, dict(user), provider_id, message, chat_doc_id)
        future.add_done_callback(lambda _: _first_names_slots.release())
        return True
    except Exception as exc:
        _first_names_slots.release()
        logger.warning("flow naming skipped reason=pool_unavailable flow=%s exception_type=%s", flow_id, type(exc).__name__)
        return False

