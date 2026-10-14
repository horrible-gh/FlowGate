"""Run store: runs, policy snapshot entries, node runs, token map, run events, propagations.

DB0010 T15~T20 (migration 144_graph_runs.sql), queries §4.4 / §4.5 Q-R8~Q-R15 and Q-W10~Q-W17.
Every write here runs inside a Flow guard (L0009 §2.1). Conditional updates return the affected
row count; the caller asserts it (a mismatch is a rollback, DB0010 §5.2).

flowgate.v02.0056 T#2 (migration 148_graph_node_run_stage.sql): an AI node run records the stage of its card
run (``stage``: ``instruction`` while its instruction AI writes the card's instruction, ``result`` while its
run AI writes the node's result; NULL for a node no AI starts) and the instruction its result stage was
given (``instruction_doc_id``). When the instruction stage ends (the move to the result stage, or the stage's
failure) it records the registration boundary of the node's result (``result_event_floor``, ``mark_result_floor``):
a document the node's tokens registered up to it belongs to the instruction stage, never to the result. A node token
records the stage it belongs to (``graph_run_token_map.stage``): an AI start's token the stage its AI was started for,
the dispatch token (NULL when mapped) the instruction stage once a registration with it reached the result binder during
that stage (``mark_token_stage``). What an instruction token registered is never the node's result, whenever the
registration is consumed (the inbox contacts the Graph before it consumes the token) or committed.

flowgate.v02.0061 T#2 (migration 151_graph_run_state_attempts.sql): a run is ``running``, ``paused`` or ``idle``
(``idle_reason``) and keeps its Flow (``active_group_id``) when it goes idle; it goes on with the next step / full
command (``run_mode``, a step's targets in ``step_node_keys``). Rows written before are read as their new state
(``_normalize``: completed -> idle (completed), stopped -> paused) and a conditional status update matches the old
stored value as well; a legacy completed row gave its Flow up and takes it back only through ``claim_flow``. Every
opening of a node is an attempt (``graph_node_attempts``, one active per node) and a node token is mapped to the
attempt it was issued for (``graph_run_token_map.attempt_id``).
"""
from __future__ import annotations

import json
import secrets
from typing import Optional

from ..domain.graph_model import Graph
from ..domain.run_scope_ports import run_scope

from modules.flow_gate.db.connection import get_store

from .layout_store import utc_now_ms

RUN_TABLE = "graph_runs"

RUNNING, PAUSED, IDLE = "running", "paused", "idle"
MODE_STEP, MODE_FULL = "step", "full"
IDLE_COMPLETED = "completed"
# The stored values a status stands for: rows before migration 151 keep their old status.
_STORED_STATUS = {RUNNING: (RUNNING,), PAUSED: (PAUSED, "stopped"), IDLE: (IDLE, "completed")}
_LEGACY_STATUS = {"stopped": (PAUSED, None), "completed": (IDLE, IDLE_COMPLETED)}
ATTEMPT_ACTIVE, ATTEMPT_ENDED = "active", "ended"
ATTEMPT_ID_PREFIX = "att_"

_RUN_COLUMNS = (
    "run_id, group_id, revision_no, status, active_group_id, started_at, started_by, stopped_at, "
    "finished_at, last_event_seq, policy_source, policy_set_id, policy_set_version, policy_digest, updated_at, "
    "run_mode, idle_reason, step_node_keys"
)
_NODE_RUN_COLUMNS = (
    "run_id, node_key, status, result_doc_id, root_bound, opened_at, finished_at, dispatch_token_issued, "
    "dispatch_issued_to, dispatch_mention_id, dispatch_error_code, stage, instruction_doc_id, result_event_floor, updated_at"
)


def available() -> bool:
    """False until migration 144 is applied (callers then see "no run")."""
    return get_store().table_exists(RUN_TABLE)


def _one(sql: str, params: list) -> Optional[dict]:
    row = get_store()._fetch_one(sql, params)
    return dict(row) if row else None


