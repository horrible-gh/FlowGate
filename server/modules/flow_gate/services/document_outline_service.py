"""Document lookup tools — canonical text, outline and section maths (group 0370, P0002 / L0003).

P0002 fixed the format (locator and response shape); L0003 fixed the computation that
fills it in. This module implements L0003 §1 (parameters) and §2-1..§2-6 verbatim and is
the **single source of truth**. The `/outline`, `/section` and `/meta` routes, search hit
positions (2-7) and the save response's change summary (2-9, change_summary_service) all
use only the functions here, so one named number never differs per screen (L0003 goal).

Three core conventions come first.

* Every coordinate is **relative to the stored file text**. Line numbers are 1-based and
  inclusive, char offsets 0-based exclusive, lengths in code points not bytes (P0002 §1-2).
* Frontmatter detection imports the very regex search uses
  (`content_search_service._FRONTMATTER`). A second copy would drift from search (L0003 §2-1).
* The alias table (the Korean/English `Changed Files` pair, etc.) is **imported** from the
  existing parser's constants. Retyping the values lets one side drift (L0003 §2-4).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from bisect import bisect_right
from pathlib import Path
from typing import Any, Optional

# L0003 §2-1: the frontmatter boundary must match search character for character. The
# private name is imported on purpose — keeping two copies would be far more dangerous.
from modules.flow_gate.services.content_search_service import (  # noqa: F401
    SEARCH_SNIPPET_CHARS as _SEARCH_SNIPPET_CHARS,
    _FRONTMATTER,
)

# ── 1. Parameters (L0003 §1 — never scattered inline through the code) ───────────
SECTION_MAX_CHARS_DEFAULT = 20000     # default section-read cap (characters)
SECTION_MAX_CHARS_LIMIT = 200000      # ceiling a request may raise it to (characters)
OUTLINE_MAX_ITEMS = 500               # max items in an outline response
MAX_HEADING_LEVEL = 6                 # max_level default and hard depth limit
CONTEXT_LINES_DEFAULT = 2             # default context lines around a search hit
CONTEXT_LINES_MAX = 10                # cap on context lines around a search hit
HITS_PER_DOC_DEFAULT = 3              # default matches returned per document
HITS_PER_DOC_MAX = 10                 # cap on matches per document
MATCH_SCAN_MAX = 1000                 # cap on matches counted within one document
CONTEXT_LINE_MAX_CHARS = 400          # display cap for one before/after line
TURN_CONTEXT_TURNS_MAX = 3            # max conversation turns attached either side
TURN_CONTEXT_CHARS = _SEARCH_SNIPPET_CHARS   # display cap per adjacent turn (reuses the existing value)
CANDIDATE_MAX_NOT_FOUND = 5           # similar headings attached to a 404 response
CANDIDATE_MAX_AMBIGUOUS = 10          # candidates reported when one name matches many
CANDIDATE_MIN_SCORE = 0.20            # below this a heading is not counted as similar
SECTION_DIFF_MAX = 50                 # cap on each of sections_added/removed/changed
SUMMARY_MAX_LINES = 20000             # skip the change summary past this many lines
SUMMARY_TIME_BUDGET_MS = 2000         # time budget for computing a change summary
HEADING_TITLE_MAX = 300               # heading text is truncated at this length


# ── 2-1. Canonical text and coordinates ─────────────────────────────────────────

def canonical_text(raw: str) -> str:
    """Canonical text: BOM stripped and every newline normalised to a single ``\\n``.

    Line numbers must not depend on which OS saved the document. If the original was
    CRLF the normalised character count is lower than the original byte count — correct.
    """
    text = raw or ""
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_canonical(path: Any) -> Optional[str]:
    """Read one file as canonical text, or None if it cannot be read.

    Bytes that are not valid UTF-8 are replaced and reading continues, so one file with
    broken encoding cannot kill the whole lookup (L0003 §5).
    """
    try:
        raw = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    return canonical_text(raw.decode("utf-8", errors="replace"))


def split_lines(text: str) -> list[str]:
    """Split canonical text into lines.

    **A trailing newline does NOT add one more line.** Leaving this undefined makes the
    same file flip between 1217 and 1218 lines (L0003 §2-1).
    """
    parts = (text or "").split("\n")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def line_offsets(lines: list[str]) -> list[int]:
    """``offsets[i]`` = 0-based char offset of the first character on line ``i+1``."""
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1  # +1 for the newline
    return offsets


def frontmatter_span(text: str) -> tuple[int, int]:
    """``(body_line_start, frontmatter_chars)``.

    ``(1, 0)`` when there is no frontmatter. If the closing line never appears the file is
    treated as having none — better than hiding the whole document as frontmatter (L0003 §2-1).
    """
    m = _FRONTMATTER.match(text or "")
    if m is None:
        return 1, 0
    consumed = text[: m.end()]
    newlines = consumed.count("\n")
    # The closing line's newline belongs to the frontmatter, so body_line_start is the line
    # *after* the closing line. Even when the file ends without a newline and `\n?` matched
    # nothing, the body still starts on the next line, so it is pushed one further.
    if consumed.endswith("\n"):
        return newlines + 1, m.end()
    return newlines + 2, m.end()


def content_sha256(text: str) -> str:
    """Fingerprint of the **entire** canonical text, frontmatter included.

    ``documents._normalise_markdown_for_fingerprint`` is deliberately NOT used: it exists
    to detect duplicate submissions and strips some frontmatter keys and trailing space.
    What is needed here is "is the file I am reading now the same file as before?", so one
    differing trailing space must produce a different fingerprint (L0003 §2-1).
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── 2-2. Heading scan ───────────────────────────────────────────────────────────
#
# Why code fences are honoured: these project documents quote Markdown constantly. When a
# TR says "write it like this" and shows `## Changed Files` inside a fence, a parser that
# ignores fences counts that line as a real heading and shifts every later section by one.
_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
# ATX heading: 0-3 leading spaces then 1-6 `#`. After the `#` run there must be at least
# one space/tab or end of line (a `#tag` is not a heading). 4+ spaces means a code block.
_ATX_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+(?P<title>.*))?$")
# A trailing run of closing `#` (` ###`).
_CLOSING_HASH_RE = re.compile(r"(?:^|[ \t])#+[ \t]*$")


