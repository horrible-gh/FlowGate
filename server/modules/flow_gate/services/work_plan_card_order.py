"""Card order on an existing sequence — flowgate.default.0649 T#2 (NR0003 O2-O5).

T#1 gave every WP card a ``card_id`` and every row the card it serves
(``source_wp_card_id``, classified for legacy rows by :mod:`work_plan_card_identity`).
This module is the one place that reads a plan's card order against those rows:

* :func:`check_started_prefix` — O2. The cards that already started (S, by identity, in
  the order they ran) must still be in the plan, as the plan's first cards, in the same
  order. Otherwise ``started_card_removed`` / ``card_identity_mismatch`` /
  ``order_conflicts_started``.
* :func:`reorder_layout` — O3/O4. Which unprotected rows of this plan serve which card,
  which ones serve no card any more (orphans), which foreign pending rows stay before or
  after the plan block, and whether one sits inside it (``foreign_rows_interleaved``).
* :func:`assert_plan_order` — O5's post-write check. After a plan-reflecting save the
  plan's rows, read by card id in sort_order, must be ``S + (plan cards without S)``,
  one instruction row per card with its automatic report right behind it. Anything
  else is :class:`PlanOrderViolation` and the save is rolled back.

Nothing here writes.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from modules.flow_gate.db.workflow_sequences import is_started_row, protected_row_ids
from modules.flow_gate.documents.constants import WORK_PLAN_STEP_TYPES

STARTED_PREFIX_CODES = ("started_card_removed", "card_identity_mismatch", "order_conflicts_started")


class PlanOrderBlocked(Exception):
    """A plan-reflecting write is refused before anything is written (O2 / O5 append)."""

    def __init__(self, code: str, wp_doc_id: str, detail: Optional[dict] = None):
        super().__init__(f"{code}:{wp_doc_id}")
        self.code = code
        self.wp_doc_id = wp_doc_id
        self.detail = dict(detail or {})


class PlanOrderViolation(Exception):
    """The rows a plan-reflecting write produced do not follow the plan's card order."""

    code = "plan_order_violation"

    def __init__(self, wp_doc_id: str, detail: dict):
        super().__init__(f"plan_order_violation:{wp_doc_id}:{detail.get('reason')}")
        self.wp_doc_id = wp_doc_id
        self.detail = dict(detail)


def _report_map() -> dict[str, str]:
    from modules.flow_gate.services.workflow_decision_service import AUTO_REPORT_MAP

    return dict(AUTO_REPORT_MAP)


def _server_assembled() -> frozenset:
    from modules.flow_gate.services.workflow_decision_service import SERVER_ASSEMBLED_REPORT_TYPES

    return frozenset(SERVER_ASSEMBLED_REPORT_TYPES)


def _code(row: dict) -> str:
    return str(row.get("type") or "").upper()


def _ordered(items: Iterable[dict]) -> list[dict]:
    return sorted(
        (dict(row) for row in items or [] if row.get("id") is not None),
        key=lambda row: (row.get("sort_order") or 0, row.get("id") or 0),
    )


def _is_retired(value: Any) -> bool:
    from modules.flow_gate.services.work_plan_card_identity import is_retired_value

    return is_retired_value(value)


# ── The plan side ────────────────────────────────────────────────────────────

def plan_cards(plan: dict) -> list[dict]:
    """The plan's cards that become sequence rows, in plan order.

    Same filter as ``plan_to_rows``: a result step makes no row of its own (its
    instruction's automatic report carries it), and TSR is assembled by the server.
    """
    report_types = set(_report_map().values())
    cards: list[dict] = []
    steps = [s for s in (plan or {}).get("steps") or [] if isinstance(s, dict)]
    result_of = {
        s.get("pair_key"): s for s in steps if s.get("pair_role") == "result" and s.get("pair_key")
    }
    for step in steps:
        code = str(step.get("type") or "").upper()
        if step.get("pair_role") == "result" or code in report_types:
            continue
        if code not in WORK_PLAN_STEP_TYPES or code in _server_assembled():
            continue
        if not step.get("card_id"):
            continue
        cards.append({
            "card_id": step.get("card_id"),
            "key": step.get("key"),
            "type": code,
            "step": step,
            "result_step": result_of.get(step.get("key")),
        })
    return cards


def started_card_ids(classification: Optional[dict]) -> list[str]:
    return list((classification or {}).get("started_card_ids") or [])


