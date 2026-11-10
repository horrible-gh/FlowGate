"""Chat run hook handlers: how the AI invoke engine reaches this package (D0006 §2.3).

``modules.flow_gate.services.chat_run_hooks`` defines the ports; the composition root
(``startup.py``) calls :func:`register` once at startup. Each handler is chat-only, and a
handler failure never fails the AI run (the hook port logs it and moves on).

    on_chat_run_start               admission: command capability, run start seq
    on_chat_run_prepare             worker, before any provider: start snapshot (0152)
    provide_chat_tools              API worker: the ``run_command`` tool (and the attachment
                                    upload request, flowgate.chat.0017 CH0002)
    on_chat_run_finalize            finalize: close open commands, then record the run change
    on_chat_run_cancel              cancel: close the run's commands before it is announced
    on_conversation_reply_appended  reply append: move the run's activity anchors
    accept_chat_call                API worker: accept a received tool call (card document
                                    call ledger, flowgate.v02.0127 L#3 §2.1 W1)

flowgate.v02.0127 L#3 §2.1 W4: cancel and finalize first close the run's card document edit
gate (``card_document_edit.chat_gate``) and only then do what they did before.
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


def on_chat_run_prepare(run: dict) -> None:
    """flowgate.v02.0152: the start-tree snapshot, on the worker thread before any provider
    starts. It hashes every file of the worktree, which on a cold file cache took longer
    than the browser's 30s wait for /ai-invoke/start when it ran inside admission. Nothing
    can change files for the run before its provider starts, so the tree means the same."""
    run_changes.start_snapshot(run)


def provide_chat_tools(run) -> list[dict]:
    """``run_command`` for a run admitted through :func:`on_chat_run_start` (or, with
    ``run=None``, the description for the CLI mention built before the run exists)."""
    from flowgate.card_document_edit import chat_tools, chat_adapter, chat_documents

    from flowgate.graph_read import tools as graph_read_tools

    from modules.flow_gate.documents.attachments import request_tools as attachment_request_tools

    tools = graph_read_tools.descriptors()
    if run is None or run.get("chat_command_capability"):
        tools.append(commands.tool_descriptor())
    # flowgate.chat.0017 CH0002: offered in reading mode too -- a request registers nothing
    # until a person approves it, and it never waits for that decision.
    tools.append(attachment_request_tools.chat_descriptor())
    return tools + chat_tools.descriptors({
        chat_tools.READ_TOOL: chat_adapter.read_card_document_call_handler,
        chat_tools.REVISE_TOOL: chat_adapter.revise_card_document_call_handler,
        # 0127 CH0028: doc_id read / existing-body edit / new-document create.
        chat_tools.READ_DOC_TOOL: chat_documents.read_document_call_handler,
        chat_tools.REVISE_DOC_TOOL: chat_documents.revise_document_call_handler,
        chat_tools.CREATE_DOC_TOOL: chat_documents.create_document_call_handler,
    })


def _run_was_cancelled(run: dict) -> bool:
    cancel_event = run.get("cancel_event")
    return bool(
        (cancel_event is not None and cancel_event.is_set())
        or run.get("status") == "cancelling"
        or run.get("end_reason") == "cancelled"
    )


def on_chat_run_finalize(run: dict) -> None:
    """Commands first -- nothing of a finished run stays open and the end snapshot must
    not race a command still writing files -- then the run change (D0006 §3.8.2).

    Before both, the card document edit gate closes with ``finished`` (0127 L#3 §2.7, W4);
    a gate already closed by a cancel keeps ``cancelled``. That step never raises."""
    from flowgate.card_document_edit import chat_gate

    chat_gate.close_on_finalize(run)
    if run.get("post_process_recovered"):
        return
    reason = c.REASON_RUN_CANCELLED if _run_was_cancelled(run) else c.REASON_RUN_FINISHED
    try:
        commands.cancel_for_run(run["run_id"], reason)
    except Exception:
        logger.warning("chat command cleanup failed for %s", run.get("run_id"), exc_info=True)
    run_changes.finalize_run(run)


def on_chat_run_cancel(run: dict, announce) -> None:
    """0127 L#3 §2.7 (W4): ① close the card document edit gate (``cancelled``; waits at most
    CANCEL_DRAIN_SEC for edits already past it, a failure never blocks the cancel) ②
    the existing command stop, which announces the cancel under its launch gate."""
    from flowgate.card_document_edit import chat_gate

    chat_gate.close_on_cancel(run)
    commands.stop_run(run["run_id"], on_closed=announce)


def on_conversation_reply_appended(doc_id: str, turn: dict) -> None:
    activity.on_turn_appended(doc_id, turn)


def accept_chat_call(run: dict, call: dict):
    """0127 L#3 §2.1 W1: the server call context of a received tool call (or None)."""
    from flowgate.card_document_edit import call_ledger

    return call_ledger.accept_call(run, call)


HANDLERS = {
    hooks.ON_CHAT_RUN_START: on_chat_run_start,
    hooks.ON_CHAT_RUN_PREPARE: on_chat_run_prepare,
    hooks.PROVIDE_CHAT_TOOLS: provide_chat_tools,
    hooks.ON_CHAT_RUN_FINALIZE: on_chat_run_finalize,
    hooks.ON_CHAT_RUN_CANCEL: on_chat_run_cancel,
    hooks.ON_CONVERSATION_REPLY_APPENDED: on_conversation_reply_appended,
    hooks.ACCEPT_CHAT_CALL: accept_chat_call,
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
