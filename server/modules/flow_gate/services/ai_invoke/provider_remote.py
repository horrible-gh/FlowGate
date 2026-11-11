"""Remote CLI execution boundary (flowgate.v02.0129 T#2, D0005 §3-3 ~ §3-6).

An AI connection whose ``exec_location`` is ``remote`` never spawns a process on this
server. It runs only as an Agent Job addressed to the connection's ``target_agent_id``:

  pre-check (no side effects) -> Graph launch approval (the same hook the local
  boundary calls) -> re-check -> Job (server-chosen job_id, cwd = the target Agent's
  remote working directory with ${AGENT_JOB_ID} replaced by that job_id)
  -> watch (cancel / stage limits / progress watchdog) -> retrieve the result as its
  owner -> scrub.

Every failure here except a refused launch is a *remote final failure*: the provider
chain stops on it and nothing else (the next connection, another Agent, this server)
runs the same request. The result keeps the local classification vocabulary
(started_ok / fast_fail / spawn_failed) so judging and records are unchanged.

Job creation is closed until the Agent working-directory contract (Q1 #555, D0005 §3-5)
is verified on a deployed Agent: ``AGENT_WORKDIR_CONTRACT_VERIFIED`` (D0005 §9 (마)).
Until then every remote CLI request ends at the pre-check with a visible final failure.

The run stays "alive" (``remote_job_active``) until its Job is *settled*: terminal and,
when the server expired it without an Agent report, its run token revoked. A watch,
lookup or retrieval error never clears that mark; the Job is asked to stop and the
supervisor (``remote_supervisor``) takes it over until it is settled.

Secrets: the prompt (which carries the run token) only goes into the Job input, which
the queue empties at claim time; the stored CLI command is never logged or copied into
a failure detail.
"""
from __future__ import annotations

import ipaddress
import re
import secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from .runtime import (
    OUTPUT_TAIL_BYTES,
    STALL_POLL_INTERVAL_SEC,
    _absolute_cap_sec,
    _absolute_remaining_sec,
    _svc,
    logger,
)

REMOTE_POLL_SEC = 1.0
# The progress watchdog samples the server-observable signals at the local watchdog's tick.
REMOTE_PROGRESS_INTERVAL_SEC = STALL_POLL_INTERVAL_SEC
REMOTE_COMMAND_FALLBACK = "remote_command_missing"

# D0005 §9 (마): Job creation is gated on the deployed Agent honouring the working-directory
# contract (create the directory it is sent when missing, report a concrete cause when it
# cannot). flowgate.v02.0135 CH0006: the directory is no longer the fixed relative
# ``flowgate-runs/<job_id>``; it is the Agent's own ``remote_workdir`` setting.
# flowgate.v02.0131 CH0004 (admin instruction, 2026-11-04): opened by the operator so remote
# CLI connections run. The deployed-Agent contract verification and the cross-path-system
# acceptance run are still outstanding (the Agent is not in this repository); the server
# side only ever sends the Agent's configured directory. Set back to False to close the
# boundary again -- every remote CLI request then ends at the pre-check as before.
AGENT_WORKDIR_CONTRACT_VERIFIED = True

# Failure codes surfaced in fallback_history / last_message (no secrets, no paths).
UNASSIGNED = "remote_agent_unassigned"
UNAVAILABLE = "remote_agent_unavailable"
OFFLINE = "remote_agent_offline"
API_BASE = "remote_api_base_unreachable"
WORKDIR_UNVERIFIED = "remote_agent_workdir_unverified"
WORKDIR_UNSET = "remote_agent_workdir_unset"
PREVIOUS_UNSETTLED = "remote_previous_job_unsettled"
JOB_REFUSED = "remote_job_refused"
NO_CLAIM = "remote_agent_no_response"
REJECTED = "remote_agent_rejected"
UNCONFIRMED = "remote_termination_unconfirmed"
RESULT_LOST = "remote_result_unavailable"
WATCH_LOST = "remote_job_watch_failed"
AGENT_MISMATCH = "remote_agent_mismatch"
CANCELLED = "remote_cancelled_before_start"
NOT_STARTED = "remote_agent_failed_before_start"
FAST_FAIL = "remote_fast_fail"

