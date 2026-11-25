"""Specification TS / TSR test-report model.

redefines the two test documents without adding a third type:

* **TS** — a human-approved, structured *test specification*. Each case carries a Case ID,
  category, requirement/AC reference, precondition, input, procedure, expected result, check
  points, execution mode and a required flag. The shell ``cmd/expect/assert/setup/teardown``
  grammar is NOT part of it.
* **TSR** — a *test report*: one PASS/FAIL/BLOCKED/NOT_RUN verdict per TS Case ID, with the
  actual result, evidence, defect/reference and source identity, plus an overall verdict the
  server computes from the required cases.

The two TS grammars are separated by an explicit contract marker in the document's own
frontmatter (``test_contract_version: 2``), never by creation time or by guessing from the
body. A TS without the marker is a legacy executable TS (contract 1) and keeps
using ``test_run_service.parse_test_plan`` and the server-side runner unchanged (§5).

This module is pure: parsing, validation, result normalisation (explicit payload and JUnit
XML), Case ID mapping, overall computation and report rendering. Persistence and workflow
effects live in ``test_run_service``.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable, Optional

CONTRACT_LEGACY = 1
CONTRACT_SPEC = 2
SUPPORTED_CONTRACTS = (CONTRACT_LEGACY, CONTRACT_SPEC)
CONTRACT_KEY = "test_contract_version"

# The H2 section holding the cases. Grammar tokens: every spelling below parses in every
# locale; the canonical renderer picks the one matching the document locale.
SPEC_SECTION_NAMES = ("시험 사양", "Test Specification", "試験仕様")
_SPEC_SECTION_BY_LOCALE = {"ko": "시험 사양", "en": "Test Specification", "ja": "試験仕様"}

CATEGORIES = ("normal", "negative", "boundary", "regression")
# automated = the server runs its code; ai = an AI worker checks it and records the result;
# manual = a person checks it; external = recorded from outside. Only automated is server-run.
EXECUTION_MODES = ("automated", "ai", "manual", "external")
STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_RUN")

# ── Scenario-case shape (F81) ────────────────────────────────────────────────────
# A case is checked one of two ways: ``code`` (the server runs its command) or ``manual``
# (someone checks it by hand and records the result). For a manual case ``checker`` says who
# checks it. The four execution modes above are still read: a document or a stored result
# written with them maps onto the two checks, and is written back in the new words on save.
CHECK_METHODS = ("code", "manual")
CHECKERS = ("ai", "human", "external")
_CHECK_BY_MODE = {
    "automated": ("code", None), "ai": ("manual", "ai"),
    "manual": ("manual", "human"), "external": ("manual", "external"),
}
_MODE_BY_CHECKER = {"ai": "ai", "human": "manual", "external": "external"}
# The result of a case as the screens and the APIs word it. The stored verdict column keeps
# its four tokens (the gate reads them); ``BLOCKED`` is read and shown as ``skip``.
CASE_RESULTS = ("not_run", "pass", "fail", "skip")
_RESULT_BY_STATUS = {"NOT_RUN": "not_run", "PASS": "pass", "FAIL": "fail", "BLOCKED": "skip"}
_STATUS_BY_RESULT = {value: key for key, value in _RESULT_BY_STATUS.items()}
RESULT_ACTOR_KINDS = ("ai", "human")
# A remark on a case: who wrote it, in which AI stage, and whether it is still open.
REMARK_PHASES = ("case_write", "test_run")
REMARK_STATES = ("open", "resolved")
REMARK_AUTHOR_KINDS = ("ai", "human")
MAX_REMARK_CHARS = 4000
MAX_REMARK_REPLY_CHARS = 500
# The tag a flow attachment carries when it is a case's evidence (meta field ``test_evidence``).
EVIDENCE_TAG_KEY = "test_evidence"
EVIDENCE_TAG_KINDS = ("input", "log", "output")
EVIDENCE_KINDS = (
    "text", "log", "structured", "file", "attachment", "screenshot", "url", "manual_note",
)

MAX_SPEC_CASES = 200
MAX_RESULTS = 1000
MAX_FIELD_CHARS = 4000
MAX_EVIDENCE_PER_RESULT = 20
MAX_JUNIT_BYTES = 5 * 1024 * 1024

# Canonical field keys (grammar tokens, never translated — the same convention as ``cmd``)
# in render order, plus the aliases a human or an AI may type.
# ``content`` / ``check`` / ``checker`` are the F81 case fields. A case is written with them;
# ``requirement`` / ``precondition`` / ``execution_mode`` are read from a document saved
# before them and are not written again (their text is carried in ``content`` / ``check``).
SPEC_FIELDS = (
    "content", "procedure", "expected", "check", "checker", "run_command", "run_cwd",
    "required", "category", "input", "check_points", "automation_ref",
    "test_assets",
)
# Read from an earlier document, never rendered.
READ_ONLY_SPEC_FIELDS = ("requirement", "precondition", "execution_mode")
# Optional fields rendered only when set, so a document without them round-trips unchanged.
# run_command is the program and arguments that run an automated Case; the
# test files it names, if any, are recorded in the run's basis. automation_ref (a target) and run_cwd (where it runs) are kept for Cases
# saved with them; the editor no longer writes them for a new command.
OPTIONAL_SPEC_FIELDS = (
    "content", "checker", "run_command", "category", "input", "check_points",
    "automation_ref", "run_cwd", "test_assets",
)
# ``required`` is not listed: an absent flag means required (stop the next step on failure),
# the same default the structured editor gives a new case and the client reads.
REQUIRED_SPEC_FIELDS = (
    "category", "requirement", "execution_mode", "procedure", "expected", "check_points",
)
# What a case written with ``check`` must carry; the list above stays the rule of a case
# still written with ``execution_mode`` (an earlier document reads exactly as it did).
REQUIRED_CASE_FIELDS = ("procedure", "expected", "check")
_FIELD_ALIASES = {
    "category": "category", "kind": "category", "분류": "category", "分類": "category",
    "requirement": "requirement", "requirements": "requirement",
    "requirement_ref": "requirement", "covers": "requirement", "대상": "requirement",
    "검증 대상": "requirement", "요구사항": "requirement", "対象": "requirement",
    "execution_mode": "execution_mode", "mode": "execution_mode",
    "content": "content", "내용": "content", "内容": "content",
    "check": "check", "확인": "check", "확인 방식": "check", "確認": "check",
    "checker": "checker", "확인자": "checker", "確認者": "checker",
    "실행 방식": "execution_mode", "実行方式": "execution_mode",
    "required": "required", "필수": "required", "必須": "required",
    "precondition": "precondition", "preconditions": "precondition",
    "전제조건": "precondition", "전제": "precondition", "前提条件": "precondition",
    "前提": "precondition",
    "input": "input", "inputs": "input", "입력": "input", "입력/조건": "input",
    "入力": "input",
    "procedure": "procedure", "steps": "procedure", "절차": "procedure",
    "시험 절차": "procedure", "手順": "procedure",
    "expected": "expected", "expect": "expected", "기대": "expected",
    "기대 결과": "expected", "期待結果": "expected", "期待": "expected",
    "check_points": "check_points", "checkpoints": "check_points",
    "확인 관점": "check_points", "確認観点": "check_points",
    "automation_ref": "automation_ref", "자동화 참조": "automation_ref",
    "실행 대상": "automation_ref", "実行対象": "automation_ref",
    "run_command": "run_command", "실행 명령": "run_command", "実行コマンド": "run_command",
    "run_cwd": "run_cwd", "실행 위치": "run_cwd", "実行場所": "run_cwd",
    "test_assets": "test_assets", "시험 자산": "test_assets",
}
# Legacy executable-TS field names. Inside a contract-2 TS they are a boundary violation,
# not a silently ignored extra: the whole point of the marker is that a specification TS is
# never half-read as an executable one ("legacy parser fallback must not misread").
_LEGACY_FIELDS = {"cmd", "assert", "기동", "start", "대기", "wait"}

_TRUE_WORDS = {"true", "yes", "y", "1", "required", "필수", "必須", "o"}
_FALSE_WORDS = {"false", "no", "n", "0", "optional", "선택", "任意", "x"}

_CASE_HEADING_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+)\s*:\s*(.*)$")
_FIELD_RE = re.compile(r"^-\s*([^:]+?)\s*:\s?(.*)$")
_NUMBERED_ID_RE = re.compile(r"^([A-Z][A-Z0-9]*)-0*(\d+)$")
_JUNIT_ID_RE = re.compile(r"(?<![A-Za-z0-9])TC[-_]?(\d+)(?![0-9])", re.IGNORECASE)


class SpecResultError(ValueError):
    """A result submission that cannot be accepted (422)."""

    def __init__(self, code: str, detail: str, errors: Optional[list] = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.errors = errors or []


# ── Contract marker ──────────────────────────────────────────────────────────────


def split_frontmatter(content: str) -> tuple[dict, str, str]:
    """Return ``(fields, frontmatter_block, body)``.

    ``frontmatter_block`` is the raw ``---`` … ``---`` text (without trailing newline) or an
    empty string, so an editor can round-trip it untouched.
    """
    text = (content or "").replace("\r\n", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, "", text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, "", text
    block = "\n".join(lines[: end + 1])
    body = "\n".join(lines[end + 1:])
    fields: dict = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip('"').strip("'")
    return fields, block, body


def detect_contract_version(content: str) -> Optional[int]:
    """1 for a TS without the marker (legacy executable), else the declared integer.

    Returns ``None`` when the marker is present but not an integer — the caller reports it
    as ``unsupported_contract`` rather than falling back to the legacy parser.
    """
    fields, _block, _body = split_frontmatter(content)
    raw = fields.get(CONTRACT_KEY)
    if raw is None or raw == "":
        return CONTRACT_LEGACY
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def is_spec_content(content: str) -> bool:
    return detect_contract_version(content) == CONTRACT_SPEC


def ensure_spec_frontmatter(block: str) -> str:
    """Return a frontmatter block that declares contract 2, keeping every other line."""
    if not block:
        return f"---\n{CONTRACT_KEY}: {CONTRACT_SPEC}\n---"
    lines = block.split("\n")
    inner = [ln for ln in lines[1:-1] if not ln.split(":", 1)[0].strip() == CONTRACT_KEY]
    return "\n".join(["---", *inner, f"{CONTRACT_KEY}: {CONTRACT_SPEC}", "---"])


# ── Specification parsing / validation ──────────────────────────────────────────


def normalize_case_id(raw: str) -> str:
    return (raw or "").strip().upper()


def case_id_key(case_id: str) -> str:
    """Matching key: ``TC-1`` / ``tc-001`` / ``TC-0001`` are the same case."""
    norm = normalize_case_id(case_id).replace("_", "-")
    match = _NUMBERED_ID_RE.match(norm)
    if match:
        return f"{match.group(1)}-{int(match.group(2))}"
    return norm


def parse_bool(raw: Any) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    word = str(raw or "").strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    return None


def _err(code: str, message: str, *, case_id: Optional[str] = None,
         field: Optional[str] = None) -> dict:
    return {"code": code, "case_id": case_id, "field": field, "message": message}


def _section_lines(body: str) -> Optional[list[str]]:
    lines = body.split("\n")
    headings = {f"## {name}" for name in SPEC_SECTION_NAMES}
    start = next((i + 1 for i, ln in enumerate(lines) if ln.strip() in headings), None)
    if start is None:
        return None
    out: list[str] = []
    for line in lines[start:]:
        if line.startswith("## ") and not line.startswith("### "):
            break
        out.append(line)
    return out


def parse_spec(content: str) -> dict:
    """Parse a specification TS. Never raises; every problem is an entry in ``errors``.

    Returns ``{"contract_version", "title", "cases", "errors"}``. ``cases`` holds the
    cases that could be read (even when other cases have errors) so an editor can show and
    fix them; approval/result admission require ``errors == []``.
    """
    version = detect_contract_version(content)
    _fields, _block, body = split_frontmatter(content)
    title = next(
        (ln[2:].strip() for ln in body.split("\n") if ln.startswith("# ")), ""
    )
    errors: list[dict] = []
    if version != CONTRACT_SPEC:
        errors.append(_err(
            "unsupported_contract",
            f"{CONTRACT_KEY} must be {CONTRACT_SPEC} for a specification TS "
            f"(found {version!r}).",
        ))
        return {"contract_version": version, "title": title, "cases": [], "errors": errors}

    section = _section_lines(body)
    if section is None:
        errors.append(_err(
            "missing_spec_section",
            "No '## Test Specification' (## 시험 사양 / ## 試験仕様) section.",
        ))
        return {"contract_version": version, "title": title, "cases": [], "errors": errors}

    blocks: list[tuple[str, list[str]]] = []
    heading: Optional[str] = None
    lines: list[str] = []
    for line in section:
        if line.startswith("### "):
            if heading is not None:
                blocks.append((heading, lines))
            heading, lines = line[4:].strip(), []
        elif heading is not None:
            lines.append(line)
    if heading is not None:
        blocks.append((heading, lines))
    if not blocks:
        errors.append(_err("no_cases", "The specification has no '### <Case ID>: <title>' case."))
        return {"contract_version": version, "title": title, "cases": [], "errors": errors}
    if len(blocks) > MAX_SPEC_CASES:
        errors.append(_err("too_many_cases", f"At most {MAX_SPEC_CASES} cases are allowed."))

    cases: list[dict] = []
    seen: dict[str, str] = {}
    for heading, block_lines in blocks[:MAX_SPEC_CASES]:
        match = _CASE_HEADING_RE.match(heading)
        if match is None:
            errors.append(_err(
                "invalid_case_heading",
                f"'{heading}': a case heading must be '### <Case ID>: <title>' "
                "(e.g. '### TC-001: ...').",
            ))
            continue
        case_id = normalize_case_id(match.group(1))
        case_title = match.group(2).strip()
        key = case_id_key(case_id)
        if key in seen:
            errors.append(_err(
                "duplicate_case_id",
                f"{case_id}: duplicate Case ID (already used by {seen[key]}).",
                case_id=case_id,
            ))
            continue
        seen[key] = case_id
        values: dict[str, str] = {}
        current: Optional[str] = None
        for raw in block_lines:
            if not raw.strip():
                continue
            stripped = raw.strip()
            field_match = _FIELD_RE.match(stripped) if not raw.startswith((" ", "\t")) else None
            if field_match:
                name = field_match.group(1).strip()
                lowered = name.lower()
                if lowered in _LEGACY_FIELDS or name in _LEGACY_FIELDS:
                    errors.append(_err(
                        "legacy_field_in_spec",
                        f"{case_id}: '{name}' is a legacy executable-TS field and is not "
                        f"allowed in a specification TS ({CONTRACT_KEY}: {CONTRACT_SPEC}).",
                        case_id=case_id, field=name,
                    ))
                    current = None
                    continue
                canonical = _FIELD_ALIASES.get(lowered) or _FIELD_ALIASES.get(name)
                if canonical is None:
                    errors.append(_err(
                        "unknown_field",
                        f"{case_id}: unknown field '{name}'.", case_id=case_id, field=name,
                    ))
                    current = None
                    continue
                values[canonical] = field_match.group(2).strip()
                current = canonical
            elif current is not None:
                # Continuation line (indented, or a bare numbered step) of the last field.
                joined = values.get(current, "")
                values[current] = f"{joined}\n{stripped}" if joined else stripped
        case = _build_case(case_id, case_title, values, errors)
        cases.append(case)

    return {"contract_version": version, "title": title, "cases": cases, "errors": errors}


def check_of_mode(mode: Any) -> tuple[Optional[str], Optional[str]]:
    """``(check, checker)`` of an execution mode; ``(None, None)`` for an unknown one."""
    return _CHECK_BY_MODE.get(str(mode or "").strip().lower(), (None, None))


def mode_of_check(check: Any, checker: Any = None) -> Optional[str]:
    """The execution mode the runner reads for a check (a manual case defaults to ``ai``)."""
    word = str(check or "").strip().lower()
    if word == "code":
        return "automated"
    if word == "manual":
        return _MODE_BY_CHECKER.get(str(checker or "").strip().lower(), "ai")
    return None


def merged_content(case: dict) -> str:
    """The case's content; an earlier case reads its requirement and precondition as one."""
    own = str(case.get("content") or "").strip()
    if own:
        return own
    return "\n".join(part for part in (
        str(case.get("requirement") or "").strip(), str(case.get("precondition") or "").strip(),
    ) if part)


