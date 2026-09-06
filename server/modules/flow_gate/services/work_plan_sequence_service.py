"""Work-plan → workflow-sequence converter (flowgate.default.0399 D0010 §2 / L0011).

D0010 §2 lists this as a component of its own: "plan-to-sequence converter (new) ... kept
separate rather than bolted onto the screen". That separation is the whole reason the module exists — a later group
is meant to drive the same conversion with no screen at all (D0010 [DEFERRED], last row), so
nothing in here may reach for a request, a session, or a rendering concern.

What it does: turn an approved work plan's step list into *candidate* sequence rows, merge
them into the current sequence the way the chosen mode says, and report what the person
should know before saving. It never writes. Pouring only produces the edit dialog's starting
state; the sequence changes when a human presses [save] (D0010 §3.3).

Everything numeric or set-valued here is L0011 §1; the algorithms are L0011 §2.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.document_type_labels import get_type_name
from modules.flow_gate.documents.constants import STEP_NOTE_MAX_CHARS
from modules.flow_gate.services.work_plan_apply_service import build_workflow_tag
from modules.flow_gate.services.workflow_decision_service import (
    AUTO_REPORT_MAP,
    SERVER_ASSEMBLED_REPORT_TYPES,
    plan_revision_freshness,
    provider_view_of,
    resolve_row_provider,
)
from modules.flow_gate.services.work_plan_service import PROVIDER_ID_PATTERN

# ── L0011 §1.1 — numeric parameters ──────────────────────────────────────────
# 0406 T0022 item 6: the value is read from the canonical one in documents.constants. The name
# is kept so existing callers (tests included) can still ask via wpseq.NOTE_MAX_CHARS.
NOTE_MAX_CHARS = STEP_NOTE_MAX_CHARS
PROVIDER_NAME_MAX_CHARS = 191
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
_log = logging.getLogger(__name__)

# L0011 §2.10 / P0013 ① — one envelope per code; the order below is the order the dialog
# reads them in, so it is fixed here rather than left to the caller.
# M0020: approval status is deliberately absent from this list. A plan still under review is
# poured exactly like an approved one; whether that is acceptable is decided by whoever presses [save].
_NOTIFICATION_ORDER = (
    "type_not_placeable",
    "provider_not_registered",
    "rows_truncated",
    "type_overlap",
    "notes_discarded",
    "note_missing",
    "paired_note_dropped",
    "server_assembled_note",
)
_SEVERITY = {
    "type_not_placeable": "warning",
    "provider_not_registered": "warning",
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
    """The one-line note being saved exceeds the cap (0406 T0022 item 6 / M0019).

    The old behaviour was a silent truncation: a user could save 1,000 characters and only 200
    survived, while the screen reported success. It now rejects instead of truncating, carrying
    the fact in the same shape the work-plan save already uses for ``note_too_long`` (code + max + actual length).
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

    "Absent" and "empty string" are not distinguished (L0011 §2.2): both come back as "". Keeping the
    distinction would flip the stored value on every save round-trip for no visible gain.

    0406 T0022 item 6 — **no silent truncation.** The old implementation dropped characters past
    the cap without a word and the save appeared to succeed. Nothing is truncated now:

    * ``strict=True`` (the save path) raises :class:`NoteTooLong` and rejects past the cap.
    * ``strict=False`` (display paths such as reads and prompt assembly) returns it as is. A
      reader must not blow up over legacy data, and a read that shrinks the value would be the
      same kind of silent loss.
    """
    if raw is None or not isinstance(raw, str):
        return ""
    text = "".join(ch for ch in raw if ch not in _CONTROL_CHARS)
    text = text.strip()
    if strict and len(text) > NOTE_MAX_CHARS:
        raise NoteTooLong(len(text))
    return text


def normalize_provider(raw_id: Any, raw_name: Any) -> tuple[Optional[str], Optional[str]]:
    """Normalize a provider pair read from a plan or an existing sequence row."""
    provider_id = None
    if isinstance(raw_id, str):
        provider_id = "".join(ch for ch in raw_id if ch not in _CONTROL_CHARS).strip() or None
    provider_name = None
    if isinstance(raw_name, str):
        provider_name = "".join(ch for ch in raw_name if ch not in _CONTROL_CHARS).strip() or None
        if provider_name:
            provider_name = provider_name[:PROVIDER_NAME_MAX_CHARS]
    if provider_id is None:
        if provider_name:
            _log.warning("dropping workflow sequence provider name without an id")
        return None, None
    if not PROVIDER_ID_PATTERN.fullmatch(provider_id):
        _log.warning("dropping malformed workflow sequence provider id: %r", raw_id)
        return None, None
    return provider_id, provider_name


def _provider_unavailable(provider_id: Optional[str], provider_view: Any) -> bool:
    """Would the apply path refuse this id? (0444 NR0003 §4-4)

    Deliberately the same condition, word for word, as
    ``work_plan_apply_service._usable_provider``: an id the registry does not know, or a
    row switched off through either flag. Pouring used to check only the id *format*, so
    the same unregistered id sailed through the pour screen, landed in the sequence, and
    was stopped only later on apply. The two paths disagreeing IS the defect, so they are
    written to the one rule here.

    Fail-open on purpose. ``provider_view`` follows the three-valued contract
    ``workflow_decision_service.provider_view_of`` returns: with no view, or a view that
    could not be read (``readable`` is not True), nothing is refused. "The settings were
    unreadable" is not a reason to erase what a person chose.
    """
    if provider_id is None:
        return False
    if not isinstance(provider_view, dict) or provider_view.get("readable") is not True:
        return False
    row = (provider_view.get("providers") or {}).get(str(provider_id))
    if row is None:
        return True
    return row.get("enabled", True) is False or row.get("is_enabled", True) is False


def resolve_step_provider(
    step: dict, plan: dict, *, provider_view: Optional[dict] = None,
) -> tuple[Optional[str], Optional[str]]:
    provider_id = step.get("provider_id")
    provider_name = step.get("provider_display_name")
    if provider_id and not provider_name:
        for candidate in plan.get("provider_candidates") or []:
            if candidate.get("provider_id") == provider_id:
                provider_name = candidate.get("display_name")
                break
    provider_id, provider_name = normalize_provider(provider_id, provider_name)
    # After the format check, never before it: a malformed id must keep being reported as
    # malformed rather than as "not registered".
    if _provider_unavailable(provider_id, provider_view):
        _log.warning("dropping unregistered workflow sequence provider id: %r", provider_id)
        return None, None
    return provider_id, provider_name


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
    provider_id: Optional[str] = None,
    provider_display_name: Optional[str] = None,
    pair_provider_id: Optional[str] = None,
    pair_provider_display_name: Optional[str] = None,
    pair_note: str = "",
    pair_note_source: Optional[str] = None,
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
        "provider_id": provider_id,
        "provider_display_name": provider_display_name,
        "pair_provider_id": pair_provider_id,
        "pair_provider_display_name": pair_provider_display_name,
        # 0408 TR0021 rev1: the note the plan wrote on the RESULT step, held on the
        # instruction row only until attach_auto_rows builds that result row (§2.4). It is
        # never this row's own note — the two steps carry two different sentences.
        "pair_note": pair_note,
        "pair_note_source": pair_note_source,
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
    they are the structural reason "finished rows and the row currently running are never deleted
    by any mode" holds without a single guard clause (L0011 §2.5).
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
        provider_id, provider_name = normalize_provider(
            item.get("provider_id"), item.get("provider_display_name")
        )
        row = _new_row(
            uid,
            code,
            locale,
            is_auto=origin == "auto",
            note=normalize_note(item.get("note")),
            origin=origin,
            source_doc_id=item.get("source_doc_id"),
            source_revision_no=_int(item.get("source_revision_no")),
            provider_id=provider_id,
            provider_display_name=provider_name,
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
    """Hold the result step's note for the automatic row that will carry it (§2.4).

    0408 M0019 re-rejection ("why is the TR/NR mention using T/N's?"): this used to move the
    note onto the INSTRUCTION row — and, if that row already had one, throw it away
    (paired_note_dropped). Both outcomes made the row that actually runs under [auto approve]
    (NR/TR) show a sentence somebody wrote for a different step, or none at all. The note now
    travels to its own row exactly the way pair_provider_id already does, so N/NR and T/TR
    keep two independent notes and neither can overwrite the other.
    """
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
    target["pair_note"] = note
    target["pair_note_source"] = "step"


def _carry_provider_to_pair(result_step: dict, rows: list[dict], plan: dict,
                            provider_view: Optional[dict] = None) -> None:
    provider_id, provider_name = resolve_step_provider(
        result_step, plan, provider_view=provider_view,
    )
    if provider_id is None:
        return
    pair_key = result_step.get("pair_key")
    target = None
    for row in rows:
        if row.get("plan_key") == pair_key:
            target = row
    if target is None:
        _log.warning("workflow plan provider pair not found for %s", result_step.get("key"))
        return
    target["pair_provider_id"] = provider_id
    target["pair_provider_display_name"] = provider_name


def plan_to_rows(
    plan: dict,
    plan_doc_id: str,
    plan_revision_no: Optional[int],
    locale: str = "ko",
    start_uid: int = 0,
    *,
    provider_view: Optional[dict] = None,
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
            # The result step's provider rides to its partner row, so it faces the same
            # check — and owes the same visible reason when it fails.
            pair_id, _pair_name = resolve_step_provider(step, plan)
            if _provider_unavailable(pair_id, provider_view):
                dropped.append({
                    "plan_key": step.get("key"), "type": code,
                    "reason": "provider_not_registered", "provider_id": pair_id,
                })
            _carry_provider_to_pair(step, rows, plan, provider_view=provider_view)
            continue
        uid += 1
        step_note = normalize_note(step.get("note"))
        provider_id, provider_name = resolve_step_provider(step, plan)
        if _provider_unavailable(provider_id, provider_view):
            dropped.append({
                "plan_key": step.get("key"), "type": code,
                "reason": "provider_not_registered", "provider_id": provider_id,
            })
            # Left as None on purpose: the default substitution below then fires and the
            # plan's common provider takes the place of the one that does not exist.
            provider_id, provider_name = None, None
        rows.append(_new_row(
            uid, code, locale,
            note=step_note,
            note_source="step" if step_note else None,
            origin="plan",
            plan_key=step.get("key"),
            source_doc_id=plan_doc_id,
            source_revision_no=plan_revision_no,
            provider_id=provider_id,
            provider_display_name=provider_name,
        ))

    defaults = plan.get("defaults")
    default_note = normalize_note(defaults.get("note")) if isinstance(defaults, dict) else ""
    default_provider_id, default_provider_name = (
        resolve_step_provider({"provider_id": defaults.get("provider_id")}, plan)
        if isinstance(defaults, dict)
        else (None, None)
    )
    if _provider_unavailable(default_provider_id, provider_view):
        dropped.append({
            "plan_key": "defaults", "type": None,
            "reason": "provider_not_registered", "provider_id": default_provider_id,
        })
        default_provider_id, default_provider_name = None, None
    for row in rows:
        if row["provider_id"] is None:
            if row["type"] in INSTRUCTION_TYPES and row.get("pair_provider_id"):
                row["provider_id"] = row.get("pair_provider_id")
                row["provider_display_name"] = row.get("pair_provider_display_name")
            elif default_provider_id is not None:
                row["provider_id"] = default_provider_id
                row["provider_display_name"] = default_provider_name
        if default_note and row["note"] == "":
            row["note"] = default_note
            row["note_source"] = "defaults"
        # The automatic row is a step of its own (and the only one an auto-approved run hands
        # to a worker), so the plan's common note reaches it on the same terms.
        if default_note and row["type"] in INSTRUCTION_TYPES and not row.get("pair_note"):
            row["pair_note"] = default_note
            row["pair_note_source"] = "defaults"
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
        # TSR is assembled by the server: no provider and no note may be written for it.
        # 0444 T0007 (NR0003 §2-7): that rule is stated once now, as
        # SERVER_ASSEMBLED_REPORT_TYPES, and workflow_decision_service.expand_steps_with_reports()
        # reads the same name. It used to spell "TSR" out on its own and commit 178b21b2
        # (0434 T0004 F1) moved only the provider half of it, so the note this function refuses
        # to write was being written on the decision/edit path.
        server_assembled = want in SERVER_ASSEMBLED_REPORT_TYPES
        pair_note = "" if server_assembled else (row.get("pair_note") or "")
        pair_note_source = row.get("pair_note_source") if pair_note else None
        if server_assembled:
            provider_id, provider_name = None, None
        elif row.get("pair_provider_id"):
            provider_id = row.get("pair_provider_id")
            provider_name = row.get("pair_provider_display_name")
        else:
            provider_id = row.get("provider_id")
            provider_name = row.get("provider_display_name")
        out.append(_new_row(
            auto_uid, want, locale,
            is_auto=True,
            auto_of_uid=row["uid"],
            # 0408 TR0021 rev1: an automatic row carries its own note too. Under [auto approve]
            # this is the very row the AI worker runs on, so what the plan wrote for this step has nowhere else to ride.
            note=pair_note,
            note_source=pair_note_source,
            origin="auto",
            # 0434 T0004 F1/F2: automatic reports belong to the same poured plan
            # revision as their instruction, so they participate in freshness checks too.
            source_doc_id=row.get("source_doc_id"),
            source_revision_no=row.get("source_revision_no"),
            provider_id=provider_id,
            provider_display_name=provider_name,
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

    Only types, never positions: what a person sees is "there are two DS rows", not "DS#2
    collides" (L0011 §2.9). Deleted rows cannot overlap with anything.
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
    added = len(pour_rows)              # automatic rows count too — a person sees them as rows
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

    unregistered = [d for d in dropped if d.get("reason") == "provider_not_registered"]
    if unregistered:
        found["provider_not_registered"] = _envelope(
            "provider_not_registered", len(unregistered),
            items=[
                {"plan_key": d.get("plan_key"), "provider_id": d.get("provider_id")}
                for d in unregistered
            ],
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

def _public_row(row: dict, provider_view: dict, plan_doc: dict) -> dict:
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
        **plan_revision_freshness(
            row.get("source_doc_id"), row.get("source_revision_no"), known_plan_doc=plan_doc
        ),
        **resolve_row_provider(
            row.get("provider_id"), row.get("provider_display_name"), provider_view
        ),
    }


def build_candidates(*, doc: dict, plan: dict, mode: str, locale: str = "ko") -> dict:
    """Build the edit dialog's starting state for one work plan and one mode.

    Reads only. Nothing here changes the sequence, and nothing changes the plan — "applying
    only reads the plan" (D0010 §4).
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
    # 0444 NR0003 §4-4: read once, here, because the conversion below now needs it to
    # refuse an unregistered provider. _public_row further down reuses this same value —
    # the settings are not read twice for one response.
    provider_view = provider_view_of(doc.get("project_id"))
    plan_rows, dropped, next_uid = plan_to_rows(
        plan, wp_doc_id, wp_revision_no, locale, start_uid=next_uid,
        provider_view=provider_view,
    )
    plan_step_count = len(plan_rows)
    pour_rows, next_uid = attach_auto_rows(plan_rows, locale, next_uid)

    truncated_count = 0
    if len(pour_rows) > POUR_ROWS_MAX:
        # Automatic rows are trimmed together with their parent so the pairing cannot break (L0011 §5).
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
        # 0403 NR0004 F2 — the plan revision, previously only on the rows, now also rides the
        # response header. Sending this value back on save is what lets the server decide
        # "did the plan change after the dialog opened". A row's source_revision_no keeps trailing along after a save and cannot serve that purpose.
        "wp_revision_no": wp_revision_no,
        "workflow_doc_id": owner_doc_id,
        "mode": mode,
        "plan_step_count": plan_step_count,
        "rows": [_public_row(row, provider_view, doc) for row in all_rows],
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


