"""A bounded, owner-only policy conversation. It never saves the policy for the user.

Local/API calls reuse the shared prompt transport. Remote CLI calls use addressed
Agent jobs with private result retrieval; no project, worktree or worker write token.
"""
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel, Field
from modules.flow_gate.settings import ai_settings_service as settings
from modules.flow_gate.services import ai_connection_test_service as connection
from modules.flow_gate.services.ai_invoke import runtime, provider_remote, provider_cli

# (CH): the final turn returns the whole draft and is the slowest one. A remote Agent's queue and
# start-up time no longer count against this: TIMEOUT starts when the Job is STARTED (see _remote).
TIMEOUT = 240
# Output ceiling of one API reply: a 4000-character Korean draft plus message and JSON must never be cut off.
MAX_TOKENS = 8000
_log = logging.getLogger(__name__)
_lock = threading.RLock()
_requests = {}


class Choice(BaseModel):
    label: str = Field(min_length=1, max_length=300)
    recommended: bool = False


class Reply(BaseModel):
    message: str = Field(default="", max_length=4000)
    choices: list[Choice] = Field(default_factory=list, max_length=4)
    policy: str | None = Field(default=None, max_length=4000)


# (CH): ask the same AI again before the user sees a failure.
# (CH): up to two more times (three in all), also after a timeout or a transient run failure; a
# second timeout ends it so the user never waits more than two TIMEOUTs on one reply. An AI that is off or
# misconfigured (provider_unavailable, remote_agent_*, provider_launch_failed) is not asked again.
MAX_ATTEMPTS = 3
RETRY_SILENT = {"timeout", "execution_failed", "transport_failed", "remote_execution_failed"}
RETRY_NOTES = {
    "reply_invalid": "Your previous reply could not be read. Reply again with ONLY the JSON object described "
                     "above: no text, heading or code fence before or after it.",
    "policy_too_long": "Your previous policy draft was longer than 4000 characters. Reply again in the same JSON "
                       "shape with a shorter policy (under 3000 characters).",
}


def providers(user_id):
    rows = settings.resolve_global().get("providers", []) + settings.personal_selectable(user_id)
    return [{"id": row["id"], "name": row["name"]} for row in rows]


def prompt(body, messages, locale):
    language = {"ko": "Korean", "ja": "Japanese", "en": "English"}.get(locale, "Korean")
    return ("Help the user define when an AI should ask them questions versus decide autonomously. "
            "This is a conversation only: do not use tools, read files, run commands or change anything. "
            "Ask ONE question at a time in at most two short sentences, with 2-4 short choices and one "
            "recommendation where helpful. The screen shows the choices as buttons: never repeat them in message. "
            "Accept free-form answers. Existing policy is provided: when editing, ask only what should change. "
            # (CH): the conversation also assesses which questions this user can answer.
            "This conversation also assesses the user: between the policy questions, show 3-5 sample questions "
            "that an AI working on a task might really ask, one per turn and each of a different kind (an "
            "implementation detail such as an index or a library, a choice between two visible screen results, a "
            "speed the user would notice, an infrastructure or permission need, deleting production data). Put the "
            "sample question itself in message, e.g. 'If the working AI asked you this, could you answer? - ...', "
            "with choices such as answer right away / could decide if shown the result / do not know, let the AI "
            "decide; the user may also simply answer it. From the answers judge which kinds of question the user "
            "can answer, which they cannot (the AI should decide those) and which must always be asked, and in "
            "which form questions get answered. Put that judgement into the policy as bullets under the matching "
            "headings with a short reason, e.g. 'decide on its own: index or library choices (the user cannot "
            "judge these)' or 'ask with the visible result as A/B'. When there is no existing policy the "
            "assessment is part of the conversation. When a policy exists, ask once in the first question whether "
            "to run the assessment again, as a choice, and run it only if the user agrees. "
            "Do not invent user preferences. Once understood, put a one-sentence summary in message (with the "
            "assessment in one more sentence when it was run) and the "
            "complete policy draft in policy for the user to confirm; do not repeat the draft in message. "
            "The user alone confirms and saves. Write the policy as Markdown bullet lists under three headings, "
            "in this order: what the AI decides on its own, what the AI asks the user, exceptions. "
            "Keep each bullet to one line. "
            "Do not repeatedly ask questions already answered. Reply in " + language + ". "
            "Return ONLY JSON: {message: string, choices: [{label: string, recommended: boolean}], "
            "policy: null or complete policy text up to 3000 characters}. Keys must be quoted. "
            "Use policy=null while asking; nonempty policy when ready to confirm.\n" +
            json.dumps({"existing_policy": body, "conversation": messages}, ensure_ascii=False))


def _json_objects(text):
    """Every JSON object in the text, outermost first: a sentence or a code fence around it is ignored."""
    # (CH): strict=False reads a multi-line Markdown draft whose newlines were not escaped.
    decoder = json.JSONDecoder(strict=False)
    index = text.find("{")
    while index != -1:
        try:
            value, _ = decoder.raw_decode(text, index)
        except ValueError:
            value = None
        if isinstance(value, dict):
            yield value
        index = text.find("{", index + 1)


