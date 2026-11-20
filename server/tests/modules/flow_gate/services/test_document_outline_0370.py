"""Document-query tools: calculation-rule unit tests (group 0370, P0002 / L0003).

Verify coordinates shared by /outline, /section, search match locations, and saved-change
summaries. A failure here would make these four views describe the same location differently.

Specifically cover two gaps L0003 explicitly identified as unverified:

* §2-2: 0370.0002-P has no line-leading # inside code fences, so use _FENCED_DOC
  to verify that headings quoted inside a fence are ignored.
* §2-1: body-to-file conversion is an identity without frontmatter and can be omitted
  unnoticed. Use _FRONTMATTER_DOC to exercise the nontrivial conversion.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import document_outline_service as outline  # noqa: E402
from modules.flow_gate.services import change_summary_service  # noqa: E402


# ── Fixtures ──

# Five frontmatter lines plus body: missing body-to-file conversion fails here.
_FRONTMATTER_DOC = (
    "---\n"          # 1
    "group_id: 0370\n"   # 2
    "type: TR\n"     # 3
    "title: 좌표 시험\n"  # 4
    "---\n"          # 5
    "\n"             # 6
    "# 뿌리\n"        # 7
    "\n"             # 8
    "머리말 뒤 첫 문단.\n"  # 9
    "\n"             # 10
    "## 가지\n"       # 11
    "\n"             # 12
    "가지 본문 한 줄.\n"   # 13
)

# Fenced code containing line-leading # characters, required by L0003 §2-2.
_FENCED_DOC = (
    "# 진짜 제목\n"          # 1  s1
    "\n"                    # 2
    "```markdown\n"          # 3
    "## 이건 인용일 뿐이다\n"  # 4: must not count as a heading
    "### 이것도\n"           # 5: must not count as a heading
    "```\n"                  # 6
    "\n"                    # 7
    "## 진짜 두 번째\n"       # 8  s2
    "\n"                    # 9
    "~~~\n"                  # 10
    "# 물결 울타리 안\n"      # 11: must not count as a heading
    "~~~\n"                  # 12
    "\n"                    # 13
    "#### 진짜 세 번째\n"     # 14 s3 (skips a depth level)
    "\n"                    # 15
    "끝.\n"                  # 16
)


def _doc(text: str) -> outline.DocumentText:
    return outline.DocumentText(outline.canonical_text(text))


# ── 2-1. Canonical text and coordinates ──

def test_frontmatter_boundary_and_document_size():
    doc = _doc(_FRONTMATTER_DOC)
    assert doc.document_lines == 13
    assert doc.body_line_start == 6, "닫는 `---` 의 다음 줄이 본문 시작이다"
    assert doc.frontmatter_chars == len("---\ngroup_id: 0370\ntype: TR\ntitle: 좌표 시험\n---\n")
    assert doc.document_chars == len(_FRONTMATTER_DOC)


def test_trailing_newline_is_not_an_extra_line():
    """A final newline does not imply an extra line after it (L0003 §2-1)."""
    assert _doc("a\nb\n").document_lines == 2
    assert _doc("a\nb").document_lines == 2, "개행 없이 끝나도 마지막 줄은 한 줄이다"
    assert _doc("").document_lines == 0
    assert _doc("").document_chars == 0


def test_crlf_and_bom_do_not_change_any_number():
    """Saving the same document on a different OS must not change line numbers."""
    lf = _doc(_FRONTMATTER_DOC)
    crlf = _doc(_FRONTMATTER_DOC.replace("\n", "\r\n"))
    bom = _doc("﻿" + _FRONTMATTER_DOC)
    for other in (crlf, bom):
        assert other.document_lines == lf.document_lines
        assert other.document_chars == lf.document_chars
        assert other.body_line_start == lf.body_line_start
        assert other.content_sha256 == lf.content_sha256


def test_unclosed_frontmatter_is_treated_as_no_frontmatter():
    """Prefer this to treating the whole document as frontmatter and hiding it (L0003 §5)."""
    doc = _doc("---\ntitle: 안 닫힘\n\n# 제목\n")
    assert doc.body_line_start == 1
    assert doc.frontmatter_chars == 0
    assert [it["title"] for it in doc.items] == ["제목"]


def test_char_end_of_one_section_equals_char_start_of_the_next_sibling():
    """Exclusive-end convention: one sibling ends where the next starts (P0002 §1-2).

    This applies to siblings at equal depth. With include_children, a parent contains
    its children, so its end cannot equal a child start; P0002 examples s3/s4 are siblings.
    """
    doc = _doc("## 첫째\n\n가\n\n## 둘째\n\n나\n\n## 셋째\n\n다\n")
    items, _ = outline.outline_items(doc, outline.MAX_HEADING_LEVEL)
    assert items[0]["char_end"] == items[1]["char_start"]
    assert items[1]["char_end"] == items[2]["char_start"]
    assert items[2]["char_end"] == doc.document_chars, "마지막 구간은 문서 끝까지"


def test_body_coordinates_convert_to_file_coordinates():
    """Convert search body coordinates to file coordinates (L0003 §2-1 / §2-7).

    Without frontmatter this is an identity and omissions stay invisible.
    Use a document with frontmatter to exercise the conversion.
    """
    doc = _doc(_FRONTMATTER_DOC)
    body = doc.text[doc.frontmatter_chars:]
    needle = "가지 본문"
    body_char = body.index(needle)
    file_char = doc.body_char_to_file(body_char)
    assert doc.text[file_char: file_char + len(needle)] == needle
    assert doc.line_containing(file_char) == 13
    # Body line 8 is file line 13.
    assert doc.body_line_to_file(8) == 13


# ── 2-2. Heading scan ──

def test_headings_inside_a_code_fence_are_not_sections():
    """Exercise the separate fixture explicitly required by L0003 §2-2.

    A parser ignoring fences counts quoted ## lines as headings and shifts all subsequent
    section IDs. TR documents often quote `## 변경 파일` as a format example.
    """
    doc = _doc(_FENCED_DOC)
    assert [it["title"] for it in doc.items] == ["진짜 제목", "진짜 두 번째", "진짜 세 번째"]
    assert [it["line_start"] for it in doc.items] == [1, 8, 14]
    assert doc.section_total == 3


def test_fence_closes_only_on_the_same_char_and_not_shorter():
    """A closing fence uses the same character, is at least as long, and has no info string."""
    def titles(text):
        return [it["title"] for it in _doc(text).items]

    # A fence opened with ``` cannot close with ~~~.
    assert titles("```\n# 안\n~~~\n# 여전히 안\n```\n# 밖\n") == ["밖"]
    # A fence opened with ```` cannot close with the shorter ```.
    assert titles("````\n# 안\n```\n# 여전히 안\n````\n# 밖\n") == ["밖"]
    # A fence opened with ``` can close with ````: closing must be at least as long.
    assert titles("```\n# 안\n````\n# 밖\n") == ["밖"]
    # A line with a trailing info string is not a closing fence.
    assert titles("```\n# 안\n``` vue\n# 여전히 안\n```\n# 밖\n") == ["밖"]


def test_unclosed_fence_swallows_the_rest_of_the_document():
    doc = _doc("# 앞\n\n```\n# 안\n## 안\n")
    assert [it["title"] for it in doc.items] == ["앞"]


@pytest.mark.parametrize("line, expected", [
    ("# 제목", ["제목"]),
    ("###### 여섯", ["여섯"]),
    ("####### 일곱", []),          # Seven or more # characters do not form a heading
    ("#태그", []),                  # A heading requires whitespace after #
    ("    # 네 칸 들여쓰기", []),    # Four or more spaces indicate a code block
    ("   ### 세 칸", ["세 칸"]),     # Up to three spaces still allow a heading
    ("## 닫는 해시 ##", ["닫는 해시"]),
    ("##", [""]),                   # Count headings even when their text is empty
])
def test_atx_heading_edge_cases(line, expected):
    assert [it["title"] for it in _doc(line + "\n").items] == expected


def test_setext_heading_is_not_counted():
    """Adding setext headings only here would disagree with the existing parser (L0003 §2-2)."""
    assert _doc("제목처럼 보이는 줄\n===\n\n또 한 줄\n---\n").items == []


def test_empty_heading_still_advances_section_id():
    """Omitting this shifts the section_id of subsequent sections."""
    doc = _doc("# 하나\n\n##\n\n## 셋\n")
    assert [(it["section_id"], it["title"]) for it in doc.items] == [
        ("s1", "하나"), ("s2", ""), ("s3", "셋"),
    ]


def test_heading_title_is_clipped():
    long_title = "가" * (outline.HEADING_TITLE_MAX + 50)
    doc = _doc(f"# {long_title}\n")
    assert len(doc.items[0]["title"]) == outline.HEADING_TITLE_MAX


# ── 2-3. Section boundaries ──

def test_skipped_depth_keeps_the_real_parent():
    """When ## is immediately followed by ####, the ## heading is its parent."""
    doc = _doc(_FENCED_DOC)
    s2, s3 = doc.items[1], doc.items[2]
    assert s3["level"] == 4, "글에 적힌 깊이를 그대로 싣는다"
    assert s3["parent_id"] == s2["section_id"]
    assert s3["heading_path"] == ["진짜 제목", "진짜 두 번째", "진짜 세 번째"]


