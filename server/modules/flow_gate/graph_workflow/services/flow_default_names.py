"""Default titles shared by the first-run flow/chat naming service (0071).

Root names remain owned by root_conversion_cards. This module only classifies
names; it never changes stored data. A manual-name marker must still be checked
by callers before renaming, even when the text matches a default.
"""
from __future__ import annotations

from .root_conversion_cards import ROOT_NAMES

FLOW_NAMES = {"ko": "새 플로우", "ja": "新規フロー", "en": "New Flow"}
CHAT_NAMES = {"ko": "새 대화", "ja": "新しい会話", "en": "New conversation"}


def default_title(target: str, locale: str = "ko", *, doc_type: str | None = None) -> str:
    """The creation default; root identity is R/B, never kind=requirement alone."""
    if target == "flow":
        names = FLOW_NAMES
    elif target == "chat":
        names = CHAT_NAMES
    elif target == "root" and doc_type in ROOT_NAMES:
        names = ROOT_NAMES[doc_type]
    else:
        raise ValueError("Expected flow, chat, or root with doc_type R/B")
    return names.get(locale, names["ko"])


def is_default_title(text: str | None, target: str, *, doc_type: str | None = None) -> bool:
    """Exact supported-locale defaults; blank/custom text is never a default.

    Exact matching avoids treating a person's differently cased/spaced name as
    an untouched creation default. Callers separately enforce manual edits.
    """
    return any(text == default_title(target, locale, doc_type=doc_type)
               for locale in ("ko", "ja", "en"))
