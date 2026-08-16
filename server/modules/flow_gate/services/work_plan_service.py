"""Work plan (WP) domain service — flowgate.default.0395.

One validator, one writer, two callers. The human API (documents/routers/work_plan.py)
and the AI inbox branch (api/inbox_routes.py) both come through this module, because
D0007 §2.2 makes that the whole point of the design: "검증기와 저장 서비스가 사람 경로와
AI 경로 양쪽의 유일한 통로". Two validators would drift within a release.

Contracts implemented here
  * P0009 §2   — canonical JSON schema, key order, unknown-field policy
  * P0009 §1.2 — ``{"code", "message", "errors": [{loc, key, code, msg}]}`` failures
  * P0009 §4   — create / read / save shapes (the routes only marshal)
  * P0009 §5   — inbox flavour: same verdicts, inbox's own failure envelope
  * L0010 §1   — every threshold; this module is the only place they are written
  * L0010 §2.1 — quantity → step expansion (the definition of the canonical body)
  * L0010 §2.3 — layered validation; a layer with errors never runs the next one

Deliberately NOT here: apply/preview/fill (P0009 §7) and the applications journal
(P0009 §8). Those need the workflow sequence and belong to the next task set.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from typing import Any, Iterable, Optional

from modules.flow_gate.db import templates as db_templates
from modules.flow_gate.documents.constants import (
    STEP_NOTE_MAX_CHARS,
    WORK_PLAN_COUNTABLE_ORDER,
    WORK_PLAN_LOCKED_TYPES,
    WORK_PLAN_PAIR_MAP,
    WORK_PLAN_SHEET_TYPES,
    WORK_PLAN_STEP_TYPES,
    WORK_PLAN_TYPE,
    WORK_PLAN_TYPE_UNITS,
)

# ── L0010 §1.1 thresholds — single source of truth ───────────────────────────
WP_VERSION_SUPPORTED = 1
COUNT_MIN = 0
COUNT_MAX = 20
STEPS_MAX = 100
# 0406 T0022 작업 6: 한줄 멘트 상한의 정본은 documents.constants 하나뿐이다. 여기에
# 200 을 따로 적어 두었던 것이 시퀀스 쪽 200 과 화면의 200 과 함께 세 벌로 늘어났다.
NOTE_MAX_CHARS = STEP_NOTE_MAX_CHARS
PROVIDER_CANDIDATES_MAX = 50
ERRORS_REPORTED_MAX = 50

BINDING_ADVISORY = "advisory"
LOCKED_REASON_SERVER_ASSEMBLED = "server_assembled"

KEY_PATTERN = re.compile(r"^([A-Z]{1,3})#([1-9][0-9]*)$")
PROVIDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# L0010 §1.1 note_forbidden_chars: C0 controls plus DEL. Newlines and tabs are
# included on purpose — a one-line note never legitimately holds them, and
# silently replacing them would tell the AI its body was stored verbatim.
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

PAIR_ROLES = ("instruction", "result", "single")
ORIGINS = ("human", "ai_suggested", "system")

TOP_LEVEL_ORDER = (
    "wp_version",
    "binding",
    "counted_types",
    "quantities",
    "provider_candidates",
    "defaults",
    "steps",
)
STEP_FIELD_ORDER = (
    "key",
    "type",
    "ordinal",
    "pair_key",
    "pair_role",
    "provider_id",
    "provider_display_name",
    "note",
    "locked",
    "locked_reason",
    "origin",
)
CANDIDATE_FIELD_ORDER = ("provider_id", "display_name", "group_label")
DEFAULTS_FIELD_ORDER = ("provider_id", "note")

LOCALES = ("ko", "en", "ja")
FALLBACK_LOCALE = "ko"

DOCUMENT_FILENAME = "document.json"
HELP_TEMPLATE_PATH = "/help/items/design_template/WP"


# ── Errors ───────────────────────────────────────────────────────────────────

class WorkPlanValidationError(Exception):
    """One or more rule violations. Carries every error, not just the first."""

    def __init__(self, errors: list[dict], *, action: str = "save"):
        self.errors = errors
        self.action = action
        super().__init__(f"{len(errors)} work plan validation error(s)")


class WorkPlanUnreadable(Exception):
    """The stored body cannot be opened as a table (P0009 §4.5)."""

    def __init__(self, reason: str, detail: str = "", raw: Optional[str] = None):
        self.reason = reason
        self.detail = detail
        self.raw = raw
        super().__init__(f"{reason}: {detail}")


# ── Copy (P0009 §1.3: message/msg follow the request locale) ─────────────────

_ERROR_COPY: dict[str, dict[str, str]] = {
    "json_parse_failed": {
        "ko": "작업계획 본문이 JSON 객체가 아닙니다.",
        "en": "The work plan body is not a JSON object.",
        "ja": "作業計画の本文が JSON オブジェクトではありません。",
    },
    "wp_version_invalid": {
        "ko": "wp_version 은 정수여야 합니다.",
        "en": "wp_version must be an integer.",
        "ja": "wp_version は整数でなければなりません。",
    },
    "wp_version_unsupported": {
        "ko": "wp_version={value} 는 이 서버가 읽을 수 없습니다.",
        "en": "wp_version={value} cannot be read by this server.",
        "ja": "wp_version={value} はこのサーバーでは読めません。",
    },
    "missing_field": {
        "ko": "필수 항목 {field} 가 없습니다.",
        "en": "Required field {field} is missing.",
        "ja": "必須項目 {field} がありません。",
    },
    "type_invalid": {
        "ko": "{field} 의 형이 올바르지 않습니다.",
        "en": "{field} has the wrong type.",
        "ja": "{field} の型が正しくありません。",
    },
    "unknown_field": {
        "ko": "모르는 항목 {field} 입니다. 실험용 항목은 x_ 로 시작해야 보존됩니다.",
        "en": "Unknown field {field}. Experimental fields must start with x_ to be preserved.",
        "ja": "未知の項目 {field} です。実験用の項目は x_ で始めてください。",
    },
    "enum_not_allowed": {
        "ko": "{field} 에 쓸 수 없는 값입니다.",
        "en": "{field} has a value that is not allowed.",
        "ja": "{field} に使用できない値です。",
    },
    "binding_not_allowed": {
        "ko": "binding 은 항상 advisory 여야 합니다. 작업계획은 실행을 강제하지 않습니다.",
        "en": "binding must always be advisory. A work plan never forces execution.",
        "ja": "binding は常に advisory でなければなりません。作業計画は実行を強制しません。",
    },
    "empty_selection": {
        "ko": "{what} 을 하나 이상 체크해 주세요.",
        "en": "Check at least one {what}.",
        "ja": "{what} を一つ以上チェックしてください。",
    },
    "unknown_type_code": {
        "ko": "{code} 는 수량을 셀 수 있는 타입이 아닙니다.",
        "en": "{code} is not a countable document type.",
        "ja": "{code} は数量を数えられるタイプではありません。",
    },
    "duplicate_type": {
        "ko": "타입 {code} 가 두 번 나옵니다.",
        "en": "Type {code} appears twice.",
        "ja": "タイプ {code} が二回出てきます。",
    },
    "quantities_key_mismatch": {
        "ko": "quantities 의 키가 counted_types 와 다릅니다.",
        "en": "The keys of quantities differ from counted_types.",
        "ja": "quantities のキーが counted_types と異なります。",
    },
    "unit_mismatch": {
        "ko": "{code} 의 단위는 {expected} 입니다.",
        "en": "The unit of {code} is {expected}.",
        "ja": "{code} の単位は {expected} です。",
    },
    "count_not_integer": {
        "ko": "수량은 정수여야 합니다.",
        "en": "The count must be an integer.",
        "ja": "数量は整数でなければなりません。",
    },
    "count_out_of_range": {
        "ko": "수량은 {min} 이상 {max} 이하여야 합니다.",
        "en": "The count must be between {min} and {max}.",
        "ja": "数量は {min} 以上 {max} 以下でなければなりません。",
    },
    "key_format_invalid": {
        "ko": "논리 키는 <타입코드>#<회차> 서식이어야 합니다. 받은 값: {value}",
        "en": "A step key must look like <TYPE>#<ordinal>. Received: {value}",
        "ja": "論理キーは <タイプコード>#<回次> の書式です。受け取った値: {value}",
    },
    "duplicate_key": {
        "ko": "논리 키 {value} 가 두 번 나옵니다.",
        "en": "Step key {value} appears twice.",
        "ja": "論理キー {value} が二回出てきます。",
    },
    "steps_quantity_mismatch": {
        "ko": "steps 가 quantities 에서 펼쳐지는 단계 목록과 다릅니다.",
        "en": "steps does not match the list expanded from quantities.",
        "ja": "steps が quantities から展開される段階リストと異なります。",
    },
    "steps_too_many": {
        "ko": "단계는 최대 {max} 개까지입니다.",
        "en": "A work plan may hold at most {max} steps.",
        "ja": "段階は最大 {max} 個までです。",
    },
    "step_shape_mismatch": {
        "ko": "이 단계의 type·ordinal·pair_key·pair_role 이 논리 키와 맞지 않습니다.",
        "en": "This step's type/ordinal/pair_key/pair_role do not match its key.",
        "ja": "この段階の type・ordinal・pair_key・pair_role がキーと一致しません。",
    },
    "locked_flag_mismatch": {
        "ko": "잠금 표시가 타입과 다릅니다.",
        "en": "The locked flag does not match the step type.",
        "ja": "ロック表示がタイプと異なります。",
    },
    "provider_not_allowed": {
        "ko": "테스트레포트(TSR)는 서버가 자동 조립하므로 공급자를 지정할 수 없습니다.",
        "en": "TSR is assembled by the server, so it cannot take a provider.",
        "ja": "テストレポート(TSR)はサーバーが自動組立するため提供者を指定できません。",
    },
    "note_not_allowed": {
        "ko": "테스트레포트(TSR)는 서버가 자동 조립하므로 한줄 멘트를 적을 수 없습니다.",
        "en": "TSR is assembled by the server, so it cannot take a note.",
        "ja": "テストレポート(TSR)はサーバーが自動組立するため一行メモを書けません。",
    },
    "origin_not_allowed": {
        "ko": "잠긴 단계의 origin 은 system 이어야 합니다.",
        "en": "A locked step must have origin=system.",
        "ja": "ロックされた段階の origin は system でなければなりません。",
    },
    # 0411 T0004: 코드 이름은 그대로 두되(화면·시험이 이 코드를 고정한다) 문구는 새 규칙을
    # 말한다 — 후보이거나 이 프로젝트에 등록된 공급자면 고를 수 있다.
    "provider_not_candidate": {
        "ko": "{value} 는 이 작업계획의 공급자 후보도 아니고 이 프로젝트에 등록된 공급자도 아닙니다.",
        "en": "{value} is neither one of this work plan's provider candidates nor a provider registered in this project.",
        "ja": "{value} はこの作業計画の提供者候補でも、このプロジェクトに登録された提供者でもありません。",
    },
    "provider_id_format_invalid": {
        "ko": "공급자 식별자 서식이 올바르지 않습니다: {value}",
        "en": "Malformed provider id: {value}",
        "ja": "提供者識別子の書式が正しくありません: {value}",
    },
    "duplicate_provider_candidate": {
        "ko": "공급자 {value} 가 후보 목록에 두 번 있습니다.",
        "en": "Provider {value} appears twice in the candidate list.",
        "ja": "提供者 {value} が候補リストに二回あります。",
    },
    "provider_candidates_too_many": {
        "ko": "공급자 후보는 최대 {max} 개까지입니다.",
        "en": "At most {max} provider candidates are allowed.",
        "ja": "提供者候補は最大 {max} 個までです。",
    },
    "note_too_long": {
        "ko": "한줄 멘트는 {max} 자까지입니다.",
        "en": "A note may be at most {max} characters.",
        "ja": "一行メモは {max} 文字までです。",
    },
    "note_has_control_char": {
        "ko": "한줄 멘트에 개행·탭 같은 제어문자를 쓸 수 없습니다.",
        "en": "A note cannot contain control characters such as newline or tab.",
        "ja": "一行メモに改行・タブなどの制御文字は使えません。",
    },
}

_HEADLINE = {
    "save": {
        "ko": "작업계획을 저장하지 못했습니다. {n}개 항목이 규칙에 맞지 않습니다.",
        "en": "The work plan was not saved. {n} item(s) break the rules.",
        "ja": "作業計画を保存できませんでした。{n}件の項目が規則に合いません。",
    },
    "create": {
        "ko": "작업계획을 만들지 못했습니다. {n}개 항목이 규칙에 맞지 않습니다.",
        "en": "The work plan was not created. {n} item(s) break the rules.",
        "ja": "作業計画を作成できませんでした。{n}件の項目が規則に合いません。",
    },
}

_EMPTY_SELECTION_WHAT = {
    "counted_types": {
        "ko": "수량을 확인할 타입", "en": "document type to count", "ja": "数量を確認するタイプ",
    },
    "provider_candidates": {
        "ko": "투입할 공급자", "en": "provider to use", "ja": "投入する提供者",
    },
}

_INBOX_PREFIX = {
    "ko": "작업계획을 저장할 수 없습니다.",
    "en": "The work plan cannot be saved.",
    "ja": "作業計画を保存できません。",
}

_INBOX_NOT_JSON = {
    "ko": ("작업계획(WP)의 본문은 JSON 이어야 합니다. Markdown 표로 보낼 수 없습니다. "
           "{where} 서식은 도움말 항목 design_template/WP 를 보세요."),
    "en": ("The body of a work plan (WP) must be JSON, not Markdown. "
           "{where} See the help item design_template/WP for the format."),
    "ja": ("作業計画(WP)の本文は JSON でなければなりません。Markdown の表では送れません。"
           "{where} 書式はヘルプ項目 design_template/WP を参照してください。"),
}

_INBOX_NOT_JSON_WHERE = {
    "ko": "{line}행 {col}열에서 '{{' 가 아닌 '{char}' 을 만났습니다.",
    "en": "At line {line}, column {col} the parser found '{char}' instead of '{{'.",
    "ja": "{line}行{col}列で '{{' ではなく '{char}' に出会いました。",
}


def normalize_locale(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return FALLBACK_LOCALE
    head = raw.split(",")[0].split("-")[0].split("_")[0]
    return head if head in LOCALES else FALLBACK_LOCALE


def _copy(table: dict, locale: str, key: str) -> str:
    entry = table.get(key) or {}
    return entry.get(locale) or entry.get(FALLBACK_LOCALE) or key


def _error(code: str, loc: str, key: Optional[str] = None, **params) -> dict:
    return {"loc": loc, "key": key, "code": code, "params": params}


def empty_selection_error(field: str) -> dict:
    """"하나도 고르지 않았다" for a create request that skipped the dialog (P0009 §4.3)."""
    return _error("empty_selection", field, what_key=field)


def render_errors(errors: list[dict], locale: str) -> list[dict]:
    """Attach the localized ``msg`` and drop the internal ``params`` carrier."""
    locale = normalize_locale(locale)
    rendered = []
    for err in errors[:ERRORS_REPORTED_MAX]:
        template = _copy(_ERROR_COPY, locale, err["code"])
        params = dict(err.get("params") or {})
        # "체크해 주세요" needs a localized noun, so the error carries the noun's key
        # rather than a pre-rendered word — the error is built before the locale is known.
        what_key = params.pop("what_key", None)
        if what_key:
            params["what"] = _copy(_EMPTY_SELECTION_WHAT, locale, what_key)
        try:
            msg = template.format(**params)
        except (KeyError, IndexError, ValueError):
            msg = template
        rendered.append({
            "loc": err.get("loc", ""),
            "key": err.get("key"),
            "code": err["code"],
            "msg": msg,
        })
    return rendered


def error_response(exc: WorkPlanValidationError, locale: str) -> dict:
    """P0009 §1.2 body for the human routes."""
    locale = normalize_locale(locale)
    rendered = render_errors(exc.errors, locale)
    payload = {
        "code": "wp_validation_failed",
        "message": _copy(_HEADLINE, locale, exc.action).format(n=len(exc.errors)),
        "errors": rendered,
    }
    if len(exc.errors) > ERRORS_REPORTED_MAX:
        payload["error_total"] = len(exc.errors)
    return payload


def inbox_error_message(exc: WorkPlanValidationError, locale: str) -> str:
    """P0009 §5.3: the same verdicts, flattened into the inbox's one-line envelope."""
    locale = normalize_locale(locale)
    parts = []
    for index, err in enumerate(render_errors(exc.errors, locale), start=1):
        where = err["loc"] or "body"
        if err.get("key"):
            where = f"{where} ({err['key']})"
        parts.append(f"({index}) {where}: {err['msg']}")
    prefix = _INBOX_PREFIX.get(locale, _INBOX_PREFIX[FALLBACK_LOCALE])
    return " ".join([prefix] + parts)


