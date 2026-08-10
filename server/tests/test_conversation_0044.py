"""T0044.0009 — conversation (CH) type: serializer/parser, registration.

Covers L0044.0008 §2 (type registration) and §6 (turn serialization/parsing/escaping).
Pure-unit + migration-row checks (no HTTP). The former §7 byte-cap carry-over
(should_carry_over/tail_turns/build_carryover_intro) was removed in group 0351 T5
(D0002 §3-6) — turns now accumulate in the shared append-only store instead of a
wholesale-rewritten file body, so no conversation ever approaches the old cap.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate import conversation as conv  # noqa: E402


# ── §6: serialize / parse / roundtrip ─────────────────────────────────────────
def test_serialize_turn_uses_fixed_speaker_tokens():
    block = conv.serialize_turn("user", "2026-06-14T10:00:00+09:00", "안녕하세요")
    assert block.splitlines()[0] == "## 🧑 사용자 · 2026-06-14T10:00:00+09:00"
    ai = conv.serialize_turn("ai", "2026-06-14T10:00:01+09:00", "Hi")
    assert ai.splitlines()[0] == "## 🤖 AI · 2026-06-14T10:00:01+09:00"


def test_append_turn_keeps_newest_at_bottom_with_blank_separator():
    body = conv.append_turn("", "user", "2026-06-14T10:00:00+09:00", "first")
    body = conv.append_turn(body, "ai", "2026-06-14T10:00:01+09:00", "second")
    # newest (AI 'second') is last
    assert body.rstrip().endswith("second")
    # exactly one blank line between the two turn blocks
    assert "first\n\n## 🤖 AI" in body


def test_parse_recovers_intro_and_turns():
    intro = "---\ntype: CH\n---"
    turns = [
        conv.Turn(speaker="user", ts="2026-06-14T10:00:00+09:00", body="hello\nworld"),
        conv.Turn(speaker="ai", ts="2026-06-14T10:00:01+09:00", body="hi there"),
    ]
    text = conv.serialize_conversation(turns, intro=intro)
    parsed = conv.parse_conversation(text)
    assert parsed["intro"] == intro
    assert [t["speaker"] for t in parsed["turns"]] == ["user", "ai"]
    assert parsed["turns"][0]["body"] == "hello\nworld"
    assert parsed["turns"][1]["ts"] == "2026-06-14T10:00:01+09:00"


def test_roundtrip_identity():
    intro = "intro line"
    turns = [
        conv.Turn(speaker="user", ts="2026-06-14T10:00:00+09:00", body="line a\nline b"),
        conv.Turn(speaker="ai", ts="2026-06-14T10:00:01+09:00", body="reply"),
        conv.Turn(speaker="user", ts="2026-06-14T10:00:02+09:00", body="multi\n\nparagraph"),
    ]
    text = conv.serialize_conversation(turns, intro=intro)
    again = conv.serialize_conversation(
        conv.parse_conversation(text)["turns"],
        intro=conv.parse_conversation(text)["intro"],
    )
    assert again == text


def test_header_like_body_is_escaped_and_roundtrips():
    # A user pastes a line that looks exactly like a turn header.
    sneaky = "## 🧑 사용자 · 2099-01-01T00:00:00+09:00"
    turns = [conv.Turn(speaker="ai", ts="2026-06-14T10:00:00+09:00", body=sneaky)]
    text = conv.serialize_conversation(turns)
    # The escaped form must NOT be mistaken for a real boundary.
    parsed = conv.parse_conversation(text)
    assert len(parsed["turns"]) == 1
    assert parsed["turns"][0]["body"] == sneaky
    # serialize escapes the header-like line with a leading backslash
    assert "\\## 🧑 사용자" in text


def test_escape_roundtrips_already_escaped_line():
    # Body already begins with a backslash + header-like text; must not be corrupted.
    body = "\\## 🤖 AI · 2099-01-01T00:00:00+09:00"
    turns = [conv.Turn(speaker="user", ts="2026-06-14T10:00:00+09:00", body=body)]
    text = conv.serialize_conversation(turns)
    parsed = conv.parse_conversation(text)
    assert parsed["turns"][0]["body"] == body


# ── R0127.0001: emoji-less / hand-typed headers are recognized as turns ────────
def test_parse_recognizes_emoji_less_headers():
    # A user (or external tool) types turn headers WITHOUT the speaker emoji.
    text = (
        "## 사용자 · 2026-06-24T22:00:00+09:00\n"
        "질문입니다\n\n"
        "## AI · 2026-06-24T22:01:00+09:00\n"
        "답변입니다\n"
    )
    parsed = conv.parse_conversation(text)
    assert [t["speaker"] for t in parsed["turns"]] == ["user", "ai"]
    assert parsed["turns"][0]["body"] == "질문입니다"
    assert parsed["turns"][1]["body"] == "답변입니다"


def test_parse_tolerates_extra_space_after_hashes():
    # R0127.0001 reproduces the actual mis-recognized line: "##  AI · …" (two spaces,
    # no emoji) must still be read as a turn boundary.
    text = "##  AI · 2026-06-24T22:01:48+09:00\nhello\n"
    parsed = conv.parse_conversation(text)
    assert len(parsed["turns"]) == 1
    assert parsed["turns"][0]["speaker"] == "ai"
    assert parsed["turns"][0]["body"] == "hello"


def test_emoji_and_emoji_less_headers_normalize_to_same_key():
    assert conv.speaker_key("🧑 사용자") == "user"
    assert conv.speaker_key("사용자") == "user"
    assert conv.speaker_key("🤖 AI") == "ai"
    assert conv.speaker_key("AI") == "ai"
    # Unknown labels fall back to the raw label (forward-compat).
    assert conv.speaker_key("System") == "System"


def test_emoji_less_header_in_body_is_escaped_and_roundtrips():
    # An emoji-less header-like line pasted into a body must not become a boundary.
    sneaky = "## AI · 2099-01-01T00:00:00+09:00"
    turns = [conv.Turn(speaker="user", ts="2026-06-14T10:00:00+09:00", body=sneaky)]
    text = conv.serialize_conversation(turns)
    parsed = conv.parse_conversation(text)
    assert len(parsed["turns"]) == 1
    assert parsed["turns"][0]["body"] == sneaky
    assert "\\## AI · 2099" in text


def test_serialize_still_emits_canonical_emoji_form():
    # Recognition is relaxed, but output stays canonical (emoji) so stored data is uniform.
    block = conv.serialize_turn("ai", "2026-06-14T10:00:00+09:00", "x")
    assert block.splitlines()[0] == "## 🤖 AI · 2026-06-14T10:00:00+09:00"


# ── 0293 R0001: provider slot in the AI label ─────────────────────────────────
def test_ai_label_with_provider_still_keys_to_ai():
    # THE regression this feature can cause (NR0004 발견 2): a provider-bearing label that
    # does not normalize to "ai" still renders as an AI bubble but stops being COUNTED as
    # one, and the chat surface then reports every successful reply as "no reply".
    parsed = conv.parse_conversation(
        "## 🤖 AI(claude-opus-4-8) · 2026-07-22T10:00:00+09:00\nhi\n"
    )
    assert [t["speaker"] for t in parsed["turns"]] == ["ai"]
    assert parsed["turns"][0]["provider"] == "claude-opus-4-8"
    assert parsed["turns"][0]["body"] == "hi"


def test_provider_slot_is_optional_and_emoji_less_form_works():
    assert conv.speaker_key("AI(gpt-5.6-sol)") == "ai"
    assert conv.speaker_provider("AI(gpt-5.6-sol)") == "gpt-5.6-sol"
    # No parentheses = not recorded, not "unknown".
    assert conv.speaker_provider("🤖 AI") is None
    assert "provider" not in conv.parse_conversation(
        "## 🤖 AI · 2026-07-22T10:00:00+09:00\nhi\n"
    )["turns"][0]
    # Empty parentheses carry no information either.
    assert conv.speaker_provider("AI()") is None


def test_provider_survives_roundtrip():
    # Re-serializing turns (e.g. for markdown rendering) must not drop the provider —
    # dropping it there would silently blank the badge.
    turns = [conv.Turn(speaker="ai", ts="2026-07-22T10:00:00+09:00", body="hi",
                       provider="claude-opus-4-8")]
    text = conv.serialize_conversation(turns)
    assert text.splitlines()[0] == "## 🤖 AI(claude-opus-4-8) · 2026-07-22T10:00:00+09:00"
    assert conv.serialize_conversation(conv.parse_conversation(text)["turns"]) == text


def test_provider_with_closing_paren_is_dropped_to_keep_header_parseable():
    # ")" would end the group early and the line would stop round-tripping.
    header = conv.turn_header("ai", "2026-07-22T10:00:00+09:00", "we(ird)name")
    assert header == "## 🤖 AI · 2026-07-22T10:00:00+09:00"


def test_provider_bearing_header_in_a_body_is_escaped():
    # Widening the alternation makes these lines header-LIKE, so they must now escape too.
    sneaky = "## 🤖 AI(x) · 2099-01-01T00:00:00+09:00"
    text = conv.serialize_conversation(
        [conv.Turn(speaker="user", ts="2026-07-22T10:00:00+09:00", body=sneaky)]
    )
    assert "\\## 🤖 AI(x) · 2099" in text
    assert conv.parse_conversation(text)["turns"][0]["body"] == sneaky


# ── 0306 NR0003 발견 1: localized user header (multi-read, locale write) ───────
def test_localized_user_headers_all_key_to_user():
    # Every ko/en/ja user label — emoji or emoji-less — normalizes to the logical role.
    for label in ("🧑 사용자", "사용자", "🧑 User", "User", "🧑 ユーザー", "ユーザー"):
        assert conv.speaker_key(label) == "user", label
    # AI is unchanged; an unrelated label still falls through to itself.
    assert conv.speaker_key("🤖 AI") == "ai"
    assert conv.speaker_key("System") == "System"


def test_turn_header_writes_locale_canonical_user_label():
    ts = "2026-07-23T10:00:00+09:00"
    assert conv.turn_header("user", ts, locale="ko") == f"## 🧑 사용자 · {ts}"
    assert conv.turn_header("user", ts, locale="en") == f"## 🧑 User · {ts}"
    assert conv.turn_header("user", ts, locale="ja") == f"## 🧑 ユーザー · {ts}"
    # Unknown/missing locale falls back to ko; AI ignores locale entirely.
    assert conv.turn_header("user", ts, locale="fr") == f"## 🧑 사용자 · {ts}"
    assert conv.turn_header("user", ts) == f"## 🧑 사용자 · {ts}"
    assert conv.turn_header("ai", ts, locale="en") == f"## 🤖 AI · {ts}"


def test_append_turn_uses_caller_locale():
    body = conv.append_turn("", "user", "2026-07-23T10:00:00+09:00", "hello", locale="en")
    assert body.splitlines()[0] == "## 🧑 User · 2026-07-23T10:00:00+09:00"


def test_mixed_locale_conversation_parses_every_turn_as_user():
    text = (
        "## 🧑 사용자 · 2026-07-23T10:00:00+09:00\n안녕\n\n"
        "## 🧑 User · 2026-07-23T10:00:01+09:00\nhi\n\n"
        "## ユーザー · 2026-07-23T10:00:02+09:00\nこんにちは\n"
    )
    parsed = conv.parse_conversation(text)
    assert [t["speaker"] for t in parsed["turns"]] == ["user", "user", "user"]
    assert [t["body"] for t in parsed["turns"]] == ["안녕", "hi", "こんにちは"]
    assert [t["locale"] for t in parsed["turns"]] == ["ko", "en", "ja"]


def test_localized_header_like_body_lines_are_escaped_in_any_language():
    # An en/ja user header pasted into an AI body must not become a turn boundary.
    for sneaky in (
        "## 🧑 User · 2099-01-01T00:00:00+09:00",
        "## ユーザー · 2099-01-01T00:00:00+09:00",
    ):
        text = conv.serialize_conversation(
            [conv.Turn(speaker="ai", ts="2026-07-23T10:00:00+09:00", body=sneaky)]
        )
        assert "\\" + sneaky in text
        parsed = conv.parse_conversation(text)
        assert len(parsed["turns"]) == 1
        assert parsed["turns"][0]["speaker"] == "ai"
        assert parsed["turns"][0]["body"] == sneaky


def test_serialize_preserves_user_locale_variant():
    # Re-serializing turns must keep an en/ja user turn in its own language, not
    # collapse it to Korean.
    text = (
        "## 🧑 User · 2026-07-23T10:00:00+09:00\nhi\n\n"
        "## 🧑 ユーザー · 2026-07-23T10:00:01+09:00\nやあ\n"
    )
    again = conv.serialize_conversation(conv.parse_conversation(text)["turns"])
    assert again == text


def test_carryover_helpers_are_retired():
    # Group 0351 T5 (D0002 §3-6): the byte-cap carry-over is removed — turns
    # accumulate in the shared append-only store instead of a wholesale-rewritten
    # file body, so this rollover machinery no longer exists on the module.
    for name in ("should_carry_over", "tail_turns", "build_carryover_intro",
                 "CARRYOVER_RATIO", "CARRYOVER_KEEP_TURNS", "content_byte_len"):
        assert not hasattr(conv, name), f"conversation.{name} should have been removed"


# ── §2: type registration ─────────────────────────────────────────────────────
def test_ch_in_type_sets():
    from modules.flow_gate.linter import VALID_TYPES
    from modules.flow_gate.db import INBOX_PROCESS_TYPES
    from modules.flow_gate.documents.routers.documents import (
        AUTO_COMPLETE_TYPES,
        CONVERSATION_TYPE_CODES,
    )
    assert "CH" in VALID_TYPES
    assert "CH" in INBOX_PROCESS_TYPES
    assert "CH" in AUTO_COMPLETE_TYPES
    assert "CH" in CONVERSATION_TYPE_CODES
    # M must remain an auto-complete type; CH joins it (not replaces it).
    assert "M" in AUTO_COMPLETE_TYPES
    # CH is conversation-specific, M is not.
    assert "M" not in CONVERSATION_TYPE_CODES


def test_ch_inbox_process_tuple_in_db_documents(monkeypatch):
    """CH 문서가 inbox 처리 질의의 대상에 실제로 실려 나가야 한다.

    0394 T0016 항목 4 (NR0003 §6.2-라): 예전에는 `inspect.getsource(...)` 로 함수 소스를
    읽어 '"CH"' 라는 글자가 있는지만 봤다. 그 글자는 주석에 있어도, 죽은 분기에 있어도
    통과하고, 반대로 튜플을 상수로 빼내 재사용하면 글자가 사라져 무고하게 빨개진다.
    질의에 실제로 실려 나가는 파라미터를 보는 것이 같은 것을 훨씬 곧게 확인한다.

    같은 목록이 `db/__init__.py` 의 INBOX_PROCESS_TYPES 로 한 벌 더 있으므로, 두 벌이
    어긋나면 한쪽 화면에서만 문서가 사라진다 — 그래서 두 목록이 같은지도 함께 못박는다.
    """
    from modules.flow_gate.db import INBOX_PROCESS_TYPES
    from modules.flow_gate.db import documents as db_docs

    seen: dict[str, object] = {}

    class _Recorder:
        def _fetch_all(self, sql, params):
            seen["sql"] = sql
            seen["params"] = list(params)
            return []

    monkeypatch.setattr(db_docs, "get_store", lambda: _Recorder())

    assert db_docs.get_inbox_process_documents(group_id="flowgate.default.0044") == []
    assert "CH" in seen["params"], "CH 가 inbox 처리 질의의 type_code 목록에 없다"
    assert set(seen["params"]) - {"flowgate.default.0044"} == set(INBOX_PROCESS_TYPES), (
        "get_inbox_process_documents 의 타입 목록이 db.INBOX_PROCESS_TYPES 와 어긋난다 — "
        "두 벌이 갈라지면 목록에서만 빠진 타입이 조용히 안 보이게 된다"
    )


def test_process_service_ch_maps():
    from modules.flow_gate import process_service as ps
    assert ps.TYPE_ACTIONS.get("CH") == []  # no review actions (non-gate, like M)


# ── §2: migration registers the CH document type ──────────────────────────────
def test_migration_seeds_ch_doctype(test_db):
    row = test_db.execute(
        "SELECT type_code, type_name, series, is_system, is_active "
        "FROM document_types WHERE type_code='CH' AND project_id IS NULL"
    ).fetchone()
    assert row is not None
    assert row["series"] == "general"
    assert row["is_system"] == 1 and row["is_active"] == 1
    # The pre-existing commit 'C' type must remain untouched (distinct code).
    c_row = test_db.execute(
        "SELECT type_code FROM document_types WHERE type_code='C' AND project_id IS NULL"
    ).fetchone()
    assert c_row is not None