def _all(sql: str, params: list) -> list[dict]:
    return [dict(r) for r in get_store()._fetch_all(sql, params) or []]


def _affected(sql: str, params: list) -> int:
    return get_store()._execute_affected(sql, params)


def _normalize(row: Optional[dict]) -> Optional[dict]:
    """0061 T#2: a run row as its state now reads — a legacy status as its new one, no mode as ``full`` and the step
    targets as a list (None for a full command)."""
    if row is None:
        return None
    if row.get("status") in _LEGACY_STATUS:
        status, reason = _LEGACY_STATUS[row["status"]]
        row["status"] = status
        row["idle_reason"] = row.get("idle_reason") or reason
    row["run_mode"] = row.get("run_mode") or MODE_FULL
    raw = row.get("step_node_keys")
    row["step_node_keys"] = [str(k) for k in json.loads(raw)] if raw else None
    return row


def completed(run: Optional[dict]) -> bool:
    """The run's targets are all done (idle with the reason ``completed``; a legacy ``completed`` row reads so)."""
    return bool(run) and run.get("status") == IDLE and run.get("idle_reason") == IDLE_COMPLETED


# ── Runs (T15) / policy snapshot (T16) ───────────────────────────────────────

def active_run(flow_id: str) -> Optional[dict]:
    """Q-R8: the run of the Flow — running, paused or idle (0061 T#2: an idle run keeps its Flow)."""
    if not available():
        return None
    return _normalize(_one(f"SELECT {_RUN_COLUMNS} FROM graph_runs WHERE active_group_id = ?", [flow_id]))


def current_run(flow_id: str) -> Optional[dict]:
    """The Flow holder, or its latest pre-151 completed run awaiting a new command.

    Reads do not claim the Flow; the command does so under its Flow guard. Never
    select an older completed run when a later run exists.
    """
    run = active_run(flow_id)
    if run is not None or not available():
        return run
    latest = _one(f"SELECT {_RUN_COLUMNS} FROM graph_runs WHERE group_id = ? "
                  "ORDER BY started_at DESC, run_id DESC LIMIT 1", [flow_id])
    if latest is not None and latest["status"] == "completed" and latest.get("active_group_id") is None:
        return _normalize(latest)
    return None


def run_by_id(run_id: str) -> Optional[dict]:
    """Q-R15."""
    if not available():
        return None
    return _normalize(_one(f"SELECT {_RUN_COLUMNS} FROM graph_runs WHERE run_id = ?", [run_id]))


def list_runs(flow_id: str, limit: int, offset: int) -> tuple[list[dict], int]:
    """Q-R14: newest first, and the total."""
    if not available():
        return [], 0
    rows = _all(f"SELECT {_RUN_COLUMNS} FROM graph_runs WHERE group_id = ? "
                "ORDER BY started_at DESC, run_id DESC LIMIT ? OFFSET ?", [flow_id, limit, offset])
    total = _one("SELECT COUNT(*) AS n FROM graph_runs WHERE group_id = ?", [flow_id]) or {}
    return [_normalize(r) for r in rows], int(total.get("n") or 0)


def policy_entries(run_id: str) -> dict[str, dict]:
    """Q-R10: ``{policy_key: {value, impl_version}}``."""
    rows = _all("SELECT policy_key, policy_value, impl_version FROM graph_run_policy_entries WHERE run_id = ?",
                [run_id])
    return {r["policy_key"]: {"value": r["policy_value"], "impl_version": r["impl_version"]} for r in rows}


def active_policy_triples() -> list[dict]:
    """Q-R7: implementations named by the Flows' runs — running, paused or idle (start-up restore check)."""
    if not available():
        return []
    return _all("SELECT DISTINCT e.policy_key, e.policy_value, e.impl_version FROM graph_run_policy_entries e "
                "JOIN graph_runs r ON r.run_id = e.run_id WHERE r.active_group_id IS NOT NULL", [])


