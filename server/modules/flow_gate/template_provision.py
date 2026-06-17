"""Design-document template body provision & management (group 0024).

Single source of truth for the L0013 algorithm contract: locale normalisation,
the 5-rank active-template resolution with ko fallback, type-default skeletons,
the path-non-exposure predicate, and the provision meta (available_locales).
Also holds the P0011 write-side validation helpers (content size / emptiness /
locale) shared by the management CRUD router.

DB access goes through modules.flow_gate.db.templates (DB0014 §4 SQL); this module
owns the language-agnostic algorithm and the application-only checks (usable() /
contains_path()) that SQL cannot express.

Design refs: D0010, P0011, P0012, L0013, DB0014.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, TypedDict

from .db import templates as tdb

logger = logging.getLogger(__name__)

# ── Parameters (L0013 §1 — single source of truth) ───────────────────────────
SUPPORTED_LOCALES: tuple[str, ...] = ("ko", "ja", "en")
FALLBACK_LOCALE = "ko"
MAX_CONTENT_BYTES = 512_000          # 500KB, mirrors settings _MAX_TEMPLATE_SIZE
MIN_CONTENT_BYTES = 1                # trimmed length must be > 0
DESIGN_TYPES: frozenset[str] = frozenset({"D", "P", "L", "DB"})  # design-time mirror (runtime gate = is_design_type)


def is_design_type(type_code: str) -> bool:
    """Runtime gate (DB0014 §4-1): True iff type_code is a design-series type.

    Single source of truth — delegates to the DB (document_types.series='design').
    DESIGN_TYPES above is only the design-time mirror used for skeleton coverage.
    Re-exported here so the provision consumers (process_service Next-Step embed,
    the worker mention) gate on one symbol instead of reaching into tdb directly.
    """
    return tdb.is_design_type(type_code)


class UnknownDesignType(Exception):
    """type_code is not a valid design type (document_types series='design')."""

    def __init__(self, type_code: str) -> None:
        super().__init__(type_code)
        self.type_code = type_code


class TemplateValidationError(ValueError):
    """Write-side validation failure (E4/E5) — maps to HTTP 422."""


# ── type-default skeletons (L0013 §1-1) ──────────────────────────────────────
# Deterministic, locale-neutral, NEVER contain a file path (AC-1 / invariant c).
TYPE_DEFAULT_SKELETON: dict[str, str] = {
    "D": (
        "# 기본설계\n\n"
        "> 표준 템플릿 본문이 아직 등록되지 않았습니다. 아래 타입 기본 골격으로 작성하세요.\n\n"
        "## 1. 배경\n## 2. 목표·성공기준\n## 3. 범위\n## 4. 요구사항\n## 5. 사용자 시나리오\n"
    ),
    "P": (
        "# 프로토콜설계\n\n"
        "> 표준 템플릿 본문이 아직 등록되지 않았습니다. 아래 타입 기본 골격으로 작성하세요.\n\n"
        "## 1. 표기 규칙(S/C)\n## 2. 리소스·엔드포인트\n## 3. 시나리오\n## 4. 실패·엣지\n## 5. 검증 규칙\n"
    ),
    "L": (
        "# 로직설계\n\n"
        "> 표준 템플릿 본문이 아직 등록되지 않았습니다. 아래 타입 기본 골격으로 작성하세요.\n\n"
        "## 목적\n## 1. 파라미터 정의\n## 2. 알고리즘/처리 로직\n## 3. 상태 전이\n## 4. 결정 트리\n## 5. 경계 조건\n"
    ),
    "DB": (
        "# DB설계\n\n"
        "> 표준 템플릿 본문이 아직 등록되지 않았습니다. 아래 타입 기본 골격으로 작성하세요.\n\n"
        "## 1. 대상 테이블\n## 2. 컬럼·키·제약\n## 3. 마이그레이션\n## 4. 읽기/쓰기 쿼리\n"
    ),
}
_DEFENSIVE_SKELETON = (
    "# {type_code} 설계\n\n"
    "> 표준 템플릿 본문이 아직 등록되지 않았습니다. 아래 기본 골격으로 작성하세요.\n\n"
    "## 1. 개요\n## 2. 본문\n"
)

# ── path-non-exposure predicate (L0013 §5-5 / AC-1) ──────────────────────────
_DRIVE_PATH_RE = re.compile(r"(^|\s)[A-Za-z]:[\\/]")
_TEMPLATE_LINE_RE = re.compile(r"^\s*Template:\s*\S", re.MULTILINE)


def bytelen(text: str) -> int:
    return len(text.encode("utf-8"))


def contains_path(text: str) -> bool:
    """True if text contains a file-path token (drive path / rule-template dir /
    a legacy ``Template:`` pointer line). Shared by ingest (write-block) and the
    provision usable() read-side graceful-degrade.
    """
    if not text:
        return False
    return bool(
        _DRIVE_PATH_RE.search(text)
        or "_rule/templates" in text
        or "_old/templates" in text
        or _TEMPLATE_LINE_RE.search(text)
    )


def normalize_locale(x_locale: Optional[str]) -> str:
    """L0013 §2-1 — x-locale is a weak preference: never reject, fold to ko.

    None/blank → ko; ko/ja/en → itself; anything else (zh, …) → ko.
    """
    if not x_locale or not x_locale.strip():
        return FALLBACK_LOCALE
    x = x_locale.strip()
    return x if x in SUPPORTED_LOCALES else FALLBACK_LOCALE


def build_type_default(type_code: str) -> str:
    """L0013 §2-3 — deterministic, DB-free type skeleton (writing never blocked).

    Falls back to a defensive skeleton (not a 500) if a design type lacks a
    skeleton entry (coverage-invariant drift, L0013 §5-6).
    """
    skeleton = TYPE_DEFAULT_SKELETON.get(type_code)
    if skeleton is None:
        logger.warning("type-default skeleton missing for design type %s", type_code)
        skeleton = _DEFENSIVE_SKELETON.format(type_code=type_code)
    # self-check: our own artefact must never carry a path (regression guard)
    assert not contains_path(skeleton), "type-default skeleton must not contain a path"
    return skeleton


class Resolved(TypedDict):
    content: str
    resolution: str
    scope: Optional[str]
    resolved_locale: Optional[str]
    is_active: Optional[int]
    bytes: int
    resolved_template_id: Optional[int]


def _usable(content: Optional[str], *, template_id: int, locale: str) -> bool:
    """A body terminates resolution only if it exists and carries no path token.

    Path-polluted bodies (E4/E5 writes are not path-validated — L0013 §5-5
    forward-note) fall through like an active-empty row; a server warning is
    logged so an admin can fix it via E5. Writing is never aborted.
    """
    if content is None:
        return False
    if contains_path(content):
        logger.warning(
            "path-polluted template body skipped (template_id=%s locale=%s)",
            template_id, locale,
        )
        return False
    return True


def resolve_active_template(
    project_id: str, type_code: str, req_locale: str
) -> Resolved:
    """L0013 §2-2 / P0012 §3 — resolve exactly one body for (project, type, locale).

    5-rank: project-exact → project-ko → global-exact → global-ko → type-default.
    Active-empty / path-polluted rows fall through (invariant b: never blocks).
    Raises UnknownDesignType for non-design types (→ 404).
    """
    if not tdb.is_design_type(type_code):
        raise UnknownDesignType(type_code)

    # active_registry_rows already orders project-first then global, id DESC.
    for row in tdb.active_registry_rows(type_code, project_id):
        rid = row["id"]
        scope = "project" if row["project_id"] == project_id else "global"
        hit = tdb.content_for(rid, req_locale)
        if _usable(hit, template_id=rid, locale=req_locale):
            return Resolved(
                content=hit,  # type: ignore[typeddict-item]
                resolution="exact" if scope == "project" else "global-exact",
                scope=scope,
                resolved_locale=req_locale,
                is_active=1,
                bytes=bytelen(hit),  # type: ignore[arg-type]
                resolved_template_id=rid,
            )
        if req_locale != FALLBACK_LOCALE:
            hit_ko = tdb.content_for(rid, FALLBACK_LOCALE)
            if _usable(hit_ko, template_id=rid, locale=FALLBACK_LOCALE):
                return Resolved(
                    content=hit_ko,  # type: ignore[typeddict-item]
                    resolution="fallback-ko" if scope == "project" else "global-fallback-ko",
                    scope=scope,
                    resolved_locale=FALLBACK_LOCALE,
                    is_active=1,
                    bytes=bytelen(hit_ko),  # type: ignore[arg-type]
                    resolved_template_id=rid,
                )
        # active-empty / path-polluted → fall through to the next row

    # rank 5: no active row produced a usable body → type-default (never blocks)
    skeleton = build_type_default(type_code)
    return Resolved(
        content=skeleton,
        resolution="type-default",
        scope=None,
        resolved_locale=None,
        is_active=None,
        bytes=bytelen(skeleton),
        resolved_template_id=None,
    )


def resolve_active_meta(project_id: str, type_code: str, req_locale: str) -> dict:
    """L0013 §2-7 / P0012 §4-6 — provision meta (no body): available_locales etc.

    available_locales is enumerated against the row that TERMINATED resolution
    (project or global), or [] for type-default.
    """
    r = resolve_active_template(project_id, type_code, req_locale)
    rid = r["resolved_template_id"]
    available = tdb.available_locales(rid) if rid is not None else []
    return {
        "project_id": project_id,
        "type_code": type_code,
        "requested_locale": req_locale,
        "resolved_locale": r["resolved_locale"],
        "resolution": r["resolution"],
        "scope": r["scope"],
        "available_locales": available,
        "bytes": r["bytes"],
    }


# ── writer-facing render (shared by mention + AC/RJ self-contained sections) ──
def render_provision_block(type_code: str, req_locale: str, resolved: Resolved) -> str:
    """Render the writer-facing template block: heading + provenance badges + body.

    Embeds the resolved body verbatim — NEVER a file path (AC-1). Single source of
    truth so the worker mention (mention_service.build_mention) and the AC/RJ
    self-contained sections (process_service) stay byte-identical and cannot drift.
    """
    display_locale = resolved["resolved_locale"] or "기본"
    lines = [f"## 다음 문서 템플릿 ({type_code} / {display_locale})"]
    if resolved["resolution"] == "type-default":
        lines.append("> 표준 템플릿 미등록 — 타입 기본 골격을 제공합니다. 작성은 진행 가능합니다.")
    else:
        if resolved["scope"] == "global":
            lines.append("> 출처: 전역 표준 템플릿")
        if resolved["resolved_locale"] != req_locale:
            lines.append(
                f"> 제공 언어: {resolved['resolved_locale']} (요청 {req_locale} 미보유, 폴백)"
            )
    lines += ["", resolved["content"]]
    return "\n".join(lines)


# ── write-side validation (P0011 §5 — E4/E5) ─────────────────────────────────
def validate_locale(locale: str) -> None:
    if locale not in SUPPORTED_LOCALES:
        raise TemplateValidationError(
            f"Unsupported locale '{locale}'. Allowed: {', '.join(SUPPORTED_LOCALES)}."
        )


def validate_content(content: Optional[str]) -> str:
    """P0011 §5 / §4-1 / §4-5 — non-empty (trim>0) and ≤500KB. Returns content."""
    if content is None or content.strip() == "":
        raise TemplateValidationError("Template content must not be empty.")
    if bytelen(content) > MAX_CONTENT_BYTES:
        raise TemplateValidationError("Template content size must not exceed 500KB.")
    return content