def inbox_not_json_message(raw: str, exc: Optional[Exception], locale: str) -> str:
    """P0009 §5.2: say where it broke, so an unattended worker can fix it."""
    locale = normalize_locale(locale)
    where = ""
    line = getattr(exc, "lineno", None)
    col = getattr(exc, "colno", None)
    if line and col:
        lines = (raw or "").splitlines() or [""]
        text = lines[line - 1] if 0 < line <= len(lines) else ""
        char = text[col - 1] if 0 < col <= len(text) else ""
        if char:
            where = _INBOX_NOT_JSON_WHERE.get(
                locale, _INBOX_NOT_JSON_WHERE[FALLBACK_LOCALE]
            ).format(line=line, col=col, char=char)
    template = _INBOX_NOT_JSON.get(locale, _INBOX_NOT_JSON[FALLBACK_LOCALE])
    return template.format(where=(where + " ") if where else "").strip()


# ── Type registry (L0010 §2.1 결정 2: order comes from the table) ─────────────

def list_countable_types(project_id: Optional[str] = None, locale: str = "ko") -> list[dict]:
    """Countable types in the order a work plan lists them.

    The set of countable codes and their units are facts about the workflow and live
    in documents.constants; the ORDER is read from document_types so that adding a
    design type does not require editing this file (L0010 §2.1 결정 2).

    Ordering rule: sheets before sets; within sheets the design *instruction* (DS)
    leads the design series; within each group, the table's own sort_order decides.
    That reproduces DS · D · P · L · DB · N · T · TS from the current table without
    naming any of them here.
    """
    try:
        rows = db_templates.list_document_types(project_id=None, locale=locale)
    except Exception:  # noqa: BLE001 — a registry outage must not crash the reader
        rows = []

    by_code: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("type_code") or "").upper()
        if code not in WORK_PLAN_TYPE_UNITS:
            continue
        if not row.get("is_active", 1):
            continue
        series = str(row.get("series") or "")
        # 'L' exists twice (design 로직 / general 로그). Only the design one is a
        # countable design sheet; ignore the log type with the same letter.
        if series not in ("design", "instruction"):
            continue
        if code in by_code and by_code[code].get("series") == "design":
            continue
        by_code[code] = {
            "code": code,
            "name": row.get("type_name") or code,
            "series": series,
            "sort_order": row.get("sort_order") or 0,
        }

    entries = list(by_code.values())
    for code in WORK_PLAN_COUNTABLE_ORDER:
        if code not in by_code:
            entries.append({
                "code": code,
                "name": code,
                "series": "design" if code in WORK_PLAN_SHEET_TYPES else "instruction",
                "sort_order": WORK_PLAN_COUNTABLE_ORDER.index(code),
            })

    def _rank(entry: dict) -> tuple:
        unit = WORK_PLAN_TYPE_UNITS[entry["code"]]
        return (
            0 if unit == "sheet" else 1,
            0 if entry["series"] == "instruction" and unit == "sheet" else 1,
            entry["sort_order"],
            entry["code"],
        )

    ordered = sorted(entries, key=_rank)
    result = []
    for entry in ordered:
        code = entry["code"]
        item = {
            "code": code,
            "name": entry["name"],
            "series": entry["series"],
            "unit": WORK_PLAN_TYPE_UNITS[code],
            "countable": True,
        }
        pair = WORK_PLAN_PAIR_MAP.get(code)
        if pair:
            item["pair_code"] = pair
            item["pair_name"] = _type_name(rows, pair, locale) or pair
        result.append(item)
    return result


