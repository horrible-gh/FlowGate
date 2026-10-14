"""AI run end hook port (flowgate.v02.0053 T#2, D0006 §2.3).

The AI invoke engine lives in ``modules.flow_gate``; the Designer card's review stage that reacts
to a finished run (``flowgate.card_settings``) lives in the v0.2 application package ``flowgate``.
That dependency may only point one way -- ``flowgate`` -> ``modules.flow_gate`` -- so this module
offers one named port the engine calls, and the composition root (``startup.py``) registers the
handler. Nothing here imports ``flowgate``.

    on_run_ended   finalization, after the run's group lease is released: the run record as it
                   finished (``run_id``, ``group_id``, ``doc_ref``, ``action_scope``, ``mode``,
                   ``outcome``, ``stop_code``, ``document_review_loop`` ...)
    on_run_ending  finalization, right before that release (flowgate.v02.0061 T#2): the same run
                   record, its stop code and review checkpoint already final. A handler marks what
                   ``on_run_ended`` will follow, so the gap between the release and that hook is not
                   taken for "nothing works any more"; it must stay short (no new run here)

* With no handler registered the port is a no-op, so the engine behaves exactly as before.
* ``on_run_ended`` is called strictly after the release, so a handler may admit a new run for the
  same group through the ordinary admission path (the same ordering the question / test-gate resumes use).
* A handler exception never fails the run's finalization: it is logged and ignored.
* One handler per port. Registering again replaces it (startup may run more than once).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ON_RUN_ENDED = "on_run_ended"
ON_RUN_ENDING = "on_run_ending"
HOOK_NAMES = (ON_RUN_ENDED, ON_RUN_ENDING)

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in HOOK_NAMES:
        raise ValueError(f"unknown AI run end hook: {name}")
    if not callable(handler):
        raise TypeError(f"AI run end hook {name} handler is not callable")
    with _lock:
        _handlers[name] = handler


def unregister(name: str) -> None:
    with _lock:
        _handlers.pop(name, None)


def registered(name: str) -> bool:
    with _lock:
        return name in _handlers


def _handler(name: str) -> Optional[Callable[..., Any]]:
    with _lock:
        return _handlers.get(name)


def _call(name: str, run: dict) -> None:
    handler = _handler(name)
    if handler is None:
        return
    try:
        handler(run)
    except Exception:  # noqa: BLE001 — a hook never fails the run's finalization
        logger.warning("AI run %s hook failed for %s (ignored)", name, (run or {}).get("run_id"), exc_info=True)


def run_ending(run: dict) -> None:
    """The run is about to release its group lease (flowgate.v02.0061 T#2). Never raises."""
    _call(ON_RUN_ENDING, run)


def run_ended(run: dict) -> None:
    """The run finished and released its group lease. Never raises."""
    _call(ON_RUN_ENDED, run)