def _card_view(card: Optional[dict], card_id: str, row: Optional[dict] = None) -> dict:
    view = {
        "card_id": card_id,
        "key": card.get("key") if card else None,
        "type": card.get("type") if card else (_code(row) if row else None),
    }
    if row is not None:
        view["item_seq"] = row.get("item_seq")
        view["item_id"] = row.get("id")
    return view


# ── O2 ───────────────────────────────────────────────────────────────────────

def check_started_prefix(
    plan: dict, classification: Optional[dict], items: Iterable[dict],
) -> Optional[dict]:
    """O2 by identity. None when the plan may be reflected, else ``{code, cards, ...}``.

    S is the classification's ``started_card_ids`` — linked started rows only, in
    sort_order; retired rows and later started duplicates are not in it.
    """
    started = started_card_ids(classification)
    if not started:
        return None
    cards = plan_cards(plan)
    by_id = {card["card_id"]: card for card in cards}
    rows_info = (classification or {}).get("rows") or {}
    report_types = set(_report_map().values())
    started_row: dict[str, dict] = {}
    for row in _ordered(items):
        info = rows_info.get(row["id"])
        if (
            info and info.get("card_id") in started and info.get("started")
            and _code(row) not in report_types and info["card_id"] not in started_row
        ):
            started_row[info["card_id"]] = row
    removed = [card_id for card_id in started if card_id not in by_id]
    if removed:
        return {
            "code": "started_card_removed",
            "cards": [_card_view(None, card_id, started_row.get(card_id)) for card_id in removed],
        }
    mismatched = [
        card_id for card_id in started
        if card_id in started_row and _code(started_row[card_id]) != by_id[card_id]["type"]
    ]
    if mismatched:
        return {
            "code": "card_identity_mismatch",
            "cards": [
                _card_view(by_id[card_id], card_id, started_row.get(card_id))
                for card_id in mismatched
            ],
        }
    head = [card["card_id"] for card in cards[:len(started)]]
    if head != started:
        return {
            "code": "order_conflicts_started",
            "cards": [
                _card_view(by_id[card_id], card_id, started_row.get(card_id))
                for card_id in started
            ],
            "plan_head": [_card_view(by_id[card_id], card_id) for card_id in head],
        }
    return None


# ── O3 / O4 — which rows serve which card ────────────────────────────────────

def plan_block_ids(items: Iterable[dict], wp_doc_id: str) -> set[int]:
    """This plan's rows: its own rows plus the automatic report right behind one of them."""
    report_map = _report_map()
    block: set[int] = set()
    previous: Optional[dict] = None
    for row in _ordered(items):
        own = str(row.get("source_doc_id") or "") == wp_doc_id
        attached = (
            previous is not None and previous["id"] in block
            and report_map.get(_code(previous)) == _code(row)
        )
        if own or attached:
            block.add(row["id"])
        previous = row
    return block


def _row_card(row: dict, classification: Optional[dict]) -> Optional[str]:
    info = ((classification or {}).get("rows") or {}).get(row["id"])
    if info is not None:
        return info.get("card_id")
    value = row.get("source_wp_card_id")
    return None if not value or _is_retired(value) else value


