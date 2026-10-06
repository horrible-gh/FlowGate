"""Chat run hook handlers: how the AI invoke engine reaches this package (D0006 §2.3).

``modules.flow_gate.services.chat_run_hooks`` defines the ports; the composition root
(``startup.py``) calls :func:`register` once at startup. Each handler is chat-only, and a
handler failure never fails the AI run (the hook port logs it and moves on).

    on_chat_run_start               admission: command capability, run start seq, start snapshot
    provide_chat_tools              API worker: the ``run_command`` tool
    on_chat_run_finalize            finalize: close open commands, then record the run change
    on_chat_run_cancel              cancel: close the run's commands before it is announced
    on_conversation_reply_appended  reply append: move the run's activity anchors
"""
from __future__ import annotations

import logging

from modules.flow_gate.services import chat_run_hooks as hooks

from flowgate.conversation import activity
from flowgate.conversation import commands
from flowgate.conversation import contracts as c
from flowgate.conversation import run_changes

logger = logging.getLogger(__name__)


def on_chat_run_start(run: dict) -> None:
    # The capability that lets this run ask for commands (D0006 §5.3 capability_required).
    run["chat_command_capability"] = True
    run["chat_start_seq"] = activity.run_start_seq(run.get("doc_ref"))
    run_changes.start_snapshot(run)


def provide_chat_tools(run) -> list[dict]:
    """``run_command`` for a run admitted through :func:`on_chat_run_start` (or, with
    ``run=None``, the description for the CLI mention built before the run exists)."""
    if run is not None and not run.get("chat_command_capability"):
        return []
    return [commands.tool_descriptor()]


def _run_was_cancelled(run: dict) -> bool:
    cancel_event = run.get("cancel_event")
    return bool(
        (cancel_event is not None and cancel_event.is_set())
        or run.get("status") == "cancelling"
        or run.get("end_reason") == "cancelled"
    )


def on_chat_run_finalize(run: dict) -> None:
    """Commands first -- nothing of a finished run stays open and the end snapshot must
    not race a command still writing files -- then the run change (D0006 §3.8.2)."""
    if run.get("post_process_recovered"):
        return
    reason = c.REASON_RUN_CANCELLED if _run_was_cancelled(run) else c.REASON_RUN_FINISHED
    try:
        commands.cancel_for_run(run["run_id"], reason)
    except Exception:
        logger.warning("chat command cleanup failed for %s", run.get("run_id"), exc_info=True)
    run_changes.finalize_run(run)


def on_chat_run_cancel(run: dict, announce) -> None:
    commands.stop_run(run["run_id"], on_closed=announce)


def on_conversation_reply_appended(doc_id: str, turn: dict) -> None:
    activity.on_turn_appended(doc_id, turn)


HANDLERS = {
    hooks.ON_CHAT_RUN_START: on_chat_run_start,
    hooks.PROVIDE_CHAT_TOOLS: provide_chat_tools,
    hooks.ON_CHAT_RUN_FINALIZE: on_chat_run_finalize,
    hooks.ON_CHAT_RUN_CANCEL: on_chat_run_cancel,
    hooks.ON_CONVERSATION_REPLY_APPENDED: on_conversation_reply_appended,
}


def register() -> None:
    """Connect every Conversation handler to its port (idempotent)."""
    for name, handler in HANDLERS.items():
        hooks.register(name, handler)


def unregister() -> None:
    for name in HANDLERS:
        hooks.unregister(name)


def recover_on_startup() -> dict:
    """Startup recovery: open commands of a dead process are cancelled (never resumed),
    and activity anchors left behind are repaired (D0006 §3.7, §4.3)."""
    closed = commands.recover()
    moved = activity.reconcile()
    return {"commands_closed": closed, "anchors_moved": moved}
