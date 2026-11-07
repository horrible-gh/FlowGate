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
_managed: dict[str, dict] = {}  # detached tokens cannot silently become independent
# One optional metadata query in flight for this server; no queue and no unbounded
# per-poll threads. A wedged DB read cannot hold the watchdog or token-adoption lock.
_probe = None
_samples: dict[str, dict | None] = {}
PROBE_DEADLINE_SEC = 1.0


def open_attempt(run: dict, *, durable: bool = False) -> None:
    with _lock:
        close_attempt(run)
        if run.get("_self_check_ending") or (run.get("cancel_event") is not None
                                             and run["cancel_event"].is_set()):
            raise db_runs.ProgressOwnerClosedError("parent execution is ending")
        run["self_check_attempt_id"] = uuid.uuid4().hex
        run["_self_check_durable"] = durable
        bind_token(run, run.get("token_id") or run.get("current_token_id"), persist=False)
        scope = run.get("self_check_scope") if run.get("self_check_attempt_id") else None
    if durable and scope:
        db_runs.register_owner_scope(scope)


def bind_token(run: dict, token_id: str | None, *, persist: bool = True) -> None:
    """Called only at the engine's verified current-token adoption boundary."""
    with _lock:
        if not run.get("self_check_attempt_id") or not token_id or run.get("_self_check_ending"):
            return
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
        _managed[token_id] = scope
        run.setdefault("self_check_scopes", []).append(scope)
        run["self_check_scope"] = scope
        # This scope was just created: no historical child can belong to it.
        run["self_check_watermark"] = 0
        run["self_check_observation"] = "none"
        run["last_self_check_progress"] = None
    if persist and run.get("_self_check_durable"):
        if previous:
            db_runs.close_owner_scopes([previous], boundary="scope",
                                       reason="token_replaced", cancel=False)
        # Persist even before the first child: restart must not downgrade a late
        # managed token into an independent admission. Never reopen a closed row.
        db_runs.register_owner_scope(scope)


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


def admission_owner(token: dict | None, project_id: str, group_id: str) -> dict | None:
    """Only an active server binding grants ownership. A detached/durable managed
    token is refused, including on another server; it is never treated as independent."""
    if not token or token.get("_is_user_jwt"):
        return None
    token_id = token.get("token_id")
    with _lock:
        owner = owner_for(token, project_id, group_id)
        if owner is not None:
            return owner
        managed = _managed.get(token_id)
        if (managed and managed["project_id"] == project_id
                and managed["group_id"] == group_id):
            raise db_runs.ProgressOwnerClosedError("managed token has no active binding")
    if token_id and db_runs.managed_owner_token(token_id, project_id, group_id):
        raise db_runs.ProgressOwnerClosedError("managed token has no active binding")
    return None


def end_owned(run: dict, *, boundary: str = "attempt", cancel: bool = True,
              reason: str = "provider_return", owner: dict | None = None,
              keep_attempt: bool = False) -> None:
    """Close admission before asking existing cancellation/proof machinery to stop.
    No SQL, process wait or cancellation runs under the progress registry lock.
    An explicit owner snapshot makes delayed attempt cleanup safe after token reuse.
    """
    with _lock:
        owner = owner or run.get("self_check_scope")
        if boundary == "run":
            run["_self_check_ending"] = True
        if not owner:
            return
        scopes = [s for s in run.get("self_check_scopes", [owner])
                  if s["ai_run_id"] == owner["ai_run_id"]
                  and (boundary != "hop" or s["hop_id"] == owner["hop_id"])
                  and (boundary != "attempt"
                       or s["provider_attempt_id"] == owner["provider_attempt_id"])]
        for scope in scopes:
            if _bindings.get(scope["owner_token_id"]) is scope:
                del _bindings[scope["owner_token_id"]]
            _samples.pop(scope["scope_id"], None)
        current_scope = run.get("self_check_scope")
        if (not keep_attempt and current_scope in scopes
                and run.get("self_check_attempt_id") == owner["provider_attempt_id"]):
            run.pop("self_check_attempt_id", None)
    scope_ids = db_runs.close_owner_scopes(scopes, boundary=boundary, reason=reason, cancel=cancel)
    # Durable fences now replace the short-lived tombstones. Reused token bindings stay.
    with _lock:
        for scope in scopes:
            if _managed.get(scope["owner_token_id"]) is scope:
                _managed.pop(scope["owner_token_id"], None)
    if cancel:
        from modules.flow_gate.services import tr_self_check_service
        tr_self_check_service.cancel_owned_scopes(scope_ids)
