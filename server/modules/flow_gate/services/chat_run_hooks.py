"""Generic chat-run hook ports (flowgate.v02.0032 T#1, D0006 §2.3).

The AI invoke engine and the conversation turn service live in ``modules.flow_gate``;
the Conversation contract that reacts to them (command requests, run changes, activity
anchors) lives in the v0.2 application package ``flowgate``. That dependency may only
point one way -- ``flowgate`` -> ``modules.flow_gate`` -- so this module offers named
ports the engine calls, and the composition root (``startup.py``) registers the
handlers. Nothing here imports ``flowgate``.

Rules every port follows:

* With no handler registered a port is a no-op, so the engine behaves exactly as it
  did before the ports existed.
* A handler exception never fails the AI run or the turn append that called it: it is
  logged and the caller continues (D0006 §2.3).
* One handler per port. Registering again replaces it (startup may run more than once
  in one process, e.g. under a test client).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

ON_CHAT_RUN_START = "on_chat_run_start"
# flowgate.v02.0152: the heavy half of the start -- the start-tree snapshot -- runs on the
# worker thread after the handoff gate opens and before any provider starts, so the
# /ai-invoke/start request no longer waits for a full worktree hash (the browser gave up at
# 30s and reported "could not call the AI" for a run the server then carried out).
ON_CHAT_RUN_PREPARE = "on_chat_run_prepare"
PROVIDE_CHAT_TOOLS = "provide_chat_tools"
ON_CHAT_RUN_FINALIZE = "on_chat_run_finalize"
ON_CHAT_RUN_CANCEL = "on_chat_run_cancel"
ON_CONVERSATION_REPLY_APPENDED = "on_conversation_reply_appended"
# flowgate.v02.0127 L#3 §2.1 W1: the API worker hands every received tool call of a chat run
# to this port exactly once, before any schema validation; the handler answers the server
# call context (or None).
ACCEPT_CHAT_CALL = "accept_chat_call"

HOOK_NAMES = (
    ON_CHAT_RUN_START,
    ON_CHAT_RUN_PREPARE,
    PROVIDE_CHAT_TOOLS,
    ON_CHAT_RUN_FINALIZE,
    ON_CHAT_RUN_CANCEL,
    ON_CONVERSATION_REPLY_APPENDED,
    ACCEPT_CHAT_CALL,
)

# Keys a provided chat tool must carry. ``handler(run, tool_input) -> (status, payload)``
# runs inside the API worker's tool loop; ``cli_mention(api_base, raw_token) -> str``
# (optional) is the same capability described for CLI providers; ``prompt_hint``
# (optional) is one line added to the API chat context.
CHAT_TOOL_KEYS = ("name", "description", "schema", "handler")
# flowgate.v02.0127 L#3 §2.1 W2: optional keys. ``call_handler(run, call_context)`` receives the
# server call context of ACCEPT_CHAT_CALL instead of the model input (the handler reads the
# input from its own frozen call record); ``own_validation`` (bool) asks the worker to skip
# its generic schema validation because the handler maps input errors itself. A tool
# without them keeps the ``handler(run, tool_input)`` contract unchanged.
CHAT_TOOL_OPTIONAL_KEYS = ("call_handler", "own_validation")

_handlers: dict[str, Callable[..., Any]] = {}
_lock = threading.Lock()


def register(name: str, handler: Callable[..., Any]) -> None:
    if name not in HOOK_NAMES:
        raise ValueError(f"unknown chat run hook: {name}")
    if not callable(handler):
        raise TypeError(f"chat run hook {name} handler is not callable")
    with _lock:
        _handlers[name] = handler


def unregister(name: str) -> None:
    with _lock:
        _handlers.pop(name, None)


def clear() -> None:
    with _lock:
        _handlers.clear()


def registered(name: str) -> bool:
    with _lock:
        return name in _handlers


def _handler(name: str) -> Optional[Callable[..., Any]]:
    with _lock:
        return _handlers.get(name)


def _call(name: str, *args: Any) -> tuple[bool, Any]:
    """(ran, result). ``ran`` is False when nothing is registered or the handler raised."""
    handler = _handler(name)
    if handler is None:
        return False, None
    try:
        return True, handler(*args)
    except Exception:
        logger.warning("chat run hook %s failed (ignored)", name, exc_info=True)
        return False, None


def _is_chat(run: Optional[dict]) -> bool:
    return bool(run) and run.get("action_scope") == "chat"


def chat_run_start(run: dict) -> None:
    """Admission: a chat run was admitted and its record built (before the worker starts).

    Keep this cheap: the /ai-invoke/start request waits for it. Slow preparation belongs
    to :func:`chat_run_prepare`."""
    if _is_chat(run):
        _call(ON_CHAT_RUN_START, run)


def chat_run_prepare(run: dict) -> None:
    """Worker: the run is registered and its gate is open, no provider has started yet.

    flowgate.v02.0152: the start-tree snapshot lives here, off the request thread. Like
    finalize, the caller picks its in-module path only when nothing is registered."""
    if _is_chat(run):
        _call(ON_CHAT_RUN_PREPARE, run)


def chat_run_finalize(run: dict) -> None:
    """Finalization: the run's process is gone; close what it left open, record changes."""
    if _is_chat(run):
        _call(ON_CHAT_RUN_FINALIZE, run)


