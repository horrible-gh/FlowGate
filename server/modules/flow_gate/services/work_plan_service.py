"""Work plan (WP) domain service — flowgate.default.0395.

One validator, one writer, two callers. The human API (documents/routers/work_plan.py)
and the AI inbox branch (api/inbox_routes.py) both come through this module, because
D0007 §2.2 makes that the whole point of the design: "the validator and the save service are
the only channel for both the human and the AI path". Two validators would drift within a release.

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
# 0406 T0022 item 6: documents.constants is the sole source of truth for the one-line note
# cap. A separate 200 written here grew into three copies with the sequence's and the screen's.
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

# locked_reason has exactly two legal values: unset, or the one reason the server ever
# assigns. A tuple (not a set) so step_contract() can publish it in a stable order.
LOCKED_REASON_VALUES = (None, LOCKED_REASON_SERVER_ASSEMBLED)


def step_contract() -> dict:
    """The canonical steps[] schema, as data (T0004 §15/§16).

    validate() and the design_template/WP help item must never carry two separately
    typed-out copies of the same field list / enums / pair map — that drift is exactly
    what NR0003 reported. Both now read this function instead of restating the values.
    """
    return {
        "step_fields": list(STEP_FIELD_ORDER),
        "pair_roles": list(PAIR_ROLES),
        "origins": list(ORIGINS),
        "locked_reason_values": list(LOCKED_REASON_VALUES),
        "pair_map": dict(WORK_PLAN_PAIR_MAP),
        "single_types": list(WORK_PLAN_SHEET_TYPES),
        "locked_types": sorted(WORK_PLAN_LOCKED_TYPES),
    }


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
    # 0411 T0004: the code name stays (screens and tests pin this code) but the wording states
    # the new rule — a candidate, or any provider registered in this project, may be chosen.
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
    "provider_display_name_without_provider_id": {
        "ko": "provider_id 가 비어 있으면 provider_display_name 도 null 이어야 합니다.",
        "en": "provider_display_name must be null whenever provider_id is null.",
        "ja": "provider_id が空なら provider_display_name も null でなければなりません。",
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
    """"Nothing was chosen at all" for a create request that skipped the dialog (P0009 §4.3)."""
    return _error("empty_selection", field, what_key=field)


def render_errors(errors: list[dict], locale: str) -> list[dict]:
    """Attach the localized ``msg`` and drop the internal ``params`` carrier."""
    locale = normalize_locale(locale)
    rendered = []
    for err in errors[:ERRORS_REPORTED_MAX]:
        template = _copy(_ERROR_COPY, locale, err["code"])
        params = dict(err.get("params") or {})
        # The "please tick one" phrasing needs a localized noun, so the error carries the noun's key
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


# ── Type registry (L0010 §2.1 decision 2: order comes from the table) ────────

def _best_rows_by_code(rows: Iterable[dict]) -> dict[str, dict]:
    """One row per countable code within a single project/global scope.

    'L' exists twice (design "logic" / general "log"). Only the design one is a
    countable design sheet; the series filter below drops the log row, and this guard
    also covers a future duplicate design/instruction row sharing a code.
    """
    best: dict[str, dict] = {}
    for row in rows:
        code = str(row.get("type_code") or "").upper()
        if code not in WORK_PLAN_TYPE_UNITS:
            continue
        series = str(row.get("series") or "")
        if series not in ("design", "instruction"):
            continue
        if code in best and best[code].get("series") == "design":
            continue
        best[code] = row
    return best


def list_countable_types(project_id: Optional[str] = None, locale: str = "ko") -> list[dict]:
    """Countable types in the order a work plan lists them.

    The set of countable codes and their units are facts about the workflow and live
    in documents.constants; the ORDER is read from document_types so that adding a
    design type does not require editing this file (L0010 §2.1 decision 2).

    Ordering rule: sheets before sets; within sheets the design *instruction* (DS)
    leads the design series; within each group, the table's own sort_order decides.
    That reproduces DS · D · P · L · DB · N · T · TS from the current table without
    naming any of them here.

    0429 T0004: project_id is no longer discarded. A project row overrides the global
    row for the same code (display name, sort_order, active flag) — including turning it
    off: an inactive project override drops the code from the registry rather than
    falling back to the still-active global row. A code that is merely missing or
    inactive after a successful DB read stays out of the registry; the
    WORK_PLAN_COUNTABLE_ORDER fallback below only fires when the DB read itself failed,
    so a plan can still be opened and read.
    """
    db_ok = True
    try:
        rows = db_templates.list_document_types(project_id=project_id, locale=locale)
    except Exception:  # noqa: BLE001 — a registry outage must not crash the reader
        rows = []
        db_ok = False

    global_best = _best_rows_by_code(row for row in rows if row.get("project_id") is None)
    project_best = (
        _best_rows_by_code(row for row in rows if row.get("project_id") is not None)
        if project_id else {}
    )

    by_code: dict[str, dict] = {}
    for code in set(global_best) | set(project_best):
        row = project_best.get(code) or global_best[code]
        if not row.get("is_active", 1):
            continue
        by_code[code] = {
            "code": code,
            "name": row.get("type_name") or code,
            "series": str(row.get("series") or ""),
            "sort_order": row.get("sort_order") or 0,
        }

    entries = list(by_code.values())
    if not db_ok:
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
            item["pair_name"] = _type_name(rows, pair, locale, project_id) or pair
        result.append(item)
    return result


def _type_name(
    rows: Iterable[dict], code: str, locale: str, project_id: Optional[str] = None,
) -> Optional[str]:
    project_match: Optional[str] = None
    global_match: Optional[str] = None
    for row in rows:
        if str(row.get("type_code") or "").upper() != code:
            continue
        if project_id and row.get("project_id") == project_id:
            project_match = row.get("type_name")
        elif row.get("project_id") is None:
            global_match = row.get("type_name")
    return project_match or global_match


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
            # P0009 §4.1 / §10: the create dialog shows the pair's name too ("3 sets of work
            # instructions, work reports included"). It reuses names from the same response, so
            # the locale matches automatically, and when the pair type is absent from the list no
            # entry is built — better absent than showing a type code dressed up as a name.
            pair_name = names.get(pair)
            if pair_name:
                item["pair_name"] = pair_name
        annotated.append(item)
    return annotated


def _order_codes(project_id: Optional[str] = None) -> list[str]:
    return [entry["code"] for entry in list_countable_types(project_id)]


def type_order(counted_types: Iterable[str], project_id: Optional[str] = None) -> list[str]:
    """L0010 §2.1 type_order: registry order first, then anything else by code.

    0429 T0004: a stored plan can carry a code the active registry no longer lists (a
    since-deactivated project override, say). That code still needs a deterministic
    slot so read/save order never wobbles — WORK_PLAN_COUNTABLE_ORDER is the fallback
    rank, the same table list_countable_types falls back to on a DB outage.
    """
    wanted = list(counted_types)
    ordered = [code for code in _order_codes(project_id) if code in wanted]
    remainder = sorted(
        set(wanted) - set(ordered),
        key=lambda code: (
            WORK_PLAN_COUNTABLE_ORDER.index(code) if code in WORK_PLAN_COUNTABLE_ORDER
            else len(WORK_PLAN_COUNTABLE_ORDER),
            code,
        ),
    )
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
    """Build a creation-time plan. Types absent from ``quantities`` fall back to count 0
    (flowgate.default.0423 T0005 item 5) — never guessed as 1. Callers that have a
    workflow_type_counts-derived value should merge it into ``quantities`` themselves
    before calling this, so an explicit request value still wins over the derivation.
    """
    ordered = type_order(counted_types, project_id)
    requested_quantities = quantities or {}
    canonical_quantities = {
        code: {
            "unit": WORK_PLAN_TYPE_UNITS.get(code, "sheet"),
            "count": requested_quantities.get(code, 0),
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


# ── Plans created outside the create dialog (0395 T0026 rework) ──────────────
#
# The create dialog is not the only way a work-plan document appears. When the workflow head
# slot is WP, "make an empty document" creates one too, and that path takes only a title and
# writes a Markdown frontmatter skeleton. This module's reader cannot open that file, so
# opening the document shows only a JSON parse error instead of a table — the reported symptom.
#
# That path has no user-chosen quantities or providers. So no numbers are invented; they are
# read from where they are already settled: quantities from this group's workflow sequence
# (the design-document count and work-set count currently held), and providers from the
# project's run chain and per-doc-type assignment table (the very values a continuous run
# would pick for that step). So a freshly opened plan holds "what this group is set to do right now", with no unset cells.

def workflow_type_counts(items: Optional[Iterable[dict]]) -> dict[str, int]:
    """Convert workflow sequence slots into work-plan quantities.

    A paired report slot (NR/TR/TSR) belongs to the same set as its instruction slot. Even
    with only the report slot left and no instruction slot, that set must still count, so the larger of the two is used.
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
    """The project's run chain; an empty chain when unreadable — plan creation must not die."""
    if not project_id:
        return {}
    try:
        from modules.flow_gate.settings import ai_settings_service

        return ai_settings_service.resolve_effective(project_id) or {}
    except Exception:  # noqa: BLE001 — an unreadable provider list is not a failure here
        return {}