# flowgate.v02.0135 CH0006: the Agent setting's placeholder for the Job id.
JOB_ID_VARIABLE = "${AGENT_JOB_ID}"
# Causes of an Agent's refusal / pre-start failure, read from its own end_reason and
# diagnostics (first match wins). The GUI words each one; anything else stays the
# generic agent_rejected / agent_failed with the Agent's own text.
CAUSE_WORKDIR_UNSET = "workdir_unset"
_CAUSES = (
    ("permission_denied", ("permission denied", "access is denied", "access denied", "eacces", "eperm",
                           "operation not permitted", "not permitted", "read-only file system", "erofs")),
    ("workdir_create_failed", ("cannot create", "can't create", "could not create", "couldn't create",
                               "failed to create", "unable to create", "create failed", "creation failed",
                               "mkdir")),
    ("workdir_missing", ("does not exist", "doesn't exist", "not exist", "no such file", "enoent",
                         "not found", "missing")),
    ("path_invalid", ("invalid", "outside", "escape", "symlink", "link", "not a directory", "enotdir",
                      "absolute", "relative", "name too long", "enametoolong", "illegal")),
)
# A directory cause needs the text to be about the directory ("command not found" is not).
_DIRECTORY_WORDS = ("workspace", "workdir", "working dir", "cwd", "directory", "folder", "path", "mkdir")
_DIRECTORY_CAUSES = frozenset({"workdir_create_failed", "workdir_missing", "path_invalid"})
_GENERIC_AGENT_REASONS = frozenset({"rejected", "failed", "failed_before_start", "error"})
_AGENT_MESSAGE_MAX = 400
_SECRET_VALUE_RE = re.compile(r"(?i)\b(agc|enr|fgt|tok)_[A-Za-z0-9_\-]{8,}")
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|credential)(\s*[=:]\s*)[^\s,;\"']+")


def is_remote_cli(provider: dict) -> bool:
    """A CLI connection configured to run on a remote Agent."""
    return (provider.get("exec_type") or "cli") == "cli" and provider.get("exec_location") == "remote"


def _store():
    from modules.flow_gate.db.connection import get_store
    return get_store()


def _binding(provider: dict) -> dict:
    """The stored connection is the authority at run time (target + owner)."""
    row = None
    provider_id = provider.get("id")
    if provider_id:
        try:
            row = _store()._fetch_one(
                "SELECT owner_scope, owner_user_id, exec_location, target_agent_id "
                "FROM ai_providers WHERE provider_id = ? AND deleted_at IS NULL",
                [provider_id],
            )
        except Exception:
            logger.warning("ai-invoke remote: provider binding lookup failed", exc_info=True)
            row = None
    source = row or provider
    owner_scope = source.get("owner_scope") or "system"
    return {
        "target_agent_id": source.get("target_agent_id") or None,
        # D0005 §3-1: a personal connection's Job is requested by its owner; a shared
        # (system/project) connection's Job has no requester and needs a system Agent.
        "requester": source.get("owner_user_id") if owner_scope == "user" else None,
    }


def agent_failure(target_agent_id: Optional[str], requester: Optional[str]) -> Optional[tuple[str, str]]:
    """Execution-time availability of the target Agent (D0005 §3-3 ①/③). None = usable."""
    if not target_agent_id:
        return UNASSIGNED, "no_target_agent"
    from modules.flow_gate.services.agent_registry import AgentError, AgentService
    try:
        agent = AgentService(_store()).get(target_agent_id)
    except AgentError:
        return UNAVAILABLE, "agent_not_found"
    if agent.get("owner_user_id") != requester:
        # Same reason as "not found": never disclose another owner's Agent.
        return UNAVAILABLE, "agent_not_found"
    if not agent.get("enabled"):
        return UNAVAILABLE, "agent_disabled"
    if agent.get("registration_status") != "registered":
        return UNAVAILABLE, "agent_revoked"
    if (agent.get("location") != "remote" or agent.get("connection_mode") != "agent_pull"
            or agent.get("protocol_compatibility") != "compatible" or agent.get("protocol_version") != 1):
        return UNAVAILABLE, "agent_incompatible"
    if not (agent.get("effective_capabilities") or {}).get("ai_cli"):
        return UNAVAILABLE, "ai_cli_capability_missing"
    if agent.get("online_status") != "online":
        return OFFLINE, "agent_offline"
    return None