def test_first_heading_deeper_than_one_is_not_an_error():
    doc = _doc("### 시작부터 셋\n\n본문\n")
    assert doc.items[0]["parent_id"] is None


def test_include_children_changes_only_the_end_line():
    doc = _doc("## 부모\n\n본문\n\n### 자식\n\n자식 본문\n\n## 다음\n")
    items = doc.items
    with_children = outline.section_end_line(items, 0, True, doc.document_lines)
    own_body = outline.section_end_line(items, 0, False, doc.document_lines)
    assert with_children == 8, "같거나 얕은 다음 제목(## 다음, 9줄)의 앞줄까지"
    assert own_body == 4, "다음 제목(### 자식, 5줄)의 앞줄까지 — 제 몸통만"


def test_back_to_back_headings_give_a_one_line_section():
    doc = _doc("## A\n## B\n")
    assert outline.section_end_line(doc.items, 0, True, doc.document_lines) == 1


def test_section_id_numbering_ignores_the_max_level_filter():
    """A section_id from a filtered outline must open the same section through /section."""
    doc = _doc("# 1\n\n## 1.1\n\n### 1.1.1\n\n## 1.2\n")
    shallow, _ = outline.outline_items(doc, 2)
    assert [it["section_id"] for it in shallow] == ["s1", "s2", "s4"]
    assert doc.section_total == 4, "section_total 은 거르기 전 전체 개수다"


