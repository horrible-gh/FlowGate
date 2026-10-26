"""0391 T0005 §5-4/§6 -- chat turn corrupted-body guard + body-fingerprint match.

The chat real-registration path (append_turn) had NO corruption check at all before
this change -- only the dry-run branch did, and the chat mention never told the
worker dry-run existed (NR0004). This suite exercises append_turn directly (per
T0005 §9-8: test_conversation_dry_run_0360.py fakes append_turn wholesale for its
own route-level tests, so a regression test for the guard belongs here instead),
plus dry_run_append parity and the pre-normalize fingerprint contract.
"""
from __future__ import annotations

import hashlib

import pytest

from modules.flow_gate.services import conversation_turn_service as service

CORRUPT = "??? ?? ? 0082(??3) ?? ? ?? ??"
CLEAN_KO = "정상적인 한글 본문입니다"
ACTOR = {"kind": "worker", "token": {"token_id": "tok-chat-0391", "issued_to": "worker-0391"}}
DOC_ID = "flowgate.default.0391.0003-CH"


def _pass_through_document_guard(monkeypatch, *, status_code=499):
    """Let append_turn's encoding guard run for real, then short-circuit right after
    it (before any DB write) by making _validate_document_for_append raise a marker
    exception -- proves the guard did NOT block without mocking the whole DB layer."""
    sentinel = service.ConversationTurnError(status_code, "reached-validate-document")

    def _raise(_doc_id):
        raise sentinel

    monkeypatch.setattr(service, "_validate_document_for_append", _raise)
    return sentinel


# ── _encoding_violation unit tests ──────────────────────────────────────────────

def test_encoding_violation_flags_corrupted_body():
    msg = service._encoding_violation(
        body_raw=CORRUPT, body_sha256=None, body_chars=None, force_encoding_reason=None,
    )
    assert msg is not None


def test_encoding_violation_passes_clean_body():
    assert service._encoding_violation(
        body_raw=CLEAN_KO, body_sha256=None, body_chars=None, force_encoding_reason=None,
    ) is None


def test_encoding_violation_force_reason_bypasses_everything():
    assert service._encoding_violation(
        body_raw=CORRUPT, body_sha256=None, body_chars=None,
        force_encoding_reason="worker confirms intentional",
    ) is None


def test_encoding_violation_force_reason_too_short_is_not_a_bypass():
    assert service._encoding_violation(
        body_raw=CORRUPT, body_sha256=None, body_chars=None, force_encoding_reason="short",
    ) is not None


def test_encoding_violation_fingerprint_mismatch_rejects():
    msg = service._encoding_violation(
        body_raw=CLEAN_KO, body_sha256="0" * 64, body_chars=None, force_encoding_reason=None,
    )
    assert msg is not None and "지문" in msg


def test_encoding_violation_fingerprint_match_trusts_it_over_heuristic():
    """T0005 §6-4: a matching fingerprint proves transit integrity, not that the
    original text was clean -- if the sender hashed already-corrupted text, the
    server has no way to know. A matching fingerprint is trusted regardless."""
    digest = hashlib.sha256(CORRUPT.encode("utf-8")).hexdigest()
    assert service._encoding_violation(
        body_raw=CORRUPT, body_sha256=digest, body_chars=len(CORRUPT), force_encoding_reason=None,
    ) is None


def test_encoding_violation_char_count_mismatch_rejects():
    msg = service._encoding_violation(
        body_raw=CLEAN_KO, body_sha256=None, body_chars=99999, force_encoding_reason=None,
    )
    assert msg is not None


def test_encoding_violation_fingerprint_checked_against_raw_not_normalized_body():
    """T0005 §6-3: normalize_body() does NFC + newline + trailing-space changes the
    sender cannot reliably reproduce, so the fingerprint contract MUST be pre-normalize
    (body_raw), not post-normalize. A CRLF+trailing-space body whose fingerprint was
    computed on the raw bytes must match here even though normalize_body would alter it."""
    raw = "hello world  \r\nsecond line   \r\n"
    assert service.normalize_body(raw) != raw  # normalize_body does change this input
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert service._encoding_violation(
        body_raw=raw, body_sha256=digest, body_chars=len(raw), force_encoding_reason=None,
    ) is None
    # Fingerprint computed against the NORMALIZED text must NOT match body_raw's check.
    normalized = service.normalize_body(raw)
    wrong_digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    msg = service._encoding_violation(
        body_raw=raw, body_sha256=wrong_digest, body_chars=len(normalized), force_encoding_reason=None,
    )
    assert msg is not None


