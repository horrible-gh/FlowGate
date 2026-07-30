"""Conversation (CH) turn serialization and parsing.

Single source of truth for the chat turn wire format shared by the serializer
(accumulation side) and the parser (render side), per L0044.0008 §6. The body of
a CH document IS the chat log: turns are appended in time order with the newest at
the bottom (D0044.0006 §6 / NR0044.0003 §3), so a viewer renders the body top→down.

Turn format (§6):

    ## <speaker emoji+label> · <ISO8601>
    <body line 1>
    <body line 2>

Turns are separated by exactly one blank line. Speaker tokens are fixed constants
(USER_SPEAKER / AI_SPEAKER) shared by serializer and parser. A body line that would
otherwise look like a turn header is escaped with a leading backslash so the parser
does not mistake it for a boundary; the escape round-trips exactly.

Byte-cap carry-over (former §7) is retired (group 0351 T5, D0002 §3-6): turns now
accumulate in the shared append-only store (conversation_turn_service) instead of a
wholesale-rewritten file body, so a conversation never approaches the old inbox
content cap and no successor document is ever opened for it. serialize_conversation/
parse_conversation stay the shared wire format for rendering (conversation_markdown_service)
and for reading conversations split by the retired mechanism before this change.
"""
from __future__ import annotations

import re
from typing import Optional, TypedDict

# ── Speaker tokens (shared by serializer and parser) ───────────────────────────
# 0306 NR0003 발견 1: the user label is LOCALIZED. The stored turn header is the CH
# document's own text — visible in the raw CH, on copy, and to external tooling — so a
# ko/en/ja session records its user turn in that language instead of a hardcoded Korean
# token. "lenient in, strict out" still holds: every locale variant PARSES back to the
# logical "user" key, but a NEW turn is written in its author's locale (turn_header).
USER_SPEAKER = "🧑 사용자"  # canonical ko form; also the fallback for unknown locales
AI_SPEAKER = "🤖 AI"

# Canonical (emoji-bearing) user label per UI locale. Unknown/missing locales fall back
# to ko, matching the client's X-Locale default (client/shared/api.ts).
_USER_SPEAKER_BY_LOCALE: dict[str, str] = {
    "ko": "🧑 사용자",
    "en": "🧑 User",
    "ja": "🧑 ユーザー",
}
# Bare user names (emoji/provider already stripped) that normalize to the "user" key,
# mapped to the locale they were written in so re-serialization can preserve the variant.
_USER_NAME_TO_LOCALE: dict[str, str] = {"사용자": "ko", "User": "en", "ユーザー": "ja"}
_AI_NAMES = frozenset({"AI"})

# Logical speaker key → display token.
_SPEAKER_TOKENS: dict[str, str] = {"user": USER_SPEAKER, "ai": AI_SPEAKER}
_TOKEN_TO_KEY: dict[str, str] = {USER_SPEAKER: "user", AI_SPEAKER: "ai"}

# Speaker label alternation accepted on PARSE (R0127.0001): the leading emoji is
# OPTIONAL so a turn typed by hand or by an external tool — "## AI · …",
# "## 사용자 · …" — is still recognized as a chat turn. Serialization always emits
# the canonical emoji form (USER_SPEAKER/AI_SPEAKER), so stored data stays uniform;
# only recognition is relaxed (robustness principle: lenient in, strict out).
#
# 0293 R0001 / NR0004 발견 1: the AI label may carry a provider in parentheses —
# "## 🤖 AI(claude-opus-4-8) · …". The turn header is the only metadata slot the wire
# format has, so the provider rides there. The suffix is OPTIONAL, so every turn
# written before this change keeps parsing unchanged. speaker_key() strips it, which
# is what keeps the parser/renderer downstream of it untouched — see the warning in
# that docstring.
#
# 0306 NR0003 발견 1: the user label may now be Korean, English, or Japanese (emoji
# still optional). All three normalize to "user" (speaker_key). Because _HEADERLIKE_RE
# and the FE parser reuse this same alternation, every locale's header — and every
# header-LIKE body line in any locale — is recognized and escaped in lockstep.
_SPEAKER_ALT = r"(?:🧑 )?(?:사용자|User|ユーザー)|(?:🤖 )?AI(?:\([^)]*\))?"

# Provider suffix on an AI label, e.g. "AI(claude-opus-4-8)" → "claude-opus-4-8".
_PROVIDER_RE = re.compile(r"^(?P<label>.*?)\((?P<provider>[^)]*)\)$")