def test_document_without_headings_is_not_an_error():
    doc = _doc("그냥 산문 한 줄.\n또 한 줄.\n")
    items, truncated = outline.outline_items(doc, outline.MAX_HEADING_LEVEL)
    assert items == [] and truncated is False and doc.section_total == 0


def test_outline_truncates_at_the_item_cap():
    doc = _doc("".join(f"## 제목 {i}\n\n" for i in range(outline.OUTLINE_MAX_ITEMS + 20)))
    items, truncated = outline.outline_items(doc, outline.MAX_HEADING_LEVEL)
    assert len(items) == outline.OUTLINE_MAX_ITEMS and truncated is True
    assert doc.section_total == outline.OUTLINE_MAX_ITEMS + 20, "전체 개수는 그대로 말한다"


# ── 2-4. Heading-name matching ──

def test_normalisation_keeps_underscores_and_asterisks():
    """Removing this would conflate client_src and clientsrc (L0003 §2-4)."""
    assert outline.normalize_heading("client_src") != outline.normalize_heading("clientsrc")
    assert outline.normalize_heading("`코드`") == "코드", "백틱만 지운다"
    assert outline.normalize_heading("  두   칸  ") == "두 칸"
    assert outline.normalize_heading("결론:") == "결론"


def test_alias_table_is_read_from_the_existing_parsers():
    """Import the value instead of copying it, preventing one-sided changes and drift."""
    from modules.flow_gate.services import test_run_service, tr_scope_service

    outline.reset_alias_map()
    mapping = outline.alias_map()
    assert outline.normalize_heading("Changed Files") in mapping
    assert outline.normalize_heading(
        tr_scope_service.SECTION_HEADING.lstrip("# ")
    ) in mapping
    for name in test_run_service.TEST_CASES_SECTION_NAMES:
        assert outline.normalize_heading(name) in mapping


