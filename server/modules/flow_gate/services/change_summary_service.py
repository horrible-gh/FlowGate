"""The change summary attached to a save response (group 0370, P0002 scenarios 14-16 / L0003 §2-9).

The "before" side is the backup file the save already creates (`previous_revision_path`), and
the "after" side is the file just written. This summary is **stored nowhere** — no table and
no column is added; it is returned in the response only (P0002 §5).

The most important rule: **whatever goes wrong while building the summary, the save is
already done.** Raising a failure here makes a worker think the save failed and submit the
same document again (P0002 scenario 14). So every failure converges on the single
`{"changed": null, "error": "summary unavailable"}`.
"""
from __future__ import annotations

import time
from difflib import SequenceMatcher
from typing import Any, Optional

from modules.flow_gate.services.document_outline_service import (
    SECTION_DIFF_MAX,
    SUMMARY_MAX_LINES,
    SUMMARY_TIME_BUDGET_MS,
    DocumentText,
    make_ref,
    normalize_heading,
    read_canonical,
    section_end_line,
)

SUMMARY_UNAVAILABLE: dict = {"changed": None, "error": "summary unavailable"}


def _unavailable() -> dict:
    return dict(SUMMARY_UNAVAILABLE)


def _side(doc: DocumentText, revision_no: int) -> dict:
    return {
        "revision_no": revision_no,
        "chars": doc.document_chars,
        "lines": doc.document_lines,
        "section_total": doc.section_total,
        "content_sha256": doc.content_sha256,
    }


def _common_line_flags(before: list[str], after: list[str]) -> tuple[list[bool], list[bool]]:
    """Mark, per line, whether it belongs to the common subsequence of two line lists.

    Line comparison is **literal**: no trimming of surrounding whitespace, no case folding —
    changing only the indentation is still a change (L0003 §2-9).

    The common part is measured with ``difflib.SequenceMatcher``'s matching blocks. True LCS
    dynamic programming would be 400 million cells at the ``SUMMARY_MAX_LINES`` cap (20,000
    lines), which does not fit ``SUMMARY_TIME_BUDGET_MS`` (2 seconds). ``autojunk`` MUST be
    off: with it on, frequently occurring lines (blank ones, say) in a document over 200 lines
    get classified as junk wholesale and far more lines are reported as changed than really were.
    """
    b_common = [False] * len(before)
    a_common = [False] * len(after)
    matcher = SequenceMatcher(None, before, after, autojunk=False)
    for i, j, size in matcher.get_matching_blocks():
        for k in range(size):
            b_common[i + k] = True
            a_common[j + k] = True
    return b_common, a_common


def _line_stats(before: list[str], after: list[str]) -> dict:
    b_common, a_common = _common_line_flags(before, after)
    common = sum(1 for flag in b_common if flag)
    # chars_added/chars_removed count **only characters within lines**, not newlines, so that a
    # person can check the arithmetic straight off the response (L0003 §2-9).
    return {
        "lines_removed": len(before) - common,
        "lines_added": len(after) - common,
        "chars_removed": sum(len(l) for l, f in zip(before, b_common) if not f),
        "chars_added": sum(len(l) for l, f in zip(after, a_common) if not f),
    }


def _section_bodies(doc: DocumentText) -> list[dict]:
    """Extract per section its ``key`` (heading path) and its own body lines only.

    Paired sections are compared on an **``include_children = false`` basis** (own body text
    only). Including descendants would mark every ancestor as "changed" when a single line of
    the deepest subheading is edited, making the summary useless (L0003 §2-9).

    Numbering prefixes are **NOT stripped** from the key. If ``## 2. Structure`` became
    ``## 3. Structure`` the document did change, so reporting it as changed is right. (Lookup's
    M4 stage exists to find things for a human asking loosely; here a machine compares two revisions — a different purpose.)
    """
    out: list[dict] = []
    for idx, it in enumerate(doc.items):
        line_end = section_end_line(doc.items, idx, False, doc.document_lines)
        out.append({
            "section_id": it["section_id"],
            "heading_path": list(it["heading_path"]),
            "level": it["level"],
            "line_start": it["line_start"],
            "line_end": line_end,
            "key": "/".join(normalize_heading(x) for x in it["heading_path"]),
            "lines": doc.lines[it["line_start"] - 1: line_end],
        })
    return out


def _entry(sec: dict, doc_id: str, revision_no: int) -> dict:
    return {
        "section_id": sec["section_id"],
        "heading_path": list(sec["heading_path"]),
        "level": sec["level"],
        "line_start": sec["line_start"],
        "line_end": sec["line_end"],
        "ref": make_ref(doc_id, revision_no, sec["line_start"], sec["line_end"]),
    }


