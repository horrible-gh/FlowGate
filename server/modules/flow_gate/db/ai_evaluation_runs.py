"""Durable Evaluation runs, ordered by project creation sequence."""
from __future__ import annotations

import hashlib
import re
import uuid

from modules.flow_gate.db.connection import get_store, now_iso

ACTIVE = ("pending", "running")
TERMINAL = ("completed", "failed", "cancelled")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SECRET_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|api[_ -]?key\s*[:=]\s*)[^\s,;]+")
FAILED_REASONS = {"provider_became_unavailable", "provider_launch_failed", "transport_failed",
                  "timeout", "execution_failed", "internal_error"}


class AlreadyRunning(Exception):
    def __init__(self, evaluation_id: str):
        self.evaluation_id = evaluation_id
        super().__init__(evaluation_id)


def _dedupe(project_id: str, provider_id: str, version: int, fingerprint: str) -> str:
    return hashlib.sha256(f"{project_id}\0{provider_id}\0{version}\0{fingerprint}".encode()).hexdigest()


def get(project_id: str, evaluation_id: str) -> dict | None:
    return get_store()._fetch_one(
        "SELECT * FROM ai_evaluation_runs WHERE project_id = ? AND evaluation_id = ?",
        [project_id, evaluation_id])


def create_pending(*, project_id: str, provider_id: str, provider_name: str | None,
                   fingerprint: str, version: int, created_by: str | None) -> dict:
    if not HEX64.fullmatch(fingerprint) or version < 1:
        raise ValueError("Invalid evaluation fingerprint")
    key = _dedupe(project_id, provider_id, version, fingerprint)
    store = get_store()
    for attempt in range(12):
        try:
            with store.transaction():
                active = store._fetch_one(
                    "SELECT evaluation_id FROM ai_evaluation_runs WHERE active_dedupe_key = ?", [key])
                if active:
                    raise AlreadyRunning(active["evaluation_id"])
                seq = store._fetch_one(
                    "SELECT COALESCE(MAX(project_run_seq), 0) AS max_seq FROM ai_evaluation_runs WHERE project_id = ?",
                    [project_id])["max_seq"] + 1
                evaluation_id = "aiev_" + uuid.uuid4().hex
                now = now_iso()
                store._execute("""INSERT INTO ai_evaluation_runs
                    (evaluation_id, project_id, project_run_seq, provider_id, provider_name_snapshot,
                     subject_fingerprint, subject_fingerprint_version, active_dedupe_key,
                     lifecycle, created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)""",
                    [evaluation_id, project_id, seq, provider_id, provider_name,
                     fingerprint, version, key, created_by, now, now])
            return get(project_id, evaluation_id)
        except AlreadyRunning:
            raise
        except Exception:
            # A competing insert may have won either the active key or the project
            # sequence. The failed transaction is gone; inspect from a new one.
            active = store._fetch_one(
                "SELECT evaluation_id FROM ai_evaluation_runs WHERE active_dedupe_key = ?", [key])
            if active:
                raise AlreadyRunning(active["evaluation_id"])
            if attempt == 11:
                raise
    raise RuntimeError("Evaluation sequence allocation failed")


def mark_running(project_id: str, evaluation_id: str) -> bool:
    now = now_iso()
    return get_store()._execute_affected(
        """UPDATE ai_evaluation_runs SET lifecycle = 'running', started_at = ?, updated_at = ?
           WHERE project_id = ? AND evaluation_id = ? AND lifecycle = 'pending'""",
        [now, now, project_id, evaluation_id]) == 1


def finish(project_id: str, evaluation_id: str, *, lifecycle: str, verdict: str | None = None,
           reason_code: str | None = None, diagnostic_summary: str | None = None) -> bool:
    if lifecycle not in TERMINAL or (lifecycle != "completed" and verdict is not None) or (lifecycle == "completed" and verdict not in ("suitable", "unsuitable")):
        raise ValueError("Invalid evaluation terminal state")
    allowed_reasons = (FAILED_REASONS if lifecycle == "failed" else
                       {"empty_response", "baseline_contract_not_met"} if verdict == "unsuitable" else
                       {"cancelled_by_user"} if lifecycle == "cancelled" else {None})
    if reason_code not in allowed_reasons:
        raise ValueError("Invalid evaluation reason code")
    now = now_iso()
    if diagnostic_summary is not None:
        diagnostic_summary = _SECRET_PATTERN.sub(
            lambda match: match.group(1) + "[redacted]", diagnostic_summary)[:2000]
    return get_store()._execute_affected("""UPDATE ai_evaluation_runs
        SET lifecycle = ?, verdict = ?, reason_code = ?, diagnostic_summary = ?,
            active_dedupe_key = NULL, finished_at = ?, updated_at = ?
        WHERE project_id = ? AND evaluation_id = ? AND lifecycle IN ('pending', 'running')""",
        [lifecycle, verdict, reason_code, diagnostic_summary, now, now, project_id, evaluation_id]) == 1


def latest(project_id: str, provider_id: str, fingerprint: str | None = None,
           version: int | None = None) -> dict | None:
    if fingerprint is None:
        return get_store()._fetch_one("""SELECT * FROM ai_evaluation_runs
            WHERE project_id = ? AND provider_id = ? ORDER BY project_run_seq DESC LIMIT 1""",
            [project_id, provider_id])
    return get_store()._fetch_one("""SELECT * FROM ai_evaluation_runs
        WHERE project_id = ? AND provider_id = ? AND subject_fingerprint_version = ?
          AND subject_fingerprint = ? ORDER BY project_run_seq DESC LIMIT 1""",
        [project_id, provider_id, version, fingerprint])


def history(project_id: str, *, provider_id: str | None = None, limit: int = 20,
            before_seq: int | None = None) -> list[dict]:
    if not 1 <= limit <= 101:
        raise ValueError("limit must be 1..101")
    where, params = ["project_id = ?"], [project_id]
    if provider_id is not None:
        where.append("provider_id = ?")
        params.append(provider_id)
    if before_seq is not None:
        where.append("project_run_seq < ?")
        params.append(before_seq)
    params.append(limit)
    return get_store()._fetch_all(
        "SELECT * FROM ai_evaluation_runs WHERE " + " AND ".join(where) +
        " ORDER BY project_run_seq DESC LIMIT ?", params)


def recover_orphans() -> int:
    """At boot no worker from the previous process can still own an active run."""
    now = now_iso()
    return get_store()._execute_affected("""UPDATE ai_evaluation_runs
        SET lifecycle = 'failed', verdict = NULL, reason_code = 'internal_error',
            diagnostic_summary = 'Evaluation interrupted by server restart.',
            active_dedupe_key = NULL, finished_at = ?, updated_at = ?
        WHERE lifecycle IN ('pending', 'running')""", [now, now])
