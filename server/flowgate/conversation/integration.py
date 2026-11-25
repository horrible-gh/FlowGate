"""Chat run hook handlers: how the AI invoke engine reaches this package.

``modules.flow_gate.services.chat_run_hooks`` defines the ports; the composition root
(``startup.py``) calls :func:`register` once at startup. Each handler is chat-only, and a
handler failure never fails the AI run (the hook port logs it and moves on).

    on_chat_run_start               admission: command capability, run start seq
    on_chat_run_prepare             worker, before any provider: start snapshot (0152)
    provide_chat_tools              API worker: the ``run_command`` tool (and the attachment
                                    upload request, the earlier change the discussion; the card question
                                    list / answer, the earlier change)
    on_chat_run_finalize            finalize: close open commands, record the run change, then
                                    the chat commit point
    on_chat_run_cancel              cancel: close the run's commands before it is announced
    on_conversation_reply_appended  reply append: move the run's activity anchors
    accept_chat_call                API worker: accept a received tool call (card document
                                    call ledger, the earlier change L#3 W1)

L#3 W4: cancel and finalize first close the run's card document edit
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
    # The capability that lets this run ask for commands (capability_required):
    # only a run whose issued token carries command access -- edit (read_write: edit, or an
    # edit_once that won its claim) or execute (commands only). A
    # reading chat is never offered the tool; the gate itself stays commands._authorize_run,
    # which re-judges the token (and an execute token's command scope) at creation,
    # approval and spawn.
    access = commands.command_access(run)
    run["chat_command_capability"] = access is not None
    run["chat_command_access"] = access
    run["chat_start_seq"] = activity.run_start_seq(run.get("doc_ref"))


def on_chat_run_prepare(run: dict) -> None:
    """On the worker thread before any provider starts: begin recording the run's own
    source writes (``run_changes`` / ``run_source_ledger``). Nothing can change files for
    the run before its provider starts. No worktree-wide snapshot is taken any more."""
    run_changes.start_snapshot(run)


def provide_chat_tools(run) -> list[dict]:
    """``run_command`` for a run admitted through :func:`on_chat_run_start` with the command
    capability (or, with ``run=None``, the description for the CLI mention built before the
    run exists; the mention adds it only for a token with command access -- edit or execute)."""
    from flowgate.card_document_edit import chat_tools, chat_adapter, chat_documents, chat_questions

    from flowgate.graph_read import tools as graph_read_tools

    from flowgate.chat_control import tools as chat_control_tools

    from modules.flow_gate.documents.attachments import request_tools as attachment_request_tools

    tools = graph_read_tools.descriptors()
    # (CH): control tower stage 1 -- place work cards and start the
    # Flow's run. Offered in every mode; reading mode is refused by the tool (chat_edit_mode_required).
    tools.extend(chat_control_tools.descriptors())
    if run is None or run.get("chat_command_capability"):
        tools.append(commands.tool_descriptor(
            execute_mode=bool(run) and run.get("chat_command_access") == "execute"))
    # offered in reading mode too -- a request registers nothing
    # until a person approves it, and it never waits for that decision.
    tools.append(attachment_request_tools.chat_descriptor())
    # the Flow's card questions (Q) — list and answer. Offered in every mode: an answer is
    # judged by the user's document create permission alone (Q#600), never by the chat's edit mode.
    tools.extend(chat_questions.descriptors())
    return tools + chat_tools.descriptors({
        chat_tools.READ_TOOL: chat_adapter.read_card_document_call_handler,
        chat_tools.REVISE_TOOL: chat_adapter.revise_card_document_call_handler,
        # doc_id read / existing-body edit / new-document create.
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
    not race a command still writing files -- then the run change.

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
    # the chat commit point -- after the run change was stored. Never raises.
    from modules.flow_gate.services import chat_commit_service
    chat_commit_service.commit_on_finalize(run)


def on_chat_run_cancel(run: dict, announce) -> None:
    """L#3 (W4): ① close the card document edit gate (``cancelled``; waits at most
    CANCEL_DRAIN_SEC for edits already past it, a failure never blocks the cancel) ②
    the existing command stop, which announces the cancel under its launch gate."""
    from flowgate.card_document_edit import chat_gate

    chat_gate.close_on_cancel(run)
    commands.stop_run(run["run_id"], on_closed=announce)


def on_conversation_reply_appended(doc_id: str, turn: dict) -> None:
    activity.on_turn_appended(doc_id, turn)
    if turn.get("_naming_handled"):
        return
    # A reply can arrive after its composer/tab was closed. Use the same durable claim.
    try:
        from .first_names_triggers import schedule_reply
        schedule_reply(doc_id, turn)
    except Exception as exc:
        logger.warning("flow naming reply exception_type=%s", type(exc).__name__)


def accept_chat_call(run: dict, call: dict):
    """L#3 W1: the server call context of a received tool call (or None)."""
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
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    from .first_names_triggers import schedule_run, schedule_completed
    first_names_hooks.register(schedule_run)
    first_names_hooks.register_completion(schedule_completed)
    from .first_names_triggers import submitted_title, title_request
    first_names_hooks.register_title(submitted_title, title_request)
    for name, handler in HANDLERS.items():
        hooks.register(name, handler)


def unregister() -> None:
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    first_names_hooks.register(None)
    first_names_hooks.register_completion(None)
    first_names_hooks.register_title(None, None)
    for name in HANDLERS:
        hooks.unregister(name)


def recover_on_startup() -> dict:
    """Startup recovery: open commands of a dead process are cancelled (never resumed),
    and activity anchors left behind are repaired."""
    closed = commands.recover()
    moved = activity.reconcile()
    return {"commands_closed": closed, "anchors_moved": moved}
