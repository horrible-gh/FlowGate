"""TR work-scope check (flowgate.default.0299 — R0001 → D0004 → NR0006 → T0007).

Catches, at TR submission time, the accident of an AI worker working somewhere other than
its assigned worktree (usually the origin repo, main). It reads the self-declared changed-
files section of the TR body and compares it with what the server actually observed inside

the worktree assigned to that group. Two design principles (D0004):

* **It never infers a culprit.** The check runs only inside this task's worktree, so
  another worker polluting the origin repo cannot affect this task's verdict.
* **The self-declaration is not trusted; it is used as the thing to compare against.**

TRV-002's scope (answer to N0005 Q1, option o3): the body carries only repo-relative paths,
so the body alone cannot say whether ``server/a.py`` was edited on main or in the worktree.
TRV-002 therefore covers only format violations whose **notation itself confesses** being
out of scope (absolute paths, ``..``). A relative path edited elsewhere is not found in the
assigned worktree and so is caught by TRV-003 (declared but unconfirmed). Real blocking of
location violations therefore takes effect at the enforce stage.

Where rejections are stored (N0005 Q3, unanswered → settled here): the check finishes
before a document number is reserved: a rejected submission has no document id, so the verdict splits in two.

* Pass/warn — stored in the created TR's ``documents.meta['tr_scope']`` and read from
  the document detail (only possible when a document exists).
* Reject — with no document, it is left in ``events`` as an ``action_code='tr_scope_rejected'``
  event. It attaches to the group timeline, so "how often and why" stays queryable afterwards.

Adding no new table is deliberate: what must be recorded is a per-group time-series fact,
``events`` already has that shape, and nothing in this requirement justifies one more
migration across three dialects.
"""
from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.services import git_service
# 0382 NR0003 proposal 3: the exclusion-rule logic lives in path_exclusion_rules alone.
# This module stays canonical by name — the two names below are re-exports.
from modules.flow_gate.services.path_exclusion_rules import (  # noqa: F401
    exclusion_reason,
    is_excluded_path,
)

# ── Reason codes (D0004 §3.5) ────────────────────────────────────────────────
TRV_MISSING_SECTION = "TRV-001"   # section missing
TRV_OUT_OF_SCOPE = "TRV-002"      # declared path outside scope
TRV_UNCONFIRMED = "TRV-003"       # declared but unconfirmed
TRV_UNREPORTED = "TRV-004"        # changed but undeclared
TRV_FORMAT = "TRV-005"            # format error
TRV_NO_SCOPE = "TRV-006"          # scope could not be determined

# T2/TR2 (0355 NR0003 §1-4): the rejection and authoring notices were 966 characters of
# Korean with no way to change language. Reason-code labels are now per language, with ko
# left as the default so existing callers (logging, etc.) stay compatible.
_CODE_LABELS_BY_LOCALE: dict[str, dict[str, str]] = {
    "ko": {
        TRV_MISSING_SECTION: "섹션 누락 — `## 변경 파일`(영어 별칭 `## Changed Files`) 섹션이 없거나 비어 있습니다.",
        TRV_OUT_OF_SCOPE: "범위 밖 신고 — 신고한 경로가 배정된 작업 폴더 밖을 가리킵니다.",
        TRV_UNCONFIRMED: "신고분 미확인 — 신고했는데 배정된 작업 폴더에 그 변경이 없습니다.",
        TRV_UNREPORTED: "신고 누락 — 작업 폴더에 변경이 있는데 신고 목록에 없습니다.",
        TRV_FORMAT: "형식 오류 — 항목 표기가 규칙에 어긋납니다.",
        TRV_NO_SCOPE: "범위 확인 불가 — 배정된 작업 폴더를 확인할 수 없습니다(서버 측 사정).",
    },
    "en": {
        TRV_MISSING_SECTION: "Missing section — the `## Changed Files` section is missing or empty.",
        TRV_OUT_OF_SCOPE: "Out-of-scope report — a reported path points outside the assigned work folder.",
        TRV_UNCONFIRMED: "Unconfirmed report — the change was reported but was not found in the assigned work folder.",
        TRV_UNREPORTED: "Unreported change — the work folder has a change missing from the report list.",
        TRV_FORMAT: "Format error — an item's notation violates the required format.",
        TRV_NO_SCOPE: "Scope unavailable — the assigned work folder could not be verified (server-side condition).",
    },
    "ja": {
        TRV_MISSING_SECTION: "セクション欠落 — `## Changed Files` セクションが無いか空です。",
        TRV_OUT_OF_SCOPE: "範囲外の報告 — 報告されたパスが割り当てられた作業フォルダの外を指しています。",
        TRV_UNCONFIRMED: "未確認の報告 — 報告されましたが、割り当てられた作業フォルダにその変更が見つかりません。",
        TRV_UNREPORTED: "未報告の変更 — 作業フォルダに変更がありますが、報告リストにありません。",
        TRV_FORMAT: "形式エラー — 項目の表記が規則に違反しています。",
        TRV_NO_SCOPE: "範囲確認不可 — 割り当てられた作業フォルダを確認できません(サーバー側の事情)。",
    },
}

