"""Server-side ports of the client-only mention builders (group 0223).

The in-app AI invoke must feed the worker the SAME text a human would have
copied from the matching [멘트복사] button (byte-identical principle,
ai_invoke_service.start_run). These builders existed only in the browser
(useFlowGateToken.ts / mentionMessages.ts / i18n templates), so an invoke
through the generic token mention would diverge from the copy flow. Each
function here mirrors its client counterpart exactly — if you change one
side, change the other.

0293 T0005: build_conversation_mention is no longer one of those mirrors. Its client
twin was deleted and /token/issue serves this text to the copy path, so the chat
mention now has exactly one implementation.

Copy texts that carry no token (reject template, design handoff, VR prompt)
cannot be byte-identical on their own: the invoked worker still needs
credentials to act. For those, the invoke prompt is the copy text followed by
the standard tokened mention for the underlying action scope.
"""
from __future__ import annotations

import json
from typing import Optional

from modules.flow_gate.db import conversation_turns

# Mirrors client SECTION_SEPARATOR / MESSAGES_SEPARATOR (mentionMessages.ts).
SECTION_SEPARATOR = "\n\n"
MESSAGES_SEPARATOR = "\n\n"

# t('main.next_action_modal.mm_section_header') — shared/i18n/{ko,en,ja}.ts:1582
_MM_SECTION_HEADER = {
    "ko": "사용자 메세지",
    "en": "User message",
    "ja": "ユーザーメッセージ",
}

# t('main.main_panel.reject_mention_template') — shared/i18n/{ko,en,ja}.ts:1535
_REJECT_TEMPLATE = {
    "ko": "## 반려\n{docName} 가 반려되었습니다.\n사유: {reason}",
    "en": "## Rejected\n{docName} has been rejected.\nReason: {reason}",
    "ja": "## 差し戻し\n{docName} が差し戻されました。\n理由: {reason}",
}

# t('main.design_handoff_dialog.mention_batch' / 'mention_single') — shared/i18n:928-929
_DESIGN_BATCH_TEMPLATE = {
    "ko": "워커님, 다음 단계는 설계 작업입니다. 다음 타입을 일괄 생성해 주세요: {types}\n대상: {docRef}",
    "en": "Worker, the next step is design-related. Please create the following types as a batch: {types}\nTarget: {docRef}",
    "ja": "ワーカーさん、次のステップは設計作業です。次のタイプを一括で作成してください: {types}\n対象: {docRef}",
}
_DESIGN_SINGLE_TEMPLATE = {
    "ko": "워커님, 다음 단계는 {label}({code})입니다. 다음 단계를 시작해 주세요.\n대상: {docRef}",
    "en": "Worker, the next step is {label}({code}). Please start the next step.\nTarget: {docRef}",
    "ja": "ワーカーさん、次のステップは{label}({code})です。次のステップを開始してください。\n対象: {docRef}",
}


def _locale(locale: Optional[str]) -> str:
    return locale if locale in ("ko", "en", "ja") else "ko"


def _chat_lookup_sections(
    *, base: str, raw_token: str, project: str, group_name: str
) -> list[str]:
    """Read-only source + document search block for the chat mention (0334 R0001).

    R0001's symptom: asked to check something in the source, the chat worker had no
    API for anything outside its own CH document, so it guessed at a local directory
    and reported the guess as fact. The scopes were never the problem — a chat token
    already resolves to ["read", "grep"] (glob shares grep's scope), and verify_bearer
    accepts it on /search/documents* — the mention simply never said so (NR0003 발견
    1/4). So this adds text, not permission.

    The source section is the same builder N/NR mentions use, asked for a "CH" step so
    it renders its read/search-only variant, behind the same source-mode gate: when the
    project runs in local mode there is no remote source API to advertise (발견 3).
    Document search stays either way — it reads storage, not the source tree.

    Kept deliberately small. TR0044.0010 rev4 rejected a chat mention padded with
    non-chat sections, and compactness is still the rule here (발견 9).
    """
    # Lazy: mention_service pulls in the full mention stack, and this module is
    # imported by the invoke path at request time.
    from modules.flow_gate.services import mention_service

    sections: list[str] = []
    source_included = mention_service._include_remote_source_crud(project)
    if source_included:
        section = mention_service._remote_source_crud_section(base, raw_token, "CH")
        if section:
            sections.append(section)

    search_lines = [
        f"Search documents by title/doc id: GET {base}/search/documents?q=<keyword>&project={project}",
        f"Search inside document bodies: GET {base}/search/documents/content?q=<keyword>&project={project}",
        f"List documents in this group: GET {base}/list/groups/{group_name}/documents?limit=5",
        f"Authorization: Bearer {raw_token}",
    ]
    if source_included:
        search_lines += [
            "",
            # 발견 5: every remote path is root-relative and the server picks the root
            # (this group's worktree, else the project branch checkout). Naming an
            # absolute path here would just give the worker a new wrong thing to trust.
            "Source paths are relative to the project source root, which the server resolves",
            "on its own (this group's worktree, otherwise the project branch checkout). Do not",
            "guess local absolute paths or search your own filesystem — use the APIs above.",
        ]
    search_lines += [
        "",
        # 발견 8: a successful edit consumes the token and its remote grant dies with it.
        "Submitting your turn consumes this token, so finish all reading and searching first.",
    ]
    sections.append("## Document search and lookup rules\n---\n" + "\n".join(search_lines))
    return sections