def _heading_title(raw: str) -> str:
    title = (raw or "").strip()
    title = _CLOSING_HASH_RE.sub("", title).strip()
    if len(title) > HEADING_TITLE_MAX:
        title = title[:HEADING_TITLE_MAX]
    return title


def scan_headings(
    text: str, body_line_start: int, lines: Optional[list[str]] = None
) -> list[dict]:
    """Scan ATX heading lines from the end of the frontmatter, skipping code fences.

    - Opening fence: 0-3 spaces then 3 or more consecutive ````` or ``~``.
    - Closing fence: the **same character**, **no shorter** than the opening fence, with
      no info string after it.
    - An unclosed fence runs to the end of the document (same as CommonMark).
    - Setext headings are not counted: ``---`` is spelled the same as the frontmatter fence
      and a rule, and the existing parsers (``tr_scope_service``, ``test_run_service``) are ATX-only.
    - A heading with empty text (a bare ``##`` line) still counts. Dropping it would shift
      every later ``section_id``.
    """
    if lines is None:
        lines = split_lines(text)
    fence_char: Optional[str] = None
    fence_len = 0
    headings: list[dict] = []
    for i in range(max(1, body_line_start), len(lines) + 1):
        line = lines[i - 1]
        fence = _FENCE_RE.match(line)
        if fence_char is None:
            if fence is not None:
                fence_char = fence.group("fence")[0]
                fence_len = len(fence.group("fence"))
                continue
        else:
            if (
                fence is not None
                and fence.group("fence")[0] == fence_char
                and len(fence.group("fence")) >= fence_len
                and not fence.group("info").strip()
            ):
                fence_char = None
            continue
        atx = _ATX_RE.match(line)
        if atx is not None:
            headings.append({
                "line": i,
                "level": len(atx.group("hashes")),
                "title": _heading_title(atx.group("title") or ""),
            })
    return headings


# ── 2-3. Section boundaries ─────────────────────────────────────────────────────