def api_base_failure(api_base: Optional[str]) -> Optional[str]:
    """A remote Agent must reach the operator-facing base; loopback is this host only."""
    try:
        host = urlsplit(api_base or "").hostname
    except ValueError:
        host = None
    if not host:
        return "api_base_missing"
    if host.lower() == "localhost" or host.lower().endswith(".localhost"):
        return "api_base_loopback"
    try:
        if ipaddress.ip_address(host).is_loopback:
            return "api_base_loopback"
    except ValueError:
        pass
    return None


def _final(run: dict, provider: dict, code: str, reason: str, *, cause: Optional[str] = None,
           message: Optional[str] = None) -> tuple[str, str]:
    """Mark a remote final failure: the chain must stop here (D0005 §3-3).

    ``cause`` / ``message`` (flowgate.v02.0135 CH0006): the classified cause and the Agent's
    own (secret-free) words, kept with the Job's working directory so the GUI can say what
    actually went wrong instead of only ``all_providers_failed``.
    """
    info = run.setdefault("remote_job", {})
    run["remote_final_failure"] = {
        "code": code, "reason": reason, "provider_id": provider.get("id"),
        "target_agent_id": info.get("target_agent_id"),
        "claimed_agent_id": info.get("claimed_agent_id"),
        "job_id": info.get("job_id"), "attempt_id": info.get("attempt_id"),
        "cause": cause, "message": message, "workdir": info.get("workdir"),
    }
    info["failure_code"] = code
    logger.warning("ai-invoke %s: remote CLI final failure %s (%s)", run.get("run_id"), code, reason)
    return "spawn_failed", f"{code}: {reason}"


def _agent_workdir(target_agent_id: Optional[str]) -> Optional[str]:
    """The target Agent's ``remote_workdir`` setting (flowgate.v02.0135 CH0006); None = not set."""
    from modules.flow_gate.services.agent_registry import AgentError, AgentService
    try:
        value = AgentService(_store()).get(target_agent_id).get("remote_workdir")
    except AgentError:
        return None
    return (value or "").strip() or None


def job_workdir(template: str, job_id: str) -> str:
    """The Job cwd: the Agent's setting with ``${AGENT_JOB_ID}`` replaced by the Job id."""
    return template.replace(JOB_ID_VARIABLE, job_id)


def _redact(text: str, run: dict) -> str:
    """No credential, run token or prompt echo leaves in a failure text; paths stay."""
    from .runtime import _known_run_prompts, _known_run_raw_tokens, _redact_secrets
    tokens = _known_run_raw_tokens(run) | {t for t in (run.get("raw_token"),) if t}
    text = _redact_secrets(text, tokens, _known_run_prompts(run)) or ""
    text = _SECRET_VALUE_RE.sub("[redacted]", text)
    return _SECRET_ASSIGN_RE.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", text)


def classify_agent_failure(text: str, default: str, workdir: Optional[str] = None) -> str:
    """The cause of an Agent refusal / pre-start failure from its own words."""
    lowered = (text or "").lower()
    about_directory = (any(word in lowered for word in _DIRECTORY_WORDS)
                       or bool(workdir and workdir.lower() in lowered))
    for cause, needles in _CAUSES:
        if cause in _DIRECTORY_CAUSES and not about_directory:
            continue
        if any(needle in lowered for needle in needles):
            return cause
    return default


def _agent_reported(run: dict, result: dict, default_cause: str, fallback: str) -> tuple[str, str, str]:
    """(reason, cause, message) of an Agent's REJECTED / FAILED-before-start report.

    The Agent may put its cause in ``end_reason``, in ``diagnostics`` or in both; the
    server used to read ``end_reason`` only, so a cause in ``diagnostics`` ended as a bare
    "rejected". Both are read, generic words dropped, secrets redacted, the working
    directory kept.
    """
    parts = []
    for key in ("end_reason", "diagnostics"):
        value = " ".join(str(result.get(key) or "").split())
        if value and value.lower() not in _GENERIC_AGENT_REASONS and value not in parts:
            parts.append(value)
    message = _redact(" · ".join(parts), run)
    if len(message) > _AGENT_MESSAGE_MAX:
        message = message[:_AGENT_MESSAGE_MAX - 1] + "…"
    workdir = (run.get("remote_job") or {}).get("workdir")
    cause = classify_agent_failure(message, default_cause, workdir)
    reason = f"{cause}: {message or fallback}"
    if workdir and workdir not in reason:
        reason += f" (workdir: {workdir})"
    return reason, cause, message or fallback