def _type_name(rows: Iterable[dict], code: str, locale: str) -> Optional[str]:
    for row in rows:
        if str(row.get("type_code") or "").upper() == code:
            return row.get("type_name")
    return None


def annotate_types(rows: list[dict]) -> list[dict]:
    """Add ``unit``/``countable``/``pair_code`` to a document-type list response.

    P0009 §4.1: without this the create dialog would have to hardcode the type codes
    and would need editing every time a type is added. Purely additive — existing
    keys are untouched.
    """
    names = {
        str(row.get("type_code") or "").upper(): row.get("type_name")
        for row in rows
        if row.get("type_name")
    }
    annotated = []
    for row in rows:
        item = dict(row)
        code = str(row.get("type_code") or "").upper()
        series = str(row.get("series") or "")
        countable = code in WORK_PLAN_TYPE_UNITS and series in ("design", "instruction")
        item["countable"] = countable
        item["unit"] = WORK_PLAN_TYPE_UNITS[code] if countable else None
        pair = WORK_PLAN_PAIR_MAP.get(code) if countable else None
        if pair:
            item["pair_code"] = pair
            # P0009 §4.1 · §10: 생성 대화상자는 짝의 이름까지 보인다("작업지시
            # 3세트 · 작업레포트 포함"). 같은 응답 안의 이름을 그대로 쓰므로
            # 로케일이 저절로 맞고, 짝 타입이 목록에 없으면 항목을 만들지
            # 않는다 — 타입 코드를 이름인 척 보이는 것보다 없는 편이 낫다.
            pair_name = names.get(pair)
            if pair_name:
                item["pair_name"] = pair_name
        annotated.append(item)
    return annotated


def _order_codes(project_id: Optional[str] = None) -> list[str]:
    return [entry["code"] for entry in list_countable_types(project_id)]


def type_order(counted_types: Iterable[str], project_id: Optional[str] = None) -> list[str]:
    """L0010 §2.1 type_order: registry order first, then anything else by code."""
    wanted = list(counted_types)
    ordered = [code for code in _order_codes(project_id) if code in wanted]
    remainder = sorted(set(wanted) - set(ordered))
    return ordered + remainder


# ── Expansion (L0010 §2.1) ───────────────────────────────────────────────────

def make_key(type_code: str, ordinal: int) -> str:
    return f"{type_code}#{ordinal}"


def make_step(type_code: str, ordinal: int, pair_key: Optional[str], pair_role: str) -> dict:
    locked = type_code in WORK_PLAN_LOCKED_TYPES
    return {
        "key": make_key(type_code, ordinal),
        "type": type_code,
        "ordinal": ordinal,
        "pair_key": pair_key,
        "pair_role": pair_role,
        "provider_id": None,
        "provider_display_name": None,
        "note": None,
        "locked": locked,
        "locked_reason": LOCKED_REASON_SERVER_ASSEMBLED if locked else None,
        "origin": "system" if locked else "human",
    }


def expand_steps(
    counted_types: Iterable[str],
    quantities: dict,
    project_id: Optional[str] = None,
) -> list[dict]:
    steps: list[dict] = []
    for code in type_order(counted_types, project_id):
        quantity = quantities.get(code) or {}
        count = quantity.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count <= 0:
            continue
        if WORK_PLAN_TYPE_UNITS.get(code) == "set":
            result_code = WORK_PLAN_PAIR_MAP[code]
            for ordinal in range(1, count + 1):
                steps.append(make_step(code, ordinal, make_key(result_code, ordinal), "instruction"))
                steps.append(make_step(result_code, ordinal, make_key(code, ordinal), "result"))
        else:
            for ordinal in range(1, count + 1):
                steps.append(make_step(code, ordinal, None, "single"))
    return steps


def initial_body(
    counted_types: list[str],
    provider_candidates: list[dict],
    project_id: Optional[str] = None,
    *,
    quantities: Optional[dict[str, int]] = None,
    defaults: Optional[dict] = None,
    type_providers: Optional[dict[str, str]] = None,
) -> dict:
    """Build a creation-time plan while preserving the legacy all-1/unassigned defaults."""
    ordered = type_order(counted_types, project_id)
    requested_quantities = quantities or {}
    canonical_quantities = {
        code: {
            "unit": WORK_PLAN_TYPE_UNITS.get(code, "sheet"),
            "count": requested_quantities.get(code, 1),
        }
        for code in ordered
    }
    requested_defaults = defaults or {}
    canonical_defaults = {
        "provider_id": requested_defaults.get("provider_id"),
        "note": requested_defaults.get("note", ""),
    }
    assignments = type_providers or {}
    provider_by_type: dict[str, Optional[str]] = {}
    for code in ordered:
        provider_id = assignments.get(code, canonical_defaults["provider_id"])
        provider_by_type[code] = provider_id
        if WORK_PLAN_TYPE_UNITS.get(code) == "set":
            paired = WORK_PLAN_PAIR_MAP.get(code)
            if paired:
                provider_by_type[paired] = provider_id

    display_names = {
        candidate.get("provider_id"): candidate.get("display_name")
        for candidate in provider_candidates
    }
    steps = expand_steps(ordered, canonical_quantities, project_id)
    for step in steps:
        if step.get("locked"):
            continue
        provider_id = provider_by_type.get(step.get("type"))
        step["provider_id"] = provider_id
        step["provider_display_name"] = display_names.get(provider_id) if provider_id else None
        step["origin"] = "human"

    return {
        "wp_version": WP_VERSION_SUPPORTED,
        "binding": BINDING_ADVISORY,
        "counted_types": ordered,
        "quantities": canonical_quantities,
        "provider_candidates": list(provider_candidates),
        "defaults": canonical_defaults,
        "steps": steps,
    }