def test_english_alias_finds_the_korean_section_and_back():
    doc = _doc("## 변경 파일\n\n- a.py\n\n## 테스트 케이스\n\n- TC1\n")
    assert outline.resolve_by_name(doc.items, "Changed Files")[0]["title"] == "변경 파일"
    assert outline.resolve_by_name(doc.items, "Test Cases")[0]["title"] == "테스트 케이스"
    assert outline.resolve_by_name(doc.items, "변경 파일")[0]["title"] == "변경 파일"


def test_numbered_changed_files_reads_but_stays_unreportable_to_tr_scope():
    """Readable queries and passing TR validation are separate contracts (L0003 §2-4).

    Lookup strips number prefixes, but reported-section validation stays strict.
    Successful lookup does not imply a valid TR declaration format.
    """
    from modules.flow_gate.services import tr_scope_service

    doc = _doc("## 5. 변경 파일\n\n- a.py\n")
    hits = outline.resolve_by_name(doc.items, "변경 파일")
    assert hits and hits[0]["title"] == "5. 변경 파일", "조회는 찾아 준다"
    assert tr_scope_service._HEADING_RE.match("## 5. 변경 파일") is None, (
        "그러나 tr_scope 의 신고 섹션 판정은 여전히 이 제목을 받지 않는다"
    )


def test_exact_match_wins_before_the_number_stripping_stage():
    """When both `2.1 구성` and `구성` exist, requesting `2.1 구성` returns only that heading."""
    doc = _doc("## 2.1 구성\n\n가\n\n## 구성\n\n나\n")
    hits = outline.resolve_by_name(doc.items, "2.1 구성")
    assert [it["title"] for it in hits] == ["2.1 구성"]


def test_heading_path_syntax_pins_one_of_two_same_named_sections():
    doc = _doc("## 앞\n\n### 변경 파일\n\n가\n\n## 부록\n\n### 변경 파일\n\n나\n")
    hits = outline.resolve_by_name(doc.items, "부록 > 변경 파일")
    assert len(hits) == 1 and hits[0]["heading_path"] == ["부록", "변경 파일"]


def test_similar_candidates_are_scored_and_floored():
    doc = _doc("## 9. 결론\n\n가\n\n## 7. 이슈 및 설계 고려사항\n\n나\n\n## zzz\n\n다\n")
    cands = outline.similar_candidates(doc.items, "결론")
    assert [c["title"] for c in cands][:1] == ["9. 결론"]
    assert all(c["title"] != "zzz" for c in cands), "하한 미만은 버린다"
    assert outline.similarity("가", "가") == 1.0, "분모가 0이면 나눗셈을 하지 않는다"
    assert outline.similarity("가", "나") == 0.0


# ── 2-5. Locators ──

_DOC_ID = "flowgate.default.0370.9999-TR"


def test_lines_and_chars_and_name_resolve_to_the_same_span():
    """The comparison L0003 §2-5 names as the first implementation check."""
    doc = _doc(_FRONTMATTER_DOC)
    by_name = outline.resolve_locator(doc, _DOC_ID, 0, section="가지")
    lines = f"{by_name.line_start}-{by_name.line_end}"
    chars = (f"{doc.char_start_of(by_name.line_start)}-"
             f"{doc.char_end_of(by_name.line_end)}")

    by_lines = outline.resolve_locator(doc, _DOC_ID, 0, lines=lines)
    by_chars = outline.resolve_locator(doc, _DOC_ID, 0, chars=chars)
    for other in (by_lines, by_chars):
        assert (other.line_start, other.line_end) == (by_name.line_start, by_name.line_end)
    assert by_lines.resolved_by == "lines" and by_chars.resolved_by == "chars"


