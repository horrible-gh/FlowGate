"""A bounded, owner-only policy conversation. It never saves the policy for the user.

Local/API calls reuse the shared prompt transport. Remote CLI calls use addressed
Agent jobs with private result retrieval; no project, worktree or worker write token.
"""
import json
import secrets
import threading
import time
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel, Field, ConfigDict
from modules.flow_gate.settings import ai_settings_service as settings
from modules.flow_gate.services import ai_connection_test_service as connection
from modules.flow_gate.services.ai_invoke import runtime, provider_remote, provider_cli

TIMEOUT = 180
_lock = threading.RLock()
_requests = {}


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=300)
    recommended: bool = False


class Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    choices: list[Choice] = Field(default_factory=list, max_length=4)
    policy: str | None = Field(default=None, max_length=4000)


def providers(user_id):
    rows = settings.resolve_global().get("providers", []) + settings.personal_selectable(user_id)
    return [{"id": row["id"], "name": row["name"]} for row in rows]


def prompt(body, messages, locale):
    language = {"ko": "Korean", "ja": "Japanese", "en": "English"}.get(locale, "Korean")
    return ("Help the user define when an AI should ask them questions versus decide autonomously. "
            "This is a conversation only: do not use tools, read files, run commands or change anything. "
            "Ask ONE short question at a time with 2-4 choices and one recommendation where helpful. "
            "Accept free-form answers. Existing policy is provided: when editing, ask only what should change. "
            "Do not invent user preferences. Once understood, explain your understanding and return the complete "
            "policy draft for the user to confirm. The user alone confirms and saves. "
            "Do not repeatedly ask questions already answered. Reply in " + language + ". "
            "Return ONLY JSON: {message: string, choices: [{label: string, recommended: boolean}], "
            "policy: null or complete policy text up to 4000 characters}. Keys must be quoted. "
            "Use policy=null while asking; nonempty policy when ready to confirm.\n" +
            json.dumps({"existing_policy": body, "conversation": messages}, ensure_ascii=False))


def parse_reply(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    reply = Reply.model_validate_json(text)
    if reply.policy is not None and not reply.policy.strip():
        raise ValueError("empty policy")
    return reply.model_dump()


def _remote(provider, text, user_id, request_id):
    from modules.flow_gate.services import agent_jobs
    binding = provider_remote._binding(provider)
    target, requester = binding["target_agent_id"], binding["requester"]
    if requester is not None and requester != user_id:
        raise ValueError("provider_unavailable")
    failure = provider_remote.agent_failure(target, requester)
    if failure:
        raise ValueError(failure[0])
    if not provider_remote.AGENT_WORKDIR_CONTRACT_VERIFIED:
        raise ValueError("remote_agent_workdir_unverified")
    template = provider_remote._agent_workdir(target)
    if not template:
        raise ValueError("remote_agent_workdir_unset")
    command = settings.normalize_cli_command(provider["kind"], provider.get("cli_command") or "")
    svc = agent_jobs.AgentJobService()
    job, attempt = "job_" + secrets.token_urlsafe(20), "att_" + secrets.token_urlsafe(20)
    owner = agent_jobs.retrieval_owner_for(request_id)
    svc.enqueue_remote_cli(command_line=command, stdin_text=text,
        cwd=provider_remote.job_workdir(template, job), timeout_seconds=TIMEOUT,
        job_id=job, attempt_id=attempt, target_agent_id=target,
        requested_by_user_id=requester, ai_run_id=request_id, retrieval_owner=owner)
    deadline = time.monotonic() + TIMEOUT
    terminal = False
    try:
        while time.monotonic() < deadline:
            row = svc.get_ai_job(job, attempt)
            if row["status"] in agent_jobs.TERMINAL:
                terminal = True
                result = svc.collect_ai_result(job, attempt, owner)
                if (not result.get("collected") or result.get("child_exit_code") != 0
                        or result.get("status") != "DONE"):
                    raise ValueError("remote_execution_failed")
                kind = "claude" if provider["kind"] == "codex" else provider["kind"]
                return provider_cli._cli_last_message(kind, result.get("stdout") or "",
                    Path("__remote_no_last_message_file__")) or ""
            time.sleep(1)
        raise ValueError("timeout")
    finally:
        if not terminal:
            svc.cancel_ai_job(job, attempt)
        svc.release_ai_retrieval(job, attempt, owner)


def _execute(provider, text, user_id, request_id):
    if provider_remote.is_remote_cli(provider):
        return _remote(provider, text, user_id, request_id)
    # Use the already owner-authorized explicit selection, including personal providers.
    credential = settings.get_provider_secret(None, provider["id"]) if provider["exec_type"] == "api" else None
    if provider["exec_type"] == "api" and not credential:
        raise ValueError("provider_unavailable")
    command = settings.normalize_cli_command(provider["kind"], provider.get("cli_command") or "") if provider["exec_type"] == "cli" else None
    subject = connection.ConnectionSubject(connection.EVALUATION_SCOPE, provider["id"], "system",
        provider["name"], provider["exec_type"], provider["kind"], command,
        (provider.get("api_base_url") or "").rstrip("/"), provider.get("api_model"), credential,
        "", connection.FINGERPRINT_VERSION)
    outcome = connection.execute_prompt(subject, text, runtime.ProbeHandle(), TIMEOUT, max_tokens=3500)
    if outcome.kind != runtime.PROBE_OK:
        raise ValueError(connection.FAILURE_REASON.get(outcome.kind, "execution_failed"))
    return outcome.text


def _worker(request_id, user_id, provider, text):
    try:
        reply = parse_reply(_execute(provider, text, user_id, request_id))
        result = {"status": "completed", "reply": reply}
    except Exception as exc:
        # Transport diagnostics can contain prompts or credentials. Return a code only.
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "provider_unavailable", "remote_agent_offline", "remote_agent_unavailable",
            "remote_agent_workdir_unset", "timeout", "remote_execution_failed",
            "transport_failed", "provider_launch_failed", "execution_failed"} else "policy_assistant_failed"
        result = {"status": "failed", "error_code": code}
    with _lock:
        _requests[request_id].update(result, ended=time.monotonic())


def start(user_id, provider_id, body, messages, locale):
    if not provider_id:
        raise HTTPException(422, detail={"code": "provider_required"})
    provider = settings.selectable_provider(None, provider_id, user_id)
    if provider is None:
        raise HTTPException(409, detail={"code": "provider_unavailable"})
    if len(body) > 4000 or len(messages) > 100 or sum(len(m["content"]) for m in messages) > 40000:
        raise HTTPException(422, detail={"code": "policy_conversation_too_long"})
    with _lock:
        now = time.monotonic()
        for key, row in list(_requests.items()):
            if row.get("ended") and now - row["ended"] > 3600:
                del _requests[key]
        if any(r["user_id"] == user_id and r["status"] == "running" for r in _requests.values()):
            raise HTTPException(409, detail={"code": "policy_assistant_busy"})
        request_id = "policy_" + secrets.token_urlsafe(20)
        _requests[request_id] = {"user_id": user_id, "status": "running"}
        try:
            threading.Thread(target=_worker, args=(request_id, user_id, provider, prompt(body, messages, locale)),
                             daemon=True, name="question-policy-assistant").start()
        except Exception:
            del _requests[request_id]
            raise
    return {"request_id": request_id, "status": "running"}


def result(user_id, request_id):
    with _lock:
        row = _requests.get(request_id)
        if row is None or row["user_id"] != user_id:
            raise HTTPException(404, detail={"code": "policy_assistant_request_not_found"})
        return {key: value for key, value in row.items() if key not in ("user_id", "ended")}