def case_check(case: dict) -> tuple[str, Optional[str]]:
    """``(check, checker)`` of a case dict in either wording; ``check`` wins when both are set.

    An unknown word is returned as it is so the validator can name it.
    """
    check = str(case.get("check") or "").strip().lower()
    checker = str(case.get("checker") or "").strip().lower() or None
    if check:
        return check, (checker if check == "manual" else None)
    mode = str(case.get("execution_mode") or "").strip().lower()
    derived, who = check_of_mode(mode)
    return (derived or mode), (checker or who if derived == "manual" else None)


def case_result_of(status: Any) -> str:
    """``not_run`` / ``pass`` / ``fail`` / ``skip`` of a stored verdict (BLOCKED reads as skip)."""
    return _RESULT_BY_STATUS.get(str(status or "NOT_RUN").strip().upper(), "not_run")


def status_of_case_result(result: Any) -> Optional[str]:
    """The stored verdict of a case result word; ``None`` for an unknown one."""
    return _STATUS_BY_RESULT.get(str(result or "").strip().lower())


def _build_case(case_id: str, title: str, values: dict, errors: list[dict]) -> dict:
    if not title:
        errors.append(_err("missing_field", f"{case_id}: title is required.",
                           case_id=case_id, field="title"))
    # A case that names its execution_mode and no check is an earlier case: it keeps the
    # rules it was written under. Every other case is judged as an F81 case.
    earlier = "execution_mode" in values and "check" not in values
    for field in (REQUIRED_SPEC_FIELDS if earlier else REQUIRED_CASE_FIELDS):
        if not (values.get(field) or "").strip():
            errors.append(_err("missing_field", f"{case_id}: required field '{field}' missing.",
                               case_id=case_id, field=field))
    check, checker = case_check(values)
    if not earlier:
        if check and check not in CHECK_METHODS:
            errors.append(_err(
                "invalid_check",
                f"{case_id}: check must be one of {', '.join(CHECK_METHODS)} (got '{check}').",
                case_id=case_id, field="check",
            ))
        raw_checker = (values.get("checker") or "").strip().lower()
        if raw_checker and raw_checker not in CHECKERS:
            errors.append(_err(
                "invalid_checker",
                f"{case_id}: checker must be one of {', '.join(CHECKERS)} (got '{raw_checker}').",
                case_id=case_id, field="checker",
            ))
            checker = None
    category = (values.get("category") or "").strip().lower()
    if category and category not in CATEGORIES:
        errors.append(_err(
            "invalid_category",
            f"{case_id}: category must be one of {', '.join(CATEGORIES)} (got '{category}').",
            case_id=case_id, field="category",
        ))
    mode = (values.get("execution_mode") or "").strip().lower()
    if earlier and mode and mode not in EXECUTION_MODES:
        errors.append(_err(
            "invalid_execution_mode",
            f"{case_id}: execution_mode must be one of {', '.join(EXECUTION_MODES)} "
            f"(got '{mode}').",
            case_id=case_id, field="execution_mode",
        ))
    if not earlier:
        # The runner and the gate still read execution_mode; it follows the check.
        mode = mode_of_check(check, checker) or ""
    if check not in CHECK_METHODS:
        check, checker = "", None
    required_raw = values.get("required")
    required = parse_bool(required_raw) if required_raw not in (None, "") else None
    if required_raw not in (None, "") and required is None:
        errors.append(_err(
            "invalid_required",
            f"{case_id}: required must be true or false (got '{required_raw}').",
            case_id=case_id, field="required",
        ))
    for field, value in values.items():
        if len(value) > MAX_FIELD_CHARS:
            errors.append(_err("field_too_long",
                               f"{case_id}: '{field}' exceeds {MAX_FIELD_CHARS} characters.",
                               case_id=case_id, field=field))
    return {
        "case_id": case_id,
        "title": title,
        "category": category or None,
        "requirement": values.get("requirement") or "",
        "content": merged_content(values),
        "check": check or None,
        "checker": checker,
        "execution_mode": mode or None,
        "required": True if required is None else required,
        "precondition": values.get("precondition") or "",
        "input": values.get("input") or "",
        "procedure": values.get("procedure") or "",
        "expected": values.get("expected") or "",
        "check_points": values.get("check_points") or "",
        "automation_ref": values.get("automation_ref") or "",
        "run_command": values.get("run_command") or "",
        "run_cwd": values.get("run_cwd") or "",
        "test_assets": values.get("test_assets") or "",
    }


