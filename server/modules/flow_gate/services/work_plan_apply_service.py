"""Work-plan apply/preview service (D0008/P0009/L0010).

Mappings are rebuilt per request; the WP canonical body is never changed by apply.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.db.document_type_labels import get_type_name
from modules.flow_gate.documents.constants import WORK_PLAN_PAIR_MAP, WORK_PLAN_STEP_TYPES
from modules.flow_gate.services.workflow_decision_service import (
    AUTO_REPORT_MAP,
    INSTRUCTION_AUTO_TYPES,
    expand_steps_with_reports,
)

# L0010 §1 — apply-only thresholds, defined once.
WORKFLOW_TAG_DIGEST_MOD = 100_000
APPLICATIONS_DEFAULT_LIMIT = 20
APPLICATIONS_MAX_LIMIT = 100
INSTRUCTION_MODES = {"auto_approved", "ai_direct"}

WARNING_CODES = (
    "workflow_not_decided", "steps_added", "extra_workflow_steps",
    "steps_already_done", "type_not_placeable", "order_differs",
    "provider_unset", "provider_not_registered", "provider_renamed",
    "note_empty", "nothing_to_fill", "locked_step_has_value",
    "instructions_folded", "wp_not_approved", "unmatched_plan_steps",
)
WARNING_SEVERITY = {
    code: ("warning" if code in {
        "type_not_placeable", "provider_unset", "provider_not_registered",
        "nothing_to_fill", "locked_step_has_value", "unmatched_plan_steps",
    } else "info")
    for code in WARNING_CODES
}
_COPY = {
    "ko": {
        "workflow_not_decided": "이 그룹의 워크플로가 아직 없습니다.",
        "steps_added": "계획에 맞추면 워크플로 단계 {count}개가 더해집니다.",
        "extra_workflow_steps": "계획보다 많은 워크플로 단계 {count}개는 지우지 않습니다.",
        "steps_already_done": "이미 시작했거나 끝난 단계 {count}개는 건드리지 않습니다.",
        "type_not_placeable": "워크플로에 놓을 수 없는 계획 단계가 {count}개 있습니다.",
        "order_differs": "계획과 워크플로의 단계 순서가 다릅니다. 워크플로 순서를 따릅니다.",
        "provider_unset": "공급자를 정하지 않은 단계 {count}개는 기본 공급자를 따릅니다.",
        "provider_not_registered": "등록되어 있지 않거나 꺼진 공급자의 단계가 {count}개 있습니다.",
        "provider_renamed": "계획 작성 뒤 공급자 이름이 바뀐 단계가 {count}개 있습니다.",
        "note_empty": "한줄 멘트가 빈 단계 {count}개는 공통 멘트를 따릅니다.",
        "nothing_to_fill": "채울 공급자나 한줄 멘트가 없습니다.",
        "locked_step_has_value": "서버 자동조립 단계의 값 {count}개는 입력하지 않습니다.",
        "instructions_folded": "자동승인 지시 단계 {count}개가 뒤의 레포트 단계로 합쳐집니다.",
        "wp_not_approved": "아직 검수 중인 작업계획으로 채웁니다.",
        "unmatched_plan_steps": "워크플로를 그대로 두어 이어질 자리가 없는 단계가 {count}개 있습니다.",
    },
    "en": {
        "workflow_not_decided": "This group has no decided workflow yet.",
        "steps_added": "{count} workflow steps will be appended to match the plan.",
        "extra_workflow_steps": "{count} workflow steps exceed the plan and will not be deleted.",
        "steps_already_done": "{count} started or completed steps will not be changed.",
        "type_not_placeable": "{count} plan steps cannot be placed in this workflow.",
        "order_differs": "Plan and workflow order differ; workflow order wins.",
        "provider_unset": "{count} steps have no provider and will use the default.",
        "provider_not_registered": "{count} steps name an unavailable provider.",
        "provider_renamed": "{count} provider names changed since the plan was written.",
        "note_empty": "{count} steps have no note and will use the common message.",
        "nothing_to_fill": "There are no provider or note values to fill.",
        "locked_step_has_value": "{count} server-assembled step values will be ignored.",
        "instructions_folded": "{count} auto-approved instruction steps fold into report steps.",
        "wp_not_approved": "This work plan is still under review.",
        "unmatched_plan_steps": "{count} plan steps have no slot because the workflow is unchanged.",
    },
    "ja": {
        "workflow_not_decided": "このグループのワークフローはまだ決定されていません。",
        "steps_added": "計画に合わせてワークフロー段階を{count}件追加します。",
        "extra_workflow_steps": "計画より多いワークフロー段階{count}件は削除しません。",
        "steps_already_done": "開始済みまたは完了済みの{count}段階は変更しません。",
        "type_not_placeable": "ワークフローに配置できない計画段階が{count}件あります。",
        "order_differs": "計画とワークフローの順序が異なるため、ワークフローを優先します。",
        "provider_unset": "プロバイダー未指定の{count}段階はデフォルトを使用します。",
        "provider_not_registered": "利用できないプロバイダーの段階が{count}件あります。",
        "provider_renamed": "計画作成後にプロバイダー名が変わった段階が{count}件あります。",
        "note_empty": "メモが空の{count}段階は共通メッセージを使用します。",
        "nothing_to_fill": "入力するプロバイダーまたはメモがありません。",
        "locked_step_has_value": "サーバー自動組立段階の値{count}件は入力しません。",
        "instructions_folded": "自動承認の指示段階{count}件をレポート段階に統合します。",
        "wp_not_approved": "まだレビュー中の作業計画を使用します。",
        "unmatched_plan_steps": "ワークフローを変更しないため、対応先のない段階が{count}件あります。",
    },
}


@dataclass
class ApplyConflict(Exception):
    code: str
    payload: dict


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ordered(items: Iterable[dict]) -> list[dict]:
    return sorted((dict(x) for x in (items or [])), key=lambda x: _int(x.get("item_seq")))


def _progress(item: dict) -> str:
    if item.get("status") == "done":
        return "done"
    if item.get("status") == "in_progress" or item.get("result_doc_id") is not None:
        return "in_progress"
    return "pending"


# L0010 §2.4
def build_workflow_tag(sequence: Optional[dict], items: Iterable[dict]) -> str:
    if not sequence:
        return "none"
    lines = []
    ordered = _ordered(items)
    for item in ordered:
        lines.append(":".join([
            str(item.get("item_seq") or ""),
            str(item.get("type") or "").upper(),
            str(item.get("status") or ""),
            str(item.get("sort_order") if item.get("sort_order") is not None else ""),
            "1" if item.get("result_doc_id") is not None else "0",
            str(item.get("label") or ""),
        ]))
    canonical = "\n".join([
        str(sequence.get("id") or ""),
        str(sequence.get("head_advanced_at") or ""),
        *lines,
    ])
    digest = int.from_bytes(hashlib.sha256(canonical.encode("utf-8")).digest()[:8], "big")
    return f"seq{sequence.get('id')}-r{digest % WORKFLOW_TAG_DIGEST_MOD}-i{len(ordered)}"


# L0010 §2.5
def build_step_map(plan_steps: Iterable[dict], items: Iterable[dict]) -> list[dict]:
    slots: dict[tuple[str, int], dict] = {}
    seen: dict[str, int] = {}
    for item in _ordered(items):
        code = str(item.get("type") or "").upper()
        seen[code] = seen.get(code, 0) + 1
        slots[(code, seen[code])] = item
    result = []
    for step in plan_steps or []:
        code, ordinal = str(step.get("type") or "").upper(), _int(step.get("ordinal"))
        item = slots.get((code, ordinal))
        result.append({
            "key": step.get("key"), "type": code, "ordinal": ordinal,
            "matched": item is not None,
            "item_seq": _int(item.get("item_seq")) if item else None,
            "status": _progress(item) if item else "unmatched",
            **({"label": item.get("label") or ""} if item else {}),
        })
    return result


def _registry(value: Any) -> dict[str, dict]:
    if isinstance(value, dict):
        rows = value.get("providers")
        if rows is None:
            rows = [
                ({**v, "id": k} if isinstance(v, dict) else {"id": k, "name": str(v)})
                for k, v in value.items()
            ]
    else:
        rows = value or []
    return {
        str(row.get("id") or row.get("provider_id")): row for row in rows
        if row.get("id") or row.get("provider_id")
    }


def _usable_provider(step: dict, registry: dict[str, dict]) -> Optional[str]:
    provider_id = step.get("provider_id")
    row = registry.get(str(provider_id)) if provider_id else None
    if not row or row.get("enabled", True) is False or row.get("is_enabled", True) is False:
        return None
    return str(provider_id)


def _usable_note(step: dict) -> Optional[str]:
    note = step.get("note")
    return str(note).strip() if note is not None and str(note).strip() else None


def _first_pair_after(items: Iterable[dict], source_seq: int, pair_type: str) -> Optional[dict]:
    return next((
        item for item in _ordered(items)
        if _int(item.get("item_seq")) > source_seq
        and str(item.get("type") or "").upper() == pair_type
    ), None)


# L0010 §2.6 / §4.2
def project(plan_steps: Iterable[dict], step_map: Iterable[dict], items: Iterable[dict],
            instruction_mode: str, provider_registry: Any) -> dict:
    mode = instruction_mode if instruction_mode in INSTRUCTION_MODES else "auto_approved"
    rows = list(step_map or [])
    mapping = {str(row.get("key")): row for row in rows}
    registry = _registry(provider_registry)
    own, tucked, folded, unfilled = [], [], [], []
    for step in plan_steps or []:
        key = str(step.get("key") or "")
        mapped = mapping.get(key) or {"matched": False}
        if not mapped.get("matched"):
            unfilled.append({"key": key, "reason": "unmatched"})
            continue
        if step.get("locked") or str(step.get("type") or "").upper() == "TSR":
            unfilled.append({"key": key, "reason": "locked", "item_seq": mapped.get("item_seq")})
            continue
        if mapped.get("status") != "pending":
            unfilled.append({"key": key, "reason": "already_started", "item_seq": mapped.get("item_seq")})
            continue
        source_seq = _int(mapped.get("item_seq"))
        target_seq, is_folded = source_seq, False
        code = str(step.get("type") or "").upper()
        if mode == "auto_approved" and code in INSTRUCTION_AUTO_TYPES:
            target = _first_pair_after(items, source_seq, AUTO_REPORT_MAP.get(code, ""))
            if target:
                target_seq, is_folded = _int(target.get("item_seq")), True
                to_map = next((row for row in rows if row.get("item_seq") == target_seq), {})
                folded.append({
                    "from_key": key, "to_key": to_map.get("key"),
                    "to_item_seq": target_seq, "reason": "auto_approved_instruction",
                })
        (tucked if is_folded else own).append((step, target_seq, source_seq))

    provider_out: dict[str, str] = {}
    note_out: dict[str, str] = {}

    def put(step: dict, target_seq: int, absent_only: bool) -> None:
        target = str(target_seq)
        provider, note = _usable_provider(step, registry), _usable_note(step)
        if provider is not None and (not absent_only or target not in provider_out):
            provider_out[target] = provider
        if note is not None and (not absent_only or target not in note_out):
            note_out[target] = note
        if provider is None:
            reason = "provider_unset" if not step.get("provider_id") else "provider_not_registered"
            entry = {"key": step.get("key"), "reason": reason}
            if step.get("provider_id"):
                entry["provider_id"] = step.get("provider_id")
            unfilled.append(entry)
        if note is None:
            unfilled.append({"key": step.get("key"), "reason": "note_empty"})

    for step, target, _source in own:  # own values win
        put(step, target, False)
    for step, target, source in sorted(tucked, key=lambda row: row[2], reverse=True):
        put(step, target, True)  # folded values fill only empty cells
    filled = sorted({int(x) for x in provider_out} | {int(x) for x in note_out})
    return {
        "provider_overrides": provider_out, "note_overrides": note_out,
        "filled_item_seqs": filled, "folded": folded, "unfilled": unfilled,
    }


# L0010 §2.7
def suggest_target_seq(plan_steps: Iterable[dict], step_map: Iterable[dict],
                       folded: Iterable[dict], provider_registry: Any) -> Optional[int]:
    rows = list(step_map or [])
    mapping = {str(row.get("key")): row for row in rows}
    folded_keys = {str(row.get("from_key")) for row in folded or []}
    registry, candidates = _registry(provider_registry), []
    for step in plan_steps or []:
        key, mapped = str(step.get("key") or ""), mapping.get(str(step.get("key") or ""))
        if not mapped or not mapped.get("matched") or mapped.get("status") != "pending":
            continue
        if step.get("locked") or str(step.get("type") or "").upper() == "TSR" or key in folded_keys:
            continue
        if step.get("provider_id") and _usable_provider(step, registry) is None:
            continue
        candidates.append(_int(mapped.get("item_seq")))
    if candidates:
        return max(candidates)
    pending = [_int(row.get("item_seq")) for row in rows
               if row.get("matched") and row.get("status") == "pending"]
    return max(pending) if pending else None


def _warning(code: str, locale: str, keys=(), item_seqs=(), detail=None) -> dict:
    keys = list(dict.fromkeys(str(x) for x in keys if x not in (None, "")))
    item_seqs = list(dict.fromkeys(_int(x) for x in item_seqs if x is not None))
    count = max(len(keys), len(item_seqs), 1 if code in {
        "workflow_not_decided", "nothing_to_fill", "wp_not_approved"
    } else 0)
    lang = locale if locale in _COPY else "ko"
    result = {
        "code": code, "severity": WARNING_SEVERITY[code], "count": count,
        "keys": keys, "item_seqs": item_seqs,
        "message": _COPY[lang][code].format(count=count),
    }
    if detail:
        result["detail"] = detail
    return result


# L0010 §2.8 / §4.3
def build_warnings(*, plan_steps: list[dict], step_map: list[dict], provider_registry: Any,
                   projection: dict, sequence_decided: bool, added: list[dict],
                   extra_item_seqs: list[int], unplaceable_keys: list[str],
                   order_differs_keys: list[str], wp_review_status: Optional[str],
                   unmatched_keys: list[str], locale: str = "ko") -> list[dict]:
    registry, result = _registry(provider_registry), []
    if not sequence_decided:
        result.append(_warning("workflow_not_decided", locale))
    if added:
        result.append(_warning("steps_added", locale, [row.get("plan_key") for row in added]))
    if extra_item_seqs:
        result.append(_warning("extra_workflow_steps", locale, item_seqs=extra_item_seqs))
    already = [row.get("item_seq") for row in step_map
               if row.get("matched") and row.get("status") != "pending"]
    if already:
        result.append(_warning("steps_already_done", locale, item_seqs=already))
    if unplaceable_keys:
        result.append(_warning("type_not_placeable", locale, unplaceable_keys))
    if order_differs_keys:
        result.append(_warning("order_differs", locale, order_differs_keys))
    unset, unavailable, renamed, empty_notes, locked_values = [], [], [], [], []
    for step in plan_steps:
        key = str(step.get("key") or "")
        if step.get("locked") or str(step.get("type") or "").upper() == "TSR":
            if step.get("provider_id") or str(step.get("note") or "").strip():
                locked_values.append(key)
            continue
        provider_id = step.get("provider_id")
        if not provider_id:
            unset.append(key)
        elif _usable_provider(step, registry) is None:
            unavailable.append(key)
        else:
            current = (registry.get(str(provider_id)) or {}).get("name")
            if current and step.get("provider_display_name") and current != step.get("provider_display_name"):
                renamed.append(key)
        if not str(step.get("note") or "").strip():
            empty_notes.append(key)
    for code, keys in (
        ("provider_unset", unset), ("provider_not_registered", unavailable),
        ("provider_renamed", renamed), ("note_empty", empty_notes),
        ("locked_step_has_value", locked_values),
    ):
        if keys:
            result.append(_warning(code, locale, keys))
    if not projection.get("provider_overrides") and not projection.get("note_overrides"):
        result.append(_warning("nothing_to_fill", locale))
    folded = [row.get("from_key") for row in projection.get("folded") or []]
    if folded:
        result.append(_warning("instructions_folded", locale, folded))
    if wp_review_status != "approved":
        result.append(_warning("wp_not_approved", locale))
    if unmatched_keys:
        result.append(_warning("unmatched_plan_steps", locale, unmatched_keys))
    return result


def _label(code: str, locale: str) -> str:
    return get_type_name(code, locale) or code


# L0010 §4.4
def _missing_items(plan_steps: list[dict], items: list[dict], locale: str) -> tuple[list[dict], list[str]]:
    counts: dict[str, int] = {}
    for item in items:
        code = str(item.get("type") or "").upper()
        counts[code] = counts.get(code, 0) + 1
    next_seq = max((_int(x.get("item_seq")) for x in items), default=0)
    next_order = max((_int(x.get("sort_order"), -1) for x in items), default=-1) + 1
    added, unplaceable = [], []
    for step in plan_steps:
        code, ordinal = str(step.get("type") or "").upper(), _int(step.get("ordinal"))
        if counts.get(code, 0) >= ordinal:
            continue
        if code not in WORK_PLAN_STEP_TYPES:
            unplaceable.append(str(step.get("key") or ""))
            continue
        # Reuse the workflow-decision draft rule; it is idempotent for an attached report.
        draft = expand_steps_with_reports([{"type": code, "label": _label(code, locale)}], locale)
        for row in draft:
            row_code = str(row.get("type") or "").upper()
            if counts.get(row_code, 0) >= ordinal:
                continue
            next_seq += 1
            added.append({
                "item_seq": next_seq, "type": row_code,
                "label": row.get("label") or _label(row_code, locale),
                "status": "pending", "sort_order": next_order, "result_doc_id": None,
                "plan_key": f"{row_code}#{ordinal}", "position": next_seq,
            })
            next_order += 1
            counts[row_code] = counts.get(row_code, 0) + 1
    return added, unplaceable


def _extra_items(plan_steps: list[dict], items: list[dict]) -> list[int]:
    wanted = {(str(x.get("type") or "").upper(), _int(x.get("ordinal"))) for x in plan_steps}
    seen, extra = {}, []
    for item in _ordered(items):
        code = str(item.get("type") or "").upper()
        seen[code] = seen.get(code, 0) + 1
        if code in WORK_PLAN_STEP_TYPES and (code, seen[code]) not in wanted:
            extra.append(_int(item.get("item_seq")))
    return extra


def _order_differs(rows: list[dict]) -> list[str]:
    matched = [row for row in rows if row.get("matched")]
    seqs = [_int(row.get("item_seq")) for row in matched]
    return [] if seqs == sorted(seqs) else [str(row.get("key")) for row in matched]


def _sequence(owner_doc_id: str) -> tuple[Optional[dict], list[dict]]:
    sequence = db_wfseq.get_sequence_by_doc_id(owner_doc_id)
    return (sequence, _ordered(db_wfseq.get_sequence_items(sequence["id"]) or [])) if sequence else (None, [])


def _comparison(plan_steps: list[dict], current: list[dict], added: list[dict]) -> dict:
    extra = _extra_items(plan_steps, current)
    return {
        "kept": {"count": len(current), "done_count": sum(_progress(x) != "pending" for x in current)},
        "added": {"count": len(added), "items": [{
            "position": x.get("position"), "type": x.get("type"),
            "label": x.get("label"), "plan_key": x.get("plan_key"),
        } for x in added]},
        "not_deleted": {"count": len(extra), "items": extra},
    }


def _preview_apply_blocker(*, sequence_decided: bool, target_seq: Optional[int],
                           projection: dict, has_unmatched_steps: bool) -> Optional[str]:
    if not sequence_decided:
        return "workflow_not_decided"
    if target_seq is None:
        return "unmatched_plan_steps" if has_unmatched_steps else "no_target"
    if not projection.get("provider_overrides") and not projection.get("note_overrides"):
        return "nothing_to_fill"
    return None


def preview(*, doc: dict, plan: dict, providers: Any,
            instruction_mode: str, locale: str = "ko") -> dict:
    mode = instruction_mode if instruction_mode in INSTRUCTION_MODES else "auto_approved"
    owner = doc.get("target_id") or doc.get("triggered_by")
    sequence, current = _sequence(owner)
    steps = list(plan.get("steps") or [])
    added, unplaceable = _missing_items(steps, current, locale)
    current_mapping = build_step_map(steps, current)
    current_projection = project(steps, current_mapping, current, mode, providers)
    current_target_seq = suggest_target_seq(
        steps, current_mapping, current_projection["folded"], providers,
    )
    keep_blocker = _preview_apply_blocker(
        sequence_decided=sequence is not None,
        target_seq=current_target_seq,
        projection=current_projection,
        has_unmatched_steps=bool(added),
    )
    projected_items = current + added
    mapping = build_step_map(steps, projected_items)
    projection = project(steps, mapping, projected_items, mode, providers)
    target_seq = suggest_target_seq(steps, mapping, projection["folded"], providers)
    change_blocker = _preview_apply_blocker(
        sequence_decided=True,
        target_seq=target_seq,
        projection=projection,
        has_unmatched_steps=False,
    )
    by_seq = {_int(x.get("item_seq")): x for x in projected_items}
    current_max = max((_int(x.get("item_seq")) for x in current), default=0)
    preview_map = []
    for row in mapping:
        view = {**row, "position_after_apply": row.get("item_seq")}
        if row.get("matched") and _int(row.get("item_seq")) > current_max:
            view.update({"matched": False, "item_seq": None, "status": "to_be_added"})
        preview_map.append(view)
    warnings = build_warnings(
        plan_steps=steps, step_map=mapping, provider_registry=providers,
        projection=projection, sequence_decided=sequence is not None, added=added,
        extra_item_seqs=_extra_items(steps, current), unplaceable_keys=unplaceable,
        order_differs_keys=_order_differs(mapping),
        wp_review_status=doc.get("doc_review_status"), unmatched_keys=[], locale=locale,
    )
    target = by_seq.get(target_seq or -1)
    return {
        "ok": True, "wp_doc_id": doc.get("doc_id"),
        "wp_revision_no": _int(doc.get("revision_no")),
        "wp_review_status": doc.get("doc_review_status"), "instruction_mode": mode,
        "workflow": {
            "owner_doc_id": owner, "sequence_id": sequence.get("id") if sequence else None,
            "decided": sequence is not None, "workflow_tag": build_workflow_tag(sequence, current),
            "current_items": current,
        },
        "comparison": _comparison(steps, current, added), "step_map": preview_map,
        "fill_preview": {
            "target_seq": target_seq,
            "target_key": next((x.get("key") for x in mapping if x.get("item_seq") == target_seq), None),
            "target_label": (target or {}).get("label"), **projection,
            "default_note": str((plan.get("defaults") or {}).get("note") or "").strip(),
        },
        "warnings": warnings,
        "can_apply": change_blocker is None,
        "can_apply_without_workflow": keep_blocker is None,
        "can_apply_with_workflow": change_blocker is None,
        "can_change_workflow": True,
        "apply_blockers": {
            "keep_workflow": keep_blocker,
            "change_workflow": change_blocker,
        },
    }


def _applications_path(plan_path: Path, doc_id: str) -> Path:
    return Path(plan_path).with_name(f"{str(doc_id).rsplit('.', 1)[-1]}_applications.jsonl")


def append_application(plan_path: Path, doc_id: str, row: dict) -> None:
    path = _applications_path(plan_path, doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def read_applications(plan_path: Path, doc_id: str, limit: int = APPLICATIONS_DEFAULT_LIMIT) -> dict:
    limit = max(1, min(_int(limit, APPLICATIONS_DEFAULT_LIMIT), APPLICATIONS_MAX_LIMIT))
    path = _applications_path(plan_path, doc_id)
    if not path.exists():
        return {"ok": True, "total": 0, "items": [], "broken_lines": 0}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {"ok": True, "total": 0, "items": [], "broken_lines": 1}
    readable, broken = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                readable.append(row)
            else:
                broken += 1
        except (json.JSONDecodeError, ValueError):
            broken += 1
    return {"ok": True, "total": len(readable), "items": list(reversed(readable))[:limit],
            "broken_lines": broken}


def apply(*, doc: dict, owner_doc: dict, plan: dict, plan_path: Path, providers: Any,
          instruction_mode: str, change_workflow: bool, workflow_tag: str,
          wp_revision_no: int, applied_by: str, locale: str = "ko") -> dict:
    mode = instruction_mode if instruction_mode in INSTRUCTION_MODES else "auto_approved"
    owner_id = owner_doc.get("doc_id")
    sequence, current = _sequence(owner_id)
    before_tag = build_workflow_tag(sequence, current)
    if workflow_tag != before_tag:
        raise ApplyConflict("workflow_changed", {
            "code": "workflow_changed", "sent_workflow_tag": workflow_tag,
            "current_workflow_tag": before_tag,
        })
    current_revision = _int(doc.get("revision_no"))
    if _int(wp_revision_no) != current_revision:
        raise ApplyConflict("wp_changed", {
            "code": "wp_changed", "sent_wp_revision_no": _int(wp_revision_no),
            "current_wp_revision_no": current_revision,
        })
    steps = list(plan.get("steps") or [])
    proposed, unplaceable = _missing_items(steps, current, locale)
    added = []
    if change_workflow and proposed:
        with get_store().transaction():
            if sequence is None:
                db_wfseq.insert_sequence(owner_id)
                sequence = db_wfseq.get_sequence_by_doc_id(owner_id)
            for item in proposed:
                db_wfseq.insert_sequence_item(
                    sequence_id=sequence["id"], item_seq=_int(item.get("item_seq")),
                    type_=str(item.get("type") or ""), label=str(item.get("label") or ""),
                    doc_class=str(owner_doc.get("type_code") or "R"),
                    sort_order=_int(item.get("sort_order")),
                )
            added = proposed
        sequence, current = _sequence(owner_id)
    mapping = build_step_map(steps, current)
    projection = project(steps, mapping, current, mode, providers)
    target_seq = suggest_target_seq(steps, mapping, projection["folded"], providers)
    unmatched = [str(x.get("key")) for x in mapping if not x.get("matched")] if not change_workflow else []
    warnings = build_warnings(
        plan_steps=steps, step_map=mapping, provider_registry=providers,
        projection=projection, sequence_decided=sequence is not None, added=added,
        extra_item_seqs=_extra_items(steps, current), unplaceable_keys=unplaceable,
        order_differs_keys=_order_differs(mapping), wp_review_status=doc.get("doc_review_status"),
        unmatched_keys=unmatched, locale=locale,
    )
    after_tag = build_workflow_tag(sequence, current)
    target = next((x for x in current if _int(x.get("item_seq")) == target_seq), {})
    applied_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    fill = {
        "source_doc_id": doc.get("doc_id"), "source_revision_no": current_revision,
        "instruction_mode": mode, "target_seq": target_seq,
        "target_type": target.get("type"), "target_label": target.get("label"),
        "provider_overrides": projection["provider_overrides"],
        "note_overrides": projection["note_overrides"],
        "default_note": str((plan.get("defaults") or {}).get("note") or "").strip(),
        "filled_item_seqs": projection["filled_item_seqs"],
        "folded": projection["folded"], "unfilled": projection["unfilled"],
    }
    history = {
        "applied_at": applied_at, "applied_by": applied_by,
        "wp_revision_no": current_revision, "instruction_mode": mode,
        "workflow_changed": bool(added), "workflow_tag_before": before_tag,
        "workflow_tag_after": after_tag,
        "added_item_seqs": [_int(x.get("item_seq")) for x in added],
        "filled_item_seqs": projection["filled_item_seqs"], "target_seq": target_seq,
        "warning_codes": [x["code"] for x in warnings],
    }
    append_application(plan_path, str(doc.get("doc_id")), history)
    return {
        "ok": True, "applied_at": applied_at, "applied_by": applied_by,
        "wp_doc_id": doc.get("doc_id"), "wp_revision_no": current_revision,
        "workflow_changed": bool(added),
        "workflow": {"owner_doc_id": owner_id, "sequence_id": sequence.get("id") if sequence else None},
        "workflow_tag": after_tag, "added_items": added, "step_map": mapping,
        "fill": fill, "warnings": warnings,
    }
