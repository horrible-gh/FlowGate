"""How AI runs reach the attachment upload request (flowgate.chat.0017 CH0002 §2).

One capability, four ways in, one service behind them (``requests``):

    API chat run       chat tool ``request_attachment_upload`` (``conversation.integration.provide_chat_tools``)
    CLI chat run       the same descriptor's ``cli_mention`` section (multipart POST /attachment-requests)
    API card worker    worker tool ``request_attachment_upload`` (``api_server_tools.definitions_for_run``)
    CLI card worker    help/tools ``document_attachments.operations`` -> ``attachment_upload_request``

API providers have no filesystem of their own, so their tool takes ``source_path`` — a file of
the run's worktree (written there with the source tools, or already present in reading mode).
CLI providers and remote Agents upload the bytes they produced in their own environment.
Every path answers at once; the run never waits for the person's decision.
"""
from __future__ import annotations

from typing import Any

from . import requests as svc

TOOL_NAME = "request_attachment_upload"

_COMMON = (
    "Hand a file you produced to a person for approval as a card attachment. Returns at once with "
    "status=pending: do NOT wait for the decision and do not resend the same file — keep working. Nothing "
    "is attached until a person approves it (they can preview or download it first); the decision may come "
    "after your run ends. source_path is a worktree-relative path of an existing file."
)
CHAT_DESCRIPTION = _COMMON + (
    " target.node_key names the card of this chat's Flow whose document gets the file (read the Flow canvas "
    "to find it). Only cards that already have a document can be targeted."
)
CARD_DESCRIPTION = _COMMON + " The file always goes to your own card's document; there is no target to choose."

_FILE_PROPS = {
    "source_path": {"type": "string", "minLength": 1,
                    "description": "Worktree-relative POSIX path of the file to hand over."},
    "filename": {"type": "string", "description": "Attachment name to propose (defaults to the file's name)."},
    "note": {"type": "string", "description": "One line telling the person what the file is (max 500 chars)."},
}

CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        **_FILE_PROPS,
        "target": {
            "type": "object",
            "properties": {"node_key": {"type": "string", "minLength": 1}},
            "required": ["node_key"],
            "additionalProperties": False,
        },
    },
    "required": ["source_path", "target"],
    "additionalProperties": False,
}

CARD_SCHEMA = {
    "type": "object",
    "properties": dict(_FILE_PROPS),
    "required": ["source_path"],
    "additionalProperties": False,
}

PROMPT_HINT = (
    "To give the user a file you made (a report, an export, an image ...) as a card attachment, call "
    "`request_attachment_upload`; it returns immediately and a person approves it later — never wait for it."
)


def _error(exc: svc.RequestError) -> tuple[int, dict]:
    body = exc.body()
    body["op"] = TOOL_NAME
    return exc.status, body


def call(run: dict, tool_input: Any) -> tuple[int, dict]:
    """In-process tool entry for API providers (chat and card workers alike)."""
    try:
        requester = svc.requester_from_run(run)
        row = svc.create_from_source_path(requester, tool_input)
    except svc.RequestError as exc:
        return _error(exc)
    except Exception:  # noqa: BLE001 - a tool failure is a result the model sees, never a crashed run
        svc.logger.error("attachment request tool failed for run %s", run.get("run_id"), exc_info=True)
        return 500, {"ok": False, "op": TOOL_NAME,
                     "error": {"code": "attachment_request_failed", "message": "the request could not be stored"}}
    return 201, svc.ai_result(row)


def cli_mention(api_base: str, raw_token: str) -> str:
    """The section a CLI chat mention carries (``chat_run_hooks`` descriptor ``cli_mention``)."""
    base = api_base.rstrip("/")
    return "\n".join([
        "## Attachment upload request",
        "---",
        "To hand the user a file you produced (report, export, image ...) as an attachment of a card of",
        "this chat's Flow, upload it for a person's approval. The answer comes at once with status",
        "\"pending\": do not wait for the decision and do not resend the file — continue your work and",
        "mention the request in your reply. Nothing is attached until a person approves it (they preview",
        "or download it first); the decision may come after this run ends. Reading mode can request too.",
        "",
        f"POST {base}/attachment-requests",
        f"Authorization: Bearer {raw_token}",
        "Content-Type: multipart/form-data",
        "",
        "file=@<path of the file in your environment>",
        'meta={"target": {"node_key": "<card node_key>"}, "filename": "report.md", "note": "what it is"}',
        "",
        "Find node_key in the Flow canvas read (nodes[].node_key); the card must already have a document.",
        "One file per request. Errors: 409 attachment_target_document_missing (the card has no document),",
        "413 attachment_request_too_large, 422 attachment_request_extension_blocked, 429 limits reached.",
        "Requesting does not consume this token.",
    ])


def chat_descriptor() -> dict:
    """The chat tool (``conversation.integration.provide_chat_tools``)."""
    return {
        "name": TOOL_NAME,
        "description": CHAT_DESCRIPTION,
        "schema": CHAT_SCHEMA,
        "handler": call,
        "cli_mention": cli_mention,
        "prompt_hint": PROMPT_HINT,
    }
