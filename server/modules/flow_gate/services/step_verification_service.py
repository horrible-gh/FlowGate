"""단계별 확인 (step-by-step verification) section check (flowgate.default.0467 — R0001).

A TR (work report) reader does not want to read through prose to find out how to check
that a change actually works. This reads a required, structured section out of a TR body
— one or more named sub-sections, each with a one-line summary and a list of verification
steps, each step carrying the expected result(s) of following it — so the document screen
can render it as a checklist instead of the worker's own prose.

Modeled after tr_scope_service's "required section, empty must say 없음" rule, but with no
git/worktree comparison: the section's own structure is the only thing being judged, so the
verdict can be recomputed at any time straight from the document body (no meta persistence,
no enforcement-stage config — R0001 rule 7/8 make this unconditionally mandatory for TR).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

SECTION_HEADING = "## 단계별 확인"
SECTION_HEADING_EN = "## Step Verification"  # English alias the parser accepts (canonical display name stays Korean)
NONE_MARKER = "없음"

SVV_MISSING_SECTION = "SVV-001"  # section missing, or present but empty with no 없음
SVV_FORMAT = "SVV-002"           # structural error inside a declared section

_CODE_LABELS: dict[str, dict[str, str]] = {
    "ko": {
        SVV_MISSING_SECTION: "섹션 누락 — `## 단계별 확인` 섹션이 없거나 비어 있습니다.",
        SVV_FORMAT: "형식 오류 — 섹션 안의 표기가 규칙에 어긋납니다.",
    },
    "en": {
        SVV_MISSING_SECTION: f"Missing section — the `{SECTION_HEADING_EN}` section is missing or empty.",
        SVV_FORMAT: "Format error — the section's notation violates the required format.",
    },
}

VERDICT_PASS = "pass"
VERDICT_REJECT = "reject"

_HEADING_RE = re.compile(r"^\s{0,3}#{2}\s*(단계별\s*확인|Step\s+Verification)\s*$", re.IGNORECASE)
_NEXT_TOP_HEADING_RE = re.compile(r"^\s{0,3}#{1,2}\s")  # only level 1-2 ends the section; ### starts a sub-section
_SUB_HEADING_RE = re.compile(r"^\s{0,3}###\s+(.+?)\s*$")
# T0009-style bilingual grammar (korean-is-in-the-protocol-grammar): the Korean field marker
# stays canonical, an English alias is accepted alongside it (case-insensitive) so a worker who
# does not read Korean can still author a valid section.
_SUMMARY_RE = re.compile(r"^-\s*(?:개요|summary)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_STEP_RE = re.compile(r"^-\s*(?:스텝|step)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_EXPECT_RE = re.compile(r"^\s{2,}-\s*(?:기대치|expected)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_NONE_VARIANTS = frozenset({
    NONE_MARKER, f"- {NONE_MARKER}",
    "none", "None", "N/A", "- none", "- None", "- N/A",
})


@dataclass
class VerificationStep:
    description: str
    expectations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"description": self.description, "expectations": list(self.expectations)}


@dataclass
class VerificationSection:
    title: str
    summary: str = ""
    steps: list[VerificationStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
        }


class ParsedStepVerification:
    """Result of parsing the `## 단계별 확인` section out of a document body."""

    def __init__(self) -> None:
        self.found: bool = False
        self.declared_none: bool = False
        self.sections: list[VerificationSection] = []
        self.format_errors: list[str] = []


_FORMAT_ERROR_STRINGS = {
    "ko": {
        "no_summary": "'{title}' 섹션에 '- 개요: ...' 줄이 없습니다.",
        "multi_summary": "'{title}' 섹션에 '- 개요: ...' 줄이 두 번 이상 있습니다.",
        "no_steps": "'{title}' 섹션에 '- 스텝: ...' 줄이 하나도 없습니다.",
        "step_no_expectation": "'{title}' 섹션의 스텝 '{step}'에 '기대치' 줄이 없습니다.",
        "orphan_expectation": "'{title}' 섹션에 앞선 스텝 없이 '기대치' 줄이 나왔습니다: {text}",
        "unrecognized_line": "'{title}' 섹션에서 알아볼 수 없는 줄입니다: {text}",
        "none_with_sections": "'없음'과 실제 섹션이 함께 선언되었습니다. 섹션이 있으면 '없음'을 빼십시오.",
    },
    "en": {
        "no_summary": "Section '{title}' has no '- Summary: ...' line.",
        "multi_summary": "Section '{title}' has more than one '- Summary: ...' line.",
        "no_steps": "Section '{title}' has no '- Step: ...' lines at all.",
        "step_no_expectation": "Step '{step}' in section '{title}' has no 'Expected' line.",
        "orphan_expectation": "Section '{title}' has an 'Expected' line with no preceding step: {text}",
        "unrecognized_line": "Unrecognized line in section '{title}': {text}",
        "none_with_sections": "Both 'None' and real sections were declared. Remove the 'None' line when sections exist.",
    },
}


def _format_error(key: str, locale: str, **kwargs) -> str:
    strings = _FORMAT_ERROR_STRINGS.get(locale) or _FORMAT_ERROR_STRINGS["ko"]
    return strings[key].format(**kwargs)


def parse_step_verification(body: str, locale: str = "ko") -> ParsedStepVerification:
    """Read the `## 단계별 확인` section out of a TR body. Never raises."""
    result = ParsedStepVerification()
    loc = locale if locale in _FORMAT_ERROR_STRINGS else "ko"
    lines = (body or "").splitlines()
    start = -1
    for index, line in enumerate(lines):
        if _HEADING_RE.match(line):
            start = index + 1
            break
    if start < 0:
        return result
    result.found = True

    body_lines: list[str] = []
    for line in lines[start:]:
        if _NEXT_TOP_HEADING_RE.match(line):
            break
        body_lines.append(line)

    # Split into (pre-first-subsection lines, [(title, [lines...]), ...]).
    preamble: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    current: Optional[list[str]] = None
    for line in body_lines:
        sub = _SUB_HEADING_RE.match(line)
        if sub:
            current = []
            blocks.append((sub.group(1), current))
            continue
        (current if current is not None else preamble).append(line)

    declared_none = any(line.strip() in _NONE_VARIANTS for line in preamble)

    sections: list[VerificationSection] = []
    for title, block_lines in blocks:
        section = VerificationSection(title=title)
        summary_count = 0
        current_step: Optional[VerificationStep] = None
        for line in block_lines:
            if not line.strip():
                continue
            m = _SUMMARY_RE.match(line)
            if m:
                summary_count += 1
                if summary_count == 1:
                    section.summary = m.group(1)
                else:
                    result.format_errors.append(_format_error("multi_summary", loc, title=title))
                continue
            m = _STEP_RE.match(line)
            if m:
                current_step = VerificationStep(description=m.group(1))
                section.steps.append(current_step)
                continue
            m = _EXPECT_RE.match(line)
            if m:
                if current_step is None:
                    result.format_errors.append(
                        _format_error("orphan_expectation", loc, title=title, text=line.strip()[:120])
                    )
                else:
                    current_step.expectations.append(m.group(1))
                continue
            result.format_errors.append(
                _format_error("unrecognized_line", loc, title=title, text=line.strip()[:120])
            )
        if summary_count == 0:
            result.format_errors.append(_format_error("no_summary", loc, title=title))
        if not section.steps:
            result.format_errors.append(_format_error("no_steps", loc, title=title))
        for step in section.steps:
            if not step.expectations:
                result.format_errors.append(
                    _format_error("step_no_expectation", loc, title=title, step=step.description[:80])
                )
        sections.append(section)

    if declared_none and sections:
        result.format_errors.append(_format_error("none_with_sections", loc))

    result.sections = sections
    result.declared_none = declared_none and not sections
    return result


def evaluate(body: str, locale: str = "ko") -> dict:
    """Judge a TR body's `## 단계별 확인` section. Side-effect free; safe to call on every read."""
    loc = locale if locale in _CODE_LABELS else "ko"
    parsed = parse_step_verification(body, loc)
    codes: list[str] = []

    if not parsed.found:
        codes.append(SVV_MISSING_SECTION)
    elif not parsed.sections and not parsed.declared_none:
        codes.append(SVV_MISSING_SECTION)
    if parsed.format_errors:
        codes.append(SVV_FORMAT)

    verdict = VERDICT_REJECT if codes else VERDICT_PASS
    result = {
        "verdict": verdict,
        "codes": codes,
        "declared_none": parsed.declared_none,
        "sections": [s.to_dict() for s in parsed.sections],
        "format_errors": parsed.format_errors,
    }
    if verdict == VERDICT_REJECT:
        result["notice"] = build_notice(result, loc)
    return result