def create_run(run_id: str, flow_id: str, revision_no: int, by: Optional[str], snapshot: dict,
               revision: Graph, mode: str = MODE_FULL) -> dict:
    """Q-W10: run, snapshot and pending rows for run_scope targets only.

    Excluded cards remain in the revision/projection without a node_run. Existing
    runs are not rewritten; completion independently ignores their excluded rows.
    0061 T#2: the run starts running with the command ``mode``; a step's targets are set by its command.
    """
    targets = run_scope(revision).targets
    node_keys = [key for key in revision.node_keys() if key in targets]
    store = get_store()
    now = utc_now_ms()
    store._execute(
        "INSERT INTO graph_runs (run_id, group_id, revision_no, status, active_group_id, started_at, started_by, "
        "last_event_seq, policy_source, policy_set_id, policy_set_version, policy_digest, updated_at, run_mode) "
        "VALUES (?, ?, ?, 'running', ?, ?, ?, 0, ?, ?, ?, ?, ?, ?)",
        [run_id, flow_id, revision_no, flow_id, now, by, snapshot["source"], snapshot["set_id"],
         snapshot["set_version"], snapshot["digest"], now, mode],
    )
    for key, entry in snapshot["entries"].items():
        store._execute(
            "INSERT INTO graph_run_policy_entries (run_id, policy_key, policy_value, impl_version) VALUES (?, ?, ?, ?)",
            [run_id, key, entry["value"], entry["impl_version"]],
        )
    for key in node_keys:
        store._execute(
            "INSERT INTO graph_node_runs (run_id, node_key, status, root_bound, updated_at) VALUES (?, ?, 'pending', 0, ?)",
            [run_id, key, now],
        )
    return run_by_id(run_id) or {}


def _expected(expected: str) -> tuple[str, list]:
    stored = _STORED_STATUS.get(expected, (expected,))
    return "status IN (" + ", ".join("?" for _ in stored) + ")", list(stored)


def set_run_status(run_id: str, expected: str, status: str, idle_reason: Optional[str] = None) -> int:
    """Q-W17 (0061 T#2): pause (running -> paused), go on (paused / idle -> running), go idle (running -> idle with
    ``idle_reason``). An idle run keeps its Flow (``active_group_id``); ``finished_at`` is when it went idle."""
    now = utc_now_ms()
    cond, stored = _expected(expected)
    if status == PAUSED:
        return _affected(f"UPDATE graph_runs SET status = 'paused', stopped_at = ?, updated_at = ? "
                         f"WHERE run_id = ? AND {cond}", [now, now, run_id, *stored])
    if status == RUNNING:
        return _affected(f"UPDATE graph_runs SET status = 'running', stopped_at = NULL, finished_at = NULL, "
                         f"idle_reason = NULL, updated_at = ? WHERE run_id = ? AND {cond}", [now, run_id, *stored])
    if status == IDLE:
        if not idle_reason:
            raise ValueError("an idle run needs its reason")
        return _affected(f"UPDATE graph_runs SET status = 'idle', idle_reason = ?, finished_at = ?, updated_at = ? "
                         f"WHERE run_id = ? AND {cond}", [idle_reason, now, now, run_id, *stored])
    raise ValueError(status)


def set_idle_reason(run_id: str, idle_reason: str) -> int:
    """0061 T#2: an idle run's reason after a node event while idle (an approval, a gate decision)."""
    return _affected("UPDATE graph_runs SET idle_reason = ?, updated_at = ? WHERE run_id = ? AND status = 'idle'",
                     [idle_reason, utc_now_ms(), run_id])


def set_run_command(run_id: str, mode: str, step_node_keys: Optional[list[str]]) -> int:
    """0061 T#2: the command a running run goes on with — ``mode`` and a step's targets (None: no step targets
    yet / a full command)."""
    keys = json.dumps(list(step_node_keys)) if step_node_keys is not None else None
    return _affected("UPDATE graph_runs SET run_mode = ?, step_node_keys = ?, updated_at = ? "
                     "WHERE run_id = ? AND status = 'running'", [mode, keys, utc_now_ms(), run_id])


