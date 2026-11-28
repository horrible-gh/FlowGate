"""Detailed chat help reads the shared contract blocks from the prompt block registry.

Help is documentation, not a grant. Actual API authorization remains unchanged.
Help never calls a mention builder. The turn contract and the
encoding probe are ``prompt_blocks`` details; the card/recovery/question/document contracts are the
chat tool modules' own ``contract_*`` functions (single definitions owned under ``server/flowgate``).
"""
import json

from modules.flow_gate.services import prompt_blocks
from modules.flow_gate.services.prompt_blocks.blocks.conversation import (  # noqa: F401 - compatibility re-export
    conversation_turn_submission_lines,
    encoding_probe_paragraph as _encoding_probe_paragraph,
)

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


def content(name, ctx):
    base = ctx["base_url"]
    token = "<YOUR_CHAT_TOKEN>"
    if name == "chat_control":
        from flowgate.chat_control import tools
        sections = ["\n".join(getattr(tools, "contract_" + topic)(base, token)
                    for topic in ("planning", "ai_options", "place", "run", "roles"))]
    elif name == "chat_recovery":
        from flowgate.chat_control.recovery_tools import contract_mention as recovery_contract
        sections = [recovery_contract(base, token)]
    elif name == "chat_questions":
        from flowgate.card_document_edit.chat_questions import contract_mention_section as questions_contract
        sections = [questions_contract(base, token)]
    elif name == "chat_documents":
        from flowgate.card_document_edit.chat_tools import contract_document_mention_section, contract_mention_section
        sections = [contract_mention_section(base, token), contract_document_mention_section(base, token)]
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
        lines = prompt_blocks.detail(
            "conversation.turn", api_base=base, doc_id="{doc_id}", raw_token=token, token_id=token_id,
            payload_lines=json.dumps(example, ensure_ascii=False, indent=2).splitlines(),
            can_write=True, provider=None,
        )
        sections = ["\n".join(lines).replace(prompt_blocks.detail("conversation.encoding", token_id)[2],
                    "<encoding_probe supplied in this invocation; never copy this placeholder>")]
    else:
        raise ValueError(name)
    return {"intro": INTRO[ctx["locale"]], "sections": sections}
