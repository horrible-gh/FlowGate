"""flowgate.v02gui.0009 (chat 0002-CH): language-neutral encoding guard for chat turns.

The question-mark heuristic rejected ordinary punctuation ("??") while missing partial
loss and mis-decoding in other languages. These tests pin the replacement:
- a per-token probe line (supplementary-plane glyph + nonce) proves the path; a matched
  probe skips every corruption heuristic and is stripped before storage, a damaged one
  rejects with the transport cause even when force_encoding_reason is given;
- browser-session and system turns are never put through the heuristics;
- without a probe, U+FFFD and reversible mojibake reject regardless of language.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from modules.flow_gate.services import conversation_turn_service as service
from modules.flow_gate.services import encoding_probe

TOKEN_ID = "tok_20261113_000099"
WORKER = {"kind": "worker", "token": {"token_id": TOKEN_ID, "issued_to": "worker-0009"}}
SESSION = {"kind": "session", "user_id": "u-0009", "user_name": "admin", "locale": "ko"}
SYSTEM = {"kind": "system", "display_name": "FlowGate", "run_id": "run-0009"}
DOC_ID = "flowgate.v02gui.0009.0002-CH"
PROBE = encoding_probe.probe_for(TOKEN_ID)

QUESTION_MARKS = "왜 안되지??\n?? ??? ?\n????"
MULTILINGUAL = [
    "한글 문장입니다. 왜 안되지??",
    "日本語のテストです。駅まで歩きます",
    "简体中文测试，这是一个句子。",
    "繁體中文測試，這是一個句子。",
    "Привет, как дела? Это тест.",
    "Café, naïve, résumé, déjà vu, ß, ü",
    "😀 🎉 👍 𠮷野家",
    "αβγ Ελληνικά — עברית שלום — العربية مرحبا — ไทย สวัสดี — हिन्दी",
    "→ ← • … — “quotes” © ® ™ € £ ¥ ✓ ✗ ★",
]


def _sentinel_after_guard(monkeypatch):
    sentinel = service.ConversationTurnError(499, "reached-validate-document")

    def _raise(_doc_id):
        raise sentinel

    monkeypatch.setattr(service, "_validate_document_for_append", _raise)
    return sentinel


def _damaged(text: str, codec: str) -> str:
    return text.encode(codec, errors="replace").decode(codec)


# ── encoding_probe ───────────────────────────────────────────────────────────────

def test_probe_is_per_token_and_carries_a_supplementary_plane_glyph():
    assert PROBE.startswith(encoding_probe.PROBE_PREFIX)
    assert PROBE.endswith("\U00020BB7")
    assert PROBE == encoding_probe.probe_for(TOKEN_ID)
    assert PROBE != encoding_probe.probe_for("tok_20261113_000100")
    assert encoding_probe.probe_for(None) is None
    assert encoding_probe.probe_for("") is None


@pytest.mark.parametrize(
    "codec", ["ascii", "latin-1", "cp1252", "cp1251", "cp949", "cp932", "cp936", "big5", "shift_jis"]
)
def test_every_legacy_codepage_breaks_the_probe(codec):
    damaged = _damaged(PROBE, codec)
    _text, status, received = encoding_probe.split_probe(f"body\n{damaged}", PROBE)
    assert status == encoding_probe.PROBE_BROKEN
    assert received == damaged


def test_split_probe_matches_and_strips_the_last_line_only():
    text, status, _ = encoding_probe.split_probe(f"line one??\n\nline two\r\n{PROBE}\r\n\n", PROBE)
    assert status == encoding_probe.PROBE_MATCHED
    assert text.rstrip("\r") == "line one??\n\nline two"
    # a probe that is not the last non-blank line is just text
    body = f"{PROBE}\nmore text"
    assert encoding_probe.split_probe(body, PROBE) == (body, encoding_probe.PROBE_ABSENT, None)
    assert encoding_probe.split_probe("plain", None)[1] == encoding_probe.PROBE_ABSENT


def test_describe_received_is_ascii_only():
    rendered = encoding_probe.describe_received(PROBE)
    assert rendered.isascii()
    assert "\\U00020bb7" in rendered


def test_hard_signals_do_not_fire_on_clean_text_in_any_language():
    for line in MULTILINGUAL:
        assert encoding_probe.hard_corruption_signal(line) is None, line
    assert encoding_probe.hard_corruption_signal(QUESTION_MARKS) is None


@pytest.mark.parametrize(
    "text,codec",
    [
        ("한글 문장입니다", "cp1252"),
        ("日本語のテストです", "cp949"),
        ("简体中文测试句子", "cp932"),
        ("Привет мир", "cp1252"),
        ("Café déjà vu", "latin-1"),
        ("emoji 😀 test", "cp1252"),
    ],
)
def test_hard_signals_catch_mis_decoded_utf8(text, codec):
    mangled = text.encode("utf-8").decode(codec, errors="replace")
    assert encoding_probe.hard_corruption_signal(mangled) in {
        encoding_probe.SIGNAL_REPLACEMENT_CHAR,
        encoding_probe.SIGNAL_MOJIBAKE,
    }


# ── _encoding_violation ──────────────────────────────────────────────────────────

def _verdict(body_raw, actor=WORKER, **kwargs):
    expected = service._expected_probe(actor)
    checked, status, received = encoding_probe.split_probe(body_raw, expected)
    return service._encoding_violation(
        body_raw=body_raw,
        body_sha256=kwargs.get("body_sha256"),
        body_chars=kwargs.get("body_chars"),
        force_encoding_reason=kwargs.get("force_encoding_reason"),
        locale=kwargs.get("locale", "ko"),
        probe_status=status,
        probe_received=received,
        checked_body=checked,
        trusted_transport=actor.get("kind") != "worker",
    )


def test_matched_probe_lets_question_marks_through():
    assert _verdict(f"{QUESTION_MARKS}\n{PROBE}") is None


def test_matched_probe_lets_a_literal_replacement_char_through():
    assert _verdict(f"U+FFFD looks like �\n{PROBE}") is None


@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_broken_probe_rejects_even_with_force_reason(locale):
    damaged = _damaged(PROBE, "cp949")
    msg = _verdict(
        f"clean body\n{damaged}", locale=locale,
        force_encoding_reason="worker confirms intentional",
    )
    assert msg is not None
    assert "fg-probe:" in msg
    if locale != "ko":
        assert not any("가" <= ch <= "힣" for ch in msg)


def test_worker_without_probe_keeps_the_legacy_heuristic():
    assert _verdict("?? ??? ?") is not None
    assert _verdict("정상적인 한글 본문입니다") is None


def test_worker_fingerprint_without_probe_still_gets_hard_signals():
    body = "한글 문장입니다".encode("utf-8").decode("cp1252", errors="replace")
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert _verdict(body, body_sha256=sha, body_chars=len(body)) is not None
    clean = "물음표 두 개?? 괜찮습니다"
    clean_sha = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    assert _verdict(clean, body_sha256=clean_sha, body_chars=len(clean)) is None


def test_fingerprint_is_checked_against_the_file_including_the_probe():
    raw = f"본문\n{PROBE}"
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert _verdict(raw, body_sha256=sha, body_chars=len(raw)) is None
    stripped_sha = hashlib.sha256("본문".encode("utf-8")).hexdigest()
    assert _verdict(raw, body_sha256=stripped_sha) is not None


@pytest.mark.parametrize("actor", [SESSION, SYSTEM])
def test_browser_session_and_system_turns_skip_the_heuristics(actor):
    assert _verdict(QUESTION_MARKS, actor=actor) is None
    assert _verdict("문장 � 포함", actor=actor) is None


# ── append_turn / dry_run_append / replay_turn ───────────────────────────────────

def test_session_user_question_marks_reach_past_the_guard(monkeypatch):
    sentinel = _sentinel_after_guard(monkeypatch)
    with pytest.raises(service.ConversationTurnError) as exc_info:
        service.append_turn(
            doc_id=DOC_ID, actor=SESSION, body_raw="왜 안되지?? ?? ???",
            idempotency_key="idem-key-0009-a",
        )
    assert exc_info.value is sentinel


def test_worker_probe_is_stripped_before_validation(monkeypatch):
    seen = []
    original = service._validate_input

    def _spy(body_raw, idempotency_key):
        seen.append(body_raw)
        return original(body_raw, idempotency_key)

    monkeypatch.setattr(service, "_validate_input", _spy)
    sentinel = _sentinel_after_guard(monkeypatch)
    with pytest.raises(service.ConversationTurnError) as exc_info:
        service.append_turn(
            doc_id=DOC_ID, actor=WORKER, body_raw=f"?? ??? ?\n{PROBE}\n",
            idempotency_key=TOKEN_ID,
        )
    assert exc_info.value is sentinel
    assert seen and PROBE not in seen[0]
    assert seen[0].strip() == "?? ??? ?"


def test_worker_broken_probe_rejects_before_validating_document(monkeypatch):
    from unittest.mock import MagicMock

    validate_doc = MagicMock()
    monkeypatch.setattr(service, "_validate_document_for_append", validate_doc)
    with pytest.raises(service.ConversationTurnError) as exc_info:
        service.append_turn(
            doc_id=DOC_ID, actor=WORKER,
            body_raw=f"body\n{_damaged(PROBE, 'cp1252')}", idempotency_key=TOKEN_ID,
        )
    assert exc_info.value.status_code == 422
    validate_doc.assert_not_called()


def test_dry_run_reports_the_probe_status(monkeypatch):
    from modules.flow_gate.services import token_service

    monkeypatch.setattr(service, "_validate_document_for_append", lambda _doc_id: {"doc_id": DOC_ID})
    monkeypatch.setattr(token_service, "increment_dry_run", lambda _tok: None)
    token_rec = {"token_id": TOKEN_ID, "dry_run_count": 0}
    ok = service.dry_run_append(
        doc_id=DOC_ID, actor=WORKER, body_raw=f"?? ???\n{PROBE}",
        idempotency_key=TOKEN_ID, token_rec=token_rec,
    )
    assert ok["corrupted"] is False and ok["encoding_probe"] == "matched"
    bad = service.dry_run_append(
        doc_id=DOC_ID, actor=WORKER, body_raw=f"text\n{_damaged(PROBE, 'cp932')}",
        idempotency_key=TOKEN_ID, token_rec=token_rec,
    )
    assert bad["corrupted"] is True and bad["encoding_probe"] == "broken"


def test_replay_hashes_the_body_without_the_probe(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        service.document_service, "get_document",
        lambda _doc_id: {"doc_id": DOC_ID, "type_code": "CH"},
    )
    monkeypatch.setattr(service, "resolve_actor", lambda actor, hint=None: {"participant_key": "p"})

    def _lookup(_doc_id, idempotency_hash):
        captured["hash"] = idempotency_hash
        return None

    monkeypatch.setattr(service.turn_store, "get_turn_by_idempotency_hash", _lookup)
    seen = []
    original = service._validate_input

    def _spy(body_raw, idempotency_key):
        seen.append(body_raw)
        return original(body_raw, idempotency_key)

    monkeypatch.setattr(service, "_validate_input", _spy)
    assert service.replay_turn(
        doc_id=DOC_ID, actor=WORKER, body_raw=f"reply\n{PROBE}", idempotency_key=TOKEN_ID,
    ) is None
    assert seen == ["reply"]


# ── prompt and API worker ────────────────────────────────────────────────────────

def test_chat_mention_hands_out_this_tokens_probe(monkeypatch):
    from modules.flow_gate.services import invoke_mention_service as ims

    monkeypatch.setattr(ims, "_chat_lookup_sections", lambda **_kwargs: [])
    monkeypatch.setattr(ims.conversation_turns, "current_head_seq", lambda doc_id: 0)
    text = ims.build_conversation_mention(
        doc_id=DOC_ID, project="flowgate", module="v02gui", group_name="flowgate.v02gui.0009",
        raw_token="raw", token_id=TOKEN_ID, api_base_url="http://example.test/api/v1",
    )
    assert f"\n{PROBE}\n" in text
    assert "ASCII-only JSON" in text


def test_api_worker_reply_carries_the_probe(monkeypatch):
    from modules.flow_gate.services.ai_invoke import worker

    sent = {}

    class _Resp:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def _urlopen(req, timeout=None):
        sent["data"] = req.data
        return _Resp()

    monkeypatch.setattr(worker.provider_api, "_resolve_transport_api_base", lambda run: "http://x/api/v1")
    monkeypatch.setattr(worker.urllib.request, "urlopen", _urlopen)
    run = {"token_id": TOKEN_ID, "doc_ref": DOC_ID, "transport_api_base": "http://x/api/v1"}
    status, _resp = worker._conversation_turn_register(run, "raw", {"body": "답변입니다??"})
    assert status == 201
    assert sent["data"].isascii()
    payload = json.loads(sent["data"].decode("ascii"))
    assert payload["body"] == f"답변입니다??\n{PROBE}"
    text, probe_status, _ = encoding_probe.split_probe(payload["body"], PROBE)
    assert probe_status == "matched" and text == "답변입니다??"
