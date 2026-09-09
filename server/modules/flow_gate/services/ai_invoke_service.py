"""AI invoke engine — the stable import path (flowgate.default.0501 T6).

The implementation lives in the `ai_invoke/` package next to this file, laid out along
NR0003 §12's module boundaries. This module is the compatibility surface §20 asks for
and re-exports the package facade. 0550 adds one deliberately narrow boundary here:
pre-worker admission failures must not strand a group lease, and unexpected failures
must cross the HTTP boundary with a useful code/message instead of becoming an opaque
500 that the client can only render as "AI run failed to start".
"""

from __future__ import annotations

from fastapi import HTTPException

from modules.flow_gate.db import group_ai_leases as db_group_ai_leases
from modules.flow_gate.services import token_service
from .ai_invoke import facade as _facade
from .ai_invoke.facade import *  # noqa: F401,F403  (inventory: facade.__all__)


# Keep the real implementation addressable for tests and for this compatibility wrapper.
# Package-internal seams resolve ai_invoke_service.start_run at call time, so this wrapper
# covers both the route's first hop and continuation/review hops without changing facade.py.
_admission_start_run = start_run


def _rollback_partial_group_admission(kwargs: dict, active_before: dict | None) -> None:
    """Best-effort cleanup for a start_run exception before a usable run exists.

    start_run acquires a durable group lease before token/mention/scratch/run setup. Several
    operations after that acquire can raise. The normal named failures clean themselves up,
    but an unexpected exception used to escape with the lease still present, leaving the UI
    on [AI 해제] even though no worker ever started.

    Never touch a lease that existed before this call, a project-scoped run, or a lease whose
    run is already present in the live registry. Those are real owners, not partial admission.
    """
    if active_before is not None or kwargs.get("action_scope") == "resolve_base_dirty":
        return
    group_id = str(kwargs.get("group_id") or "")
    if not group_id:
        return
    try:
        active = db_group_ai_leases.get_active(group_id)
    except Exception:
        _facade.logger.warning(
            "ai-invoke partial-admission cleanup could not read lease for %s",
            group_id,
            exc_info=True,
        )
        return
    if not active:
        return
    run_id = str(active.get("run_id") or "")
    if not run_id:
        return
    # A registered run has crossed the in-memory admission boundary. Its worker/finalizer owns
    # cleanup; releasing it here would turn an unrelated post-start exception into split-brain.
    with _facade._runs_lock:
        if run_id in _facade._runs:
            return
    issued_to = kwargs.get("issued_to")
    lease_owner = active.get("worker_identity")
    if issued_to and lease_owner and lease_owner != issued_to:
        return
    action_scope = kwargs.get("action_scope")
    lease_scope = active.get("action_scope")
    if action_scope and lease_scope and lease_scope != action_scope:
        return

    token_id = active.get("token_id")
    if token_id:
        try:
            token_service.revoke(token_id, reason="ai_invoke_partial_admission_rollback")
        except Exception:
            _facade.logger.warning(
                "ai-invoke partial-admission token rollback failed run_id=%s token_id=%s",
                run_id,
                token_id,
                exc_info=True,
            )
    try:
        db_group_ai_leases.release(
            group_id,
            run_id,
            reason="admission_rollback_unexpected_start_failure",
        )
    except Exception:
        _facade.logger.warning(
            "ai-invoke partial-admission lease rollback failed run_id=%s group_id=%s",
            run_id,
            group_id,
            exc_info=True,
        )


def start_run(*args, **kwargs):
    """Compatibility entry point with a fail-closed partial-admission rollback.

    Expected domain errors retain their established route mapping. Only an unexpected
    exception is normalized to a structured HTTPException after cleanup, so the browser can
    show the real exception class/message and the operator can retry immediately instead of
    being blocked by a lease for a worker that never existed.
    """
    if args:
        # admission.start_run is keyword-only. Keep its TypeError contract rather than invent
        # a positional compatibility surface here.
        return _admission_start_run(*args, **kwargs)

    group_id = str(kwargs.get("group_id") or "")
    active_before = None
    if group_id and kwargs.get("action_scope") != "resolve_base_dirty":
        try:
            active_before = db_group_ai_leases.get_active(group_id)
        except Exception:
            # Admission itself remains authoritative. A diagnostic pre-read must never make a
            # start fail that previously could have succeeded.
            active_before = None

    try:
        return _admission_start_run(**kwargs)
    except (HTTPException, LookupError, ValueError):
        raise
    except Exception as exc:
        _rollback_partial_group_admission(kwargs, active_before)
        _facade.logger.exception(
            "ai-invoke unexpected pre-run start failure group_id=%s action_scope=%s",
            group_id,
            kwargs.get("action_scope"),
        )
        # The route already preserves HTTPException detail through its normal _err() path.
        # Keep the detail bounded and never include token/prompt/provider-secret material.
        detail = str(exc).strip()
        if len(detail) > 300:
            detail = detail[:300] + "..."
        message = type(exc).__name__ + (f": {detail}" if detail else "")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "ai_invoke_start_internal_error",
                "message": message,
                "phase": "pre_run_admission",
            },
        ) from exc
