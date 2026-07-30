"""Shared human/AI append service for conversation turns."""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from modules.flow_gate import conversation as legacy_conversation
from modules.flow_gate import process_service
from modules.flow_gate.db import ai_providers
from modules.flow_gate.db import conversation_turns as turn_store
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.documents import document_service
from modules.flow_gate.storage import paths as storage_paths

TURN_BODY_BYTE_MAX = 65_536
IDEMPOTENCY_KEY_LEN_MIN = 8
IDEMPOTENCY_KEY_LEN_MAX = 200
BODY_HASH_ALGO = "sha256"
SEQ_RETRY_MAX = 5
SEQ_RETRY_BACKOFF_MS = (10, 20, 30, 40, 50)
MIGRATION_BATCH_TURNS = 200
MIGRATION_MAX_TURNS_PER_DOC = 5_000
CONVERSATION_TYPE_CODES = {"CH"}

_log = logging.getLogger(__name__)


@dataclass
class ConversationTurnError(Exception):
    status_code: int
    message: str

    def __str__(self) -> str:
        return self.message


def normalize_body(raw: str) -> str:
    value = unicodedata.normalize("NFC", raw or "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.split("\n"))
    return value.strip()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_input(body_raw: str, idempotency_key: str) -> tuple[str, str, str, str]:
    body = normalize_body(body_raw)
    if not body:
        raise ConversationTurnError(422, "Turn body must not be empty.")
    if len(body.encode("utf-8")) > TURN_BODY_BYTE_MAX:
        raise ConversationTurnError(422, "Turn body exceeds the size limit.")
    key = (idempotency_key or "").strip()
    if not IDEMPOTENCY_KEY_LEN_MIN <= len(key) <= IDEMPOTENCY_KEY_LEN_MAX:
        raise ConversationTurnError(422, "idempotency_key is required (8..200 chars).")
    return body, key, _sha256(body), _sha256(key)


def _provider_row(project_id: Optional[str], provider_id: str) -> Optional[dict]:
    row = ai_providers.get_row(project_id, provider_id) if project_id else None
    return row or ai_providers.get_row(None, provider_id)


def resolve_actor(actor: dict[str, Any], display_name_hint: Optional[str] = None) -> dict:
    kind = actor.get("kind")
    if kind == "session":
        user_id = str(actor.get("user_id") or "")
        if not user_id:
            raise ConversationTurnError(401, "Invalid session actor.")
        return {
            "speaker": "user",
            "participant_key": turn_store.compose_participant_key("user", user_id),
            "display_name": actor.get("user_name") or actor.get("username") or user_id,
            "locale": actor.get("locale") if actor.get("locale") in {"ko", "en", "ja"} else "ko",
            "source_run_id": None,
        }
    if kind == "worker":
        token = actor.get("token") or {}
        token_id = str(token.get("token_id") or "")
        provider_id = token.get("provider_id")
        if provider_id:
            participant = turn_store.compose_participant_key("provider", str(provider_id))
            provider = _provider_row(token.get("project"), str(provider_id))
            name = (provider or {}).get("name") or str(provider_id)
        else:
            participant = turn_store.compose_participant_key("provider", f"unbound:{token_id}")
            name = (display_name_hint or "").strip() or token_id
        return {
            "speaker": "ai",
            "participant_key": participant,
            "display_name": name,
            "locale": None,
            "source_run_id": token.get("ai_run_id"),
        }
    raise ConversationTurnError(401, "Invalid conversation actor.")


def _turn_wire(row: dict) -> dict:
    return {
        "seq": int(row["seq"]),
        "speaker": row["speaker"],
        "participant_key": row["participant_key"],
        "display_name": row.get("display_name"),
        "locale": row.get("locale"),
        "body": row["body"],
        "based_on_seq": int(row.get("based_on_seq") or 0),
        "stale_since_seq": (
            int(row["stale_since_seq"]) if row.get("stale_since_seq") is not None else None
        ),
        "source_run_id": row.get("source_run_id"),
        "created_at": row["created_at"],
    }


def turn_wire(row: dict) -> dict:
    """Public alias — the turn object of P0003 §0-2, built in exactly one place.

    Both HTTP families and the SSE payload must serialize a turn identically (P0003
    §0-1); the query adapter and the event publisher call this rather than each
    growing their own copy.
    """
    return _turn_wire(row)


def participant_wire(row: dict) -> dict:
    """The participant object of P0003 §0-4, built from a stored row alone.

    `last_viewed_seq` stays out of the wire on purpose: L0004 §2-8 splits the cursor
    into two columns but keeps the schema P0003 defined.
    """
    return {
        "participant_key": row["participant_key"],
        "kind": row.get("kind"),
        "display_name": row.get("display_name"),
        "first_seen_seq": int(row.get("first_seen_seq") or 0),
        "last_read_seq": int(row.get("last_read_seq") or 0),
        "last_written_seq": int(row.get("last_written_seq") or 0),
        "last_seen_at": row.get("last_seen_at"),
    }


def _participant_wire(row: Optional[dict], resolved: dict) -> dict:
    row = row or {}
    return {
        "participant_key": resolved["participant_key"],
        "kind": resolved["speaker"],
        "display_name": row.get("display_name") or resolved.get("display_name"),
        "first_seen_seq": int(row.get("first_seen_seq") or 0),
        "last_read_seq": int(row.get("last_read_seq") or 0),
        "last_written_seq": int(row.get("last_written_seq") or 0),
        "last_seen_at": row.get("last_seen_at"),
    }


def _result(doc_id: str, row: dict, resolved: dict, replayed: bool) -> dict:
    participant = turn_store.get_participant(doc_id, resolved["participant_key"])
    return {
        "ok": True,
        "doc_id": doc_id,
        "replayed": replayed,
        "head_seq": turn_store.current_head_seq(doc_id),
        "turn": _turn_wire(row),
        "me": _participant_wire(participant, resolved),
    }


def _replay_or_conflict(
    doc_id: str, existing: dict, body_hash: str, resolved: dict
) -> dict:
    if existing.get("body_hash") != body_hash:
        raise ConversationTurnError(409, "idempotency_key already used with a different body.")
    if existing.get("participant_key") != resolved["participant_key"]:
        raise ConversationTurnError(409, "idempotency_key already used by another participant.")
    return _result(doc_id, existing, resolved, True)


def _is_unique_violation(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "unique" in str(exc).lower()
    code = getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)
    if code == "23505":
        return True
    args = getattr(exc, "args", ())
    return bool(args and (args[0] == 1062 or "duplicate" in str(exc).lower()))


def _document_path(doc: dict) -> Optional[Path]:
    raw = (doc.get("file_path") or "").strip()
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    resolved = storage_paths.resolve_storage_path(
        raw, doc.get("project_id"), branch=(doc.get("branch") or "main")
    )
    return resolved


def _legacy_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFC", value).lower()).strip("-")
    return slug or "unknown"


def _legacy_participant(doc: dict, turn: dict) -> str:
    if turn.get("speaker") == "user":
        return turn_store.compose_participant_key("user", str(doc.get("owner_id") or "legacy"))
    provider_name = (turn.get("provider") or "").strip()
    if provider_name:
        for scope in (doc.get("project_id"), None):
            for provider in ai_providers.list_scope(scope):
                if (provider.get("name") or "").strip().casefold() == provider_name.casefold():
                    return turn_store.compose_participant_key("provider", provider["provider_id"])
        return turn_store.compose_participant_key("provider", f"legacy:{_legacy_slug(provider_name)}")
    return turn_store.compose_participant_key("provider", "legacy:unknown")


def _valid_timestamp(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    return fallback


def migrate_conversation(doc_id: str) -> bool:
    """Idempotently migrate one legacy CH markdown body into turn rows."""
    if turn_store.migration_state(doc_id) == "migrated":
        return True
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise ConversationTurnError(404, f"Document not found: {doc_id}")
    if (doc.get("type_code") or "").upper() not in CONVERSATION_TYPE_CODES:
        raise ConversationTurnError(400, "Not a conversation document.")
    owner = f"migration:{uuid.uuid4().hex}"
    if not turn_store.acquire_migration_lock(doc_id, owner):
        return False
    try:
        turn_store.reset_migration_data(doc_id, owner)
        path = _document_path(doc)
        content = path.read_text(encoding="utf-8") if path and path.is_file() else ""
        parsed = legacy_conversation.parse_conversation(content)
        turns = parsed["turns"]
        if len(turns) > MIGRATION_MAX_TURNS_PER_DOC:
            raise ValueError(f"conversation has more than {MIGRATION_MAX_TURNS_PER_DOC} turns")
        fallback_ts = doc.get("created_at") or now_iso()
        for start in range(0, len(turns), MIGRATION_BATCH_TURNS):
            with get_store().transaction():
                for offset, legacy in enumerate(turns[start:start + MIGRATION_BATCH_TURNS], start=start + 1):
                    speaker = legacy.get("speaker") if legacy.get("speaker") in {"user", "ai"} else "user"
                    body = normalize_body(legacy.get("body") or "")
                    key = f"migrate:{doc_id}:{offset}"
                    turn_store.insert_migrated_turn(
                        doc_id=doc_id,
                        seq=offset,
                        speaker=speaker,
                        participant_key=_legacy_participant(doc, legacy),
                        display_name=legacy.get("provider") if speaker == "ai" else None,
                        locale=legacy.get("locale") if speaker == "user" else None,
                        body=body,
                        body_hash=_sha256(body),
                        based_on_seq=offset - 1,
                        idempotency_key=key,
                        idempotency_hash=_sha256(key),
                        created_at=_valid_timestamp(legacy.get("ts"), fallback_ts),
                    )
        with get_store().transaction():
            turn_store.rebuild_participants(doc_id)
            turn_store.mark_migrated(doc_id, owner, parsed["intro"], len(turns))
        return turn_store.migration_state(doc_id) == "migrated"
    except Exception as exc:
        try:
            turn_store.mark_failed(doc_id, owner, str(exc))
        except Exception:
            _log.exception("conversation migration cleanup failed for %s", doc_id)
        return False


def _validate_document_for_append(doc_id: str) -> dict:
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise ConversationTurnError(404, f"Document not found: {doc_id}")
    if (doc.get("type_code") or "").upper() not in CONVERSATION_TYPE_CODES:
        raise ConversationTurnError(400, "Not a conversation document.")
    if process_service.is_group_disposed(doc.get("group_id")):
        raise ConversationTurnError(409, "Modification not allowed: the group has been disposed.")
    final_approved = document_service.is_final_approved(doc)
    if not document_service.is_document_editable(doc, final_approved=final_approved):
        raise ConversationTurnError(422, "Conversation is closed for new turns.")
    state = turn_store.migration_state(doc_id)
    if state == "failed":
        raise ConversationTurnError(409, "Conversation migration failed; the document is read-only.")
    if state != "migrated" and not migrate_conversation(doc_id):
        raise ConversationTurnError(409, "Conversation migration is not complete.")
    return doc


def replay_turn(
    *, doc_id: str, actor: dict[str, Any], body_raw: str,
    idempotency_key: str, display_name_hint: Optional[str] = None,
) -> Optional[dict]:
    """Replay a recorded request without requiring an unused worker token."""
    body, _key, body_hash, idempotency_hash = _validate_input(body_raw, idempotency_key)
    del body
    doc = document_service.get_document(doc_id)
    if doc is None:
        raise ConversationTurnError(404, f"Document not found: {doc_id}")
    if (doc.get("type_code") or "").upper() not in CONVERSATION_TYPE_CODES:
        raise ConversationTurnError(400, "Not a conversation document.")
    resolved = resolve_actor(actor, display_name_hint)
    existing = turn_store.get_turn_by_idempotency_hash(doc_id, idempotency_hash)
    return None if existing is None else _replay_or_conflict(doc_id, existing, body_hash, resolved)


def append_turn(
    *,
    doc_id: str,
    actor: dict[str, Any],
    body_raw: str,
    idempotency_key: str,
    based_on_seq: Optional[int] = None,
    display_name_hint: Optional[str] = None,
    after_commit: Optional[Callable[[dict], None]] = None,
) -> dict:
    """Append one turn for either a session user or a token-bound AI worker."""
    body, key, body_hash, idempotency_hash = _validate_input(body_raw, idempotency_key)
    doc = _validate_document_for_append(doc_id)
    resolved = resolve_actor(actor, display_name_hint)

    # Cheap replay path.  The unique constraints remain the authority for the race
    # where two requests both observe absence below.
    existing = turn_store.get_turn_by_idempotency_hash(doc_id, idempotency_hash)
    if existing is not None:
        return _replay_or_conflict(doc_id, existing, body_hash, resolved)

    last_exc: Optional[BaseException] = None
    for attempt in range(SEQ_RETRY_MAX + 1):
        try:
            with get_store().transaction():
                based = (
                    int(based_on_seq)
                    if based_on_seq is not None
                    else turn_store.get_last_read_seq(doc_id, resolved["participant_key"])
                )
                if based < 0:
                    raise ConversationTurnError(422, "based_on_seq must be >= 0.")
                row = turn_store.insert_turn_with_next_seq(
                    doc_id=doc_id,
                    speaker=resolved["speaker"],
                    participant_key=resolved["participant_key"],
                    display_name=resolved["display_name"],
                    locale=resolved["locale"],
                    body=body,
                    body_hash=body_hash,
                    based_on_seq=based,
                    source_run_id=resolved["source_run_id"],
                    idempotency_key=key,
                    idempotency_hash=idempotency_hash,
                    created_at=now_iso(),
                )
                assigned = int(row["seq"])
                previous_head = assigned - 1
                if based > previous_head:
                    raise ConversationTurnError(
                        422, f"based_on_seq {based} is ahead of head_seq {previous_head}."
                    )
                stale = turn_store.compute_stale_since(
                    doc_id, based, assigned, resolved["participant_key"]
                )
                turn_store.set_stale_since(doc_id, assigned, stale)
                participant = turn_store.touch_participant(
                    doc_id=doc_id,
                    participant_key=resolved["participant_key"],
                    kind=resolved["speaker"],
                    display_name=resolved["display_name"],
                    written_seq=assigned,
                    read_upto=based,
                    seen_at=row["created_at"],
                )
                row["stale_since_seq"] = stale
            result = {
                "ok": True,
                "doc_id": doc_id,
                "replayed": False,
                "head_seq": turn_store.current_head_seq(doc_id),
                "turn": _turn_wire(row),
                "me": _participant_wire(participant, resolved),
            }
            if actor.get("kind") == "worker":
                try:
                    from modules.flow_gate.services import token_service
                    token = actor.get("token") or {}
                    token_service.consume(token["token_id"], token["project"], doc_id)
                except Exception:
                    _log.exception("conversation worker token consumption failed after commit")
            # Live delivery of this one turn (T2 / L0004 §2-11).  Both adapters go
            # through here, so a screen sees a worker's reply and a human's message
            # by the same route.  A replay never reaches this line — re-broadcasting
            # an already-delivered turn is itself the side effect idempotency forbids.
            from modules.flow_gate.services import conversation_events
            conversation_events.broadcast_turn_appended(doc, result)
            if after_commit is not None:
                try:
                    after_commit(result)
                except Exception:
                    _log.exception("conversation post-commit hook failed")
            return result
        except ConversationTurnError:
            raise
        except Exception as exc:
            last_exc = exc
            if not _is_unique_violation(exc):
                raise
            existing = turn_store.get_turn_by_idempotency_hash(doc_id, idempotency_hash)
            if existing is not None:
                return _replay_or_conflict(doc_id, existing, body_hash, resolved)
            if attempt >= SEQ_RETRY_MAX:
                break
            time.sleep(SEQ_RETRY_BACKOFF_MS[attempt] / 1000.0)
    raise ConversationTurnError(409, "Could not allocate a turn number; retry.") from last_exc