def _registered_providers(project_id: Optional[str]) -> list[dict]:
    """The providers currently registered in this project; an empty list when unreadable."""
    return [
        provider for provider in (_effective_chain(project_id).get("providers") or [])
        if provider.get("id")
    ]


def _registered_provider_ids(project_id: Optional[str]) -> set[str]:
    """0411 T0004 (B0001 "each provider should be changeable from the full provider list").

    What a human may pick by hand is not the provider_candidates frozen into the plan but
    "candidates ∪ providers currently registered in this project". provider_candidates keeps
    only its original role (the AI-delegation range plus a display-name snapshot) — a split of roles, not a removal.

    With an unknown project_id or unreadable settings the set is empty and the rule narrows
    back to the old one (candidates only). Unreadable settings never loosen a save.
    """
    return {str(provider["id"]) for provider in _registered_providers(project_id)}


def _assigned_provider(project_id: Optional[str], type_code: str) -> Optional[str]:
    """The provider the per-doc-type assignment table (0317 D0004) sets for this type, or None."""
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
    """The first body of a work plan that skipped the create dialog — it opens already filled.

    Quantities come from the workflow sequence; provider candidates, defaults and per-type
    assignments from project settings. Nothing is invented, since no human chose it: a type
    absent from the sequence stays 0 for the editor to raise, and a project with no providers is left unassigned.
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
        # A table with nothing decided beats no plan at all. Not leaving behind an unopenable
        # file is the entire reason this function exists.
        return empty_recovery_body(project_id)


def is_unwritten_plan(raw: Optional[str]) -> bool:
    """Has a plan never been written to this file (empty file / Markdown frontmatter skeleton)?

    Reviving is permitted only here. A file with content that is merely broken is for a human
    to inspect at source, not for the server to overwrite.
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
        # 0411 T0004: a candidate, or any currently registered provider, is enough (NR0003 §6 option B).
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
    elif provider_id is None and display is not None:
        # T0004 §12/§26.8: provider-unspecified is the pair (provider_id=null,
        # provider_display_name=null) — never id=null with a leftover display name.
        errors.append(_error("provider_display_name_without_provider_id",
                             f"{loc}.provider_display_name", key))
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
    Layers are not merged on purpose (L0010 §2.3 decision 5): reporting "the count is out
    of range" together with "the steps do not match the counts" would show two places
    to fix where there is only one.

    0411 T0004: enforce_provider_scope=False is used only when reading a plan *already on
    disk* (load_body). A plan that passed validation when written must not become entirely
    unopenable just because a provider was later deleted from the project — the editor already
    handles a vanished provider as a greyed name (unavailable_provider). Format checks still run.
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
    # 0411 T0004: the registered list is read once per validation. The candidate list itself is
    # not widened here — candidates are "the AI-delegation range", the registered list is "what a human may pick".
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
    # 0411 T0004 (B0001): an unlocked step's provider need only be a candidate or currently registered.
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