def empty_recovery_body(project_id: Optional[str] = None) -> dict:
    """A valid but undecided plan, for recovering a work plan whose file is missing.

    Every countable type is on the table at 0 — "the plan exists, nothing is decided
    yet". The alternative (a Markdown stub, which is what generic recovery writes) would
    produce a canonical file the reader cannot open at all, and the alternative of
    guessing counts would put numbers nobody chose in front of a reviewer.
    """
    ordered = [entry["code"] for entry in list_countable_types(project_id)]
    quantities = {
        code: {"unit": WORK_PLAN_TYPE_UNITS.get(code, "sheet"), "count": 0}
        for code in ordered
    }
    return {
        "wp_version": WP_VERSION_SUPPORTED,
        "binding": BINDING_ADVISORY,
        "counted_types": ordered,
        "quantities": quantities,
        "provider_candidates": [],
        "defaults": {"provider_id": None, "note": ""},
        "steps": [],
    }


# ── Plans created outside the create dialog (0395 T0026 재작업) ───────────────
#
# 작업계획 문서를 만드는 길은 생성 대화상자 하나가 아니다. 워크플로 머리 칸이 WP 일 때
# [빈 문서 만들기]로도 만들어지는데, 그 길은 제목만 받아 마크다운 머리말 뼈대를 쓴다.
# 그 파일은 이 모듈의 판독기가 열 수 없어서, 문서를 열면 표 대신
# "이 작업계획을 표로 열 수 없습니다"(JSON 파싱 오류)만 남는다 — 사용자가 신고한 증상이다.
#
# 그 길에는 사용자가 고른 수량·공급자가 없다. 그래서 숫자를 지어내지 않고, 이미 정해져
# 있는 곳에서 읽어 온다: 수량은 이 그룹의 워크플로 시퀀스(지금 잡혀 있는 설계 장수와 작업
# 세트수 그 자체), 공급자는 프로젝트의 실행 체인과 문서종류별 배정표(연속 작업이 실제로
# 그 단계를 돌릴 때 고르는 바로 그 값). 그래서 처음 열린 계획은 "이 그룹이 지금 하기로 되어
# 있는 일"을 그대로 담고, 미지정 칸이 남지 않는다.

def workflow_type_counts(items: Optional[Iterable[dict]]) -> dict[str, int]:
    """워크플로 시퀀스 칸을 작업계획 수량으로 환산한다.

    짝으로 붙어 나오는 레포트 칸(NR/TR/TSR)은 지시 칸과 같은 한 세트다. 시퀀스에 지시
    칸 없이 레포트 칸만 남아 있어도 그 세트는 세어야 하므로 둘 중 큰 쪽을 쓴다.
    """
    direct: dict[str, int] = {}
    paired: dict[str, int] = {}
    result_to_instruction = {
        result: instruction for instruction, result in WORK_PLAN_PAIR_MAP.items()
    }
    for item in items or []:
        code = str((item or {}).get("type") or "").strip().upper()
        if code in WORK_PLAN_TYPE_UNITS:
            direct[code] = direct.get(code, 0) + 1
        elif code in result_to_instruction:
            instruction = result_to_instruction[code]
            paired[instruction] = paired.get(instruction, 0) + 1
    return {
        code: min(max(direct.get(code, 0), paired.get(code, 0)), COUNT_MAX)
        for code in set(direct) | set(paired)
    }


def _effective_chain(project_id: Optional[str]) -> dict:
    """프로젝트의 실행 체인. 읽을 수 없으면 빈 체인 — 계획 생성이 죽으면 안 된다."""
    if not project_id:
        return {}
    try:
        from modules.flow_gate.settings import ai_settings_service

        return ai_settings_service.resolve_effective(project_id) or {}
    except Exception:  # noqa: BLE001 — an unreadable provider list is not a failure here
        return {}


def _registered_providers(project_id: Optional[str]) -> list[dict]:
    """이 프로젝트에 지금 등록돼 있는 공급자들. 읽을 수 없으면 빈 목록."""
    return [
        provider for provider in (_effective_chain(project_id).get("providers") or [])
        if provider.get("id")
    ]


def _registered_provider_ids(project_id: Optional[str]) -> set[str]:
    """0411 T0004 (B0001 "각 프로바이더는 전체 프로바이더에서 바꿀수 있게").

    사람이 손으로 고를 수 있는 범위는 계획에 얼어붙은 provider_candidates 가 아니라
    "후보 ∪ 이 프로젝트에 현재 등록된 공급자"다. provider_candidates 는 원래 역할
    (AI 에게 맡길 범위 + 표시 이름 스냅샷)만 갖는다 — 제거가 아니라 역할 분리다.

    project_id 를 모르거나 설정을 읽지 못하면 빈 집합이 되고, 규칙은 예전(후보만)으로
    좁아진다. 설정을 못 읽었다는 이유로 저장이 열리는 일은 없다.
    """
    return {str(provider["id"]) for provider in _registered_providers(project_id)}


def _assigned_provider(project_id: Optional[str], type_code: str) -> Optional[str]:
    """문서종류별 배정표(0317 D0004)가 이 타입에 정해 둔 공급자. 없으면 None."""
    if not project_id:
        return None
    try:
        from modules.flow_gate.settings import ai_settings_service

        return ai_settings_service.resolve_doctype_provider(project_id, type_code)
    except Exception:  # noqa: BLE001 — degrade to the plan default
        return None


def auto_plan_body(
    project_id: Optional[str] = None,
    workflow_items: Optional[Iterable[dict]] = None,
) -> dict:
    """생성 대화상자를 거치지 않은 작업계획의 첫 본문 — 채워진 채로 열린다.

    수량은 워크플로 시퀀스에서, 공급자 후보·기본값·타입별 배정은 프로젝트 설정에서 읽는다.
    사람이 고른 값이 아니므로 지어내지 않는다: 시퀀스에 없는 타입은 0으로 두어 편집기에서
    올릴 수 있게만 하고, 공급자가 하나도 없는 프로젝트라면 배정 없이 그대로 둔다.
    """
    counts = workflow_type_counts(workflow_items)
    ordered = [entry["code"] for entry in list_countable_types(project_id)]
    quantities = {code: counts.get(code, 0) for code in ordered}

    chain = _effective_chain(project_id)
    providers = [p for p in (chain.get("providers") or []) if p.get("id")]
    provider_ids = [p["id"] for p in providers]
    default_provider_id = chain.get("default_provider_id")
    if default_provider_id not in provider_ids:
        default_provider_id = provider_ids[0] if provider_ids else None

    type_providers: dict[str, str] = {}
    for code in ordered:
        if quantities.get(code, 0) <= 0:
            continue
        assigned = _assigned_provider(project_id, code)
        if assigned in provider_ids:
            type_providers[code] = assigned

    body = initial_body(
        ordered,
        snapshot_candidates(provider_ids, providers),
        project_id,
        quantities=quantities,
        defaults={"provider_id": default_provider_id, "note": ""},
        type_providers=type_providers,
    )
    try:
        return validate(body, project_id=project_id, action="create")
    except WorkPlanValidationError:
        # 계획을 못 만드는 것보다 "아무것도 정해지지 않은 표"가 낫다. 열리지 않는 파일만은
        # 남기지 않는다는 것이 이 함수의 존재 이유다.
        return empty_recovery_body(project_id)


