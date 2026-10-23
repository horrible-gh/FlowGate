"""The decision history of a confirm gate (flowgate.v02.0085 T#1, D0007 §7.2 "결정 이력").

One ordered list per gate (Flow + node key) with two sources:

- the new decisions (``gate_decisions``): every round, with who decided, the comment, the status, the settings the
  decision froze, the effects of each phase and whether an archive checkpoint exists;
- the legacy run events: before migration 158 a decision was only the node event ``gate_confirmed`` /
  ``gate_rejected`` of the run (``graph_run_events``: actor, reason, time, event sequence). They are served as legacy
  entries. A new decision also records its own success / reject event, and that event names its decision: the event
  payload carries ``gate_decision_id`` (``decision_link``). Such an event is the decision's own and is never served as
  a legacy entry (``legacy_events`` skips it), however late it was written (an async confirm completes after
  ``accepted_at``); an event without the link is a real earlier event and stays, also on a node that has new decisions. Nothing else was recorded then, so the Git result, the archive result, the frozen sha and the settings
  are *unknown* (``None``, named in ``unknown``): a legacy confirm is never shown with a merge, a push or an archive
  it may not have had, and ``archived`` is false.

The order is the decision order: the time the decision was accepted (legacy: the event's time), then new decisions
after legacy entries of the same instant, then the id. A cursor is the position of the last entry of a page, so the
pages stay stable while newer decisions are added behind them.
"""
from __future__ import annotations

import base64
import json
from typing import Optional

from modules.flow_gate.db.connection import get_store

from flowgate.gate_decisions import repository

LEGACY_CAUSES = {"gate_confirmed": "confirmed", "gate_rejected": "rejected"}
SOURCE_DECISION, SOURCE_LEGACY = "gate_decision", "legacy_event"
# What a legacy entry cannot tell (nothing but the event itself was recorded).
LEGACY_UNKNOWN = ("attempt_id", "request_id", "settings", "settings_version", "frozen_sha", "target_remote",
                  "target_branch", "target_sha", "job_id", "effects")
DEFAULT_LIMIT, MAX_LIMIT = 50, 200
DECISION_LINK_KEY = "gate_decision_id"      # the key of a new decision's run event payload that names the decision


def decision_link(decision_id: str) -> dict:
    """The payload part a ``gate_confirmed`` / ``gate_rejected`` event of a new decision carries (its writer merges it
    into the event's ``payload_json``): the explicit link that keeps the event out of the legacy entries."""
    return {DECISION_LINK_KEY: decision_id}


def linked_decision_id(payload_json) -> Optional[str]:
    """The decision a run event payload names, or None for an event without the link (a legacy event)."""
    if not payload_json:
        return None
    try:
        payload = json.loads(payload_json) if isinstance(payload_json, (str, bytes)) else payload_json
    except (ValueError, TypeError):
        return None
    value = payload.get(DECISION_LINK_KEY) if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value else None

_RESULT = {repository.SUCCEEDED: "confirmed", repository.REJECTED: "rejected", repository.CANCELED: "canceled",
           repository.ABANDONED: "abandoned"}


def _result(status: str) -> str:
    return _RESULT.get(status, "in_progress")


def _entry_of_decision(row: dict) -> dict:
    checkpoint = repository.checkpoint_of_decision(row["decision_id"])
    effects = repository.effects_of(row["decision_id"])
    return {
        "decision_id": row["decision_id"], "source": SOURCE_DECISION, "run_id": row["run_id"],
        "node_key": row["node_key"], "attempt_id": row["attempt_id"], "revision_no": row["revision_no"],
        "decision": row["decision"], "result": _result(row["status"]), "status": row["status"],
        "decision_version": row["decision_version"], "actor_user_id": row["actor_user_id"],
        "comment": row["decision_comment"], "decided_at": row["accepted_at"], "completed_at": row["completed_at"],
        "request_id": row["request_id"], "settings_version": row["settings_version"], "settings": row.get("settings"),
        "frozen_sha": row["frozen_sha"], "target_remote": row["target_remote"], "target_branch": row["target_branch"],
        "target_sha": row["target_sha"], "job_id": row["job_id"], "last_error_code": row["last_error_code"],
        "previous_decision_id": row["previous_decision_id"],
        "superseded_by_decision_id": row["superseded_by_decision_id"], "time_machine_ref": row["time_machine_ref"],
        "close": ({"kind": row["close_kind"], "by": row["closed_by"], "reason": row["close_reason"],
                   "at": row["closed_at"]} if row["close_kind"] else None),
        "recovery_reset_required": row["recovery_reset_required"],
        "effects": [{"phase": e["phase"], "status": e["status"], "target_ref": e["target_ref"],
                     "sha_before": e["sha_before"], "sha_after": e["sha_after"], "archive_id": e["archive_id"],
                     "error_code": e["error_code"], "recovery_state": e["recovery_state"],
                     "recovery_ref": e["recovery_ref"], "evidence_version": e["evidence_version"],
                     "reviewed_evidence_version": e["reviewed_evidence_version"]} for e in effects],
        # The chip of a finished confirm: its own checkpoint exists. An accepted, failed or abandoned decision, or
        # the archive of an earlier round, never shows it.
        "archived": bool(checkpoint) and row["status"] == repository.SUCCEEDED,
        "archive_id": checkpoint["archive_id"] if checkpoint else None,
        "event_seq": None, "unknown": [],
    }