def build_conversation_mention(
    *,
    doc_id: str,
    project: str,
    module: Optional[str],
    group_name: str,
    raw_token: str,
    token_id: str,
    api_base_url: str,
    provider: Optional[str] = None,
    provider_id: Optional[str] = None,
) -> str:
    """Build the single-turn chat mention used by copy and in-app invoke paths.

    A pinned provider resumes from the cursor the server tracks for that provider.
    An unpinned copy/fallback mention starts at zero because the eventual participant
    is not known when the mention is issued. ``provider`` is display text only;
    identity comes exclusively from the token-bound ``provider_id``.
    """
    del module  # Kept in the public signature for the two established call sites.
    after_seq = (
        conversation_turns.get_last_read_seq(doc_id, f"provider:{provider_id}")
        if provider_id
        else 0
    )
    api_base = api_base_url.rstrip("/")
    display_name = provider or "<your model name>"
    payload_lines = [
        "{",
        '  "body": "<your reply>",',
        f'  "idempotency_key": {json.dumps(token_id, ensure_ascii=False)},',
        '  "based_on_seq": <the head_seq you got from your last read>,',
        f'  "display_name": {json.dumps(display_name, ensure_ascii=False)}',
        "}",
    ]
    lines = [
        "## Conversation (대화)",
        "---",
        "You are a participant in an ongoing conversation. Read the latest messages and",
        "reply naturally and concisely. This is a chat — no document headers, no Q /",
        "clarification registration, no review. Just talk.",
        "",
        f"Conversation document: {doc_id}",
        "",
        "Read what you have not read yet:",
        f"GET {api_base}/conversation/{doc_id}/turns?after_seq={after_seq}&include_head=1",
        f"Authorization: Bearer {raw_token}",
        "",
        "The after_seq above is YOUR last read position — the server tracks it, do not compute",
        "it yourself. `head` carries the document intro and the opening of the conversation as",
        "background; read it first, then the turns. If `next_after_seq` is not null, call again",
        "with that value until it is null. Reading does not consume this token.",
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
    for section in _chat_lookup_sections(
        base=api_base, raw_token=raw_token, project=project, group_name=group_name
    ):
        lines += ["", section]
    return "\n".join(lines)

def build_rejection_section(history: list[dict], last: Optional[str]) -> str:
    """Port of buildRejectionSection (useFlowGateToken.ts).

    history items: {rejected_at, reason}. The final history entry duplicates
    `last`, so only the PRIOR entries are listed as context (R0001 #2 / T0004).
    """
    if not last and not history:
        return ""
    parts: list[str] = [
        "## Revision Request",
        "---",
        "Requesting document revisions for the reason(s) below. Apply the latest rejection first; prior history (if any) is listed for context.",
        "",
        "### Last rejection reason (apply first on rework)",
        last or "",
    ]
    prior = history[:-1]
    if prior:
        parts.append("")
        unit = "item" if len(prior) == 1 else "items"
        parts.append(f"### Prior rejection history ({len(prior)} {unit}, chronological)")
        for i, item in enumerate(prior):
            parts.append(f"{i + 1}. [{item.get('rejected_at', '')}] {item.get('reason', '')}")
    parts.append("")
    return "\n".join(parts)


def prepend_messages_section(mention_text: str, messages: list[str], locale: Optional[str]) -> str:
    """Port of prependMessagesSection (mentionMessages.ts) with the localized header."""
    body = MESSAGES_SEPARATOR.join(m.strip() for m in messages if m and m.strip())
    if not body:
        return mention_text
    header = _MM_SECTION_HEADER[_locale(locale)]
    section = f"## {header}\n---\n{body}"
    if not mention_text:
        return section
    return section + SECTION_SEPARATOR + mention_text


def build_reject_context(doc_name: str, reason: str, locale: Optional[str]) -> str:
    """Port of t('main.main_panel.reject_mention_template', {docName, reason})."""
    return _REJECT_TEMPLATE[_locale(locale)].replace("{docName}", doc_name).replace("{reason}", reason)


def build_design_handoff_context(
    *,
    types: list[str],
    mode: str,
    doc_ref: str,
    locale: Optional[str],
    first_label: Optional[str] = None,
) -> str:
    """Port of DesignHandoffDialog.mentionText (mention_batch / mention_single)."""
    loc = _locale(locale)
    if mode == "batch":
        return (
            _DESIGN_BATCH_TEMPLATE[loc]
            .replace("{types}", " / ".join(types))
            .replace("{docRef}", doc_ref)
        )
    first = types[0] if types else ""
    return (
        _DESIGN_SINGLE_TEMPLATE[loc]
        .replace("{label}", first_label or first)
        .replace("{code}", first)
        .replace("{docRef}", doc_ref)
    )
