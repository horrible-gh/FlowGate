"""Work-plan → workflow-sequence converter (flowgate.default.0399 D0010 §2 / L0011).

D0010 §2 lists this as a component of its own: "계획→시퀀스 변환기 (신규) … 화면에 붙이지
않고 따로 떼어 둔다". That separation is the whole reason the module exists — a later group
is meant to drive the same conversion with no screen at all (D0010 [DEFERRED] 마지막 줄), so
nothing in here may reach for a request, a session, or a rendering concern.

What it does: turn an approved work plan's step list into *candidate* sequence rows, merge
them into the current sequence the way the chosen mode says, and report what the person
should know before saving. It never writes. Pouring only produces the edit dialog's starting
state; the sequence changes when a human presses [저장] (D0010 §3.3).

Everything numeric or set-valued here is L0011 §1; the algorithms are L0011 §2.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.document_type_labels import get_type_name
from modules.flow_gate.documents.constants import STEP_NOTE_MAX_CHARS
from modules.flow_gate.services.work_plan_apply_service import build_workflow_tag
from modules.flow_gate.services.workflow_decision_service import AUTO_REPORT_MAP

# ── L0011 §1.1 — numeric parameters ──────────────────────────────────────────
# 0406 T0022 작업 6: 값은 documents.constants 의 정본을 읽는다. 이름은 그대로 두어
# 기존 호출부(시험 포함)가 wpseq.NOTE_MAX_CHARS 로 계속 물어볼 수 있게 한다.
NOTE_MAX_CHARS = STEP_NOTE_MAX_CHARS
POUR_ROWS_MAX = 100
UNDO_DEPTH = 1
PLACEABLE_MIN = 1

# ── L0011 §1.2 — fixed sets ──────────────────────────────────────────────────
PLACEABLE_TYPES = frozenset({"DS", "D", "P", "L", "DB", "N", "T", "TS", "NR", "TR", "TSR"})
# AUTO_ROW_MAP is not a second copy of the instruction→report pairing: L0011 §1.2 requires one
# side to be the original and the other to read it, because a drift between the two would make
# the rows we pour differ from the rows the save path writes.
AUTO_ROW_MAP = dict(AUTO_REPORT_MAP)
INSTRUCTION_TYPES = frozenset(AUTO_ROW_MAP)
AUTO_ROW_TYPES = frozenset(AUTO_ROW_MAP.values())
SERVER_ASSEMBLED_TYPES = frozenset({"TSR"})

MODES = ("append", "replace_after")

_CONTROL_CHARS = {chr(code) for code in range(0x20)} | {chr(0x7F)}

# L0011 §2.10 / P0013 ① — one envelope per code; the order below is the order the dialog
# reads them in, so it is fixed here rather than left to the caller.
# M0020: 승인 여부는 이 목록에서 빠졌다. 아직 검토 중인 계획도 승인된 계획과 똑같이
# 부어지고, 그래도 되는지는 마지막에 [저장]을 누르는 사람이 정한다.
_NOTIFICATION_ORDER = (
    "type_not_placeable",
    "rows_truncated",
    "type_overlap",
    "notes_discarded",
    "note_missing",
    "paired_note_dropped",
    "server_assembled_note",
)
_SEVERITY = {
    "type_not_placeable": "warning",
    "rows_truncated": "warning",
    "type_overlap": "warning",
    "notes_discarded": "warning",
    "note_missing": "warning",
    "paired_note_dropped": "info",
    "server_assembled_note": "info",
}


class InvalidMode(ValueError):
    """The caller asked for a mode that is neither append nor replace_after."""


class NoteTooLong(ValueError):
    """저장하려는 한줄 멘트가 상한을 넘었다 (0406 T0022 작업 6 / M0019).

    옛 동작은 조용한 절단이었다. 사용자가 1,000 자를 적어 저장해도 아무 말 없이 200 자만
    남았고, 화면은 저장에 성공했다고 말했다. 자르는 대신 거절하고, 작업계획 저장이 이미
    쓰는 ``note_too_long`` 과 같은 모양(코드 + max + 실제 길이)으로 사실을 실어 보낸다.
    """

    code = "note_too_long"

    def __init__(self, length: int, max_chars: int = NOTE_MAX_CHARS):
        super().__init__(f"note_too_long:{length}>{max_chars}")
        self.length = int(length)
        self.max_chars = int(max_chars)


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def label_of(code: str, locale: str = "ko") -> str:
    return get_type_name(code, locale) or code


# ── L0011 §2.2 ───────────────────────────────────────────────────────────────

def normalize_note(raw: Any, *, strict: bool = False) -> str:
    """Trim a step note down to what a sequence row may carry.

    "값이 없음"과 "빈 글자"를 나누지 않는다 (L0011 §2.2): both come back as "". Keeping the
    distinction would flip the stored value on every save round-trip for no visible gain.

    0406 T0022 작업 6 — **조용한 절단은 없다.** 옛 구현은 상한을 넘는 글자를 말없이 버렸고,
    저장은 성공한 것처럼 보였다. 이제 자르지 않는다:

    * ``strict=True`` (저장 경로) 는 상한을 넘으면 :class:`NoteTooLong` 을 올려 거절한다.
    * ``strict=False`` (읽기·프롬프트 조립 같은 표시 경로) 는 있는 그대로 돌려준다. 읽는
      쪽이 옛 데이터 때문에 터지면 안 되고, 읽기가 값을 줄이면 그것도 같은 종류의 조용한
      손실이기 때문이다.
    """
    if raw is None or not isinstance(raw, str):
        return ""
    text = "".join(ch for ch in raw if ch not in _CONTROL_CHARS)
    text = text.strip()
    if strict and len(text) > NOTE_MAX_CHARS:
        raise NoteTooLong(len(text))
    return text


# ── L0011 §2.1 — a row inside the edit dialog ────────────────────────────────

def _new_row(
    uid: int,
    type_: str,
    locale: str,
    *,
    is_auto: bool = False,
    auto_of_uid: Optional[int] = None,
    note: str = "",
    note_source: Optional[str] = None,
    origin: str = "manual",
    plan_key: Optional[str] = None,
    source_doc_id: Optional[str] = None,
    source_revision_no: Optional[int] = None,
    item_seq_before: Optional[int] = None,
    label: Optional[str] = None,
    status: str = "pending",
    locked: bool = False,
) -> dict:
    return {
        "uid": uid,
        "type": type_,
        "label": label or label_of(type_, locale),
        "is_auto": is_auto,
        "auto_of_uid": auto_of_uid,
        "note": note,
        "note_source": note_source,
        "origin": origin,
        "plan_key": plan_key,
        "source_doc_id": source_doc_id,
        "source_revision_no": source_revision_no,
        "item_seq_before": item_seq_before,
        "status": status,
        "locked": locked,
        "poured": False,
    }


def origin_of_loaded_row(row: dict, previous: Optional[dict]) -> str:
    """L0011 §2.1: how a row already in the sequence reports where it came from.

    A stored row with no source document is read as a row a person put there — which is
    what it is, and what migration 079 leaves every pre-existing row saying.
    """
    code = str(row.get("type") or "").upper()
    if code in AUTO_ROW_TYPES and previous is not None:
        parent = str(previous.get("type") or "").upper()
        if AUTO_ROW_MAP.get(parent) == code:
            return "auto"
    if row.get("source_doc_id"):
        return "plan"
    return "manual"


def load_current_rows(items: Iterable[dict], locale: str = "ko") -> tuple[list[dict], list[dict]]:
    """Split the stored sequence into (locked rows, editable rows).

    Locked rows are the ones a save may not touch — they already produced a document, so
    they are the structural reason "이미 끝난 줄과 지금 진행 중인 줄은 어느 방식에서도
    지우지 않는다" holds without a single guard clause (L0011 §2.5).
    """
    locked: list[dict] = []
    pending: list[dict] = []
    uid = 0
    previous: Optional[dict] = None
    for item in items or []:
        uid += 1
        code = str(item.get("type") or "").upper()
        is_locked = item.get("result_doc_id") is not None or item.get("status") != "pending"
        origin = origin_of_loaded_row(item, previous)
        row = _new_row(
            uid,
            code,
            locale,
            is_auto=origin == "auto",
            note=normalize_note(item.get("note")),
            origin=origin,
            source_doc_id=item.get("source_doc_id"),
            source_revision_no=_int(item.get("source_revision_no")),
            item_seq_before=_int(item.get("item_seq")),
            label=item.get("label") or None,
            status=str(item.get("status") or "pending"),
            locked=bool(is_locked),
        )
        (locked if is_locked else pending).append(row)
        previous = item
    return locked, pending


# ── L0011 §2.3 — the conversion itself ───────────────────────────────────────

def _carry_note_to_pair(result_step: dict, rows: list[dict], dropped: list[dict]) -> None:
    note = normalize_note(result_step.get("note"))
    if note == "":
        return
    pair_key = result_step.get("pair_key")
    target = None
    for row in rows:
        if row.get("plan_key") == pair_key:
            target = row
    if target is None:
        dropped.append({"plan_key": result_step.get("key"), "reason": "pair_not_found"})
        return
    if target["note"] == "":
        target["note"] = note
        target["note_source"] = "pair"
    else:
        # 지시 줄에 사람이 적어 둔 말이 자동 줄의 말에 덮이는 편이 훨씬 나쁘다 (L0011 §2.3).
        dropped.append({
            "plan_key": result_step.get("key"),
            "reason": "paired_note_dropped",
            "note": note,
        })


def plan_to_rows(
    plan: dict,
    plan_doc_id: str,
    plan_revision_no: Optional[int],
    locale: str = "ko",
    start_uid: int = 0,
) -> tuple[list[dict], list[dict], int]:
    """Turn a plan body's steps into row candidates (L0011 §2.3).

    A plan stores an instruction step and its result step as a pair. The sequence dialog
    attaches the result row itself, so building a row from the plan's result step too would
    produce two of them — the result step therefore hands its note to its partner and makes
    no row of its own.
    """
    rows: list[dict] = []
    dropped: list[dict] = []
    uid = start_uid
    for step in plan.get("steps") or []:
        code = str(step.get("type") or "").upper()
        if code not in PLACEABLE_TYPES:
            dropped.append({
                "plan_key": step.get("key"), "type": code, "reason": "type_not_placeable",
            })
            continue
        if code in SERVER_ASSEMBLED_TYPES:
            if normalize_note(step.get("note")) != "":
                dropped.append({
                    "plan_key": step.get("key"), "type": code,
                    "reason": "server_assembled_note",
                })
            continue
        if code in AUTO_ROW_TYPES:
            _carry_note_to_pair(step, rows, dropped)
            continue
        uid += 1
        step_note = normalize_note(step.get("note"))
        rows.append(_new_row(
            uid, code, locale,
            note=step_note,
            note_source="step" if step_note else None,
            origin="plan",
            plan_key=step.get("key"),
            source_doc_id=plan_doc_id,
            source_revision_no=plan_revision_no,
        ))

    defaults = plan.get("defaults")
    default_note = normalize_note(defaults.get("note")) if isinstance(defaults, dict) else ""
    if default_note:
        for row in rows:
            if row["note"] == "":
                row["note"] = default_note
                row["note_source"] = "defaults"
    return rows, dropped, uid


# ── L0011 §2.4 ───────────────────────────────────────────────────────────────

def attach_auto_rows(rows: list[dict], locale: str = "ko", next_uid: int = 0) -> tuple[list[dict], int]:
    """Re-derive every automatic report row. Idempotent by construction.

    The save path runs the same pairing again on the server, so a list that already has its
    report rows must come back unchanged — old automatic rows are dropped and rebuilt rather
    than reconciled, which is what makes running it twice indistinguishable from once.
    """
    uid = next_uid
    out: list[dict] = []
    by_parent = {row.get("auto_of_uid"): row for row in rows if row.get("is_auto")}
    for row in rows:
        if row.get("is_auto"):
            continue
        out.append(row)
        want = AUTO_ROW_MAP.get(row["type"]) if row["type"] in INSTRUCTION_TYPES else None
        if not want:
            continue
        old = by_parent.get(row["uid"])
        if old is not None and old.get("type") == want:
            auto_uid = old["uid"]
            status = old.get("status", "pending")
            locked = bool(old.get("locked"))
            item_seq_before = old.get("item_seq_before")
        else:
            uid += 1
            auto_uid = uid
            status, locked, item_seq_before = "pending", False, None
        out.append(_new_row(
            auto_uid, want, locale,
            is_auto=True,
            auto_of_uid=row["uid"],
            note="",              # 자동 줄은 멘트를 갖지 않는다 (L0011 §2.4)
            origin="auto",
            status=status,
            locked=locked,
            item_seq_before=item_seq_before,
        ))
    return out, max(uid, next_uid)


# ── L0011 §2.5 · §2.6 — the two modes ────────────────────────────────────────

def cut_index(pending_before: list[dict], wp_item_seq: Optional[int]) -> int:
    """Where 'replace after this plan' starts cutting.

    A work plan document is the result of the row it sits on, and a row with a result is a
    locked row — so the plan's own row is almost never in the editable list and this returns
    0. That is the right answer, not a fallback: when the plan's row is above the editable
    list, the whole editable list *is* "the rows after the plan" (L0011 §2.6).
    """
    if wp_item_seq is None:
        return 0
    for index, row in enumerate(pending_before):
        if row.get("item_seq_before") == wp_item_seq:
            return index + 1
    return 0


def pour_append(pending_before: list[dict], pour_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    return list(pending_before) + list(pour_rows), []


def pour_replace_after(
    pending_before: list[dict], pour_rows: list[dict], wp_item_seq: Optional[int],
) -> tuple[list[dict], list[dict]]:
    cut = cut_index(pending_before, wp_item_seq)
    return list(pending_before[:cut]) + list(pour_rows), list(pending_before[cut:])


# ── L0011 §2.9 ───────────────────────────────────────────────────────────────

def overlap_types(before_uids: set[int], poured_uids: set[int], rows: list[dict],
                  poured_type_of: dict[int, str]) -> list[str]:
    """Which types a surviving original row and a poured row now share.

    Only types, never positions: what a person sees is "DS 가 두 줄이네", not "DS#2 가
    겹치네" (L0011 §2.9). Deleted rows cannot overlap with anything.
    """
    surviving = {
        row["type"] for row in rows
        if not row.get("is_auto") and row["uid"] in before_uids
    }
    incoming = {poured_type_of[uid] for uid in poured_uids if uid in poured_type_of}
    return sorted(surviving & incoming)


# ── L0011 §4.3 ───────────────────────────────────────────────────────────────

def row_count_change(
    mode: str,
    locked_rows: list[dict],
    pending_before: list[dict],
    pour_rows: list[dict],
    wp_item_seq: Optional[int],
) -> dict:
    before = len(locked_rows) + len(pending_before)
    deleted = 0 if mode == "append" else len(pending_before) - cut_index(pending_before, wp_item_seq)
    added = len(pour_rows)              # 자동 줄도 사람이 목록에서 보는 한 줄이므로 센다
    return {"before": before, "after": before - deleted + added, "deleted": deleted, "added": added}


# ── L0011 §2.10 ──────────────────────────────────────────────────────────────

def _envelope(code: str, count: int, **extra) -> dict:
    return {"code": code, "severity": _SEVERITY[code], "count": count, **extra}


def build_notifications(
    *,
    rows: list[dict],
    overlap: list[str],
    deleted_rows: list[dict],
    dropped: list[dict],
    truncated_count: int,
) -> list[dict]:
    """Everything the person should know before saving. None of it blocks the save.

    ``note_missing`` is recomputed here every time rather than carried along, because the
    person may have filled a note in since the last time it was asked (L0011 §2.10).
    """
    found: dict[str, dict] = {}

    unplaceable = [d for d in dropped if d.get("reason") == "type_not_placeable"]
    if unplaceable:
        found["type_not_placeable"] = _envelope(
            "type_not_placeable", len(unplaceable),
            items=[{"plan_key": d.get("plan_key"), "type": d.get("type")} for d in unplaceable],
        )

    if truncated_count > 0:
        found["rows_truncated"] = _envelope("rows_truncated", truncated_count)

    if overlap:
        found["type_overlap"] = _envelope("type_overlap", len(overlap), types=list(overlap))

    discarded = [row for row in deleted_rows if row.get("note")]
    if discarded:
        found["notes_discarded"] = _envelope(
            "notes_discarded", len(discarded),
            items=[
                {"type": row["type"], "label": row["label"], "note": row["note"]}
                for row in discarded
            ],
        )

    missing = [
        index for index, row in enumerate(rows)
        if not row.get("is_auto") and not row.get("locked") and row.get("note") == ""
    ]
    if missing:
        found["note_missing"] = _envelope("note_missing", len(missing), row_indexes=missing)

    paired = [d for d in dropped if d.get("reason") == "paired_note_dropped"]
    if paired:
        found["paired_note_dropped"] = _envelope(
            "paired_note_dropped", len(paired),
            items=[{"plan_key": d.get("plan_key"), "note": d.get("note")} for d in paired],
        )

    assembled = [d for d in dropped if d.get("reason") == "server_assembled_note"]
    if assembled:
        found["server_assembled_note"] = _envelope(
            "server_assembled_note", len(assembled),
            items=[{"plan_key": d.get("plan_key"), "type": d.get("type")} for d in assembled],
        )

    return [found[code] for code in _NOTIFICATION_ORDER if code in found]


# ── P0013 ① — the whole response ─────────────────────────────────────────────

def _public_row(row: dict) -> dict:
    """The row shape the dialog receives. ``item_seq`` is deliberately absent: the save
    renumbers every row, and the note rides on the row itself (L0011 §2.11)."""
    return {
        "type": row["type"],
        "label": row["label"],
        "status": row.get("status", "pending"),
        "locked": bool(row.get("locked")),
        "poured": bool(row.get("poured")),
        "note": row.get("note") or "",
        "note_source": row.get("note_source"),
        "origin": row.get("origin") or "manual",
        "plan_key": row.get("plan_key"),
        "source_doc_id": row.get("source_doc_id"),
        "source_revision_no": row.get("source_revision_no"),
    }


def build_candidates(*, doc: dict, plan: dict, mode: str, locale: str = "ko") -> dict:
    """Build the edit dialog's starting state for one work plan and one mode.

    Reads only. Nothing here changes the sequence, and nothing changes the plan — "적용은
    계획을 읽기만 한다" (D0010 §4).
    """
    if mode not in MODES:
        raise InvalidMode(mode)

    wp_doc_id = str(doc.get("doc_id") or "")
    owner_doc_id = doc.get("target_id") or doc.get("triggered_by")
    sequence = db_wfseq.get_sequence_by_doc_id(owner_doc_id) if owner_doc_id else None
    items = list(db_wfseq.get_sequence_items(sequence["id"]) or []) if sequence else []

    locked_rows, pending_before = load_current_rows(items, locale)
    next_uid = len(items)

    wp_revision_no = _int(doc.get("revision_no"), 0)
    plan_rows, dropped, next_uid = plan_to_rows(
        plan, wp_doc_id, wp_revision_no, locale, start_uid=next_uid,
    )
    plan_step_count = len(plan_rows)
    pour_rows, next_uid = attach_auto_rows(plan_rows, locale, next_uid)

    truncated_count = 0
    if len(pour_rows) > POUR_ROWS_MAX:
        # 자동 줄은 부모와 함께 잘라 짝이 깨지지 않게 한다 (L0011 §5).
        keep = pour_rows[:POUR_ROWS_MAX]
        while keep and keep[-1].get("type") in INSTRUCTION_TYPES:
            keep.pop()
        truncated_count = len(pour_rows) - len(keep)
        pour_rows = keep
        plan_step_count = sum(1 for row in pour_rows if row.get("origin") == "plan")
    for row in pour_rows:
        row["poured"] = True

    wp_item = db_wfseq.get_item_by_result_doc_id(wp_doc_id)
    wp_item_seq = _int(wp_item.get("item_seq")) if wp_item else None

    if mode == "append":
        next_rows, deleted_rows = pour_append(pending_before, pour_rows)
    else:
        next_rows, deleted_rows = pour_replace_after(pending_before, pour_rows, wp_item_seq)

    all_rows = locked_rows + next_rows
    before_uids = {row["uid"] for row in pending_before}
    poured_uids = {row["uid"] for row in pour_rows if not row.get("is_auto")}
    poured_type_of = {row["uid"]: row["type"] for row in pour_rows if not row.get("is_auto")}

    return {
        "wp_doc_id": wp_doc_id,
        # 0403 NR0004 F2 — 행에만 적혀 있던 계획 리비전을 응답 머리에도 싣는다. 저장할 때
        # 이 값을 그대로 돌려보내야 "대화상자를 연 뒤 계획이 바뀌었는지"를 서버가 판정할 수
        # 있다. 행 안의 source_revision_no 는 저장 뒤에도 계속 따라다녀 이 판정에 못 쓴다.
        "wp_revision_no": wp_revision_no,
        "workflow_doc_id": owner_doc_id,
        "mode": mode,
        "plan_step_count": plan_step_count,
        "rows": [_public_row(row) for row in all_rows],
        "row_count_change": row_count_change(
            mode, locked_rows, pending_before, pour_rows, wp_item_seq,
        ),
        "notifications": build_notifications(
            rows=all_rows,
            overlap=overlap_types(before_uids, poured_uids, next_rows, poured_type_of),
            deleted_rows=deleted_rows,
            dropped=dropped,
            truncated_count=truncated_count,
        ),
        "workflow_tag": build_workflow_tag(sequence, items),
    }
