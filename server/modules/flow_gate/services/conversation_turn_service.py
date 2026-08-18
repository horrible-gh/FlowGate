"""Shared human/AI append service for conversation turns."""
from __future__ import annotations

import hashlib
import logging
import os
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

# ── 전환기 가드: 낡은 전체 본문 제출 차단 (0344.0005-L §2-16) ────────────────────
# 0344.0008-TR 이 이 마무리를 시도했다가 반려된 뒤 방치돼 있었다(0432.0003-NR §4·§7-1).
# 두 진입점 — 워커 인박스 edit 과 세션 PATCH /documents/{doc_id}/content — 이 똑같은
# 문장을 돌려줘야 하므로 문구는 여기 한 곳에만 둔다. 봉투 모양은 계열마다 다르다
# (0344.0004-P §0-5: 세션은 HTTPException(detail), 워커는 _fail 의 error_message).
FULL_BODY_EDIT_MESSAGE_TEMPLATE = (
    "This conversation no longer accepts a full-body edit. "
    "Append one turn: POST /api/v1/conversation/{doc_id}/turn"
)

_log = logging.getLogger(__name__)


def full_body_edit_message(doc_id: str) -> str:
    """L §2-16 안내 문구. ``{doc_id}`` 는 실제 문서 ID 로 채운다 — 워커가 응답에서
    그대로 복사해 호출할 수 있는 주소여야 한다."""
    return FULL_BODY_EDIT_MESSAGE_TEMPLATE.format(doc_id=doc_id)


def is_full_body_edit_blocked(doc_type_code: Optional[str], doc_id: str) -> bool:
    """이관이 끝난 대화(CH)인가 — 전체 본문 교체를 거절해야 하는가.

    L §2-16 그대로: 대화 타입이고 ``migration_state == "migrated"`` 일 때만 참이다.
    이관되지 않은 대화(``pending`` / ``in_progress`` / ``failed``)에는 걸지 않는다 —
    아직 파일이 정본이기 때문이다.

    판정은 **읽기만** 한다. ``conversation_query_service._ensure_readable_rows()`` 같은
    지연 이관 함수를 부르면 판정이 부작용을 낳고 dry-run 이 DB 를 바꾼다.
    조회가 실패하면 열어 둔다(``_disposed_group_fail`` 과 같은 결) — 이 변경의 위험은
    덜 막는 쪽이 아니라 지금 돌아가는 대화를 끊는 쪽이다.
    """
    if (doc_type_code or "").upper() not in CONVERSATION_TYPE_CODES:
        return False
    if not doc_id:
        return False
    try:
        return turn_store.migration_state(doc_id) == "migrated"
    except Exception:  # noqa: BLE001 — fail open, same as the disposed-group guard
        _log.warning("migration_state lookup failed for %s; full-body edit allowed", doc_id)
        return False


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