def is_unwritten_plan(raw: Optional[str]) -> bool:
    """이 파일에 계획이 한 번도 쓰인 적 없는가 (빈 파일 / 마크다운 머리말 뼈대).

    되살리기는 여기서만 허용한다. 내용이 있는데 깨진 파일은 사람이 원문으로 확인할 몫이지
    서버가 덮어쓸 대상이 아니다.
    """
    text = (raw or "").strip()
    if not text:
        return True
    if not text.startswith("---"):
        return False
    rest = text[3:]
    closing = rest.find("\n---")
    if closing < 0:
        return False
    return rest[closing + 4:].strip() == ""


# ── Validation (L0010 §2.3) ──────────────────────────────────────────────────

def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _unknown_fields(obj: dict, known: Iterable[str], prefix: str) -> list[dict]:
    known_set = set(known)
    errors = []
    for name in obj:
        if name in known_set or str(name).startswith("x_"):
            continue
        loc = f"{prefix}.{name}" if prefix else str(name)
        errors.append(_error("unknown_field", loc, field=loc))
    return errors


def _note_errors(note: Any, loc: str, key: Optional[str]) -> list[dict]:
    if note is None:
        return []
    if not isinstance(note, str):
        return [_error("type_invalid", loc, key, field=loc)]
    if CONTROL_CHARS.search(note):
        return [_error("note_has_control_char", loc, key)]
    if len(unicodedata.normalize("NFC", note)) > NOTE_MAX_CHARS:
        return [_error("note_too_long", loc, key, max=NOTE_MAX_CHARS)]
    return []


def _check_provider_candidates(candidates: Any) -> list[dict]:
    errors: list[dict] = []
    if not isinstance(candidates, list):
        return [_error("type_invalid", "provider_candidates", field="provider_candidates")]
    if len(candidates) > PROVIDER_CANDIDATES_MAX:
        errors.append(_error("provider_candidates_too_many", "provider_candidates",
                             max=PROVIDER_CANDIDATES_MAX))
    seen: set[str] = set()
    for index, entry in enumerate(candidates):
        loc = f"provider_candidates[{index}]"
        if not isinstance(entry, dict):
            errors.append(_error("type_invalid", loc, field=loc))
            continue
        errors.extend(_unknown_fields(entry, CANDIDATE_FIELD_ORDER, loc))
        provider_id = entry.get("provider_id")
        if not isinstance(provider_id, str) or not PROVIDER_ID_PATTERN.match(provider_id):
            errors.append(_error("provider_id_format_invalid", f"{loc}.provider_id",
                                 value=provider_id))
            continue
        if provider_id in seen:
            errors.append(_error("duplicate_provider_candidate", loc, value=provider_id))
        seen.add(provider_id)
        for name in ("display_name", "group_label"):
            value = entry.get(name)
            if value is not None and not isinstance(value, str):
                errors.append(_error("type_invalid", f"{loc}.{name}", field=f"{loc}.{name}"))
    return errors


def _candidate_ids(candidates: Any) -> set[str]:
    if not isinstance(candidates, list):
        return set()
    return {
        entry.get("provider_id")
        for entry in candidates
        if isinstance(entry, dict) and isinstance(entry.get("provider_id"), str)
    }


def _check_defaults(
    defaults: Any,
    candidates: Any,
    registered_ids: Optional[set[str]] = None,
    enforce_scope: bool = True,
) -> list[dict]:
    if not isinstance(defaults, dict):
        return [_error("type_invalid", "defaults", field="defaults")]
    errors = _unknown_fields(defaults, DEFAULTS_FIELD_ORDER, "defaults")
    provider_id = defaults.get("provider_id")
    if provider_id is not None:
        # 0411 T0004: 후보이거나 지금 등록된 공급자면 된다 (NR0003 §6 안 B).
        allowed_ids = _candidate_ids(candidates) | (registered_ids or set())
        if not isinstance(provider_id, str) or not PROVIDER_ID_PATTERN.match(provider_id):
            errors.append(_error("provider_id_format_invalid", "defaults.provider_id",
                                 value=provider_id))
        elif enforce_scope and provider_id not in allowed_ids:
            errors.append(_error("provider_not_candidate", "defaults.provider_id",
                                 value=provider_id))
    note = defaults.get("note", "")
    errors.extend(_note_errors(note, "defaults.note", None))
    return errors


def _check_step_shape(step: Any, loc: str) -> list[dict]:
    if not isinstance(step, dict):
        return [_error("type_invalid", loc, field=loc)]
    errors = _unknown_fields(step, STEP_FIELD_ORDER, loc)
    key = step.get("key")
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        errors.append(_error("key_format_invalid", f"{loc}.key", value=key))
        key = None
    for field in STEP_FIELD_ORDER:
        if field not in step:
            errors.append(_error("missing_field", f"{loc}.{field}", key, field=f"{loc}.{field}"))
    type_code = step.get("type")
    if type_code not in WORK_PLAN_STEP_TYPES:
        errors.append(_error("enum_not_allowed", f"{loc}.type", key, field=f"{loc}.type"))
    if not _is_int(step.get("ordinal")):
        errors.append(_error("type_invalid", f"{loc}.ordinal", key, field=f"{loc}.ordinal"))
    pair_key = step.get("pair_key")
    if pair_key is not None and (not isinstance(pair_key, str) or not KEY_PATTERN.match(pair_key)):
        errors.append(_error("key_format_invalid", f"{loc}.pair_key", key, value=pair_key))
    if step.get("pair_role") not in PAIR_ROLES:
        errors.append(_error("enum_not_allowed", f"{loc}.pair_role", key, field=f"{loc}.pair_role"))
    if step.get("origin") not in ORIGINS:
        errors.append(_error("enum_not_allowed", f"{loc}.origin", key, field=f"{loc}.origin"))
    if not isinstance(step.get("locked"), bool):
        errors.append(_error("type_invalid", f"{loc}.locked", key, field=f"{loc}.locked"))
    locked_reason = step.get("locked_reason")
    if locked_reason is not None and locked_reason != LOCKED_REASON_SERVER_ASSEMBLED:
        errors.append(_error("enum_not_allowed", f"{loc}.locked_reason", key,
                             field=f"{loc}.locked_reason"))
    provider_id = step.get("provider_id")
    if provider_id is not None and (
        not isinstance(provider_id, str) or not PROVIDER_ID_PATTERN.match(provider_id)
    ):
        errors.append(_error("provider_id_format_invalid", f"{loc}.provider_id", key,
                             value=provider_id))
    display = step.get("provider_display_name")
    if display is not None and not isinstance(display, str):
        errors.append(_error("type_invalid", f"{loc}.provider_display_name", key,
                             field=f"{loc}.provider_display_name"))
    errors.extend(_note_errors(step.get("note"), f"{loc}.note", key))
    return errors