def reorder_layout(
    plan: dict, items: Iterable[dict], wp_doc_id: str, classification: Optional[dict],
) -> dict:
    """Where every unprotected row goes when this plan is rewritten in card order (O3).

    Returns::

        {
          "entries": [{"kind": "foreign", "row"} | {"kind": "card", "card", "row", "report"}],
          "cards": {card_id: {"card", "row", "report", "started"}},
          "removed": [row, ...],          # unprotected plan rows serving no card (orphans)
          "interleaved": [row, ...],      # foreign pending rows inside the plan block
          "order_differs": bool,          # existing card rows are not in card order
          "order_differs_keys": [key, ...],
          "started": [card_id, ...],      # S
        }

    A card keeps its first unprotected linked row (and the report right behind it); every
    other unprotected row of this plan is removed. Protected rows never appear: they stay
    where they are.
    """
    ordered = _ordered(items)
    protected = protected_row_ids(ordered)
    report_map = _report_map()
    report_types = set(report_map.values())
    started = started_card_ids(classification)
    cards = plan_cards(plan)
    by_id = {card["card_id"]: card for card in cards}
    block = plan_block_ids(ordered, wp_doc_id)
    index = {row["id"]: i for i, row in enumerate(ordered)}

    chosen: dict[str, dict] = {}
    used: set[int] = set()
    for row in ordered:
        if row["id"] not in block or row["id"] in protected or _code(row) in report_types:
            continue
        card_id = _row_card(row, classification)
        card = by_id.get(card_id) if card_id else None
        if card is None or card_id in started or card_id in chosen or card["type"] != _code(row):
            continue
        report = None
        following = ordered[index[row["id"]] + 1] if index[row["id"]] + 1 < len(ordered) else None
        if (
            following is not None and following["id"] in block
            and following["id"] not in protected
            and report_map.get(_code(row)) == _code(following)
        ):
            report = following
            used.add(following["id"])
        chosen[card_id] = {"card": card, "row": row, "report": report}
        used.add(row["id"])

    unprotected = [row for row in ordered if row["id"] not in protected]
    plan_rows = [row for row in unprotected if row["id"] in block]
    removed = [row for row in plan_rows if row["id"] not in used]
    foreign = [row for row in unprotected if row["id"] not in block]
    positions = {row["id"]: i for i, row in enumerate(unprotected)}
    if plan_rows:
        first, last = positions[plan_rows[0]["id"]], positions[plan_rows[-1]["id"]]
        interleaved = [row for row in foreign if first < positions[row["id"]] < last]
        before = [row for row in foreign if positions[row["id"]] < first]
        after = [row for row in foreign if positions[row["id"]] > last]
    else:
        # Nothing of this plan is left to rewrite: its new rows go after the foreign rows
        # that came before the plan's own (protected) rows, i.e. where the block was.
        anchor = max(
            ((row.get("sort_order") or 0) for row in ordered if row["id"] in block),
            default=None,
        )
        interleaved = []
        if anchor is None:
            before, after = list(foreign), []
        else:
            before = [row for row in foreign if (row.get("sort_order") or 0) < anchor]
            after = [row for row in foreign if (row.get("sort_order") or 0) >= anchor]

    entries: list[dict] = [{"kind": "foreign", "row": row} for row in before]
    for card in cards:
        if card["card_id"] in started:
            continue
        picked = chosen.get(card["card_id"]) or {}
        entries.append({
            "kind": "card", "card": card,
            "row": picked.get("row"), "report": picked.get("report"),
        })
    entries.extend({"kind": "foreign", "row": row} for row in after)

    existing_order = [
        card_id for card_id, _ in sorted(chosen.items(), key=lambda kv: index[kv[1]["row"]["id"]])
    ]
    plan_order = [card["card_id"] for card in cards if card["card_id"] in chosen]
    differs = existing_order != plan_order
    return {
        "entries": entries,
        "cards": chosen,
        "removed": removed,
        "interleaved": interleaved,
        "order_differs": differs,
        "order_differs_keys": [by_id[c]["key"] for c in plan_order] if differs else [],
        "started": started,
    }


def started_card_rows(
    items: Iterable[dict], classification: Optional[dict],
) -> dict[str, dict]:
    """The representative started instruction row of each card in S (+ its report)."""
    started = set(started_card_ids(classification))
    rows_info = (classification or {}).get("rows") or {}
    report_map = _report_map()
    ordered = _ordered(items)
    found: dict[str, dict] = {}
    for i, row in enumerate(ordered):
        info = rows_info.get(row["id"])
        if not info or not info.get("started") or info.get("card_id") not in started:
            continue
        if _code(row) in set(report_map.values()) or info["card_id"] in found:
            continue
        following = ordered[i + 1] if i + 1 < len(ordered) else None
        report = (
            following
            if following is not None and report_map.get(_code(row)) == _code(following)
            else None
        )
        found[info["card_id"]] = {"row": row, "report": report}
    return found


# ── O5 — the post-write check ────────────────────────────────────────────────

def plan_pending_rows(items: Iterable[dict], wp_doc_id: str) -> list[dict]:
    """This plan's rows a save would still have to delete or keep (unprotected pending)."""
    ordered = _ordered(items)
    protected = protected_row_ids(ordered)
    block = plan_block_ids(ordered, wp_doc_id)
    return [row for row in ordered if row["id"] in block and row["id"] not in protected]


# The settings a pour row carries for its card: its own, and those of the automatic report
# right behind it. Two cards whose rows agree on all of them are the same row to a client.
_INSTRUCTION_SIGNATURE = (
    "note", "provider_id", "review_count", "reviewer_provider_id",
    "pre_instruction_text", "pre_instruction_attachment",
)
_REPORT_SIGNATURE = ("note", "provider_id", "review_count", "reviewer_provider_id")
_MISSING = object()


def _signature_value(field: str, value: Any) -> Any:
    if field == "note":
        from modules.flow_gate.services.work_plan_sequence_service import normalize_note

        return normalize_note(value)
    if field == "review_count":
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return value
    if field == "pre_instruction_attachment":
        return json.dumps(value, sort_keys=True) if isinstance(value, dict) else None
    return value or None