def enforce_on_save(doc_type: Optional[str], body: str, locale: str = "ko") -> Optional[dict]:
    """Judge a submitted body against the TR step-verification rule.

    This is the shared decision point for inbox new/edit submission boundaries. The PATCH
    content routes edit existing documents and deliberately do not call it. The type
    comparison is case/blank-safe and every type other than TR passes straight through
    (returns None). For TR, the result is exactly ``evaluate(body, locale)``.
    """
    normalized_type = (doc_type or "").strip().upper()
    if normalized_type != "TR":
        return None
    return evaluate(body, locale=locale)


def _spelling_heading(locale: str) -> str:
    """Per-language heading (T2/TR2 pattern): an EN request never sees the Korean form."""
    return SECTION_HEADING_EN if locale == "en" else SECTION_HEADING


def _spelling_none(locale: str) -> str:
    return "None" if locale == "en" else NONE_MARKER


_NOTICE: dict[str, dict[str, str]] = {
    "ko": {
        "head": "TR 제출이 [단계별 확인] 섹션 검증에서 반려되었습니다.",
        "reason_heading": "\n[1] 반려 사유",
        "format_errors_heading": "\n[2] 형식 오류",
        "resubmit": (
            "\n[3] 다시 제출하는 방법\n"
            "  TR 본문에 아래 형태로 '{heading}' 섹션을 포함해 다시 제출하십시오.\n"
            "  자세한 서식은 도움말 항목 step_verification_format 에 있습니다.\n"
            "\n"
            "  {heading}\n"
            "\n"
            "  ### <섹션 제목>\n"
            "  - 개요: <한줄 개요>\n"
            "  - 스텝: <확인 스텝 설명>\n"
            "    - 기대치: <이 스텝을 따라했을 때 기대되는 결과>\n"
            "\n"
            "  확인할 것이 전혀 없으면 섹션 대신 '{none}' 한 줄만 적습니다.\n"
        ),
    },
    "en": {
        "head": "The TR submission was rejected by step-verification section validation.",
        "reason_heading": "\n[1] Rejection reason",
        "format_errors_heading": "\n[2] Format errors",
        "resubmit": (
            "\n[3] How to resubmit\n"
            "  Include a '{heading}' section in the TR body in the form below.\n"
            "  See the step_verification_format help item for the full format.\n"
            "\n"
            "  {heading}\n"
            "\n"
            "  ### <section title>\n"
            "  - Summary: <one-line summary>\n"
            "  - Step: <verification step description>\n"
            "    - Expected: <expected result of following this step>\n"
            "\n"
            "  If there is nothing to verify, write a single '{none}' line instead of a section.\n"
        ),
    },
}