def test_chars_range_in_the_middle_of_a_line_widens_to_whole_lines():
    doc = _doc("첫 줄입니다\n두 번째 줄입니다\n세 번째 줄입니다\n")
    res = outline.resolve_locator(doc, _DOC_ID, 0, chars="2-9")
    assert (res.line_start, res.line_end) == (1, 2), "줄 가운데를 자르지 않는다"


def test_exactly_one_locator_is_required():
    doc = _doc(_FENCED_DOC)
    with pytest.raises(outline.LocatorError) as two:
        outline.resolve_locator(doc, _DOC_ID, 0, section_id="s1", lines="1-2")
    assert two.value.status == 422
    with pytest.raises(outline.LocatorError) as none:
        outline.resolve_locator(doc, _DOC_ID, 0)
    assert none.value.status == 422


def test_line_range_rules():
    doc = _doc(_FENCED_DOC)                     # 16 lines
    ok = outline.resolve_locator(doc, _DOC_ID, 0, lines="14-999")
    assert ok.line_end == 16, "끝만 넘치면 문서 끝으로 자른다"
    for bad in ("999-1000", "49-22", "0-3", "not-a-range"):
        with pytest.raises(outline.LocatorError) as exc:
            outline.resolve_locator(doc, _DOC_ID, 0, lines=bad)
        assert exc.value.status == 422


def test_missing_section_raises_404_with_candidates_and_section_total():
    doc = _doc("## 9. 결론\n\n가\n")
    with pytest.raises(outline.LocatorError) as exc:
        outline.resolve_locator(doc, _DOC_ID, 0, section="결론 요약")
    assert exc.value.status == 404
    assert exc.value.extra["section_total"] == 1
    assert exc.value.extra["candidates"][0]["section_id"] == "s1"


def test_unknown_section_id_gets_no_candidates():
    """IDs come from the outline; an invalid ID means a different revision, not a similar suggestion."""
    doc = _doc("## 하나\n\n가\n")
    with pytest.raises(outline.LocatorError) as exc:
        outline.resolve_locator(doc, _DOC_ID, 0, section_id="s99")
    assert exc.value.status == 404 and exc.value.extra["candidates"] == []
    assert exc.value.extra["section_total"] == 1


def test_ambiguous_name_reads_the_first_and_says_so():
    """Ambiguity must not stall unattended work: return a result while reporting the ambiguity."""
    doc = _doc("## 변경 파일\n\n가\n\n## 부록\n\n### 변경 파일\n\n나\n")
    res = outline.resolve_locator(doc, _DOC_ID, 2, section="변경 파일")
    assert res.ambiguous is True
    assert res.line_start == 1, "문서에서 먼저 나오는 것을 읽는다"
    assert [c["section_id"] for c in res.candidates] == ["s1", "s3"]
    assert res.candidates[0]["ref"].startswith(f"{_DOC_ID}@r2#L")


def test_enclosing_section_is_null_inside_the_frontmatter():
    doc = _doc(_FRONTMATTER_DOC)
    res = outline.resolve_locator(doc, _DOC_ID, 0, lines="2-3")
    loc = outline.build_locator(doc, _DOC_ID, 0, res.line_start, res.line_end, res.item)
    assert loc["section_id"] is None and loc["heading_path"] == [] and loc["level"] is None


# ── 2-6. Truncation and next_locator ──