def _card_signatures(
    plan: dict, wp_doc_id: str, revision_no: int, provider_view: Optional[dict],
) -> dict[str, dict]:
    """What each card's pour rows carry, built the way the pour candidates build them."""
    from modules.flow_gate.services import work_plan_sequence_service as _seq

    rows, _dropped, _uid = _seq.plan_to_rows(
        plan, wp_doc_id, revision_no, provider_view=provider_view,
    )
    rows, _uid = _seq.attach_auto_rows(rows)
    signatures: dict[str, dict] = {}
    for row in rows:
        card_id = row.get("source_wp_card_id")
        if not card_id:
            continue
        side, fields = ("r", _REPORT_SIGNATURE) if row.get("is_auto") else ("i", _INSTRUCTION_SIGNATURE)
        signature = signatures.setdefault(card_id, {})
        for field in fields:
            signature[(side, field)] = _signature_value(field, row.get(field))
    return signatures


def _row_signature(item: dict, following: Optional[dict]) -> dict:
    """The settings a sent row states. A field the row does not send tells nothing."""
    signature = {
        ("i", field): _signature_value(field, item[field])
        for field in _INSTRUCTION_SIGNATURE if field in item
    }
    report = _report_map().get(_code(item))
    if following is not None and report and _code(following) == report:
        signature.update({
            ("r", field): _signature_value(field, following[field])
            for field in _REPORT_SIGNATURE if field in following
        })
    return signature


def infer_missing_card_ids(
    new_items: list[dict],
    plan: dict,
    wp_doc_id: str,
    revision_no: int,
    started: Iterable[str],
    provider_view: Optional[dict] = None,
) -> int:
    """Give a card id to pour rows sent without one (a client that predates card ids).

    Only rows that say they come from *this* plan at *this* revision, carry no
    ``item_id`` and no card id are touched. A row's place in the payload does not name its
    card -- the dialog lets a person move rows -- so a row is placed by what it carries:

    * a type with one candidate card (not started, not already named) gives it to the
      first such row of that type;
    * with more, a row gets the candidate whose candidate row carries the same settings
      (note, provider, review, brief, attachment -- its own and its report's). Candidates
      whose settings are identical are interchangeable and go in plan order. One row left
      over against one card left over gets that card (its settings were edited).

    A row matching candidates that differ, or rows left over against more than one card,
    cannot be placed without guessing: :class:`PlanOrderViolation` (``ambiguous_card``),
    raised before anything is written. Report rows are left alone: the report expansion
    copies the instruction's card. Returns how many rows were given an id. Whatever this
    cannot place stays without one, and :func:`assert_plan_order` refuses it.
    """
    report_types = set(_report_map().values())
    skip = set(started)
    items = list(new_items or [])
    named = {
        str(item.get("source_wp_card_id")) for item in items if item.get("source_wp_card_id")
    }
    queues: dict[str, list[str]] = {}
    keys: dict[str, Any] = {}
    for card in plan_cards(plan):
        keys[card["card_id"]] = card["key"]
        if card["card_id"] in skip or card["card_id"] in named:
            continue
        queues.setdefault(card["type"], []).append(card["card_id"])
    waiting: dict[str, list[tuple[dict, Optional[dict]]]] = {}
    for index, item in enumerate(items):
        code = _code(item)
        if (
            code in report_types
            or item.get("item_id") is not None
            or item.get("source_wp_card_id")
            or str(item.get("source_doc_id") or "") != wp_doc_id
        ):
            continue
        try:
            if int(item.get("source_revision_no")) != int(revision_no):
                continue
        except (TypeError, ValueError):
            continue
        following = items[index + 1] if index + 1 < len(items) else None
        waiting.setdefault(code, []).append((item, following))

    def ambiguous(code: str, candidates: list[str], rows: list[dict]) -> None:
        raise PlanOrderViolation(wp_doc_id, {
            "reason": "ambiguous_card", "type": code,
            "candidates": [{"card_id": c, "key": keys.get(c)} for c in candidates],
            "rows": [{"type": _code(row), "note": row.get("note")} for row in rows],
        })

    signatures: Optional[dict] = None
    filled = 0
    for code, rows in waiting.items():
        queue = queues.get(code) or []
        if len(queue) <= 1:
            if queue:
                rows[0][0]["source_wp_card_id"] = queue.pop(0)
                filled += 1
            continue
        if signatures is None:
            signatures = _card_signatures(plan, wp_doc_id, revision_no, provider_view)
        left: list[dict] = []
        for item, following in rows:
            stated = _row_signature(item, following)
            fits = [
                card_id for card_id in queue
                if all(
                    (signatures.get(card_id) or {}).get(key, _MISSING) == value
                    for key, value in stated.items()
                )
            ]
            if not fits:
                left.append(item)
                continue
            if any(signatures.get(card_id) != signatures.get(fits[0]) for card_id in fits[1:]):
                ambiguous(code, fits, [item])
            item["source_wp_card_id"] = fits[0]
            queue.remove(fits[0])
            filled += 1
        if left and queue:
            if len(left) != 1 or len(queue) != 1:
                ambiguous(code, list(queue), left)
            left[0]["source_wp_card_id"] = queue.pop(0)
            filled += 1
    return filled