# ── Corrupted-body guard + body-fingerprint match (0391 B0001 proposals 3+4, T0005 §5-4/§6) ──
# Shared by append_turn (raises) and dry_run_append (reports) so both give the exact
# same verdict for the same input — the T0005 §5-4 common rule ("a dry run and a real
# registration must always reach the same verdict"). Fingerprints are checked against body_raw, i.e.
# BEFORE normalize_body() — normalize_body's NFC/newline/trailing-space changes are not
# something the sender can reliably reproduce client-side, so pinning the contract to
# the original text is the only version of it that holds (T0005 §6-3).
_ENCODING_VIOLATION_STRINGS = {
    "ko": {
        "sha256_mismatch": "sha256 기대={expected} 실제={actual}",
        "chars_mismatch": "글자수 기대={expected} 실제={actual}",
        "chars_format_error": "body_chars 형식 오류: {value!r}",
        "fingerprint_violation": (
            "본문 지문이 어긋납니다: {mismatches}. 본문을 UTF-8 파일로 "
            "먼저 쓰고 그 파일에서 글자 수와 해시를 구해 다시 보내거나, "
            "force_encoding_reason에 사유(공백 제외 10자 이상)를 적어 다시 보내세요."
        ),
        "corrupted_body": (
            "본문이 깨진 글자(예: ??????)로 보입니다. 본문을 UTF-8 파일로 먼저 쓰고, 그 "
            "파일에서 글자 수와 해시(body_chars/body_sha256)를 구한 다음 다시 보내세요. "
            "정말 이대로 보내야 하면 force_encoding_reason에 사유(공백 제외 10자 이상)를 "
            "적어 다시 보내세요."
        ),
    },
    "en": {
        "sha256_mismatch": "expected sha256={expected} actual={actual}",
        "chars_mismatch": "expected char count={expected} actual={actual}",
        "chars_format_error": "body_chars has an invalid format: {value!r}",
        "fingerprint_violation": (
            "The body fingerprint does not match: {mismatches}. Write the body to a "
            "UTF-8 file first and compute the char count/hash from that file, or "
            "resend with force_encoding_reason (10+ non-whitespace characters)."
        ),
        "corrupted_body": (
            "The body looks like corrupted text (e.g. ??????). Write it to a UTF-8 "
            "file first, compute the char count/hash (body_chars/body_sha256) from "
            "that file, then resend. If you really must send it as-is, resend with "
            "force_encoding_reason (10+ non-whitespace characters)."
        ),
    },
    "ja": {
        "sha256_mismatch": "sha256 期待値={expected} 実際値={actual}",
        "chars_mismatch": "文字数 期待値={expected} 実際値={actual}",
        "chars_format_error": "body_chars の形式が不正です: {value!r}",
        "fingerprint_violation": (
            "本文の指紋が一致しません: {mismatches}。本文をUTF-8ファイルとして先に書き出し、"
            "そのファイルから文字数とハッシュを求めて送り直すか、force_encoding_reason に"
            "理由（空白を除き10文字以上）を書いて送り直してください。"
        ),
        "corrupted_body": (
            "本文が文字化け（例: ??????）しているようです。本文をUTF-8ファイルとして先に書き出し、"
            "そのファイルから文字数とハッシュ（body_chars/body_sha256）を求めてから送り直してください。"
            "どうしてもこのまま送る必要がある場合は、force_encoding_reason に理由"
            "（空白を除き10文字以上）を書いて送り直してください。"
        ),
    },
}


def _encoding_violation_string(key: str, locale: str, **kwargs) -> str:
    strings = _ENCODING_VIOLATION_STRINGS.get(locale) or _ENCODING_VIOLATION_STRINGS["ko"]
    return strings[key].format(**kwargs)


def _encoding_violation(
    *,
    body_raw: str,
    body_sha256: Optional[str],
    body_chars: Optional[int],
    force_encoding_reason: Optional[str],
    locale: str = "ko",
) -> Optional[str]:
    reason = (force_encoding_reason or "").strip()
    if len(reason.replace(" ", "")) >= 10:
        return None

    if body_sha256 or body_chars is not None:
        actual_sha256 = _sha256(body_raw)
        actual_chars = len(body_raw)
        mismatches = []
        if body_sha256 and str(body_sha256).strip().lower() != actual_sha256:
            mismatches.append(_encoding_violation_string(
                "sha256_mismatch", locale, expected=body_sha256, actual=actual_sha256
            ))
        if body_chars is not None:
            try:
                if int(body_chars) != actual_chars:
                    mismatches.append(_encoding_violation_string(
                        "chars_mismatch", locale, expected=body_chars, actual=actual_chars
                    ))
            except (TypeError, ValueError):
                mismatches.append(_encoding_violation_string(
                    "chars_format_error", locale, value=body_chars
                ))
        if mismatches:
            return _encoding_violation_string(
                "fingerprint_violation", locale, mismatches="; ".join(mismatches)
            )
        return None  # fingerprint matched — trust it, skip the question-mark heuristic

    from modules.flow_gate.services import workflow_decision_service

    if workflow_decision_service._text_is_corrupted(body_raw):
        return _encoding_violation_string("corrupted_body", locale)
    return None