def claim_flow(run_id: str, flow_id: str) -> int:
    """0061 T#2: a run written before migration 151 that completed gave its Flow up (``active_group_id`` NULL); going
    on, it takes the Flow back — this run only; the unique ``idx_graph_runs_active`` keeps one run per Flow."""
    return _affected("UPDATE graph_runs SET active_group_id = ?, updated_at = ? "
                     "WHERE run_id = ? AND group_id = ? AND active_group_id IS NULL",
                     [flow_id, utc_now_ms(), run_id, flow_id])


# ── Node runs (T17) ──────────────────────────────────────────────────────────

def node_runs(run_id: str) -> list[dict]:
    """Q-R9 first half, node_key order."""
    return _all(f"SELECT {_NODE_RUN_COLUMNS} FROM graph_node_runs WHERE run_id = ? ORDER BY node_key", [run_id])


def node_run_by_result(run_id: str, doc_id: str) -> Optional[dict]:
    """Q-R12."""
    return _one(f"SELECT {_NODE_RUN_COLUMNS} FROM graph_node_runs WHERE run_id = ? AND result_doc_id = ?",
                [run_id, doc_id])


def set_root_bound(run_id: str, node_key: str, status: str, doc_id: str) -> int:
    """L0009 §2.6.5 step 2: pending -> done | review with the bound root document, no dispatch."""
    now = utc_now_ms()
    return _affected(
        "UPDATE graph_node_runs SET status = ?, result_doc_id = ?, root_bound = 1, opened_at = NULL, "
        "finished_at = ?, updated_at = ? WHERE run_id = ? AND node_key = ? AND status = 'pending'",
        [status, doc_id, now if status == "done" else None, now, run_id, node_key],
    )


def transition_node_run(run_id: str, node_key: str, expected_status: str, expected_doc_id: Optional[str],
                        status: str, result_doc_id: Optional[str], finished: bool) -> int:
    """Q-W11: status and result document in one conditional update (NULL and ``= ?`` split)."""
    now = utc_now_ms()
    doc_cond = "result_doc_id IS NULL" if expected_doc_id is None else "result_doc_id = ?"
    params = [status, result_doc_id, now, run_id, node_key, expected_status]
    if expected_doc_id is not None:
        params.append(expected_doc_id)
    finished_sql = "finished_at = ?, " if finished else ""
    if finished:
        params.insert(2, now)
    return _affected(
        f"UPDATE graph_node_runs SET status = ?, result_doc_id = ?, {finished_sql}updated_at = ? "
        f"WHERE run_id = ? AND node_key = ? AND status = ? AND {doc_cond}",
        params,
    )


def open_node_run(run_id: str, node_key: str, dispatch: Optional[dict]) -> int:
    """Q-W12: pending -> open with the work dispatch result."""
    now = utc_now_ms()
    d = dispatch or {}
    return _affected(
        "UPDATE graph_node_runs SET status = 'open', opened_at = ?, dispatch_token_issued = ?, dispatch_issued_to = ?, "
        "dispatch_mention_id = ?, dispatch_error_code = ?, updated_at = ? "
        "WHERE run_id = ? AND node_key = ? AND status = 'pending'",
        [now, _flag(dispatch), d.get("issued_to"), d.get("mention_id"), d.get("error_code"), now, run_id, node_key],
    )


def set_dispatch(run_id: str, node_key: str, dispatch: Optional[dict]) -> int:
    """Re-dispatch / AI start failure on an open node (L0009 §2.6.6 / §2.6.7 step 3)."""
    d = dispatch or {}
    return _affected(
        "UPDATE graph_node_runs SET dispatch_token_issued = ?, dispatch_issued_to = ?, dispatch_mention_id = ?, "
        "dispatch_error_code = ?, updated_at = ? WHERE run_id = ? AND node_key = ? AND status = 'open'",
        [_flag(dispatch), d.get("issued_to"), d.get("mention_id"), d.get("error_code"), utc_now_ms(), run_id, node_key],
    )


