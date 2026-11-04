"""API chat tool descriptors for the card document (L#3 §2.1 W2, §2.6.2; P#2 §5.1·§5.2).

``CHAT_TOOL_KEYS`` (name, description, schema, handler) is unchanged. The descriptors add
the optional keys ``own_validation`` (the worker skips its generic schema validation; the
handler maps input errors onto P#1 codes) and ``call_handler`` (receives only the server
call context). The ``handler`` of both tools is a fail-closed shell: reached through the old
``(run, input)`` signature it authenticates first and then always answers 422
``call_identity_unavailable`` without writing anything.

The call handlers (``chat_read`` / ``chat_edit``) and the merge into ``PROVIDE_CHAT_TOOLS``
are T#7 (L#3 §2.5·§2.6); until a call handler is supplied the descriptors only carry the shell.
"""
from __future__ import annotations

from typing import Callable, Optional

from . import chat_authz, chat_errors

READ_TOOL = "read_card_document"
REVISE_TOOL = "revise_card_document"

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "flow_id": {"type": "string", "minLength": 1},
        "node_key": {"type": "string", "minLength": 1},
        "slot": {"type": "string", "minLength": 1},
    },
    "required": ["flow_id", "node_key", "slot"],
    "additionalProperties": False,
}

REVISE_SCHEMA = {
    "type": "object",
    "properties": {
        "flow_id": {"type": "string", "minLength": 1},
        "node_key": {"type": "string", "minLength": 1},
        "slot": {"type": "string", "minLength": 1},
        "expected_doc_id": {"type": "string", "minLength": 1},
        "expected_revision": {"type": "integer", "minimum": 0},
        "content": {"type": "string"},
    },
    "required": ["flow_id", "node_key", "slot", "expected_doc_id", "expected_revision", "content"],
    "additionalProperties": False,
}

_SIZE_NOTE = (
    " A 413 result_too_large is the API chat size limit: do not say the document was read or "
    "edited, tell the user, and point them to the CLI chat, the Designer or an Agent. Never work "
    "around it with PATCH, register_document or a shell."
)
READ_DESCRIPTION = (
    "Read one card document of this project (flow_id, node_key, slot). Use the returned doc_id "
    "and revision_no as expected_doc_id / expected_revision before any edit." + _SIZE_NOTE
)
REVISE_DESCRIPTION = (
    "Replace the whole body of one card document on the user's behalf. Send expected_doc_id and "
    "expected_revision from your latest read_card_document result and the full new content. "
    "Refusals, conflicts and errors are not saves: report them as they are." + _SIZE_NOTE
)


def stub_handler(run, tool_input):
    """W2 fail-closed shell for the old ``(run, input)`` signature (§2.6.2 ``stub_handler``).

    Authentication and the run's life come first (their 401/403/409 is the answer); only an
    authenticated call then gets 422 ``call_identity_unavailable``. Never reads ``tool_input``.
    """
    def refuse():
        chat_authz.authorize_chat_run(chat_authz.API, None, run)
        raise chat_errors.call_identity_unavailable()
    return chat_errors.tool_result(refuse)


def descriptor(name: str, call_handler: Optional[Callable] = None) -> dict:
    if name == READ_TOOL:
        description, schema = READ_DESCRIPTION, READ_SCHEMA
    elif name == REVISE_TOOL:
        description, schema = REVISE_DESCRIPTION, REVISE_SCHEMA
    else:
        raise ValueError(f"unknown card document chat tool: {name}")
    tool = {"name": name, "description": description, "schema": schema,
            "handler": stub_handler, "own_validation": True, "completion": False}
    if call_handler is not None:
        if not callable(call_handler):
            raise TypeError(f"call_handler of {name} is not callable")
        tool["call_handler"] = call_handler
    return tool


def descriptors(call_handlers: Optional[dict] = None) -> list:
    """Both descriptors; ``call_handlers`` maps tool name -> ``call_handler(run, call_context)``."""
    handlers = call_handlers or {}
    return [descriptor(READ_TOOL, handlers.get(READ_TOOL)), descriptor(REVISE_TOOL, handlers.get(REVISE_TOOL))]