def _choices(raw):
    choices = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            item = {"label": item}
        label = item.get("label") if isinstance(item, dict) else None
        if not isinstance(label, str) or not label.strip():
            continue
        recommended = item.get("recommended") in (True, "true")
        choices.append({"label": label.strip()[:300], "recommended": recommended})
        if len(choices) == 4:
            break
    return choices


def parse_reply(text):
    """Reads the AI's answer leniently: unknown keys are dropped, choices past four are cut and a blank policy
    means it is still asking. Only an answer without any readable object or one whose policy is too long to save
    fails (reply_invalid / policy_too_long), and the worker asks the same AI once more first."""
    for data in _json_objects(text or ""):
        if "message" not in data and "policy" not in data:
            continue
        message = data.get("message")
        message = message.strip()[:4000] if isinstance(message, str) else ""
        policy = data.get("policy")
        policy = policy.strip() if isinstance(policy, str) and policy.strip() else None
        if not message and not policy:
            continue
        if policy is not None and len(policy) > 4000:
            raise ValueError("policy_too_long")
        return Reply.model_validate({"message": message, "choices": _choices(data.get("choices")),
                                     "policy": policy}).model_dump()
    raise ValueError("reply_invalid")


def _ask(provider, text, user_id, request_id):
    note, timeouts = "", 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        answer = None
        try:
            answer = _execute(provider, text + note, user_id, request_id)
            return parse_reply(answer)
        except ValueError as exc:
            code = str(exc)
            # (CH): leave the cause on the server. Code, attempt and sizes only: never prompt or reply text.
            _log.warning("question policy assistant attempt failed: request=%s attempt=%d/%d code=%s exec=%s "
                         "kind=%s prompt_chars=%d reply_chars=%s", request_id, attempt, MAX_ATTEMPTS, code,
                         provider.get("exec_type"), provider.get("kind"), len(text + note),
                         len(answer) if isinstance(answer, str) else "-")
            timeouts += code == "timeout"
            retriable = code in RETRY_NOTES or code in RETRY_SILENT
            if not retriable or attempt == MAX_ATTEMPTS or timeouts > 1:
                raise
            note = "\n" + RETRY_NOTES[code] if code in RETRY_NOTES else note


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
    # (CH): TIMEOUT counts from STARTED. Before that the server itself ends a Job nobody claimed
    # (AI_CLAIM_WAIT_SECONDS) or started (AI_START_WAIT_SECONDS); this cap only guards against a stuck row.
    waiting_until = time.monotonic() + agent_jobs.AI_CLAIM_WAIT_SECONDS + agent_jobs.AI_START_WAIT_SECONDS + 30
    deadline = None
    terminal = False
    try:
        while True:
            row = svc.get_ai_job(job, attempt)
            if row["status"] in agent_jobs.TERMINAL:
                terminal = True
                result = svc.collect_ai_result(job, attempt, owner)
                if result.get("status") != "DONE" and result.get("started_at") is None:
                    # Never picked up or never started (the Agent was busy or gone): a wait, not a bad run.
                    raise ValueError("timeout")
                if (not result.get("collected") or result.get("child_exit_code") != 0
                        or result.get("status") != "DONE"):
                    raise ValueError("remote_execution_failed")
                kind = "claude" if provider["kind"] == "codex" else provider["kind"]
                return provider_cli._cli_last_message(kind, result.get("stdout") or "",
                    Path("__remote_no_last_message_file__")) or ""
            now = time.monotonic()
            if deadline is None and row["status"] == "STARTED":
                deadline = now + TIMEOUT
            if deadline is None and now >= waiting_until:
                raise ValueError("timeout")
            if deadline is not None and now >= deadline:
                raise ValueError("timeout")
            time.sleep(1)
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
    outcome = connection.execute_prompt(subject, text, runtime.ProbeHandle(), TIMEOUT, max_tokens=MAX_TOKENS)
    if outcome.kind != runtime.PROBE_OK:
        raise ValueError(connection.FAILURE_REASON.get(outcome.kind, "execution_failed"))
    return outcome.text


def _worker(request_id, user_id, provider, text):
    try:
        result = {"status": "completed", "reply": _ask(provider, text, user_id, request_id)}
    except Exception as exc:
        # Transport diagnostics can contain prompts or credentials. Return a code only.
        code = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "provider_unavailable", "remote_agent_offline", "remote_agent_unavailable",
            "remote_agent_workdir_unset", "remote_agent_workdir_unverified", "timeout", "remote_execution_failed",
            "transport_failed", "provider_launch_failed", "execution_failed", "reply_invalid",
            "policy_too_long"} else "policy_assistant_failed"
        if code == "policy_assistant_failed":
            _log.warning("question policy assistant failed: request=%s error=%s", request_id, type(exc).__name__)
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
