"""0061 T#4 execution ledger. Call requests under the Flow guard; all ownership writes are CAS."""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from modules.flow_gate.db.group_ai_leases import ACTIVE_HEARTBEAT_TTL_SEC
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services.graph_execution_hooks import ExecutionLeaseLost


class LeaseLost(ExecutionLeaseLost):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def deadline():
    return (datetime.now(timezone.utc) + timedelta(seconds=ACTIVE_HEARTBEAT_TTL_SEC)).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def get(exec_id):
    return run_store._one("SELECT * FROM graph_attempt_runs WHERE exec_id = ?", [exec_id])


def request(attempt_id, run_id, kind, payload):
    if kind not in ("write", "review", "rework"):
        raise ValueError(kind)
    if run_store._one("SELECT 1 AS present FROM graph_attempt_runs WHERE attempt_id = ? "
                      "AND status IN ('requested', 'running')", [attempt_id]):
        raise LeaseLost("attempt already has an execution")
    exec_id = "exe_" + secrets.token_hex(12)
    count = run_store._affected(
        "INSERT INTO graph_attempt_runs (exec_id, attempt_id, run_id, kind, status, created_at, request_json) "
        "SELECT ?, ?, ?, ?, 'requested', ?, ? WHERE EXISTS "
        "(SELECT 1 FROM graph_node_attempts WHERE attempt_id = ? AND run_id = ? AND status = 'active' "
        "AND COALESCE(result_commit, '') = '' AND COALESCE(integration_status, 'none') IN ('', 'none'))",
        [exec_id, attempt_id, run_id, kind, now(), json.dumps(payload, ensure_ascii=False), attempt_id, run_id])
    if count != 1:
        raise LeaseLost("attempt is not active")
    return exec_id


def eligible(row):
    return bool(run_store._one(
        "SELECT 1 AS present FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
        "WHERE a.attempt_id = ? AND a.status = 'active' AND r.status IN ('running', 'paused')",
        [row["attempt_id"]]))


def acquire(exec_id, ai_run_id):
    token = secrets.token_hex(24)
    count = run_store._affected(
        "UPDATE graph_attempt_runs SET status = 'running', lease_token = ?, lease_expires_at = ?, ai_run_id = ? "
        "WHERE exec_id = ? AND status = 'requested' AND EXISTS "
        "(SELECT 1 FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
        "WHERE a.attempt_id = graph_attempt_runs.attempt_id AND a.status = 'active' "
        "AND r.status IN ('running', 'paused'))", [token, deadline(), ai_run_id, exec_id])
    if count != 1:
        raise LeaseLost("execution acquire refused")
    return token


def renew(exec_id, token):
    count = run_store._affected(
        "UPDATE graph_attempt_runs SET lease_expires_at = ?, lease_touch_seq = lease_touch_seq + 1 "
        "WHERE exec_id = ? AND lease_token = ? "
        "AND status = 'running' AND lease_expires_at > ? AND cancel_requested_at IS NULL "
        "AND EXISTS (SELECT 1 FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
        "WHERE a.attempt_id = graph_attempt_runs.attempt_id AND a.status = 'active' "
        "AND r.status IN ('running', 'paused'))", [deadline(), exec_id, token, now()])
    if count != 1:
        raise LeaseLost("execution renew refused")


def finish(exec_id, token, status, expected="running"):
    if status not in ("ended", "cancelled", "lost") or expected not in ("requested", "running"):
        raise ValueError((expected, status))
    if expected == "requested" and status != "cancelled":
        raise ValueError("unacquired executions can only be cancelled")
    token_sql = "lease_token IS NULL" if token is None else "lease_token = ?"
    params = [status, now(), exec_id, expected] + ([] if token is None else [token])
    extra = " AND lease_expires_at <= ?" if status == "lost" else ""
    if extra:
        params.append(now())
    count = run_store._affected(
        f"UPDATE graph_attempt_runs SET status = ?, ended_at = ? WHERE exec_id = ? AND status = ? "
        f"AND {token_sql}{extra}", params)
    if count != 1:
        raise LeaseLost("execution terminal update refused")


def end(exec_id, token):
    finish(exec_id, token, "ended")


def cancel(exec_id, token, expected="running"):
    finish(exec_id, token, "cancelled", expected)


def mark_lost(exec_id, token):
    finish(exec_id, token, "lost")


def request_cancel(exec_id, token):
    count = run_store._affected(
        "UPDATE graph_attempt_runs SET cancel_requested_at = ?, lease_touch_seq = lease_touch_seq + 1 "
        "WHERE exec_id = ? "
        "AND lease_token = ? AND status = 'running'", [now(), exec_id, token])
    if count != 1:
        raise LeaseLost("execution cancel request refused")


def cancel_ineligible(exec_id):
    """A losing launcher may only cancel an unowned request whose prerequisites disappeared."""
    return run_store._affected(
        "UPDATE graph_attempt_runs SET status = 'cancelled', ended_at = ? "
        "WHERE exec_id = ? AND status = 'requested' AND lease_token IS NULL AND NOT EXISTS "
        "(SELECT 1 FROM graph_node_attempts a JOIN graph_runs r ON r.run_id = a.run_id "
        "WHERE a.attempt_id = graph_attempt_runs.attempt_id AND a.status = 'active' "
        "AND r.status IN ('running', 'paused'))", [now(), exec_id])


def working(run_id):
    return bool(run_store._one("SELECT 1 AS present FROM graph_attempt_runs WHERE run_id = ? "
                              "AND status IN ('requested', 'running') LIMIT 1", [run_id]))


# 0061 T#7: the run-end hand-offs in flight (in-process). An execution that ended on its own and was owned to its end
# is followed by its finalize's run-end stage (the instruction approval and the result stage, the result's review or
# approval); until that stage is over the run is still worked on (``settle.working``), so it never goes idle between
# the execution's ledger end and what follows it. Cleared by the finalize (``handoff_end``) or after the TTL.
HANDOFF_TTL_SEC = 600
_handoffs: dict[str, tuple[str, float]] = {}            # exec_id -> (run_id, deadline)
_handoffs_lock = threading.Lock()


def begin_handoff(exec_id, run_id):
    with _handoffs_lock:
        _handoffs[exec_id] = (run_id, time.monotonic() + HANDOFF_TTL_SEC)


def end_handoff(exec_id):
    """-> the run whose hand-off ended, or None."""
    with _handoffs_lock:
        found = _handoffs.pop(exec_id, None)
    return found[0] if found else None


def handing_off(run_id):
    with _handoffs_lock:
        clock = time.monotonic()
        for exec_id, (owner, until) in list(_handoffs.items()):
            if until <= clock:
                _handoffs.pop(exec_id, None)
        return any(owner == run_id for owner, _until in _handoffs.values())