# ── Canonical form (P0009 §2.6 decisions 3 and 4) ────────────────────────────

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
    """Where this document's canonical file SHOULD be, not where the document record points."""
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
    """Path of the canonical JSON file one work-plan document reads and writes.

    0403 NR0004 F3: if the place that reads a plan (the document route) and the place that
    records applications (the sequence save) each computed the path, they would eventually point at different files. It is computed here only.
    """
    from modules.flow_gate.storage import paths as storage_paths

    stored = (doc.get("file_path") or "").strip()
    branch = (doc.get("branch") or "main").strip() or "main"
    if stored:
        resolved = storage_paths.resolve_storage_path(stored, doc.get("project_id"), branch=branch)
        if resolved is not None:
            return resolved
        # The file may not exist yet (a reverted tree). Instead of answering "this is not a work
        # plan", it falls back to where the canonical file should be.
        loose = storage_paths.resolve_storage_dir(stored, doc.get("project_id"))
        if loose is not None:
            return loose
    return canonical_path_for_doc(doc)


# ── Reading a stored plan (P0009 §4.4 / §4.5) ────────────────────────────────

def load_body(path, project_id: Optional[str] = None) -> dict:
    """Read + validate a canonical file. Raises WorkPlanUnreadable, never a 500.

    0429 T0004: project_id now reaches validate() so a project's own countable-type
    registry (order, overrides) decides layer 6/7, not just the global one — every
    caller of this function passes the document's own project_id.
    """
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
        # 0411 T0004: reads do not re-ask about provider scope. The save already did, and if a
        # deleted provider blocked the plan from opening, the "vanished provider" marker could never be seen.
        return validate(parsed, project_id=project_id, enforce_provider_scope=False)
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

    0411 T0004: a non-candidate (merely registered) provider can now be assigned to a step.
    Name resolution runs registered list → candidate snapshot → id, so such a provider is
    counted under its current name rather than a raw id — this function has always counted steps, not the candidate list.
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
    """Provider ids the body actually uses — the default first, then step order, deduplicated."""
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

    0411 T0004 (B0001): a step provider may now sit outside the candidates — merely registered
    in this project. The editor asks this list whether a provider "is still registered", so an
    id the body actually uses gets a row even when it is not a candidate. Without that, a
    perfectly registered provider gets labelled "unavailable provider". A non-candidate row has
    no snapshot name (none was ever frozen), so name_changed is always false for it.
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