# Leading-emoji prefixes stripped when normalizing a parsed label to a logical key.
_EMOJI_PREFIXES = ("🧑 ", "🤖 ")

# Turn header: "## <speaker> · <ISO8601>". The "##" may be followed by one or more
# spaces (a hand-typed "##  AI" double-space still counts). The timestamp is a single
# non-space token (offset-bearing ISO 8601, e.g. 2026-06-13T14:02:11+09:00).
HEADER_RE = re.compile(rf"^##\s+(?P<speaker>{_SPEAKER_ALT}) · (?P<ts>\S+)\s*$")

# Header-like line with zero or more leading escape backslashes — used to decide
# whether a body line must be escaped (and to reverse it on parse). Kept in lockstep
# with HEADER_RE so the same lines that parse as turns also escape correctly.
_HEADERLIKE_RE = re.compile(rf"^\\*##\s+(?:{_SPEAKER_ALT}) · \S+\s*$")


def _strip_speaker_decorations(label: str) -> tuple[str, Optional[str]]:
    """Split a raw speaker label into its bare name and its provider suffix."""
    s = label
    for prefix in _EMOJI_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    m = _PROVIDER_RE.match(s)
    if m is None:
        return s, None
    provider = m.group("provider").strip()
    return m.group("label"), (provider or None)


def speaker_key(label: str) -> str:
    """Normalize a parsed speaker label to a logical key ("user"/"ai").

    Accepts the canonical emoji tokens ("🧑 사용자"/"🤖 AI"), their emoji-less forms
    ("사용자"/"AI"), (0293) an AI label carrying a provider suffix
    ("🤖 AI(claude-opus-4-8)"), and (0306) the English/Japanese user labels
    ("User"/"ユーザー") in either emoji or emoji-less form. Unknown labels fall back to the
    raw label (forward-compat with other speakers), matching old _TOKEN_TO_KEY behaviour.

    The provider suffix MUST be stripped here. Everything downstream keys off the
    logical "ai" string — including the chat surface's own success test, which counts
    AI turns before and after a run (ConversationView.pollRun). A label that leaks the
    parentheses through still RENDERS as an AI bubble (the renderer only tests for
    "user"), so the failure is silent: every successful chat invoke would report
    "no reply" (0293 NR0004 발견 2)."""
    bare, _ = _strip_speaker_decorations(label)
    if bare in _USER_NAME_TO_LOCALE:
        return "user"
    if bare in _AI_NAMES:
        return "ai"
    return label


def user_locale_of(label: str) -> Optional[str]:
    """The UI locale a user turn header was written in ("ko"/"en"/"ja"), or None when the
    label is not a user turn (0306 NR0003 발견 1). Lets a turn be re-serialized in its
    original language instead of collapsing every user turn to Korean."""
    bare, _ = _strip_speaker_decorations(label)
    return _USER_NAME_TO_LOCALE.get(bare)


def speaker_provider(label: str) -> Optional[str]:
    """The provider recorded in a speaker label's parentheses, or None.

    None means "not recorded" — not "unknown provider". A turn written before 0293,
    or by a model that does not know its own name, simply carries no suffix and the
    renderer draws no badge."""
    bare, provider = _strip_speaker_decorations(label)
    known = bare in _USER_NAME_TO_LOCALE or bare in _AI_NAMES
    return provider if known else None


class _TurnBase(TypedDict):
    speaker: str  # "user" | "ai" (unknown tokens fall back to the raw label)
    ts: str
    body: str


class Turn(_TurnBase, total=False):
    # 0293: provider recorded in the header's parentheses; absent/None = not recorded.
    # Optional so existing Turn(speaker=…, ts=…, body=…) construction stays valid, and
    # so re-serializing a turn never loses its provider.
    provider: Optional[str]
    # 0306: the UI locale a user turn header was written in ("ko"/"en"/"ja"), so
    # re-serialization keeps it in the same language. Absent on AI turns and on turns
    # constructed without one (which then serialize to the ko fallback).
    locale: Optional[str]


class ParsedConversation(TypedDict):
    intro: str          # text before the first turn header (frontmatter/intro = turn 0)
    turns: list[Turn]


def _escape_line(line: str) -> str:
    """Escape a body line that would otherwise be read as a turn header."""
    if _HEADERLIKE_RE.match(line):
        return "\\" + line
    return line


def _unescape_line(line: str) -> str:
    """Reverse _escape_line: strip one backslash from an escaped header-like line."""
    if line.startswith("\\") and _HEADERLIKE_RE.match(line[1:]):
        return line[1:]
    return line