def entry(row: dict) -> dict:
    """The history entry of one stored decision (flowgate.v02.0085 T#2: the decision part of a gate decision answer)."""
    return _entry_of_decision(row)


def legacy_events(flow_id: str, node_key: str) -> list[dict]:
    """The gate's ``gate_confirmed`` / ``gate_rejected`` run events as legacy entries, oldest first. An event that
    names a new decision (``decision_link``) is that decision's, not a legacy entry."""
    store = get_store()
    if not (store.table_exists("graph_run_events") and store.table_exists("graph_runs")):
        return []
    rows = store._fetch_all(
        "SELECT e.run_id, e.event_seq, e.cause, e.actor_user_id, e.reason, e.created_at, e.payload_json "
        "FROM graph_run_events e JOIN graph_runs r ON r.run_id = e.run_id "
        "WHERE r.group_id = ? AND e.node_key = ? AND e.event_type = 'graph_node_state_changed' "
        "AND e.cause IN ('gate_confirmed', 'gate_rejected') ORDER BY e.created_at, e.run_id, e.event_seq",
        [flow_id, node_key]) or []
    out = []
    for raw in rows:
        row = dict(raw)
        if linked_decision_id(row.get("payload_json")) is not None:
            continue
        result = LEGACY_CAUSES[row["cause"]]
        out.append({
            "decision_id": f"legacy:{row['run_id']}:{row['event_seq']}", "source": SOURCE_LEGACY,
            "run_id": row["run_id"], "node_key": node_key, "attempt_id": None, "revision_no": None,
            "decision": "confirm" if result == "confirmed" else "reject", "result": result, "status": result,
            "decision_version": None, "actor_user_id": row["actor_user_id"], "comment": row["reason"],
            "decided_at": row["created_at"], "completed_at": row["created_at"], "request_id": None,
            "settings_version": None, "settings": None, "frozen_sha": None, "target_remote": None,
            "target_branch": None, "target_sha": None, "job_id": None, "last_error_code": None,
            "previous_decision_id": None, "superseded_by_decision_id": None, "time_machine_ref": None,
            "close": None, "recovery_reset_required": False,
            # Unknown, not "none": no Git / archive success is made up for an event that recorded none.
            "effects": None, "archived": False, "archive_id": None,
            "event_seq": int(row["event_seq"]), "unknown": list(LEGACY_UNKNOWN),
        })
    return out


def _position(entry: dict) -> tuple:
    return (entry["decided_at"] or "", 0 if entry["source"] == SOURCE_LEGACY else 1, entry["decision_id"])


def encode_cursor(entry: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(list(_position(entry))).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: Optional[str]) -> Optional[tuple]:
    """The position a cursor stands for, or None for the start; ``ValueError`` for a cursor this module did not make."""
    if not cursor:
        return None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        if not (isinstance(decoded, list) and len(decoded) == 3 and isinstance(decoded[0], str)
                and decoded[1] in (0, 1) and isinstance(decoded[2], str)):
            raise ValueError("bad cursor")
        return (decoded[0], decoded[1], decoded[2])
    except (ValueError, TypeError, UnicodeError) as exc:
        raise ValueError("invalid_cursor") from exc


def history(flow_id: str, node_key: str, *, cursor: Optional[str] = None, limit: int = DEFAULT_LIMIT) -> dict:
    """``{"items": [...], "next_cursor": str | None, "latest": entry | None}`` in decision order, one page behind
    ``cursor``. ``latest`` is the newest entry of the whole history (the current result), whatever the page."""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    after = decode_cursor(cursor)
    entries = legacy_events(flow_id, node_key) + [_entry_of_decision(r) for r in repository.of_node(flow_id, node_key)]
    entries.sort(key=_position)
    page = [e for e in entries if after is None or _position(e) > after][:limit + 1]
    more = len(page) > limit
    page = page[:limit]
    return {"items": page, "next_cursor": encode_cursor(page[-1]) if more and page else None,
            "latest": entries[-1] if entries else None}