def _pair_sections(before: list[dict], after: list[dict]) -> tuple[list[dict], list[dict], list[tuple[dict, dict]]]:
    """Duplicate keys within one revision are **paired in order of appearance** (1st before ↔ 1st after).

    Whatever is left unpaired counts as added or removed.
    """
    buckets: dict[str, list[dict]] = {}
    for sec in before:
        buckets.setdefault(sec["key"], []).append(sec)
    used: dict[str, int] = {}

    added: list[dict] = []
    pairs: list[tuple[dict, dict]] = []
    matched_before: set[int] = set()
    for sec in after:
        seen = used.get(sec["key"], 0)
        bucket = buckets.get(sec["key"], [])
        if seen < len(bucket):
            partner = bucket[seen]
            used[sec["key"]] = seen + 1
            matched_before.add(id(partner))
            pairs.append((partner, sec))
        else:
            added.append(sec)
    removed = [sec for sec in before if id(sec) not in matched_before]
    return added, removed, pairs


def build(
    *,
    doc_id: str,
    after_path: Any,
    after_revision_no: int,
    before_path: Any = None,
    before_revision_no: Optional[int] = None,
) -> dict:
    """Build the change summary by comparing before and after. Every failure is ``summary unavailable``.

    No ``before_path`` means a first registration: with no prior revision to compare, ``before``
    is ``null``, ``changed`` is true, and everything counts as added (P0002 scenario 15).
    """
    started = time.monotonic()

    def _over_budget() -> bool:
        return (time.monotonic() - started) * 1000.0 > SUMMARY_TIME_BUDGET_MS

    try:
        after_text = read_canonical(after_path)
        if after_text is None:
            return _unavailable()
        after_doc = DocumentText(after_text)

        before_doc: Optional[DocumentText] = None
        if before_path:
            before_text = read_canonical(before_path)
            if before_text is None:
                # The backup file should exist but is unreadable → give up on the summary only.
                return _unavailable()
            before_doc = DocumentText(before_text)

        if after_doc.document_lines > SUMMARY_MAX_LINES or (
            before_doc is not None and before_doc.document_lines > SUMMARY_MAX_LINES
        ):
            return _unavailable()

        after_side = _side(after_doc, after_revision_no)
        before_side = (
            _side(before_doc, before_revision_no if before_revision_no is not None else 0)
            if before_doc is not None else None
        )

        changed = before_doc is None or (
            before_doc.content_sha256 != after_doc.content_sha256
        )

        summary: dict = {
            "changed": changed,
            "before": before_side,
            "after": after_side,
            "lines_added": 0,
            "lines_removed": 0,
            "chars_added": 0,
            "chars_removed": 0,
            "sections_added": [],
            "sections_removed": [],
            "sections_changed": [],
            "sections_unchanged": 0,
            "truncated": False,
        }

        if not changed:
            # Identical fingerprints mean all counts are 0 and every section is unchanged. The
            # revision number still increments — this rule does not alter save behaviour (P0002 scenario 16).
            summary["sections_unchanged"] = after_doc.section_total
            return summary

        before_lines = before_doc.lines if before_doc is not None else []
        summary.update(_line_stats(before_lines, after_doc.lines))
        if _over_budget():
            return _unavailable()

        before_secs = _section_bodies(before_doc) if before_doc is not None else []
        after_secs = _section_bodies(after_doc)
        added, removed, pairs = _pair_sections(before_secs, after_secs)

        sections_added = [_entry(s, doc_id, after_revision_no) for s in added]
        # A removed section's line numbers and ref are relative to the **old** revision. That is
        # exactly what the `@r` in a ref is for (L0003 §2-9).
        old_rev = before_revision_no if before_revision_no is not None else 0
        sections_removed = [_entry(s, doc_id, old_rev) for s in removed]

        sections_changed: list[dict] = []
        unchanged = 0
        for old, new in pairs:
            if old["lines"] == new["lines"]:
                unchanged += 1
                continue
            stats = _line_stats(old["lines"], new["lines"])
            entry = _entry(new, doc_id, after_revision_no)
            entry["lines_added"] = stats["lines_added"]
            entry["lines_removed"] = stats["lines_removed"]
            sections_changed.append(entry)
            if _over_budget():
                return _unavailable()

        truncated = (
            len(sections_added) > SECTION_DIFF_MAX
            or len(sections_removed) > SECTION_DIFF_MAX
            or len(sections_changed) > SECTION_DIFF_MAX
        )
        summary["sections_added"] = sections_added[:SECTION_DIFF_MAX]
        summary["sections_removed"] = sections_removed[:SECTION_DIFF_MAX]
        summary["sections_changed"] = sections_changed[:SECTION_DIFF_MAX]
        summary["sections_unchanged"] = unchanged
        summary["truncated"] = truncated
        return summary
    except Exception:  # noqa: BLE001 — the save is already done; only the summary is abandoned
        return _unavailable()