def build_tree(headings: list[dict]) -> list[dict]:
    """Attach ``section_id``, ``parent_id`` and ``heading_path`` to a heading list.

    When ``####`` follows ``##`` directly, the parent of ``####`` is that ``##``. No absent
    ``###`` is invented, and ``level`` is carried exactly as written. A document starting
    at ``###`` simply has a null ``parent_id`` — that is not an error (L0003 §2-3).
    """
    items: list[dict] = []
    stack: list[dict] = []
    for n, h in enumerate(headings, start=1):
        while stack and stack[-1]["level"] >= h["level"]:
            stack.pop()
        parent = stack[-1] if stack else None
        heading_path = (parent["heading_path"] + [h["title"]]) if parent else [h["title"]]
        item = {
            "section_id": f"s{n}",
            "parent_id": parent["section_id"] if parent else None,
            "level": h["level"],
            "title": h["title"],
            "heading_path": heading_path,
            "line_start": h["line"],
        }
        items.append(item)
        stack.append(item)
    return items


def section_end_line(
    items: list[dict], idx: int, include_children: bool, document_lines: int
) -> int:
    """The section's last line (inclusive).

    When ``include_children`` is true it runs to the line before the next heading at the
    same or shallower level; when false, to the line before the next heading (own body
    only). Either way the end never precedes the start — back-to-back headings give a
    """
    me = items[idx]
    end = document_lines
    for j in range(idx + 1, len(items)):
        if include_children:
            if items[j]["level"] <= me["level"]:
                end = items[j]["line_start"] - 1
                break
        else:
            end = items[j]["line_start"] - 1
            break
    return max(end, me["line_start"])


def enclosing_section(items: list[dict], line: int) -> Optional[dict]:
    """The last heading at or before the start line = the deepest ancestor.

    None when there is none (inside frontmatter, or before the first heading) — not an
    error. Every heading-less document (requirement R) falls in this case.
    """
    best: Optional[dict] = None
    for it in items:
        if it["line_start"] <= line:
            best = it
        else:
            break
    return best


class DocumentText:
    """One canonical text and every coordinate computed on top of it.

    **Every section, outline and summary calculation runs on the single text this object
    holds**, because reading the same file differently yields different numbers (L0003 §2-1).
    """

    def __init__(self, text: str):
        self.text = text or ""
        self.lines = split_lines(self.text)
        self.offsets = line_offsets(self.lines)
        self.document_lines = len(self.lines)
        self.document_chars = len(self.text)
        self.body_line_start, self.frontmatter_chars = frontmatter_span(self.text)
        self._sha: Optional[str] = None
        self._items: Optional[list[dict]] = None

    # -- construction --------------------------------------------------------
    @classmethod
    def from_path(cls, path: Any) -> Optional["DocumentText"]:
        text = read_canonical(path)
        return None if text is None else cls(text)

    @classmethod
    def from_raw(cls, raw: str) -> "DocumentText":
        return cls(canonical_text(raw))

    # -- derived values ------------------------------------------------------
    @property
    def content_sha256(self) -> str:
        if self._sha is None:
            self._sha = content_sha256(self.text)
        return self._sha

    @property
    def items(self) -> list[dict]:
        if self._items is None:
            self._items = build_tree(
                scan_headings(self.text, self.body_line_start, self.lines)
            )
        return self._items

    @property
    def section_total(self) -> int:
        return len(self.items)

    # -- coordinates ---------------------------------------------------------
    def char_start_of(self, line: int) -> int:
        if self.document_lines == 0:
            return 0
        if line <= 1:
            return 0
        if line > self.document_lines:
            return self.document_chars
        return self.offsets[line - 1]

    def char_end_of(self, line: int) -> int:
        """End of ``line`` (exclusive), so one section's end equals the next one's start."""
        if line >= self.document_lines:
            return self.document_chars
        return self.offsets[line]

    def line_containing(self, char_pos: int) -> int:
        if self.document_lines == 0:
            return 1
        pos = max(0, min(int(char_pos), max(0, self.document_chars - 1)))
        return max(1, bisect_right(self.offsets, pos))

    def slice_lines(self, line_start: int, line_end: int) -> str:
        return self.text[self.char_start_of(line_start): self.char_end_of(line_end)]

    def heading_line_text(self, item: Optional[dict]) -> Optional[str]:
        if not item:
            return None
        line = item["line_start"]
        if 1 <= line <= self.document_lines:
            return self.lines[line - 1]
        return None

    def index_of(self, item: dict) -> int:
        return self.items.index(item)

    def find_by_section_id(self, section_id: str) -> Optional[dict]:
        for it in self.items:
            if it["section_id"] == section_id:
                return it
        return None

    # -- body coordinate → file coordinate (L0003 §2-1 / §2-7) ---------------
    def body_char_to_file(self, body_char: int) -> int:
        return int(body_char) + self.frontmatter_chars

    def body_line_to_file(self, body_line: int) -> int:
        return int(body_line) + self.body_line_start - 1


