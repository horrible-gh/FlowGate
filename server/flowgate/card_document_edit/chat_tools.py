"""API chat tool descriptors for the card document (L#3 §2.1 W2, §2.6.2; P#2 §5.1·§5.2).

``CHAT_TOOL_KEYS`` (name, description, schema, handler) is unchanged. The descriptors add
the optional keys ``own_validation`` (the worker skips its generic schema validation; the
handler maps input errors onto P#1 codes) and ``call_handler`` (receives only the server
call context). The ``handler`` of both tools is a fail-closed shell: reached through the old
``(run, input)`` signature it authenticates first and then always answers 422
``call_identity_unavailable`` without writing anything.

T#7 connects the call handlers in ``chat_adapter`` through ``PROVIDE_CHAT_TOOLS``.
Without a call handler the descriptors retain the fail-closed shell.
"""
from __future__ import annotations

from typing import Callable, Optional

from . import chat_authz, chat_errors

READ_TOOL = "read_card_document"
REVISE_TOOL = "revise_card_document"
# 0127 CH0028: doc_id tools. The server resolves card bindings; no card coordinates are asked.
READ_DOC_TOOL = "read_document_by_id"
REVISE_DOC_TOOL = "revise_document_by_id"
CREATE_DOC_TOOL = "create_new_document"

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

READ_DOC_SCHEMA = {
    "type": "object",
    "properties": {"doc_id": {"type": "string", "minLength": 1}},
    "required": ["doc_id"],
    "additionalProperties": False,
}

REVISE_DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_id": {"type": "string", "minLength": 1},
        "expected_revision": {"type": "integer", "minimum": 0},
        "content": {"type": "string"},
    },
    "required": ["doc_id", "expected_revision", "content"],
    "additionalProperties": False,
}

