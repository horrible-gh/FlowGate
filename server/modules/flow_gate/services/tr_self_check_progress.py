"""0146: server-owned hop bindings; no bearer values or test output in telemetry.

Bindings live only while a provider attempt is active. Durable child ownership and
progress live in tr_self_check_run_progress. A different server without the binding
fails closed (no attribution), never falls back to group/TR/latest-run matching.
"""
from __future__ import annotations

import threading
import time
import uuid
from modules.flow_gate.db import tr_self_check_runs as db_runs

_lock = threading.RLock()
_bindings: dict[str, dict] = {}
# One optional metadata query in flight for this server; no queue and no unbounded
# per-poll threads. A wedged DB read cannot hold the watchdog or token-adoption lock.
_probe = None
_samples: dict[str, dict | None] = {}
PROBE_DEADLINE_SEC = 1.0


def open_attempt(run: dict) -> None:
    close_attempt(run)
    run["self_check_attempt_id"] = uuid.uuid4().hex
    bind_token(run, run.get("token_id") or run.get("current_token_id"))


def bind_token(run: dict, token_id: str | None) -> None:
    """Called only at the engine's verified current-token adoption boundary."""
    if not run.get("self_check_attempt_id") or not token_id:
        return
    with _lock:
        previous = run.get("self_check_scope")
        if (previous and previous["owner_token_id"] == token_id
                and previous["provider_attempt_id"] == run["self_check_attempt_id"]
                and _bindings.get(token_id) is previous):
            return
        if previous:
            _samples.pop(previous["scope_id"], None)
        if previous and _bindings.get(previous["owner_token_id"]) is previous:
            del _bindings[previous["owner_token_id"]]
        scope = {
            "scope_id": uuid.uuid4().hex, "ai_run_id": run["run_id"],
            "hop_id": uuid.uuid5(uuid.NAMESPACE_URL, repr((
                run["run_id"], int(run.get("submission_hop") or 0), run.get("hop_kind"),
                (run.get("register_context") or {}).get("doc", run.get("doc_ref")),
                (run.get("register_context") or {}).get("action", run.get("action_scope")),
            ))).hex,
            "provider_attempt_id": run["self_check_attempt_id"],
            "owner_token_id": token_id, "project_id": run["project_id"],
            "group_id": run["group_id"],
        }
        _bindings[token_id] = scope
        run["self_check_scope"] = scope
        # This scope was just created: no historical child can belong to it.
        run["self_check_watermark"] = 0
        run["self_check_observation"] = "none"
        run["last_self_check_progress"] = None


def close_attempt(run: dict) -> None:
    with _lock:
        scope = run.get("self_check_scope")
        if scope and _bindings.get(scope["owner_token_id"]) is scope:
            del _bindings[scope["owner_token_id"]]
        if scope:
            _samples.pop(scope["scope_id"], None)
        run.pop("self_check_attempt_id", None)


def owner_for(token: dict | None, project_id: str, group_id: str) -> dict | None:
    if not token or token.get("_is_user_jwt"):
        return None
    with _lock:
        scope = _bindings.get(token.get("token_id"))
        if (scope and token.get("project") == project_id == scope["project_id"]
                and token.get("group_id") == group_id == scope["group_id"]):
            return dict(scope)
    return None


def observe(run: dict) -> dict:
    scope = run.get("self_check_scope")
    if not scope:
        return {"seq": 0, "last": None}
    return db_runs.observe_effective_progress(scope["scope_id"])


def publish(run_id: str, *, status: str | None = None, output_bytes: int | None = None) -> None:
    # Optional telemetry must never change a test result, ownership proof or lock release.
    try:
        db_runs.record_effective_progress(run_id, status=status, output_bytes=output_bytes)
    except Exception:
        # The watchdog reports unavailable reads; the next cumulative observation recovers
        # output without replaying the last successful watermark. No output is logged.
        pass


def consume(run: dict, observed_at: str, sample: dict | None = None) -> bool:
    """Serialize observation/watermark with token adoption: never credit an old scope
    to a hop adopted while its DB read was in flight. The lock bounds registry memory
    and holds only a scope-indexed metadata read, never process waits or output reads.
    """
    with _lock:
        sample = observe(run) if sample is None else sample
        previous = run.get("self_check_watermark")
        run["self_check_observation"] = "ok" if sample["last"] else "none"
        advanced = previous is not None and sample["seq"] > previous
        if advanced:
            run["last_self_check_progress"] = {**sample["last"], "observed_at": observed_at}
        run["self_check_watermark"] = max(previous or 0, sample["seq"])
        return advanced


def poll(run: dict, observed_at: str) -> bool:
    """Nonblocking watchdog view. A read still pending after 1s is unavailable;
    one daemon query may finish later, but it cannot delay document/Git/cap checks.
    Late results are discarded after scope closure/adoption. No output/heartbeat
    is fabricated while waiting. Cached successful seqs use the same watermark.
    """
    global _probe
    with _lock:
        scope = run.get("self_check_scope")
        if not scope:
            return consume(run, observed_at)
        if _probe is None:
            job = {"scope": scope, "started": time.monotonic()}
            _probe = job
            def query():
                global _probe
                try:
                    sample = db_runs.observe_effective_progress(scope["scope_id"])
                except Exception:
                    sample = None
                with _lock:
                    if _bindings.get(scope["owner_token_id"]) is scope:
                        _samples[scope["scope_id"]] = sample
                    if _probe is job:
                        _probe = None
            thread = threading.Thread(target=query, name="selfcheck-progress-probe", daemon=True)
            job["thread"] = thread
            try:
                thread.start()
            except Exception:
                _probe = None
                run["self_check_observation"] = "unavailable"
                return False
        sample = _samples.get(scope["scope_id"])
        overdue = (_probe is not None and _probe["scope"] is scope
                   and time.monotonic() - _probe["started"] >= PROBE_DEADLINE_SEC)
        if sample is None or overdue:
            run["self_check_observation"] = "unavailable"
            return False
        return consume(run, observed_at, sample)