def validate_cases_payload(cases: Any) -> tuple[list[dict], list[dict]]:
    """Validate a structured-editor payload by rendering and re-parsing it.

    One grammar, one validator: the editor never has a second, looser rule set than the
    document body it produces.
    """
    if not isinstance(cases, list):
        return [], [_err("invalid_payload", "cases must be a list.")]
    if not cases:
        # Deleting the last case leaves an empty specification to author again; approval
        # and result intake still refuse it through parse_spec's ``no_cases``.
        return [], []
    body = render_spec_body(cases, title="validation")
    parsed = parse_spec(f"---\n{CONTRACT_KEY}: {CONTRACT_SPEC}\n---\n{body}")
    return parsed["cases"], parsed["errors"]


_RUN_SETTINGS = ("execution_mode", "automation_ref", "run_command", "run_cwd")


def unrunnable_case_errors(cases: list[dict], previous: list[dict]) -> list[dict]:
    """The editor's run check: an automated Case it changed must be runnable.

    Only a Case that is new or whose execution settings differ from ``previous`` (the
    stored cases) is judged, so a save never fails on a Case it did not touch. The judgment
    is the Automation Locator's -- the same one the run uses. Each refusal is a
    ``run_command`` field error with the locator's capability as its code.
    """
    from modules.flow_gate.services import test_basis_service

    stored = {case_id_key(case["case_id"]): case for case in previous}
    errors = []
    for case in cases:
        if case.get("execution_mode") != "automated":
            continue
        before = stored.get(case_id_key(case["case_id"]))
        if before is not None and all((before.get(key) or "") == (case.get(key) or "")
                                      for key in _RUN_SETTINGS):
            continue
        loc = test_basis_service.locator(case)
        if loc["capability"] in ("case_selectable", "suite_only"):
            continue
        detail = f" ({loc['reason']})" if loc.get("reason") else ""
        errors.append(_err(loc["capability"], f"{case['case_id']}: run_command cannot run "
                           f"({loc['capability']}){detail}.", case_id=case["case_id"],
                           field="run_command"))
    return errors


# ── Canonical rendering (structured editor → document body) ─────────────────────


def _render_value(value: Any) -> str:
    text = str(value if value is not None else "").replace("\r\n", "\n").strip()
    if "\n" not in text:
        return text
    first, *rest = text.split("\n")
    return "\n".join([first, *(f"  {line.strip()}" for line in rest)])


def render_spec_body(cases: Iterable[dict], *, title: str, locale: str = "ko",
                     intro: str = "") -> str:
    """Canonical Markdown body (no frontmatter) for a list of structured cases."""
    section = _SPEC_SECTION_BY_LOCALE.get(locale) or _SPEC_SECTION_BY_LOCALE["ko"]
    out = [f"# {title}".rstrip(), ""]
    if intro.strip():
        out.extend([intro.strip(), ""])
    out.extend([f"## {section}", ""])
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = normalize_case_id(str(case.get("case_id") or ""))
        out.append(f"### {case_id}: {str(case.get('title') or '').strip()}".rstrip())
        check, checker = case_check(case)
        for field in SPEC_FIELDS:
            value = case.get(field)
            if field == "content":
                value = merged_content(case)
            elif field == "check":
                value = check
            elif field == "checker":
                value = checker
            elif field == "run_command" and check == "manual":
                # A manual case has no command; one left from before it was manual is dropped.
                value = ""
            elif field == "required":
                # Every case counts; only a case kept as optional says so.
                parsed = parse_bool(value)
                if value in (None, "") or parsed is True:
                    continue
                value = "false" if parsed is False else str(value)
            if field in OPTIONAL_SPEC_FIELDS and not str(value or "").strip():
                continue
            out.append(f"- {field}: {_render_value(value)}".rstrip())
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_spec_document(cases: Iterable[dict], *, title: str, frontmatter_block: str = "",
                         locale: str = "ko", intro: str = "", trailing_body: str = "") -> str:
    block = ensure_spec_frontmatter(frontmatter_block)
    content = f"{block}\n{render_spec_body(cases, title=title, locale=locale, intro=intro)}"
    suffix = trailing_body.strip("\n")
    return content if not suffix.strip() else f"{content.rstrip()}\n\n{suffix}\n"