def validate(
    body: Any,
    *,
    project_id: Optional[str] = None,
    action: str = "save",
    enforce_provider_scope: bool = True,
) -> dict:
    """Run every layer of L0010 §2.3 and return the canonical body.

    Raises WorkPlanValidationError carrying all errors of the first failing layer.
    Layers are not merged on purpose (L0010 §2.3 결정 5): reporting "the count is out
    of range" together with "the steps do not match the counts" would show two places
    to fix where there is only one.

    0411 T0004: enforce_provider_scope=False 는 *이미 디스크에 있는* 계획을 읽을 때만
    쓴다(load_body). 쓰는 시점에는 검증을 통과했는데 그 뒤 공급자가 프로젝트에서
    삭제됐다는 이유로 계획 파일이 통째로 안 열리면 안 된다 — 사라진 공급자는 편집기가
    회색 이름(unavailable_provider)으로 이미 다루고 있다. 서식 검사는 그대로 돈다.
    """
    def fail(errors: list[dict]):
        raise WorkPlanValidationError(errors, action=action)

    # ── layer 1: can it be read at all ──────────────────────────────────────
    if not isinstance(body, dict):
        fail([_error("json_parse_failed", "")])
    version = body.get("wp_version")
    if not _is_int(version):
        fail([_error("wp_version_invalid", "wp_version")])
    if version > WP_VERSION_SUPPORTED:
        fail([_error("wp_version_unsupported", "wp_version", value=version)])
    # A lower version is read as the current one (P0009 §2.7). Only version 1 exists,
    # so the upgrade is the identity — the branch stays so version 2 has a home.

    # ── layer 2: top-level fields ───────────────────────────────────────────
    errors: list[dict] = []
    expected_types = {
        "binding": str,
        "counted_types": list,
        "quantities": dict,
        "provider_candidates": list,
        "defaults": dict,
        "steps": list,
    }
    for field, expected in expected_types.items():
        if field not in body:
            errors.append(_error("missing_field", field, field=field))
        elif not isinstance(body[field], expected):
            errors.append(_error("type_invalid", field, field=field))
    errors.extend(_unknown_fields(body, TOP_LEVEL_ORDER, ""))
    if body.get("binding") != BINDING_ADVISORY:
        errors.append(_error("binding_not_allowed", "binding"))
    if errors:
        fail(errors)

    counted_types = body["counted_types"]
    quantities = body["quantities"]

    # ── layer 3: the quantity boxes ─────────────────────────────────────────
    if not counted_types:
        errors.append(_error("empty_selection", "counted_types", what_key="counted_types"))
    known_codes = set(WORK_PLAN_TYPE_UNITS)
    seen_codes: set[str] = set()
    for index, code in enumerate(counted_types):
        if not isinstance(code, str) or code not in known_codes:
            errors.append(_error("unknown_type_code", f"counted_types[{index}]", code=code))
            continue
        if code in seen_codes:
            errors.append(_error("duplicate_type", f"counted_types[{index}]", code=code))
        seen_codes.add(code)
    if set(quantities) != {c for c in counted_types if isinstance(c, str)}:
        errors.append(_error("quantities_key_mismatch", "quantities"))
    for code, quantity in quantities.items():
        loc = f"quantities.{code}"
        if not isinstance(quantity, dict):
            errors.append(_error("type_invalid", loc, field=loc))
            continue
        errors.extend(_unknown_fields(quantity, ("unit", "count"), loc))
        expected_unit = WORK_PLAN_TYPE_UNITS.get(code)
        if expected_unit and quantity.get("unit") != expected_unit:
            errors.append(_error("unit_mismatch", f"{loc}.unit", code=code, expected=expected_unit))
        count = quantity.get("count")
        if not _is_int(count):
            errors.append(_error("count_not_integer", f"{loc}.count"))
        elif count < COUNT_MIN or count > COUNT_MAX:
            errors.append(_error("count_out_of_range", f"{loc}.count",
                                 min=COUNT_MIN, max=COUNT_MAX))
    if errors:
        fail(errors)

    # ── layer 4: providers ──────────────────────────────────────────────────
    # 0411 T0004: 등록 목록은 이 검증 한 번에 한 번만 읽는다. 후보 목록 자체는 여기서
    # 넓히지 않는다 — 후보는 "AI 에게 맡길 범위", 등록 목록은 "사람이 고를 수 있는 범위".
    registered_ids = _registered_provider_ids(project_id) if enforce_provider_scope else set()
    errors.extend(_check_provider_candidates(body["provider_candidates"]))
    errors.extend(_check_defaults(
        body["defaults"], body["provider_candidates"], registered_ids, enforce_provider_scope,
    ))
    if errors:
        fail(errors)

    # ── layer 5: one step at a time ─────────────────────────────────────────
    steps = body["steps"]
    for index, step in enumerate(steps):
        errors.extend(_check_step_shape(step, f"steps[{index}]"))
    seen_keys: set[str] = set()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        key = step.get("key")
        if not isinstance(key, str):
            continue
        if key in seen_keys:
            errors.append(_error("duplicate_key", f"steps[{index}].key", key, value=key))
        seen_keys.add(key)
    if errors:
        fail(errors)

    # ── layer 6: do the steps match the counts ──────────────────────────────
    expected_steps = expand_steps(counted_types, quantities, project_id)
    if [s["key"] for s in steps] != [s["key"] for s in expected_steps]:
        errors.append(_error("steps_quantity_mismatch", "steps"))
    if len(steps) > STEPS_MAX:
        errors.append(_error("steps_too_many", "steps", max=STEPS_MAX))
    if errors:
        fail(errors)

    # ── layer 7: what the values mean ───────────────────────────────────────
    # 0411 T0004 (B0001): 비잠금 단계의 공급자는 후보이거나 지금 등록된 것이면 된다.
    selectable_ids = _candidate_ids(body["provider_candidates"]) | registered_ids
    for index, step in enumerate(steps):
        loc = f"steps[{index}]"
        expected = expected_steps[index]
        key = step.get("key")
        if (
            step.get("type") != expected["type"]
            or step.get("ordinal") != expected["ordinal"]
            or step.get("pair_key") != expected["pair_key"]
            or step.get("pair_role") != expected["pair_role"]
        ):
            errors.append(_error("step_shape_mismatch", loc, key))
        if (
            step.get("locked") != expected["locked"]
            or step.get("locked_reason") != expected["locked_reason"]
        ):
            errors.append(_error("locked_flag_mismatch", f"{loc}.locked", key))
        if expected["locked"]:
            if step.get("provider_id") is not None:
                errors.append(_error("provider_not_allowed", f"{loc}.provider_id", key))
            if (step.get("note") or "").strip():
                errors.append(_error("note_not_allowed", f"{loc}.note", key))
            if step.get("origin") != "system":
                errors.append(_error("origin_not_allowed", f"{loc}.origin", key))
        elif step.get("provider_id") is not None:
            if enforce_provider_scope and step["provider_id"] not in selectable_ids:
                errors.append(_error("provider_not_candidate", f"{loc}.provider_id", key,
                                     value=step["provider_id"]))
    if errors:
        fail(errors)

    return canonicalize(body)


# ── Canonical form (P0009 §2.6 결정 3·4) ─────────────────────────────────────

def canonicalize(body: dict) -> dict:
    """Fixed key order, `x_` extras preserved at the end of their own object."""
    out: dict[str, Any] = {}
    out["wp_version"] = WP_VERSION_SUPPORTED
    out["binding"] = BINDING_ADVISORY
    out["counted_types"] = list(body.get("counted_types") or [])
    quantities = body.get("quantities") or {}
    out["quantities"] = {
        code: {
            "unit": quantities[code].get("unit"),
            "count": quantities[code].get("count"),
            **{k: v for k, v in quantities[code].items() if str(k).startswith("x_")},
        }
        for code in out["counted_types"]
        if code in quantities
    }
    out["provider_candidates"] = [
        {
            "provider_id": entry.get("provider_id"),
            "display_name": entry.get("display_name"),
            "group_label": entry.get("group_label"),
            **{k: v for k, v in entry.items() if str(k).startswith("x_")},
        }
        for entry in (body.get("provider_candidates") or [])
    ]
    defaults = body.get("defaults") or {}
    out["defaults"] = {
        "provider_id": defaults.get("provider_id"),
        "note": defaults.get("note", ""),
        **{k: v for k, v in defaults.items() if str(k).startswith("x_")},
    }
    out["steps"] = [
        {
            **{field: step.get(field) for field in STEP_FIELD_ORDER},
            **{k: v for k, v in step.items() if str(k).startswith("x_")},
        }
        for step in (body.get("steps") or [])
    ]
    for name, value in body.items():
        if str(name).startswith("x_"):
            out[name] = value
    return out


