"""The read-only gates of a v0.2 completion (flowgate.v02.0119 T#4, T-3; 0004-CH seq 9 T-3, seq 11 교체 3).

Before a node's result is completed (``card_settings.legacy_adapter.approve``) or a card's instruction is approved
(``instruction.approve``), the document passes the gates the v0.1 approval asked — never the v0.1 approval itself:

    require_body       a readable, non-blank body after the front matter
    require_test_gate  a TSR's test gate, a TS's specification contract (a contract v2 TS validates) — the v0.1
                       ``_require_test_gate_for_approval``; a card's TS instruction asks it too (flowgate.v02.0119 T#4
                       rework, rej_01M6DQW6YF0SCQ5D P1: ``instruction.approve``)
    require_gates      the body, then the test gate (a node's result)
    body_complete    the body judgement as a yes / no (``card_documents.lines.has_body``: whether a card line is written)
    text_complete    the same judgement of a body text already read (the card line read)
    fileless         a type the body gate never asked a file of

The rules themselves are the v0.1 ones, called through ``legacy_adapter`` (never changed); what judges a card document
"approvable" in v0.2 is this module (T-3: ``card_documents.legacy_adapter``'s approvability judgement is replaced by it).
"""
from __future__ import annotations

from typing import Optional

from flowgate.card_completion import legacy_adapter as legacy

GateRefused = legacy.GateRefused
LOCALE = "ko"


def require_body(doc: dict, locale: str = LOCALE) -> None:
    legacy.require_body(doc, locale)


def require_test_gate(doc: dict, locale: str = LOCALE) -> None:
    legacy.require_test_gate(doc, locale)


def require_gates(doc: dict, locale: str = LOCALE) -> None:
    """A node result's gates: its body, then a TSR's test gate. Raises ``GateRefused``."""
    legacy.require_body(doc, locale)
    legacy.require_test_gate(doc, locale)


def body_complete(doc: Optional[dict]) -> bool:
    if not doc:
        return False
    try:
        legacy.require_body(doc)
    except GateRefused:
        return False
    return True


def text_complete(doc: dict, text: str) -> bool:
    return legacy.approvable_text(doc, text)


def fileless(doc: Optional[dict]) -> bool:
    return legacy.fileless_type((doc or {}).get("type_code"))


__all__ = ["GateRefused", "require_body", "require_test_gate", "require_gates", "body_complete", "text_complete", "fileless"]
