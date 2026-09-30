"""AI connection test: one isolated baseline prompt against an AI connection
(flowgate.v02.0017 T0002 §3.1).

This is the former 0014 "AI Evaluation" baseline probe, moved out of AI Evaluation. It only
answers "does this connection respond?" -- it is a connection diagnostic, never a capability
evaluation. Rows stay in ai_evaluation_runs (migration 124). The stored verdict column keeps
its suitable/unsuitable values for compatibility, but the public read model exposes only the
connection meaning: result = responded | no_response.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
from modules.flow_gate.db import ai_evaluation_runs as repo
from modules.flow_gate.db import ai_providers
from modules.flow_gate.services.ai_invoke import provider_api, provider_cli
from modules.flow_gate.services.ai_invoke import runtime as probe
from modules.flow_gate.services.ai_evaluation_fingerprint import FINGERPRINT_VERSION, subject_fingerprint
from modules.flow_gate.settings import ai_settings_service
from modules.flow_gate.utils.api_key_crypto import ApiKeyCryptoError

BASELINE = ("Reply with one short sentence confirming that you can answer this harmless test. "
            "Do not call tools, modify files, run commands, or disclose secrets.")
TIMEOUT_SEC = 60
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.RLock()
_SECRET_PATTERN = re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|api[_ -]?key\s*[:=]\s*)[^\s,;]+")
_RESULT = {"suitable": "responded", "unsuitable": "no_response"}


class ConnectionTestError(Exception):
    def __init__(self, code: str, status: int, test_id: str | None = None):
        self.code, self.status, self.test_id = code, status, test_id
        super().__init__(code)


@dataclass(frozen=True)
class ConnectionSubject:
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


def resolve_subject(project_id: str, provider_id: str, *, error=ConnectionTestError) -> ConnectionSubject:
    """Freeze one effective AI connection (config + credential) for a run-less execution.
    Shared with AI Evaluation, which resolves its target and judge the same way."""
    view = _effective(project_id)
    scope = view["source"]
    if scope == "disabled":
        raise error("provider_unavailable", 409)
    provider = next((p for p in view["providers"] if p["id"] == provider_id), None)
    if provider is None:
        stored = ai_providers.get_row(None if scope == "system" else project_id, provider_id)
        if stored is None:
            raise error("provider_not_found", 404)
        raise error("provider_disabled" if not stored.get("enabled") else "provider_unavailable", 409)
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
            raise error("provider_unavailable", 409) from exc
        if not credential:
            raise error("provider_unavailable", 409)
    fingerprint = subject_fingerprint(project_id=project_id, provider_id=provider_id,
        scope=scope, provider=provider, credential=credential)
    return ConnectionSubject(project_id, provider_id, scope, provider["name"],
        provider["exec_type"], provider["kind"], provider.get("cli_command"),
        provider.get("api_base_url"), provider.get("api_model"), credential,
        fingerprint, FINGERPRINT_VERSION)


def still_effective(subject: ConnectionSubject) -> bool:
    # Identity/enabled/effective only. Never take config or credentials from this read.
    try:
        view = _effective(subject.project_id)
    except LookupError:
        return False
    return (view["source"] == subject.scope and
            any(p["id"] == subject.provider_id for p in view["providers"]))


def _emit(kind: str, row: dict) -> None:
    payload = {"type": "ai_connection_test." + kind, "project_id": row["project_id"],
               "test_id": row["evaluation_id"], "provider_id": row["provider_id"],
               "lifecycle": row["lifecycle"], "result": _RESULT.get(row["verdict"])}
    broadcast_event_threadsafe(FlowEvent("ai_connection_test." + kind, payload, "*",
                                       project=row["project_id"]))


def _public(row: dict, current: ConnectionSubject | None = None) -> dict:
    return {
        "test_id": row["evaluation_id"], "project_id": row["project_id"],
        "provider_id": row["provider_id"], "provider_name": row.get("provider_name_snapshot"),
        "lifecycle": row["lifecycle"], "result": _RESULT.get(row.get("verdict")),
        "reason_code": row.get("reason_code"), "diagnostic_summary": row.get("diagnostic_summary"),
        "is_stale": (current is None or row["subject_fingerprint_version"] != current.version
                     or row["subject_fingerprint"] != current.fingerprint),
        "created_at": row.get("created_at"), "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
    }


def _current_subject(project_id: str, provider_id: str) -> ConnectionSubject | None:
    try:
        return resolve_subject(project_id, provider_id)
    except ConnectionTestError:
        return None


def start(project_id: str, provider_id: str, created_by: str | None) -> dict:
    subject = resolve_subject(project_id, provider_id)  # Frozen before 202 and used unchanged by worker.
    try:
        row = repo.create_pending(project_id=project_id, provider_id=provider_id,
            provider_name=subject.name, fingerprint=subject.fingerprint,
            version=subject.version, created_by=created_by)
    except repo.AlreadyRunning as exc:
        raise ConnectionTestError("connection_test_already_running", 409, exc.evaluation_id) from exc
    state = {"handle": probe.ProbeHandle(), "subject": subject}
    with _RUNS_LOCK:
        _RUNS[row["evaluation_id"]] = state
    _emit("updated", row)
    try:
        threading.Thread(target=_worker, args=(row["evaluation_id"], state), daemon=True,
                         name="ai-connection-test-" + row["evaluation_id"][-8:]).start()
    except Exception:
        with _RUNS_LOCK:
            _RUNS.pop(row["evaluation_id"], None)
        repo.finish(project_id, row["evaluation_id"], lifecycle="failed", reason_code="internal_error")
        raise
    return _public(row, subject)


def execute_prompt(subject: ConnectionSubject, prompt: str, handle: probe.ProbeHandle, timeout: float, *,
                   max_tokens: int = 100, scratch=None, run_id: str | None = None) -> probe.ProbeOutcome:
    """One prompt on the shared ai_invoke transports; no socket or process is opened here.
    `scratch`/`run_id` let a caller (AI Evaluation) run a CLI inside its own managed scratch."""
    if subject.exec_type == "api":
        return provider_api.probe_api_completion(
            kind=subject.kind, base_url=subject.api_base_url, model=subject.api_model,
            credential=subject.credential, prompt=prompt, timeout=timeout, handle=handle,
            max_tokens=max_tokens)
    if subject.exec_type == "cli":
        return provider_cli.probe_cli_completion(
            project_id=subject.project_id, kind=subject.kind, command=subject.cli_command,
            prompt=prompt, timeout=timeout, handle=handle, scratch=scratch, run_id=run_id)
    return probe.ProbeOutcome(probe.PROBE_LAUNCH_FAILED)


FAILURE_REASON = {
    probe.PROBE_TIMEOUT: "timeout",
    probe.PROBE_TRANSPORT_FAILED: "transport_failed",
    probe.PROBE_LAUNCH_FAILED: "provider_launch_failed",
    probe.PROBE_EXECUTION_FAILED: "execution_failed",
}


def _settle(project_id: str, test_id: str, lifecycle: str, *,
            verdict: str | None = None, reason_code: str | None = None) -> bool:
    """The only terminal write. repo.finish is a conditional UPDATE (pending/running only), so
    whichever of cancel / completion / failure / timeout gets here first wins."""
    if not repo.finish(project_id, test_id, lifecycle=lifecycle, verdict=verdict,
                       reason_code=reason_code):
        return False
    _emit(lifecycle, repo.get(project_id, test_id))
    return True


def _apply_outcome(project_id: str, test_id: str, outcome: probe.ProbeOutcome) -> None:
    if outcome.kind == probe.PROBE_CANCELLED:
        _settle(project_id, test_id, "cancelled", reason_code="cancelled_by_user")
    elif outcome.kind == probe.PROBE_OK:
        if outcome.text.strip():
            _settle(project_id, test_id, "completed", verdict="suitable")
        else:
            _settle(project_id, test_id, "completed", verdict="unsuitable", reason_code="empty_response")
    else:
        _settle(project_id, test_id, "failed",
                reason_code=FAILURE_REASON.get(outcome.kind, "internal_error"))


def _worker(test_id: str, state: dict) -> None:
    subject, handle = state["subject"], state["handle"]
    project_id = subject.project_id
    try:
        if handle.cancelled or not repo.mark_running(project_id, test_id):
            return
        _emit("started", repo.get(project_id, test_id))
        if not still_effective(subject):
            _settle(project_id, test_id, "failed", reason_code="provider_became_unavailable")
            return
        _apply_outcome(project_id, test_id, execute_prompt(subject, BASELINE, handle, TIMEOUT_SEC))
    except Exception:
        _settle(project_id, test_id, "failed", reason_code="internal_error")
    finally:
        with _RUNS_LOCK:
            _RUNS.pop(test_id, None)


def detail(project_id: str, test_id: str) -> dict:
    row = repo.get(project_id, test_id)
    if row is None:
        raise ConnectionTestError("connection_test_not_found", 404)
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
        raise ConnectionTestError("invalid_request", 422) from exc
    page, more = rows[:limit], len(rows) > limit
    subjects = {row["provider_id"]: _current_subject(project_id, row["provider_id"]) for row in page}
    return {"items": [_public(row, subjects[row["provider_id"]]) for row in page],
            "next_cursor": str(page[-1]["project_run_seq"]) if more else None}


def cancel(project_id: str, test_id: str) -> dict:
    row = repo.get(project_id, test_id)
    if row is None:
        raise ConnectionTestError("connection_test_not_found", 404)
    # DB first: if completion/failure/timeout already won, this loses with 409 and interrupts nothing.
    if not _settle(project_id, test_id, "cancelled", reason_code="cancelled_by_user"):
        raise ConnectionTestError("connection_test_not_cancellable", 409)
    with _RUNS_LOCK:
        state = _RUNS.get(test_id)
    if state:
        state["handle"].cancel()
    return {"test_id": test_id, "lifecycle": "cancelled", "result": None}