def _sanitize_prompt(prompt: str, run: dict) -> str:
    """No server scratch absolute path may reach the remote Agent (D0005 invariant 10)."""
    for key in ("token_scratch_dir", "scratch_dir"):
        path = run.get(key)
        if path and str(path) in prompt:
            prompt = prompt.replace(str(path), ".")
    return prompt


def execute(provider: dict, prompt: str, run: dict) -> tuple[str, Optional[str]]:
    """Run one remote CLI attempt on the target Agent only. Same return shape as _cli_execute."""
    from modules.flow_gate.services import agent_jobs, graph_execution_hooks
    from modules.flow_gate.services.agent_registry import AgentError
    from modules.flow_gate.settings import ai_settings_service
    from flowgate.ai_roles import invocations as role_invocations
    from . import remote_supervisor

    binding = _binding(provider)
    target, requester = binding["target_agent_id"], binding["requester"]
    info = {"provider_id": provider.get("id"), "target_agent_id": target, "claimed_agent_id": None,
            "job_id": None, "attempt_id": None, "status": None, "workdir": None}
    run["remote_job"] = info

    # ① pre-check: no launch call, no Job.
    # flowgate.v02.0129 CH0012 (approved (B)): a user's own AI runs only for its owner. Admission
    # already refuses another requester; this keeps the remote boundary closed on its own.
    if requester is not None and run.get("issued_to") != requester:
        return _final(run, provider, UNAVAILABLE, "requester_not_owner")
    failure = agent_failure(target, requester)
    if failure is not None:
        return _final(run, provider, *failure)
    base_problem = api_base_failure(run.get("api_base_url"))
    if base_problem is not None:
        return _final(run, provider, API_BASE, base_problem)
    kind = provider.get("kind") or ""
    command = ai_settings_service.normalize_cli_command(kind, (provider.get("cli_command") or "").strip())
    if not command or "\n" in command or "\r" in command:
        return _final(run, provider, REMOTE_COMMAND_FALLBACK, "cli_command not set")
    # Defensive (§3-4 rule 4): the run this one replaces -- an earlier attempt of the same
    # run, the same Graph execution / node, or the group's previous run -- must have no
    # remote Job still alive. Normally the held lease already refuses such a run.
    if remote_supervisor.prior_remote_unsettled(run):
        return _final(run, provider, PREVIOUS_UNSETTLED, "previous_remote_job_not_settled")
    # (마) is not active before the Agent working-directory contract is verified (§9).
    if not AGENT_WORKDIR_CONTRACT_VERIFIED:
        return _final(run, provider, WORKDIR_UNVERIFIED, "agent_workdir_contract_unverified")
    # flowgate.v02.0135 CH0006: the Job runs in the Agent's own configured directory.
    if _agent_workdir(target) is None:
        return _final(run, provider, WORKDIR_UNSET, "remote_workdir_not_set", cause=CAUSE_WORKDIR_UNSET)

    # ② the same launch approval the local boundary asks for, before any Job exists.
    if graph_execution_hooks.call("launch", run) is False:
        return "spawn_failed", "Graph execution lease refused"
    # ③ re-check: the Agent may have changed while waiting for capacity.
    if run["cancel_event"].is_set():
        return _final(run, provider, CANCELLED, "cancelled_before_job")
    failure = agent_failure(target, requester)
    if failure is not None:
        return _final(run, provider, *failure)

    template = _agent_workdir(target)     # read again: the setting may have changed meanwhile
    if template is None:
        return _final(run, provider, WORKDIR_UNSET, "remote_workdir_not_set", cause=CAUSE_WORKDIR_UNSET)

    svc = agent_jobs.AgentJobService()
    job_id = "job_" + secrets.token_urlsafe(20)
    attempt_id = "att_" + secrets.token_urlsafe(20)
    owner = agent_jobs.retrieval_owner_for(run.get("run_id"))
    workdir = job_workdir(template, job_id)
    info.update(job_id=job_id, attempt_id=attempt_id, workdir=workdir)
    timeout_seconds = max(1, int(_absolute_remaining_sec(run)))
    try:
        svc.enqueue_remote_cli(
            command_line=command, stdin_text=_sanitize_prompt(prompt, run),
            cwd=workdir, timeout_seconds=timeout_seconds,
            job_id=job_id, attempt_id=attempt_id, target_agent_id=target,
            # The requester follows the connection's owner rule (§3-1); the project is the
            # run's own, for personal and shared connections alike (T0008 item 1).
            requested_by_user_id=requester, project_id=run.get("project_id") or None,
            ai_run_id=str(run.get("run_id") or ""), graph_exec_id=run.get("graph_exec_id"),
            retrieval_owner=owner, ai_group_id=run.get("group_id") or None,
            ai_token_id=run.get("token_id") or None,
        )
    except AgentError as exc:
        return _final(run, provider, JOB_REFUSED, exc.code)
    run["remote_job_active"] = True
    run["_remote_watch_live"] = True
    role_invocations.cli_prepared(run, provider, "remote_agent")
    launched = time.monotonic()
    try:
        try:
            _watch(svc, run, job_id, attempt_id, info)
        except Exception:
            # Polling / lookup failed: the Job may still run. Ask it to stop and leave it
            # to the supervisor; the run stays alive below until the Job is settled.
            logger.warning("ai-invoke %s: remote Job watch failed; handed to the supervisor",
                           run.get("run_id"), exc_info=True)
            _cancel_quietly(svc, run, job_id, attempt_id)
            return _final(run, provider, WATCH_LOST, "watch_failed")
        try:
            result = svc.collect_ai_result(job_id, attempt_id, owner)
        except Exception:
            logger.warning("ai-invoke %s: remote Job result retrieval failed", run.get("run_id"), exc_info=True)
            return _final(run, provider, RESULT_LOST, "retrieval_failed")
        info.update(status=result["status"], claimed_agent_id=result.get("lease_owner_agent_id"),
                    terminal_cause=result.get("terminal_cause"))
        return _settle(run, provider, kind, result, launched, role_invocations)
    finally:
        try:
            svc.release_ai_retrieval(job_id, attempt_id, owner)
        except Exception:
            logger.warning("ai-invoke %s: remote job scrub deferred", run.get("run_id"), exc_info=True)
        run["_remote_watch_live"] = False
        # §3-4: only a settled Job (terminal + fenced when unconfirmed) lets the run stop.
        # Anything else keeps the provider "alive", so terminal cleanup parks the leases
        # and the supervisor resumes them once the Job is settled.
        run["remote_job_active"] = not _job_settled(svc, run, job_id, attempt_id, info)


