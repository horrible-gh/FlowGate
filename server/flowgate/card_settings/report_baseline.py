"""The report baseline of an attempt (flowgate.v02.0113 0038-T T#3 A-2, 0002-D 개정 §3.6-0-2).

An attempt of a card that uses an instruction and opens with none written records the card's report as it is then —
its report line document and that document's content (revision, body digest, last body save) — as the attempt's
baseline. The result stage then takes only a report saved after the baseline (a person's save, an AI's write): the
baseline report stays as the card's history and is never deleted, and it never completes the card ("지시서 없음 · 레포트
있음" → the instruction first, then a new report).

- ``record_at_opening``: at the node's opening, inside the run step's Flow guard. Instruction not written: the report of
  this moment. Instruction written: the baseline the node's earlier attempt of the same run kept, while its report is
  still the line's (an attempt replaced by a command — a reinterpreted wait, a reopened failure — keeps the rule).
- ``advance_at_instruction``: the moment the attempt's own instruction is written (a person's save judged, an AI's
  instruction end), inside that step's Flow guard. The baseline moves to the report as it is now — a report saved
  while the instruction was still waiting was saved before the instruction and is no report of after it (0030-M
  §3.1 ⑥: the instruction first, then a report written anew). A report saved after this moment is the new report.
- ``is_baseline``: the document is the attempt's baseline report, unchanged since.

0038-T T#3 rework: a baseline the attempt needs is never assumed. A required record that cannot be made (no store —
migration 167 missing —, a failed read or insert) raises ``BaselineUnavailable`` and fails the opening with the run
step's guard: nothing of the step stays, the report is kept as it is, unregistered, and the next command opens the
node again once the store works. A baseline that cannot be read (no store, a failed read) raises too and is never
"no baseline": the stage takes no report while it is not known. One row per attempt: written at the opening (or the
carry), moved once more each time the attempt's instruction is written (``advance_at_instruction``).

0045-TR rework (flowgate.v02.0113 T#6 → T#3): the baseline of the opening alone let a report saved during the
instruction wait pass as a report of after the instruction (it differs from the opening's report), and the card was
completed with no report written after its instruction. The write order is kept by moving the baseline to the
instruction's write moment.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

from modules.flow_gate.db.connection import get_store

_log = logging.getLogger(__name__)

TABLE = "graph_attempt_report_baselines"
_COLUMNS = ("attempt_id", "run_id", "group_id", "node_key", "doc_id", "revision_no", "body_sha256", "body_event_id",
            "carried_from", "recorded_at")
_INSERT = f"INSERT INTO {TABLE} (" + ", ".join(_COLUMNS) + ") VALUES (" + ", ".join("?" for _ in _COLUMNS) + ")"


class BaselineUnavailable(RuntimeError):
    """The attempt's report baseline is needed but cannot be recorded or read — never "no baseline"."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def available() -> bool:
    """False until migration 167 is applied (the store is missing)."""
    return get_store().table_exists(TABLE)


def read(attempt_id: Optional[str]) -> Optional[dict]:
    """The attempt's baseline row, or None (none was recorded). No store or a failed read raises
    ``BaselineUnavailable`` (never "no baseline")."""
    if not attempt_id:
        return None
    try:
        if not available():
            raise BaselineUnavailable(f"{TABLE} is missing")
        return get_store()._fetch_one(f"SELECT * FROM {TABLE} WHERE attempt_id = ?", [attempt_id])
    except BaselineUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — a failed read is told apart from no baseline
        raise BaselineUnavailable(f"baseline read failed: {type(exc).__name__}") from exc


def _digest(doc: dict) -> Optional[str]:
    from modules.flow_gate.storage.body import body_read_state  # noqa: PLC0415 — the v0.1 body read on use

    try:
        state, text = body_read_state(doc)
    except (OSError, UnicodeError):
        _log.warning("report body not read doc=%s", doc.get("doc_id"), exc_info=True)
        return None
    if state != "readable" or text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _last_body_save(doc_id: str) -> Optional[int]:
    row = get_store()._fetch_one("SELECT MAX(event_id) AS last FROM events WHERE doc_id = ? "
                                 "AND event_type = 'doc_body_saved'", [doc_id])
    return int(row["last"]) if row and row.get("last") is not None else None


def fingerprint(doc: dict) -> dict:
    """What tells a later save of ``doc`` apart: its revision, its body digest and its last body save."""
    return {"doc_id": doc["doc_id"], "revision_no": int(doc.get("revision_no") or 0), "body_sha256": _digest(doc),
            "body_event_id": _last_body_save(doc["doc_id"])}


def _insert(attempt_id: str, run_id: str, flow_id: str, node_key: str, mark: dict, carried_from: Optional[str]) -> None:
    values = {"attempt_id": attempt_id, "run_id": run_id, "group_id": flow_id, "node_key": node_key,
              "doc_id": mark.get("doc_id"), "revision_no": mark.get("revision_no"),
              "body_sha256": mark.get("body_sha256"), "body_event_id": mark.get("body_event_id"),
              "carried_from": carried_from, "recorded_at": _now()}
    get_store().insert_isolated(_INSERT, [values[c] for c in _COLUMNS], "fg_report_baseline_ins")


def _earlier(run_id: str, node_key: str, attempt_id: str) -> Optional[dict]:
    return get_store()._fetch_one(f"SELECT * FROM {TABLE} WHERE run_id = ? AND node_key = ? AND attempt_id <> ? "
                                  "ORDER BY recorded_at DESC LIMIT 1", [run_id, node_key, attempt_id])