CREATE_DOC_SCHEMA = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["R", "B"]},
        "title": {"type": "string", "minLength": 1},
        "content": {"type": "string"},
        "module": {"type": "string"},
        "priority": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["title", "content"],
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
    "Refusals, conflicts and errors are not saves: report them as they are. When you only know a "
    "doc_id, use read_document_by_id / revise_document_by_id instead and never ask the user for card "
    "coordinates." + _SIZE_NOTE
)
READ_DOC_DESCRIPTION = (
    "Read one existing document of this project by doc_id (e.g. test2.default.0121.0001-R), card-bound "
    "or not. Returns content, revision_no, status and edit_path. Use revision_no as expected_revision "
    "before revise_document_by_id. Never ask the user for flow_id, node_key or slot." + _SIZE_NOTE
)
REVISE_DOC_DESCRIPTION = (
    "Write or rewrite the body of an EXISTING document by doc_id on the user's behalf (an empty body "
    "included). The server finds the card binding itself and keeps the doc_id; the revision goes up "
    "by one. Send expected_revision from your latest read_document_by_id result and the whole new content. "
    "If content has no front matter, the document's current front matter is kept; never change the "
    "identity fields (doc_id, type, group, target_id). It never creates documents: a missing doc_id "
    "is 404 document_not_found, use create_new_document for a new one. Refusals, revision_conflict and "
    "errors are not saves: report them as they are and re-read after a conflict." + _SIZE_NOTE
)
CREATE_DOC_DESCRIPTION = (
    "Register a NEW workflow-root document (doc_type R = requirement, B = bug report; default R) in "
    "this project with a new group, on the user's behalf. Send the title and the body as content "
    "WITHOUT front matter (the server writes it). module defaults to this chat's module. Returns the "
    "new doc_id. Use it only when the user asks for a new document; to write into an existing "
    "document use revise_document_by_id. Refusals and errors are not creations: report them as they are."
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
    elif name == READ_DOC_TOOL:
        description, schema = READ_DOC_DESCRIPTION, READ_DOC_SCHEMA
    elif name == REVISE_DOC_TOOL:
        description, schema = REVISE_DOC_DESCRIPTION, REVISE_DOC_SCHEMA
    elif name == CREATE_DOC_TOOL:
        description, schema = CREATE_DOC_DESCRIPTION, CREATE_DOC_SCHEMA
    else:
        raise ValueError(f"unknown card document chat tool: {name}")
    tool = {"name": name, "description": description, "schema": schema,
            "handler": stub_handler, "own_validation": True, "completion": False}
    if name == REVISE_TOOL:
        tool["cli_mention"] = cli_mention_section
    elif name == REVISE_DOC_TOOL:
        tool["cli_mention"] = cli_document_mention_section
    if call_handler is not None:
        if not callable(call_handler):
            raise TypeError(f"call_handler of {name} is not callable")
        tool["call_handler"] = call_handler
    return tool


ALL_TOOLS = (READ_TOOL, REVISE_TOOL, READ_DOC_TOOL, REVISE_DOC_TOOL, CREATE_DOC_TOOL)


def descriptors(call_handlers: Optional[dict] = None) -> list:
    """All descriptors; ``call_handlers`` maps tool name -> ``call_handler(run, call_context)``."""
    handlers = call_handlers or {}
    return [descriptor(name, handlers.get(name)) for name in ALL_TOOLS]


def _chat_base(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/api/v1"):
        return base[:-len("/api/v1")] + "/api/v2/chat"
    return base + "/api/v2/chat"


def cli_document_mention_section(api_base: str, raw_token: str) -> str:
    """0127 CH0028: doc_id read / existing-body edit / new-document create for CLI chat."""
    base = _chat_base(api_base)
    return (
        "[Document read, edit and create by doc_id]\n"
        f"GET {base}/documents/{{doc_id}}\n"
        f"POST {base}/documents/{{doc_id}}/revisions\n"
        f"POST {base}/documents\n"
        f"Authorization: Bearer {raw_token}\n"
        "Existing document (any doc_id of this project, card-bound or not): GET it first for content and "
        "revision_no, then POST /revisions with expected_revision, content (the whole new body; without "
        "front matter the current front matter is kept) and idempotency_key (new UUID v4 per attempt). "
        "The server resolves card bindings; never ask the user for flow_id, node_key or slot. A missing "
        "doc_id is 404 and is never created by /revisions. "
        "New document: POST /documents with title, content (body without front matter), optional "
        "doc_type (R or B, default R), module, priority and idempotency_key; it returns the new doc_id. "
        "Read mode refuses edits and creation. Report refusals, revision_conflict and errors honestly; "
        "do not claim a save succeeded, and re-read after an uncertain result."
    )


def cli_mention_section(api_base: str, raw_token: str) -> str:
    """L#3 §2.12: one section for both routes, independent of command permission."""
    base = api_base.rstrip("/")
    if base.endswith("/api/v1"):
        base = base[:-len("/api/v1")] + "/api/v2/chat"
    else:
        base += "/api/v2/chat"
    return (
        "[Card document read and edit]\n"
        f"GET {base}/flows/{{flow_id}}/nodes/{{node_key}}/slots/{{slot}}/document\n"
        f"POST {base}/flows/{{flow_id}}/nodes/{{node_key}}/slots/{{slot}}/document/revisions\n"
        f"Authorization: Bearer {raw_token}\n"
        "First GET the full body, doc_id and revision_no. Only then edit with POST, sending "
        "expected_doc_id, expected_revision, content (the entire new body), and idempotency_key. "
        "Make a new UUID v4 idempotency_key for each save attempt; reuse it only when retransmitting "
        "the same attempt. After a conflict, a new body requires a new key. "
        "Never write the same document through /inbox, a shell, v1 PATCH or register_document. "
        "Read mode refuses edits. Report refusals, conflicts and 500 errors honestly; do not claim "
        "a save succeeded, and re-read after an uncertain result. Document editing does not consume "
        "this token. API chat 413 result_too_large means its transport size limit: do not claim "
        "a read or edit succeeded; tell the user and suggest CLI chat, Designer or Agent. "
        "CLI chat has no API chat result-size limit."
    )