# ── Change summary (P0009 §5.1 decision 10 / §5.4 decision 11) ───────────────

_QUANTITY_SET_TEMPLATES = {
    "ko": "{code} {count}세트",
    "en": "{code} {count} sets",
    "ja": "{code} {count}セット",
}
_NOTE_CHANGED_TEMPLATES = {
    "ko": "steps[{key}].note 변경",
    "en": "steps[{key}].note changed",
    "ja": "steps[{key}].note 変更",
}


def _quantities_line(body: dict, locale: str = "ko") -> str:
    template = _QUANTITY_SET_TEMPLATES.get(locale) or _QUANTITY_SET_TEMPLATES["ko"]
    parts = []
    for code in body.get("counted_types") or []:
        quantity = (body.get("quantities") or {}).get(code) or {}
        count = quantity.get("count")
        if WORK_PLAN_TYPE_UNITS.get(code) == "set":
            parts.append(template.format(code=code, count=count))
        else:
            parts.append(f"{code} {count}")
    return " · ".join(parts)


def change_summary(after: dict, before: Optional[dict] = None, locale: str = "ko") -> dict:
    """The work-plan flavour of the inbox change summary.

    A prose document is summarized by "how many lines landed in which section"; a
    work plan has neither. What an unattended worker needs to know is whether the
    counts and assignments it sent are the ones now stored — and, on an edit,
    whether it overwrote someone else's values.
    """
    steps = after.get("steps") or []
    # Both examples in P0009 §5.1 and §5.4 satisfy assigned_steps + unassigned_steps == steps.
    # "Unassigned" counts only cells a human has not chosen yet (a locked TSR cannot be chosen,
    # so it is not unassigned), and "assigned" is the remainder. Putting locked cells in
    # neither would make the two sum to less than the step count, leaving an unmanned worker
    # unable to verify by arithmetic that everything it sent was saved.
    unassigned = unassigned_step_count(after)
    summary: dict[str, Any] = {
        "kind": "work_plan",
        "quantities": _quantities_line(after, locale),
        "steps": len(steps),
        "assigned_steps": len(steps) - unassigned,
        "unassigned_steps": unassigned,
    }
    if before is None:
        summary["sections"] = ["quantities", "provider_candidates", "defaults", "steps"]
        return summary

    note_changed_template = _NOTE_CHANGED_TEMPLATES.get(locale) or _NOTE_CHANGED_TEMPLATES["ko"]
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
            changed.append(note_changed_template.format(key=step.get("key")))
    summary["changed"] = changed
    return summary


# ── Template served to AI workers (P0009 §6) ─────────────────────────────────

