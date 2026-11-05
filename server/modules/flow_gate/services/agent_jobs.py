"""Durable Remote CLI jobs for authenticated pull Agents.

Only this service enqueues work. Each mutation uses a conditional update so a stale
owner cannot change an attempt.

flowgate.v02.0129 T#2 (D0005 §3-4/§3-6): a Job created by an AI run carries
``ai_run_id``. Only those Jobs get the extra lifecycle rules -- one claim only (a lease
that expires after claim never returns the Job to NEW), cancel-vs-claim decided by one
conditional update, finite stage limits enforced by the server itself
(``advance_ai_jobs``), result-retrieval ownership and an idempotent body scrub. A Job
without ``ai_run_id`` keeps every rule it had before.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.services.agent_registry import AgentError, AgentService

LEASE_TTL_SECONDS = 90
RENEW_AFTER_SECONDS = 30
OUTPUT_LIMIT_BYTES = 1024 * 1024
TERMINAL = frozenset({"DONE", "FAILED", "CANCELLED", "TIMED_OUT", "REJECTED"})

# AI-run Job stage limits (D0005 §3-4 DEFERRED -> T#2). They are derived from the Agent
# heartbeat/online window (agent_registry: heartbeat 30s, online TTL 90s) and the lease
# above: an online Agent polls well inside AI_CLAIM_WAIT_SECONDS, a claimed Job must be
# STARTED inside one lease, and a cancel request is seen on the next renew.
AI_CLAIM_WAIT_SECONDS = 120
AI_START_WAIT_SECONDS = LEASE_TTL_SECONDS
AI_RUN_GRACE_SECONDS = 60
AI_CANCEL_GRACE_SECONDS = LEASE_TTL_SECONDS
# How long the executor that owns retrieval has, after an Agent's terminal report, to
# read the output before the supervisor takes the retrieval over and scrubs (§3-6).
AI_RETRIEVAL_GRACE_SECONDS = 300
# Result retrieval states of an AI-run Job (D0005 §3-6).
RETRIEVAL_PENDING = "pending"
RETRIEVAL_COLLECTED = "collected"
RETRIEVAL_HANDED_OVER = "handed_over"
# Terminal causes recorded by the server for AI-run Jobs.
CAUSE_AGENT_REPORT = "agent_report"
CAUSE_SERVER_EXPIRED = "server_expired"
CAUSE_CLAIM_TIMEOUT = "claim_timeout"
CAUSE_SERVER_CANCELLED = "server_cancelled"

# Identity of THIS server process for retrieval ownership. A restart gets a new one, so
# an owner written by a previous process is recognisably orphaned.
PROCESS_OWNER_PREFIX = "proc_" + uuid.uuid4().hex

_AI_TERMINAL_SQL = "('DONE','FAILED','CANCELLED','TIMED_OUT','REJECTED')"

# flowgate.v02.0135 T#1: cancel_requested / stdout_truncated / stderr_truncated are BOOLEAN on
# PostgreSQL (INTEGER on SQLite, TINYINT(1) on MySQL). The SQL below writes and compares them
# only with the TRUE / FALSE literals, which all three engines accept. Comparing one with an
# integer literal (0 / 1) is a PostgreSQL parse error (boolean = integer) and failed every claim:
# claim() -> _reclaim() -> _advance_ai() runs it even on an empty queue.


def retrieval_owner_for(run_id):
    return f"{PROCESS_OWNER_PREFIX}:{run_id}"


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
        requested_by_user_id=None, project_id=None,
        ai_run_id=None, graph_exec_id=None, retrieval_owner=None,
        ai_group_id=None, ai_token_id=None,
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
        if ai_run_id is not None:
            # An AI run never creates a Job "any Agent of the owner may take" (D0005 §3-3).
            if (not isinstance(ai_run_id, str) or not ai_run_id or not target_agent_id
                    or not isinstance(retrieval_owner, str) or not retrieval_owner):
                raise AgentError(422, "validation_failed", "An AI run Job needs a run, a target Agent and an owner")
        if requested_by_user_id is not None:
            if not isinstance(requested_by_user_id, str) or not requested_by_user_id:
                raise AgentError(422, "validation_failed", "A requester identity is required")
            if not self._requester_allowed(requested_by_user_id, project_id):
                raise AgentError(403, "agent_job_forbidden", "Requester cannot access this project")
        if target_agent_id:
            target = AgentService(self.s).get(target_agent_id)
            if target.get("owner_user_id") != requested_by_user_id:
                raise AgentError(403, "agent_job_forbidden", "Agent ownership does not match the requester")
        payload = {"execution_type": "remote_cli", "command_line": command_line,
                   "stdin_text": stdin_text, "cwd": cwd, "timeout_seconds": timeout_seconds,
                   "target_agent_id": target_agent_id}
        # Keep legacy system-job fingerprints stable across the migration.
        if requested_by_user_id is not None or project_id is not None:
            payload.update(requested_by_user_id=requested_by_user_id, project_id=project_id)
        if ai_run_id is not None:
            payload.update(ai_run_id=ai_run_id, graph_exec_id=graph_exec_id)
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
            if ai_run_id is None:
                inserted = self.s._execute_affected(
                    """INSERT INTO agent_jobs(job_id,attempt_id,execution_type,command_line,stdin_text,cwd,
                       timeout_seconds,status,target_agent_id,payload_digest,cancel_requested,
                       stdout_truncated,stderr_truncated,created_at,updated_at,requested_by_user_id,project_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,FALSE,FALSE,FALSE,?,?,?,?) ON CONFLICT DO NOTHING""",
                    [job_id, attempt_id, "remote_cli", command_line, stdin_text, cwd,
                     timeout_seconds, "NEW", target_agent_id, fingerprint, ts, ts, requested_by_user_id, project_id],
                )
            else:
                inserted = self.s._execute_affected(
                    """INSERT INTO agent_jobs(job_id,attempt_id,execution_type,command_line,stdin_text,cwd,
                       timeout_seconds,status,target_agent_id,payload_digest,cancel_requested,
                       stdout_truncated,stderr_truncated,created_at,updated_at,requested_by_user_id,project_id,
                       ai_run_id,graph_exec_id,retrieval_state,retrieval_owner,ai_group_id,ai_token_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,FALSE,FALSE,FALSE,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                    [job_id, attempt_id, "remote_cli", command_line, stdin_text, cwd,
                     timeout_seconds, "NEW", target_agent_id, fingerprint, ts, ts, requested_by_user_id, project_id,
                     ai_run_id, graph_exec_id, RETRIEVAL_PENDING, retrieval_owner, ai_group_id, ai_token_id],
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

    def _requester_allowed(self, user_id, project_id):
        user = self.s._fetch_one("SELECT is_active,is_admin FROM users WHERE user_id=?", [user_id])
        if not user or not user["is_active"]:
            return False
        if project_id is None:
            return True
        project = self.s._fetch_one("SELECT is_active FROM projects WHERE project_id=?", [project_id])
        if not project or not project["is_active"]:
            return False
        from modules.flow_gate.rbac.permission_service import has_permission
        return bool(user["is_admin"]) or has_permission(user_id, project_id, "perm_document_read")

    def _job_allowed(self, row, agent_id):
        agent = self.s._fetch_one("SELECT owner_user_id FROM agents WHERE agent_id=?", [agent_id])
        if not agent or agent.get("owner_user_id") != row.get("requested_by_user_id"):
            return False
        owner = agent.get("owner_user_id")
        return owner is None or self._requester_allowed(owner, row.get("project_id"))

    def _reclaim(self, ts):
        # AI-run Jobs are settled first and never re-queued (one claim only, §3-4).
        self._advance_ai(ts)
        self.s._execute_affected(
            """UPDATE agent_jobs SET status='NEW',lease_owner_agent_id=NULL,lease_token=NULL,
                   lease_expires_at=NULL,claimed_at=NULL,updated_at=?
                   WHERE status='CLAIMED' AND lease_expires_at<=? AND ai_run_id IS NULL""",
            [ts, ts],
        )
        self.s._execute_affected(
            """UPDATE agent_jobs SET status='FAILED',end_reason='lease_expired',
                   diagnostics='Started Agent Job lease expired',finished_at=?,updated_at=?,
                   lease_expires_at=NULL WHERE status='STARTED' AND lease_expires_at<=? AND ai_run_id IS NULL""",
            [ts, ts, ts],
        )

    # ── AI-run Job lifecycle (flowgate.v02.0129 T#2, D0005 §3-4/§3-6) ─────────────

    def _advance_ai(self, ts):
        """Server-driven stage limits for AI-run Jobs. Idempotent; call inside a transaction.

        NEW past the claim limit -> CANCELLED (input scrubbed). CLAIMED past the start limit
        or STARTED past its timeout + grace -> cancel requested. A claimed Job whose lease
        expired, or whose cancel grace ran out, -> FAILED "remote termination unconfirmed".
        """
        now = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        claim_cut = _stamp(now - timedelta(seconds=AI_CLAIM_WAIT_SECONDS))
        start_cut = _stamp(now - timedelta(seconds=AI_START_WAIT_SECONDS))
        cancel_cut = _stamp(now - timedelta(seconds=AI_CANCEL_GRACE_SECONDS))
        self.s._execute_affected(
            """UPDATE agent_jobs SET status='CANCELLED',end_reason='not_claimed',terminal_cause=?,
                   stdin_text='',command_line='',retrieval_state=?,scrubbed_at=?,finished_at=?,updated_at=?
                   WHERE ai_run_id IS NOT NULL AND status='NEW' AND created_at<=?""",
            [CAUSE_CLAIM_TIMEOUT, RETRIEVAL_HANDED_OVER, ts, ts, ts, claim_cut],
        )
        self.s._execute_affected(
            """UPDATE agent_jobs SET cancel_requested=TRUE,cancel_requested_at=?,updated_at=?
                   WHERE ai_run_id IS NOT NULL AND status='CLAIMED' AND cancel_requested=FALSE
                   AND claimed_at<=?""",
            [ts, ts, start_cut],
        )
        for row in self.s._fetch_all(
            """SELECT job_id,attempt_id,started_at,timeout_seconds FROM agent_jobs
                   WHERE ai_run_id IS NOT NULL AND status='STARTED' AND cancel_requested=FALSE""",
        ):
            try:
                started = datetime.fromisoformat(str(row["started_at"]).replace("Z", "+00:00"))
            except ValueError:
                started = now
            limit = started + timedelta(seconds=int(row["timeout_seconds"]) + AI_RUN_GRACE_SECONDS)
            if limit <= now:
                self.s._execute_affected(
                    """UPDATE agent_jobs SET cancel_requested=TRUE,cancel_requested_at=?,updated_at=?
                           WHERE job_id=? AND attempt_id=? AND status='STARTED' AND cancel_requested=FALSE""",
                    [ts, ts, row["job_id"], row["attempt_id"]],
                )
        self.s._execute_affected(
            """UPDATE agent_jobs SET status='FAILED',end_reason='remote_termination_unconfirmed',
                   terminal_cause=?,diagnostics='AI Job ended without a terminal Agent report',
                   stdin_text='',command_line='',stdout=NULL,stderr=NULL,retrieval_state=?,scrubbed_at=?,
                   finished_at=?,updated_at=?,lease_expires_at=NULL
                   WHERE ai_run_id IS NOT NULL AND status IN ('CLAIMED','STARTED')
                   AND (lease_expires_at IS NULL OR lease_expires_at<=?
                        OR (cancel_requested=TRUE AND cancel_requested_at IS NOT NULL AND cancel_requested_at<=?))""",
            [CAUSE_SERVER_EXPIRED, RETRIEVAL_HANDED_OVER, ts, ts, ts, ts, cancel_cut],
        )

    def advance_ai_jobs(self):
        """Apply the AI-run stage limits now, without waiting for an Agent request.

        Bodies of terminal Jobs whose retrieval is already settled (collected / handed
        over) are scrubbed in the same pass, so a late output never outlives one poll.
        """
        with self.s.transaction():
            ts = _stamp(_now())
            self._advance_ai(ts)
            self._scrub(ts)

    def get_ai_job(self, job_id, attempt_id):
        """Metadata of one AI-run Job; never the input or output bodies."""
        row = self.s._fetch_one(
            """SELECT job_id,attempt_id,status,target_agent_id,lease_owner_agent_id,cancel_requested,
                      child_exit_code,end_reason,terminal_cause,retrieval_state,ai_run_id,graph_exec_id,
                      cwd,created_at,claimed_at,started_at,finished_at,scrubbed_at,fenced_at,
                      ai_token_id,ai_group_id,retrieval_deadline_at
               FROM agent_jobs WHERE job_id=? AND attempt_id=? AND ai_run_id IS NOT NULL""",
            [job_id, attempt_id],
        )
        if not row:
            raise AgentError(404, "job_not_found", "Agent Job not found")
        return row

    def cancel_ai_job(self, job_id, attempt_id):
        """Cancel an AI-run Job: a NEW Job ends at once; a claimed one is asked to stop.

        The NEW branch and claim() are both conditional on status='NEW' (claim also on
        cancel_requested=FALSE), so exactly one of them wins (D0005 §3-4).
        """
        with self.s.transaction():
            ts = _stamp(_now())
            won = self.s._execute_affected(
                """UPDATE agent_jobs SET status='CANCELLED',end_reason='cancelled_before_claim',
                       terminal_cause=?,cancel_requested=TRUE,cancel_requested_at=?,stdin_text='',command_line='',
                       retrieval_state=?,scrubbed_at=?,finished_at=?,updated_at=?
                       WHERE job_id=? AND attempt_id=? AND ai_run_id IS NOT NULL AND status='NEW'""",
                [CAUSE_SERVER_CANCELLED, ts, RETRIEVAL_HANDED_OVER, ts, ts, ts, job_id, attempt_id],
            )
            if won != 1:
                self.s._execute_affected(
                    """UPDATE agent_jobs SET cancel_requested=TRUE,
                           cancel_requested_at=COALESCE(cancel_requested_at,?),updated_at=?
                           WHERE job_id=? AND attempt_id=? AND ai_run_id IS NOT NULL
                           AND status IN ('CLAIMED','STARTED')""",
                    [ts, ts, job_id, attempt_id],
                )
            row = self.s._fetch_one(
                "SELECT status,cancel_requested FROM agent_jobs WHERE job_id=? AND attempt_id=?",
                [job_id, attempt_id],
            )
            return {"status": row["status"] if row else None,
                    "cancel_requested": bool(row and row["cancel_requested"]), "cancelled_unclaimed": won == 1}

    def collect_ai_result(self, job_id, attempt_id, owner):
        """Read a terminal AI-run Job's output once, as its retrieval owner, then scrub it.

        Returns the Job metadata plus ``collected``. Output is returned only when this
        caller still owned retrieval; otherwise ``collected`` is False and the bodies are
        None, so an empty result is never mistaken for a real one (D0005 §3-6).
        """
        with self.s.transaction():
            row = self._get(job_id, attempt_id)
            if not row.get("ai_run_id") or row["status"] not in TERMINAL:
                raise AgentError(409, "job_not_terminal", "AI Job is not terminal")
            ts = _stamp(_now())
            won = 0
            if row.get("retrieval_state") == RETRIEVAL_PENDING and row.get("retrieval_owner") == owner:
                won = self.s._execute_affected(
                    """UPDATE agent_jobs SET retrieval_state=?,stdout=NULL,stderr=NULL,stdin_text='',
                           command_line='',scrubbed_at=?,updated_at=?
                           WHERE job_id=? AND attempt_id=? AND retrieval_state=? AND retrieval_owner=?""",
                    [RETRIEVAL_COLLECTED, ts, ts, job_id, attempt_id, RETRIEVAL_PENDING, owner],
                )
            result = {key: row.get(key) for key in (
                "job_id", "attempt_id", "status", "target_agent_id", "lease_owner_agent_id",
                "child_exit_code", "end_reason", "terminal_cause", "duration_seconds",
                "claimed_at", "started_at", "finished_at", "diagnostics")}
            result["collected"] = won == 1
            result["stdout"] = row.get("stdout") if won == 1 else None
            result["stderr"] = row.get("stderr") if won == 1 else None
            return result

    def release_ai_retrieval(self, job_id, attempt_id, owner):
        """The executor's finally: give up retrieval (if still held) and scrub. Idempotent."""
        with self.s.transaction():
            ts = _stamp(_now())
            self.s._execute_affected(
                """UPDATE agent_jobs SET retrieval_state=?,updated_at=?
                       WHERE job_id=? AND attempt_id=? AND ai_run_id IS NOT NULL
                       AND retrieval_state=? AND retrieval_owner=?""",
                [RETRIEVAL_HANDED_OVER, ts, job_id, attempt_id, RETRIEVAL_PENDING, owner],
            )
            self._scrub(ts, job_id=job_id, attempt_id=attempt_id)

    def _scrub(self, ts, *, job_id=None, attempt_id=None):
        """Empty bodies of terminal AI-run Jobs whose retrieval is settled. Metadata stays."""
        where = ["ai_run_id IS NOT NULL", "status IN " + _AI_TERMINAL_SQL,
                 "retrieval_state IN (?,?)", "scrubbed_at IS NULL"]
        params = [RETRIEVAL_COLLECTED, RETRIEVAL_HANDED_OVER]
        if job_id is not None:
            where.append("job_id=? AND attempt_id=?")
            params += [job_id, attempt_id]
        return self.s._execute_affected(
            "UPDATE agent_jobs SET stdout=NULL,stderr=NULL,stdin_text='',command_line='',scrubbed_at=?,updated_at=? "
            "WHERE " + " AND ".join(where),
            [ts, ts, *params],
        )

    def sweep_orphaned_ai_jobs(self, is_live_owner=None):
        """Settle AI-run Jobs whose retrieval owner is gone or out of time (§3-6).

        ``is_live_owner(owner)`` says whether the executor named by ``owner`` still runs.
        It defaults to "written by this process" (startup: every earlier owner is gone);
        the periodic supervisor passes "this process AND that run is still live here",
        so a run that vanished inside this process is recognised too.

        For a gone owner: NEW Jobs are cancelled (input scrubbed), claimed ones are asked
        to stop and their retrieval is handed over (the stage limits then end them),
        terminal ones are handed over. A terminal Job whose retrieval deadline passed is
        handed over even while its owner lives. Every hand-over is conditional on the
        owner and state read here, so it never races a live owner's collect into losing
        a result it is entitled to. Settled bodies are scrubbed. Returns Jobs touched.
        """
        live = is_live_owner or (lambda owner: str(owner or "").startswith(PROCESS_OWNER_PREFIX + ":"))
        touched = 0
        with self.s.transaction():
            ts = _stamp(_now())
            self._advance_ai(ts)
            rows = self.s._fetch_all(
                """SELECT job_id,attempt_id,status,retrieval_owner,retrieval_state,retrieval_deadline_at
                       FROM agent_jobs
                       WHERE ai_run_id IS NOT NULL AND (status IN ('NEW','CLAIMED','STARTED')
                       OR retrieval_state=?)""",
                [RETRIEVAL_PENDING],
            )
            for row in rows:
                owner = row.get("retrieval_owner")
                pending = row.get("retrieval_state") == RETRIEVAL_PENDING
                overdue = bool(pending and row["status"] in TERMINAL and row.get("retrieval_deadline_at")
                               and str(row["retrieval_deadline_at"]) <= ts)
                if not overdue and live(owner):
                    continue
                won = 0
                if row["status"] == "NEW":
                    won = self.s._execute_affected(
                        """UPDATE agent_jobs SET status='CANCELLED',end_reason='cancelled_before_claim',
                               terminal_cause=?,cancel_requested=TRUE,cancel_requested_at=?,stdin_text='',
                               command_line='',retrieval_state=?,scrubbed_at=?,finished_at=?,updated_at=?
                               WHERE job_id=? AND attempt_id=? AND status='NEW'""",
                        [CAUSE_SERVER_CANCELLED, ts, RETRIEVAL_HANDED_OVER, ts, ts, ts,
                         row["job_id"], row["attempt_id"]],
                    )
                    touched += won
                if won:
                    continue
                if row["status"] in ("NEW", "CLAIMED", "STARTED"):
                    # A claim that won against the NEW cancel above lands here: the claimed
                    # Job is asked to stop instead (one of the two always holds, §3-4).
                    touched += self.s._execute_affected(
                        """UPDATE agent_jobs SET cancel_requested=TRUE,
                               cancel_requested_at=COALESCE(cancel_requested_at,?),retrieval_state=?,updated_at=?
                               WHERE job_id=? AND attempt_id=? AND status IN ('CLAIMED','STARTED')
                               AND COALESCE(retrieval_owner,'')=COALESCE(?,'')""",
                        [ts, RETRIEVAL_HANDED_OVER, ts, row["job_id"], row["attempt_id"], owner],
                    )
                elif pending:
                    touched += self.s._execute_affected(
                        """UPDATE agent_jobs SET retrieval_state=?,updated_at=?
                               WHERE job_id=? AND attempt_id=? AND retrieval_state=?
                               AND COALESCE(retrieval_owner,'')=COALESCE(?,'')""",
                        [RETRIEVAL_HANDED_OVER, ts, row["job_id"], row["attempt_id"], RETRIEVAL_PENDING, owner],
                    )
            touched += self._scrub(ts)
        return touched

    # -- settlement (D0005 §3-4 "종결"): terminal AND, for an unconfirmed end, fenced --

    _UNSETTLED_SQL = ("(status IN ('NEW','CLAIMED','STARTED') OR (terminal_cause='" + CAUSE_SERVER_EXPIRED
                      + "' AND fenced_at IS NULL))")

    def unsettled_ai_jobs(self, *, ai_run_id=None, graph_exec_ids=None, group_id=None):
        """AI-run Jobs not yet settled, for one run, a set of Graph executions or a group.

        Settled means terminal and -- when the server expired it without an Agent report
        -- its run token fenced (``fenced_at``). Metadata only, never bodies.
        """
        clauses, params = [], []
        if ai_run_id:
            clauses.append("ai_run_id=?")
            params.append(str(ai_run_id))
        execs = [str(e) for e in (graph_exec_ids or ()) if e]
        if execs:
            clauses.append("graph_exec_id IN (" + ",".join("?" for _ in execs) + ")")
            params += execs
        if group_id:
            clauses.append("(ai_group_id=? AND graph_exec_id IS NULL)")
            params.append(str(group_id))
        if not clauses:
            return []
        return self.s._fetch_all(
            "SELECT job_id,attempt_id,status,terminal_cause,ai_run_id,graph_exec_id,ai_group_id,ai_token_id,"
            "retrieval_owner,fenced_at FROM agent_jobs WHERE ai_run_id IS NOT NULL AND "
            + self._UNSETTLED_SQL + " AND (" + " OR ".join(clauses) + ")",
            params,
        )

    def unsettled_ai_run_ids(self):
        """Every AI run that still has an unsettled remote Job (restart recovery input)."""
        return self.s._fetch_all(
            "SELECT ai_run_id,MIN(ai_group_id) AS ai_group_id,MIN(graph_exec_id) AS graph_exec_id,"
            "MIN(ai_token_id) AS ai_token_id FROM agent_jobs WHERE ai_run_id IS NOT NULL AND "
            + self._UNSETTLED_SQL + " GROUP BY ai_run_id",
        )

    def unfenced_expired_ai_jobs(self):
        """Server-expired Jobs whose run token is not fenced yet (revocation retry input)."""
        return self.s._fetch_all(
            """SELECT job_id,attempt_id,ai_run_id,ai_token_id FROM agent_jobs
                   WHERE ai_run_id IS NOT NULL AND terminal_cause=? AND fenced_at IS NULL""",
            [CAUSE_SERVER_EXPIRED],
        )

    def mark_fenced(self, *, ai_run_id=None, token_id=None, job_id=None, attempt_id=None):
        """Record that the run token of these Jobs is revoked (or never existed). Idempotent."""
        ts = _stamp(_now())
        if job_id is not None:
            return self.s._execute_affected(
                "UPDATE agent_jobs SET fenced_at=COALESCE(fenced_at,?),updated_at=? "
                "WHERE job_id=? AND attempt_id=? AND ai_run_id IS NOT NULL",
                [ts, ts, job_id, attempt_id],
            )
        clauses, params = [], []
        if ai_run_id:
            clauses.append("ai_run_id=?")
            params.append(str(ai_run_id))
        if token_id:
            clauses.append("ai_token_id=?")
            params.append(str(token_id))
        if not clauses:
            return 0
        return self.s._execute_affected(
            "UPDATE agent_jobs SET fenced_at=COALESCE(fenced_at,?),updated_at=? WHERE ai_run_id IS NOT NULL AND ("
            + " OR ".join(clauses) + ")",
            [ts, ts, *params],
        )

    def claim(self, credential):
        with self.s.transaction():
            agent_id = self._auth(credential, claim=True)
            ts = _stamp(_now())
            self._reclaim(ts)
            candidates = self.s._fetch_all(
                """SELECT j.* FROM agent_jobs j JOIN agents a ON a.agent_id=?
                   WHERE j.status='NEW' AND (j.target_agent_id IS NULL OR j.target_agent_id=?)
                   AND ((a.owner_user_id IS NULL AND j.requested_by_user_id IS NULL)
                        OR a.owner_user_id=j.requested_by_user_id)
                   ORDER BY j.created_at,j.job_id,j.attempt_id""",
                [agent_id, agent_id],
            )
            for candidate in candidates:
                if not self._job_allowed(candidate, agent_id):
                    continue
                token = "lsh_" + secrets.token_urlsafe(32)
                expiry = _stamp(_now() + timedelta(seconds=LEASE_TTL_SECONDS))
                won = self.s._execute_affected(
                    """UPDATE agent_jobs SET status='CLAIMED',lease_owner_agent_id=?,lease_token=?,
                       lease_expires_at=?,claimed_at=?,updated_at=?
                       WHERE job_id=? AND attempt_id=? AND status='NEW'
                       AND (target_agent_id IS NULL OR target_agent_id=?)
                       AND (ai_run_id IS NULL OR cancel_requested=FALSE)""",
                    [agent_id, token, expiry, ts, ts, candidate["job_id"], candidate["attempt_id"], agent_id],
                )
                if won == 1:
                    row = self._get(candidate["job_id"], candidate["attempt_id"])
                    job = {"protocol_version": 1, "job_id": row["job_id"],
                           "attempt_id": row["attempt_id"], "execution_type": row["execution_type"],
                           "command_line": row["command_line"], "stdin_text": row["stdin_text"],
                           "cwd": row["cwd"], "timeout_seconds": row["timeout_seconds"],
                           "cancel_requested": bool(row["cancel_requested"]),
                           "lease_token": token, "lease_expires_at": expiry}
                    if row.get("ai_run_id"):
                        # Delivered exactly once (one claim only): the token-bearing input
                        # is not kept after this hand-off (D0005 §3-6 "수신 시").
                        self.s._execute_affected(
                            """UPDATE agent_jobs SET stdin_text='',command_line='',updated_at=?
                               WHERE job_id=? AND attempt_id=?""",
                            [ts, row["job_id"], row["attempt_id"]],
                        )
                    return {"job": job}
            return None

    def _owned(self, row, agent_id, lease_token):
        if not self._job_allowed(row, agent_id):
            raise AgentError(403, "agent_job_forbidden", "Agent cannot execute this user or project job")
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
                """UPDATE agent_jobs SET cancel_requested=TRUE,cancel_requested_at=COALESCE(cancel_requested_at,?),
                   updated_at=? WHERE job_id=? AND attempt_id=? AND status IN ('NEW','CLAIMED','STARTED')""",
                [ts, ts, job_id, attempt_id],
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
            if row.get("ai_run_id"):
                deadline = _stamp(_now() + timedelta(seconds=AI_RETRIEVAL_GRACE_SECONDS))
                self.s._execute_affected(
                    "UPDATE agent_jobs SET terminal_cause=?,retrieval_deadline_at=? WHERE job_id=? AND attempt_id=?",
                    [CAUSE_AGENT_REPORT, deadline, job_id, attempt_id],
                )
                # Nobody collects a Job whose retrieval was already handed over (its
                # executor is gone): that output is not kept past this report (§3-6).
                self._scrub(ts, job_id=job_id, attempt_id=attempt_id)
            return {"job_id": job_id, "attempt_id": attempt_id, "status": state, "idempotent": False}
