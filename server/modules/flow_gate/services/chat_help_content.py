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
        from flowgate.chat_control.tools import cli_mention
        sections = [cli_mention(base, token)]
    elif name == "chat_recovery":
        from flowgate.chat_control.recovery_tools import cli_mention
        sections = [cli_mention(base, token)]
    elif name == "chat_questions":
        from flowgate.card_document_edit.chat_questions import cli_mention_section
        sections = [cli_mention_section(base, token)]
    elif name == "chat_documents":
        from flowgate.card_document_edit.chat_tools import cli_mention_section, cli_document_mention_section
        sections = [cli_mention_section(base, token), cli_document_mention_section(base, token)]
    elif name == "chat_turn":
        from modules.flow_gate.services.invoke_mention_service import (
            conversation_turn_submission_lines, _encoding_probe_paragraph,
        )
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
