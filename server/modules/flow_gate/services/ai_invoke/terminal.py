"""Independent, retryable terminal cleanup for an exited provider (0573 T0008).

No provider execution or workflow advancement belongs here. Callers must cross the
provider-return boundary first. Each step claims only a short in-memory lock; a
blocked record writer cannot prevent recovery from attempting lease cleanup.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from modules.flow_gate.db import group_ai_leases
from modules.flow_gate.db import project_ai_leases
from .runtime import _svc, logger


def attempt(run: dict, name: str, action) -> bool:
    """Run a cleanup effect once on success; leave failures available for retry."""
    lock = run.setdefault("_terminal_lock", threading.RLock())
    with lock:
        done = run.setdefault("_terminal_done", set())
        busy = run.setdefault("_terminal_busy", set())
        if name in done:
            return True
        if name in busy:
            return False
        busy.add(name)
    try:
        action()
    except Exception as exc:
        with lock:
            errors = run.setdefault("terminal_cleanup_errors", {})
            errors[name] = type(exc).__name__
        logger.exception("ai-invoke %s: terminal cleanup step %s failed",
                         run.get("run_id"), name)
        return False
    else:
        with lock:
            done.add(name)
            run.setdefault("terminal_cleanup_errors", {}).pop(name, None)
        return True
    finally:
        with lock:
            busy.discard(name)


def provider_stopped(run: dict) -> bool:
    # API runs have no child. The worker flag, not the absence of a PID, fences them.
    if run.get("_provider_active"):
        return False
    proc = run.get("proc")
    if proc is not None:
        try:
            return proc.poll() is not None
        except Exception:
            logger.exception("ai-invoke %s: cannot confirm provider exit", run.get("run_id"))
            return False
    return True


def release_owned_lease(run: dict, reason: str) -> None:
    """Never delete by group/project alone, even after a successor has adopted."""
    if run.get("action_scope") == "resolve_base_dirty":
        project_ai_leases.release(str(run.get("project_id") or ""), run["run_id"])
        row = project_ai_leases.get_active(str(run.get("project_id") or ""))
    elif run.get("group_id"):
        group_ai_leases.release(run["group_id"], run["run_id"], reason=reason)
        row = group_ai_leases.get(run["group_id"])
    else:
        return
    if row and row.get("run_id") == run["run_id"]:
        raise RuntimeError("terminal lease still belongs to this run")


def _handoff(run: dict) -> None:
    group_ai_leases.begin_handoff(run["group_id"], run["run_id"])
    row = group_ai_leases.get(run["group_id"])
    if row and row.get("run_id") == run["run_id"] and row.get("state") != "releasing":
        raise RuntimeError("handoff lease did not enter releasing")


def cleanup(run: dict, *, handoff: bool = False, reason: str = "normal_finish") -> bool:
    """Low-level fallback usable even when judge/finalize were never called.

    A release failure is visible in terminal_cleanup_errors/lease_cleanup_pending.
    It never claims the DB was fixed. The dead worker still becomes non-live, so
    the existing manual recovery remains usable during an actual DB outage.
    """
    if not provider_stopped(run):
        run["lease_cleanup_pending"] = True
        return False
    # Once abandoned/released, a late finalizer must never reopen handoff.
    if run.get("_terminal_abandoned") or "lease_release" in run.get("_terminal_done", ()):
        handoff = False
    if handoff and run.get("action_scope") != "resolve_base_dirty":
        lease_ok = attempt(run, "lease_handoff", lambda: _handoff(run))
    else:
        lease_ok = attempt(run, "lease_release", lambda: release_owned_lease(run, reason))
    run["lease_cleanup_pending"] = not lease_ok

    run.setdefault("outcome", "none")
    if not run.get("outcome"):
        run["outcome"] = "none"
    run.setdefault("end_reason", "worker_error")
    if not run.get("finished_at"):
        run["finished_at"] = datetime.now(timezone.utc).isoformat()
    run["duration_ms"] = int((time.monotonic() - run.get("started_mono", time.monotonic())) * 1000)
    run["status"] = "finished"
    run["proc"] = None

    # Keep token IDs and redaction history for diagnostics, discard the live credential.
    # The normal scope-token policies (consumption, reuse, handoff) remain authoritative.
    run.pop("raw_token", None)
    publish(run)
    return lease_ok


def publish(run: dict) -> None:
    """At most one successful publication per verdict, including a later park."""
    version = str((run.get("stop_code"), run.get("end_reason"), run.get("outcome")))
    attempt(run, "persist:" + version, lambda:
        require_success(_svc()._persist_run_record(run), "run record persist"))
    attempt(run, "finished_event:" + version, lambda: _svc()._broadcast(
        run, "ai_invoke_finished", _svc().finished_payload(run)))
    attempt(run, "refresh_event:" + version, lambda: _svc()._broadcast(
        run, "group_view_refresh",
        {"group_id": run.get("group_id"), "reason": "ai_invoke_finished"}))


def abandon_handoff(run: dict, *, stop_code: str) -> None:
    """Preserve the predecessor's intent and release only its ownership on failure."""
    if not claim_abandon(run):
        return
    pending = run.get("_terminal_pending")
    if pending is None:
        pending = _svc().peek_auto_resume(run.get("group_id"))
    if pending is not None:
        run["_terminal_pending"] = pending
        _svc()._park_handoff(run, pending, stop_code)
        # Identity CAS protects an intent replaced by a successor or another producer.
        attempt(run, "consume_abandoned_handoff", lambda:
                _svc()._pop_auto_resume_if_same(run.get("group_id"), pending))

# Only an engine-triggered successor inherits this context. It is reset before
# leaving the predecessor's worker; ordinary/manual admission has no parent.
from contextvars import ContextVar

handoff_parent = ContextVar("ai_invoke_handoff_parent", default=None)


def claim_abandon(run: dict) -> bool:
    """Race recovery against successor gate OPEN with no external I/O in the lock."""
    lock = run.setdefault("_terminal_lock", threading.RLock())
    with lock:
        if run.get("_handoff_succeeded"):
            return False
        run["_terminal_abandoned"] = True
        return True


def open_successor_gate(gate) -> bool:
    """A recovered predecessor cannot launch a late successor after its lease release."""
    parent = handoff_parent.get()
    if parent is None:
        return gate.open_direct()
    lock = parent.setdefault("_terminal_lock", threading.RLock())
    with lock:
        cancel = parent.get("cancel_event")
        if parent.get("_terminal_abandoned") or (cancel is not None and cancel.is_set()):
            return False
        opened = gate.open_direct()
        if opened:
            parent["_handoff_succeeded"] = True
        return opened


def require_success(result, step: str) -> None:
    if result is False:
        raise RuntimeError(step + " failed")