def template_body(project_id: Optional[str] = None) -> str:
    """A minimal valid body with the project's real provider candidates.

    All countable types render at count 0 here — this is a JSON *format* example, not a
    recommended quantity (flowgate.default.0423 T0005 item 4). The worker still has to
    derive real counts from evidence per TEMPLATE_RULES; do not copy these zeros either.
    """
    providers = [
        provider for provider in (_effective_chain(project_id).get("providers") or [])
        if provider.get("id")
    ]
    candidates = snapshot_candidates([provider["id"] for provider in providers], providers)
    body = initial_body(list(WORK_PLAN_COUNTABLE_ORDER), candidates, project_id)
    return dumps(body)


def canonical_step_examples() -> dict[str, dict]:
    """Filled steps[] field-value examples for the design_template/WP help child
    (flowgate.default.0488 T0005). Every value comes from make_step() itself — never
    hand-authored JSON — so this cannot drift from what expand_steps() actually produces;
    see the drift-guard assertions in test_work_plan_0395.py.
    """
    return {
        "single": make_step("D", 1, None, "single"),
        "paired_instruction": make_step("T", 1, "TR#1", "instruction"),
        "paired_result": make_step("TR", 1, "T#1", "result"),
        "locked": make_step("TSR", 1, "TS#1", "result"),
    }


def contract_example(project_id: Optional[str] = None) -> dict:
    """A worked WP body -- T x2, TS x2, D x1 -- built with expand_steps()/canonicalize().

    T0004 §13/§20: the help item must show a canonical example that actually passes
    validate() unchanged, so it is built from the very functions validate() itself
    calls instead of a second hand-written JSON blob that could silently drift from
    them. Every non-locked step keeps provider_id/provider_display_name at null (the
    provider-unspecified case, always valid regardless of project/candidate state) and
    is marked origin="ai_suggested"; every TSR step keeps make_step()'s server-assembled
    values untouched.
    """
    counted_types = ["D", "T", "TS"]
    quantities = {
        "D": {"unit": WORK_PLAN_TYPE_UNITS["D"], "count": 1},
        "T": {"unit": WORK_PLAN_TYPE_UNITS["T"], "count": 2},
        "TS": {"unit": WORK_PLAN_TYPE_UNITS["TS"], "count": 2},
    }
    steps = expand_steps(counted_types, quantities, project_id)
    for step in steps:
        if not step["locked"]:
            step["origin"] = "ai_suggested"
    body = {
        "wp_version": WP_VERSION_SUPPORTED,
        "binding": BINDING_ADVISORY,
        "counted_types": type_order(counted_types, project_id),
        "quantities": quantities,
        "provider_candidates": [],
        "defaults": {"provider_id": None, "note": ""},
        "steps": steps,
    }
    return canonicalize(body)