def test_truncation_cuts_on_a_line_boundary_and_resumes_exactly():
    doc = _doc("".join(f"{i:03d} 줄 내용이 여기에 있습니다\n" for i in range(1, 41)))
    res = outline.resolve_locator(doc, _DOC_ID, 0, lines="1-40")
    last, truncated = outline.cut_to_limit(doc, res.line_start, res.line_end, 200)
    assert truncated is True and last < 40

    head = doc.slice_lines(1, last)
    assert head.endswith("\n"), "줄 가운데를 자르지 않는다"
    assert len(head) <= 200

    tail = doc.slice_lines(last + 1, 40)
    assert head + tail == doc.text, "이어 읽으면 원문이 그대로 복원된다"


def test_a_single_oversized_line_is_returned_whole():
    """Returning no character makes next_locator repeat the same request indefinitely."""
    doc = _doc("가" * 5000 + "\n두 번째 줄\n")
    last, truncated = outline.cut_to_limit(doc, 1, 2, 800)
    assert last == 1 and truncated is True


def test_max_chars_is_clamped_and_zero_is_rejected():
    assert outline.clamp_max_chars(None) == outline.SECTION_MAX_CHARS_DEFAULT
    assert outline.clamp_max_chars(10 ** 9) == outline.SECTION_MAX_CHARS_LIMIT
    for bad in (0, -1, "x"):
        with pytest.raises(outline.LocatorError) as exc:
            outline.clamp_max_chars(bad)
        assert exc.value.status == 422


def test_context_line_is_clipped_with_an_ellipsis():
    long_line = "표" * (outline.CONTEXT_LINE_MAX_CHARS + 100)
    clipped = outline.clip_line(long_line)
    assert len(clipped) == outline.CONTEXT_LINE_MAX_CHARS + 1 and clipped.endswith("…")
    assert outline.clip_line("짧은 줄") == "짧은 줄", "짧으면 원문 그대로"


# ── 2-9. Compare before and after saving ──

def _write(tmp_path: Path, name: str, text: str) -> Path:
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    return target


def test_new_registration_has_no_before_and_counts_everything_as_added(tmp_path):
    after = _write(tmp_path, "after.md", "# 하나\n\n가\n\n## 둘\n\n나\n")
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=0
    )
    assert summary["changed"] is True and summary["before"] is None
    assert summary["after"]["lines"] == 7 and summary["after"]["section_total"] == 2
    assert summary["lines_added"] == 7 and summary["lines_removed"] == 0
    assert [s["heading_path"] for s in summary["sections_added"]] == [["하나"], ["하나", "둘"]]
    assert summary["sections_removed"] == [] and summary["sections_unchanged"] == 0


def test_identical_save_reports_changed_false_with_zeroes(tmp_path):
    """changed: false means no difference, not a failed save."""
    text = "# 하나\n\n가\n\n## 둘\n\n나\n"
    before = _write(tmp_path, "before.md", text)
    after = _write(tmp_path, "after.md", text)
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=4,
        before_path=before, before_revision_no=3,
    )
    assert summary["changed"] is False
    assert summary["before"]["content_sha256"] == summary["after"]["content_sha256"]
    assert (summary["lines_added"], summary["lines_removed"],
            summary["chars_added"], summary["chars_removed"]) == (0, 0, 0, 0)
    assert summary["sections_unchanged"] == 2
    assert summary["before"]["revision_no"] == 3 and summary["after"]["revision_no"] == 4


def test_section_diff_splits_added_removed_and_changed(tmp_path):
    before = _write(tmp_path, "before.md",
                    "## 그대로\n\n같은 줄\n\n## 고칠 것\n\n옛 줄\n\n## 사라질 것\n\n가\n")
    after = _write(tmp_path, "after.md",
                   "## 그대로\n\n같은 줄\n\n## 고칠 것\n\n새 줄\n하나 더\n\n## 새로 생길 것\n\n나\n")
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=3,
        before_path=before, before_revision_no=2,
    )
    assert [s["heading_path"] for s in summary["sections_added"]] == [["새로 생길 것"]]
    assert [s["heading_path"] for s in summary["sections_removed"]] == [["사라질 것"]]
    assert [s["heading_path"] for s in summary["sections_changed"]] == [["고칠 것"]]
    assert summary["sections_unchanged"] == 1
    changed = summary["sections_changed"][0]
    assert changed["lines_added"] == 2 and changed["lines_removed"] == 1
    assert "@r3#L" in summary["sections_added"][0]["ref"], (
        "추가된 구간은 새 판의 줄 번호와 새 개정 번호를 쓴다"
    )
    assert "@r2#L" in summary["sections_removed"][0]["ref"], (
        "사라진 구간은 옛 판의 줄 번호와 옛 개정 번호를 쓴다"
    )