def replace_spec_cases(content: str, cases: Iterable[dict], *, title: str, locale: str = "ko") -> str:
    """Replace only the specification section; prose outside it is never re-rendered."""
    _fields, block, body = split_frontmatter(content)
    lines = body.splitlines(keepends=True)
    headings = {f"## {name}" for name in SPEC_SECTION_NAMES}
    start = next((i for i, line in enumerate(lines) if line.strip() in headings), None)
    section = render_spec_body(cases, title=title, locale=locale).split("\n", 2)[2]
    if start is None:
        prefix = body if any(line.startswith("# ") for line in lines) else f"# {title}\n\n" + body
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        body = prefix + "\n" + section
    else:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        body = "".join(lines[:start]) + section + "".join(lines[end:])
    return ensure_spec_frontmatter(block) + "\n" + body


def brief_parts(content: str) -> tuple[str, str, str]:
    """Keep frontmatter/title and every H2 section apart from editable introductory prose."""
    _fields, block, body = split_frontmatter(content)
    lines = body.splitlines(keepends=True)
    first_section = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
    title = next((i for i in range(first_section) if lines[i].startswith("# ")), None)
    start = title + 1 if title is not None else 0
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    prefix = (block + "\n" if block else "") + "".join(lines[:start])
    return prefix, "".join(lines[start:end]).strip(), "".join(lines[end:])


def replace_brief(content: str, text: str) -> str:
    prefix, _old, suffix = brief_parts(content)
    return prefix + "\n" + text.strip() + "\n\n" + suffix


def spec_trailing_body(content: str) -> str:
    """Preserve everything after the spec's next H2, using the parser's boundary.

    No spec section means the whole body remains intro; never duplicate it here.
    The suffix comes from the current server document, not an editor's old copy.
    """
    _fields, _block, body = split_frontmatter(content)
    lines = body.split("\n")
    headings = {f"## {name}" for name in SPEC_SECTION_NAMES}
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() in headings), None)
    if start is None:
        return ""
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("## ") and not lines[i].startswith("### ")), None)
    return "" if end is None else "\n".join(lines[end:]).strip("\n")


def spec_intro(content: str) -> str:
    """Free text between the H1 title and the specification section (kept on re-render)."""
    _fields, _block, body = split_frontmatter(content)
    lines = body.split("\n")
    headings = {f"## {name}" for name in SPEC_SECTION_NAMES}
    out: list[str] = []
    started = not any(line.startswith("# ") for line in lines)
    for line in lines:
        if not started:
            if line.startswith("# "):
                started = True
            continue
        if line.strip() in headings:
            break
        out.append(line)
    return "\n".join(out).strip()


# ── Result normalisation ──────────────────────────────────────────────────────────


def _clip(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def normalize_status(raw: Any) -> Optional[str]:
    word = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "PASS": "PASS", "PASSED": "PASS", "OK": "PASS", "SUCCESS": "PASS",
        "FAIL": "FAIL", "FAILED": "FAIL", "FAILURE": "FAIL", "ERROR": "FAIL",
        "BLOCKED": "BLOCKED", "BLOCK": "BLOCKED",
        "NOT_RUN": "NOT_RUN", "NOTRUN": "NOT_RUN", "SKIPPED": "NOT_RUN", "SKIP": "NOT_RUN",
        "NOT_EXECUTED": "NOT_RUN",
    }
    return aliases.get(word)


def _normalize_source_identity(raw: Any) -> dict:
    if not isinstance(raw, dict):
        return {}
    keys = (
        "bundle_id", "bundle_hash", "git_revision", "tree", "worktree", "branch",
        "runner", "runner_version", "tool", "tool_version", "executed_at", "ci_url",
        # Basis identity; bundle_sha256 is read only from v2 rows recorded before
        # removed Source Bundle (stored results stay as written, the earlier implementation).
        "kind", "basis_id", "content_fingerprint", "bundle_sha256", "exclusion_policy_version",
    )
    return {key: _clip(raw.get(key), 500) for key in keys if raw.get(key) not in (None, "")}


def normalize_source_identity(raw: Any) -> dict:
    """Public form of the submission-level source identity filter."""
    return _normalize_source_identity(raw)


def _normalize_evidence(raw: Any, case_ref: str, errors: list[dict]) -> list[dict]:
    if raw in (None, ""):
        return []
    items = raw if isinstance(raw, list) else [raw]
    out: list[dict] = []
    for item in items[:MAX_EVIDENCE_PER_RESULT]:
        if isinstance(item, str):
            item = {"kind": "text", "value": item}
        if not isinstance(item, dict):
            errors.append(_err("invalid_evidence", f"{case_ref}: evidence items must be objects.",
                               case_id=case_ref, field="evidence"))
            continue
        kind = str(item.get("kind") or "text").strip().lower()
        if kind not in EVIDENCE_KINDS:
            errors.append(_err(
                "invalid_evidence",
                f"{case_ref}: evidence kind must be one of {', '.join(EVIDENCE_KINDS)}.",
                case_id=case_ref, field="evidence",
            ))
            continue
        value = item.get("value") if item.get("value") is not None else item.get("ref")
        if value in (None, ""):
            errors.append(_err("invalid_evidence", f"{case_ref}: evidence value is empty.",
                               case_id=case_ref, field="evidence"))
            continue
        entry = {"kind": kind, "value": _clip(value)}
        if item.get("label"):
            entry["label"] = _clip(item.get("label"), 200)
        out.append(entry)
    return out


def normalize_results(raw_results: Any, *, submitted_by: str, now: str,
                      default_source_identity: Optional[dict] = None,
                      default_origin: str = "payload",
                      actor_kind: Optional[str] = None) -> list[dict]:
    """Validate an explicit result payload. Raises SpecResultError listing every problem."""
    if raw_results in (None, ""):
        return []
    if not isinstance(raw_results, list):
        raise SpecResultError("invalid_results", "results must be a list.")
    if len(raw_results) > MAX_RESULTS:
        raise SpecResultError("invalid_results", f"At most {MAX_RESULTS} results per submission.")
    base_identity = _normalize_source_identity(default_source_identity)
    errors: list[dict] = []
    out: list[dict] = []
    for index, item in enumerate(raw_results, start=1):
        ref = f"results[{index}]"
        if not isinstance(item, dict):
            errors.append(_err("invalid_result", f"{ref}: each result must be an object."))
            continue
        raw_id = item.get("case_id")
        case_id = normalize_case_id(str(raw_id)) if raw_id not in (None, "") else ""
        # F81: ``result`` (not_run / pass / fail / skip) is the case result as the case
        # window records it; it wins over ``status`` and a skip must say why.
        raw_result = item.get("result")
        skip_reason = _clip(item.get("skip_reason"), 500).strip()
        if raw_result not in (None, ""):
            status = status_of_case_result(raw_result)
            if status is None:
                errors.append(_err(
                    "invalid_status",
                    f"{ref}: result must be one of {', '.join(CASE_RESULTS)} (got {raw_result!r}).",
                    case_id=case_id or None, field="result",
                ))
                continue
            if status == "BLOCKED" and not skip_reason:
                errors.append(_err(
                    "skip_reason_required", f"{ref}: a skipped case needs a one-line skip_reason.",
                    case_id=case_id or None, field="skip_reason",
                ))
                continue
        else:
            status = normalize_status(item.get("status"))
            if status is None:
                errors.append(_err(
                    "invalid_status",
                    f"{ref}: status must be one of {', '.join(STATUSES)} (got {item.get('status')!r}).",
                    case_id=case_id or None, field="status",
                ))
                continue
        mode = item.get("execution_mode")
        mode = str(mode).strip().lower() if mode not in (None, "") else None
        if mode is None and item.get("check") not in (None, ""):
            mode = mode_of_check(item.get("check"), item.get("checker"))
        if mode is not None and mode not in EXECUTION_MODES:
            errors.append(_err("invalid_execution_mode",
                               f"{ref}: execution_mode must be one of {', '.join(EXECUTION_MODES)}.",
                               case_id=case_id or None, field="execution_mode"))
            continue
        evidence = _normalize_evidence(item.get("evidence"), case_id or ref, errors)
        identity = {**base_identity, **_normalize_source_identity(item.get("source_identity"))}
        out.append({
            "case_id": case_id or None,
            "status": status,
            "actual": _clip(item.get("actual")),
            "evidence": evidence,
            "defect_ref": _clip(item.get("defect_ref") or item.get("reference"), 500),
            "execution_mode": mode,
            "checked_by": _clip(item.get("checked_by") or submitted_by, 200),
            "checked_at": _clip(item.get("checked_at") or now, 64),
            "note": _clip(item.get("note")),
            "skip_reason": skip_reason if status == "BLOCKED" else "",
            "result_by": normalize_result_by(
                item.get("result_by"), default_kind=actor_kind,
                default_name=item.get("checked_by") or submitted_by),
            "source_name": _clip(item.get("source_name") or item.get("test_name"), 500),
            "source_identity": identity,
            "origin": default_origin,
        })
    if errors:
        raise SpecResultError(
            "invalid_results", "; ".join(e["message"] for e in errors[:10]), errors
        )
    return out