def set_stage(run_id: str, node_key: str, stage: Optional[str], instruction_doc_id: Optional[str] = None) -> int:
    """flowgate.v02.0056 T#2: the card run stage of a node run (dispatch, the instruction stage's target, the
    move to the result stage). ``instruction_doc_id`` None keeps the recorded one."""
    doc_sql = "instruction_doc_id = ?, " if instruction_doc_id is not None else ""
    doc_params = [instruction_doc_id] if instruction_doc_id is not None else []
    params = [stage, *doc_params, utc_now_ms(), run_id, node_key]
    return _affected(
        f"UPDATE graph_node_runs SET stage = ?, {doc_sql}updated_at = ? WHERE run_id = ? AND node_key = ?", params)


def mark_result_floor(run_id: str, node_key: str) -> int:
    """flowgate.v02.0056 T#2: the end of the node's instruction stage, as a registration boundary — the last
    ``workflow_events`` id now (the inbox's ``token_consumed`` events are among them). A document the node's tokens
    registered at or before it was registered during the instruction stage and is never the node's result. The first
    boundary stays (a repeated end does not move it). It only sees registrations consumed by then: one still on its way
    is told by its token's stage (``mark_token_stage``, the AI start's mapping)."""
    last = _one("SELECT MAX(id) AS n FROM workflow_events", []) or {}
    return _affected(
        "UPDATE graph_node_runs SET result_event_floor = ?, updated_at = ? "
        "WHERE run_id = ? AND node_key = ? AND result_event_floor IS NULL",
        [int(last.get("n") or 0), utc_now_ms(), run_id, node_key])


def _flag(dispatch: Optional[dict]) -> Optional[int]:
    return None if dispatch is None else (1 if dispatch.get("token_issued") else 0)


# ── Node attempts (0061 T#2) ─────────────────────────────────────────────────

def open_attempt(run_id: str, node_key: str, revision_no: int) -> str:
    """A node is opened: its new active attempt. A second active attempt of the node breaks the unique
    ``(run_id, active_node_key)`` index (the step rolls back)."""
    attempt_id = ATTEMPT_ID_PREFIX + secrets.token_hex(12)
    get_store()._execute(
        "INSERT INTO graph_node_attempts (attempt_id, run_id, node_key, revision_no, status, active_node_key, "
        "created_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
        [attempt_id, run_id, node_key, revision_no, node_key, utc_now_ms()],
    )
    return attempt_id


def end_attempt(run_id: str, node_key: str) -> int:
    """The node's active attempt ends (the node is done). 0 when it has none (opened before migration 151)."""
    now = utc_now_ms()
    return _affected("UPDATE graph_node_attempts SET status = 'ended', active_node_key = NULL, ended_at = ? "
                     "WHERE run_id = ? AND node_key = ? AND status = 'active'", [now, run_id, node_key])


def active_attempt_id(run_id: str, node_key: str) -> Optional[str]:
    row = _one("SELECT attempt_id FROM graph_node_attempts WHERE run_id = ? AND node_key = ? AND status = 'active'",
               [run_id, node_key])
    return row["attempt_id"] if row else None


def has_active_attempts(run_id: str) -> bool:
    row = _one("SELECT COUNT(*) AS n FROM graph_node_attempts WHERE run_id = ? AND status = 'active'", [run_id]) or {}
    return int(row.get("n") or 0) > 0


def current_attempts(run_id: str) -> dict[str, str]:
    """``{node_key: attempt_id}``: the node's active attempt, else its last ended one (none: not opened since 151)."""
    rows = _all("SELECT attempt_id, node_key, status FROM graph_node_attempts WHERE run_id = ? "
                "AND status IN ('active', 'ended') ORDER BY created_at, attempt_id", [run_id])
    out: dict[str, str] = {}
    active: set[str] = set()
    for r in rows:
        if r["node_key"] in active:
            continue
        out[r["node_key"]] = r["attempt_id"]
        if r["status"] == ATTEMPT_ACTIVE:
            active.add(r["node_key"])
    return out


