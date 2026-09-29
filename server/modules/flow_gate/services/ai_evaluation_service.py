"""Project AI Evaluation lifecycle and isolated baseline execution."""
from __future__ import annotations

import http.client
import json
import re
import socket
import subprocess
from urllib.parse import urlsplit
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
from modules.flow_gate.db import ai_evaluation_runs as repo
from modules.flow_gate.db import ai_providers
from modules.flow_gate.services import process_runner
from modules.flow_gate.services.ai_evaluation_fingerprint import FINGERPRINT_VERSION, subject_fingerprint
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.utils.api_key_crypto import ApiKeyCryptoError

BASELINE = ("Reply with one short sentence confirming that you can answer this harmless test. "
            "Do not call tools, modify files, run commands, or disclose secrets.")
TIMEOUT_SEC = 60
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.RLock()
_SECRET_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|api[_ -]?key\s*[:=]\s*)[^\s,;]+")


class EvaluationError(Exception):
    def __init__(self, code: str, status: int, evaluation_id: str | None = None):
        self.code, self.status, self.evaluation_id = code, status, evaluation_id
        super().__init__(code)


@dataclass(frozen=True)
class EvaluationExecutionSubject:
    project_id: str
    provider_id: str
    scope: str
    name: str
    exec_type: str
    kind: str
    cli_command: str | None
    api_base_url: str | None
    api_model: str | None
    credential: str | None
    fingerprint: str
    version: int


def _effective(project_id: str) -> dict:
    return ai_settings_service.resolve_effective(project_id)


def _resolve(project_id: str, provider_id: str) -> EvaluationExecutionSubject:
    view = _effective(project_id)
    scope = view["source"]
    if scope == "disabled":
        raise EvaluationError("provider_unavailable", 409)
    provider = next((p for p in view["providers"] if p["id"] == provider_id), None)
    if provider is None:
        stored = ai_providers.get_row(None if scope == "system" else project_id, provider_id)
        if stored is None:
            raise EvaluationError("provider_not_found", 404)
        raise EvaluationError("provider_disabled" if not stored.get("enabled") else "provider_unavailable", 409)
    provider = dict(provider)
    if provider["exec_type"] == "cli" and provider.get("cli_command"):
        provider["cli_command"] = ai_settings_service.normalize_cli_command(
            provider["kind"], provider["cli_command"])
    if provider["exec_type"] == "api" and provider.get("api_base_url"):
        provider["api_base_url"] = provider["api_base_url"].rstrip("/")
    credential = None
    if provider["exec_type"] == "api":
        try:
            credential = ai_settings_service.get_provider_secret(
                None if scope == "system" else project_id, provider_id)
        except ApiKeyCryptoError as exc:
            raise EvaluationError("provider_unavailable", 409) from exc
        if not credential:
            raise EvaluationError("provider_unavailable", 409)
    fingerprint = subject_fingerprint(project_id=project_id, provider_id=provider_id,
        scope=scope, provider=provider, credential=credential)
    return EvaluationExecutionSubject(project_id, provider_id, scope, provider["name"],
        provider["exec_type"], provider["kind"], provider.get("cli_command"),
        provider.get("api_base_url"), provider.get("api_model"), credential,
        fingerprint, FINGERPRINT_VERSION)


def _emit(kind: str, row: dict) -> None:
    payload = {"type": "ai_evaluation." + kind, "project_id": row["project_id"],
               "evaluation_id": row["evaluation_id"], "provider_id": row["provider_id"],
               "lifecycle": row["lifecycle"], "verdict": row["verdict"]}
    broadcast_event_threadsafe(FlowEvent("ai_evaluation." + kind, payload, "*",
                                       project=row["project_id"]))


def _safe_summary(message: str | None, credential: str | None = None) -> str | None:
    if not message:
        return None
    text = message.replace(credential, "[redacted]") if credential else message
    return _SECRET_PATTERN.sub(lambda m: m.group(1) + "[redacted]", text)[:2000]


def _public(row: dict, current: EvaluationExecutionSubject | None = None) -> dict:
    result = {k: row.get(k) for k in (
        "evaluation_id", "project_id", "provider_id", "subject_fingerprint", "lifecycle",
        "verdict", "reason_code", "diagnostic_summary", "created_at", "started_at", "finished_at")}
    result["provider_name"] = row.get("provider_name_snapshot")
    result["is_stale"] = (current is None or row["subject_fingerprint_version"] != current.version
                          or row["subject_fingerprint"] != current.fingerprint)
    return result