# ── 2-4. Matching heading names ─────────────────────────────────────────────────

_WS_COLLAPSE = re.compile(r"\s+")
_TRAILING_COLON = re.compile(r"[:：]$")
_NUM_PREFIX = re.compile(r"^\d+([.\-]\d+)*[.)]?\s+")
_KO_PREFIX = re.compile(r"^[가-힣]\.\s+")
_LEADING_HASHES = re.compile(r"^\s{0,3}#{1,6}\s*")


def normalize_heading(s: str) -> str:
    """n1 NFKC → n2 fold spaces → n3 drop backticks → n4 casefold → n5 drop one trailing ':'.

    **Emphasis markers (``*``, ``_``) are NOT stripped.** ``_`` is common in filenames and
    identifiers, so stripping it would make ``client_src`` and ``clientsrc`` the same
    heading. Only backticks go, because they never carry meaning in a heading (L0003 §2-4).
    """
    t = unicodedata.normalize("NFKC", s or "")
    t = _WS_COLLAPSE.sub(" ", t).strip()
    t = t.replace("`", "")
    t = t.casefold()
    t = _TRAILING_COLON.sub("", t)
    return t.strip()


def strip_number_prefix(t: str) -> str:
    """Strip a numbering prefix: ``2.1 ``, ``5. ``, ``3) ``, or a Korean syllable prefix."""
    t = _NUM_PREFIX.sub("", t or "")
    t = _KO_PREFIX.sub("", t)
    return t.strip()


def _strip_heading_marks(s: str) -> str:
    return _LEADING_HASHES.sub("", s or "").strip()


_ALIAS_MAP: Optional[dict[str, str]] = None


def alias_map() -> dict[str, str]:
    """Normalised alias → the group's representative name.

    **No new constant is defined; the existing values are imported.** Retyping them lets
    one side drift (L0003 §2-4). There are no Japanese aliases (0355 T0009 decided against).

    This table is used for **lookup only**. It does not loosen ``tr_scope_service``'s report
    section check one bit — that parser still uses its own regex and rejects a numbered
    ``## 5. Changed Files``. Assuming lookup's tolerance implies the report format is fine gets a TR rejected.
    """
    global _ALIAS_MAP
    if _ALIAS_MAP is not None:
        return _ALIAS_MAP
    groups: list[tuple[str, ...]] = []
    try:
        from modules.flow_gate.services import tr_scope_service

        groups.append((
            _strip_heading_marks(tr_scope_service.SECTION_HEADING),
            _strip_heading_marks(tr_scope_service.SECTION_HEADING_EN),
        ))
    except Exception:  # noqa: BLE001 — lookup must survive even with no alias table
        pass
    try:
        from modules.flow_gate.services import test_run_service

        groups.append(tuple(test_run_service.TEST_CASES_SECTION_NAMES))
        groups.append(tuple(test_run_service.SETUP_SECTION_NAMES))
        groups.append(tuple(test_run_service.TEARDOWN_SECTION_NAMES))
    except Exception:  # noqa: BLE001
        pass

    mapping: dict[str, str] = {}
    for grp in groups:
        keys = [normalize_heading(name) for name in grp if name]
        keys = [k for k in keys if k]
        if not keys:
            continue
        canon = keys[0]
        for key in keys:
            mapping[key] = canon
            mapping[strip_number_prefix(key)] = canon
    _ALIAS_MAP = mapping
    return mapping


def reset_alias_map() -> None:
    """Clear the alias-table cache (test helper)."""
    global _ALIAS_MAP
    _ALIAS_MAP = None


def apply_alias(t: str) -> str:
    return alias_map().get(t, t)


# Match stages — tried top down, stopping at the first that hits.
#   M1 raw, no normalisation / M2 normalize_heading / M3 M2+aliases / M4 M3+strip numbering
_STAGES = (1, 2, 3, 4)