# ── Token map (T18) ──────────────────────────────────────────────────────────

def map_token(token_id: str, run_id: str, node_key: str, stage: Optional[str] = None) -> None:
    """Q-W13 — after a successful issue, outside the dispatch savepoint. ``stage``: the card run stage an AI start's
    token is minted for (flowgate.v02.0056 T#2); None for the node's dispatch token. 0061 T#2: the token is mapped
    to the node's active attempt (NULL for a node opened before migration 151)."""
    get_store()._execute(
        "INSERT INTO graph_run_token_map (token_id, run_id, node_key, stage, attempt_id, mapped_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [token_id, run_id, node_key, stage, active_attempt_id(run_id, node_key), utc_now_ms()],
    )


def mark_token_stage(token_id: str, stage: str) -> int:
    """flowgate.v02.0056 T#2: a token mapped with no stage (the node's dispatch token) was spent in ``stage``. A
    token's stage, once recorded, stays."""
    return _affected("UPDATE graph_run_token_map SET stage = ? WHERE token_id = ? AND stage IS NULL", [stage, token_id])


def authoring_token_map(group_id: str) -> dict[str, dict]:
    """``{token_id: mapping}`` of the Flow's attempt-mapped new/edit tokens (0061 T#8 authorship).

    0061 #15: the scope is read from the tokens row while it exists, else from the
    token's ``token_issued`` event (written in the same transaction as the row), so a
    token purged after expiry (``tokens.delete_expired``) still attributes its document.
    """
    rows = _all(
        "SELECT m.token_id, m.attempt_id, m.node_key, m.run_id, m.stage, t.action_scope FROM graph_run_token_map m "
        "JOIN graph_runs r ON r.run_id = m.run_id LEFT JOIN tokens t ON t.token_id = m.token_id "
        "WHERE r.group_id = ? AND m.attempt_id IS NOT NULL", [group_id])
    if any(r.get("action_scope") is None for r in rows):
        issued: dict[str, str] = {}
        for event in _all("SELECT metadata FROM workflow_events WHERE group_id = ? "
                          "AND event_type = 'token_issued'", [group_id]):
            try:
                data = json.loads(event.get("metadata") or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("token_id"):
                issued[data["token_id"]] = data.get("action_scope")
        for r in rows:
            if r.get("action_scope") is None:
                r["action_scope"] = issued.get(r["token_id"])
    return {r["token_id"]: r for r in rows if r.get("action_scope") in ("new", "edit")}


def registered_document_attempt(group_id: str, doc_id: str) -> Optional[dict]:
    """0061 T#8: durable authorship, never inferred from the current node/run.

    Keep ended/invalidated token mappings: approving history must not commit the
    group's unrelated work. Parse metadata in Python for all three SQL dialects.
    New/edit registrations share token_consumed; the latest mapped registration wins.
    """
    if not get_store().table_exists("graph_node_attempts"):
        return None
    mapped = authoring_token_map(group_id)
    if not mapped:
        return None
    for event in _all("SELECT metadata FROM workflow_events WHERE group_id = ? "
                      "AND event_type = 'token_consumed' ORDER BY id DESC", [group_id]):
        try:
            data = json.loads(event.get("metadata") or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("doc_id") == doc_id and data.get("token_id") in mapped:
            return mapped[data["token_id"]]
    return None


def node_run_by_token(token_id: str) -> Optional[dict]:
    """Q-R11 (with the token's ``stage``)."""
    if not available():
        return None
    return _one("SELECT run_id, node_key, stage FROM graph_run_token_map WHERE token_id = ?", [token_id])


def token_ids(run_id: str, node_key: str, skip_stage: Optional[str] = None) -> list[str]:
    """Q-R13, mapping order — without the tokens of ``skip_stage`` when it is given. 0061 T#7: never the tokens of
    an invalidated attempt (a Stop, a lost lease, a failed worktree): a node opened again takes nothing of them."""
    stage_sql = " AND (stage IS NULL OR stage <> ?)" if skip_stage is not None else ""
    params = [run_id, node_key, *([skip_stage] if skip_stage is not None else [])]
    rows = _all("SELECT token_id FROM graph_run_token_map WHERE run_id = ? AND node_key = ?"
                f"{stage_sql} AND (attempt_id IS NULL OR attempt_id NOT IN "
                "(SELECT attempt_id FROM graph_node_attempts WHERE status = 'invalidated')) "
                "ORDER BY mapped_at, token_id", params)
    return [r["token_id"] for r in rows]


def invalidated_token_ids(run_id: str, node_key: str) -> list[str]:
    """0061 T#7: the node's tokens of its invalidated attempts, mapping order — what an ended old attempt wrote with."""
    rows = _all("SELECT m.token_id FROM graph_run_token_map m JOIN graph_node_attempts a ON a.attempt_id = m.attempt_id "
                "WHERE m.run_id = ? AND m.node_key = ? AND a.status = 'invalidated' ORDER BY m.mapped_at, m.token_id",
                [run_id, node_key])
    return [r["token_id"] for r in rows]


# ── Run events (T19) / propagations (T20) ────────────────────────────────────

def append_event(run_id: str, seq: int, event_type: str, payload: dict, *, node_key: Optional[str],
                 from_status: Optional[str], to_status: str, cause: str, result_doc_id: Optional[str],
                 canvas_state: Optional[str], actor_user_id: Optional[str], reason: Optional[str]) -> None:
    """Q-W14: the event row, then ``last_event_seq`` moved from ``seq - 1`` to ``seq`` (asserted)."""
    now = utc_now_ms()
    get_store()._execute(
        "INSERT INTO graph_run_events (run_id, event_seq, event_type, node_key, from_status, to_status, cause, "
        "result_doc_id, canvas_state, actor_user_id, reason, payload_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, seq, event_type, node_key, from_status, to_status, cause, result_doc_id, canvas_state,
         actor_user_id, reason, json.dumps(payload, ensure_ascii=False, sort_keys=True), now],
    )
    moved = _affected("UPDATE graph_runs SET last_event_seq = ?, updated_at = ? WHERE run_id = ? AND last_event_seq = ?",
                      [seq, now, run_id, seq - 1])
    if moved != 1:
        raise AssertionError(f"event_seq gap on run {run_id} at {seq}")


def events(run_id: str) -> list[dict]:
    return _all("SELECT * FROM graph_run_events WHERE run_id = ? ORDER BY event_seq", [run_id])


def insert_propagation(run_id: str, seq: int, node_key: str, outcome: str, status: str) -> None:
    """Q-W15 — after the event it points at."""
    now = utc_now_ms()
    get_store()._execute(
        "INSERT INTO graph_propagations (run_id, source_event_seq, node_key, outcome, status, created_at, done_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [run_id, seq, node_key, outcome, status, now, now if status == "done" else None],
    )


def pending_propagations(run_id: str) -> list[dict]:
    """Q-R9 second half."""
    return _all("SELECT run_id, source_event_seq, node_key, outcome, status FROM graph_propagations "
                "WHERE run_id = ? AND status = 'pending' ORDER BY source_event_seq, node_key", [run_id])


def propagations(run_id: str) -> list[dict]:
    return _all("SELECT run_id, source_event_seq, node_key, outcome, status, done_at FROM graph_propagations "
                "WHERE run_id = ? ORDER BY source_event_seq", [run_id])


def mark_propagation_done(run_id: str, seq: int) -> int:
    """Q-W16: pending -> done once (L0009 E-35)."""
    return _affected("UPDATE graph_propagations SET status = 'done', done_at = ? "
                     "WHERE run_id = ? AND source_event_seq = ? AND status = 'pending'", [utc_now_ms(), run_id, seq])
