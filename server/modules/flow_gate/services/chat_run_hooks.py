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
PROVIDE_CHAT_TOOLS = "provide_chat_tools"
ON_CHAT_RUN_FINALIZE = "on_chat_run_finalize"
ON_CHAT_RUN_CANCEL = "on_chat_run_cancel"
ON_CONVERSATION_REPLY_APPENDED = "on_conversation_reply_appended"

HOOK_NAMES = (
    ON_CHAT_RUN_START,
    PROVIDE_CHAT_TOOLS,
    ON_CHAT_RUN_FINALIZE,
    ON_CHAT_RUN_CANCEL,
    ON_CONVERSATION_REPLY_APPENDED,
)

# Keys a provided chat tool must carry. ``handler(run, tool_input) -> (status, payload)``
# runs inside the API worker's tool loop; ``cli_mention(api_base, raw_token) -> str``
# (optional) is the same capability described for CLI providers; ``prompt_hint``
# (optional) is one line added to the API chat context.
CHAT_TOOL_KEYS = ("name", "description", "schema", "handler")

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
    """Admission: a chat run was admitted and its record built (before the worker starts)."""
    if _is_chat(run):
        _call(ON_CHAT_RUN_START, run)


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
                and callable(tool["handler"]):
            valid.append(tool)
        else:
            logger.warning("chat run hook %s returned a malformed tool (ignored)", PROVIDE_CHAT_TOOLS)
    return valid


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