def normalize_result_by(raw: Any, *, default_kind: Optional[str] = None,
                        default_name: Any = None) -> Optional[dict]:
    """Who recorded a case result: ``{"kind": "ai" | "human", "name": <shown name>}``.

    ``None`` when neither the item nor the caller says who (an imported report).
    """
    row = raw if isinstance(raw, dict) else {}
    kind = str(row.get("kind") or default_kind or "").strip().lower()
    if kind not in RESULT_ACTOR_KINDS:
        return None
    return {"kind": kind, "name": _clip(row.get("name") or default_name or "", 200)}


def parse_junit_xml(xml_text: str, *, submitted_by: str, now: str,
                    default_source_identity: Optional[dict] = None) -> list[dict]:
    """JUnit XML adapter (pytest ``--junitxml``, Vitest/Jest junit reporters, Maven …).

    Case ID linkage, first match wins: a ``flowgate.case_id`` (or ``case_id``) property on
    the testcase, then a ``TC-<n>`` / ``TC_<n>`` token in the test name, then in the class
    name. A testcase with no Case ID is kept with ``case_id=None`` so the mapping step can
    report it as an unmapped result instead of dropping it.
    """
    if not isinstance(xml_text, str) or not xml_text.strip():
        raise SpecResultError("invalid_junit_xml", "junit_xml is empty.")
    if len(xml_text.encode("utf-8")) > MAX_JUNIT_BYTES:
        raise SpecResultError("invalid_junit_xml", f"junit_xml exceeds {MAX_JUNIT_BYTES} bytes.")
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        # Entity declarations are how XML bombs and external-entity reads are built; a
        # test report never needs them.
        raise SpecResultError("invalid_junit_xml", "DOCTYPE/ENTITY declarations are not allowed.")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SpecResultError("invalid_junit_xml", f"junit_xml is not well-formed XML: {exc}") from exc

    base_identity = _normalize_source_identity(default_source_identity)
    results: list[dict] = []
    for testcase in root.iter("testcase"):
        if len(results) >= MAX_RESULTS:
            break
        name = testcase.get("name") or ""
        classname = testcase.get("classname") or ""
        props = {
            (prop.get("name") or "").strip(): (prop.get("value") or "").strip()
            for prop in testcase.iter("property")
        }
        case_id = props.get("flowgate.case_id") or props.get("case_id") or ""
        if not case_id:
            for source in (name, classname):
                match = _JUNIT_ID_RE.search(source)
                if match:
                    case_id = f"TC-{match.group(1)}"
                    break
        failure = testcase.find("failure")
        error = testcase.find("error")
        skipped = testcase.find("skipped")
        if failure is not None or error is not None:
            node = failure if failure is not None else error
            status = "FAIL"
            actual = " ".join(
                part for part in (node.get("message") or "", (node.text or "").strip()) if part
            )
        elif skipped is not None:
            status = "NOT_RUN"
            actual = skipped.get("message") or "skipped"
        else:
            status = "PASS"
            actual = "passed"
        source_name = f"{classname}::{name}" if classname else name
        evidence = [{
            "kind": "structured",
            "value": _clip(f"junit {source_name} status={status} time={testcase.get('time') or '-'}s"),
        }]
        system_out = testcase.find("system-out")
        if system_out is not None and (system_out.text or "").strip():
            evidence.append({"kind": "log", "value": _clip(system_out.text.strip()[-2000:])})
        results.append({
            "case_id": normalize_case_id(case_id) or None,
            "status": status,
            "actual": _clip(actual),
            "evidence": evidence,
            "defect_ref": "",
            "execution_mode": "automated",
            "checked_by": _clip(submitted_by, 200),
            "checked_at": now,
            "note": "",
            "source_name": _clip(source_name, 500),
            "source_identity": dict(base_identity),
            "origin": "junit_xml",
        })
    if not results:
        raise SpecResultError("invalid_junit_xml", "junit_xml contains no <testcase> element.")
    return results


# ── Mapping and overall ──────────────────────────────────────────────────────────

_SEVERITY = {"FAIL": 3, "BLOCKED": 2, "NOT_RUN": 1, "PASS": 0}


def map_results(spec_cases: list[dict], results: list[dict], *,
                previous: Optional[dict] = None, previous_run_id: Optional[str] = None) -> dict:
    """Map results onto TS cases by explicit Case ID.

    * a TS case with no result → NOT_RUN (or the previous submission's verdict, carried
      forward with its original evidence, when ``previous`` is given);
    * a result whose Case ID is absent from the TS (or missing) → ``unmapped``;
    * more than one result for one case in the same submission → ``conflicts``; the case
      becomes FAIL if any of them failed, otherwise BLOCKED — a gate never passes on an
      ambiguous mapping.
    """
    by_key: dict[str, list[dict]] = {}
    unmapped: list[dict] = []
    spec_keys = {case_id_key(case["case_id"]): case for case in spec_cases}
    for result in results:
        key = case_id_key(result.get("case_id") or "") if result.get("case_id") else ""
        if not key or key not in spec_keys:
            unmapped.append({
                "case_id": result.get("case_id"),
                "status": result.get("status"),
                "source_name": result.get("source_name"),
                "actual": result.get("actual"),
                "origin": result.get("origin"),
                "reason": "missing_case_id" if not key else "unknown_case_id",
            })
            continue
        by_key.setdefault(key, []).append(result)

    previous = previous or {}
    rows: list[dict] = []
    conflicts: list[dict] = []
    for case in spec_cases:
        key = case_id_key(case["case_id"])
        mapped = by_key.get(key, [])
        base = {
            "case_id": case["case_id"],
            "title": case.get("title") or "",
            "category": case.get("category"),
            "requirement": case.get("requirement") or "",
            "required": bool(case.get("required")),
            "precondition": case.get("precondition") or "",
            "input": case.get("input") or "",
            "procedure": case.get("procedure") or "",
            "expected": case.get("expected") or "",
            "check_points": case.get("check_points") or "",
            "automation_ref": case.get("automation_ref") or "",
            "run_command": case.get("run_command") or "",
            "spec_execution_mode": case.get("execution_mode"),
            "content": merged_content(case),
            "check": case_check(case)[0] or None,
            "checker": case_check(case)[1],
            "skip_reason": "",
            "result_by": None,
        }
        if len(mapped) == 1:
            result = mapped[0]
            rows.append({
                **base,
                "status": result["status"],
                "skip_reason": result.get("skip_reason") or "",
                "result_by": result.get("result_by"),
                "actual": result.get("actual") or "",
                "evidence": result.get("evidence") or [],
                "defect_ref": result.get("defect_ref") or "",
                "execution_mode": result.get("execution_mode") or case.get("execution_mode"),
                "checked_by": result.get("checked_by") or "",
                "checked_at": result.get("checked_at") or "",
                "note": result.get("note") or "",
                "source_name": result.get("source_name") or "",
                "source_identity": result.get("source_identity") or {},
                "result_origin": result.get("origin") or "payload",
                "mapping_conflict": False,
                "result_count": 1,
                "carried_from_run_id": None,
            })
            continue
        if len(mapped) > 1:
            worst = max(mapped, key=lambda r: _SEVERITY.get(r["status"], 0))
            status = "FAIL" if worst["status"] == "FAIL" else "BLOCKED"
            conflicts.append({
                "case_id": case["case_id"],
                "result_count": len(mapped),
                "statuses": [r["status"] for r in mapped],
                "source_names": [r.get("source_name") for r in mapped],
            })
            evidence: list[dict] = []
            for r in mapped:
                evidence.extend(r.get("evidence") or [])
            rows.append({
                **base,
                "status": status,
                "actual": "; ".join(
                    f"[{r['status']}] {r.get('source_name') or '-'}: {r.get('actual') or ''}".strip()
                    for r in mapped
                ),
                "evidence": evidence[:MAX_EVIDENCE_PER_RESULT],
                "defect_ref": next((r.get("defect_ref") for r in mapped if r.get("defect_ref")), ""),
                "execution_mode": worst.get("execution_mode") or case.get("execution_mode"),
                "checked_by": worst.get("checked_by") or "",
                "checked_at": worst.get("checked_at") or "",
                "note": "mapping_conflict",
                "source_name": ", ".join(r.get("source_name") or "-" for r in mapped),
                "source_identity": worst.get("source_identity") or {},
                "result_origin": "conflict",
                "mapping_conflict": True,
                "result_count": len(mapped),
                "carried_from_run_id": None,
            })
            continue
        carried = previous.get(key)
        if carried is not None:
            rows.append({
                **base,
                **{k: carried.get(k) for k in (
                    "status", "actual", "evidence", "defect_ref", "execution_mode",
                    "checked_by", "checked_at", "note", "source_name", "source_identity",
                    "mapping_conflict", "result_count", "skip_reason", "result_by",
                )},
                "result_origin": "carried",
                "carried_from_run_id": carried.get("carried_from_run_id") or previous_run_id,
            })
            continue
        rows.append({
            **base,
            "status": "NOT_RUN",
            "actual": "",
            "evidence": [],
            "defect_ref": "",
            "execution_mode": case.get("execution_mode"),
            "checked_by": "",
            "checked_at": "",
            "note": "",
            "source_name": "",
            "source_identity": {},
            "result_origin": "missing",
            "mapping_conflict": False,
            "result_count": 0,
            "carried_from_run_id": None,
        })
    return {"cases": rows, "unmapped": unmapped, "conflicts": conflicts}