def history_row_ids(classification: Optional[dict]) -> set[int]:
    """Protected rows the classification retired — history that belongs to no card."""
    return {
        int(item_id) for item_id, info in ((classification or {}).get("rows") or {}).items()
        if info.get("classification") == "retired"
    }


def assert_plan_order(
    items_after: Iterable[dict], plan: dict, wp_doc_id: str, started: Iterable[str],
    history_ids: Iterable[int] = (),
) -> None:
    """O5: this plan's rows after a reflecting write follow ``S + (cards without S)``.

    Rows of this plan are the rows whose ``source_doc_id`` is the plan. A protected row
    that holds a retired marker (a deleted card's history, an acknowledged unresolved
    row), or that the classification retired (``history_ids``: a later started duplicate
    of a card), is left out — it belongs to no card. Every other row
    must name a card of the plan; the instruction rows read in sort_order must be exactly
    the expected card list; every row must be of its card's type (an instruction row the
    card's type, a report row the report that type gets -- ``card_type_mismatch`` /
    ``report_type_mismatch``); each automatic report row must sit right behind its card's
    instruction row; a card that has not started has exactly one report row when its type
    has one. Raises :class:`PlanOrderViolation` with the first broken rule.
    """
    ordered = _ordered(items_after)
    protected = protected_row_ids(ordered)
    history = {int(x) for x in history_ids or ()}
    report_map = _report_map()
    report_types = set(report_map.values())
    started = list(started)
    cards = plan_cards(plan)
    card_ids = [card["card_id"] for card in cards]
    by_id = {card["card_id"]: card for card in cards}
    expected = started + [card_id for card_id in card_ids if card_id not in started]

    def fail(reason: str, **extra) -> None:
        raise PlanOrderViolation(wp_doc_id, {
            "reason": reason, "expected": [
                {"card_id": c, "key": (by_id.get(c) or {}).get("key")} for c in expected
            ], **extra,
        })

    instruction_cards: list[str] = []
    report_count: dict[str, int] = {}
    previous: Optional[dict] = None
    for row in ordered:
        own = str(row.get("source_doc_id") or "") == wp_doc_id
        value = row.get("source_wp_card_id")
        if not own:
            previous = row
            continue
        if row["id"] in protected and (_is_retired(value) or row["id"] in history):
            previous = row
            continue
        if not value or value not in by_id or _is_retired(value):
            fail("row_without_card", item_seq=row.get("item_seq"), type=_code(row),
                 source_wp_card_id=value)
        code = _code(row)
        card_type = by_id[value]["type"]
        if code in report_types:
            # A row runs as its own type: a report must be the one its card's type gets.
            if report_map.get(card_type) != code:
                fail("report_type_mismatch", item_seq=row.get("item_seq"), type=code,
                     card_id=value, card_type=card_type)
            parent_ok = (
                previous is not None
                and str(previous.get("source_doc_id") or "") == wp_doc_id
                and previous.get("source_wp_card_id") == value
                and report_map.get(_code(previous)) == code
            )
            if not parent_ok:
                fail("report_not_after_instruction", item_seq=row.get("item_seq"),
                     type=code, card_id=value)
            report_count[value] = report_count.get(value, 0) + 1
        else:
            if code != card_type:
                fail("card_type_mismatch", item_seq=row.get("item_seq"), type=code,
                     card_id=value, card_type=card_type)
            instruction_cards.append(value)
        previous = row
    if instruction_cards != expected:
        fail("order", actual=[
            {"card_id": c, "key": (by_id.get(c) or {}).get("key")} for c in instruction_cards
        ])
    for card in cards:
        if card["card_id"] in started or card["type"] not in report_map:
            continue
        if report_count.get(card["card_id"], 0) != 1:
            fail("report_count", card_id=card["card_id"], key=card["key"],
                 count=report_count.get(card["card_id"], 0))
