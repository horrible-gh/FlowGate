"""Attachment block: the attachment upload request contract a CLI chat mention carries.

The help item ``document_attachments`` (chat context) and leaf ``attachments/upload_request``
render the same text from here (flowgate.v02test.0061 card 4).
"""
from __future__ import annotations

from ..registry import Block, register


def upload_request_contract(api_base: str, raw_token: str) -> str:
    """The section a CLI chat mention carries (``chat_run_hooks`` descriptor ``cli_mention``).

    Moved verbatim from ``documents.attachments.request_tools.contract_mention`` (0061 card 4).
    """
    base = api_base.rstrip("/")
    return "\n".join([
        "## Attachment upload request",
        "---",
        "To hand the user a file you produced (report, export, image ...) as an attachment of",
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
        'meta={"filename": "report.md", "note": "what it is"}',
        "",
        "The attachment belongs to this chat's Flow. No card target is needed.",
        "One file per request. Errors: 409 attachment_target_document_missing (the card has no document),",
        "413 attachment_request_too_large, 422 attachment_request_extension_blocked, 429 limits reached.",
        "Requesting does not consume this token.",
        f"List shared files: GET {base}/flow/<this flow_id>/attachments (same Bearer).",
        "The response gives document read/copy URLs. Read selected files only; no automatic content injection.",
    ])


register(Block("attachments.upload_request", brief=upload_request_contract, detail=upload_request_contract,
               help_leaf="attachments/upload_request"))
