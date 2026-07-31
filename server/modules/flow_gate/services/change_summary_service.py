"""저장 응답에 붙는 변경 요약 (group 0370, P0002 시나리오 14~16 / L0003 §2-9).

견줄 "저장 전"은 저장 과정이 이미 만들어 두는 백업 파일(`previous_revision_path`)이고,
"저장 후"는 방금 쓴 파일이다. 이 요약은 **어디에도 저장하지 않는다** — 표도 컬럼도 늘리지
않고 응답으로만 돌려준다(P0002 §5).

가장 중요한 규칙: **요약을 만들다 무슨 일이 나도 저장은 이미 끝난 것이다.** 여기서
실패를 오류로 올리면 작업자가 저장이 실패한 줄 알고 같은 문서를 또 올린다(P0002
시나리오 14). 그래서 모든 실패는 `{"changed": null, "error": "summary unavailable"}`
한 가지로 수렴한다.
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
    """두 줄 목록의 공통 부분수열에 속하는지를 줄마다 표시한다.

    줄 비교는 **글자 그대로**다. 앞뒤 공백을 다듬거나 대소문자를 무시하지 않는다 —
    들여쓰기만 고친 것도 고친 것이다(L0003 §2-9).

    공통 부분은 ``difflib.SequenceMatcher`` 의 매칭 블록으로 잰다. 참 LCS 의 동적계획법은
    ``SUMMARY_MAX_LINES``(2만 줄) 상한에서 4억 칸이라 ``SUMMARY_TIME_BUDGET_MS``(2초)
    예산에 들어가지 않는다. ``autojunk`` 는 반드시 꺼야 한다 — 켜 두면 200줄이 넘는
    문서에서 자주 나오는 줄(빈 줄 등)이 통째로 "쓰레기"로 분류돼 실제보다 훨씬 많은
    줄이 바뀐 것으로 잡힌다.
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
    # chars_added/chars_removed 는 **줄 안의 글자만** 세고 개행은 세지 않는다. 사람이
    # 응답을 보고 검산할 수 있어야 하기 때문이다(L0003 §2-9).
    return {
        "lines_removed": len(before) - common,
        "lines_added": len(after) - common,
        "chars_removed": sum(len(l) for l, f in zip(before, b_common) if not f),
        "chars_added": sum(len(l) for l, f in zip(after, a_common) if not f),
    }


def _section_bodies(doc: DocumentText) -> list[dict]:
    """구간마다 ``key``(제목 경로)와 제 몸통 줄만 뽑아 둔다.

    짝지어진 구간의 본문은 **``include_children = false`` 기준**(제 몸통 글자만)으로
    견준다. 하위까지 넣어 견주면 맨 끝 소제목 한 줄만 고쳐도 그 위 모든 조상이
    "고쳤다" 로 잡혀 요약이 쓸모없어진다(L0003 §2-9).

    키에서 **번호 접두사를 떼지 않는다.** ``## 2. 구성`` 이 ``## 3. 구성`` 으로 바뀌었으면
    그것은 문서가 달라진 것이므로 "고쳤다" 로 잡히는 편이 맞다. (조회의 M4 단계는 사람이
    대충 물어도 찾아 주려는 것이고, 여기는 기계가 두 판을 견주는 자리라 목적이 다르다.)
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
    """같은 키가 한 판 안에 여럿이면 **등장 순서대로 짝짓는다**(before 1번째 ↔ after 1번째).

    짝이 없는 쪽이 추가/삭제다.
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
    """저장 전후를 견주어 변경 요약을 만든다. 실패는 전부 ``summary unavailable``.

    ``before_path`` 가 없으면 신규 등록이다 — 견줄 옛 판이 없으므로 ``before`` 는
    ``null``, ``changed`` 는 참, 전부 추가로 집계한다(P0002 시나리오 15).
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
                # 백업 파일이 있어야 하는데 읽히지 않는다 → 요약만 포기한다.
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
            # 지문이 같으면 숫자는 전부 0 이고 구간은 전부 그대로다. 개정 번호는 그래도
            # 올라간다 — 이 규칙은 저장 동작을 바꾸지 않는다(P0002 시나리오 16).
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
        # 사라진 구간의 줄 번호와 ref 는 **옛 판** 기준이다. ref 의 `@r` 이 바로 이걸
        # 구분하라고 있는 것이다(L0003 §2-9).
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
    except Exception:  # noqa: BLE001 — 저장은 이미 끝났다. 요약만 포기한다.
        return _unavailable()