def _counts(rows: list[dict]) -> dict:
    counts = {"total": len(rows), "pass": 0, "fail": 0, "blocked": 0, "not_run": 0}
    for row in rows:
        counts[str(row.get("status") or "NOT_RUN").lower()] = (
            counts.get(str(row.get("status") or "NOT_RUN").lower(), 0) + 1
        )
    return counts


def compute_overall(rows: list[dict]) -> dict:
    """Server-authoritative verdict, computed from REQUIRED cases only.

    required FAIL → FAIL; else required BLOCKED → BLOCKED; else required NOT_RUN → NOT_RUN
    (gate blocked); else PASS. Optional cases are counted and shown but never move the gate.
    An all-optional specification has no blocking cases; an
    empty row list is never PASS.
    """
    required = [row for row in rows if row.get("required")]
    statuses = {row.get("status") for row in required}
    if not required:
        overall = "PASS" if rows else "NOT_RUN"
    elif "FAIL" in statuses:
        overall = "FAIL"
    elif "BLOCKED" in statuses:
        overall = "BLOCKED"
    elif "NOT_RUN" in statuses:
        overall = "NOT_RUN"
    else:
        overall = "PASS"
    optional = [row for row in rows if not row.get("required")]
    return {
        "overall": overall,
        "gate_passed": overall == "PASS",
        "counts": _counts(rows),
        "required_counts": _counts(required),
        "optional_counts": _counts(optional),
    }


def gate_error_code(overall: str) -> Optional[str]:
    return {
        "PASS": None,
        "FAIL": None,  # failure-origin owns the error column for a FAIL
        "BLOCKED": "spec_blocked",
        "NOT_RUN": "spec_required_not_run",
    }.get(overall)


# ── Stored row <-> case dict ─────────────────────────────────────────────────────

_CASE_META_KEYS = (
    "category", "requirement", "required", "precondition", "input", "procedure",
    "check_points", "automation_ref", "run_command", "spec_execution_mode", "evidence",
    "defect_ref", "execution_mode", "checked_by", "checked_at", "note", "source_name", "source_identity",
    "result_origin", "mapping_conflict", "result_count", "carried_from_run_id",
    "content", "check", "checker", "skip_reason", "result_by",
)


def case_row_to_meta(row: dict) -> str:
    return json.dumps({key: row.get(key) for key in _CASE_META_KEYS}, ensure_ascii=False)


def stored_case_to_row(stored: dict) -> dict:
    try:
        meta = json.loads(stored.get("case_meta") or "{}")
    except (TypeError, ValueError):
        meta = {}
    row = {
        "case_id": stored.get("case_no"),
        "title": stored.get("case_title") or "",
        "expected": stored.get("expect") or "",
        "status": stored.get("case_status") or "NOT_RUN",
        "actual": stored.get("actual") or "",
        **{key: meta.get(key) for key in _CASE_META_KEYS},
    }
    # A row stored before the F81 fields reads them from what it has.
    if not row.get("check"):
        row["check"], who = check_of_mode(row.get("spec_execution_mode") or row.get("execution_mode"))
        row["checker"] = row.get("checker") or who
    if row.get("content") is None:
        row["content"] = merged_content(row)
    row["skip_reason"] = row.get("skip_reason") or ""
    row["result"] = case_result_of(row["status"])
    return row


def case_result_counts(rows: Iterable[dict]) -> dict:
    """The scenario list's tally in the case-result words (the report's own numbers)."""
    counts = {"total": 0, **{word: 0 for word in CASE_RESULTS}}
    for row in rows:
        counts["total"] += 1
        counts[case_result_of(row.get("status"))] += 1
    return counts


# ── Remarks and evidence tags: shapes only ───────────────────────────────────────


def normalize_remark(raw: Any, *, author_kind: str, author_name: str,
                     default_phase: str = "case_write") -> dict:
    """Validate a new remark. Raises SpecResultError (``invalid_remark``)."""
    row = raw if isinstance(raw, dict) else {}
    text = str(row.get("text") or "").strip()
    if not text:
        raise SpecResultError("invalid_remark", "A remark needs its text.")
    if len(text) > MAX_REMARK_CHARS:
        raise SpecResultError("invalid_remark", f"A remark is at most {MAX_REMARK_CHARS} characters.")
    phase = str(row.get("phase") or default_phase).strip().lower()
    if phase not in REMARK_PHASES:
        raise SpecResultError(
            "invalid_remark", f"phase must be one of {', '.join(REMARK_PHASES)} (got '{phase}').")
    kind = str(author_kind or "").strip().lower()
    if kind not in REMARK_AUTHOR_KINDS:
        raise SpecResultError(
            "invalid_remark", f"author kind must be one of {', '.join(REMARK_AUTHOR_KINDS)}.")
    return {"text": text, "phase": phase, "author_kind": kind,
            "author_name": _clip(author_name or "", 200)}


def normalize_remark_state(raw: Any) -> str:
    state = str(raw or "").strip().lower()
    if state not in REMARK_STATES:
        raise SpecResultError(
            "invalid_remark", f"state must be one of {', '.join(REMARK_STATES)} (got '{state}').")
    return state


def normalize_remark_reply(raw: Any) -> str:
    """The AI's one-line answer to a remark (newlines folded)."""
    return _clip(" ".join(str(raw or "").split()), MAX_REMARK_REPLY_CHARS)


def shape_remark(row: dict) -> dict:
    """A stored remark as the API answers it."""
    return {
        "remark_id": row.get("remark_id"),
        "case_id": row.get("case_id"),
        "text": row.get("body") or "",
        "phase": row.get("phase"),
        "state": row.get("state") or "open",
        "author": {"kind": row.get("author_kind"), "name": row.get("author_name") or ""},
        "ai_reply": row.get("ai_reply") or None,
        "created_at": row.get("created_at"),
        "resolved_at": row.get("resolved_at"),
        "updated_at": row.get("updated_at"),
    }


