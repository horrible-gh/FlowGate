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
        "Submitting below consumes this token, so finish all reading and searching first.",
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
    api_base_url: str,
    provider: Optional[str] = None,
) -> str:
    """The chat (CH) mention — the ONE builder for both chat paths (0293 T0005).

    Compact chat-only mention: read the CH conversation, append ONE AI turn,
    submit the full body via inbox action:edit (edit_reason=worker_self).

    Until 0293 this was a port of the browser's buildConversationMention
    (useFlowGateToken.ts), kept byte-identical by hand. The client builder is gone:
    POST /token/issue now accepts action_scope='chat' and returns this text as its
    `mention`, so the copy path and the invoke path read the same bytes by
    construction rather than by discipline (NR0004 발견 3).

    *provider* (NR0004 발견 4/5) is the value the SERVER knows for this run — the
    enabled provider's display name — and is only passed when the run is pinned to a
    single provider, because the mention is built before the fallback chain has
    picked one. When it is None the worker is asked to fill the slot in itself, and
    "I don't know" is an accepted answer: the parentheses are simply omitted and the
    UI draws no badge (there is no forgery threat here — the user knows who they
    pasted the mention to)."""
    if provider and ")" not in provider:
        # Server-known value: no instruction, the worker copies the header verbatim.
        header_line = f"## 🤖 AI({provider}) · <ISO-8601 timestamp>"
        provider_hint = None
    else:
        header_line = "## 🤖 AI(<your model name>) · <ISO-8601 timestamp>"
        provider_hint = (
            "Replace <your model name> with your own model name. If you do not know it, "
            "drop the parentheses entirely and write `## 🤖 AI` — that is a valid header."
        )
    api_base = api_base_url.rstrip("/")
    body: dict = {"action": "edit", "project": project}
    if module:
        body["module"] = module
    body.update({
        "group_name": group_name,
        "doc_id": doc_id,
        # inbox validates edit_reason against {rejected, qna_followup, user_comment,
        # worker_self}; an AI appending its own conversation turn is a self-initiated edit.
        "edit_reason": "worker_self",
        "content": "<the full conversation body, with your new turn appended at the end>",
    })
    from urllib.parse import quote

    doc_q = quote(doc_id, safe="")
    lines = [
        "## Conversation (대화)",
        "---",
        "You are a participant in an ongoing conversation. Read the latest messages and",
        "reply naturally and concisely. This is a chat — no document headers, no Q /",
        "clarification registration, no review. Just talk.",
        "",
        f"Conversation document: {doc_id}",
        f"Read the full conversation: GET {api_base}/document?doc_id={doc_q}",
        f"Authorization: Bearer {raw_token}",
        "",
        "To reply, append ONE new turn to the END of the existing body in this exact",
        "format, then submit the COMPLETE body (every existing turn + your new one):",
        "",
        header_line,
        "<your reply>",
    ]
    if provider_hint:
        lines += ["", provider_hint]
    # Before the submit block, not after: the worker reads it in the order it must act
    # (look things up, then submit), and the inbox JSON stays the tail of the mention.
    for section in _chat_lookup_sections(
        base=api_base, raw_token=raw_token, project=project, group_name=group_name
    ):
        lines += ["", section]
    lines += [
        "",
        f"Submit: POST {api_base}/inbox",
        f"Authorization: Bearer {raw_token}",
        "",
        json.dumps(body, indent=2, ensure_ascii=False),
    ]
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