CODE_LABELS: dict[str, str] = _CODE_LABELS_BY_LOCALE["ko"]


def _code_label(code: str, locale: str) -> str:
    table = _CODE_LABELS_BY_LOCALE.get(locale, _CODE_LABELS_BY_LOCALE["ko"])
    return table.get(code, CODE_LABELS.get(code, code))


# ── Enforcement stages (D0004 §3.6) ──────────────────────────────────────────
STAGE_OBSERVE = "observe"
STAGE_WARN = "warn"
STAGE_ENFORCE = "enforce"

# Verdicts
VERDICT_PASS = "pass"
VERDICT_WARN = "warn"
VERDICT_REJECT = "reject"
VERDICT_SKIPPED = "skipped"

SECTION_HEADING = "## 변경 파일"
SECTION_HEADING_EN = "## Changed Files"  # T0009: English alias the parser accepts (the canonical display name stays Korean)
NONE_MARKER = "없음"
MAX_ITEMS = 200
_MAX_LISTED = 40  # max lines actually listed in the rejection notice (D0004 §3.8-3)

# ── Exclusion rules (D0004 §3.3 → merged with the screen rules in 0382 NR0003) ──
#
# The rule bodies moved to path_exclusion_rules. The 0382 B0001 incident came from exactly
# two sets of rules where the screen hid a file the check still flagged — the worker was
# blocked by a file they could not even see. `is_excluded_path` here is a re-export of that
# shared module (see the import above), and git_service's screen filter calls the same one.


# ── Declared-list parser (D0004 §3.2) ────────────────────────────────────────

# T0009: both the Korean heading and the English alias "Changed Files" are accepted — only
# the spellings grow; the canonical name (SECTION_HEADING, used in resubmit guidance) stays Korean.
_HEADING_RE = re.compile(r"^\s{0,3}#{2,6}\s*(변경\s*파일|Changed\s+Files)\s*$", re.IGNORECASE)
_NEXT_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_ITEM_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_NONE_VARIANTS = frozenset({
    NONE_MARKER, f"- {NONE_MARKER}",
    "none", "None", "N/A", "- none", "- None", "- N/A",
})


class ReportedFiles:
    """Result of parsing the changed-files section."""

    def __init__(self) -> None:
        self.found: bool = False
        self.declared_none: bool = False
        self.paths: list[str] = []          # normalised relative paths (deduped, order kept)
        self.out_of_scope: list[str] = []   # absolute path / '..' escape — TRV-002
        self.format_errors: list[str] = []  # one-line human-readable explanation — TRV-005


_FORMAT_ERROR_STRINGS = {
    "ko": {
        "empty_item": "빈 항목",
        "unreadable_path": "경로로 읽을 수 없음: {text}",
        "looks_like_prose": "경로가 아니라 설명으로 보임: {text}",
        "not_a_list_line": "목록 형식이 아닌 줄: {text}",
        "too_many_items": "항목이 {count}개입니다. 최대 {max}개까지 받습니다.",
    },
    "en": {
        "empty_item": "empty item",
        "unreadable_path": "cannot be read as a path: {text}",
        "looks_like_prose": "looks like prose, not a path: {text}",
        "not_a_list_line": "not a list-formatted line: {text}",
        "too_many_items": "{count} items — at most {max} are accepted.",
    },
    "ja": {
        "empty_item": "空の項目",
        "unreadable_path": "パスとして読み取れません: {text}",
        "looks_like_prose": "パスではなく説明文のようです: {text}",
        "not_a_list_line": "リスト形式ではない行です: {text}",
        "too_many_items": "{count} 件の項目があります。最大 {max} 件までです。",
    },
}


def _format_error(key: str, locale: str, **kwargs) -> str:
    """T0009 task 4: locale branch for the five format_errors strings.

    The ko wording is byte-identical to the pre-T0009 hardcode — existing suites
    (test_tr_scope_0299.py) assert those exact strings.
    """
    strings = _FORMAT_ERROR_STRINGS.get(locale) or _FORMAT_ERROR_STRINGS["ko"]
    return strings[key].format(**kwargs)