def _cancel_quietly(svc, run: dict, job_id: str, attempt_id: str) -> None:
    try:
        svc.cancel_ai_job(job_id, attempt_id)
    except Exception:
        logger.warning("ai-invoke %s: remote Job cancel request deferred to the supervisor",
                       run.get("run_id"), exc_info=True)


def _job_settled(svc, run: dict, job_id: str, attempt_id: str, info: dict) -> bool:
    """Read the durable Job: terminal, and fenced when its end was never reported."""
    from modules.flow_gate.services.agent_jobs import CAUSE_SERVER_EXPIRED, TERMINAL
    from . import remote_supervisor
    try:
        row = svc.get_ai_job(job_id, attempt_id)
    except Exception:
        logger.warning("ai-invoke %s: remote Job state unreadable; run kept alive", run.get("run_id"), exc_info=True)
        return False
    info["status"] = row.get("status")
    if row.get("terminal_cause"):
        info["terminal_cause"] = row["terminal_cause"]
    if row.get("status") not in TERMINAL:
        return False
    if row.get("terminal_cause") == CAUSE_SERVER_EXPIRED and not row.get("fenced_at"):
        if not remote_supervisor.fence_job(svc, row):
            return False
        run["remote_token_revoked"] = True
    return True


class _RemoteProgress:
    """The local watchdog's two strong signals, observed by the server for a remote attempt.

    A remote CLI writes through this server (documents via the API, source via the
    remote source tools), so the group's document max-seq and ``git status`` of the
    group worktree move here when it works. Process liveness is not observable and is
    not used. The window is anchored when the Job is STARTED (the process start).
    """

    def __init__(self, run: dict):
        self.run = run
        root = run.get("source_root")
        self.source_root = Path(root) if root else None
        self.git_enabled = self.source_root is not None and self.source_root.is_dir()
        self.git_mark = run.get("dirty_baseline")
        self.doc_mark = run.get("baseline_seq")
        self.started = False
        self.next_sample = 0.0
        run["watchdog_kill"] = None

    def stalled(self, status: str) -> bool:
        from . import oracle
        from modules.flow_gate.db.connection import now_iso
        run = self.run
        now = _svc()._now_mono()
        if status != "STARTED":
            return False
        if not self.started:
            self.started = True
            run["stall_anchor_mono"] = now
            self.next_sample = now + REMOTE_PROGRESS_INTERVAL_SEC
            return False
        if now < self.next_sample:
            return False
        self.next_sample = now + REMOTE_PROGRESS_INTERVAL_SEC
        moved, readable = [], True
        seq = oracle._observe_group_max_seq(run)
        if seq is None:
            readable = False
        elif self.doc_mark is None:
            self.doc_mark = seq
        elif seq > self.doc_mark:
            self.doc_mark = seq
            moved.append("document")
        if self.git_enabled:
            paths = _svc()._git_status_paths(self.source_root)
            if paths is None:
                readable = False
            elif self.git_mark is None:
                self.git_mark = paths
            elif paths != self.git_mark:
                self.git_mark = paths
                moved.append("source")
        if moved:
            run["stall_anchor_mono"] = now
            run["last_progress_mono"] = now
            run["last_progress_at"] = now_iso()
            run["last_progress_signal"] = ",".join(moved)
            run["progress_observations"] = int(run.get("progress_observations") or 0) + 1
            return False
        if not readable or not run.get("timeout_sec"):
            return False
        return now - run.get("stall_anchor_mono", now) >= float(run["timeout_sec"])