def test_changing_a_child_does_not_mark_its_ancestors_changed(tmp_path):
    """Including descendants would mark every ancestor changed after a single subheading edit."""
    before = _write(tmp_path, "before.md", "## 부모\n\n부모 본문\n\n### 자식\n\n옛 줄\n")
    after = _write(tmp_path, "after.md", "## 부모\n\n부모 본문\n\n### 자식\n\n새 줄\n")
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=1,
        before_path=before, before_revision_no=0,
    )
    assert [s["heading_path"] for s in summary["sections_changed"]] == [["부모", "자식"]]
    assert summary["sections_unchanged"] == 1


def test_number_prefix_change_counts_as_a_different_section(tmp_path):
    """Query M4 tolerates imprecise human requests; this comparison instead aligns two revisions."""
    before = _write(tmp_path, "before.md", "## 2. 구성\n\n가\n")
    after = _write(tmp_path, "after.md", "## 3. 구성\n\n가\n")
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=1,
        before_path=before, before_revision_no=0,
    )
    assert [s["heading_path"] for s in summary["sections_added"]] == [["3. 구성"]]
    assert [s["heading_path"] for s in summary["sections_removed"]] == [["2. 구성"]]


def test_indentation_only_edit_is_still_an_edit(tmp_path):
    before = _write(tmp_path, "before.md", "## A\n\n- 가\n")
    after = _write(tmp_path, "after.md", "## A\n\n  - 가\n")
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=1,
        before_path=before, before_revision_no=0,
    )
    assert summary["changed"] is True and summary["lines_added"] == 1


def test_unreadable_backup_gives_up_on_the_summary_only(tmp_path):
    """The save already completed; surfacing this as an error would cause duplicate submissions."""
    after = _write(tmp_path, "after.md", "# 하나\n")
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=1,
        before_path=tmp_path / "does-not-exist.md", before_revision_no=0,
    )
    assert summary == {"changed": None, "error": "summary unavailable"}


def test_missing_stored_file_gives_up_on_the_summary_only(tmp_path):
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=tmp_path / "nope.md", after_revision_no=0
    )
    assert summary == {"changed": None, "error": "summary unavailable"}


def test_oversized_document_skips_the_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(change_summary_service, "SUMMARY_MAX_LINES", 5)
    after = _write(tmp_path, "after.md", "".join(f"{i}\n" for i in range(20)))
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=0
    )
    assert summary == {"changed": None, "error": "summary unavailable"}


def test_document_without_headings_still_reports_line_numbers(tmp_path):
    before = _write(tmp_path, "before.md", "산문 한 줄\n")
    after = _write(tmp_path, "after.md", "산문 한 줄\n두 번째 줄\n")
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=1,
        before_path=before, before_revision_no=0,
    )
    assert summary["sections_added"] == [] and summary["sections_unchanged"] == 0
    assert summary["lines_added"] == 1 and summary["lines_removed"] == 0
    assert summary["chars_added"] == len("두 번째 줄")


def test_section_lists_are_capped_and_flagged(tmp_path):
    over = change_summary_service.SECTION_DIFF_MAX + 5
    before = _write(tmp_path, "before.md", "")
    after = _write(tmp_path, "after.md", "".join(f"## 제목 {i}\n\n본문\n\n" for i in range(over)))
    summary = change_summary_service.build(
        doc_id=_DOC_ID, after_path=after, after_revision_no=1,
        before_path=before, before_revision_no=0,
    )
    assert len(summary["sections_added"]) == change_summary_service.SECTION_DIFF_MAX
    assert summary["truncated"] is True
