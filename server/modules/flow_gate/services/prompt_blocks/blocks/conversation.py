"""Conversation blocks: the turn submission contract and the encoding probe paragraph.

Moved verbatim from ``chat_help_content`` (flowgate.v02test.0061 card 4). The chat mention
(``invoke_mention_service.build_conversation_mention``) and the help leaves
``conversation/turn`` and ``conversation/encoding`` both render from here.
"""
from __future__ import annotations

from ..registry import Block, register


def encoding_probe_paragraph(token_id: str) -> list[str]:
    """flowgate.v02gui.0009: the per-token encoding probe and the transport rule.

    The probe proves the path, whatever language the body is in
    (``encoding_probe``); with it the question-mark heuristic never runs.
    """
    from modules.flow_gate.services import encoding_probe

    probe = encoding_probe.probe_for(token_id)
    return [
        "Encoding probe: make the LAST line of the body exactly this line. FlowGate checks it",
        "and removes it before saving:",
        f"{probe}",
        "If it arrives intact, the path carried every Unicode character and no corruption check",
        "runs, so any text is fine ('??' included), in any language. If it arrives damaged, the",
        "turn is refused with the cause; fix the transport, not the text.",
        "The same line works for a document body you save from this chat (card document or",
        "doc_id revisions): put it as the last line of `content`; it is removed before saving.",
        "Transport: write the body (probe line last) to a UTF-8 file, then send it with a script",
        "that reads that file and posts ASCII-only JSON, e.g. Python",
        "`json.dumps(payload).encode(\"ascii\")` (json.dumps escapes non-ASCII by default). Never",
        "put the body or the probe on a command line, and never pass them through a console pipe,",
        "redirect, Set-Content or Out-File: those can drop characters.",
        "Without the probe, a body that looks corrupted (U+FFFD, mojibake, runs of '?') is",
        "rejected. body_chars / body_sha256 are optional; compute them from that same file, probe",
        "line included. A matching fingerprint is trusted over the question-mark heuristic. If the",
        "fingerprint mismatches, or you must send the body as-is, put a reason (10+",
        "non-whitespace characters) in force_encoding_reason; it never overrides a damaged probe.",
    ]

def conversation_turn_submission_lines(*, api_base, doc_id, raw_token, token_id,
                                       payload_lines, can_write, provider):
    """Shared CLI/help turn contract; callers supply examples, never another token."""
    lines = [
        "",
        "To reply, append ONE turn. Do NOT resubmit the conversation body — the body is no",
        "longer where the conversation lives.",
        "",
        f"Submit: POST {api_base}/conversation/{doc_id}/turn",
        f"Authorization: Bearer {raw_token}",
        "",
        *payload_lines,
        "",
        "based_on_seq records what you had actually read when you wrote this. Send the head_seq",
        "from your last read — not a guess. If someone speaks while you are writing, your turn is",
        "still kept in order and marked as written without having seen theirs.",
        "",
        *encoding_probe_paragraph(token_id),
        "",
        f'To preview without registering, POST the same body to {api_base}/conversation/{doc_id}/turn',
        'with "dry_run": true added. Nothing is registered and the token is not consumed — only',
        "the corruption/fingerprint verdict (and encoding_probe: matched/broken/absent) comes back.",
    ]
    if can_write:
        lines += [
            "",
            "If you changed source files in this chat, put the commit message in commit_message:",
            "English, `type(scope): summary`, one line. Do not commit yourself: when this chat",
            "ends FlowGate commits the flow's worktree with that message and writes the result",
            "(or why the commit failed) into the conversation. commit_message is REQUIRED",
            "whenever the flow worktree holds uncommitted changes (yours or left from before):",
            "without it the turn is refused with 422 commit_message_required, dry_run included,",
            "and nothing is registered; resend the same turn with commit_message. Leave it out",
            "only when there is nothing to commit.",
        ]
    if provider:
        lines += [
            "display_name is a badge only and is not used to determine identity. The server",
            "fixed it to the pinned provider name; keep the value shown above.",
        ]
    else:
        lines += [
            "display_name is a badge only and is not used to determine identity. If you do not",
            "know your own model name, omit the field.",
        ]
    return lines


def conversation_turn_brief(*, api_base, doc_id, raw_token, token_id,
                            payload_lines, can_write, provider):
    from modules.flow_gate.services import encoding_probe
    lines = [
        "", "To reply, append ONE turn. Do NOT resubmit the conversation body.",
        f"Submit: POST {api_base}/conversation/{doc_id}/turn",
        f"Authorization: Bearer {raw_token}", *payload_lines,
        "based_on_seq: send head_seq from your last read, never a guess.",
        "Write the body as a UTF-8 file; read it and post ASCII-only JSON (json.dumps defaults).",
        "Never put the body or probe on a command line, console pipe, redirect, Set-Content or Out-File.",
        "Make the LAST line of the body exactly this probe (the server removes it before saving):",
        encoding_probe.probe_for(token_id),
        'Preview: add "dry_run": true to the same request; no registration or token consumption.',
        f"GET {api_base}/help/items/conversation/encoding",
        f"GET {api_base}/help/items/conversation/turn",
    ]
    if can_write:
        # Keep the existing commit contract verbatim; it is a prohibition/required field.
        full = conversation_turn_submission_lines(
            api_base=api_base, doc_id=doc_id, raw_token=raw_token, token_id=token_id,
            payload_lines=[], can_write=True, provider=provider)
        start = full.index("If you changed source files in this chat, put the commit message in commit_message:")
        lines += full[start:]
    else:
        lines += (["display_name is a badge only and is not used to determine identity.",
                   "Keep the pinned provider name." if provider else "If your model name is unknown, omit display_name."])
    return lines


register(Block("conversation.turn", brief=conversation_turn_brief,
               detail=conversation_turn_submission_lines, help_leaf="conversation/turn"))
register(Block("conversation.encoding", brief=encoding_probe_paragraph, detail=encoding_probe_paragraph,
               help_leaf="conversation/encoding"))
