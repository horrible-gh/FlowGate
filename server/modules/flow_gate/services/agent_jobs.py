"""Durable Remote CLI jobs for authenticated pull Agents.

Only this service enqueues work; the existing provider CLI path is unchanged.
Each mutation uses a conditional update so a stale owner cannot change an attempt.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services.agent_registry import AgentError, AgentService

LEASE_TTL_SECONDS = 90
RENEW_AFTER_SECONDS = 30
OUTPUT_LIMIT_BYTES = 1024 * 1024
TERMINAL = frozenset({"DONE", "FAILED", "CANCELLED", "TIMED_OUT", "REJECTED"})


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _conflict(code, message):
    raise AgentError(409, code, message)


class AgentJobService:
    def __init__(self, store=None):
        self.s = store or get_store()

    def _get(self, job_id, attempt_id):
        row = self.s._fetch_one(
            "SELECT * FROM agent_jobs WHERE job_id=? AND attempt_id=?",
            [job_id, attempt_id],
        )
        if not row:
            raise AgentError(404, "job_not_found", "Agent Job not found")
        return row

    def _auth(self, credential, *, claim=False):
        identity = AgentService(self.s).authenticate(credential)
        row = self.s._fetch_one(
            """SELECT a.enabled,a.location,a.connection_mode,a.protocol_version,
                      a.protocol_compatibility,a.configured_ai_cli,a.reported_ai_cli
               FROM agents a JOIN agent_credentials c ON c.agent_id=a.agent_id
               WHERE c.credential_id=? AND c.revoked_at IS NULL AND a.agent_id=?""",
            [identity["credential_id"], identity["agent_id"]],
        )
        if not row:
            raise AgentError(401, "unauthorized", "Invalid Agent credential")
        if not row["enabled"]:
            raise AgentError(403, "agent_disabled", "Agent is disabled")
        if (row["location"] != "remote" or row["connection_mode"] != "agent_pull"
                or row["protocol_compatibility"] != "compatible" or row["protocol_version"] != 1):
            raise AgentError(403, "agent_incompatible", "Agent is not a compatible Remote Agent")
        if claim and not (row["configured_ai_cli"] and row["reported_ai_cli"]):
            raise AgentError(403, "capability_required", "Effective ai_cli capability is required")
        return identity["agent_id"]

    def enqueue_remote_cli(
        self, *, command_line, stdin_text, cwd, timeout_seconds,
        job_id=None, attempt_id=None, target_agent_id=None,
    ):
        if not isinstance(command_line, str) or not command_line.strip() or "\n" in command_line or "\r" in command_line:
            raise AgentError(422, "validation_failed", "command_line must be one nonempty line")
        if not isinstance(stdin_text, str) or not isinstance(cwd, str) or not cwd:
            raise AgentError(422, "validation_failed", "stdin_text and cwd are required")
        if type(timeout_seconds) is not int or timeout_seconds <= 0:
            raise AgentError(422, "validation_failed", "timeout_seconds must be positive")
        job_id = job_id or "job_" + secrets.token_urlsafe(20)
        attempt_id = attempt_id or "att_" + secrets.token_urlsafe(20)
        if (not isinstance(job_id, str) or not job_id or len(job_id) > 96
                or not isinstance(attempt_id, str) or not attempt_id or len(attempt_id) > 96
                or (target_agent_id is not None and (not isinstance(target_agent_id, str) or not target_agent_id))):
            raise AgentError(422, "validation_failed", "Invalid Job identity or target")
        payload = {"execution_type": "remote_cli", "command_line": command_line,
                   "stdin_text": stdin_text, "cwd": cwd, "timeout_seconds": timeout_seconds,
                   "target_agent_id": target_agent_id}
        fingerprint = _hash(payload)
        ts = _stamp(_now())
        with self.s.transaction():
            existing = self.s._fetch_one(
                "SELECT payload_digest,status FROM agent_jobs WHERE job_id=? AND attempt_id=?",
                [job_id, attempt_id],
            )
            if existing:
                if existing["payload_digest"] != fingerprint:
                    _conflict("attempt_payload_conflict", "Attempt payload cannot be overwritten")
                return {"job_id": job_id, "attempt_id": attempt_id, "status": existing["status"], "created": False}
            active = self.s._fetch_one(
                "SELECT 1 AS present FROM agent_jobs WHERE job_id=? AND status IN ('NEW','CLAIMED','STARTED')",
                [job_id],
            )
            if active:
                _conflict("job_attempt_active", "A Job attempt is already active")
            inserted = self.s._execute_affected(
                """INSERT INTO agent_jobs(job_id,attempt_id,execution_type,command_line,stdin_text,cwd,
                   timeout_seconds,status,target_agent_id,payload_digest,cancel_requested,
                   stdout_truncated,stderr_truncated,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,0,0,0,?,?) ON CONFLICT DO NOTHING""",
                [job_id, attempt_id, "remote_cli", command_line, stdin_text, cwd,
                 timeout_seconds, "NEW", target_agent_id, fingerprint, ts, ts],
            )
            if inserted != 1:
                existing = self.s._fetch_one(
                    "SELECT payload_digest,status FROM agent_jobs WHERE job_id=? AND attempt_id=?",
                    [job_id, attempt_id],
                )
                if existing:
                    if existing["payload_digest"] != fingerprint:
                        _conflict("attempt_payload_conflict", "Attempt payload cannot be overwritten")
                    return {"job_id": job_id, "attempt_id": attempt_id,
                            "status": existing["status"], "created": False}
                _conflict("job_attempt_active", "A Job attempt is already active")
        return {"job_id": job_id, "attempt_id": attempt_id, "status": "NEW", "created": True}

    def _reclaim(self, ts):
        self.s._execute_affected(
            """UPDATE agent_jobs SET status='NEW',lease_owner_agent_id=NULL,lease_token=NULL,
                   lease_expires_at=NULL,claimed_at=NULL,updated_at=?
                   WHERE status='CLAIMED' AND lease_expires_at<=?""",
            [ts, ts],
        )
        self.s._execute_affected(
            """UPDATE agent_jobs SET status='FAILED',end_reason='lease_expired',
                   diagnostics='Started Agent Job lease expired',finished_at=?,updated_at=?,
                   lease_expires_at=NULL WHERE status='STARTED' AND lease_expires_at<=?""",
            [ts, ts, ts],
        )

    def claim(self, credential):
        with self.s.transaction():
            agent_id = self._auth(credential, claim=True)
            ts = _stamp(_now())
            self._reclaim(ts)
            candidates = self.s._fetch_all(
                """SELECT job_id,attempt_id FROM agent_jobs
                   WHERE status='NEW' AND (target_agent_id IS NULL OR target_agent_id=?)
                   ORDER BY created_at,job_id,attempt_id""",
                [agent_id],
            )
            for candidate in candidates:
                token = "lsh_" + secrets.token_urlsafe(32)
                expiry = _stamp(_now() + timedelta(seconds=LEASE_TTL_SECONDS))
                won = self.s._execute_affected(
                    """UPDATE agent_jobs SET status='CLAIMED',lease_owner_agent_id=?,lease_token=?,
                       lease_expires_at=?,claimed_at=?,updated_at=?
                       WHERE job_id=? AND attempt_id=? AND status='NEW'
                       AND (target_agent_id IS NULL OR target_agent_id=?)""",
                    [agent_id, token, expiry, ts, ts, candidate["job_id"], candidate["attempt_id"], agent_id],
                )
                if won == 1:
                    row = self._get(candidate["job_id"], candidate["attempt_id"])
                    return {"job": {"protocol_version": 1, "job_id": row["job_id"],
                                    "attempt_id": row["attempt_id"], "execution_type": row["execution_type"],
                                    "command_line": row["command_line"], "stdin_text": row["stdin_text"],
                                    "cwd": row["cwd"], "timeout_seconds": row["timeout_seconds"],
                                    "cancel_requested": bool(row["cancel_requested"]),
                                    "lease_token": token, "lease_expires_at": expiry}}
            return None

    def _owned(self, row, agent_id, lease_token):
        if row["lease_owner_agent_id"] != agent_id or not lease_token or not secrets.compare_digest(
            str(row["lease_token"] or ""), lease_token
        ):
            raise AgentError(403, "lease_owner_mismatch", "Job lease owner or token does not match")

    def _active(self, row, ts):
        if row["status"] not in ("CLAIMED", "STARTED") or not row["lease_expires_at"] or row["lease_expires_at"] <= ts:
            _conflict("lease_inactive", "Job lease is inactive")

    def renew(self, credential, job_id, attempt_id, lease_token):
        with self.s.transaction():
            agent_id = self._auth(credential)
            ts = _stamp(_now())
            self._reclaim(ts)
            row = self._get(job_id, attempt_id)
            self._owned(row, agent_id, lease_token)
            self._active(row, ts)
            expiry = _stamp(_now() + timedelta(seconds=LEASE_TTL_SECONDS))
            won = self.s._execute_affected(
                """UPDATE agent_jobs SET lease_expires_at=?,updated_at=?
                   WHERE job_id=? AND attempt_id=? AND status IN ('CLAIMED','STARTED')
                   AND lease_owner_agent_id=? AND lease_token=? AND lease_expires_at>?""",
                [expiry, ts, job_id, attempt_id, agent_id, lease_token, ts],
            )
            if won != 1:
                _conflict("lease_inactive", "Job lease is inactive")
            return {"job_id": job_id, "attempt_id": attempt_id, "status": row["status"],
                    "lease_expires_at": expiry, "renew_after_seconds": RENEW_AFTER_SECONDS,
                    "cancel_requested": bool(row["cancel_requested"])}

    def started(self, credential, job_id, attempt_id, lease_token):
        with self.s.transaction():
            agent_id = self._auth(credential)
            ts = _stamp(_now())
            self._reclaim(ts)
            row = self._get(job_id, attempt_id)
            self._owned(row, agent_id, lease_token)
            self._active(row, ts)
            if row["status"] == "STARTED":
                return {"job_id": job_id, "attempt_id": attempt_id, "status": "STARTED",
                        "started_at": row["started_at"], "idempotent": True,
                        "cancel_requested": bool(row["cancel_requested"])}
            won = self.s._execute_affected(
                """UPDATE agent_jobs SET status='STARTED',started_at=?,updated_at=?
                   WHERE job_id=? AND attempt_id=? AND status='CLAIMED'
                   AND lease_owner_agent_id=? AND lease_token=? AND lease_expires_at>?""",
                [ts, ts, job_id, attempt_id, agent_id, lease_token, ts],
            )
            if won != 1:
                _conflict("job_state_conflict", "Job is no longer CLAIMED")
            return {"job_id": job_id, "attempt_id": attempt_id, "status": "STARTED",
                    "started_at": ts, "idempotent": False,
                    "cancel_requested": bool(row["cancel_requested"])}

    def request_cancel(self, job_id, attempt_id):
        with self.s.transaction():
            row = self._get(job_id, attempt_id)
            if row["status"] in TERMINAL:
                return {"status": row["status"], "cancel_requested": bool(row["cancel_requested"])}
            ts = _stamp(_now())
            self.s._execute_affected(
                """UPDATE agent_jobs SET cancel_requested=1,updated_at=?
                   WHERE job_id=? AND attempt_id=? AND status IN ('NEW','CLAIMED','STARTED')""",
                [ts, job_id, attempt_id],
            )
            row = self._get(job_id, attempt_id)
            return {"status": row["status"], "cancel_requested": bool(row["cancel_requested"])}

    def report(self, credential, job_id, attempt_id, lease_token, result):
        state = result.get("status")
        if state not in TERMINAL:
            raise AgentError(422, "validation_failed", "Terminal status is required")
        for key in ("stdout", "stderr"):
            if len(result[key].encode("utf-8")) > OUTPUT_LIMIT_BYTES:
                raise AgentError(422, "output_too_large", key + " exceeds 1 MiB")
        exit_code = result["exit_code"]
        if state == "DONE" and exit_code != 0:
            raise AgentError(422, "validation_failed", "DONE requires exit_code 0")
        if state == "FAILED" and exit_code == 0:
            raise AgentError(422, "validation_failed", "FAILED cannot have exit_code 0")
        fingerprint = _hash(result)
        with self.s.transaction():
            agent_id = self._auth(credential)
            ts = _stamp(_now())
            self._reclaim(ts)
            row = self._get(job_id, attempt_id)
            self._owned(row, agent_id, lease_token)
            if row["status"] in TERMINAL:
                if row["result_digest"] == fingerprint:
                    return {"job_id": job_id, "attempt_id": attempt_id, "status": row["status"], "idempotent": True}
                _conflict("terminal_conflict", "Terminal result cannot be overwritten")
            self._active(row, ts)
            if row["status"] == "CLAIMED" and state not in ("REJECTED", "FAILED", "CANCELLED"):
                _conflict("job_state_conflict", "Status requires STARTED")
            if row["status"] == "CLAIMED" and state == "CANCELLED" and not row["cancel_requested"]:
                _conflict("job_state_conflict", "Cancellation was not requested")
            if row["status"] == "STARTED" and state == "REJECTED":
                _conflict("job_state_conflict", "REJECTED requires pre-start validation")
            won = self.s._execute_affected(
                """UPDATE agent_jobs SET status=?,child_exit_code=?,stdout=?,stderr=?,
                   stdout_truncated=?,stderr_truncated=?,diagnostics=?,end_reason=?,
                   duration_seconds=?,reported_at=?,result_digest=?,finished_at=?,updated_at=?,
                   lease_expires_at=NULL
                   WHERE job_id=? AND attempt_id=? AND status=? AND lease_owner_agent_id=?
                   AND lease_token=? AND lease_expires_at>?""",
                [state, exit_code, result["stdout"], result["stderr"],
                 result["stdout_truncated"], result["stderr_truncated"], result["diagnostics"],
                 result["end_reason"], result["duration_seconds"], result["reported_at"],
                 fingerprint, ts, ts, job_id, attempt_id, row["status"], agent_id, lease_token, ts],
            )
            if won != 1:
                _conflict("job_state_conflict", "Job state changed before terminal report")
            return {"job_id": job_id, "attempt_id": attempt_id, "status": state, "idempotent": False}