def turn_header(
    speaker: str,
    ts: str,
    provider: Optional[str] = None,
    locale: Optional[str] = None,
) -> str:
    """Build the one-line header for a turn. *speaker* is a key ("user"/"ai") or a
    raw display label (used verbatim for forward-compat with other speakers).

    *provider* (0293) appends the parenthesized provider slot. It is dropped when it
    contains ")" — that character would end the group early and the line would no
    longer round-trip through HEADER_RE.

    *locale* (0306) selects the localized user label ("🧑 사용자"/"🧑 User"/"🧑 ユーザー")
    for a NEW user turn; it is ignored for other speakers and falls back to ko for a
    missing/unknown locale."""
    if speaker == "user":
        label = _USER_SPEAKER_BY_LOCALE.get(locale or "", USER_SPEAKER)
    else:
        label = _SPEAKER_TOKENS.get(speaker, speaker)
    if provider and ")" not in provider:
        label = f"{label}({provider})"
    return f"## {label} · {ts}"


def serialize_turn(
    speaker: str,
    ts: str,
    body: str,
    provider: Optional[str] = None,
    locale: Optional[str] = None,
) -> str:
    """Serialize a single turn to its `header + escaped body` block (no trailing
    separator)."""
    lines = [turn_header(speaker, ts, provider, locale)]
    for body_line in body.split("\n"):
        lines.append(_escape_line(body_line))
    return "\n".join(lines)


def append_turn(
    existing_content: str,
    speaker: str,
    ts: str,
    body: str,
    locale: Optional[str] = None,
) -> str:
    """Append one turn to the bottom of *existing_content*, separated by a single
    blank line. Returns the full new body to submit via inbox edit (full replace).

    *locale* (0306) records a NEW user turn in the caller's UI language; it falls back
    to ko for a missing/unknown locale and is ignored for non-user turns."""
    block = serialize_turn(speaker, ts, body, locale=locale)
    if existing_content and existing_content.strip():
        return existing_content.rstrip("\n") + "\n\n" + block + "\n"
    return block + "\n"


def parse_conversation(content: str) -> ParsedConversation:
    """Split a conversation body into its intro (turn 0) and ordered turns.

    Lines matching HEADER_RE (unescaped) are turn boundaries; everything before the
    first boundary is the intro. Each turn's body has the single separator blank
    line removed and header-like lines unescaped, so re-serializing is identity."""
    intro_lines: list[str] = []
    turns: list[Turn] = []
    cur_speaker: Optional[str] = None
    cur_ts: Optional[str] = None
    cur_provider: Optional[str] = None
    cur_locale: Optional[str] = None
    cur_body: list[str] = []
    started = False

    def _flush() -> None:
        if cur_speaker is None:
            return
        body_lines = list(cur_body)
        # Drop the single trailing blank line that separates this turn from the next
        # (or the file's trailing newline for the last turn).
        if body_lines and body_lines[-1] == "":
            body_lines.pop()
        turn = Turn(speaker=cur_speaker, ts=cur_ts or "", body="\n".join(body_lines))
        if cur_provider:
            turn["provider"] = cur_provider
        if cur_locale:
            turn["locale"] = cur_locale
        turns.append(turn)

    for line in content.split("\n"):
        m = HEADER_RE.match(line)
        if m:
            _flush()
            cur_speaker = speaker_key(m.group("speaker"))
            cur_provider = speaker_provider(m.group("speaker"))
            cur_locale = user_locale_of(m.group("speaker"))
            cur_ts = m.group("ts")
            cur_body = []
            started = True
        elif not started:
            intro_lines.append(line)
        else:
            cur_body.append(_unescape_line(line))
    _flush()

    intro = "\n".join(intro_lines).rstrip("\n")
    return ParsedConversation(intro=intro, turns=turns)


def serialize_conversation(turns: list[Turn], intro: str = "") -> str:
    """Inverse of parse_conversation at the turn-list level: intro + turns joined by
    a single blank line, with a trailing newline. serialize→parse→serialize is
    identity (bodies are taken to have no trailing blank line, as chat turns do)."""
    parts: list[str] = []
    if intro:
        parts.append(intro.rstrip("\n"))
    for t in turns:
        parts.append(
            serialize_turn(
                t["speaker"], t["ts"], t["body"], t.get("provider"), t.get("locale")
            )
        )
    return ("\n\n".join(parts) + "\n") if parts else ""