def _log_force_encoding_reason(doc_id: str, actor: dict[str, Any], reason: str) -> None:
    """Best-effort audit trail (T0005 §5-6): conversation_turns has no meta column, so
    the bypass reason goes to db_events instead — never blocks the append it guards."""
    try:
        import json as _json

        from modules.flow_gate.db import workflow_events as _db_events

        doc = document_service.get_document(doc_id)
        actor_id = (
            actor.get("user_id")
            or (actor.get("token") or {}).get("issued_to")
            or "unknown"
        )
        _db_events.create({
            "event_type": "action_taken",
            "project_id": (doc or {}).get("project_id"),
            "group_id": (doc or {}).get("group_id"),
            "document_id": None,
            "actor_user_id": str(actor_id),
            "from_state": None,
            "to_state": None,
            "metadata": _json.dumps(
                {"action_code": "force_encoding_reason_used", "doc_id": doc_id, "reason": reason},
                ensure_ascii=False,
            ),
        })
    except Exception:
        _log.warning("force_encoding_reason event log failed (ignored)", exc_info=True)


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
    body_sha256: Optional[str] = None,
    body_chars: Optional[int] = None,
    force_encoding_reason: Optional[str] = None,
    locale: str = "ko",
) -> dict:
    """Append one turn for either a session user or a token-bound AI worker."""
    body, key, body_hash, idempotency_hash = _validate_input(body_raw, idempotency_key)
    # 0391 T0005 §5-4: reject a corrupted/fingerprint-mismatched body here — before any
    # side effect, including _validate_document_for_append's possible migration write.
    _violation = _encoding_violation(
        body_raw=body_raw,
        body_sha256=body_sha256,
        body_chars=body_chars,
        force_encoding_reason=force_encoding_reason,
        locale=locale,
    )
    if _violation is not None:
        raise ConversationTurnError(422, _violation)
    if (force_encoding_reason or "").strip():
        _log_force_encoding_reason(doc_id, actor, force_encoding_reason)
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


_DRY_RUN_MAX_DEFAULT = 5


def _dry_run_limit() -> int:
    """Per-token dry-run attempt limit, shared with inbox (NR0003 4-6: no new chat-only env var)."""
    try:
        return int(os.environ.get("FLOWGATE_INBOX_DRYRUN_MAX", _DRY_RUN_MAX_DEFAULT))
    except (ValueError, TypeError):
        return _DRY_RUN_MAX_DEFAULT


def dry_run_append(
    *,
    doc_id: str,
    actor: dict[str, Any],
    body_raw: str,
    idempotency_key: str,
    token_rec: dict,
    body_sha256: Optional[str] = None,
    body_chars: Optional[int] = None,
    force_encoding_reason: Optional[str] = None,
    locale: str = "ko",
) -> dict:
    """Validate-only counterpart to append_turn (T0004 / NR0003 3-3).

    Runs the same side-effect-free validation steps append_turn does -- input shape and
    document eligibility -- but never inserts a turn, consumes the token, or broadcasts.
    Mirrors inbox_routes._maybe_dry_run: the only side effect allowed is the per-token
    dry-run counter. 0391 T0005 §5-4: the encoding/fingerprint verdict is computed with
    the exact same _encoding_violation() append_turn uses, so a dry-run preview and the
    real submission never disagree.
    """
    del actor
    body, _key, _body_hash, _idempotency_hash = _validate_input(body_raw, idempotency_key)
    _validate_document_for_append(doc_id)

    limit = _dry_run_limit()
    cnt = int(token_rec.get("dry_run_count") or 0)
    if cnt >= limit:
        raise ConversationTurnError(
            429, f"Dry-run limit ({limit}) reached. Submit for real or request a new token."
        )

    from modules.flow_gate.services import token_service

    token_service.increment_dry_run(token_rec["token_id"])
    _violation = _encoding_violation(
        body_raw=body_raw,
        body_sha256=body_sha256,
        body_chars=body_chars,
        force_encoding_reason=force_encoding_reason,
        locale=locale,
    )
    corrupted = _violation is not None
    message = _violation or (
        "Dry-run OK. Submitting this payload will register it; nothing was registered by this check."
    )
    return {
        "ok": True,
        "dry_run": True,
        "corrupted": corrupted,
        "dry_run_count": cnt + 1,
        "dry_run_remaining": limit - (cnt + 1),
        "message": message,
    }