def _mark_watchdog(run: dict, kind: str) -> None:
    """The same in-memory verdict the local watchdog leaves: ends as end_reason timeout."""
    now = _svc()._now_mono()
    anchor = run.get("stall_anchor_mono") or run.get("started_mono") or now
    run["watchdog_kill"] = {
        "kind": kind, "transport": "remote_agent",
        "stalled_sec": int(max(0.0, now - anchor)),
        "elapsed_sec": int(max(0.0, now - (run.get("started_mono") or now))),
        "threshold_sec": int(run.get("timeout_sec") or 0),
        "absolute_cap_sec": _absolute_cap_sec(),
        "last_progress_at": run.get("last_progress_at"),
        "progress_observations": int(run.get("progress_observations") or 0),
        "attempt_no": int(run.get("attempt_no") or 0),
    }
    run["timed_out"] = True
    logger.warning("ai-invoke %s: remote %s -- asking the Agent Job to stop", run.get("run_id"), kind)


def _watch(svc, run: dict, job_id: str, attempt_id: str, info: dict) -> dict:
    """Wait for the Job to become terminal. Every stage is bounded by the queue limits.

    User cancel / Stop / lease loss (``cancel_event``), the absolute ceiling and the
    no-progress watchdog each turn into one Job cancel request; the watch then goes on
    until the Job is terminal, so the run's leases are held for the whole remote life.
    """
    from modules.flow_gate.services.agent_jobs import TERMINAL
    cancel_sent = False
    progress = _RemoteProgress(run)
    while True:
        svc.advance_ai_jobs()
        row = svc.get_ai_job(job_id, attempt_id)
        if row.get("lease_owner_agent_id"):
            info["claimed_agent_id"] = row["lease_owner_agent_id"]
        info["status"] = row["status"]
        if row["status"] in TERMINAL:
            return row
        if not cancel_sent:
            reason = None
            if run["cancel_event"].is_set():
                reason = "cancel"
            elif _absolute_remaining_sec(run) <= 0:
                _mark_watchdog(run, "absolute_cap")
                reason = "absolute_cap"
            elif progress.stalled(row["status"]):
                _mark_watchdog(run, "no_progress")
                reason = "no_progress"
            if reason is not None:
                svc.cancel_ai_job(job_id, attempt_id)
                info["cancel_reason"] = reason
                cancel_sent = True
                continue
            run["cancel_event"].wait(REMOTE_POLL_SEC)
        else:
            time.sleep(REMOTE_POLL_SEC)


