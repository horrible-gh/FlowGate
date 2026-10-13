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
"""
from __future__ import annotations

import json
from typing import Iterable, Optional

from modules.flow_gate.db.connection import get_store

from .layout_store import utc_now_ms

RUN_TABLE = "graph_runs"

_RUN_COLUMNS = (
    "run_id, group_id, revision_no, status, active_group_id, started_at, started_by, stopped_at, "
    "finished_at, last_event_seq, policy_source, policy_set_id, policy_set_version, policy_digest, updated_at"
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


# ── Runs (T15) / policy snapshot (T16) ───────────────────────────────────────

def active_run(flow_id: str) -> Optional[dict]:
    """Q-R8: the running or stopped run of the Flow."""
    if not available():
        return None
    return _one(f"SELECT {_RUN_COLUMNS} FROM graph_runs WHERE active_group_id = ?", [flow_id])


def run_by_id(run_id: str) -> Optional[dict]:
    """Q-R15."""
    if not available():
        return None
    return _one(f"SELECT {_RUN_COLUMNS} FROM graph_runs WHERE run_id = ?", [run_id])


def list_runs(flow_id: str, limit: int, offset: int) -> tuple[list[dict], int]:
    """Q-R14: newest first, and the total."""
    if not available():
        return [], 0
    rows = _all(f"SELECT {_RUN_COLUMNS} FROM graph_runs WHERE group_id = ? "
                "ORDER BY started_at DESC, run_id DESC LIMIT ? OFFSET ?", [flow_id, limit, offset])
    total = _one("SELECT COUNT(*) AS n FROM graph_runs WHERE group_id = ?", [flow_id]) or {}
    return rows, int(total.get("n") or 0)


def policy_entries(run_id: str) -> dict[str, dict]:
    """Q-R10: ``{policy_key: {value, impl_version}}``."""
    rows = _all("SELECT policy_key, policy_value, impl_version FROM graph_run_policy_entries WHERE run_id = ?",
                [run_id])
    return {r["policy_key"]: {"value": r["policy_value"], "impl_version": r["impl_version"]} for r in rows}


def active_policy_triples() -> list[dict]:
    """Q-R7: implementations named by running / stopped runs (start-up restore check)."""
    if not available():
        return []
    return _all("SELECT DISTINCT e.policy_key, e.policy_value, e.impl_version FROM graph_run_policy_entries e "
                "JOIN graph_runs r ON r.run_id = e.run_id WHERE r.active_group_id IS NOT NULL", [])


def create_run(run_id: str, flow_id: str, revision_no: int, by: Optional[str], snapshot: dict,
               node_keys: Iterable[str]) -> dict:
    """Q-W10: the run (running, active), its nine policy entries and one pending node run per node."""
    store = get_store()
    now = utc_now_ms()
    store._execute(
        "INSERT INTO graph_runs (run_id, group_id, revision_no, status, active_group_id, started_at, started_by, "
        "last_event_seq, policy_source, policy_set_id, policy_set_version, policy_digest, updated_at) "
        "VALUES (?, ?, ?, 'running', ?, ?, ?, 0, ?, ?, ?, ?, ?)",
        [run_id, flow_id, revision_no, flow_id, now, by, snapshot["source"], snapshot["set_id"],
         snapshot["set_version"], snapshot["digest"], now],
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


def set_run_status(run_id: str, expected: str, status: str) -> int:
    """Q-W17: stop (running -> stopped), resume (stopped -> running), complete (running -> completed)."""
    now = utc_now_ms()
    if status == "stopped":
        return _affected("UPDATE graph_runs SET status = 'stopped', stopped_at = ?, updated_at = ? "
                         "WHERE run_id = ? AND status = ?", [now, now, run_id, expected])
    if status == "running":
        return _affected("UPDATE graph_runs SET status = 'running', stopped_at = NULL, updated_at = ? "
                         "WHERE run_id = ? AND status = ?", [now, run_id, expected])
    if status == "completed":
        return _affected("UPDATE graph_runs SET status = 'completed', finished_at = ?, active_group_id = NULL, "
                         "updated_at = ? WHERE run_id = ? AND status = ?", [now, now, run_id, expected])
    raise ValueError(status)


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


# ── Token map (T18) ──────────────────────────────────────────────────────────

def map_token(token_id: str, run_id: str, node_key: str, stage: Optional[str] = None) -> None:
    """Q-W13 — after a successful issue, outside the dispatch savepoint. ``stage``: the card run stage an AI start's
    token is minted for (flowgate.v02.0056 T#2); None for the node's dispatch token."""
    get_store()._execute(
        "INSERT INTO graph_run_token_map (token_id, run_id, node_key, stage, mapped_at) VALUES (?, ?, ?, ?, ?)",
        [token_id, run_id, node_key, stage, utc_now_ms()],
    )


def mark_token_stage(token_id: str, stage: str) -> int:
    """flowgate.v02.0056 T#2: a token mapped with no stage (the node's dispatch token) was spent in ``stage``. A
    token's stage, once recorded, stays."""
    return _affected("UPDATE graph_run_token_map SET stage = ? WHERE token_id = ? AND stage IS NULL", [stage, token_id])


def node_run_by_token(token_id: str) -> Optional[dict]:
    """Q-R11 (with the token's ``stage``)."""
    if not available():
        return None
    return _one("SELECT run_id, node_key, stage FROM graph_run_token_map WHERE token_id = ?", [token_id])


def token_ids(run_id: str, node_key: str, skip_stage: Optional[str] = None) -> list[str]:
    """Q-R13, mapping order — without the tokens of ``skip_stage`` when it is given."""
    stage_sql = " AND (stage IS NULL OR stage <> ?)" if skip_stage is not None else ""
    params = [run_id, node_key, *([skip_stage] if skip_stage is not None else [])]
    rows = _all("SELECT token_id FROM graph_run_token_map WHERE run_id = ? AND node_key = ?"
                f"{stage_sql} ORDER BY mapped_at, token_id", params)
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
