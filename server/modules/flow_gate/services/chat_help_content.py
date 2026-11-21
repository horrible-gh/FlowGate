"""Detailed chat help reuses the authoritative CLI contract renderers.

Help is documentation, not a grant. Actual API authorization remains unchanged.
"""
import json

ITEMS = ("chat_control", "chat_recovery", "chat_turn", "chat_questions", "chat_documents")
TITLES = {
    "ko": ("채팅 카드 배치·실행", "Flow 복구", "채팅 턴 전송", "채팅 카드 질의 목록·답변", "채팅 문서 읽기·수정·생성"),
    "ja": ("チャットのカード配置・実行", "Flow の復旧", "チャットターン送信", "カード質問の一覧・回答", "文書の参照・編集・作成"),
    "en": ("Chat card placement and control", "Flow recovery", "Chat turn submission", "Chat card questions and answers", "Chat document read, edit and create"),
}
INTRO = {
    "ko": "현재 채팅 도구의 상세 계약입니다. 아래 영문은 기존 도구 안내 원문이며, help 열람은 API 쓰기 권한을 부여하지 않습니다.",
    "ja": "現在のチャットツールの詳細契約です。以下の英語は既存ツールの案内原文です。help の参照は API の書込権限を付与しません。",
    "en": "Detailed current chat tool contracts. The canonical tool instructions below do not grant API write permission.",
}


def help_item_url(name, base_url="/flowgate/api/v1"):
    """Stable pointer for the next prompt-selection card (no prompt insertion here)."""
    if name not in ITEMS and name != "document_attachments":
        raise ValueError("unknown chat help item: " + name)
    return base_url.rstrip("/") + "/help/items/" + name


def content(name, ctx):
    base = ctx["base_url"]
    token = "<YOUR_CHAT_TOKEN>"
    if name == "chat_control":
        from flowgate.chat_control import tools
        sections = ["\n".join(getattr(tools, "contract_" + topic)(base, token)
                    for topic in ("planning", "ai_options", "place", "run", "roles"))]
    elif name == "chat_recovery":
        from flowgate.chat_control.recovery_tools import contract_mention as cli_mention
        sections = [cli_mention(base, token)]
    elif name == "chat_questions":
        from flowgate.card_document_edit.chat_questions import contract_mention_section as cli_mention_section
        sections = [cli_mention_section(base, token)]
    elif name == "chat_documents":
        from flowgate.card_document_edit.chat_tools import contract_mention_section as cli_mention_section, contract_document_mention_section as cli_document_mention_section
        sections = [cli_mention_section(base, token), cli_document_mention_section(base, token)]
    elif name == "chat_turn":
        # A documentation probe must never masquerade as this invocation's real probe.
        token_id = "help-example-not-a-live-token"
        example = {
            "body": "<reply; actual invocation encoding probe as the last line>",
            "idempotency_key": "<this invocation token_id>",
            "based_on_seq": "<head_seq from last read>",
            "display_name": "<model name>",
            "body_sha256": "<optional UTF-8 sha256>",
            "body_chars": "<optional character count>",
            "force_encoding_reason": "<optional reason>",
            "commit_message": "<required if worktree has uncommitted changes>",
        }
        lines = conversation_turn_submission_lines(
            api_base=base, doc_id="{doc_id}", raw_token=token, token_id=token_id,
            payload_lines=json.dumps(example, ensure_ascii=False, indent=2).splitlines(),
            can_write=True, provider=None,
        )
        sections = ["\n".join(lines).replace(_encoding_probe_paragraph(token_id)[2],
                    "<encoding_probe supplied in this invocation; never copy this placeholder>")]
    else:
        raise ValueError(name)
    return {"intro": INTRO[ctx["locale"]], "sections": sections,
            "help_url": help_item_url(name, base)}


def _encoding_probe_paragraph(token_id: str) -> list[str]:
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
        *_encoding_probe_paragraph(token_id),
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