def build_notice(result: dict, locale: str = "ko") -> str:
    loc = locale if locale in _NOTICE else "ko"
    strings = _NOTICE[loc]
    labels = _CODE_LABELS.get(loc) or _CODE_LABELS["ko"]
    codes: list[str] = result.get("codes") or []
    parts: list[str] = [strings["head"], strings["reason_heading"]]
    for code in codes:
        parts.append(f"  - {code}: {labels.get(code, code)}")
    if result.get("format_errors"):
        parts.append(strings["format_errors_heading"])
        for msg in result["format_errors"]:
            parts.append(f"  - {msg}")
    parts.append(strings["resubmit"].format(heading=_spelling_heading(loc), none=_spelling_none(loc)))
    return "\n".join(parts)


_SECTION_GUIDE_TEMPLATES: dict[str, str] = {
    "ko": (
        "TR 본문에는 아래 섹션을 반드시 포함하십시오. 섹션은 여러 개 적을 수 있고,\n"
        "확인할 것이 전혀 없으면 섹션 대신 '{none}' 한 줄만 적습니다.\n"
        "\n"
        "{heading}\n"
        "\n"
        "### <섹션 제목>\n"
        "- 개요: <한줄 개요>\n"
        "- 스텝: <확인 스텝 설명>\n"
        "  - 기대치: <이 스텝을 따라했을 때 기대되는 결과>\n"
        "  - 기대치: <기대되는 결과가 여러 개면 줄을 늘립니다>\n"
        "- 스텝: <다음 스텝>\n"
        "  - 기대치: <그 스텝의 기대치>\n"
        "\n"
        "### <다른 섹션이 있으면 반복>\n"
        "...\n"
        "\n"
        "규칙: 섹션 제목은 '###'(레벨 3), 개요는 한 줄, 스텝은 하나 이상, 스텝마다\n"
        "기대치가 하나 이상 있어야 합니다. '{none}'과 실제 섹션을 함께 적지 않습니다.\n"
    ),
    "en": (
        "Include the section below in the TR body. There can be several '###' sub-\n"
        "sections; if there is nothing to verify, write a single '{none}' line instead.\n"
        "\n"
        "{heading}\n"
        "\n"
        "### <section title>\n"
        "- Summary: <one-line summary>\n"
        "- Step: <verification step description>\n"
        "  - Expected: <expected result of following this step>\n"
        "  - Expected: <add another line for more than one expected result>\n"
        "- Step: <next step>\n"
        "  - Expected: <that step's expected result>\n"
        "\n"
        "### <repeat for another section>\n"
        "...\n"
        "\n"
        "Rules: section titles are level-3 ('###'); one summary line; at least one\n"
        "step; every step needs at least one Expected line. Do not declare '{none}'\n"
        "alongside real sections.\n"
    ),
}