TEMPLATE_RULES = {
    "ko": [
        "본문은 Markdown 이 아니라 UTF-8 JSON 입니다.",
        "제목과 연결 대상은 인박스 요청의 title / prev_doc_id 를 쓰고 본문에 적지 않습니다.",
        "steps 의 key 는 <타입코드>#<회차> 서식이며 문서 안에서 유일해야 합니다.",
        "steps 는 quantities 에서 펼쳐지는 목록과 순서까지 같아야 합니다.",
        "steps[].note 는 그 단계를 맡을 AI에게 줄 한 줄 지시입니다. TSR 외 단계는 null이어도 되지만, 값을 채우면 한 줄로 200자 이내여야 하며 줄바꿈과 탭은 쓰지 않습니다.",
        "수량은 근거(부모 R/B, workflow_type_counts, group_documents)에서 산정하고, 근거가 없으면 counted_types/quantities에 키를 남긴 채 0으로 둡니다. 1을 기본값으로 추측하지 않습니다.",
        "defaults.note 는 모든 단계에 공통으로 붙일 한 줄입니다. 요청 멘트의 '작업계획 맡길 범위' 절에 '전달 멘트'가 있으면 그 값을 그대로 옮겨 적습니다.",
        "steps[].provider_id 는 provider_candidates 안의 값이거나 이 프로젝트에 등록된 공급자여야 하며, 고를 것이 없으면 비워 둡니다.",
        "공급자를 지정하지 않는다는 뜻은 steps[].provider_id 와 steps[].provider_display_name 을 둘 다 null 로 두는 것입니다. provider_id 가 null 인데 provider_display_name 만 채우는 것은 허용되지 않습니다(validate() 가 거부합니다).",
        "defaults.provider_id 는 provider_candidates 안의 값이거나 null 입니다. 요청 멘트의 '실행 프로바이더'는 그 멘트를 실행 중인 공급자일 뿐이므로 여기에 옮겨 적지 않습니다.",
        "TSR 단계에는 공급자와 멘트를 적지 않습니다.",
        "item_seq(실제 단계 번호)는 적지 않습니다.",
        "binding 은 항상 advisory 입니다.",
        "steps[].pair_role 은 instruction/result/single 중 하나입니다. N/T/TS 같은 세트형 타입은 instruction·result 한 쌍을 이루고, DS/D/P/L/DB 같은 시트형 타입은 single 하나입니다.",
        "steps[].origin 은 human/ai_suggested/system 중 하나입니다. 잠기지 않은 단계는 human 이고, WORK_PLAN_LOCKED_TYPES(TSR)에 속한 단계는 서버가 채우므로 system 입니다.",
        "이 응답의 examples 필드에 single/paired instruction/paired result/locked 필드값 예시가 채워져 있습니다. 그대로 참고하되 key/provider_id/note 값은 실제 상황에 맞게 바꿉니다.",
    ],
    "en": [
        "The body is UTF-8 JSON, not Markdown.",
        "Title and parent come from the inbox request (title / prev_doc_id); never write them in the body.",
        "Each steps[].key is <TYPE>#<ordinal> and must be unique in the document.",
        "steps must equal the list expanded from quantities, in the same order.",
        "steps[].note is a one-line instruction for the AI assigned to that step; non-TSR steps may leave it null, but a non-null note must be one line, within 200 characters, and without newlines or tabs.",
        "Quantities are derived from evidence (the parent R/B, workflow_type_counts, group_documents); when there is no basis, keep the key in counted_types/quantities with count 0. Never guess 1 as a default.",
        "defaults.note is the one-line instruction shared by every step; when the request mention carries a Delivery note in its work-plan scope section, copy that value verbatim.",
        "steps[].provider_id must be one of provider_candidates or a provider registered in this project; leave it empty when there is nothing to choose.",
        "Provider-unspecified means BOTH steps[].provider_id and steps[].provider_display_name are null. Leaving provider_id null while still filling provider_display_name is rejected by validate().",
        "defaults.provider_id is one of provider_candidates or null. The mention's Execution provider is merely the provider running that mention, so never copy it here.",
        "A TSR step carries no provider and no note.",
        "Never write item_seq (the real workflow step number).",
        "binding is always advisory.",
        "steps[].pair_role is one of instruction/result/single. Set types like N/T/TS form an instruction+result pair; sheet types like DS/D/P/L/DB use single.",
        "steps[].origin is one of human/ai_suggested/system. An unlocked step is human; a step whose type is in WORK_PLAN_LOCKED_TYPES (TSR) is filled by the server, so it is system.",
        "This response's examples field carries filled single/paired-instruction/paired-result/locked field-value samples. Use them as a reference, but replace key/provider_id/note with values that fit the actual case.",
    ],
    "ja": [
        "本文は Markdown ではなく UTF-8 の JSON です。",
        "タイトルと連結対象はインボックス要求の title / prev_doc_id を使い、本文には書きません。",
        "steps の key は <タイプコード>#<回次> の書式で、文書内で一意でなければなりません。",
        "steps は quantities から展開されるリストと順序まで一致していなければなりません。",
        "steps[].note はその段階を担当するAIへの一行指示です。TSR以外の段階は null でも構いませんが、値を入れる場合は一行・200文字以内、改行・タブなしとします。",
        "数量は根拠(親 R/B、workflow_type_counts、group_documents)から算定し、根拠がなければ counted_types/quantities にキーを残したまま 0 とします。1 を既定値として推測しません。",
        "defaults.note は全段階に共通する一行指示です。要求メモの「作業計画を任せる範囲」節に「伝達メモ」があれば、その値をそのまま書き写します。",
        "steps[].provider_id は provider_candidates 内の値、またはこのプロジェクトに登録された提供者でなければならず、選べるものが無ければ空欄にします。",
        "提供者を指定しないとは steps[].provider_id と steps[].provider_display_name の両方を null にすることです。provider_id が null なのに provider_display_name だけ値を入れることは validate() が拒否します。",
        "defaults.provider_id は provider_candidates 内の値または null です。要求メモの「実行プロバイダー」はそのメモを実行中の提供者にすぎないため、ここに書き写しません。",
        "TSR 段階には提供者と一行メモを書きません。",
        "item_seq(実際の段階番号)は書きません。",
        "binding は常に advisory です。",
        "steps[].pair_role は instruction/result/single のいずれかです。N/T/TS のようなセット型は instruction・result の一対を成し、DS/D/P/L/DB のようなシート型は single 一つです。",
        "steps[].origin は human/ai_suggested/system のいずれかです。ロックされていない段階は human で、WORK_PLAN_LOCKED_TYPES(TSR)に属する段階はサーバーが埋めるため system です。",
        "この応答の examples フィールドに single/paired instruction/paired result/locked のフィールド値例が入っています。参考にしつつ、key/provider_id/note は実際の状況に合わせて書き換えてください。",
    ],
}