def match_key(stage: int, s: str) -> str:
    if stage == 1:
        return s or ""
    normalized = normalize_heading(s)
    if stage == 2:
        return normalized
    if stage == 3:
        return apply_alias(normalized)
    return apply_alias(strip_number_prefix(normalized))


def resolve_by_name(items: list[dict], requested: str) -> list[dict]:
    """Find a section by name (or by a ``>``-joined heading path).

    The stages run in order rather than all at once so that, when a document holds both
    ``2.1 Structure`` and ``Structure``, asking for ``2.1 Structure`` returns exactly that
    one. Stripping numbering up front would match both and be needlessly ambiguous (L0003 §2-4).
    """
    requested = requested or ""
    if ">" in requested:
        return _resolve_by_path(items, requested)
    for stage in _STAGES:
        want = match_key(stage, requested)
        hits = [it for it in items if match_key(stage, it["title"]) == want]
        if hits:
            return hits
    return []


def _resolve_by_path(items: list[dict], requested: str) -> list[dict]:
    parts = [p.strip() for p in requested.split(">")]
    parts = [p for p in parts if p]
    if not parts:
        return []
    for stage in _STAGES:
        want = [match_key(stage, p) for p in parts]
        hits = []
        for it in items:
            path = [match_key(stage, x) for x in it["heading_path"]]
            if len(path) >= len(want) and path[-len(want):] == want:
                hits.append(it)
        if hits:
            return hits
    return []


def _bigrams(x: str) -> set[str]:
    return {x[i:i + 2] for i in range(len(x) - 1)}


def similarity(a: str, b: str) -> float:
    """Dice coefficient over character bigrams.

    With a zero denominator (both one character or shorter) there is no division: 1.0 if
    """
    ba, bb = _bigrams(a or ""), _bigrams(b or "")
    denom = len(ba) + len(bb)
    if denom == 0:
        return 1.0 if (a or "") == (b or "") else 0.0
    return 2.0 * len(ba & bb) / denom


def similar_candidates(
    items: list[dict], requested: str, limit: int = CANDIDATE_MAX_NOT_FOUND
) -> list[dict]:
    """Pick similar headings, document order first, then descending score.

    Anything below ``CANDIDATE_MIN_SCORE`` is dropped. If nothing remains the list is empty and the 404 stands.
    """
    want = normalize_heading(requested)
    scored: list[tuple[float, int, dict]] = []
    for idx, it in enumerate(items):
        score = similarity(want, normalize_heading(it["title"]))
        if score >= CANDIDATE_MIN_SCORE:
            scored.append((score, idx, it))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [it for _, _, it in scored[:limit]]


# ── 2-6. Truncation ─────────────────────────────────────────────────────────────

def cut_to_limit(
    doc: DocumentText, line_start: int, line_end: int, max_chars: int
) -> tuple[int, bool]:
    """``(last line included, was it truncated)``.

    Only whole lines that fit under the cap are returned, so the real character count can
    be below ``max_chars``. **If the very first line already exceeds the cap it is returned
    whole** — otherwise nothing would come back and ``next_locator`` would point at the same
    place, repeating the identical request forever (L0003 §2-6).
    """
    taken = 0
    cursor = line_start
    while cursor <= line_end:
        need = len(doc.lines[cursor - 1]) + 1  # newline included
        if taken + need > max_chars and cursor > line_start:
            break
        taken += need
        cursor += 1
    return cursor - 1, cursor <= line_end


# ── Locators (P0002 §1-1) ───────────────────────────────────────────────────────

def make_ref(doc_id: str, revision_no: int, line_start: int, line_end: int) -> str:
    return f"{doc_id}@r{revision_no}#L{line_start}-{line_end}"


def build_locator(
    doc: DocumentText,
    doc_id: str,
    revision_no: int,
    line_start: int,
    line_end: int,
    enclosing: Optional[dict] = None,
    char_start: Optional[int] = None,
    char_end: Optional[int] = None,
) -> dict:
    """One P0002 §1-1 locator set — same shape whether asked by name or by line."""
    cs = doc.char_start_of(line_start) if char_start is None else char_start
    ce = doc.char_end_of(line_end) if char_end is None else char_end
    return {
        "doc_id": doc_id,
        "revision_no": revision_no,
        "ref": make_ref(doc_id, revision_no, line_start, line_end),
        "unit": "line",
        "section_id": enclosing["section_id"] if enclosing else None,
        "heading_path": list(enclosing["heading_path"]) if enclosing else [],
        "level": enclosing["level"] if enclosing else None,
        "line_start": line_start,
        "line_end": line_end,
        "char_start": cs,
        "char_end": ce,
    }