def _settle(run, provider, kind, result, launched, role_invocations):
    from modules.flow_gate.services import agent_jobs
    from . import oracle
    target = run["remote_job"].get("target_agent_id")
    claimed = result.get("lease_owner_agent_id")
    status = result["status"]
    cancelled = run["cancel_event"].is_set()
    if claimed and claimed != target:
        return _final(run, provider, AGENT_MISMATCH, "claimed_by_other_agent")
    if status == "CANCELLED" and not claimed:
        if cancelled:
            return _final(run, provider, CANCELLED, "cancelled_before_claim")
        if run.get("timed_out"):
            return _final(run, provider, NO_CLAIM, "timeout_before_claim")
        # flowgate.v02test.0022 CH0002: the code/reason stay as before; the server's verdict
        # on why the target Agent never claimed (agent_offline, claim_refused, agent_busy,
        # claim_polling_stopped, polled_not_taken, agent_missing) rides in cause/message.
        return _final(run, provider, NO_CLAIM, "not_claimed",
                      cause=result.get("claim_wait_cause"), message=result.get("diagnostics"))
    if result.get("terminal_cause") == agent_jobs.CAUSE_SERVER_EXPIRED:
        return _final(run, provider, UNCONFIRMED, "no_terminal_report")
    if status == "REJECTED":
        reason, cause, message = _agent_reported(run, result, "agent_rejected", "rejected")
        return _final(run, provider, REJECTED, reason, cause=cause, message=message)
    if not result.get("collected"):
        return _final(run, provider, RESULT_LOST, "retrieval_not_owned")
    if not result.get("started_at") and status in ("CANCELLED", "FAILED") and not cancelled:
        # Claimed but never started: stopped by the server's start limit, or the Agent
        # gave up before starting the CLI.
        if status == "FAILED":
            reason, cause, message = _agent_reported(run, result, "agent_failed", "failed_before_start")
            return _final(run, provider, NOT_STARTED, reason, cause=cause, message=message)
        return _final(run, provider, NO_CLAIM, "not_started")

    out_text = result.get("stdout") or ""
    err_text = result.get("stderr") or ""
    timed_out = status == "TIMED_OUT" or bool(run.get("timed_out"))
    exit_code = result.get("child_exit_code")
    run["stdout_tail"] = out_text[-OUTPUT_TAIL_BYTES:]
    run["stderr_tail"] = err_text[-OUTPUT_TAIL_BYTES:]
    run["exit_code"] = exit_code if not timed_out else None
    if timed_out:
        run["timed_out"] = True
    role_invocations.cli_outcome(
        run, stdout_full=out_text,
        exit_code=exit_code if not timed_out and not cancelled else None, timed_out=timed_out,
        process_exited_itself=not timed_out and not cancelled and not run.get("user_paused"))
    elapsed = float(result.get("duration_seconds") or (time.monotonic() - launched))
    if (not cancelled and not timed_out and exit_code not in (None, 0)
            and elapsed < _svc().FAST_FAIL_WINDOW_SEC and not oracle._work_landed(run)):
        detail = (err_text or out_text).strip()[-500:] or f"exit {exit_code} within {int(elapsed)}s"
        # It already ran on the target Agent: never hand the same work to another place.
        _final(run, provider, FAST_FAIL, f"exit {exit_code}")
        return "fast_fail", detail
    _recover_last_message(run, kind, out_text)
    return "started_ok", None


def _recover_last_message(run: dict, kind: str, out_text: str) -> None:
    """codex's --output-last-message file is a server path; remotely, stdout is the source."""
    from . import oracle
    from .provider_cli import _cli_last_message
    message = _cli_last_message("claude" if kind == "codex" else kind, out_text,
                                Path("__remote_no_last_message_file__"))
    run["last_message"] = oracle._truncate_front(message)
    run["last_message_received"] = bool(message)
