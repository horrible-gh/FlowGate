"""Node title text rules (0062 L0006 1.1 / 1.3 / 2.1 / 2.2, 0064 L0005 1.5 / 2.17-1).

- ``normalize_title``: NFC, control characters and line / paragraph / space separators as one space, zero-width
  space and BOM removed, runs of spaces collapsed, trimmed. Person titles and AI candidates are stored as this.
- ``validate_title``: text, raw length (code points) at most the raw cap, then 1~30 code points once normalized.
- ``default_names`` / ``is_default_name``: the default names of a card in every supported locale (exact match of
  the casefolded normalized text): its palette item (by id, else by its document type), its node kind, and the
  R / B root names (0064 L0005 2.17-1: a ``Bug`` card's ``버그`` is a default name too).

The names are the client i18n values copied (``gui.designer.palette.items`` / ``gui.designer.center.node_kinds``,
the R / B names of ``root_conversion_cards``); a test keeps them equal to the client files.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Optional

from modules.flow_gate.graph_workflow.domain import canvas_mapping as cm
from modules.flow_gate.graph_workflow.domain.graph_model import node_kind
from modules.flow_gate.graph_workflow.projection.graph_projector import to_graph_node

from .root_conversion_cards import ROOT_NAMES

TITLE_MAX_CHARS = 30
TITLE_MIN_CHARS = 1
HUMAN_RAW_MAX_CHARS = 200
CANDIDATE_RAW_MAX_CHARS = 200
UNICODE_NORMAL_FORM = "NFC"
DEFAULT_NAME_LOCALES = ("ko", "ja", "en")
_SPACE_CATEGORIES = frozenset({"Cc", "Zs", "Zl", "Zp"})
_REMOVED = frozenset({"​", "﻿"})

PALETTE_NAMES = {
    "basic_design": {"ko": "기본 설계", "ja": "基本設計", "en": "Basic design"},
    "protocol": {"ko": "프로토콜", "ja": "プロトコル", "en": "Protocol"},
    "logic": {"ko": "로직", "ja": "ロジック", "en": "Logic"},
    "db_design": {"ko": "DB설계", "ja": "DB設計", "en": "DB design"},
    "research": {"ko": "조사", "ja": "調査", "en": "Research"},
    "plan": {"ko": "작업계획", "ja": "作業計画", "en": "Work plan"},
    "task": {"ko": "작업", "ja": "作業", "en": "Task"},
    "test": {"ko": "테스트", "ja": "テスト", "en": "Test"},
}
PALETTE_KEY_OF_DOC_TYPE = {"D": "basic_design", "P": "protocol", "L": "logic", "DB": "db_design", "N": "research",
                           "WP": "plan", "T": "task", "TS": "test"}
NODE_KIND_NAMES = {
    "requirement": {"ko": "요구사항", "ja": "要件", "en": "Requirement"},
    "research": {"ko": "조사", "ja": "調査", "en": "Research"},
    "plan": {"ko": "작업계획", "ja": "作業計画", "en": "Work plan"},
    "task": {"ko": "작업", "ja": "作業", "en": "Task"},
    "test": {"ko": "테스트", "ja": "テスト", "en": "Test"},
    "approval": {"ko": "승인", "ja": "承認", "en": "Approval"},
    "design": {"ko": "설계", "ja": "設計", "en": "Design"},
    "memo": {"ko": "메모", "ja": "メモ", "en": "Memo"},
    "unknown": {"ko": "노드", "ja": "ノード", "en": "Node"},
}


@dataclass(frozen=True)
class TitleCheck:
    value: Optional[str]          # the normalized title when valid
    reason: Optional[str]         # None (valid) | "not_text" | "too_long" | "empty"
    length: int                   # code points: of the raw text when it is over the raw cap, else normalized


def normalize_title(raw: Optional[str]) -> str:
    text = unicodedata.normalize(UNICODE_NORMAL_FORM, raw or "")
    text = "".join(" " if unicodedata.category(ch) in _SPACE_CATEGORIES else ch for ch in text if ch not in _REMOVED)
    return " ".join(part for part in text.split(" ") if part)


def title_length(text: str) -> int:
    return len(text)


def validate_title(raw, raw_max: int) -> TitleCheck:
    if not isinstance(raw, str):
        return TitleCheck(None, "not_text", 0)
    if len(raw) > raw_max:
        return TitleCheck(None, "too_long", len(raw))
    text = normalize_title(raw)
    if title_length(text) < TITLE_MIN_CHARS:
        return TitleCheck(None, "empty", 0)
    if title_length(text) > TITLE_MAX_CHARS:
        return TitleCheck(None, "too_long", title_length(text))
    return TitleCheck(text, None, title_length(text))


def name_key(text: Optional[str]) -> str:
    return normalize_title(text).casefold()


def default_names(node_row: dict) -> set[str]:
    """The name keys a card shows by default (0062 L0006 2.2 + 0064 L0005 2.17-1)."""
    names: set[str] = set()
    doc_type = cm.normalize_type(node_row.get("doc_type"))
    item = node_row.get("palette_item_id")
    item = item if item in PALETTE_NAMES else PALETTE_KEY_OF_DOC_TYPE.get(doc_type)
    if item is not None:
        names |= {name_key(PALETTE_NAMES[item][loc]) for loc in DEFAULT_NAME_LOCALES}
    kind = node_kind(to_graph_node({"node_key": "", "node_type": None, "label": "", **node_row}), cm.kind_for_type)
    if kind in NODE_KIND_NAMES:
        names |= {name_key(NODE_KIND_NAMES[kind][loc]) for loc in DEFAULT_NAME_LOCALES}
    if node_row.get("node_type") == "document" and doc_type in ROOT_NAMES:
        names |= {name_key(ROOT_NAMES[doc_type][loc]) for loc in DEFAULT_NAME_LOCALES}
    return names


def is_default_name(text: Optional[str], node_row: dict) -> bool:
    key = name_key(text)
    return bool(key) and key in default_names(node_row)
