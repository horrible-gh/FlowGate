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
from modules.flow_gate.services import chat_settings_service

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
        # build_conversation_mention has no locale parameter of its own (chat mention
        # stays English-only by design — see its docstring); pin "en" explicitly rather
        # than falling through to the shared function's ko default.
        section = mention_service._remote_source_crud_section(base, raw_token, "CH", "en")
        if section:
            sections.append(section)

    search_lines = [
        f"Search documents by title/doc id: GET {base}/search/documents?q=<keyword>&project={project}",
        f"List documents in this group: GET {base}/list/groups/{group_name}/documents?limit=5",
        "",
        *mention_service._document_lookup_lines(
            base, raw_token, project=project
        ),
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


def _start_paragraph(folded: int) -> list[str]:
    """The paragraph that explains what ``after_seq`` is (L0010 §2-4-1).

    Unfolded, this is the existing wording, unchanged to the byte: it is what nearly
    every call produces, and rewriting it would shake every check that pins it.

    Folded, the original sentence is simply false — ``after_seq`` is no longer where
    that worker stopped reading. Left alone, the worker concludes it has already read
    the very turns it is about to be told were folded away, which is the whole reason
    for saying anything at all.
    """
    if folded == 0:
        return [
            "The after_seq above is YOUR last read position — the server tracks it, do not compute",
            "it yourself. `head` carries the document intro and the opening of the conversation as",
            "background; read it first, then the turns. If `next_after_seq` is not null, call again",
            "with that value until it is null. Reading does not consume this token.",
        ]
    return [
        "The after_seq above is where the server wants you to start — your own last read position,",
        "moved forward to the recent-conversation range this user chose. The server tracks it, do",
        "not compute it yourself. `head` carries the document intro and the opening of the",
        "conversation as background; read it first, then the turns. If `next_after_seq` is not null,",
        "call again with that value until it is null. Reading does not consume this token.",
    ]


def _fold_notice(
    *, api_base: str, doc_id: str, raw_token: str, folded: int, before_seq: int
) -> list[str]:
    """Say what was skipped and how to go and get it (L0010 §2-4-2).

    ``before_seq`` is ``start + 1`` because the backward page is ``seq < before_seq``
    (conversation_turns.fetch_turns_before). Passing ``start`` itself would drop the
    turn sitting on the boundary without a word about it.

    The singular split is not decoration: "The 1 turns … Read them" makes the notice
    itself the broken-looking part of the mention. build_rejection_section already
    splits item/items for the same reason.

    The line breaks are fixed exactly as written and do not reflow with the size of
    ``folded`` — that is what lets a check compare this text as a plain string.
    """
    if folded == 1:
        head = [
            "The 1 turn before that point is folded, not deleted. Read it when you need the",
            "earlier context:",
        ]
    else:
        head = [
            f"The {folded} turns before that point are folded, not deleted. Read them when you need the",
            "earlier context:",
        ]
    return head + [
        f"GET {api_base}/conversation/{doc_id}/turns?before_seq={before_seq}",
        f"Authorization: Bearer {raw_token}",
        "If `prev_before_seq` is not null, call again with that value to keep going further back.",
        # Said again here on purpose. The paragraph above carries the same rule, but the
        # place where "am I allowed to read further back" is actually decided is this
        # one (D0008 §3-3).
        "Paging back does not consume this token either.",
    ]


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
    user_id: Optional[str] = None,
) -> str:
    """Build the single-turn chat mention used by copy and in-app invoke paths.

    A pinned provider resumes from the cursor the server tracks for that provider.
    An unpinned copy/fallback mention starts at zero because the eventual participant
    is not known when the mention is issued. ``provider`` is display text only;
    identity comes exclusively from the token-bound ``provider_id``.

    0362 T0012: ``user_id`` is whoever pressed the button, and the range they saved can
    move the starting point forward from that cursor. Both call sites already hold the
    value and neither sends it over the wire — the screen does not get to say how much
    conversation an AI is handed, or a path that never went through the screen would
    have no value there at all, or a made-up one (P0009 시나리오 14). Left None, the
    settings come out as their defaults, which is also what an unreachable store gives.
    """
    del module  # Kept in the public signature for the two established call sites.
    last_read = (
        conversation_turns.get_last_read_seq(doc_id, f"provider:{provider_id}")
        if provider_id
        else 0
    )
    settings = chat_settings_service.resolve_chat_settings_safe(user_id)
    context_mode = settings["context_mode"]
    # [전체] adds no query. With the whole conversation on offer the head cannot change
    # the answer, and costing exactly what it cost before is what keeps "put it back on
    # [전체] and compare" usable as a way to diagnose anything (L0010 §2-3).
    head_seq = (
        conversation_turns.current_head_seq(doc_id) if context_mode == "recent" else 0
    )
    after_seq, folded = chat_settings_service.resolve_context_window(
        last_read=last_read,
        head_seq=head_seq,
        mode=context_mode,
        turns=settings["context_turns"],
    )
    api_base = api_base_url.rstrip("/")
    display_name = provider or "<your model name>"
    payload_lines = [
        "{",
        '  "body": "<your reply>",',
        f'  "idempotency_key": {json.dumps(token_id, ensure_ascii=False)},',
        '  "based_on_seq": <the head_seq you got from your last read>,',
        f'  "display_name": {json.dumps(display_name, ensure_ascii=False)},',
        '  "body_sha256": "<optional: sha256 hex of body, UTF-8 bytes>",',
        '  "body_chars": "<optional: character count of body>",',
        '  "force_encoding_reason": "<optional: only if a genuinely-flagged body must go through anyway>"',
        "}",
    ]
    lines = [
        "## Conversation",
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
        *_start_paragraph(folded),
    ]
    # Right behind the paragraph that named the starting point, and ahead of the reply
    # instructions: said in that order the worker reads the two as one thought. Appended
    # at the end of the document it reads the first half and moves on (P0009 시나리오 11).
    if folded > 0:
        lines += [""] + _fold_notice(
            api_base=api_base,
            doc_id=doc_id,
            raw_token=raw_token,
            folded=folded,
            before_seq=after_seq + 1,
        )
    lines += [
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
        "A body that looks corrupted (mojibake '?'s) is rejected. Calculation order: write the",
        "body to a UTF-8 file first, compute its character count (body_chars) and sha256",
        "(body_sha256) from that file, then build the request above. A matching fingerprint is",
        "trusted over the question-mark heuristic; if neither field is sent, the heuristic runs",
        "instead. If the fingerprint mismatches, or you must send the body as-is, put a reason",
        "(10+ non-whitespace characters) in force_encoding_reason.",
        "",
        f'To preview without registering, POST the same body to {api_base}/conversation/{doc_id}/turn',
        'with "dry_run": true added. Nothing is registered and the token is not consumed — only',
        "the corruption/fingerprint verdict comes back.",
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