def _current_subject(project_id: str, provider_id: str) -> EvaluationExecutionSubject | None:
    try:
        return _resolve(project_id, provider_id)
    except EvaluationError:
        return None


def start(project_id: str, provider_id: str, created_by: str | None) -> dict:
    subject = _resolve(project_id, provider_id)  # Frozen before 202 and used unchanged by worker.
    try:
        row = repo.create_pending(project_id=project_id, provider_id=provider_id,
            provider_name=subject.name, fingerprint=subject.fingerprint,
            version=subject.version, created_by=created_by)
    except repo.AlreadyRunning as exc:
        raise EvaluationError("evaluation_already_running", 409, exc.evaluation_id) from exc
    state = {"cancel": threading.Event(), "proc": None, "transport": None, "subject": subject}
    with _RUNS_LOCK:
        _RUNS[row["evaluation_id"]] = state
    _emit("updated", row)
    try:
        threading.Thread(target=_worker, args=(row["evaluation_id"], state), daemon=True,
                         name="ai-evaluation-" + row["evaluation_id"][-8:]).start()
    except Exception:
        with _RUNS_LOCK:
            _RUNS.pop(row["evaluation_id"], None)
        repo.finish(project_id, row["evaluation_id"], lifecycle="failed", reason_code="internal_error")
        raise
    return _public(row, subject)


def _safety_available(subject: EvaluationExecutionSubject) -> bool:
    # Identity/enabled/effective only. Never take config or credentials from this read.
    try:
        view = _effective(subject.project_id)
    except LookupError:
        return False
    return (view["source"] == subject.scope and
            any(p["id"] == subject.provider_id for p in view["providers"]))


def _close_api_transport(state: dict) -> None:
    connection = state.get("transport")
    if connection is None:
        return
    sock = connection.sock
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
    connection.close()


def _api_execution(subject: EvaluationExecutionSubject, state: dict) -> str:
    if not subject.api_base_url or not subject.api_model or not subject.credential:
        raise EvaluationError("provider_launch_failed", 500)
    base = subject.api_base_url.rstrip("/")
    if subject.kind == "claude":
        url = base + "/v1/messages"
        body = {"model": subject.api_model, "max_tokens": 100,
                "messages": [{"role": "user", "content": BASELINE}]}
        headers = {"x-api-key": subject.credential, "anthropic-version": "2023-06-01"}
    else:
        url = base + "/chat/completions"
        body = {"model": subject.api_model, "messages": [{"role": "user", "content": BASELINE}]}
        headers = {"Authorization": "Bearer " + subject.credential}
    connection = None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("invalid API URL")
        connection_type = (http.client.HTTPSConnection if parsed.scheme == "https"
                           else http.client.HTTPConnection)
        connection = connection_type(parsed.hostname, parsed.port, timeout=TIMEOUT_SEC)
        with _RUNS_LOCK:
            state["transport"] = connection
            if state["cancel"].is_set():
                raise EvaluationError("cancelled", 409)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        connection.request("POST", path, json.dumps(body).encode("utf-8"),
                           {"Content-Type": "application/json", **headers})
        response = connection.getresponse()
        if response.status >= 400:
            raise OSError(f"API returned HTTP {response.status}")
        data = json.loads(response.read().decode("utf-8"))
    except TimeoutError as exc:
        raise EvaluationError("timeout", 500) from exc
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise EvaluationError("transport_failed", 500) from exc
    finally:
        with _RUNS_LOCK:
            if state.get("transport") is connection:
                state["transport"] = None
        if connection is not None:
            connection.close()
    if subject.kind == "claude":
        return "\n".join(part.get("text", "") for part in data.get("content", [])
                         if part.get("type") == "text")
    choices = data.get("choices") or []
    return str((choices[0].get("message") or {}).get("content") or "") if choices else ""


