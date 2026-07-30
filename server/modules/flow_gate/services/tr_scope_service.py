"""TR 작업범위 검증 (flowgate.default.0299 — R0001 → D0004 → NR0006 → T0007).

AI 작업자가 배정받은 워크트리가 아닌 곳(주로 원본 레포, main)에 작업하는 사고를
TR 제출 시점에 잡는다. TR 본문의 ``## 변경 파일`` 섹션(자기신고)을 읽고, 그 그룹에
배정된 워크트리 안에서 서버가 실제로 관측한 변경과 대조한다.

설계 원칙 두 가지(D0004):

* **범인을 추리하지 않는다.** 검사는 그 작업의 워크트리 안에서만 이루어지므로,
  다른 작업자가 원본 레포를 오염시켜도 이 작업의 판정은 영향받지 않는다.
* **자기신고를 믿는 것이 아니라 대조 대상으로 쓴다.**

TRV-002 의 범위(N0005 Q1 답변 = o3 채택): 제출 본문에는 저장소 상대경로만 오므로
``server/a.py`` 가 main 에서 편집됐는지 워크트리에서 편집됐는지 본문만으로는 알 수
없다. 그래서 TRV-002 는 절대경로·``..`` 처럼 **표기 자체가 범위 밖임을 자백하는**
형식 위반만 담당하고, 다른 위치에서 편집된 상대경로는 배정 워크트리 안에서 그 변경이
발견되지 않으므로 TRV-003(신고분 미확인)으로 잡힌다. 즉 위치 위반의 실질 차단은
강제(enforce) 단계에서 성립한다.

거부 기록의 저장 위치(N0005 Q3, 미답변 → 여기서 확정): 검증은 문서 번호 예약 전에
끝나므로 거부된 제출에는 문서 ID가 없다. 따라서 판정 결과는 두 곳으로 나뉜다.

* 통과/경고 — 생성된 TR 문서의 ``documents.meta['tr_scope']`` 에 넣어 문서 상세에서
  조회한다(문서가 있는 경우에만 가능).
* 거부 — 문서가 없으므로 ``events`` 에 ``action_code='tr_scope_rejected'`` 이벤트로
  남긴다. 그룹 타임라인에 붙으므로 "몇 번 반려됐고 왜였는지"가 사후에 조회된다.

새 테이블을 만들지 않은 것은 의도적이다. 기록해야 할 것은 그룹 단위 시계열 사실이고
events 가 이미 그 모양이며, 3개 dialect 마이그레이션을 하나 더 늘릴 근거가 이 요건
안에는 없다.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from modules.flow_gate.db import git_integration as db_git
from modules.flow_gate.services import git_service

# ── 사유 코드 (D0004 §3.5) ────────────────────────────────────────────────────
TRV_MISSING_SECTION = "TRV-001"   # 섹션 누락
TRV_OUT_OF_SCOPE = "TRV-002"      # 범위 밖 신고
TRV_UNCONFIRMED = "TRV-003"       # 신고분 미확인
TRV_UNREPORTED = "TRV-004"        # 신고 누락
TRV_FORMAT = "TRV-005"            # 형식 오류
TRV_NO_SCOPE = "TRV-006"          # 범위 확인 불가

# T2/TR2 (0355 NR0003 §1-4): 반려 안내문·작성 안내문이 966자 전부 한국어였고
# 언어를 바꿀 방법이 없었다. 사유 코드 라벨을 언어별로 두고, ko 를 default 로
# 남겨 기존 호출부(로그 등)와 호환한다.
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


# ── 적용 단계 (D0004 §3.6) ───────────────────────────────────────────────────
STAGE_OBSERVE = "observe"
STAGE_WARN = "warn"
STAGE_ENFORCE = "enforce"

# 판정 결과
VERDICT_PASS = "pass"
VERDICT_WARN = "warn"
VERDICT_REJECT = "reject"
VERDICT_SKIPPED = "skipped"

SECTION_HEADING = "## 변경 파일"
SECTION_HEADING_EN = "## Changed Files"  # T0009: 파서가 받아주는 영어 별칭 (표시용 정식 명칭은 한국어 그대로)
NONE_MARKER = "없음"
MAX_ITEMS = 200
_MAX_LISTED = 40  # 반려 안내문에 실제로 나열하는 최대 줄 수 (D0004 §3.8-3)

# ── 제외 규칙 (D0004 §3.3, N0005 Q2 미답변 → 여기서 확정) ────────────────────
#
# 기존 read_group_changes 는 "경로의 어느 구간이든 점으로 시작" + "*.db" 를 제외하지만
# 그건 트리 노출용 규칙이다. 여기서는 D0004 §3.3 의 세 부류를 그대로 구현한다.
#
#   1) 최상위에서 점(.)으로 시작하는 항목 — `.git/`, `.venv/`, `.env` 등. 최상위로
#      한정하는 이유는 하위의 점 디렉터리까지 싸잡으면 실제 작업 산출물(예:
#      `client/src/.../.eslintrc` 를 정말 고친 경우)까지 조용히 사라지기 때문이다.
#   2) 로컬 데이터베이스 파일 — 실행하면 생기는 것이지 작업의 산출물이 아니다.
#   3) 워크트리 안의 임시·빌드 산출물 경로.
#
# 프로젝트별 설정은 두지 않는다. 이 목록은 "도구가 남기는 흔적"의 목록이고, 이걸
# 프로젝트마다 다르게 만들면 검증의 기준선 자체가 프로젝트마다 달라져 판정을 서로
# 비교할 수 없게 된다. 늘려야 할 것이 생기면 여기에 추가한다.
_EXCLUDED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".log")
_EXCLUDED_DIR_SEGMENTS = frozenset({
    "node_modules", "__pycache__", "dist", "build", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov", ".tox", "site-packages",
})
_EXCLUDED_DIR_PREFIXES = ("pytest-cache-files-",)


def is_excluded_path(path: str) -> bool:
    """작업의 산출물이 아니라 도구·환경이 남긴 흔적인가 (D0004 §3.3)."""
    if not path:
        return True
    segments = path.split("/")
    if segments[0].startswith("."):
        return True
    if any(seg in _EXCLUDED_DIR_SEGMENTS for seg in segments):
        return True
    if any(seg.startswith(_EXCLUDED_DIR_PREFIXES) for seg in segments):
        return True
    return segments[-1].lower().endswith(_EXCLUDED_SUFFIXES)


# ── 신고목록 파서 (D0004 §3.2) ────────────────────────────────────────────────

# T0009: "변경 파일" 과 영어 별칭 "Changed Files" 를 둘 다 받는다 — 표기가 늘 뿐,
# 정식 명칭(재제출 안내에 쓰는 SECTION_HEADING)은 여전히 한국어다.
_HEADING_RE = re.compile(r"^\s{0,3}#{2,6}\s*(변경\s*파일|Changed\s+Files)\s*$", re.IGNORECASE)
_NEXT_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_ITEM_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_NONE_VARIANTS = frozenset({
    NONE_MARKER, f"- {NONE_MARKER}",
    "none", "None", "N/A", "- none", "- None", "- N/A",
})


class ReportedFiles:
    """``## 변경 파일`` 섹션 파싱 결과."""

    def __init__(self) -> None:
        self.found: bool = False
        self.declared_none: bool = False
        self.paths: list[str] = []          # 정규화된 상대경로 (중복 제거, 순서 유지)
        self.out_of_scope: list[str] = []   # 절대경로 / '..' 이탈 — TRV-002
        self.format_errors: list[str] = []  # 사람이 읽는 한 줄 설명 — TRV-005