def remark_counts(remarks: Iterable[dict]) -> dict:
    """``{case key: {"open": n, "resolved": m}}`` of shaped or stored remarks."""
    out: dict[str, dict] = {}
    for remark in remarks:
        entry = out.setdefault(case_id_key(remark.get("case_id") or ""), {"open": 0, "resolved": 0})
        entry["resolved" if remark.get("state") == "resolved" else "open"] += 1
    return out


def normalize_evidence_tag(raw: Any) -> dict:
    """The ``test_evidence`` meta of a flow attachment: case, kind and which attempt.

    ``{"case_id": "TC-001", "kind": "input" | "log" | "output", "attempt": 1}``. Raises
    SpecResultError (``invalid_evidence_tag``).
    """
    row = raw if isinstance(raw, dict) else {}
    case_id = normalize_case_id(str(row.get("case_id") or ""))
    kind = str(row.get("kind") or "").strip().lower()
    attempt = row.get("attempt", 1)
    if not case_id:
        raise SpecResultError("invalid_evidence_tag", "An evidence tag needs its case_id.")
    if kind not in EVIDENCE_TAG_KINDS:
        raise SpecResultError(
            "invalid_evidence_tag",
            f"kind must be one of {', '.join(EVIDENCE_TAG_KINDS)} (got '{kind}').")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise SpecResultError("invalid_evidence_tag", "attempt must be an integer from 1.")
    return {"case_id": case_id, "kind": kind, "attempt": attempt}


def evidence_counts(tags: Iterable[dict]) -> dict:
    """``{case key: {"input": n, "log": n, "output": n, "total": n}}`` of each case's latest attempt."""
    latest: dict[str, int] = {}
    kept: list[dict] = []
    for tag in tags:
        try:
            item = normalize_evidence_tag(tag)
        except SpecResultError:
            continue
        key = case_id_key(item["case_id"])
        latest[key] = max(latest.get(key, 0), item["attempt"])
        kept.append(item)
    out: dict[str, dict] = {}
    for item in kept:
        key = case_id_key(item["case_id"])
        if item["attempt"] != latest[key]:
            continue
        entry = out.setdefault(key, {**{kind: 0 for kind in EVIDENCE_TAG_KINDS}, "total": 0})
        entry[item["kind"]] += 1
        entry["total"] += 1
    return out