def build_turn_locator(
    doc_id: str, revision_no: int, turn_seq: int, char_start: int, char_end: int
) -> dict:
    """A conversation turn's position (P0002 §1-4). Lines carry no meaning, so ``unit`` is ``turn``."""
    return {
        "doc_id": doc_id,
        "revision_no": revision_no,
        "ref": f"{doc_id}@r{revision_no}#T{turn_seq}",
        "unit": "turn",
        "section_id": None,
        "heading_path": [],
        "level": None,
        "turn_seq": turn_seq,
        "line_start": None,
        "line_end": None,
        "char_start": char_start,
        "char_end": char_end,
    }


def candidate_entry(doc_id: str, revision_no: int, item: dict, doc: DocumentText,
                    include_children: bool = True) -> dict:
    """One candidate row carried on a 404/ambiguous response (P0002 scenarios 5 and 6)."""
    idx = doc.items.index(item)
    line_end = section_end_line(doc.items, idx, include_children, doc.document_lines)
    return {
        "section_id": item["section_id"],
        "heading_path": list(item["heading_path"]),
        "level": item["level"],
        "line_start": item["line_start"],
        "line_end": line_end,
        "ref": make_ref(doc_id, revision_no, item["line_start"], line_end),
    }


def outline_items(doc: DocumentText, max_level: int) -> tuple[list[dict], bool]:
    """The outline response's ``items`` and ``truncated``.

    Filtering by ``max_level`` only decides **what goes in the response**; the numbering is
    the pre-filter ordinal. So a ``section_id`` from an outline fetched with ``max_level=2``
    opens the same section when passed straight to ``/section?section_id=`` (L0003 §2-2).
    """
    items = doc.items
    out: list[dict] = []
    truncated = False
    for idx, it in enumerate(items):
        if it["level"] > max_level:
            continue
        if len(out) >= OUTLINE_MAX_ITEMS:
            truncated = True
            break
        line_end = section_end_line(items, idx, True, doc.document_lines)
        char_start = doc.char_start_of(it["line_start"])
        char_end = doc.char_end_of(line_end)
        has_children = idx + 1 < len(items) and items[idx + 1]["level"] > it["level"]
        out.append({
            "section_id": it["section_id"],
            "parent_id": it["parent_id"],
            "level": it["level"],
            "title": it["title"],
            "heading_path": list(it["heading_path"]),
            "line_start": it["line_start"],
            "line_end": line_end,
            "char_start": char_start,
            "char_end": char_end,
            "lines": line_end - it["line_start"] + 1,
            "chars": char_end - char_start,
            "has_children": has_children,
        })
    return out, truncated


# ── 2-5. Resolving a given position into a real section ─────────────────────────