def section_guide(locale: str = "ko") -> str:
    loc = locale if locale in _SECTION_GUIDE_TEMPLATES else "ko"
    return _SECTION_GUIDE_TEMPLATES[loc].format(heading=_spelling_heading(loc), none=_spelling_none(loc))


_SECTION_PLACEHOLDER_TEMPLATES: dict[str, str] = {
    "ko": (
        "{heading}\n\n"
        "### <섹션 제목>\n"
        "- 개요: <한줄 개요>\n"
        "- 스텝: <확인 스텝 설명>\n"
        "  - 기대치: <이 스텝을 따라했을 때 기대되는 결과>\n"
        "\n"
        "확인할 것이 없으면 위 섹션 대신 '{none}' 한 줄만 적습니다.\n"
    ),
    "en": (
        "{heading}\n\n"
        "### <section title>\n"
        "- Summary: <one-line summary>\n"
        "- Step: <verification step description>\n"
        "  - Expected: <expected result of following this step>\n"
        "\n"
        "If there is nothing to verify, write a single '{none}' line instead of the section above.\n"
    ),
}


def section_placeholder(locale: str = "ko") -> str:
    loc = locale if locale in _SECTION_PLACEHOLDER_TEMPLATES else "ko"
    return _SECTION_PLACEHOLDER_TEMPLATES[loc].format(heading=_spelling_heading(loc), none=_spelling_none(loc))