def load_result_meta(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# ── TSR report rendering ────────────────────────────────────────────────────────

_REPORT_STRINGS = {
    "ko": {
        "kind": "> 문서 성격: 시험성적서 (TS {doc_id} revision {revision} 의 Case ID별 결과)",
        "run": "> 결과 기록: {run_id} · 기록 시각 {at}",
        "overall": "> 종합 판정(서버 계산, 필수 Case 기준): **{overall}** — {gate}",
        "gate_pass": "시험 gate 통과",
        "gate_block": "시험 gate 미통과 · 다음 단계 진행 차단",
        "summary_heading": "## 요약",
        "summary_header": "| 구분 | 전체 | PASS | FAIL | BLOCKED | NOT_RUN |",
        "all": "전체", "required": "필수", "optional": "선택",
        "cases_heading": "## Case별 결과",
        "cases_header": "| Case | 제목 | 필수 | 방식 | 기대 결과 | 실제 결과 | 판정 | 결함/참조 |",
        "evidence_heading": "## Evidence 및 source identity",
        "unmapped_heading": "## TS에 없는 결과 (unmapped)",
        "conflicts_heading": "## 중복 매핑 (mapping conflict)",
        "none": "없음",
        "source": "source",
        "carried": "이전 기록에서 유지: {run_id}",
        "footer": "*이 시험성적서는 결과 기록 {run_id} 로부터 FlowGate가 조립했다. 종합 판정은 문서 본문이 아니라 서버 기록으로 결정된다.*",
        "yes": "필수", "no": "선택",
    },
    "en": {
        "kind": "> Document: test report (per-Case-ID results of TS {doc_id} revision {revision})",
        "run": "> Result record: {run_id} · recorded at {at}",
        "overall": "> Overall (server-computed from required cases): **{overall}** — {gate}",
        "gate_pass": "test gate passed",
        "gate_block": "test gate NOT passed · next step blocked",
        "summary_heading": "## Summary",
        "summary_header": "| Scope | Total | PASS | FAIL | BLOCKED | NOT_RUN |",
        "all": "All", "required": "Required", "optional": "Optional",
        "cases_heading": "## Results by Case",
        "cases_header": "| Case | Title | Required | Mode | Expected | Actual | Verdict | Defect/ref |",
        "evidence_heading": "## Evidence and source identity",
        "unmapped_heading": "## Results not in the TS (unmapped)",
        "conflicts_heading": "## Duplicate mappings (mapping conflict)",
        "none": "none",
        "source": "source",
        "carried": "carried over from {run_id}",
        "footer": "*FlowGate assembled this test report from result record {run_id}. The overall verdict is decided by the server record, not by this text.*",
        "yes": "required", "no": "optional",
    },
    "ja": {
        "kind": "> 文書種別: 試験成績書 (TS {doc_id} revision {revision} のCase ID別結果)",
        "run": "> 結果記録: {run_id} · 記録時刻 {at}",
        "overall": "> 総合判定(サーバー計算・必須Case基準): **{overall}** — {gate}",
        "gate_pass": "試験ゲート通過",
        "gate_block": "試験ゲート未通過 · 次の段階へ進めない",
        "summary_heading": "## 概要",
        "summary_header": "| 区分 | 全体 | PASS | FAIL | BLOCKED | NOT_RUN |",
        "all": "全体", "required": "必須", "optional": "任意",
        "cases_heading": "## Case別結果",
        "cases_header": "| Case | タイトル | 必須 | 方式 | 期待結果 | 実際の結果 | 判定 | 欠陥/参照 |",
        "evidence_heading": "## エビデンスとsource identity",
        "unmapped_heading": "## TSにない結果 (unmapped)",
        "conflicts_heading": "## 重複マッピング (mapping conflict)",
        "none": "なし",
        "source": "source",
        "carried": "以前の記録から維持: {run_id}",
        "footer": "*この試験成績書は結果記録 {run_id} からFlowGateが組み立てた。総合判定は本文ではなくサーバー記録で決まる。*",
        "yes": "必須", "no": "任意",
    },
}


def _cell(value: Any) -> str:
    text = str(value if value is not None else "").replace("\r\n", "\n").strip()
    text = text.replace("|", "\\|").replace("\n", "<br>")
    return text or "-"


def render_report_markdown(*, doc: dict, run: dict, rows: list[dict], summary: dict,
                           unmapped: list[dict], conflicts: list[dict], title: str,
                           locale: str = "ko") -> str:
    s = _REPORT_STRINGS.get(locale) or _REPORT_STRINGS["ko"]
    overall = summary.get("overall") or "NOT_RUN"
    lines = [
        f"# {title}",
        "",
        s["kind"].format(doc_id=doc.get("doc_id"), revision=run.get("revision_no")),
        s["run"].format(run_id=run.get("run_id"), at=run.get("finished_at") or run.get("created_at")),
        s["overall"].format(
            overall=overall, gate=s["gate_pass"] if overall == "PASS" else s["gate_block"]
        ),
        "",
        s["summary_heading"],
        "",
        s["summary_header"],
        "|---|---|---|---|---|---|",
    ]
    for label_key, counts_key in (("all", "counts"), ("required", "required_counts"),
                                  ("optional", "optional_counts")):
        c = summary.get(counts_key) or {}
        lines.append(
            f"| {s[label_key]} | {c.get('total', 0)} | {c.get('pass', 0)} | {c.get('fail', 0)} | "
            f"{c.get('blocked', 0)} | {c.get('not_run', 0)} |"
        )
    lines.extend(["", s["cases_heading"], "", s["cases_header"], "|---|---|---|---|---|---|---|---|"])
    for row in rows:
        lines.append(
            f"| {_cell(row.get('case_id'))} | {_cell(row.get('title'))} | "
            f"{s['yes'] if row.get('required') else s['no']} | {_cell(row.get('execution_mode'))} | "
            f"{_cell(row.get('expected'))} | {_cell(row.get('actual'))} | "
            f"**{_cell(row.get('status'))}** | {_cell(row.get('defect_ref'))} |"
        )
    lines.extend(["", s["evidence_heading"], ""])
    for row in rows:
        evidence = row.get("evidence") or []
        identity = row.get("source_identity") or {}
        detail: list[str] = []
        for item in evidence:
            label = f"{item.get('label')}: " if item.get("label") else ""
            detail.append(f"  - [{item.get('kind')}] {label}{_cell(item.get('value'))}")
        if identity:
            detail.append("  - " + s["source"] + ": " + ", ".join(
                f"{key}={value}" for key, value in identity.items()
            ))
        if row.get("carried_from_run_id"):
            detail.append("  - " + s["carried"].format(run_id=row.get("carried_from_run_id")))
        checked = " / ".join(x for x in (row.get("checked_by"), row.get("checked_at")) if x)
        lines.append(f"- {row.get('case_id')} ({row.get('status')}){' · ' + checked if checked else ''}")
        lines.extend(detail or [f"  - {s['none']}"])
    lines.extend(["", s["unmapped_heading"], ""])
    if unmapped:
        for item in unmapped:
            lines.append(
                f"- {item.get('case_id') or '-'} · {item.get('status')} · "
                f"{_cell(item.get('source_name'))} ({item.get('reason')})"
            )
    else:
        lines.append(f"- {s['none']}")
    lines.extend(["", s["conflicts_heading"], ""])
    if conflicts:
        for item in conflicts:
            lines.append(
                f"- {item.get('case_id')}: {item.get('result_count')} → "
                f"{', '.join(item.get('statuses') or [])}"
            )
    else:
        lines.append(f"- {s['none']}")
    lines.extend(["", s["footer"].format(run_id=run.get("run_id")), ""])
    return "\n".join(lines)


# the report a run opens before it has results. PENDING is a display
# state only -- never a stored verdict -- so this body carries no overall and no gate line.
PENDING = "PENDING"

_PENDING_STRINGS = {
    "ko": {
        "kind": "> 문서 성격: 시험성적서 (TS {doc_id} revision {revision} 의 Case ID별 결과)",
        "run": "> 시험 run: {run_id} · 접수 시각 {at}",
        "state_queued": "> 상태: **시험 대기/실행 중** — 결과가 기록되면 이 레포트가 갱신된다. 아직 판정이 없다.",
        "state_running": "> 상태: **시험 실행 중** — Case 결과 {done}/{total}건 반영. 남은 Case가 끝나면 결과가 기록되고 종합 판정이 정해진다.",
        "state_prepare_refused": "> 상태: **준비 실패** ({reason}) — 시험을 실행하지 않았다. 원인을 해결한 뒤 다시 실행한다.",
        "state_cancelled": "> 상태: **취소됨** — 시험 run이 결과 없이 취소됐다. 다시 실행한다.",
        "state_failed": "> 상태: **실행 중단** ({reason}) — 기록된 결과가 없다. 다시 실행한다.",
        "cases_heading": "## Case별 상태",
        "cases_header": "| Case | 제목 | 필수 | 방식 | 기대 결과 | 상태 |",
        "pending": "대기 (PENDING)", "not_selected": "이번 run 대상 아님", "no_result": "결과 없음",
        "kept": "{status} (이전 기록 유지)",
        "reported": "{status} (이번 run 결과)",
        "footer": "*이 레포트는 시험 run {run_id} 접수 시 FlowGate가 만들었다. 종합 판정은 결과가 기록된 뒤 서버 기록으로 결정된다.*",
        "yes": "필수", "no": "선택",
    },
    "en": {
        "kind": "> Document: test report (per-Case-ID results of TS {doc_id} revision {revision})",
        "run": "> Test run: {run_id} · admitted at {at}",
        "state_queued": "> State: **test queued/running** — this report is updated when results are recorded. No verdict yet.",
        "state_running": "> State: **test running** — {done}/{total} Case results in. The result is recorded and the overall verdict decided when the remaining Cases finish.",
        "state_prepare_refused": "> State: **preparation failed** ({reason}) — the test was not run. Fix the cause and run again.",
        "state_cancelled": "> State: **cancelled** — the test run was cancelled without results. Run again.",
        "state_failed": "> State: **run aborted** ({reason}) — no results were recorded. Run again.",
        "cases_heading": "## Case Status",
        "cases_header": "| Case | Title | Required | Mode | Expected | Status |",
        "pending": "pending (PENDING)", "not_selected": "not in this run", "no_result": "no result",
        "kept": "{status} (earlier record kept)",
        "reported": "{status} (this run)",
        "footer": "*FlowGate opened this report when test run {run_id} was admitted. The overall verdict is decided by the server record once results are recorded.*",
        "yes": "required", "no": "optional",
    },
    "ja": {
        "kind": "> 文書種別: 試験成績書 (TS {doc_id} revision {revision} のCase ID別結果)",
        "run": "> 試験run: {run_id} · 受付時刻 {at}",
        "state_queued": "> 状態: **試験待機/実行中** — 結果が記録されるとこのレポートが更新される。まだ判定はない。",
        "state_running": "> 状態: **試験実行中** — Case結果 {done}/{total}件反映。残りのCaseが終わると結果が記録され総合判定が決まる。",
        "state_prepare_refused": "> 状態: **準備失敗** ({reason}) — 試験は実行されていない。原因を解消してから再実行する。",
        "state_cancelled": "> 状態: **キャンセル** — 試験runは結果なしでキャンセルされた。再実行する。",
        "state_failed": "> 状態: **実行中断** ({reason}) — 記録された結果はない。再実行する。",
        "cases_heading": "## Case別状態",
        "cases_header": "| Case | タイトル | 必須 | 方式 | 期待結果 | 状態 |",
        "pending": "待機 (PENDING)", "not_selected": "今回のrun対象外", "no_result": "結果なし",
        "kept": "{status} (以前の記録を維持)",
        "reported": "{status} (今回のrun結果)",
        "footer": "*このレポートは試験run {run_id} の受付時にFlowGateが作成した。総合判定は結果記録後にサーバー記録で決まる。*",
        "yes": "必須", "no": "任意",
    },
}


def render_pending_report_markdown(*, doc: dict, run: dict, cases: list[dict],
                                   selected_case_ids: Iterable[str], title: str,
                                   state: str = "queued", reason: Optional[str] = None,
                                   kept: Optional[dict] = None, reported: Optional[dict] = None,
                                   locale: str = "ko") -> str:
    """The TSR body of a run that has no result record yet.

    ``state`` is ``queued`` (admitted, nothing reported), ``running`` (some Cases reported),
    ``prepare_refused``, ``cancelled`` or ``failed``. Selected Cases show PENDING while the
    run is live; ``reported`` maps a Case ID key to the verdict the live run already reported
    for it; ``kept`` maps a Case ID key to the verdict an earlier record of the same TS
    revision still holds.
    """
    s = _PENDING_STRINGS.get(locale) or _PENDING_STRINGS["ko"]
    selected = {case_id_key(case_id) for case_id in selected_case_ids}
    kept = kept or {}
    live = state in ("queued", "running")
    reported = {key: status for key, status in (reported or {}).items() if key in selected} if live else {}
    state_line = s.get("state_" + state) or s["state_failed"]
    lines = [
        f"# {title}",
        "",
        s["kind"].format(doc_id=doc.get("doc_id"), revision=run.get("revision_no")),
        s["run"].format(run_id=run.get("run_id"), at=run.get("created_at") or run.get("started_at")),
        state_line.format(reason=reason or "-", done=len(reported), total=len(selected)),
        "",
        s["cases_heading"],
        "",
        s["cases_header"],
        "|---|---|---|---|---|---|",
    ]
    for case in cases:
        key = case_id_key(case.get("case_id") or "")
        if key in reported:
            status = s["reported"].format(status=reported[key])
        elif key in selected:
            status = s["pending"] if live else s["no_result"]
        elif key in kept:
            status = s["kept"].format(status=kept[key])
        else:
            status = s["not_selected"] if case.get("execution_mode") == "automated" else s["no_result"]
        lines.append(
            f"| {_cell(case.get('case_id'))} | {_cell(case.get('title'))} | "
            f"{s['yes'] if case.get('required') else s['no']} | {_cell(case.get('execution_mode'))} | "
            f"{_cell(case.get('expected'))} | {status} |"
        )
    lines.extend(["", s["footer"].format(run_id=run.get("run_id")), ""])
    return "\n".join(lines)