def dumps(body: dict) -> str:
    """UTF-8 JSON, 2-space indent, fixed key order, one trailing newline."""
    return json.dumps(body, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


# ── Where a plan's canonical file lives ──────────────────────────────────────

def canonical_path_for_doc(doc: dict):
    """문서 레코드가 가리키는 곳이 아니라, 이 문서의 정본이 있어야 할 자리."""
    from modules.flow_gate.storage import paths as storage_paths

    group_id = doc.get("group_id") or ""
    doc_id = doc.get("doc_id") or ""
    doc_code = doc_id[len(group_id) + 1:] if doc_id.startswith(group_id + ".") else doc_id
    return storage_paths.document_path(
        project_id=doc.get("project_id"),
        group_code=group_id,
        doc_code=doc_code,
        filename=DOCUMENT_FILENAME,
        module=doc.get("module") or "none",
        branch=(doc.get("branch") or "main") or "main",
    )


def plan_path_for_doc(doc: dict):
    """작업계획 문서 하나가 읽고 쓰는 정본 JSON 파일의 경로.

    0403 NR0004 F3: 계획을 읽는 곳(문서 라우트)과 적용 이력을 남기는 곳(시퀀스 저장)이
    각자 경로를 계산하면 언젠가 서로 다른 파일을 가리킨다. 계산은 여기 한 번만 한다.
    """
    from modules.flow_gate.storage import paths as storage_paths

    stored = (doc.get("file_path") or "").strip()
    branch = (doc.get("branch") or "main").strip() or "main"
    if stored:
        resolved = storage_paths.resolve_storage_path(stored, doc.get("project_id"), branch=branch)
        if resolved is not None:
            return resolved
        # 파일이 아직 없을 수도 있다(되돌려진 트리). "작업계획이 아니다"라고 답하는 대신
        # 정본이 있어야 할 자리로 떨어진다.
        loose = storage_paths.resolve_storage_dir(stored, doc.get("project_id"))
        if loose is not None:
            return loose
    return canonical_path_for_doc(doc)


# ── Reading a stored plan (P0009 §4.4 / §4.5) ────────────────────────────────

def load_body(path) -> dict:
    """Read + validate a canonical file. Raises WorkPlanUnreadable, never a 500."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise WorkPlanUnreadable("file_missing", str(exc)) from exc
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WorkPlanUnreadable("not_json", str(exc), raw=raw) from exc
    if not isinstance(parsed, dict):
        raise WorkPlanUnreadable("not_json", "top level is not an object", raw=raw)
    version = parsed.get("wp_version")
    if _is_int(version) and version > WP_VERSION_SUPPORTED:
        raise WorkPlanUnreadable(
            "wp_version_unsupported", f"wp_version={version}", raw=raw
        )
    try:
        # 0411 T0004: 읽기는 공급자 범위를 다시 묻지 않는다. 저장 때 이미 물었고, 그 뒤
        # 공급자가 삭제됐다고 계획이 안 열리면 "사라진 공급자" 표기를 볼 방법이 없어진다.
        return validate(parsed, enforce_provider_scope=False)
    except WorkPlanValidationError as exc:
        first = render_errors(exc.errors, FALLBACK_LOCALE)[0]
        raise WorkPlanUnreadable(
            "schema_invalid", f"{first['loc']}: {first['msg']}", raw=raw
        ) from exc


def write_body_atomically(path, body: dict) -> None:
    """Write through a temp file in the same directory, then replace.

    A half-written canonical file would make the document unopenable, and the
    review pipeline reads this very file to decide whether the document has a body.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(dumps(body))
        os.replace(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ── Derived views (P0009 §4.4: computed on read, never stored) ───────────────

def totals(body: dict) -> dict:
    quantities = body.get("quantities") or {}
    design_sheets = sum(
        q.get("count") or 0
        for code, q in quantities.items()
        if WORK_PLAN_TYPE_UNITS.get(code) == "sheet"
    )
    work_sets = sum(
        q.get("count") or 0
        for code, q in quantities.items()
        if WORK_PLAN_TYPE_UNITS.get(code) == "set"
    )
    return {
        "design_sheets": design_sheets,
        "work_sets": work_sets,
        "steps": len(body.get("steps") or []),
    }


def unassigned_step_count(body: dict) -> int:
    return sum(
        1
        for step in (body.get("steps") or [])
        if not step.get("locked") and not step.get("provider_id")
    )


def assignment_summary(body: dict, providers: Optional[list[dict]] = None) -> list[dict]:
    """Per-provider step counts, named with the CURRENT display name when known.

    0411 T0004: 후보 밖(등록만 된) 공급자가 단계에 배정될 수 있게 됐다. 이름 해석 순서가
    등록 목록 → 후보 스냅샷 → id 이므로 그런 공급자도 raw id 가 아니라 현재 이름으로
    세어진다 — 이 함수는 원래부터 후보 목록을 순회하지 않고 steps 를 센다.
    """
    current = {p["id"]: p.get("name") for p in (providers or [])}
    snapshot = {
        entry.get("provider_id"): entry.get("display_name")
        for entry in (body.get("provider_candidates") or [])
    }
    counts: dict[str, int] = {}
    order: list[str] = []
    for step in body.get("steps") or []:
        provider_id = step.get("provider_id")
        if not provider_id:
            continue
        if provider_id not in counts:
            order.append(provider_id)
        counts[provider_id] = counts.get(provider_id, 0) + 1
    return [
        {
            "provider_id": provider_id,
            "display_name": current.get(provider_id) or snapshot.get(provider_id) or provider_id,
            "step_count": counts[provider_id],
        }
        for provider_id in order
    ]


def _used_provider_ids(body: dict) -> list[str]:
    """본문이 실제로 쓰고 있는 공급자 id — 기본값 다음 단계 순서, 중복 없이."""
    used: list[str] = []
    seen: set[str] = set()
    candidates = [(body.get("defaults") or {}).get("provider_id")]
    candidates += [step.get("provider_id") for step in (body.get("steps") or [])]
    for provider_id in candidates:
        if not isinstance(provider_id, str) or not provider_id or provider_id in seen:
            continue
        seen.add(provider_id)
        used.append(provider_id)
    return used


def provider_status(body: dict, providers: Optional[list[dict]] = None) -> list[dict]:
    """P0009 §4.4: is each candidate still registered, and did its name change.

    0411 T0004 (B0001): 단계 공급자는 이제 후보 밖 — 이 프로젝트에 등록만 된 공급자 — 일
    수 있다. 편집기는 이 목록에 물어 "지금도 등록돼 있는가"를 판정하므로, 본문이 실제로
    쓰고 있는 id 는 후보가 아니어도 한 줄을 갖는다. 없으면 멀쩡히 등록된 공급자에
    "(사용할 수 없는 프로바이더)" 딱지가 붙는다. 후보가 아닌 줄에는 스냅샷 이름이 없다
    (얼려 둔 적이 없다) — 그래서 name_changed 는 언제나 거짓이다.
    """
    current = {p["id"]: p.get("name") for p in (providers or [])}

    def row(provider_id: Any, snapshot_name: Any) -> dict:
        registered = provider_id in current
        current_name = current.get(provider_id)
        return {
            "provider_id": provider_id,
            "registered": registered,
            "current_name": current_name,
            "snapshot_name": snapshot_name,
            "name_changed": bool(registered and snapshot_name and current_name != snapshot_name),
        }

    status = []
    seen: set[Any] = set()
    for entry in body.get("provider_candidates") or []:
        provider_id = entry.get("provider_id")
        seen.add(provider_id)
        status.append(row(provider_id, entry.get("display_name")))
    for provider_id in _used_provider_ids(body):
        if provider_id in seen:
            continue
        seen.add(provider_id)
        status.append(row(provider_id, None))
    return status


def provider_group_label(provider: dict) -> str:
    """"Claude · CLI" from the effective-settings row (kind + exec_type)."""
    kind = str(provider.get("kind") or "").strip()
    exec_type = str(provider.get("exec_type") or "").strip()
    if kind and exec_type:
        return f"{kind.capitalize()} · {exec_type.upper()}"
    return kind.capitalize() or exec_type.upper() or ""


def snapshot_candidates(provider_ids: list[str], providers: list[dict]) -> list[dict]:
    """Freeze display names at the moment of choosing (DS0006 §7)."""
    by_id = {p["id"]: p for p in providers}
    snapshots = []
    for provider_id in provider_ids:
        provider = by_id.get(provider_id)
        snapshots.append({
            "provider_id": provider_id,
            "display_name": (provider or {}).get("name"),
            "group_label": provider_group_label(provider or {}),
        })
    return snapshots


# ── Change summary (P0009 §5.1 결정 10 / §5.4 결정 11) ────────────────────────

def _quantities_line(body: dict) -> str:
    parts = []
    for code in body.get("counted_types") or []:
        quantity = (body.get("quantities") or {}).get(code) or {}
        count = quantity.get("count")
        if WORK_PLAN_TYPE_UNITS.get(code) == "set":
            parts.append(f"{code} {count}세트")
        else:
            parts.append(f"{code} {count}")
    return " · ".join(parts)


def change_summary(after: dict, before: Optional[dict] = None) -> dict:
    """The work-plan flavour of the inbox change summary.

    A prose document is summarized by "how many lines landed in which section"; a
    work plan has neither. What an unattended worker needs to know is whether the
    counts and assignments it sent are the ones now stored — and, on an edit,
    whether it overwrote someone else's values.
    """
    steps = after.get("steps") or []
    # P0009 §5.1 · §5.4 의 두 예시 모두 assigned_steps + unassigned_steps == steps 다.
    # "미배정"은 사람이 아직 고르지 않은 칸만 세고(잠긴 TSR 은 고를 수 없으니
    # 미배정이 아니다), "배정됨"은 그 나머지다. 잠긴 칸을 어느 쪽에도 넣지
    # 않으면 두 수의 합이 단계 수보다 작아져, 무인 작업자가 자기가 보낸 것이
    # 다 저장됐는지를 셈으로 확인할 수 없다.
    unassigned = unassigned_step_count(after)
    summary: dict[str, Any] = {
        "kind": "work_plan",
        "quantities": _quantities_line(after),
        "steps": len(steps),
        "assigned_steps": len(steps) - unassigned,
        "unassigned_steps": unassigned,
    }
    if before is None:
        summary["sections"] = ["quantities", "provider_candidates", "defaults", "steps"]
        return summary

    changed: list[str] = []
    before_q = before.get("quantities") or {}
    after_q = after.get("quantities") or {}
    for code in sorted(set(before_q) | set(after_q)):
        old = (before_q.get(code) or {}).get("count")
        new = (after_q.get(code) or {}).get("count")
        if old != new:
            changed.append(f"quantities.{code}.count {old} → {new}")
    if len(before.get("steps") or []) != len(steps):
        changed.append(f"steps {len(before.get('steps') or [])} → {len(steps)}")
    before_steps = {s.get("key"): s for s in (before.get("steps") or [])}
    for step in steps:
        old = before_steps.get(step.get("key"))
        if old is None:
            continue
        if old.get("provider_id") != step.get("provider_id"):
            changed.append(
                f"steps[{step.get('key')}].provider_id "
                f"{old.get('provider_id')} → {step.get('provider_id')}"
            )
        if (old.get("note") or "") != (step.get("note") or ""):
            changed.append(f"steps[{step.get('key')}].note 변경")
    summary["changed"] = changed
    return summary


# ── Template served to AI workers (P0009 §6) ─────────────────────────────────

def template_body(project_id: Optional[str] = None) -> str:
    """A minimal valid body with the project's real provider candidates."""
    providers = [
        provider for provider in (_effective_chain(project_id).get("providers") or [])
        if provider.get("id")
    ]
    candidates = snapshot_candidates([provider["id"] for provider in providers], providers)
    body = initial_body(list(WORK_PLAN_COUNTABLE_ORDER), candidates, project_id)
    return dumps(body)


TEMPLATE_RULES = {
    "ko": [
        "본문은 Markdown 이 아니라 UTF-8 JSON 입니다.",
        "제목과 연결 대상은 인박스 요청의 title / prev_doc_id 를 쓰고 본문에 적지 않습니다.",
        "steps 의 key 는 <타입코드>#<회차> 서식이며 문서 안에서 유일해야 합니다.",
        "steps 는 quantities 에서 펼쳐지는 목록과 순서까지 같아야 합니다.",
        "steps[].note 는 그 단계를 맡을 AI에게 줄 한 줄 지시이며 TSR 외 모든 단계에 200자 이내로 채웁니다. 줄바꿈과 탭은 쓰지 않습니다.",
        "defaults.note 는 모든 단계에 공통으로 붙일 한 줄이며, 각 단계의 steps[].note 와는 역할이 다릅니다. 요청 멘트의 '작업계획 맡길 범위' 절에 '전달 멘트'가 있으면 그것을 입력 삼아 이 한 줄을 새로 작성합니다. 없으면 범위와 참조 문서만으로 작성합니다.",
        "steps[].provider_id 는 provider_candidates 안의 값이거나 이 프로젝트에 등록된 공급자여야 하며, 고를 것이 없으면 비워 둡니다.",
        "defaults.provider_id 는 provider_candidates 안의 값이거나 null 입니다. 요청 멘트의 '실행 프로바이더'는 그 멘트를 실행 중인 공급자일 뿐이므로 여기에 옮겨 적지 않습니다.",
        "TSR 단계에는 공급자와 멘트를 적지 않습니다.",
        "item_seq(실제 단계 번호)는 적지 않습니다.",
        "binding 은 항상 advisory 입니다.",
    ],
    "en": [
        "The body is UTF-8 JSON, not Markdown.",
        "Title and parent come from the inbox request (title / prev_doc_id); never write them in the body.",
        "Each steps[].key is <TYPE>#<ordinal> and must be unique in the document.",
        "steps must equal the list expanded from quantities, in the same order.",
        "steps[].note is a one-line instruction for the AI assigned to that step; fill it for every non-TSR step, within 200 characters and without newlines or tabs.",
        "defaults.note is the one-line instruction shared by every step, a different role from each step's steps[].note; when the request mention carries a Delivery note in its work-plan scope section, use that note as input and author this shared line yourself. With no Delivery note, author it from the scope and reference documents alone.",
        "steps[].provider_id must be one of provider_candidates or a provider registered in this project; leave it empty when there is nothing to choose.",
        "defaults.provider_id is one of provider_candidates or null. The mention's Execution provider is merely the provider running that mention, so never copy it here.",
        "A TSR step carries no provider and no note.",
        "Never write item_seq (the real workflow step number).",
        "binding is always advisory.",
    ],
    "ja": [
        "本文は Markdown ではなく UTF-8 の JSON です。",
        "タイトルと連結対象はインボックス要求の title / prev_doc_id を使い、本文には書きません。",
        "steps の key は <タイプコード>#<回次> の書式で、文書内で一意でなければなりません。",
        "steps は quantities から展開されるリストと順序まで一致していなければなりません。",
        "steps[].note はその段階を担当するAIへの一行指示です。TSR以外の全段階に200文字以内、改行・タブなしで記入します。",
        "defaults.note は全段階に共通する一行指示で、各段階の steps[].note とは役割が違います。要求メモの「作業計画を任せる範囲」節に「伝達メモ」があれば、それを入力として一行を新しく書きます。無ければ範囲と参照文書だけで書きます。",
        "steps[].provider_id は provider_candidates 内の値、またはこのプロジェクトに登録された提供者でなければならず、選べるものが無ければ空欄にします。",
        "defaults.provider_id は provider_candidates 内の値または null です。要求メモの「実行プロバイダー」はそのメモを実行中の提供者にすぎないため、ここに書き写しません。",
        "TSR 段階には提供者と一行メモを書きません。",
        "item_seq(実際の段階番号)は書きません。",
        "binding は常に advisory です。",
    ],
}

TEMPLATE_HEADING = {
    "ko": "## 다음 문서 템플릿 (WP / ko)",
    "en": "## Next document template (WP / en)",
    "ja": "## 次の文書テンプレート (WP / ja)",
}


def template_payload(locale: str, project_id: Optional[str] = None) -> dict:
    locale = normalize_locale(locale)
    return {
        "type_code": WORK_PLAN_TYPE,
        "requested_locale": locale,
        "resolved_locale": locale,
        "body_format": "json",
        "heading": TEMPLATE_HEADING.get(locale, TEMPLATE_HEADING[FALLBACK_LOCALE]),
        "body": template_body(project_id),
        "rules": TEMPLATE_RULES.get(locale, TEMPLATE_RULES[FALLBACK_LOCALE]),
    }

def request_work_plan_fill(
    doc_id: str,
    issued_to: str,
    api_base_url: str,
    scope: dict,
    locale: str = "ko",
    ai_run_id: Optional[str] = None,
) -> dict:
    """Issue an edit token and bounded canonical-JSON prompt for a WP document."""
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services import mention_service, token_service
    from modules.flow_gate.storage import paths as storage_paths

    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")
    if str(doc.get("type_code") or "").upper() != WORK_PLAN_TYPE:
        raise ValueError(f"work_plan_required:{doc_id}")
    group_id = doc.get("group_id")
    if not group_id:
        raise ValueError(f"group_not_found:{doc_id}")
    stored = str(doc.get("file_path") or "").strip()
    path = storage_paths.resolve_storage_path(
        stored, doc.get("project_id"), branch=(doc.get("branch") or "main")
    )
    if path is None:
        raise ValueError(f"work_plan_body_not_found:{doc_id}")
    body = load_body(path)
    issued = token_service.issue(
        project=doc.get("project_id") or "",
        group_id=group_id,
        action_scope="edit",
        doc_ref=doc_id,
        issued_to=issued_to,
        ai_run_id=ai_run_id,
    )
    mention = mention_service.build_work_plan_fill_mention(
        token_rec={
            "project": doc.get("project_id") or "",
            "group_id": group_id,
            "scratch_dir": issued["scratch_dir"],
        },
        target_doc=doc,
        body=body,
        scope=scope,
        api_base_url=api_base_url,
        raw_token=issued["raw_token"],
        locale=locale,
    )
    return {
        "raw_token": issued["raw_token"],
        "token_id": issued["token_id"],
        "expires_at": issued["expires_at"],
        "scratch_dir": issued["scratch_dir"],
        "mention": mention,
    }