class LocatorError(Exception):
    """Position resolution failed. Carries ``status`` plus ``extra`` to attach to the response."""

    def __init__(self, status: int, message: str, extra: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


class Resolved:
    """Result of resolving a position."""

    def __init__(self, resolved_by: str, line_start: int, line_end: int,
                 item: Optional[dict], ambiguous: bool, candidates: list[dict]):
        self.resolved_by = resolved_by
        self.line_start = line_start
        self.line_end = line_end
        self.item = item
        self.ambiguous = ambiguous
        self.candidates = candidates


_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def resolve_locator(
    doc: DocumentText,
    doc_id: str,
    revision_no: int,
    *,
    section: Optional[str] = None,
    section_id: Optional[str] = None,
    lines: Optional[str] = None,
    chars: Optional[str] = None,
    include_children: bool = True,
) -> Resolved:
    """Turn **exactly one** of ``section``/``section_id``/``lines``/``chars`` into a section.

    Two or more, or none at all, is a 422 (P0002 scenario 19). The check order follows
    L0003 §4-1 — a changed revision (409) is already filtered out before this function.
    """
    given = [x for x in (section, section_id, lines, chars) if x is not None and x != ""]
    if len(given) != 1:
        raise LocatorError(
            422, "specify exactly one of section, section_id, lines, chars"
        )

    items = doc.items

    if section:
        hits = resolve_by_name(items, section)
        if not hits:
            cands = similar_candidates(items, section, CANDIDATE_MAX_NOT_FOUND)
            raise LocatorError(
                404,
                f"section not found: {section}",
                {
                    "doc_id": doc_id,
                    "revision_no": revision_no,
                    "section_total": len(items),
                    "candidates": [
                        candidate_entry(doc_id, revision_no, c, doc, include_children)
                        for c in cands
                    ],
                },
            )
        # On multiple hits, serve the one that comes first in the document and say it was
        # ambiguous. The point of this rule is not to stall unattended work (P0002 scenario 5).
        hits.sort(key=lambda it: (it["line_start"], items.index(it)))
        chosen = hits[0]
        idx = items.index(chosen)
        return Resolved(
            "section",
            chosen["line_start"],
            section_end_line(items, idx, include_children, doc.document_lines),
            chosen,
            len(hits) > 1,
            [
                candidate_entry(doc_id, revision_no, c, doc, include_children)
                for c in hits[:CANDIDATE_MAX_AMBIGUOUS]
            ] if len(hits) > 1 else [],
        )

    if section_id:
        chosen = doc.find_by_section_id(section_id)
        if chosen is None:
            # The id came from an outline, not from a human guess. If it is wrong the
            # revision changed; suggesting a nearby id would be wrong. section_total is
            # returned instead so the caller re-fetches the outline (L0003 §4-2).
            raise LocatorError(
                404,
                f"section not found: {section_id}",
                {
                    "doc_id": doc_id,
                    "revision_no": revision_no,
                    "section_total": len(items),
                    "candidates": [],
                },
            )
        idx = items.index(chosen)
        return Resolved(
            "section_id",
            chosen["line_start"],
            section_end_line(items, idx, include_children, doc.document_lines),
            chosen,
            False,
            [],
        )

    if lines:
        m = _RANGE_RE.match(lines)
        if m is None:
            raise LocatorError(422, "lines must look like 'a-b'")
        a, b = int(m.group(1)), int(m.group(2))
        if a < 1 or b < a:
            raise LocatorError(422, "lines must look like 'a-b' with 1 <= a <= b")
        if a > doc.document_lines:
            raise LocatorError(
                422,
                f"lines out of range: document has {doc.document_lines} lines",
                {"doc_id": doc_id, "revision_no": revision_no,
                 "document_lines": doc.document_lines},
            )
        line_start, line_end = a, min(b, doc.document_lines)
        return Resolved("lines", line_start, line_end,
                        enclosing_section(items, line_start), False, [])

    m = _RANGE_RE.match(chars or "")
    if m is None:
        raise LocatorError(422, "chars must look like 'c-d'")
    c, d = int(m.group(1)), int(m.group(2))
    if d <= c:
        raise LocatorError(422, "chars must look like 'c-d' with 0 <= c < d")
    if c >= doc.document_chars:
        raise LocatorError(
            422,
            f"chars out of range: document has {doc.document_chars} chars",
            {"doc_id": doc_id, "revision_no": revision_no,
             "document_chars": doc.document_chars},
        )
    # chars is end-exclusive, so the end line uses d - 1. Missing that one step stretches
    # the section one line too far. Starting/ending mid-line widens out to whole lines.
    line_start = doc.line_containing(c)
    line_end = doc.line_containing(min(d, doc.document_chars) - 1)
    line_end = max(line_end, line_start)
    return Resolved("chars", line_start, line_end,
                    enclosing_section(items, line_start), False, [])


def clamp_max_chars(value: Optional[int]) -> int:
    """Clamp ``max_chars`` into ``[1, SECTION_MAX_CHARS_LIMIT]``; 0 or less is a 422."""
    if value is None:
        return SECTION_MAX_CHARS_DEFAULT
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise LocatorError(422, "max_chars must be a positive integer")
    if v <= 0:
        raise LocatorError(422, "max_chars must be a positive integer")
    return min(v, SECTION_MAX_CHARS_LIMIT)


def clip_line(line: str, limit: int = CONTEXT_LINE_MAX_CHARS) -> str:
    """Context lines are carried **verbatim**, truncated with a ``…`` past the cap.

    Unlike a snippet, whitespace is not folded — the line's shape is what locates it.
    """
    if len(line) <= limit:
        return line
    return line[:limit] + "…"