def record_at_opening(attempt_id: str, run_id: str, flow_id: str, node_key: str, instruction_written: bool,
                      report: Optional[dict], instruction_doc_id: Optional[str] = None) -> None:
    """Inside the opening's Flow guard. ``report``: the card's live report line document now (None: none).
    ``instruction_doc_id``: the written instruction the opening reuses (``instruction_written``).

    Instruction not written: the baseline is required — a missing store, a failed read or a failed insert raises
    ``BaselineUnavailable`` and the opening fails with the step (its guard rolls everything back; the report is never
    taken on an unknown baseline). Instruction written: the earlier attempt's baseline is carried while the report is
    unchanged — or changed by a save made before the instruction's last save (0045-TR rework: a report saved while
    no person's stage took the instruction, then the instruction, is no report of after it); a failed read raises, and
    with no store no earlier baseline can exist (nothing to carry)."""
    try:
        _record(attempt_id, run_id, flow_id, node_key, instruction_written, report, instruction_doc_id)
    except BaselineUnavailable:
        _log.warning("report baseline not recorded attempt=%s node=%s", attempt_id, node_key, exc_info=True)
        raise
    except Exception as exc:  # noqa: BLE001 — told as one failure: the opening does not go on without it
        _log.warning("report baseline not recorded attempt=%s node=%s", attempt_id, node_key, exc_info=True)
        raise BaselineUnavailable(f"baseline not recorded: {type(exc).__name__}") from exc


def _record(attempt_id: str, run_id: str, flow_id: str, node_key: str, instruction_written: bool,
            report: Optional[dict], instruction_doc_id: Optional[str] = None) -> None:
    if instruction_written and not available():
        return                                       # no store: no earlier attempt kept a baseline to carry
    if read(attempt_id) is not None:
        return
    if not instruction_written:
        if report is not None:
            _insert(attempt_id, run_id, flow_id, node_key, fingerprint(report), None)
        return
    earlier = _earlier(run_id, node_key, attempt_id)
    if earlier is None or not earlier.get("doc_id") or report is None or report["doc_id"] != earlier["doc_id"]:
        return
    if _same(earlier, report):
        _insert(attempt_id, run_id, flow_id, node_key, earlier, earlier["attempt_id"])
    elif _saved_before(report, instruction_doc_id):
        _insert(attempt_id, run_id, flow_id, node_key, fingerprint(report), earlier["attempt_id"])


def _saved_before(report: dict, instruction_doc_id: Optional[str]) -> bool:
    """The report's last body save comes before the instruction's last body save (every body write records its
    ``doc_body_saved`` event in its own save transaction; event ids grow). Either save unknown: False (the rule of the
    unchanged report alone)."""
    if not instruction_doc_id:
        return False
    report_saved, instruction_saved = _last_body_save(report["doc_id"]), _last_body_save(instruction_doc_id)
    return report_saved is not None and instruction_saved is not None and report_saved < instruction_saved


def advance_at_instruction(attempt_id: str, run_id: str, flow_id: str, node_key: str,
                           report: Optional[dict]) -> None:
    """Inside the Flow guard of the step that takes the attempt's own instruction as written (not one reused at the
    opening). ``report``: the card's live report line document now (None: none).

    The attempt's baseline becomes the report of this moment: the row of the opening is updated (its report, revision,
    body digest, last body save), or written now when the opening had no report. No report now and no row: nothing to
    keep (any report saved later is after the instruction). A report there that cannot be kept (no store, a failed read
    or write) raises ``BaselineUnavailable``: the step's guard rolls back and the instruction is judged again later —
    a report is never taken on an unknown order."""
    try:
        _advance(attempt_id, run_id, flow_id, node_key, report)
    except BaselineUnavailable:
        _log.warning("report baseline not moved attempt=%s node=%s", attempt_id, node_key, exc_info=True)
        raise
    except Exception as exc:  # noqa: BLE001 — told as one failure: the instruction does not go on without it
        _log.warning("report baseline not moved attempt=%s node=%s", attempt_id, node_key, exc_info=True)
        raise BaselineUnavailable(f"baseline not moved: {type(exc).__name__}") from exc


def _advance(attempt_id: str, run_id: str, flow_id: str, node_key: str, report: Optional[dict]) -> None:
    if not available():
        if report is None:
            return                                   # no store and no report: no row exists, nothing to keep
        raise BaselineUnavailable(f"{TABLE} is missing")
    row = read(attempt_id)
    if row is None:
        if report is not None:
            _insert(attempt_id, run_id, flow_id, node_key, fingerprint(report), None)
        return
    mark = fingerprint(report) if report is not None else {}
    get_store()._execute(f"UPDATE {TABLE} SET doc_id = ?, revision_no = ?, body_sha256 = ?, body_event_id = ?, "
                         "recorded_at = ? WHERE attempt_id = ?",
                         [mark.get("doc_id"), mark.get("revision_no"), mark.get("body_sha256"),
                          mark.get("body_event_id"), _now(), attempt_id])


def _same(row: dict, doc: dict) -> bool:
    now = fingerprint(doc)
    return (int(row.get("revision_no") or 0) == now["revision_no"] and row.get("body_sha256") == now["body_sha256"]
            and row.get("body_event_id") == now["body_event_id"])


def is_baseline(attempt_id: Optional[str], doc: Optional[dict]) -> bool:
    """``doc`` is the attempt's baseline report and nothing was saved to it since (its revision, body and last body
    save all as recorded). A document saved since, another document, or no baseline recorded: False. A baseline that
    cannot be read (no store, a failed read) raises ``BaselineUnavailable``: the caller takes no report."""
    if not doc or not attempt_id:
        return False
    row = read(attempt_id)
    if row is None or row.get("doc_id") != doc.get("doc_id"):
        return False
    return _same(row, doc)
