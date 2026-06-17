"""T0044.0009 — conversation (CH) type: serializer/parser, carry-over, registration.

Covers L0044.0008 §2 (type registration), §6 (turn serialization/parsing/escaping),
and §7 (carry-over threshold/tail). Pure-unit + migration-row checks (no HTTP).
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


# ── §7: carry-over threshold / tail ───────────────────────────────────────────
def test_should_carry_over_at_80_percent():
    assert conv.should_carry_over(80, 100) is True
    assert conv.should_carry_over(79, 100) is False
    assert conv.should_carry_over(100, 100) is True
    assert conv.should_carry_over(10, 0) is False  # guard: no cap


def test_tail_turns_returns_most_recent():
    turns = [
        conv.Turn(speaker="user", ts=f"2026-06-14T10:00:{i:02d}+09:00", body=f"turn {i}")
        for i in range(5)
    ]
    text = conv.serialize_conversation(turns)
    tail = conv.tail_turns(text, keep_turns=2)
    assert [t["body"] for t in tail] == ["turn 3", "turn 4"]
    assert conv.tail_turns(text, keep_turns=0) == []


def test_build_carryover_intro_carries_recent_turns():
    turns = [
        conv.Turn(speaker="user", ts=f"2026-06-14T10:00:{i:02d}+09:00", body=f"turn {i}")
        for i in range(4)
    ]
    text = conv.serialize_conversation(turns)
    intro = conv.build_carryover_intro("flowgate.default.0044.0010-CH", text, keep_turns=2)
    assert "continued from flowgate.default.0044.0010-CH" in intro
    assert "turn 3" in intro and "turn 2" in intro
    assert "turn 0" not in intro


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


def test_ch_inbox_process_tuple_in_db_documents():
    import inspect
    from modules.flow_gate.db import documents as db_docs
    src = inspect.getsource(db_docs.get_inbox_process_documents)
    assert '"CH"' in src


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