def expand_final_work_plan(*, doc: dict, plan: dict, locale: str = "ko") -> dict:
    """Persist a final WP through the shared sequence-edit SSOT exactly once."""
    if str(doc.get("doc_review_status") or "") != "approved":
        return {"status": "skipped", "reason": "not_final"}
    wp_doc_id = str(doc.get("doc_id") or "")
    revision_no = _int(doc.get("revision_no"), 0)
    owner_doc_id = doc.get("target_id") or doc.get("triggered_by")
    if not wp_doc_id or not owner_doc_id:
        return {"status": "skipped", "reason": "missing_workflow_owner"}
    sequence = db_wfseq.get_sequence_by_doc_id(owner_doc_id)
    existing = list(db_wfseq.get_sequence_items(sequence["id"]) or []) if sequence else []
    if any(str(row.get("source_doc_id") or "") == wp_doc_id and _int(row.get("source_revision_no"), -1) == revision_no for row in existing):
        return {"status": "skipped", "reason": "already_applied", "revision_no": revision_no}
    candidate = build_candidates(doc=doc, plan=plan, mode="append", locale=locale)
    if not candidate.get("plan_step_count"):
        return {"status": "skipped", "reason": "no_placeable_steps", "revision_no": revision_no}
    pending_rows = [{key: row.get(key) for key in ("type", "label", "note", "source_doc_id", "source_revision_no", "provider_id", "provider_display_name")} for row in candidate["rows"] if row.get("status") == "pending"]
    from modules.flow_gate.services.workflow_decision_service import edit_workflow_pending
    result = edit_workflow_pending(owner_doc_id, pending_rows, expected_workflow_tag=candidate["workflow_tag"], expected_plan={"wp_doc_id": wp_doc_id, "wp_revision_no": revision_no}, applied_by="wp_final_auto_expand", locale=locale)
    return {"status": "expanded", "revision_no": revision_no, "result": result}