def _cli_execution(subject: EvaluationExecutionSubject, state: dict) -> str:
    command = (subject.cli_command or "").strip()
    if not command:
        raise EvaluationError("provider_launch_failed", 500)
    with tempfile.TemporaryDirectory(prefix="flowgate_evaluation_") as root:
        path = Path(root)
        kwargs = process_runner.popen_kwargs(path, {
            "FLOWGATE_TOKEN": "", "FLOWGATE_API_BASE": "", "FLOWGATE_BUNDLE_API": "",
            "FLOWGATE_SCRATCH": root, "TMP": root, "TEMP": root, "TMPDIR": root,
        }, include_stdio=True)
        kwargs["stdin"] = subprocess.PIPE
        try:
            proc = subprocess.Popen(command, **kwargs)
        except OSError as exc:
            raise EvaluationError("provider_launch_failed", 500) from exc
        state["proc"] = proc
        if state["cancel"].is_set():
            process_runner.kill_process_tree(proc)
        try:
            stdout, _stderr = proc.communicate(input=BASELINE.encode(), timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired as exc:
            process_runner.kill_process_tree(proc)
            proc.communicate()
            raise EvaluationError("timeout", 500) from exc
        finally:
            state["proc"] = None
        if proc.returncode != 0:
            raise EvaluationError("execution_failed", 500)
        return process_runner.safe_decode(stdout)


def _worker(evaluation_id: str, state: dict) -> None:
    subject = state["subject"]
    project_id = subject.project_id
    try:
        if state["cancel"].is_set() or not repo.mark_running(project_id, evaluation_id):
            return
        _emit("started", repo.get(project_id, evaluation_id))
        if not _safety_available(subject):
            raise EvaluationError("provider_became_unavailable", 500)
        text = (_api_execution(subject, state) if subject.exec_type == "api" else
                _cli_execution(subject, state))
        verdict = "suitable" if text.strip() else "unsuitable"
        changed = repo.finish(project_id, evaluation_id, lifecycle="completed", verdict=verdict,
                              reason_code=None if verdict == "suitable" else "empty_response")
        if changed:
            _emit("completed", repo.get(project_id, evaluation_id))
    except EvaluationError as exc:
        if repo.finish(project_id, evaluation_id, lifecycle="failed", reason_code=exc.code):
            _emit("failed", repo.get(project_id, evaluation_id))
    except Exception:
        if repo.finish(project_id, evaluation_id, lifecycle="failed", reason_code="internal_error"):
            _emit("failed", repo.get(project_id, evaluation_id))
    finally:
        with _RUNS_LOCK:
            _RUNS.pop(evaluation_id, None)


def detail(project_id: str, evaluation_id: str) -> dict:
    row = repo.get(project_id, evaluation_id)
    if row is None:
        raise EvaluationError("evaluation_not_found", 404)
    return _public(row, _current_subject(project_id, row["provider_id"]))


def current(project_id: str, provider_id: str) -> dict:
    subject = _current_subject(project_id, provider_id)
    active = repo.latest(project_id, provider_id, subject.fingerprint, subject.version) if subject else None
    previous = repo.latest(project_id, provider_id)
    return {"provider_id": provider_id,
            "current": _public(active, subject) if active else None,
            "latest_history": _public(previous, subject) if previous else None}


def history(project_id: str, provider_id: str | None, limit: int, cursor: str | None) -> dict:
    try:
        before = int(cursor) if cursor is not None else None
        if before is not None and before < 1:
            raise ValueError
        rows = repo.history(project_id, provider_id=provider_id, limit=limit + 1, before_seq=before)
    except ValueError as exc:
        raise EvaluationError("invalid_request", 422) from exc
    page, more = rows[:limit], len(rows) > limit
    subjects = {row["provider_id"]: _current_subject(project_id, row["provider_id"])
                for row in page}
    return {"items": [_public(row, subjects[row["provider_id"]]) for row in page],
            "next_cursor": str(page[-1]["project_run_seq"]) if more else None}


def cancel(project_id: str, evaluation_id: str) -> dict:
    row = repo.get(project_id, evaluation_id)
    if row is None:
        raise EvaluationError("evaluation_not_found", 404)
    if not repo.finish(project_id, evaluation_id, lifecycle="cancelled", reason_code="cancelled_by_user"):
        raise EvaluationError("evaluation_not_cancellable", 409)
    with _RUNS_LOCK:
        state = _RUNS.get(evaluation_id)
        if state:
            state["cancel"].set()
            if state["proc"] is not None:
                process_runner.kill_process_tree(state["proc"])
            _close_api_transport(state)
    row = repo.get(project_id, evaluation_id)
    _emit("cancelled", row)
    return {"evaluation_id": evaluation_id, "lifecycle": "cancelled", "verdict": None}