TEMPLATE_HEADING = {
    "ko": "## 다음 문서 템플릿 (WP / ko)",
    "en": "## Next document template (WP / en)",
    "ja": "## 次の文書テンプレート (WP / ja)",
}


def _contract_rule_lines(locale: str) -> list[str]:
    """Rules generated FROM step_contract()'s data, not retyped (T0004 §15/§17).

    A hand-written sentence naming '11 fields' would itself rot the moment a twelfth
    field were added. Building the sentence from STEP_FIELD_ORDER/PAIR_ROLES/ORIGINS/
    WORK_PLAN_PAIR_MAP means it can only ever describe what validate() enforces today.
    """
    fields = ", ".join(STEP_FIELD_ORDER)
    pair_roles = " | ".join(PAIR_ROLES)
    origins = " | ".join(ORIGINS)
    locked_reasons = " | ".join("null" if v is None else v for v in LOCKED_REASON_VALUES)
    pairs = ", ".join(f"{code}\u2194{pair}" for code, pair in WORK_PLAN_PAIR_MAP.items())
    single_types = ", ".join(WORK_PLAN_SHEET_TYPES)
    if locale == "en":
        return [
            f"Every steps[] entry must carry exactly these {len(STEP_FIELD_ORDER)} required fields, in any JSON key order: {fields}. Never omit a key just because its value is null. Extra fields are rejected unless their name starts with x_, which is preserved as an optional extension.",
            f"pair_role allowed values: {pair_roles}. origin allowed values: {origins}. locked_reason allowed values: {locked_reasons}.",
            f"A set type's instruction/result steps pair with each other: {pairs}. The instruction step has pair_role=instruction and pair_key = the result step's key; the result step has pair_role=result and pair_key = the instruction step's key.",
            f"Sheet types ({single_types}) are unpaired single steps: pair_role=single, pair_key=null.",
            "A non-locked step with no provider chosen represents that with BOTH fields null: provider_id=null AND provider_display_name=null. provider_id=null with a non-null provider_display_name is invalid and validate() rejects it.",
            "TSR is assembled by the server from a finished test run, never written by a human or an AI: provider_id=null, provider_display_name=null, note=null, locked=true, locked_reason=server_assembled, origin=system.",
            "This same schema is also published as data in this item's `contract` field; `example` is a whole minimal body that passes validate() unchanged, while `examples` carries one filled sample step per pair_role.",
        ]
    if locale == "ja":
        return [
            f"steps[] の各項目は必ず次の{len(STEP_FIELD_ORDER)}個の必須フィールドを持ちます(JSON内の順序は問いません): {fields}。値が null でもキー自体を省略しません。x_ で始まる名前の追加フィールドは任意の拡張として許可され、それ以外の未知のフィールドは拒否されます。",
            f"pair_role の許容値: {pair_roles}。origin の許容値: {origins}。locked_reason の許容値: {locked_reasons}。",
            f"セット型の instruction/result 段階は互いに対になります: {pairs}。instruction 段階は pair_role=instruction、pair_key=result 段階の key。result 段階は pair_role=result、pair_key=instruction 段階の key です。",
            f"シート型({single_types})は対のない単独段階です: pair_role=single, pair_key=null。",
            "提供者を選ばない非ロック段階は provider_id=null と provider_display_name=null の両方で表します。provider_id が null なのに provider_display_name だけ値がある状態は無効で、validate() が拒否します。",
            "TSR はサーバーが完了したテスト実行から組み立てる段階で、人間もAIも書きません: provider_id=null, provider_display_name=null, note=null, locked=true, locked_reason=server_assembled, origin=system。",
            "同じ契約はこの項目の `contract` フィールドにもデータとして載っており、`example` は validate() をそのまま通る最小の本文全体、`examples` は pair_role ごとに値を埋めた単一段階の見本です。",
        ]
    return [
        f"steps[] 각 항목은 JSON 키 순서와 무관하게 반드시 다음 {len(STEP_FIELD_ORDER)}개 필수 필드를 가져야 합니다: {fields}. 값이 null이어도 키 자체를 생략하지 않습니다. x_ 로 시작하는 이름의 추가 필드는 선택적 확장으로 허용되며, 그 외 알 수 없는 필드는 거부됩니다.",
        f"pair_role 허용값: {pair_roles}. origin 허용값: {origins}. locked_reason 허용값: {locked_reasons}.",
        f"set 타입의 instruction/result 단계는 서로 짝입니다: {pairs}. instruction 단계는 pair_role=instruction, pair_key=result 단계의 key이고, result 단계는 pair_role=result, pair_key=instruction 단계의 key입니다.",
        f"sheet 타입({single_types})은 짝이 없는 단일 단계입니다: pair_role=single, pair_key=null.",
        "공급자를 고르지 않은 non-locked 단계는 provider_id=null 과 provider_display_name=null 둘 다로 표시합니다. provider_id 가 null인데 provider_display_name 만 값이 있는 상태는 무효이며 validate() 가 거부합니다.",
        "TSR은 서버가 완료된 test run에서 조립하는 단계이며 사람도 AI도 작성하지 않습니다: provider_id=null, provider_display_name=null, note=null, locked=true, locked_reason=server_assembled, origin=system.",
        "같은 계약이 이 항목의 `contract` 필드에도 데이터로 실려 있고, `example`은 validate()를 그대로 통과하는 최소 본문 전체이며, `examples`는 pair_role 별로 값을 채운 단일 단계 견본입니다.",
    ]


