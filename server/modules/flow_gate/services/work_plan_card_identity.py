"""Which work-plan card a sequence row belongs to — flowgate.default.0649 T#1 (NR0003 O0).

A WP step carries a ``card_id`` that survives renumbering and revisions (§5.1), and every row
a pour writes from now on records it as ``workflow_sequence_items.source_wp_card_id``. Rows
poured before that column existed have ``NULL`` there, and the obvious shortcut — "the row
counted as T#2 belongs to the card whose key is T#2 today" — is wrong even without the new
reordering (§3.8, G9): lowering the T quantity deletes T#2, raising it again creates a *new*
card that gets the key T#2 back. A started row of the deleted card linked to the new card
would make the new card look started, and it would never run.

So a legacy row is classified, not guessed:

  1. Binding evidence ``(r_b, key_b)`` — which revision and key the row was bound to.
     E1  a started instruction row whose result document records ``source_wp_doc_id`` = this
         WP: that document's ``source_wp_revision_no``/``source_wp_step_key`` (what actually ran).
     E2  an automatic report row follows the instruction row right before it (sort_order).
     E3  otherwise ``r_b = source_revision_no`` and ``key_b`` is counted two ways — the N-th
         of its type among this WP's rows of revision r_b (the materializer's rule) and the
         N-th of its type in this WP's poured block (build_step_map's rule). Only an agreeing
         answer is evidence.
  2. Continuity — ``key_b`` must exist in every revision snapshot from ``r_b`` up to ``L``,
     the last revision whose stored body has no card ids. A gap means the card was deleted
     and the key reused: **retired**. A snapshot that is missing or unreadable means the
     link cannot be proven: **unresolved**.
  3. Against the current body — the legacy id of ``key_b`` at ``L`` is ``key_b`` itself; if
     the current body has that card the row is **linked**. If not, a started row is still
     recorded as linked (the person just removed a started card; O2 refuses that), and a
     pending row is an orphan.

Nothing here writes, except :func:`record_backfill`, which only ever touches
``source_wp_card_id`` (never position or result document) and refuses to settle an
unresolved started row without the person's acknowledgement (``legacy_card_unresolved``).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from modules.flow_gate.db import workflow_sequences as db_wfseq
from modules.flow_gate.db.workflow_sequences import is_started_row, protected_row_ids

_log = logging.getLogger(__name__)

RETIRED_PREFIX = "retired:"
ACK_LEGACY_CARD_UNRESOLVED = "legacy_card_unresolved"

LINKED = "linked"
RETIRED = "retired"
UNRESOLVED = "unresolved"
ORPHAN = "orphan"


class LegacyCardUnresolved(Exception):
    """A started legacy row cannot be proven to belong to any card (NR0003 O0).

    Raised by a reflecting write that was not acknowledged. Carries the rows so the caller
    can show them: position, type, result document, bound revision and candidate keys.
    """

    def __init__(self, wp_doc_id: str, rows: list[dict]):
        super().__init__(f"legacy_card_unresolved:{wp_doc_id}")
        self.wp_doc_id = wp_doc_id
        self.rows = rows


def retired_marker(revision_no: int, key: str) -> str:
    return f"{RETIRED_PREFIX}r{int(revision_no)}:{key}"


def unresolved_marker(row_id: int) -> str:
    return f"{RETIRED_PREFIX}unresolved:{int(row_id)}"


def is_retired_value(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(RETIRED_PREFIX)


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _report_types() -> dict[str, str]:
    from modules.flow_gate.services.workflow_decision_service import AUTO_REPORT_MAP

    return dict(AUTO_REPORT_MAP)


def _key_parts(key: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    if not isinstance(key, str) or "#" not in key:
        return None, None
    code, _, ordinal = key.partition("#")
    return code.upper(), _int(ordinal)


# ── Evidence E1: the result document's own provenance ────────────────────────

_PROVENANCE_RE = {
    "source_wp_doc_id": re.compile(r"(?m)^source_wp_doc_id:\s*(.+?)\s*$"),
    "source_wp_revision_no": re.compile(r"(?m)^source_wp_revision_no:\s*(.+?)\s*$"),
    "source_wp_step_key": re.compile(r"(?m)^source_wp_step_key:\s*(.+?)\s*$"),
}


def _frontmatter_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw.strip().strip('"').strip("'")


def read_result_provenance(result_doc_id: Optional[str]) -> Optional[dict]:
    """The WP provenance lines a materialized instruction document carries, or None.

    Same reading as ``documents._materialized_document_matches``: the lines are part of the
    stored file, not a column, so the file is read. Any failure is "no evidence", never an
    error — E3 is then the fallback.
    """
    if not result_doc_id:
        return None
    try:
        from modules.flow_gate.db import documents as db_documents
        from modules.flow_gate.documents.routers.documents import _document_file_path

        doc = db_documents.get_by_id(result_doc_id)
        if doc is None:
            return None
        content = _document_file_path(doc).read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — unreadable provenance is absent provenance
        return None
    found: dict[str, Any] = {}
    for name, pattern in _PROVENANCE_RE.items():
        match = pattern.search(content)
        if match:
            found[name] = _frontmatter_value(match.group(1))
    if not found.get("source_wp_doc_id") or not found.get("source_wp_step_key"):
        return None
    revision_no = _int(found.get("source_wp_revision_no"))
    if revision_no is None:
        return None
    return {
        "source_wp_doc_id": str(found["source_wp_doc_id"]),
        "source_wp_revision_no": revision_no,
        "source_wp_step_key": str(found["source_wp_step_key"]),
    }


# ── Revision bodies as stored ────────────────────────────────────────────────

def stored_revision_loader(wp_doc: dict) -> Callable[[int], Optional[dict]]:
    """Return ``load(revision_no) -> parsed stored body | None`` for one WP document.

    The *stored* JSON is returned (not load_body()'s filled view), because whether a
    revision stored card ids is exactly what decides ``L``. ``None`` means the snapshot is
    missing or unreadable — which the continuity check treats as "cannot prove".
    """
    from modules.flow_gate.db import document_revisions as db_revisions
    from modules.flow_gate.services import work_plan_service as wp

    doc_id = wp_doc.get("doc_id")
    project_id = wp_doc.get("project_id")
    current_no = _int(wp_doc.get("revision_no")) or 0
    cache: dict[int, Optional[dict]] = {}

    def _read(path) -> Optional[dict]:
        try:
            parsed = json.loads(Path(str(path)).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def load(revision_no: int) -> Optional[dict]:
        if revision_no in cache:
            return cache[revision_no]
        body: Optional[dict] = None
        try:
            if revision_no == current_no:
                body = _read(wp.plan_path_for_doc(wp_doc))
            else:
                row = db_revisions.get_single_by_doc_revision(doc_id, revision_no)
                if row is not None:
                    body = _read(wp.resolve_revision_snapshot(
                        row, project_id=project_id, doc_id=doc_id,
                    ))
        except Exception:  # noqa: BLE001 — a snapshot that cannot be read proves nothing
            body = None
        cache[revision_no] = body
        return body

    return load


def _quantity(body: dict, code: str) -> int:
    quantities = body.get("quantities") if isinstance(body, dict) else None
    entry = quantities.get(code) if isinstance(quantities, dict) else None
    count = entry.get("count") if isinstance(entry, dict) else None
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def _stores_card_ids(body: Optional[dict]) -> bool:
    from modules.flow_gate.services import work_plan_service as wp

    return wp.body_stores_card_ids(body)


def last_legacy_revision(
    current_no: int, load: Callable[[int], Optional[dict]],
) -> tuple[Optional[int], bool]:
    """``(L, provable)`` — the last revision whose stored body has no card ids.

    Walks down from the current revision. ``provable`` is False when a snapshot on the way
    cannot be read: then no revision range ending at L can be checked either.
    """
    for revision_no in range(current_no, -1, -1):
        body = load(revision_no)
        if body is None:
            return None, False
        if not _stores_card_ids(body):
            return revision_no, True
    return -1, True  # every revision stored card ids: nothing is legacy


# ── Classification ───────────────────────────────────────────────────────────

def _instruction_rows_with_counts(
    items: list[dict], wp_doc_id: str,
) -> tuple[dict[int, Optional[str]], dict[int, Optional[str]]]:
    """Two key estimates per WP row: same-revision N-th and poured-block N-th."""
    from modules.flow_gate.services.work_plan_apply_service import _poured_block

    by_revision: dict[int, Optional[str]] = {}
    seen_revision: dict[tuple[str, int], int] = {}
    for item in items:  # sort_order — the materializer walks get_sequence_items()
        if str(item.get("source_doc_id") or "") != wp_doc_id:
            continue
        revision_no = _int(item.get("source_revision_no"))
        code = str(item.get("type") or "").upper()
        if revision_no is None:
            by_revision[item["id"]] = None
            continue
        seen_revision[(code, revision_no)] = seen_revision.get((code, revision_no), 0) + 1
        by_revision[item["id"]] = f"{code}#{seen_revision[(code, revision_no)]}"

    by_block: dict[int, Optional[str]] = {}
    seen_block: dict[str, int] = {}
    for item in _poured_block(items, wp_doc_id) or []:  # item_seq — build_step_map's walk
        code = str(item.get("type") or "").upper()
        seen_block[code] = seen_block.get(code, 0) + 1
        by_block[item["id"]] = f"{code}#{seen_block[code]}"
    return by_revision, by_block


def _public_row(item: dict, position: int, **extra) -> dict:
    return {
        "item_id": item.get("id"),
        "item_seq": item.get("item_seq"),
        "sort_order": item.get("sort_order"),
        "position": position,
        "type": str(item.get("type") or "").upper(),
        "label": item.get("label") or "",
        "result_doc_id": item.get("result_doc_id"),
        **extra,
    }


def classify_rows(
    *,
    wp_doc: dict,
    plan: dict,
    items: Iterable[dict],
    revision_loader: Optional[Callable[[int], Optional[dict]]] = None,
    provenance_reader: Optional[Callable[[Optional[str]], Optional[dict]]] = None,
) -> dict:
    """Classify every row of this WP by card identity (read-only).

    Returns::

        {
          "rows": {item_id: {"classification", "card_id", "value", "evidence",
                             "r_b", "key_b", "candidate_keys", "started"}},
          "writes": [(item_id, value)],        # backfill a reflecting write may record
          "unresolved_started": [public row],  # blocks until acknowledged
          "retired_started": [public row],     # retired_plan_rows (info)
          "orphans": [public row],             # pending rows of no current card
          "started_card_ids": [card_id, ...],  # S, sort_order order, de-duplicated
        }

    ``card_id`` is the live card a row serves (None for retired/unresolved/orphan rows);
    ``value`` is what ``source_wp_card_id`` holds or would hold.
    """
    wp_doc_id = str(wp_doc.get("doc_id") or "")
    ordered = sorted(
        (dict(item) for item in items or [] if item.get("id") is not None),
        key=lambda row: (row.get("sort_order") or 0, row.get("id") or 0),
    )
    position_of = {row["id"]: index for index, row in enumerate(ordered, start=1)}
    report_map = _report_types()
    report_types = set(report_map.values())
    load = revision_loader or stored_revision_loader(wp_doc)
    read_provenance = provenance_reader or read_result_provenance
    current_no = _int(wp_doc.get("revision_no")) or 0
    live_cards = {
        step.get("card_id")
        for step in plan.get("steps") or []
        if isinstance(step, dict) and step.get("pair_role") != "result"
    }

    legacy_l: Optional[tuple[Optional[int], bool]] = None

    def _legacy_boundary() -> tuple[Optional[int], bool]:
        nonlocal legacy_l
        if legacy_l is None:
            legacy_l = last_legacy_revision(current_no, load)
        return legacy_l

    by_revision, by_block = _instruction_rows_with_counts(ordered, wp_doc_id)
    rows: dict[int, dict] = {}

    def _bind(item: dict) -> dict:
        """Steps 1-3 for an instruction/single row."""
        started = is_started_row(item)
        evidence = "E3"
        candidate_keys: list[str] = []
        r_b: Optional[int] = None
        key_b: Optional[str] = None
        provenance = read_provenance(item.get("result_doc_id")) if started else None
        if provenance and provenance.get("source_wp_doc_id") == wp_doc_id:
            evidence = "E1"
            r_b = provenance["source_wp_revision_no"]
            key_b = provenance["source_wp_step_key"]
            candidate_keys = [key_b]
        else:
            r_b = _int(item.get("source_revision_no"))
            estimates = [by_revision.get(item["id"]), by_block.get(item["id"])]
            candidate_keys = sorted({key for key in estimates if key})
            if r_b is not None and estimates[0] and estimates[0] == estimates[1]:
                key_b = estimates[0]
        base = {
            "evidence": evidence, "r_b": r_b, "key_b": key_b,
            "candidate_keys": candidate_keys, "started": started,
        }
        code, ordinal = _key_parts(key_b)
        if r_b is None or key_b is None or code is None or ordinal is None or r_b > current_no:
            return {**base, "classification": UNRESOLVED, "card_id": None, "value": None}

        bound_body = load(r_b)
        if bound_body is None:
            return {**base, "classification": UNRESOLVED, "card_id": None, "value": None}
        if _stores_card_ids(bound_body):
            # Bound in a revision that already stored ids: the id is explicit there.
            step = next(
                (s for s in bound_body.get("steps") or []
                 if isinstance(s, dict) and s.get("key") == key_b),
                None,
            )
            card_id = step.get("card_id") if step else None
            if not card_id:
                return {**base, "classification": UNRESOLVED, "card_id": None, "value": None}
        else:
            boundary, provable = _legacy_boundary()
            if not provable or boundary is None or boundary < r_b:
                return {**base, "classification": UNRESOLVED, "card_id": None, "value": None}
            for revision_no in range(r_b, boundary + 1):
                body = load(revision_no)
                if body is None:
                    return {**base, "classification": UNRESOLVED, "card_id": None, "value": None}
                if _quantity(body, code) < ordinal:
                    marker = retired_marker(r_b, key_b)
                    return {**base, "classification": RETIRED, "card_id": None, "value": marker}
            card_id = key_b  # the legacy id of key_b at L
        if card_id in live_cards or started:
            return {**base, "classification": LINKED, "card_id": card_id, "value": card_id}
        return {**base, "classification": ORPHAN, "card_id": None, "value": None}

    previous: Optional[dict] = None
    for item in ordered:
        own = str(item.get("source_doc_id") or "") == wp_doc_id
        code = str(item.get("type") or "").upper()
        stored = item.get("source_wp_card_id")
        started = is_started_row(item)
        if not own:
            previous = item
            continue
        if stored:
            if is_retired_value(stored):
                rows[item["id"]] = {
                    "classification": RETIRED, "card_id": None, "value": stored,
                    "evidence": "stored", "r_b": None, "key_b": None,
                    "candidate_keys": [], "started": started,
                }
            else:
                live = stored in live_cards or started
                rows[item["id"]] = {
                    "classification": LINKED if live else ORPHAN,
                    "card_id": stored if live else None,
                    "value": stored, "evidence": "stored", "r_b": None, "key_b": None,
                    "candidate_keys": [], "started": started,
                }
        elif code in report_types:
            parent = rows.get(previous.get("id")) if previous is not None else None
            parent_ok = (
                previous is not None
                and str(previous.get("source_doc_id") or "") == wp_doc_id
                and report_map.get(str(previous.get("type") or "").upper()) == code
                and parent is not None
            )
            if parent_ok:
                rows[item["id"]] = {
                    **parent, "evidence": "E2", "started": started,
                }
            else:
                rows[item["id"]] = {
                    "classification": UNRESOLVED, "card_id": None, "value": None,
                    "evidence": "E2", "r_b": _int(item.get("source_revision_no")), "key_b": None,
                    "candidate_keys": [], "started": started,
                }
        else:
            rows[item["id"]] = _bind(item)
        previous = item

    # One card, several linked rows (a past append poured it twice): a started row wins over
    # pending ones, the earliest started row is the card's row, and later started rows are
    # kept as history — protected, not part of S.
    first_started: dict[str, int] = {}
    for item in ordered:
        info = rows.get(item["id"])
        if info is None or info["classification"] != LINKED or item.get("type") is None:
            continue
        if str(item.get("type") or "").upper() in report_types:
            continue
        card_id = info["card_id"]
        if info["started"]:
            if card_id in first_started:
                marker = retired_marker(info.get("r_b") or 0, info.get("key_b") or card_id)
                info.update({"classification": RETIRED, "card_id": None, "value": marker,
                             "duplicate_of": first_started[card_id]})
            else:
                first_started[card_id] = item["id"]
    seen_pending: set[str] = set()
    for item in ordered:
        info = rows.get(item["id"])
        if info is None or info["classification"] != LINKED or info["started"]:
            continue
        if str(item.get("type") or "").upper() in report_types:
            continue
        card_id = info["card_id"]
        if card_id in first_started or card_id in seen_pending:
            info.update({"classification": ORPHAN, "card_id": None, "value": None})
        else:
            seen_pending.add(card_id)
    # Reports follow their (possibly re-classified) instruction once more.
    previous = None
    for item in ordered:
        info = rows.get(item["id"])
        code = str(item.get("type") or "").upper()
        if (
            info is not None and info.get("evidence") == "E2" and previous is not None
            and previous["id"] in rows
        ):
            parent = rows[previous["id"]]
            info.update({
                "classification": parent["classification"], "card_id": parent["card_id"],
                "value": parent["value"], "r_b": parent.get("r_b"), "key_b": parent.get("key_b"),
                "candidate_keys": parent.get("candidate_keys", []),
            })
        previous = item

    writes: list[tuple[int, str]] = []
    unresolved_started: list[dict] = []
    retired_started: list[dict] = []
    orphans: list[dict] = []
    started_card_ids: list[str] = []
    protected = protected_row_ids(ordered)
    for item in ordered:
        info = rows.get(item["id"])
        if info is None:
            continue
        kept = item["id"] in protected
        position = position_of[item["id"]]
        if info["classification"] == UNRESOLVED and kept:
            unresolved_started.append(_public_row(
                item, position, r_b=info.get("r_b"), candidate_keys=info.get("candidate_keys", []),
            ))
        elif info["classification"] == RETIRED and kept:
            retired_started.append(_public_row(item, position, marker=info["value"]))
        elif info["classification"] in (ORPHAN, UNRESOLVED, RETIRED) and not kept:
            orphans.append(_public_row(item, position))
        if (
            info["classification"] == LINKED and info["started"]
            and str(item.get("type") or "").upper() not in report_types
            and info["card_id"] not in started_card_ids
        ):
            started_card_ids.append(info["card_id"])
        # Only rows that stay are worth recording; a pending orphan is deleted by the rewrite.
        if (
            kept and info["evidence"] != "stored" and info["value"]
            and info["classification"] in (LINKED, RETIRED)
        ):
            writes.append((item["id"], info["value"]))
    return {
        "rows": rows,
        "writes": writes,
        "unresolved_started": unresolved_started,
        "retired_started": retired_started,
        "orphans": orphans,
        "started_card_ids": started_card_ids,
    }


def acknowledged(codes: Optional[Iterable[str]]) -> bool:
    return ACK_LEGACY_CARD_UNRESOLVED in {str(code) for code in (codes or [])}


def record_backfill(
    sequence_id: int,
    classification: dict,
    *,
    wp_doc_id: str,
    acknowledged_codes: Optional[Iterable[str]] = None,
) -> list[tuple[int, str]]:
    """Write the classification's backfill — ``source_wp_card_id`` only (NR0003 O0).

    Must run inside the caller's reflecting-write transaction. An unresolved started row
    blocks with :class:`LegacyCardUnresolved` unless ``legacy_card_unresolved`` was
    acknowledged; then it is recorded as ``retired:unresolved:{row id}`` — history that
    belongs to no card, so the card it might have been is poured again — and is never asked
    about again.
    """
    unresolved = classification.get("unresolved_started") or []
    if unresolved and not acknowledged(acknowledged_codes):
        raise LegacyCardUnresolved(wp_doc_id, unresolved)
    writes = list(classification.get("writes") or [])
    writes.extend((row["item_id"], unresolved_marker(row["item_id"])) for row in unresolved)
    for item_id, value in writes:
        db_wfseq.update_sequence_item_card_id(item_id, sequence_id, value)
    return writes