def _normalize_reported_path(raw: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """한 항목 → ``(정규경로, 범위밖사유, 형식오류사유)``. 셋 중 하나만 채워진다."""
    text = raw.strip()
    # 백틱/따옴표로 감싼 표기는 흔한 무해한 변형이라 벗겨서 받아준다. 뒤의 설명
    # (`path — 무엇을 고쳤는지`)도 잘라 낸다. 여기서 엄격하게 굴면 정직하게 신고한
    # 작업자가 형식 때문에 반려되고, 그건 이 기능이 잡으려는 사고가 아니다.
    for separator in (" — ", " – ", " -- ", " - ", " : ", "\t"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
    text = text.strip("`").strip('"').strip("'").strip()
    if not text:
        return None, None, "빈 항목"
    normalized = text.replace("\\", "/")
    if normalized.startswith("/") or (len(normalized) >= 2 and normalized[1] == ":"):
        return None, text, None  # 절대경로 = 자백 (TRV-002)
    if ".." in normalized.split("/"):
        return None, text, None  # 상위 이탈 = 자백 (TRV-002)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = re.sub(r"/{2,}", "/", normalized).strip("/")
    if not normalized:
        return None, None, f"경로로 읽을 수 없음: {text}"
    if " " in normalized and "/" not in normalized:
        # 경로가 아니라 산문 한 줄을 목록에 적은 경우.
        return None, None, f"경로가 아니라 설명으로 보임: {text}"
    return normalized, None, None


def parse_reported_files(body: str) -> ReportedFiles:
    """TR 본문에서 ``## 변경 파일`` 섹션을 읽어낸다. 예외를 던지지 않는다."""
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
            result.format_errors.append(f"목록 형식이 아닌 줄: {stripped[:120]}")
            continue
        normalized, out_of_scope, format_error = _normalize_reported_path(match.group(1))
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
            f"항목이 {len(result.paths)}개입니다. 최대 {MAX_ITEMS}개까지 받습니다."
        )
    return result


# ── 적용 단계 조회 ────────────────────────────────────────────────────────────

def resolve_stage(project_id: str) -> Optional[str]:
    """프로젝트의 적용 단계. git 연동이 없거나 꺼져 있으면 ``None`` = 검증 비대상.

    연동이 꺼진 프로젝트에는 그룹 워크트리라는 개념 자체가 없다. 그런 프로젝트에서
    TRV-006(범위 확인 불가)을 계속 달아 두면 아무도 고칠 수 없는 경고만 쌓이므로,
    검증을 시도조차 하지 않는다.
    """
    try:
        cfg = db_git.get_config(project_id)
    except Exception:  # noqa: BLE001 — 설정 조회 실패가 TR 접수를 막아선 안 된다
        return None
    if cfg is None or not cfg.get("enabled"):
        return None
    stage = (cfg.get("tr_scope_stage") or STAGE_OBSERVE).strip() or STAGE_OBSERVE
    return stage if stage in db_git.TR_SCOPE_STAGE_VALUES else STAGE_OBSERVE


# ── 대조 판정 (D0004 §3.4 / §3.7) ────────────────────────────────────────────

def evaluate(project_id: str, group_id: str, body: str, locale: str = "ko") -> dict:
    """TR 본문을 판정한다. 부작용 없음 — dry-run 과 실등록이 같은 함수를 호출한다.

    반환 dict 는 그대로 dry-run 응답, 문서 meta, 이벤트 metadata 에 실린다.
    ``locale`` 은 거부됐을 때 붙는 안내문(``notice``)의 언어만 정한다(T2/TR2) — 판정
    자체는 언어와 무관하다.
    """
    stage = resolve_stage(project_id)
    if stage is None:
        return {
            "verdict": VERDICT_SKIPPED, "stage": None, "codes": [],
            "reason": "git_integration_off",
        }

    reported = parse_reported_files(body)
    codes: list[str] = []

    # 1) 섹션 누락 / 비어 있음
    if not reported.found:
        codes.append(TRV_MISSING_SECTION)
    elif not reported.paths and not reported.declared_none and not reported.out_of_scope:
        codes.append(TRV_MISSING_SECTION)

    # 2) 형식 오류
    if reported.format_errors:
        codes.append(TRV_FORMAT)

    # 3) 범위 밖 신고 — 표기 자체가 자백인 경우만 (N0005 Q1)
    if reported.out_of_scope:
        codes.append(TRV_OUT_OF_SCOPE)

    actual = git_service.collect_scope_changes(project_id, group_id)
    detected = [p for p in actual.get("paths") or [] if not is_excluded_path(p)]
    declared = [p for p in reported.paths if not is_excluded_path(p)]

    unconfirmed: list[str] = []
    unreported: list[str] = []
    if not actual.get("available"):
        # 4) 범위 확인 불가 — 판정을 계속하지 않는다. 워크트리를 못 보는 상태에서
        #    대조하면 신고 전부가 TRV-003 으로 찍혀 작업자를 오도한다.
        codes.append(TRV_NO_SCOPE)
    else:
        detected_set = set(detected)
        declared_set = set(declared)
        # 5) 신고분 미확인 — 여기가 "다른 위치에서 작업함"이 실제로 잡히는 자리다.
        unconfirmed = sorted(declared_set - detected_set)
        # 6) 신고 누락
        unreported = sorted(detected_set - declared_set)
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
    """사유 코드 + 적용 단계 → 최종 처리 (D0004 §3.7 판정 매트릭스).

    여러 사유가 동시에 나오면 가장 무거운 처리를 따른다 — 하나라도 거부면 거부다.
    """
    if not codes:
        return VERDICT_PASS
    # TRV-002 는 단계와 무관하게 항상 거부한다. 신고한 경로 자체가 범위 밖을
    # 가리키는 것은 추리가 아니라 자백이고, 통과시키면 원본 레포에 그대로 남는다.
    if TRV_OUT_OF_SCOPE in codes:
        return VERDICT_REJECT
    # TRV-006 은 단계와 무관하게 항상 통과 — 서버 사정을 작업자 책임으로 돌리지
    # 않는다. 다른 사유 없이 TRV-006 뿐이면 경고에 그친다.
    blocking = [c for c in codes if c != TRV_NO_SCOPE]
    if not blocking:
        return VERDICT_WARN
    if stage == STAGE_ENFORCE:
        return VERDICT_REJECT
    if stage == STAGE_WARN:
        return VERDICT_WARN
    return VERDICT_PASS  # 관측 — 기록만 하고 작업자에게는 아무것도 보이지 않는다


# ── 반려 안내문 (D0004 §3.8, 언어 전달 T2/TR2 — NR0003 §1-4) ────────────────
#
# NR0003 §1-4 가 실측한 문제: TR 반려 안내문이 966자 전부 한국어였고 언어를
# 바꿀 방법이 없었다. 이 함수를 부르는 자리(inbox_routes._handle_new)는 이미
# 토큰/헤더에서 locale 을 정해 두고 있었으므로(L0007 §2-2 "통로는 이미 있다"),
# 여기서 할 일은 그 값을 받아 안내문을 그 언어로 짓는 것뿐이다.

_SUPPORTED_NOTICE_LOCALES = ("ko", "en", "ja")


def _normalize_notice_locale(locale: str) -> str:
    return locale if locale in _SUPPORTED_NOTICE_LOCALES else "ko"


def _spelling_changed_files(locale: str) -> str:
    """구역 제목의 언어별 표기. 일본어 요청에도 영어 정식 표기가 나간다 — 이 문법
    글자에 일본어 별칭은 만들지 않았으므로(0355 T0009), 알려주는 표기도 영어다."""
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
    """거부 사유별 재작업 지시서.

    오류 통보가 아니라 지시서인 것이 요점이다. "형식 오류입니다" 한 줄만 던지면
    같은 것을 그대로 다시 제출한다. 어조는 단정이 아니라 추정으로 쓴다 — 실제
    원인이 다를 수 있는데 단정하면 엉뚱한 곳을 고치게 된다.

    ``locale`` 은 안내문 전체의 언어를 정한다(T2/TR2). 구역 번호(``[1]``~``[5]``)와
    사유 코드(``TRV-00N``)는 언어와 무관하게 그대로 남는다 — 번역 대상이 아니다.
    """
    loc = _normalize_notice_locale(locale)
    strings = _NOTICE[loc]
    codes: list[str] = result.get("codes") or []
    parts: list[str] = [strings["head"]]

    # 1. 왜 반려인지
    parts.append(strings["reason_heading"])
    for code in codes:
        parts.append(f"  - {code}: {_code_label(code, loc)}")

    # 2. 어디서 작업했어야 하는지 — 사고의 대부분이 자기 위치를 몰라서 생기므로
    #    이 두 줄의 효과가 가장 크다.
    parts.append(strings["location_heading"])
    parts.append(strings["branch_label"].format(branch=result.get("branch") or strings["unavailable"]))
    parts.append(strings["worktree_label"].format(worktree=result.get("worktree") or strings["unavailable"]))
    parts.append(strings["location_note"])

    # 3. 서버가 실제로 본 변경 목록
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

    # 4. 어떻게 다시 제출하는지
    parts.append(strings["resubmit"].format(
        heading=_spelling_changed_files(loc), none=_spelling_none(loc),
    ))

    # 5. 되돌리는 법 — 이게 없으면 원본 레포에 쓰레기가 남은 채 재제출만 반복된다.
    if TRV_OUT_OF_SCOPE in codes or TRV_UNCONFIRMED in codes:
        parts.append(strings["revert"])
    return "\n".join(parts)


# ── 문서 상세 조회 (D0004 §6) ───────────────────────────────────────────────

def verdict_from_meta(meta) -> Optional[dict]:
    """``documents.meta`` → 저장된 작업범위 검증 결과, 없으면 None.

    meta 는 dialect 에 따라 TEXT(JSON 문자열)로도 dict 로도 올라오므로 둘 다 받는다.
    검증 도입 이전의 TR 에는 이 키가 없고, 그때는 화면에서 영역 자체를 감춘다.
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


# ── 작업 지시에 실리는 안내 (D0004 §3.9, 언어 전달 T2/TR2) ───────────────────

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
    """작업 지시에 실리는 TR 작성 안내문 (D0004 §3.9, T2/TR2 언어 전달).

    NR0003 §1-4: 이전에는 이 안내문도 966자 반려 안내문과 함께 전부 한국어였다.
    """
    loc = _normalize_notice_locale(locale)
    return _TR_SECTION_GUIDE_TEMPLATES[loc].format(
        heading=_spelling_changed_files(loc), none=_spelling_none(loc),
    )


# 하위호환: 기존 호출부(ko 고정 화면·문서)는 모듈 상수를 그대로 쓴다.
TR_SECTION_GUIDE = tr_section_guide("ko")


_TR_SECTION_PLACEHOLDER_TEMPLATES: dict[str, str] = {
    "ko": "{heading}\n\n- <저장소 루트 기준 상대경로. 바꾼 파일이 없으면 이 목록 대신 '{none}' 한 줄>\n",
    "en": "{heading}\n\n- <path relative to the repository root. If nothing changed, write a single '{none}' line instead>\n",
    "ja": "{heading}\n\n- <リポジトリルート基準の相対パス。変更が無い場合はこの代わりに '{none}' の1行のみ>\n",
}


def tr_section_placeholder(locale: str = "ko") -> str:
    """새 TR ``content`` 자리에 미리 넣어 두는 빈 섹션 (T1, T2/TR2 언어 전달)."""
    loc = _normalize_notice_locale(locale)
    return _TR_SECTION_PLACEHOLDER_TEMPLATES[loc].format(
        heading=_spelling_changed_files(loc), none=_spelling_none(loc),
    )


TR_SECTION_PLACEHOLDER = tr_section_placeholder("ko")