def test_encoding_violation_uses_line_based_check_not_whole_body_ratio():
    """Regression for the same NR0004 §4 dilution bug the document paths hit: a long
    chat turn with one corrupted line among clean filler must still be caught."""
    from modules.flow_gate.services import workflow_decision_service as wf_decision

    filler = "\n".join([f"line {i}: ordinary english filler text" for i in range(20)])
    body = filler + "\n" + CORRUPT + "\n" + filler
    assert not wf_decision._label_is_corrupted(body)  # whole-body ratio misses it
    msg = service._encoding_violation(
        body_raw=body, body_sha256=None, body_chars=None, force_encoding_reason=None,
    )
    assert msg is not None  # line-based check still catches it


# ── append_turn: reject before any side effect ──────────────────────────────────

def test_append_turn_rejects_corrupted_body_before_validating_document(monkeypatch):
    from unittest.mock import MagicMock

    validate_doc = MagicMock()
    monkeypatch.setattr(service, "_validate_document_for_append", validate_doc)

    with pytest.raises(service.ConversationTurnError) as exc_info:
        service.append_turn(
            doc_id=DOC_ID, actor=ACTOR, body_raw=CORRUPT, idempotency_key="idem-key-0391-a",
        )
    assert exc_info.value.status_code == 422
    validate_doc.assert_not_called()  # rejected before the (possibly write-causing) document check


def test_append_turn_rejects_fingerprint_mismatch_before_validating_document(monkeypatch):
    from unittest.mock import MagicMock

    validate_doc = MagicMock()
    monkeypatch.setattr(service, "_validate_document_for_append", validate_doc)

    with pytest.raises(service.ConversationTurnError) as exc_info:
        service.append_turn(
            doc_id=DOC_ID, actor=ACTOR, body_raw=CLEAN_KO, idempotency_key="idem-key-0391-b",
            body_sha256="0" * 64,
        )
    assert exc_info.value.status_code == 422
    validate_doc.assert_not_called()


def test_append_turn_clean_body_reaches_past_the_guard(monkeypatch):
    sentinel = _pass_through_document_guard(monkeypatch)
    with pytest.raises(service.ConversationTurnError) as exc_info:
        service.append_turn(
            doc_id=DOC_ID, actor=ACTOR, body_raw=CLEAN_KO, idempotency_key="idem-key-0391-c",
        )
    assert exc_info.value is sentinel  # guard did not block a clean body


def test_append_turn_force_encoding_reason_reaches_past_the_guard(monkeypatch):
    sentinel = _pass_through_document_guard(monkeypatch)
    with pytest.raises(service.ConversationTurnError) as exc_info:
        service.append_turn(
            doc_id=DOC_ID, actor=ACTOR, body_raw=CORRUPT, idempotency_key="idem-key-0391-d",
            force_encoding_reason="worker confirms intentional",
        )
    assert exc_info.value is sentinel


# ── dry_run_append: same verdict as append_turn, never inserts ──────────────────

def test_dry_run_append_reports_same_verdict_as_append_turn_would_raise(monkeypatch):
    from modules.flow_gate.services import token_service

    monkeypatch.setattr(service, "_validate_document_for_append", lambda _doc_id: {"doc_id": DOC_ID})
    monkeypatch.setattr(token_service, "increment_dry_run", lambda _tok: None)

    result = service.dry_run_append(
        doc_id=DOC_ID, actor=ACTOR, body_raw=CORRUPT, idempotency_key="idem-key-0391-e",
        token_rec={"token_id": "tok-chat-0391", "dry_run_count": 0},
    )
    assert result["corrupted"] is True

    with pytest.raises(service.ConversationTurnError):
        service.append_turn(
            doc_id=DOC_ID, actor=ACTOR, body_raw=CORRUPT, idempotency_key="idem-key-0391-f",
        )


def test_dry_run_append_clean_body_reports_not_corrupted(monkeypatch):
    from modules.flow_gate.services import token_service

    monkeypatch.setattr(service, "_validate_document_for_append", lambda _doc_id: {"doc_id": DOC_ID})
    monkeypatch.setattr(token_service, "increment_dry_run", lambda _tok: None)

    result = service.dry_run_append(
        doc_id=DOC_ID, actor=ACTOR, body_raw=CLEAN_KO, idempotency_key="idem-key-0391-g",
        token_rec={"token_id": "tok-chat-0391", "dry_run_count": 0},
    )
    assert result["corrupted"] is False