def _normalize_reported_path(
    raw: str, locale: str = "ko"
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """One entry → ``(normalised path, out-of-scope reason, format-error reason)``; exactly one is set."""
    text = raw.strip()
    # Backtick/quote wrapping is a common harmless variation, so it is stripped and accepted.
    # A trailing explanation (`path — what was fixed`) is cut too. Being strict here would
    # reject an honest declaration over formatting, which is not the accident this catches.
    for separator in (" — ", " – ", " -- ", " - ", " : ", "\t"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
    text = text.strip("`").strip('"').strip("'").strip()
    if not text:
        return None, None, _format_error("empty_item", locale)
    normalized = text.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        return None, text, None  # absolute path = confession (TRV-002)
    if ".." in normalized.split("/"):
        return None, text, None  # parent escape = confession (TRV-002)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = re.sub(r"/{2,}", "/", normalized).strip("/")
    if not normalized:
        return None, None, _format_error("unreadable_path", locale, text=text)
    if " " in normalized and "/" not in normalized:
        # A prose line was written into the list instead of a path.
        return None, None, _format_error("looks_like_prose", locale, text=text)
    return normalized, None, None


def parse_reported_files(body: str, locale: str = "ko") -> ReportedFiles:
    """Read the changed-files section out of a TR body. Never raises."""
    result = ReportedFiles()
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
        if _NEXT_HEADING_RE.match(line):
            break
        body_lines.append(line)

    seen: set[str] = set()
    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in _NONE_VARIANTS:
            result.declared_none = True
            continue
        match = _ITEM_RE.match(line)
        if not match:
            result.format_errors.append(
                _format_error("not_a_list_line", locale, text=stripped[:120])
            )
            continue
        normalized, out_of_scope, format_error = _normalize_reported_path(match.group(1), locale)
        if out_of_scope is not None:
            if out_of_scope not in result.out_of_scope:
                result.out_of_scope.append(out_of_scope)
        elif format_error is not None:
            result.format_errors.append(format_error)
        elif normalized is not None and normalized not in seen:
            seen.add(normalized)
            result.paths.append(normalized)

    if len(result.paths) > MAX_ITEMS:
        result.format_errors.append(
            _format_error("too_many_items", locale, count=len(result.paths), max=MAX_ITEMS)
        )
    return result


# ── Enforcement-stage lookup ─────────────────────────────────────────────────

def resolve_stage(project_id: str) -> Optional[str]:
    """The project's enforcement stage. ``None`` when git integration is absent or off = not checked.

    A project with integration off has no concept of a group worktree at all. Leaving
    TRV-006 (scope undeterminable) attached there would only pile up warnings nobody can
    fix, so the check is not even attempted.
    """
    try:
        cfg = db_git.get_config(project_id)
    except Exception:  # noqa: BLE001 — a settings lookup failure must not block TR intake
        return None
    if cfg is None or not cfg.get("enabled"):
        return None
    stage = (cfg.get("tr_scope_stage") or STAGE_OBSERVE).strip() or STAGE_OBSERVE
    return stage if stage in db_git.TR_SCOPE_STAGE_VALUES else STAGE_OBSERVE


# ── Comparison verdict (D0004 §3.4 / §3.7) ───────────────────────────────────

def evaluate(
    project_id: str,
    group_id: str,
    body: str,
    locale: str = "ko",
    prior_declared: Optional[Iterable[str]] = None,
) -> dict:
    """Judge a TR body. Side-effect free — dry-run and real registration call this same function.

    The returned dict is carried verbatim into the dry-run response, document meta and event
    metadata. ``locale`` only picks the language of the ``notice`` attached on rejection
    (T2/TR2); the verdict itself is language-independent. ``prior_declared`` holds paths
    already declared by earlier TRs in the same group and is merged only into the undeclared
    verdict — it does not affect this body's unconfirmed verdict.
    """
    stage = resolve_stage(project_id)
    if stage is None:
        return {
            "verdict": VERDICT_SKIPPED, "stage": None, "codes": [],
            "reason": "git_integration_off",
        }

    reported = parse_reported_files(body, _normalize_notice_locale(locale))
    codes: list[str] = []

    # 1) section missing / empty
    if not reported.found:
        codes.append(TRV_MISSING_SECTION)
    elif not reported.paths and not reported.declared_none and not reported.out_of_scope:
        codes.append(TRV_MISSING_SECTION)

    # 2) format errors
    if reported.format_errors:
        codes.append(TRV_FORMAT)

    # 3) out-of-scope declaration — only where the notation itself confesses (N0005 Q1)
    if reported.out_of_scope:
        codes.append(TRV_OUT_OF_SCOPE)

    actual = git_service.collect_scope_changes(project_id, group_id)
    detected = [p for p in actual.get("paths") or [] if not is_excluded_path(p)]
    declared = [p for p in reported.paths if not is_excluded_path(p)]

    unconfirmed: list[str] = []
    unreported: list[str] = []
    if not actual.get("available"):
        # 4) scope undeterminable — judging stops here. Comparing while blind to the
        #    worktree would stamp every declaration TRV-003 and mislead the worker.
        codes.append(TRV_NO_SCOPE)
    else:
        detected_set = set(detected)
        declared_set = set(declared)
        prior_declared_set = {
            path for path in (prior_declared or [])
            if isinstance(path, str) and not is_excluded_path(path)
        }
        # 5) declared but unconfirmed — this is where "worked in the wrong place" is caught.
        #    Paths declared by earlier TRs are no evidence about this body's honesty.
        unconfirmed = sorted(declared_set - detected_set)
        # 6) undeclared — real changes accumulate per group, so declarations are compared cumulatively.
        unreported = sorted(detected_set - (declared_set | prior_declared_set))
        if unconfirmed:
            codes.append(TRV_UNCONFIRMED)
        if unreported:
            codes.append(TRV_UNREPORTED)

    verdict = _verdict_for(codes, stage)
    result: dict = {
        "verdict": verdict,
        "stage": stage,
        "codes": codes,
        "reported": declared,
        "detected": detected,
        "out_of_scope": reported.out_of_scope,
        "unconfirmed": unconfirmed,
        "unreported": unreported,
        "format_errors": reported.format_errors,
        "branch": actual.get("branch"),
        "worktree": actual.get("worktree"),
        "scope_reason": actual.get("reason"),
    }
    if verdict == VERDICT_REJECT:
        result["notice"] = build_notice(result, locale)
    return result


def _verdict_for(codes: list[str], stage: str) -> str:
    """Reason codes + enforcement stage → final disposition (D0004 §3.7 verdict matrix).

    When several reasons fire at once the heaviest disposition wins — one reject means reject.
    """
    if not codes:
        return VERDICT_PASS
    # TRV-002 always rejects, whatever the stage. A declared path pointing outside scope is
    # a confession, not an inference, and letting it pass leaves it in the origin repo.
    if TRV_OUT_OF_SCOPE in codes:
        return VERDICT_REJECT
    # TRV-006 always passes, whatever the stage — a server-side problem is not the worker's
    # fault. TRV-006 alone, with no other reason, stops at a warning.
    blocking = [c for c in codes if c != TRV_NO_SCOPE]
    if not blocking:
        return VERDICT_WARN
    if stage == STAGE_ENFORCE:
        return VERDICT_REJECT
    if stage == STAGE_WARN:
        return VERDICT_WARN
    return VERDICT_PASS  # observe — recorded only, nothing surfaces to the worker


# ── Rejection notice (D0004 §3.8, language plumbing T2/TR2 — NR0003 §1-4) ────
#
# The problem NR0003 §1-4 measured: the TR rejection notice was 966 characters of Korean
# with no way to change the language. The caller (inbox_routes._handle_new) had already
# resolved a locale from the token/header (L0007 §2-2, "the channel already exists"), so
# all that is needed here is to take that value and compose the notice in that language.

_SUPPORTED_NOTICE_LOCALES = ("ko", "en", "ja")


def _normalize_notice_locale(locale: str) -> str:
    return locale if locale in _SUPPORTED_NOTICE_LOCALES else "ko"


def _spelling_changed_files(locale: str) -> str:
    """Per-language section titles. A Japanese request still gets the English canonical form:
    no Japanese alias was created for this grammar token (0355 T0009), so English is shown."""
    return SECTION_HEADING if _normalize_notice_locale(locale) == "ko" else SECTION_HEADING_EN


def _spelling_none(locale: str) -> str:
    return NONE_MARKER if _normalize_notice_locale(locale) == "ko" else "None"


_BULLET_EMPTY = {"ko": "  (없음)", "en": "  (none)", "ja": "  (なし)"}
_BULLET_MORE = {
    "ko": "  … 외 {rest}건 (전체 {total}건)",
    "en": "  … and {rest} more (total {total})",
    "ja": "  … 他{rest}件(全{total}件)",
}


def _bullet_list(paths: list[str], locale: str = "ko") -> str:
    loc = _normalize_notice_locale(locale)
    if not paths:
        return _BULLET_EMPTY[loc]
    shown = paths[:_MAX_LISTED]
    lines = [f"  - {p}" for p in shown]
    if len(paths) > len(shown):
        lines.append(_BULLET_MORE[loc].format(rest=len(paths) - len(shown), total=len(paths)))
    return "\n".join(lines)


_NOTICE: dict[str, dict[str, str]] = {
    "ko": {
        "head": "TR 제출이 작업범위 검증에서 반려되었습니다.",
        "reason_heading": "\n[1] 반려 사유",
        "location_heading": "\n[2] 이 작업에 배정된 위치",
        "branch_label": "  브랜치   : {branch}",
        "worktree_label": "  작업 폴더: {worktree}",
        "unavailable": "(확인 불가)",
        "location_note": "  이 폴더 밖에서 편집한 내용은 이 작업의 산출물로 집계되지 않습니다.",
        "observed_heading": "\n[3] 서버가 작업 폴더에서 실제로 관측한 변경 ({count}건)",
        "unconfirmed_heading": "\n  신고했지만 작업 폴더에서 찾지 못한 경로:",
        "unconfirmed_note": (
            "  이런 형태는 대개 배정된 작업 폴더가 아닌 다른 위치(원본 레포/main 등)에서"
            " 파일을 직접 편집했을 때 나타납니다. 저장하지 않았거나 경로를 잘못 적은"
            " 경우일 수도 있으니 확인해 보십시오."
        ),
        "unreported_heading": "\n  작업 폴더에 있는데 신고 목록에 없는 경로:",
        "out_of_scope_heading": "\n  배정 범위 밖을 가리키는 신고 경로(절대경로 또는 '..' 포함):",
        "format_errors_heading": "\n  형식 오류:",
        "resubmit": (
            "\n[4] 다시 제출하는 방법\n"
            "  TR 본문에 아래 섹션을 그대로 포함하고, 작업 지시에 동봉된 것과 같은\n"
            "  Artifact registration POST 를 다시 호출하십시오(주소·토큰·prev_doc_id 동일).\n"
            "\n"
            "  {heading}\n"
            "\n"
            "  - <저장소 루트 기준 상대경로. 바뀐 파일마다 한 줄씩 추가>\n"
            "\n"
            "  규칙: 저장소 루트 기준 상대경로, 구분자는 '/', 한 줄에 하나, '- ' 로 시작.\n"
            "  앞에 '/' 를 붙이지 않고 './' 로 시작하지 않으며 '..' 을 포함하지 않습니다.\n"
            "  새로 만든/고친/지운 파일을 모두 적고, 이름을 바꾼 경우 바뀐 뒤 경로만 적습니다.\n"
            "  바꾼 파일이 하나도 없으면 항목 대신 '{none}'(또는 'None') 한 줄만 적습니다.\n"
            f"  영어 별칭: 섹션 제목은 '{SECTION_HEADING_EN}' 로 적어도 받습니다.\n"
            "  본 제출 전에 같은 본문으로 \"dry_run\": true 를 보내 판정을 미리 확인할 수 있습니다."
        ),
        "revert": (
            "\n[5] 배정 범위 밖에 쓴 내용이 있다면\n"
            "  1) 먼저 그 위치(원본 레포/main 등)의 변경을 되돌리십시오. 그대로 두면\n"
            "     다음 작업자가 그 오염을 물려받습니다.\n"
            "  2) 위 [2] 의 작업 폴더에서 같은 변경을 다시 적용하십시오. 원격 소스\n"
            "     CRUD 엔드포인트(/remote/write, /remote/remove)를 쓰면 서버가 이 작업의\n"
            "     워크트리에만 쓰므로 위치를 잘못 잡는 일이 없습니다.\n"
            "  3) 그다음 이 TR 을 다시 제출하십시오."
        ),
    },
    "en": {
        "head": "The TR submission was rejected by work-scope verification.",
        "reason_heading": "\n[1] Rejection reason",
        "location_heading": "\n[2] Location assigned to this task",
        "branch_label": "  Branch     : {branch}",
        "worktree_label": "  Work folder: {worktree}",
        "unavailable": "(unavailable)",
        "location_note": "  Edits made outside this folder are not counted as output for this task.",
        "observed_heading": "\n[3] Changes the server actually observed in the work folder ({count})",
        "unconfirmed_heading": "\n  Paths reported but not found in the work folder:",
        "unconfirmed_note": (
            "  This usually happens when a file was edited directly in a location other"
            " than the assigned work folder (e.g. the original repo/main). It could also"
            " mean the change was not saved, or the path was written incorrectly — please check."
        ),
        "unreported_heading": "\n  Paths present in the work folder but missing from the report list:",
        "out_of_scope_heading": "\n  Reported paths pointing outside the assigned scope (absolute path or containing '..'):",
        "format_errors_heading": "\n  Format errors:",
        "resubmit": (
            "\n[4] How to resubmit\n"
            "  Include the section below verbatim in the TR body, and call the same\n"
            "  Artifact registration POST included with the work instruction again\n"
            "  (same URL/token/prev_doc_id).\n"
            "\n"
            "  {heading}\n"
            "\n"
            "  - <path relative to the repository root. Add one line per changed file>\n"
            "\n"
            "  Rule: paths are relative to the repository root, separator '/', one per line,\n"
            "  starting with '- '. Do not prefix with '/', do not start with './', and do not\n"
            "  include '..'. List every file you created/changed/deleted; for renames, list\n"
            "  only the new path.\n"
            "  If you changed no files, write a single line '{none}' instead of items.\n"
            "  You can preview the verdict beforehand by sending the same body with"
            " \"dry_run\": true."
        ),
        "revert": (
            "\n[5] If you wrote anything outside the assigned scope\n"
            "  1) First revert the change at that location (original repo/main, etc).\n"
            "     Leaving it in place hands the contamination to the next worker.\n"
            "  2) Re-apply the same change in the work folder from [2] above. The remote\n"
            "     source CRUD endpoints (/remote/write, /remote/remove) write only into\n"
            "     this task's worktree, so you cannot mis-target the location.\n"
            "  3) Then resubmit this TR."
        ),
    },
    "ja": {
        "head": "TRの提出は作業範囲検証で却下されました。",
        "reason_heading": "\n[1] 却下理由",
        "location_heading": "\n[2] この作業に割り当てられた場所",
        "branch_label": "  ブランチ    : {branch}",
        "worktree_label": "  作業フォルダ: {worktree}",
        "unavailable": "(確認不可)",
        "location_note": "  このフォルダの外で編集した内容は、この作業の成果物として集計されません。",
        "observed_heading": "\n[3] サーバーが作業フォルダで実際に観測した変更({count}件)",
        "unconfirmed_heading": "\n  報告されたが作業フォルダで見つからなかったパス:",
        "unconfirmed_note": (
            "  これは通常、割り当てられた作業フォルダ以外の場所(元のリポジトリ/main など)で"
            "ファイルを直接編集した場合に発生します。保存し忘れたか、パスを誤って"
            "記載した可能性もあるため確認してください。"
        ),
        "unreported_heading": "\n  作業フォルダにはあるが、報告リストに無いパス:",
        "out_of_scope_heading": "\n  割り当てられた範囲外を指す報告パス(絶対パスまたは '..' を含む):",
        "format_errors_heading": "\n  形式エラー:",
        "resubmit": (
            "\n[4] 再提出の方法\n"
            "  TR本文に以下のセクションをそのまま含め、作業指示に添付されたものと同じ\n"
            "  Artifact registration POST を再度呼び出してください(URL・トークン・\n"
            "  prev_doc_id は同一)。\n"
            "\n"
            "  {heading}\n"
            "\n"
            "  - <リポジトリルート基準の相対パス。変更したファイルごとに1行追加>\n"
            "\n"
            "  規則: リポジトリルート基準の相対パス、区切りは '/'、1行に1件、'- ' で開始。\n"
            "  先頭に '/' を付けず、'./' で始めず、'..' を含めません。新規作成・変更・\n"
            "  削除したファイルをすべて記載し、名前を変更した場合は変更後のパスのみ\n"
            "  記載します。\n"
            "  変更したファイルが一つも無い場合は、項目の代わりに '{none}' の1行のみを\n"
            "  記載します。\n"
            "  本提出の前に、同じ本文で \"dry_run\": true を送ると判定を事前に確認できます。"
        ),
        "revert": (
            "\n[5] 割り当てられた範囲外に書いた内容がある場合\n"
            "  1) まずその場所(元のリポジトリ/main など)の変更を元に戻してください。\n"
            "     そのままにすると、次の作業者がその汚染を引き継ぐことになります。\n"
            "  2) 上記[2]の作業フォルダで同じ変更を再度適用してください。リモートソース\n"
            "     CRUDエンドポイント(/remote/write, /remote/remove)を使うと、サーバーは\n"
            "     この作業のワークツリーにのみ書き込むため、場所を誤ることがありません。\n"
            "  3) その後、このTRを再提出してください。"
        ),
    },
}


def build_notice(result: dict, locale: str = "ko") -> str:
    """A rework instruction sheet per rejection reason.

    The point is that it is an instruction sheet, not an error notice. A bare "format error"
    line just gets the same thing resubmitted. The tone is tentative, not assertive: the
    real cause may differ, and asserting it sends the worker to fix the wrong thing.

    ``locale`` sets the language of the whole notice (T2/TR2). Section numbers (``[1]``-``[5]``)
    and reason codes (``TRV-00N``) stay as they are in every language — they are not translated.
    """
    loc = _normalize_notice_locale(locale)
    strings = _NOTICE[loc]
    codes: list[str] = result.get("codes") or []
    parts: list[str] = [strings["head"]]

    # 1. why it was rejected
    parts.append(strings["reason_heading"])
    for code in codes:
        parts.append(f"  - {code}: {_code_label(code, loc)}")

    # 2. where the work should have happened — most accidents come from not knowing one's
    #    own location, so these two lines carry the most weight.
    parts.append(strings["location_heading"])
    parts.append(strings["branch_label"].format(branch=result.get("branch") or strings["unavailable"]))
    parts.append(strings["worktree_label"].format(worktree=result.get("worktree") or strings["unavailable"]))
    parts.append(strings["location_note"])

    # 3. the change list the server actually saw
    parts.append(strings["observed_heading"].format(count=len(result.get("detected") or [])))
    parts.append(_bullet_list(result.get("detected") or [], loc))
    if result.get("unconfirmed"):
        parts.append(strings["unconfirmed_heading"])
        parts.append(_bullet_list(result.get("unconfirmed") or [], loc))
        parts.append(strings["unconfirmed_note"])
    if result.get("unreported"):
        parts.append(strings["unreported_heading"])
        parts.append(_bullet_list(result.get("unreported") or [], loc))
    if result.get("out_of_scope"):
        parts.append(strings["out_of_scope_heading"])
        parts.append(_bullet_list(result.get("out_of_scope") or [], loc))
    if result.get("format_errors"):
        parts.append(strings["format_errors_heading"])
        parts.append(_bullet_list(result.get("format_errors") or [], loc))

    # 4. how to resubmit
    parts.append(strings["resubmit"].format(
        heading=_spelling_changed_files(loc), none=_spelling_none(loc),
    ))

    # 5. how to undo it — without this, junk stays in the origin repo and only the resubmit repeats.
    if TRV_OUT_OF_SCOPE in codes or TRV_UNCONFIRMED in codes:
        parts.append(strings["revert"])
    return "\n".join(parts)


# ── Document-detail lookup (D0004 §6) ───────────────────────────────────────

def verdict_from_meta(meta) -> Optional[dict]:
    """``documents.meta`` → the stored work-scope verdict, or None when absent.

    Depending on the dialect, meta arrives as TEXT (a JSON string) or as a dict, so both are
    accepted. A TR predating the check has no such key, and then the screen hides the area.
    """
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (ValueError, TypeError):
            return None
    if not isinstance(meta, dict):
        return None
    value = meta.get("tr_scope")
    return value if isinstance(value, dict) else None


def unevaluated_verdict(type_code, body: Optional[str]) -> Optional[dict]:
    """A display-only verdict for documents with no stored one (0390 TR0005 rev2).

    ``meta['tr_scope']`` is computed once at submission time and frozen, so a document
    submitted while its type was not yet checked (a TS from before TS became a target) has
    no key at all. If the screen then hides the whole area, the user cannot tell "this kind
    of document is not checked" from "this one was never checked" — the work-scope card
    vanishing entirely from the sidebar is exactly that symptom.

    So for a target type (``MUTATING_STEP_TYPES``) it reads only the body's declared list,
    without touching git, and builds an ``evaluated: False`` verdict. Document detail is
    fetched on every request, so the worktree comparison (``evaluate``) is NOT redone here:
    it must remain a record of submission time, and must not change on every read.

    rev3: when the body had no changed-files section, rev2 returned None and hid the card.
    That is why rev2 was rejected again for the same reason: a TS submitted before it became
    a target was never told to write that section, so **its absence is normal**, and the
    screen ended up as empty as in rev1. Now the card is drawn even without the section, and
    ``scope_reason`` separates "there is no section" from "the section exists but was not compared".
    R0001's requirement to "offer the work-scope check in the sidebar too" does not mean
    offering it only when a declared list exists.

    The same holds when ``body`` is None (the body file is unreadable) — it draws zero declarations.
    """
    from modules.flow_gate.services import tool_registry

    if str(type_code or "").upper() not in tool_registry.MUTATING_STEP_TYPES:
        return None
    reported = parse_reported_files(body or "")
    declared = [path for path in reported.paths if not is_excluded_path(path)]
    return {
        "verdict": VERDICT_SKIPPED,
        "evaluated": False,
        "stage": None,
        "codes": [],
        "branch": None,
        "scope_reason": "not_evaluated" if reported.found else "not_evaluated_no_section",
        "reported": {"count": len(declared), "items": declared[:MAX_ITEMS]},
    }


# ── Guidance carried in the work instruction (D0004 §3.9, language plumbing T2/TR2) ──

_TR_SECTION_GUIDE_TEMPLATES: dict[str, str] = {
    "ko": (
        "TR 본문에는 아래 섹션을 반드시 포함하십시오. 서버가 이 목록을 배정된 작업\n"
        "폴더(워크트리)의 실제 변경과 대조하며, 어긋나면 제출이 반려될 수 있습니다.\n"
        "\n"
        "{heading}\n"
        "\n"
        "- <저장소 루트 기준 상대경로. 바뀐 파일마다 한 줄씩 추가>\n"
        "\n"
        "저장소 루트 기준 상대경로로, 구분자는 '/', 한 줄에 하나씩 '- ' 로 시작해 적습니다.\n"
        "앞에 '/' 를 붙이거나 './' 로 시작하거나 '..' 을 포함하면 범위 밖 신고로 봅니다.\n"
        "새로 만든/고친/지운 파일을 모두 적고, 이름을 바꾼 경우 바뀐 뒤 경로만 적습니다.\n"
        "바꾼 파일이 하나도 없으면 항목 대신 '{none}'(또는 'None') 한 줄만 적습니다(최대 200개).\n"
        f"영어로 작성해도 됩니다 — 섹션 제목은 '{SECTION_HEADING_EN}' 로 적어도 똑같이 받습니다.\n"
    ),
    "en": (
        "Include the section below in the TR body. The server compares this list\n"
        "against the actual changes in the assigned work folder (worktree); a\n"
        "mismatch may cause the submission to be rejected.\n"
        "\n"
        "{heading}\n"
        "\n"
        "- <path relative to the repository root. Add one line per changed file>\n"
        "\n"
        "Paths are relative to the repository root, separator '/', one per line,\n"
        "starting with '- '. A leading '/', a './' prefix, or '..' is treated as an\n"
        "out-of-scope report. List every file you created/changed/deleted; for\n"
        "renames, list only the new path.\n"
        "If you changed no files, write a single line '{none}' instead of items (up to 200).\n"
    ),
    "ja": (
        "TR本文には以下のセクションを必ず含めてください。サーバーはこのリストを、\n"
        "割り当てられた作業フォルダ(ワークツリー)の実際の変更と照合し、食い違いが\n"
        "あれば提出が却下されることがあります。\n"
        "\n"
        "{heading}\n"
        "\n"
        "- <リポジトリルート基準の相対パス。変更したファイルごとに1行追加>\n"
        "\n"
        "リポジトリルート基準の相対パスで、区切りは '/'、1行に1件、'- ' で始めて\n"
        "記載します。先頭に '/' を付ける、'./' で始める、'..' を含む場合は範囲外の\n"
        "報告とみなします。新規作成・変更・削除したファイルをすべて記載し、名前を\n"
        "変更した場合は変更後のパスのみ記載します。\n"
        "変更したファイルが一つも無い場合は、項目の代わりに '{none}' の1行のみを\n"
        "記載します(最大200件)。\n"
    ),
}


def tr_section_guide(locale: str = "ko") -> str:
    """The TR authoring guidance carried in a work instruction (D0004 §3.9, T2/TR2).

    NR0003 §1-4: this notice, like the 966-character rejection notice, used to be all Korean.
    """
    loc = _normalize_notice_locale(locale)
    return _TR_SECTION_GUIDE_TEMPLATES[loc].format(
        heading=_spelling_changed_files(loc), none=_spelling_none(loc),
    )


# Backward compatibility: existing callers (ko-fixed screens and documents) keep using the module constant.
TR_SECTION_GUIDE = tr_section_guide("ko")


_TR_SECTION_PLACEHOLDER_TEMPLATES: dict[str, str] = {
    "ko": "{heading}\n\n- <저장소 루트 기준 상대경로. 바꾼 파일이 없으면 이 목록 대신 '{none}' 한 줄>\n",
    "en": "{heading}\n\n- <path relative to the repository root. If nothing changed, write a single '{none}' line instead>\n",
    "ja": "{heading}\n\n- <リポジトリルート基準の相対パス。変更が無い場合はこの代わりに '{none}' の1行のみ>\n",
}


def tr_section_placeholder(locale: str = "ko") -> str:
    """The empty section pre-filled into a new TR's ``content`` (T1, T2/TR2 language plumbing)."""
    loc = _normalize_notice_locale(locale)
    return _TR_SECTION_PLACEHOLDER_TEMPLATES[loc].format(
        heading=_spelling_changed_files(loc), none=_spelling_none(loc),
    )


TR_SECTION_PLACEHOLDER = tr_section_placeholder("ko")
