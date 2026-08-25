"""Group 0460 T0004 — submission-header normalizer + frontmatter ambiguity guard.

R0001 cited a group 0458 document whose "next_type ... target_id" header block
renders as one run-together line in MdViewer. NR0003 found the stored body is
actually fine (LF-separated already) — the display layer is the direct cause —
but also found a real, separate server risk: when a submission genuinely
arrives with these seven fields collapsed onto one line, the HTTP inbox
new/edit path partially parses it (first key wins, the rest is swallowed into
its value) and either 200s a malformed document or 409s depending on which key
happened to come first.

`normalize_submission_header` (linter.py) repairs the *complete, high-
confidence* collapsed form back into 7 separate lines — in the block's own line
ending — and leaves anything ambiguous untouched. `frontmatter_parse_is_ambiguous`
hardens the separate real ``---``-delimited frontmatter path so a
partial/unclosed parse is no longer silently treated as "no identity declared"
(fail-open), while ordinary values that merely name a key stay accepted.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

import pytest  # noqa: E402

from modules.flow_gate.linter import (  # noqa: E402
    frontmatter_parse_is_ambiguous,
    looks_like_frontmatter,
    normalize_submission_header,
)

# Spelled out on purpose: an invisible U+FEFF sitting in a test fixture is a
# trap for the next reader.
BOM = "\ufeff"

# The exact string R0001 quoted (flowgate.default.0458.0005-T's collapsed form).
_R0001_COLLAPSED_LINE = (
    "next_type: T next_type_detail: 작업지시 project: flowgate module: default "
    "group: 0458 title: 검수 행 식별 기반 중복 반려 차단과 재진입 멱등 회귀 "
    "target_id: B0001"
)
_R0001_SEVEN_LINES = (
    "next_type: T\n"
    "next_type_detail: 작업지시\n"
    "project: flowgate\n"
    "module: default\n"
    "group: 0458\n"
    "title: 검수 행 식별 기반 중복 반려 차단과 재진입 멱등 회귀\n"
    "target_id: B0001"
)


class TestNormalizeSubmissionHeader:
    def test_collapsed_one_line_recovers_seven_lines(self):
        text = _R0001_COLLAPSED_LINE + "\n\n# Body\nSome content."
        out, norms = normalize_submission_header(text)
        assert out == _R0001_SEVEN_LINES + "\n\n# Body\nSome content."
        assert norms == [
            {"kind": "collapsed_next_header", "line_start": 1, "inserted_breaks": 6}
        ]

    def test_already_seven_lines_is_byte_identical_and_unflagged(self):
        text = _R0001_SEVEN_LINES + "\n\n# Body\nSome content."
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_applying_twice_is_idempotent(self):
        text = _R0001_COLLAPSED_LINE + "\n\nBody."
        once, _ = normalize_submission_header(text)
        twice, norms_twice = normalize_submission_header(once)
        assert twice == once
        assert norms_twice == []

    def test_crlf_and_tabs_and_multiple_spaces_are_high_confidence_boundaries(self):
        """A CRLF document stays a CRLF document: the repair inserts the line
        ending the block already uses, it does not convert the file to LF."""
        text = (
            "next_type:  T\r\n"
            "next_type_detail:\tworkorder\r\n"
            "project: flowgate  module: default\tgroup: 0460 "
            "title: CRLF test target_id: R0001\n\nBody"
        )
        out, norms = normalize_submission_header(text)
        assert out.startswith(
            "next_type: T\r\nnext_type_detail: workorder\r\nproject: flowgate\r\n"
            "module: default\r\ngroup: 0460\r\ntitle: CRLF test\r\n"
            "target_id: R0001\n\nBody"
        )
        assert norms == [
            {"kind": "collapsed_next_header", "line_start": 1, "inserted_breaks": 4}
        ]

    def test_already_correct_crlf_block_is_byte_identical_and_unflagged(self):
        """T0004 2.1 / completion criterion 3 — "already-correct multi-line input
        is left byte-for-byte alone" has to hold for CRLF too. Rebuilding a valid
        CRLF block with LF would rewrite bytes nobody asked to change and report a
        normalization that never happened."""
        text = _R0001_SEVEN_LINES.replace("\n", "\r\n") + "\r\n\r\n# Body\r\nSome content."
        out, norms = normalize_submission_header(text)
        assert out == text
        assert "\r\n" in out and "\n\n" not in out.replace("\r\n", "\r")
        assert norms == []

    def test_already_correct_crlf_block_is_idempotent(self):
        text = _R0001_SEVEN_LINES.replace("\n", "\r\n") + "\r\n\r\nBody."
        once, _ = normalize_submission_header(text)
        twice, norms_twice = normalize_submission_header(once)
        assert twice == once == text
        assert norms_twice == []

    def test_collapsed_sequence_inside_leading_frontmatter_is_recovered(self):
        """T0004 2.3 — the collapsed form arrives inside a real ``---`` block just
        as often as at the bare document start, and there it is still the
        high-confidence complete pattern. It must be repaired *first*, so the
        identity comparison can run (match -> continue, conflict -> 409); it must
        not fall through to the ambiguity verdict and be blanket-rejected."""
        text = "---\n" + _R0001_COLLAPSED_LINE + "\n---\n\n# Body\n"
        out, norms = normalize_submission_header(text)
        assert out == "---\n" + _R0001_SEVEN_LINES + "\n---\n\n# Body\n"
        assert norms == [
            {"kind": "collapsed_next_header", "line_start": 2, "inserted_breaks": 6}
        ]
        # And the repaired text is no longer ambiguous, so the guard that used to
        # 422 it now has a clean, comparable identity in front of it.
        assert frontmatter_parse_is_ambiguous(text) is True
        assert frontmatter_parse_is_ambiguous(out) is False

    def test_title_carrying_a_valid_target_id_uses_the_last_one_as_boundary(self):
        """T0004 2.1, verbatim: "a title may contain the string project: or
        target_id:, so take the LAST valid target ID as the boundary and do not
        damage the title". The earlier, equally well-formed-looking 'target_id:
        R999' is title text and has to survive inside the title line."""
        text = (
            "next_type: T next_type_detail: 작업지시 project: flowgate "
            "module: default group: 0460 title: target_id: R999 표기 규칙 "
            "target_id: R0001\n\nBody."
        )
        out, norms = normalize_submission_header(text)
        assert out == (
            "next_type: T\nnext_type_detail: 작업지시\nproject: flowgate\n"
            "module: default\ngroup: 0460\ntitle: target_id: R999 표기 규칙\n"
            "target_id: R0001\n\nBody."
        )
        assert norms == [
            {"kind": "collapsed_next_header", "line_start": 1, "inserted_breaks": 6}
        ]

    def test_repeated_target_id_is_resolved_by_the_boundary_not_left_half_repaired(self):
        """Same rule, and the reason it is a rule: the two shapes are textually
        identical, so 'title text' and 'a second target_id' cannot be told apart.
        T0004 2.1 settles it with the last valid id, which is also what keeps a
        duplicate label from being left dangling *outside* the repaired block —
        every byte up to the boundary is consumed, nothing is clipped off and
        saved beside it."""
        text = (
            "next_type: T next_type_detail: 작업지시 project: flowgate "
            "module: default group: 0460 title: dup target target_id: R0001 "
            "target_id: R0002\n\nBody"
        )
        out, norms = normalize_submission_header(text)
        assert out == (
            "next_type: T\nnext_type_detail: 작업지시\nproject: flowgate\n"
            "module: default\ngroup: 0460\ntitle: dup target target_id: R0001\n"
            "target_id: R0002\n\nBody"
        )
        assert len(norms) == 1
        # No duplicate field survives outside the seven repaired lines.
        assert out.count("\ntarget_id:") == 1
        # And it is still idempotent.
        assert normalize_submission_header(out) == (out, [])

    def test_stray_key_after_target_id_is_untouched(self):
        """Any other key label re-appearing after the target value leaves the end
        boundary unsettled — same rule, different key."""
        text = (
            "next_type: T next_type_detail: 작업지시 project: flowgate "
            "module: default group: 0460 title: stray tail target_id: R0001 "
            "project: other\n\nBody"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_trailing_prose_on_the_header_line_is_untouched(self):
        text = (
            "next_type: T next_type_detail: 작업지시 project: flowgate "
            "module: default group: 0460 title: trailing prose target_id: R0001 "
            "and then some sentence\n\nBody"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_title_containing_target_id_colon_is_preserved_verbatim(self):
        """A title may legitimately describe the field itself. The real
        (rightmost, validating) target_id must still be found and the whole
        title text — including the embedded 'target_id:' — must survive."""
        text = (
            "next_type: T next_type_detail: 작업지시 project: flowgate "
            "module: default group: 0460 title: target_id: 표기 규칙 설명 "
            "target_id: R0001\n\nBody."
        )
        out, norms = normalize_submission_header(text)
        assert "title: target_id: 표기 규칙 설명\n" in out
        assert out.endswith("target_id: R0001\n\nBody.")
        assert len(norms) == 1

    def test_title_containing_project_colon_is_preserved_verbatim(self):
        text = (
            "next_type: T next_type_detail: 작업지시 project: flowgate "
            "module: default group: 0460 title: project: 필드 표기 예시 "
            "target_id: R0001\n\nBody."
        )
        out, _ = normalize_submission_header(text)
        assert "title: project: 필드 표기 예시\ntarget_id: R0001" in out

    def test_ordinary_prose_is_untouched(self):
        text = "This is just a normal paragraph about the project.\n\nBody."
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_url_and_time_in_body_are_untouched(self):
        text = (
            "# Notes\n\nSee http://example.com/next_type:foo around 12:30:00 "
            "for details.\n"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_code_fence_example_is_untouched(self):
        text = (
            "# Doc\n\nExample:\n```text\n"
            + _R0001_COLLAPSED_LINE
            + "\n```\n\nEnd."
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_tilde_code_fence_example_is_untouched(self):
        """``~~~`` is a Markdown fence too (CommonMark 4.5). A documentation
        example written with tildes — the natural choice when the sample itself
        contains backticks — must be as untouchable as a ``` one."""
        text = (
            "# Doc\n\n## Instruction to include next document header\n\n"
            "Example of the block, do not copy the values:\n\n~~~text\n"
            + _R0001_COLLAPSED_LINE
            + "\n~~~\n\nEnd."
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_tilde_fence_directly_below_the_marker_heading_is_untouched(self):
        """The worst case for both rules at once: the marker heading is real, the
        sequence below it is complete — but it sits inside a tilde fence, so it is
        an *example* of the header, not the header."""
        text = (
            "## Instruction to include next document header\n~~~\n"
            + _R0001_COLLAPSED_LINE
            + "\n~~~\n"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_marker_phrase_quoted_in_prose_is_not_a_candidate(self):
        """Only the section *heading* designates a candidate. The same phrase
        quoted in a sentence is prose, and the next next_type line — possibly
        paragraphs below — must not be rewritten because of it."""
        text = (
            "위 'Instruction to include next document header' 섹션에 주어진 헤더를 "
            "그대로 쓰십시오.\n\n인용 예시:\n\n"
            + _R0001_COLLAPSED_LINE
            + "\n"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_marker_heading_with_intervening_content_is_not_a_candidate(self):
        """Repair only what sits immediately below the heading: a paragraph between the heading and the
        sequence means the sequence is not that section's header block."""
        text = (
            "# Doc\n\n## Instruction to include next document header\n---\n"
            "Read the notes before filling this in.\n\n"
            + _R0001_COLLAPSED_LINE
            + "\n"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_missing_key_is_untouched(self):
        text = (
            "next_type: T next_type_detail: 작업지시 project: flowgate "
            "module: default title: no group here target_id: R0001\n\nBody"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    # T0004 2.1: "repair only when every key appears exactly once". Every one of
    # the seven labels gets its own case — a duplicate must abort the repair, not
    # be folded into a neighbouring value. (target_id has its own pair of cases
    # above: there the boundary rule, not an abort, is what T0004 prescribes.)
    @pytest.mark.parametrize(
        "label,collapsed",
        [
            (
                "next_type",
                "next_type: T next_type_detail: 작업지시 next_type: TR "
                "project: flowgate module: default group: 0460 "
                "title: dup next_type target_id: R0001",
            ),
            (
                "next_type_detail",
                "next_type: T next_type_detail: 작업지시 next_type_detail: 조사레포트 "
                "project: flowgate module: default group: 0460 "
                "title: dup detail target_id: R0001",
            ),
            (
                "project",
                "next_type: T next_type_detail: 작업지시 project: flowgate "
                "project: flowgate2 module: default group: 0460 "
                "title: dup project target_id: R0001",
            ),
            (
                "module",
                "next_type: T next_type_detail: 작업지시 project: flowgate "
                "module: default module: ui group: 0460 "
                "title: dup module target_id: R0001",
            ),
            (
                "group",
                "next_type: T next_type_detail: 작업지시 project: flowgate "
                "module: default group: 0460 group: 0461 "
                "title: dup group target_id: R0001",
            ),
            (
                "group_id",
                "next_type: T next_type_detail: 작업지시 project: flowgate "
                "module: default group_id: flowgate.default.0460 group: 0460 "
                "title: dup group_id target_id: R0001",
            ),
            (
                "title",
                "next_type: T next_type_detail: 작업지시 project: flowgate "
                "module: default group: 0460 title: first title: second "
                "target_id: R0001",
            ),
        ],
    )
    def test_a_duplicated_key_of_any_kind_is_untouched(self, label, collapsed):
        text = collapsed + "\n\nBody"
        out, norms = normalize_submission_header(text)
        assert out == text, f"{label} duplicate was rewritten"
        assert norms == []

    @pytest.mark.parametrize("token", ["tr", "Tr", "ZZ", "TASK"])
    def test_unregistered_next_type_token_is_untouched(self, token):
        """The *submitted* token has to be a registered document type. Matching on
        next_type.upper() and then writing the original spelling back would bless
        an unregistered "next_type: tr" by rewriting it into a 7-line header."""
        text = (
            f"next_type: {token} next_type_detail: 작업지시 project: flowgate "
            "module: default group: 0460 title: strict type target_id: R0001"
            "\n\nBody"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_reversed_order_is_untouched(self):
        text = (
            "target_id: R0001 title: reversed order next_type: T "
            "next_type_detail: 작업지시 project: flowgate module: default "
            "group: 0460\n\nBody"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_instruction_section_below_heading_is_recovered(self):
        text = (
            "# Some doc\n\n"
            "## Instruction to include next document header\n"
            "---\n"
            "next_type: TR next_type_detail: 작업레포트 project: flowgate "
            "module: default group: 0460 title: <Title here> target_id: R0001\n\n"
            "More text."
        )
        out, norms = normalize_submission_header(text)
        assert (
            "## Instruction to include next document header\n"
            "---\n"
            "next_type: TR\n"
            "next_type_detail: 작업레포트\n"
            "project: flowgate\n"
            "module: default\n"
            "group: 0460\n"
            "title: <Title here>\n"
            "target_id: R0001\n\n"
            "More text."
        ) in out
        assert len(norms) == 1

    # A CRLF document must obey exactly the same rules as an LF one. It is easy
    # for it not to: in MULTILINE mode `$` matches *before* the "\n" of a CRLF
    # pair, so an end-of-line anchor written as plain `$` silently stops
    # recognizing fences, headings and delimiters as soon as the file uses CRLF —
    # and "silently stops recognizing a fence" means rewriting an example.
    def test_crlf_code_fence_example_is_untouched(self):
        text = (
            "# Doc\r\n\r\nExample:\r\n```text\r\n"
            + _R0001_COLLAPSED_LINE
            + "\r\n```\r\n\r\nEnd.\r\n"
        )
        out, norms = normalize_submission_header(text)
        assert out == text
        assert norms == []

    def test_crlf_instruction_section_is_recovered_in_crlf(self):
        text = (
            "# Some doc\r\n\r\n"
            "## Instruction to include next document header\r\n"
            "---\r\n"
            + _R0001_COLLAPSED_LINE
            + "\r\n\r\nMore text.\r\n"
        )
        out, norms = normalize_submission_header(text)
        assert out == (
            "# Some doc\r\n\r\n"
            "## Instruction to include next document header\r\n"
            "---\r\n"
            + _R0001_SEVEN_LINES.replace("\n", "\r\n")
            + "\r\n\r\nMore text.\r\n"
        )
        assert len(norms) == 1
        assert "\r\n" in out and out.count("\n") == out.count("\r\n")

    def test_group_id_label_is_preserved(self):
        text = (
            "next_type: T next_type_detail: 작업지시 project: flowgate "
            "module: default group_id: flowgate.default.0460 title: alias "
            "target_id: R0001\n\nBody"
        )
        out, _ = normalize_submission_header(text)
        assert "group_id: flowgate.default.0460\n" in out

    def test_empty_text_returns_unchanged(self):
        assert normalize_submission_header("") == ("", [])


class TestFrontmatterParseIsAmbiguous:
    _WELL_FORMED = (
        "---\n"
        "project: testprj\n"
        "module: __ALL__\n"
        "group_id: testprj-__ALL__-0001\n"
        "doc_number: CH7503\n"
        "type: CH\n"
        "target_id: testprj-__ALL__-0001-R0001\n"
        "title: Target conversation\n"
        "---\n"
        "own short body\n"
    )

    def test_plain_body_without_frontmatter_is_not_ambiguous(self):
        assert frontmatter_parse_is_ambiguous("next_type: T\nbody text") is False

    def test_well_formed_frontmatter_is_not_ambiguous(self):
        assert frontmatter_parse_is_ambiguous(self._WELL_FORMED) is False

    def test_unclosed_frontmatter_is_ambiguous(self):
        text = "---\nproject: x\nno closing dashes here"
        assert frontmatter_parse_is_ambiguous(text) is True

    def test_crlf_frontmatter_delimiters_are_recognized(self):
        """The delimiter of a CRLF document is "---\\r" to a MULTILINE `$`. If the
        closing-line test misses it, every well-formed CRLF submission is called
        unclosed and 422'd."""
        assert frontmatter_parse_is_ambiguous(self._WELL_FORMED.replace("\n", "\r\n")) is False
        assert frontmatter_parse_is_ambiguous(
            "---\r\nproject: x\r\nno closing dashes here\r\n"
        ) is True

    def test_collapsed_line_inside_real_frontmatter_is_recovered_not_rejected(self):
        """Raw, this parse *is* ambiguous — but it is also the high-confidence
        complete pattern, so callers normalize before they judge. After the repair
        the verdict flips to "not ambiguous" and the identity comparison runs
        (T0004 2.3). The submission path is asserted end to end in
        tests/test_inbox.py::test_new_recovers_collapsed_frontmatter_then_compares_identity."""
        text = "---\n" + _R0001_COLLAPSED_LINE + "\n---\nbody\n"
        assert frontmatter_parse_is_ambiguous(text) is True

        repaired, norms = normalize_submission_header(text)
        assert repaired == "---\n" + _R0001_SEVEN_LINES + "\n---\nbody\n"
        assert len(norms) == 1
        assert frontmatter_parse_is_ambiguous(repaired) is False

    def test_title_naming_another_key_is_not_ambiguous(self):
        """T0004 2.3: do not judge a colon in a title/URL/body on the key name
        alone. A title that names a field is ordinary authored text, and this
        frontmatter is completely well-formed — it must not 422."""
        text = (
            "---\n"
            "project: testprj\n"
            "module: __ALL__\n"
            "type: NR\n"
            "title: project: 마이그레이션 안내\n"
            "---\n"
            "body\n"
        )
        assert frontmatter_parse_is_ambiguous(text) is False

    def test_url_and_prose_values_are_not_ambiguous(self):
        text = (
            "---\n"
            "project: testprj\n"
            "type: NR\n"
            "title: 참고 http://example.com/target_id: 42 를 보라\n"
            "---\n"
            "body\n"
        )
        assert frontmatter_parse_is_ambiguous(text) is False

    def test_list_and_nested_dict_values_naming_keys_are_not_ambiguous(self):
        """Normal list / nested-dict parsing is a preserved contract (T0004 2.3);
        a list item that happens to read "target_id: 예시" is authored content, not
        evidence that a line collapsed."""
        text = (
            "---\n"
            "project: testprj\n"
            "type: NR\n"
            "title: 목록 예시\n"
            "approved_files:\n"
            "  - target_id: 예시\n"
            "  - project: 예시\n"
            "clear_scope:\n"
            "  note: target_id: 예시\n"
            "---\n"
            "body\n"
        )
        from modules.flow_gate.linter import parse_yaml_header

        header, err = parse_yaml_header(text)
        assert err == ""
        assert header["approved_files"] == ["target_id: 예시", "project: 예시"]
        assert header["clear_scope"] == {"note": "target_id: 예시"}
        assert frontmatter_parse_is_ambiguous(text) is False

    def test_collapsed_line_first_key_project_is_ambiguous(self):
        """NR0003's exact table case: first key 'project' swallows the rest,
        so a naive identity check sees a garbled-but-present project value
        instead of the real missing-fields signal."""
        text = (
            "---\n"
            "project: flowgate module: default group: 0458 title: x "
            "target_id: B0001\n"
            "---\nbody\n"
        )
        assert frontmatter_parse_is_ambiguous(text) is True

    @pytest.mark.parametrize(
        "collapsed_line",
        [
            # The shortest possible chain: two known keys, one line. The outer key
            # is the one the parser kept; the second pair is what it swallowed.
            "module: default group: 0460",
            "project: testprj module: __ALL__",
            "type: NR doc_number: NR0007",
            # ... and longer chains of the same kind.
            "module: __ALL__ group: 0001 target_id: R0001",
        ],
    )
    def test_a_partial_known_key_chain_of_any_length_is_ambiguous(self, collapsed_line):
        """T0004 2.3: "one line still carrying several known key traces" is a
        partial parse and must be rejected, whatever its length. A two-pair line
        is the smallest such chain and used to slip through as "one embedded key,
        below threshold" — it now trips the same test as a six-pair one."""
        text = "---\nproject: testprj\n" + collapsed_line + "\ntitle: x\n---\nbody\n"
        assert frontmatter_parse_is_ambiguous(text) is True

    def test_three_hyphens_inside_a_scalar_do_not_close_the_frontmatter(self):
        """Closure has to be a real standalone delimiter *line*. Scanning for the
        next literal "---" accepts a hyphenated scalar as the terminator, so an
        unclosed block parses "successfully" and fails open."""
        text = "---\ntitle: a --- b\nproject: testprj\nno closing line at all\n"
        assert frontmatter_parse_is_ambiguous(text) is True

    def test_a_parse_truncated_before_the_real_delimiter_is_ambiguous(self):
        """Here the block *is* closed, but parse_yaml_header cuts it at the "---"
        inside the title, so `project` never reaches the header dict and the
        identity comparison would silently compare nothing."""
        text = "---\ntitle: a --- b\nproject: mailanchor\n---\nbody\n"
        from modules.flow_gate.linter import parse_yaml_header

        header, _err = parse_yaml_header(text)
        assert "project" not in (header or {})  # the truncation, demonstrated
        assert frontmatter_parse_is_ambiguous(text) is True

    def test_bom_prefixed_frontmatter_is_still_frontmatter(self):
        """U+FEFF is not whitespace, so `text.lstrip().startswith("---")` says
        "not frontmatter" for a BOM-prefixed body and every guard behind that test
        is skipped — while the normalizer recognizes the very same block. All the
        entry points have to agree (T0004 2.3)."""
        well_formed = BOM + self._WELL_FORMED
        assert looks_like_frontmatter(well_formed) is True
        assert frontmatter_parse_is_ambiguous(well_formed) is False

        malformed = (
            BOM + "---\n"
            "title: T project: testprj module: __ALL__ group: 0001\n"
            "---\nbody\n"
        )
        assert looks_like_frontmatter(malformed) is True
        assert frontmatter_parse_is_ambiguous(malformed) is True

    def test_bom_prefixed_frontmatter_parses_its_identity_fields(self):
        """The identity comparison reads parse_yaml_header's dict, so the BOM has
        to be stripped there too — otherwise a BOM-prefixed foreign frontmatter
        reports "no YAML header" and reads as "declares nothing"."""
        from modules.flow_gate.linter import parse_yaml_header

        header, err = parse_yaml_header(BOM + self._WELL_FORMED)
        assert err == ""
        assert header["project"] == "testprj"