def template_payload(locale: str, project_id: Optional[str] = None) -> dict:
    locale = normalize_locale(locale)
    rules = TEMPLATE_RULES.get(locale, TEMPLATE_RULES[FALLBACK_LOCALE])
    return {
        "type_code": WORK_PLAN_TYPE,
        "requested_locale": locale,
        "resolved_locale": locale,
        "body_format": "json",
        "heading": TEMPLATE_HEADING.get(locale, TEMPLATE_HEADING[FALLBACK_LOCALE]),
        "body": template_body(project_id),
        "rules": [*rules, *_contract_rule_lines(locale)],
        "contract": step_contract(),
        "example": contract_example(project_id),
        "examples": canonical_step_examples(),
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
    body = load_body(path, project_id=doc.get("project_id"))
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


# 0492 T0014: server-authoritative T/TR assignment gate.  The caller supplies only
# acknowledgement keys; provider capabilities always come from effective settings.
def capability_warning_findings(body: dict, project_id: str) -> list[dict]:
    from modules.flow_gate.settings import ai_settings_service
    from modules.flow_gate.services.provider_capability_service import capability_finding
    try:
        providers = {p.get("id"): p for p in (ai_settings_service.resolve_effective(project_id).get("providers") or [])}
    except Exception:
        providers = {}
    raw: list[dict] = []
    for step in body.get("steps") or []:
        finding = capability_finding(step.get("key"), step.get("type"), providers.get(step.get("provider_id")))
        if finding:
            finding["pair_key"] = step.get("pair_key")
            raw.append(finding)
    # A matching T/TR pair asks once through the T representative. Differing assignments
    # remain independent, so acknowledging one never silently covers another.
    result: list[dict] = []
    seen: set[tuple] = set()
    for finding in raw:
        pair = finding.get("pair_key")
        signature = (pair, finding["provider_id"], tuple(finding["missing_capabilities"]))
        if pair and signature in seen:
            continue
        seen.add(signature)
        if pair:
            representative = next((f for f in raw if f.get("pair_key") == pair and f["step_type"] == "T" and (f["provider_id"], tuple(f["missing_capabilities"])) == signature[1:]), finding)
            finding = dict(representative)
        finding.pop("pair_key", None)
        result.append(finding)
    return result