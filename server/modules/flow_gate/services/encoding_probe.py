"""Language-neutral transport proof and corruption signals for AI-submitted text.

flowgate.v02gui.0009 (chat 0002-CH): the question-mark heuristic
(``workflow_decision_service._text_is_corrupted``) is tied to one failure shape -- a
script whose every glyph turns into ASCII '?' -- so it rejects ordinary punctuation
("??") yet misses a partial loss (cp949 keeps kana but drops Japanese-only kanji) and
any mis-decoding. Corruption is not a property of a language but of the path: did the
text cross a non-UTF-8 hop (a legacy console codepage)? This module answers that
question without looking at which language the text is in.

1. Probe (path proof). Each worker token gets its own probe line
   ``fg-probe:<nonce>-<U+20BB7>``. The glyph sits on a supplementary plane that no
   legacy codepage carries (ascii, cp125x, cp949, cp932, cp936, big5, koi8 ...) and
   GB18030 maps it losslessly, so any lossy hop replaces or alters it; an exact match
   is proof the body file travelled intact. The nonce is derived from the token id,
   so it changes per request without server state and an old probe recalled from
   memory never matches. The worker writes it as the LAST line of the body file
   itself (not a separate JSON field), so a body file mangled before sending (e.g. a
   PowerShell ``Set-Content``) mangles the probe with it.
2. Hard signals for text that carries no probe: U+FFFD (a decoder already failed) and
   reversible mojibake (re-encoding a line with a legacy codepage yields valid UTF-8
   that is shorter and made of real characters). Neither counts '?', so neither
   rejects punctuation in any language.
"""
from __future__ import annotations

import hashlib
import unicodedata
from typing import Optional

PROBE_PREFIX = "fg-probe:"
PROBE_GLYPH = "\U00020BB7"

PROBE_ABSENT = "absent"
PROBE_MATCHED = "matched"
PROBE_BROKEN = "broken"

SIGNAL_REPLACEMENT_CHAR = "replacement_char"
SIGNAL_MOJIBAKE = "mojibake"

# Legacy codepages a UTF-8 byte stream is commonly mis-decoded through on Windows
# consoles and editors. Order only affects which codec reports first.
_MOJIBAKE_CODECS = ("cp1252", "latin-1", "cp949", "cp932", "cp936", "cp1251")
_IMPLAUSIBLE_CATEGORIES = frozenset({"Cn", "Co", "Cs", "Cc"})


def probe_for(token_id: Optional[str]) -> Optional[str]:
    """The probe line a worker holding ``token_id`` must end its body with."""
    if not token_id:
        return None
    digest = hashlib.sha256(f"flowgate-encoding-probe:{token_id}".encode("utf-8")).hexdigest()
    return f"{PROBE_PREFIX}{digest[:10]}-{PROBE_GLYPH}"


def split_probe(text: Optional[str], expected: Optional[str]) -> tuple[str, str, Optional[str]]:
    """Find the probe on the last non-blank line of ``text``.

    Returns ``(text_without_probe, status, received)``. ``status`` is PROBE_ABSENT when
    the last non-blank line does not start with the ASCII prefix (or no probe is
    expected), PROBE_MATCHED when it equals ``expected`` exactly -- the line and the
    blank lines after it are removed -- and PROBE_BROKEN otherwise (text unchanged).
    The prefix is ASCII so it survives every codepage: a damaged probe is still
    recognised as a probe. ``received`` is the probe line as it arrived.
    """
    text = text or ""
    if not expected:
        return text, PROBE_ABSENT, None
    lines = text.split("\n")
    idx = len(lines) - 1
    while idx >= 0 and not lines[idx].strip():
        idx -= 1
    if idx < 0:
        return text, PROBE_ABSENT, None
    candidate = lines[idx].strip().lstrip("﻿")
    if not candidate.startswith(PROBE_PREFIX):
        return text, PROBE_ABSENT, None
    if candidate != expected:
        return text, PROBE_BROKEN, candidate
    return "\n".join(lines[:idx]).rstrip("\r\n"), PROBE_MATCHED, candidate


def describe_received(received: Optional[str], limit: int = 80) -> str:
    """ASCII-only rendering of a received probe for an error message.

    ``ascii()`` escapes every non-ASCII code point, so the message itself survives
    the same lossy console that damaged the probe and shows exactly what was lost.
    """
    rendered = ascii(received or "")
    return rendered if len(rendered) <= limit else rendered[: limit - 3] + "..."


def line_is_mojibake(line: str) -> bool:
    """True when ``line`` reads as UTF-8 bytes decoded through a legacy codepage.

    A line counts only when re-encoding it with one of the codecs gives valid UTF-8
    that is at least two characters shorter (every UTF-8 multibyte sequence widens to
    2+ characters when mis-decoded; legitimate text does not shrink) and consists of
    assigned, non-private, non-control characters.
    """
    if not line or line.isascii():
        return False
    for codec in _MOJIBAKE_CODECS:
        try:
            repaired = line.encode(codec).decode("utf-8")
        except UnicodeError:
            continue
        if repaired == line or len(line) - len(repaired) < 2:
            continue
        if any(
            unicodedata.category(ch) in _IMPLAUSIBLE_CATEGORIES
            for ch in repaired
            if ord(ch) > 0x7F
        ):
            continue
        return True
    return False


def hard_corruption_signal(text: Optional[str]) -> Optional[str]:
    """Language-neutral corruption evidence in ``text``, or None."""
    if not text:
        return None
    if "�" in text:
        return SIGNAL_REPLACEMENT_CHAR
    if any(line_is_mojibake(line) for line in text.splitlines()):
        return SIGNAL_MOJIBAKE
    return None