def chat_run_cancel(run: dict, announce: Callable[[], None]) -> bool:
    """Run cancel, called BEFORE the run is announced ``cancelling``.

    The handler must call ``announce`` itself once nothing it owns can start any more.
    Returns whether a handler ran; the caller announces itself when it did not (or when
    the handler failed before announcing).
    """
    if not _is_chat(run):
        return False
    ran, _ = _call(ON_CHAT_RUN_CANCEL, run, announce)
    return ran


def conversation_reply_appended(doc_id: str, turn: dict) -> None:
    """A conversation turn committed (called before it is broadcast)."""
    _call(ON_CONVERSATION_REPLY_APPENDED, doc_id, turn)


def chat_tools(run: Optional[dict]) -> list[dict]:
    """Tools a chat run may call besides its reply tool. ``run`` is None when the caller
    only needs the descriptions (the CLI mention is built before the run exists)."""
    if run is not None and not _is_chat(run):
        return []
    ran, tools = _call(PROVIDE_CHAT_TOOLS, run)
    if not ran or not tools:
        return []
    valid: list[dict] = []
    for tool in tools:
        if isinstance(tool, dict) and all(tool.get(key) for key in CHAT_TOOL_KEYS) \
                and callable(tool["handler"]) \
                and ("call_handler" not in tool or callable(tool["call_handler"])) \
                and ("own_validation" not in tool or isinstance(tool["own_validation"], bool)):
            valid.append(tool)
        else:
            logger.warning("chat run hook %s returned a malformed tool (ignored)", PROVIDE_CHAT_TOOLS)
    return valid


def accept_chat_call(run: Optional[dict], call: Any) -> Any:
    """W1: a received tool call of a chat run. The handler records it and answers its server
    call context. Not registered, failing, or not a chat run -> None (no context)."""
    if not _is_chat(run):
        return None
    ran, context = _call(ACCEPT_CHAT_CALL, run, call)
    return context if ran else None


def chat_mention_sections(api_base: str, raw_token: str) -> list[str]:
    """CLI-provider instructions of every provided chat tool, for the chat mention."""
    sections: list[str] = []
    for tool in chat_tools(None):
        builder = tool.get("cli_mention")
        if not callable(builder):
            continue
        try:
            text = builder(api_base, raw_token)
        except Exception:
            logger.warning("chat tool %s mention section failed (ignored)", tool.get("name"), exc_info=True)
            continue
        if text:
            sections.append(str(text))
    return sections
