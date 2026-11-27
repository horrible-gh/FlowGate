"""Server-side ports of the client-only mention builders (the earlier change).

The in-app AI invoke must feed the worker the SAME text a human would have
copied from the matching [copy mention] button (byte-identical principle,
ai_invoke_service.start_run). These builders existed only in the browser
(useFlowGateToken.ts / mentionMessages.ts / i18n templates), so an invoke
through the generic token mention would diverge from the copy flow. Each
function here mirrors its client counterpart exactly — if you change one
side, change the other.

build_conversation_mention is no longer one of those mirrors. Its client
twin was deleted and /token/issue serves this text to the copy path, so the chat
mention now has exactly one implementation.

Copy texts that carry no token (reject template, design handoff, VR prompt)
cannot be byte-identical on their own: the invoked worker still needs
credentials to act. For those, the invoke prompt is the copy text followed by
the standard tokened mention for the underlying action scope.
"""
from __future__ import annotations
from modules.flow_gate.services import terminal_prompt

import json
from typing import Optional

from modules.flow_gate.db import conversation_turns
from modules.flow_gate.services import chat_doc_settings_service
from modules.flow_gate.services import chat_settings_service
from modules.flow_gate.services import display_names

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

# ── the earlier implementation: the previous-run handoff block ────────────────────────────
# Added to a rework prompt ONLY when the run right before it on the same document was
# stopped by the clock. Everything here is a label around data the server already
# stored; the diagnosis sentence itself comes from the `ai_invoke_runs` row and, like
# its neighbour `stop_reason`, is written in English (a row has no locale).
_PREV_RUN_HEADING = {
    "ko": "직전 AI 실행",
    "en": "Previous AI run",
    "ja": "直前のAI実行",
}
_PREV_RUN_TIMED_OUT = {
    "ko": "직전 실행({runId})이 제한시간에 걸려 중단되었습니다. 아래는 그 실행이 남긴 상태입니다.",
    "en": "The previous run on this document ({runId}) was stopped by its time limit. What it left behind is below.",
    "ja": "直前の実行（{runId}）は制限時間により中断されました。以下はその実行が残した状態です。",
}
_PREV_RUN_DIAGNOSIS = {
    "ko": "중단 진단",
    "en": "Stop diagnosis",
    "ja": "中断診断",
}
_PREV_RUN_SOURCE = {
    "ko": "소스 상태",
    "en": "Source state",
    "ja": "ソース状態",
}
_PREV_RUN_SOURCE_DIRTY = {
    "ko": "그 실행이 시작된 뒤 생긴 변경 {count}건이 작업 폴더에 남아 있습니다.",
    "en": "{count} change(s) made after that run started were left in the working tree.",
    "ja": "その実行の開始後に生じた変更が{count}件、作業フォルダに残っています。",
}
_PREV_RUN_SOURCE_DIRTY_NO_LIST = {
    "ko": "작업 폴더에 남은 변경이 있지만 파일 목록은 기록되지 않았습니다. 직접 확인하십시오.",
    "en": "Changes were left in the working tree, but no file list was recorded. Check it yourself.",
    "ja": "作業フォルダに残った変更がありますが、ファイル一覧は記録されていません。ご自身で確認してください。",
}
_PREV_RUN_SOURCE_CLEAN = {
    "ko": "그 실행이 남긴 변경은 없습니다.",
    "en": "That run left no change behind.",
    "ja": "その実行が残した変更はありません。",
}
_PREV_RUN_SOURCE_UNKNOWN = {
    "ko": "작업 폴더 상태를 확인하지 못했습니다. 남은 변경이 있는지는 알 수 없습니다.",
    "en": "The working-tree state could not be read, so whether anything was left behind is unknown.",
    "ja": "作業フォルダの状態を確認できなかったため、変更が残っているかは不明です。",
}
_PREV_RUN_GUIDE = {
    "ko": "이 파일들은 이전 세션의 미완 변경일 수 있습니다. 이어서 고치기 전에 각 파일의 현재 내용을 먼저 읽어 검증하십시오.",
    "en": "These files may be an unfinished edit from that earlier session. Read and verify each file's current content before continuing to change it.",
    "ja": "これらのファイルは前のセッションの未完了の変更である可能性があります。続けて修正する前に、各ファイルの現在の内容を読んで検証してください。",
}
# The server stores at most this many paths (ai_invoke_service.SOURCE_DIRTY_FILES_LIMIT).
# Restated rather than imported: this module is the mention side and must not start
# depending on the run service, but it must not print more than what was stored either.
_PREV_RUN_FILES_LIMIT = 20


def _locale(locale: Optional[str]) -> str:
    return locale if locale in ("ko", "en", "ja") else "ko"


def _chat_lookup_sections(
    *, base: str, raw_token: str, project: str, group_name: str,
    source_access: Optional[str] = None,
) -> list[str]:
    """Read-only source + document search block for the chat mention.

    's symptom: asked to check something in the source, the chat worker had no
    API for anything outside its own CH document, so it guessed at a local directory
    and reported the guess as fact. The scopes were never the problem — a chat token
    already resolves to ["read", "grep"] (glob shares grep's scope), and verify_bearer
    accepts it on /search/documents* — the mention simply never said so (the review finding
    1/4). So this adds text, not permission.

    The source section is the same builder N/NR mentions use, asked for a "CH" step so
    it renders its read/search-only variant, behind the same source-mode gate: when the
    project runs in local mode there is no remote source API to advertise (finding 3).
    Document search stays either way — it reads storage, not the source tree.

    Kept deliberately small. the earlier implementation.0010 rejected a chat mention padded with
    non-chat sections, and compactness is still the rule here (finding 9).
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
        #
        # action_scope must be "chat", not the default "new": passing
        # the real scope is what keeps this judge matching whatever remote_tool_service
        # actually grants a chat token, instead of guessing from the step type alone.
        # Under 0392's chat=none policy the two diverged — default "new" judged "read"
        # while a real chat token got "none" — so this call used to render the section
        # empty. the earlier implementation/the review flips that policy: kind_for_step now returns "read"
        # for "chat" at the same early branch as "review"/"workflow_decide", so the
        # section renders the four read-only tools, still matching what the token can
        # actually call.
        # the just-issued token's real capability, when known, wins over
        # the generic chat default (kind_for_step("chat", ...) always answers "read",
        # blind to this user's edit/edit_once setting). None/damaged falls through to
        # that same default via _remote_source_crud_section's own kind=None branch.
        section = mention_service._remote_source_crud_section(
            base, raw_token, "CH", "en", action_scope="chat",
            kind=source_access if source_access in ("read", "read_write") else None,
        )
        if section:
            sections.append(section)

    search_lines = [
        f"List flows with names: GET {base}/list/projects/{project}/groups (flow_label F77, flow_name F77(name))",
        f"Read flow basic information: GET {base}/list/groups/<group_id> (also takes F77 / F77-12; answers flow_ref, flow_name)",
        "Flow/document lookup is allowed in projects the requesting user can read; other flow canvases remain restricted.",
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
            # Finding 5: every remote path is root-relative and the server picks the root
            # (this group's worktree, else the project branch checkout). Naming an
            # absolute path here would just give the worker a new wrong thing to trust.
            "Source paths are relative to the project source root, which the server resolves",
            "on its own (this group's worktree, otherwise the project branch checkout). Do not",
            "guess local absolute paths or search your own filesystem — use the APIs above.",
        ]
    search_lines += [
        "",
        # Finding 8: a successful edit consumes the token and its remote grant dies with it.
        "Submitting your turn consumes this token, so finish all reading and searching first.",
    ]
    sections.append("## Document search and lookup rules\n---\n" + "\n".join(search_lines))
    return sections


def _command_section(*, base: str, raw_token: str) -> str:
    """how a CLI chat worker runs tests/builds/git through FlowGate.

    Only an in-app AI invocation has a live run for a command to belong to, so the
    copy-mention path (no run) never shows this. API providers get the same contract
    as the in-process ``run_command`` tool.
    """
    lines = [
        "## Command execution",
        "---",
        "Run tests, builds, linters and git commands for this conversation through FlowGate so",
        "they execute in this group's worktree under the user's command policy (run at once,",
        "wait for the user's approval, or refuse). API tool-calling runs use the `run_command`",
        "tool instead of this endpoint.",
        "",
        f"POST {base}/chat-commands",
        f"Authorization: Bearer {raw_token}",
        "",
        '{"program": "pytest", "args": ["-q", "server/tests/test_x.py"], "cwd": ".",',
        ' "timeout_seconds": 300, "wait_seconds": 60}',
        "",
        "program is the executable name only and args are separate strings: no shell, no",
        "pipes/redirection/&&, no inline `python -c` / `node -e`. cwd is relative to the worktree.",
        "When the response has terminal=false (waiting for approval, or still running), keep",
        f"calling GET {base}/chat-commands/<request_id>?wait=60 with the same Authorization until",
        "terminal=true. result carries status, exit_code, stdout, stderr, timed_out, rejected and",
        "rejected_by. A rejected command was not run: do not resend it unchanged; continue with",
        "what you have and say so in your reply. Running commands does not consume this token,",
        "so finish the commands you need before you submit your turn.",
    ]
    return "\n".join(lines)


def _conversation_reread_lines(locale: Optional[str]) -> list[str]:
    if _locale(locale) == "ko":
        return [
            "제출 직전에 같은 대화 읽기 주소를 마지막으로 읽은 seq 이후(after_seq)로 다시 조회하되, 최초 범위의 through_seq·limit은 빼고 next_after_seq가 null일 때까지 읽으십시오. 새 사용자 메시지를 답에 반영하고 그때 받은 head_seq를 based_on_seq로 보내십시오.",
            "작업이 길면 큰 단계를 마칠 때마다 같은 방법으로 새 메시지를 확인하고, 방향이 바뀌면 이후 작업에 반영하십시오.",
        ]
    return [
        "Immediately before submitting, reread the same conversation endpoint after your last read seq (after_seq), without the initial through_seq or limit, following next_after_seq until null. Incorporate new user messages and send the head_seq from that read as based_on_seq.",
        "For long tasks, check for new messages the same way after each major stage; if they change direction, follow that direction in subsequent work.",
    ]


def _start_paragraph(folded: int) -> list[str]:
    """Explain the invocation-local meaning of ``after_seq``."""
    del folded
    return [
        "after_seq starts this invocation's window, not persisted model memory; overlap is intentional.",
        "Read messages in `turns` (head is metadata). Follow next_after_seq until null; reads do not consume the token.",
    ]



def _fold_notice(
    *, api_base: str, doc_id: str, raw_token: str, after_seq: int, through_seq: Optional[int]
) -> list[str]:
    """Say which turns this invocation received and how to read the earlier ones.

    only the range actually delivered is stated. Whatever decided it
    (the latest-N limit or the user's checkpoint) is a user-side way of keeping the context
    small and is not the AI's business; naming it made the notice carry two different
    starting points whenever the limit was narrower than the checkpoint.

    The backward link uses ``before_seq = after_seq + 1`` because the backward page is
    ``seq < before_seq`` (conversation_turns.fetch_turns_before). Passing ``after_seq``
    itself would drop the turn sitting on the boundary without a word about it.

    The line breaks are fixed exactly as written so a check can compare this text as a
    plain string.
    """
    first = after_seq + 1
    if through_seq is not None and through_seq >= first:
        count = through_seq - first + 1
        if count == 1:
            head = f"This invocation's context is the latest turn (seq {first})."
        else:
            head = f"This invocation's context is the latest {count} turns (seq {first} to {through_seq})."
    else:
        head = f"This invocation's context starts at seq {first}."
    return [
        head,
        f"Turns before seq {first} are not included. Read them only when the request depends on them:",
        f"GET {api_base}/conversation/{doc_id}/turns?before_seq={first}",
        f"Authorization: Bearer {raw_token}",
        "If `prev_before_seq` is not null, call again with that value to keep going further back.",
        # Said again here on purpose. The paragraph above carries the same rule, but the
        # place where "am I allowed to read further back" is actually decided is this
        # one.
        "Paging back does not consume this token either.",
    ]


# The turn contract and the probe paragraph are prompt blocks.
from modules.flow_gate.services.prompt_blocks.blocks.conversation import (  # noqa: E402
    conversation_turn_submission_lines,
    encoding_probe_paragraph as _encoding_probe_paragraph,
)


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
    # the just-minted token's own `source_access` ("read"/"read_write"/
    # None) -- both call sites (token_routes, ai_invoke_routes) already hold it on the
    # token they just issued/inspected. Threaded to _chat_lookup_sections so the CRUD
    # section this mention advertises matches the real grant, not a generic chat guess.
    source_access: Optional[str] = None,
    # True only from the in-app AI invoke
    # path, whose token is bound to a live run that a command request can belong to. The
    # sections come from the chat run hook port (or, with none registered, the in-module
    # command section); the copy path (no run) never shows them, and neither does a token
    # whose token carries no command access (reading mode).
    command_execution: bool = False,
    # the token's own `command_access` ("execute" for an execute-mode
    # chat: commands allowed while source_access stays "read"; None otherwise).
    command_access: Optional[str] = None,
    one_shot_claimed: Optional[bool] = None,
    locale: Optional[str] = None,
) -> str:
    """Build the single-turn chat mention used by copy and in-app invoke paths.

    Each invocation derives its context window from the saved mode/count and current
    conversation head. A persisted provider participant cursor is delivery history, not
    model memory, and does not choose that window. ``provider`` is display text only;
    identity remains token-bound.

    ``user_id`` selects the saved chat settings. Left None, defaults are used.
    """
    del module, provider_id  # Public signature is retained; neither chooses the window.
    settings = chat_settings_service.resolve_chat_settings_safe(user_id)
    # [all] (and a set checkpoint) adds no head query. With a fixed start the head cannot change
    # the answer, and costing exactly what it cost before is what keeps "put it back on
    # [all] and compare" usable as a way to diagnose anything.
    # the same window helper as the API worker, so [checkpoint] (from / after the
    # chat's checkpoint, or the last 5 turns without one) is sent identically on both paths.
    window = chat_doc_settings_service.context_window(
        user_id, doc_id, settings, head_seq_of=conversation_turns.current_head_seq,
    )
    after_seq, folded = window["after_seq"], window["folded"]
    window_query = ""
    if window.get("through_seq") is not None:
        window_query += f"&through_seq={window['through_seq']}"
    if window.get("limit") is not None:
        window_query += f"&limit={window['limit']}"
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
    # an edit-mode chat names its commit; FlowGate commits at the run end.
    can_write = source_access == "read_write"
    if one_shot_claimed is None:
        # In-app mentions are built before admission commits/clears the claim.
        # Compare token identity, never the saved mode (already reset to read_only).
        one_shot_claimed = False
        if can_write and user_id:
            from modules.flow_gate.db import user_chat_source_access
            claim = user_chat_source_access.get(user_id, project, group_name) or {}
            one_shot_claimed = claim.get("one_shot_token_id") == token_id
    if can_write:
        payload_lines[-2] += ","
        payload_lines.insert(-1, '  "commit_message": "<required when the flow worktree has uncommitted changes: see below>"')
    lines = [
        "## Conversation",
        "---",
        f"Conversation document: {doc_id}",
        "",
        "Read the conversation context selected for this invocation:",
        f"GET {api_base}/conversation/{doc_id}/turns?after_seq={after_seq}&include_head=1{window_query}",
        f"Search within this conversation: GET {api_base}/search/documents/content?q=<keyword>&doc_id={doc_id}&sort=desc (asc for oldest first; continue with cursor=<next_cursor> using the same query until next_cursor is null).",
        "Search results carry only seq, speaker, display_name, created_at and a short snippet; read the turns you need by seq (turns?after_seq=<seq-1>&through_seq=<seq>&limit=1).",
        f"Authorization: Bearer {raw_token}",
        "",
        *_start_paragraph(folded),
        *_conversation_reread_lines(locale),
        "When paging this selected context, keep through_seq and limit from the initial URL unchanged.",
    ]
    from modules.flow_gate.services.prompt_blocks.registry import brief
    lines += ["", brief("chat.current_mode", source_access=source_access,
                        command_access=command_access, one_shot_claimed=one_shot_claimed,
                        locale=_locale(locale))]
    # Right behind the paragraph that named the starting point, and ahead of the reply
    # instructions: said in that order the worker reads the two as one thought. Appended
    # at the end of the document it reads the first half and moves on (scenario 11).
    if folded > 0:
        lines += [""] + _fold_notice(
            api_base=api_base,
            doc_id=doc_id,
            raw_token=raw_token,
            after_seq=after_seq,
            through_seq=window.get("through_seq"),
        )
    from modules.flow_gate.services.prompt_blocks.blocks.conversation import conversation_turn_brief
    lines += conversation_turn_brief(
        api_base=api_base, doc_id=doc_id, raw_token=raw_token, token_id=token_id,
        payload_lines=payload_lines, can_write=can_write, provider=provider,
    )
    for section in _chat_lookup_sections(
        base=api_base, raw_token=raw_token, project=project, group_name=group_name,
        source_access=source_access,
    ):
        lines += ["", section]
    # A reading-mode chat token can never run a command (commands._authorize_run refuses it),
    # so its mention does not offer the endpoint either: only a token with command access --
    # read_write (edit / edit_once) or execute. The other chat tool
    # sections (graph read, attachment request, card document) stay in reading mode too.
    may_run = can_write or (source_access == "read" and command_access == "execute")
    from modules.flow_gate.services.prompt_selection import chat_situation
    situation = chat_situation(group_name, can_write=can_write)
    if command_execution:
        from modules.flow_gate.services import chat_run_hooks

        omit = () if may_run else ("run_command",)
        sections = chat_run_hooks.chat_mention_sections(
            api_base, raw_token, omit=omit,
            situation=situation,
        )
        if not chat_run_hooks.registered(chat_run_hooks.PROVIDE_CHAT_TOOLS):
            sections = [_command_section(base=api_base, raw_token=raw_token)] if may_run else []
        for section in sections:
            lines += ["", section]
    from modules.flow_gate.services import question_policy
    policy = question_policy.text(user_id)
    if policy:
        lines += ["", "Before taking action or asking the user, read and follow their decision policy:",
                  f"GET {api_base}/help/items/question/policy"]
    from modules.flow_gate.services import message_image_delivery
    context = {"conversation_id": doc_id, "after_seq": after_seq, "through_seq": window.get("through_seq"), "locale": _locale(locale)}
    from modules.flow_gate.services.prompt_blocks.compose import compose
    # Deduplicate only generated credential lines; never rewrite policy or conversation data.
    auth = f"Authorization: Bearer {raw_token}"
    body = "\n".join(lines)
    body = "\n".join(line for line in body.splitlines() if line != auth)
    body = (auth + "\nUse this Authorization for all API calls below; never log the Bearer.\n"
            + display_names.bound_flow_line(group_name, _group_title(group_name))
            + " Read cards/state help to discover node keys when needed.\n"
            + display_names.NAMING_RULE + "\n\n"
            + body)
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    title_request = first_names_hooks.title_request(group_name, _locale(locale), "chat", doc_id)
    if title_request:
        body += "\n\n## " + {"ko": "작업 이름", "ja": "作業名", "en": "Task names"}[_locale(locale)] + "\n---\n" + title_request
    text = compose([body], kind="chat", locale=_locale(locale),
                                   unanswered=situation.unanswered, can_write=can_write)
    if command_execution:
        return message_image_delivery.ConversationMention(text, context)
    notice = message_image_delivery.copy_notice(context)
    return terminal_prompt.finalize(text + ("\n\n" + notice if notice else ""))

def _group_title(group_id: str) -> Optional[str]:
    """The bound Flow's name for the naming line; None when it cannot be read (the line still names F<n>)."""
    try:
        from modules.flow_gate.db import groups as db_groups
        return (db_groups.get_by_id(group_id) or {}).get("title")
    except Exception:  # a missing table / store must never break the chat mention
        return None


def _single_line(value: Optional[str]) -> str:
    """Collapse stored text to one safe line for a bullet.

    Both the diagnosis sentence and the spilled paths are DATA. `git status` will report a
    path containing a newline or a backtick without complaint, and either one, printed
    verbatim, ends the line (or the code span) early and lets the remainder read as prompt
    text of its own.
    """
    text = str(value or "").replace("\r", " ").replace("\n", " ").replace("`", "'")
    return " ".join(text.split()).strip()


def build_previous_run_section(handoff: Optional[dict], locale: Optional[str]) -> str:
    """The previous-run handoff block for a rework prompt.

    `handoff` is exactly what `ai_invoke_service.previous_timeout_handoff()` returned, so
    it has ALREADY been narrowed to "the newest finished run on this document ended on the
    clock". Anything falsy returns "" and the caller then adds nothing at all — that empty
    string is what keeps every other rework prompt byte-identical (§4-2).

    Two things are deliberately absent (§4-4). The stored stdout/stderr tails are
    post-mortem material for a person reading the run card, not an instruction to a worker.
    And the previous run's scratch directory is gone as far as this worker is concerned —
    every run gets its own — so naming it would be handing over something unopenable.
    """
    if not handoff:
        return ""
    loc = _locale(locale)
    lines = [
        f"## {_PREV_RUN_HEADING[loc]}",
        "---",
        _PREV_RUN_TIMED_OUT[loc].replace("{runId}", _single_line(handoff.get("run_id")) or "?"),
    ]
    diagnosis = _single_line(handoff.get("timeout_diagnosis"))
    if diagnosis:
        # Absent on a legacy row, or on a timeout the watchdog never marked. The block still
        # reports the timeout itself — that fact does not depend on the sentence.
        lines.append(f"- {_PREV_RUN_DIAGNOSIS[loc]}: {diagnosis}")

    dirty = handoff.get("source_dirty")
    files = [p for p in (_single_line(p) for p in (handoff.get("source_dirty_files") or [])) if p]
    files = files[:_PREV_RUN_FILES_LIMIT]
    if dirty is None:
        lines.append(f"- {_PREV_RUN_SOURCE[loc]}: {_PREV_RUN_SOURCE_UNKNOWN[loc]}")
    elif not dirty:
        lines.append(f"- {_PREV_RUN_SOURCE[loc]}: {_PREV_RUN_SOURCE_CLEAN[loc]}")
    elif files:
        lines.append(
            f"- {_PREV_RUN_SOURCE[loc]}: "
            + _PREV_RUN_SOURCE_DIRTY[loc].replace("{count}", str(len(files)))
        )
        lines += [f"  - `{path}`" for path in files]
        lines.append(_PREV_RUN_GUIDE[loc])
    else:
        # Dirty is still dirty with an empty list (the paths were capped away, or the row
        # predates the column). Say that rather than invent file names for it.
        lines.append(f"- {_PREV_RUN_SOURCE[loc]}: {_PREV_RUN_SOURCE_DIRTY_NO_LIST[loc]}")
    lines.append("")
    return "\n".join(lines)


def build_rejection_section(history: list[dict], last: Optional[str]) -> str:
    """Port of buildRejectionSection (useFlowGateToken.ts).

    history items: {rejected_at, reason}. The final history entry duplicates
    `last`, so only the PRIOR entries are listed as context.
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


def _rejection_history(doc: dict) -> list:
    history = doc.get("rejection_history") or []
    if isinstance(history, str):
        try:
            history = json.loads(history) or []
        except Exception:  # noqa: BLE001 — a malformed column must not block the rework
            history = []
    return history


def compose_rework_mention(base: str, doc: dict, reject_reason: Optional[str], *, handoff: str = "") -> str:
    """The one rework composition: rejection section, then the previous-run handoff, then ``base``.

    ``build_rework_mention`` here and the route's
    ``ai_invoke_routes.build_rework_mention`` each carried their own copy of this assembly. Both now
    call this. Empty leading chunks are dropped, and when both are empty ``base`` comes back as is.
    Both chunks already end in a blank line, so joining on "\n" leaves exactly one blank line
    between every pair.
    """
    section = build_rejection_section(_rejection_history(doc), reject_reason or doc.get("rejection_reason"))
    leading = [chunk for chunk in (section, handoff) if chunk]
    return "\n".join(leading) + "\n" + base if leading else base


def build_rework_mention(
    *,
    doc_id: str,
    group_id: str,
    project_id: str,
    scratch_dir: str,
    raw_token: str,
    api_base_url: str,
    locale: str = "ko",
    reject_reason: Optional[str] = None,
    continuous: bool = False,
    continuous_review_mode: bool = False,
) -> Optional[str]:
    """The [AI 수정] prompt: rejection history section + the standard edit mention.

    0414 L0008 §2.6 pulled this out of ai_invoke_routes so the human [AI 수정] button and the
    engine's automatic rework hop share ONE assembly. Kept in the route, the engine would
    have grown a second copy, and two copies of a prompt always drift.

    The edit mention it wraps is the ordinary one, which matters: it reads
    doc_review_status == "rejected" and therefore offers edit_reason="rejected", and THAT is
    what makes the submission fire the rejected -> revised transition. Without that
    transition revision_no still climbs but the approval transition stays blocked. The same
    mention also carries the current revision's review as a "Review feedback" section — and
    right after an automatic rejection the revision has not moved, so it is always there.

    ``reject_reason`` is the live, possibly-unsaved reason from the human reject dialog; the
    engine passes none and the stored ``rejection_reason`` (which the auto-rejection just
    wrote) is used instead.
    """
    from modules.flow_gate.api import token_routes as _token_routes
    from modules.flow_gate.db import documents as db_documents

    doc = db_documents.get_by_id(doc_id) or {}
    base = _token_routes._build_mention_for_token(
        doc_ref=doc_id,
        group_id=group_id,
        project_id=project_id,
        scratch_dir=scratch_dir,
        raw_token=raw_token,
        action_scope="edit",
        locale=locale,
        continuous=continuous,
        continuous_review_mode=continuous_review_mode,
        api_base_url=api_base_url,
    )
    if not base:
        return None
    mention = compose_rework_mention(base, doc, reject_reason)
    # re-applies the same item's WorkPlan pre-instruction — the
    # reviewer never sees this (its own prompt is a different assembly, review_mention.py),
    # only the worker being asked to fix the document does. May raise
    # PreInstructionAttachmentError (fail-closed, the earlier implementation); the caller decides cleanup.
    pre = _resolve_rework_pre_instruction(doc_id)
    if pre:
        mention = prepend_pre_instruction_section(
            mention, text=pre.get("text"), attachment=pre.get("attachment"), locale=locale,
        )
    return mention


def _resolve_rework_pre_instruction(doc_id: str) -> Optional[dict]:
    """The pre-instruction stored on the sequence slot ``doc_id`` itself occupies.

    A rework hop's ``doc_id`` is always a result document already sitting in a slot
    (``result_doc_id``), never the sequence root — so the reverse lookup used by note
    resolution's fold is unnecessary here: the slot IS the row to read.
    """
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import work_plan_attachment_service

    item = db_wfseq.get_item_by_result_doc_id(doc_id)
    return work_plan_attachment_service.resolve_pre_instruction(item)


def issue_rework_request(
    *,
    doc_id: str,
    issued_to: str,
    api_base_url: str,
    locale: str = "ko",
    ai_run_id: Optional[str] = None,
    reject_reason: Optional[str] = None,
) -> dict:
    """Mint an edit token bound to doc_id and build its rework mention.

    The issue_builder contract start_run expects, so the engine's rework hop launches through
    exactly the same path every other scope does. ``ai_run_id`` is not optional in practice:
    a token that cannot name its own run is refused by the group lease that run is holding
    , which would make the rework hop fail against itself.

    Raises
    ------
    LookupError  "doc_not_found:{doc_id}"
    ValueError   "group_not_found:{doc_id}"
    """
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services import token_service

    doc = db_documents.get_by_id(doc_id)
    if doc is None:
        raise LookupError(f"doc_not_found:{doc_id}")
    group_id: Optional[str] = doc.get("group_id")
    if not group_id:
        raise ValueError(f"group_not_found:{doc_id}")
    project_id: str = doc.get("project_id") or ""

    issued = token_service.issue(
        project=project_id,
        group_id=group_id,
        action_scope="edit",
        doc_ref=doc_id,
        issued_to=issued_to,
        ai_run_id=ai_run_id,
    )
    try:
        mention = build_rework_mention(
            doc_id=doc_id,
            group_id=group_id,
            project_id=project_id,
            scratch_dir=issued["scratch_dir"],
            raw_token=issued["raw_token"],
            api_base_url=api_base_url,
            locale=locale,
            reject_reason=reject_reason,
        )
    except Exception:
        # fail-closed means the token this call already minted must not be
        # left behind unrevoked — the caller (admission.start_run) only learns of this
        # failure through the exception, never through a token id it could clean up itself.
        try:
            token_service.revoke(issued["token_id"], reason="ai_invoke_pre_instruction_attachment_invalid")
        except Exception:  # noqa: BLE001 — revocation must not mask the original failure
            pass
        raise
    return {
        "raw_token": issued["raw_token"],
        "token_id": issued["token_id"],
        "scratch_dir": issued["scratch_dir"],
        "mention": mention or "",
    }


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


# the WorkPlan pre-instruction section header — deliberately its own
# constant, not _MM_SECTION_HEADER, so this section can never merge with the step note
# (a different provenance per the design) into one indistinguishable block.
_PRE_INSTRUCTION_SECTION_HEADER = {
    "ko": "WorkPlan 사전지시",
    "en": "WorkPlan pre-instruction",
    "ja": "WorkPlan 事前指示",
}

_PRE_INSTRUCTION_ATTACHMENT_LINES = {
    "ko": (
        "이 단계에는 WorkPlan 사전지시 첨부 파일이 1개 있습니다.\n"
        "- 원래 파일명: {original}\n"
        "- 읽어야 할 대상: 문서 {doc_id}의 첨부 \"{filename}\""
        " (도움말 attachments/read의 읽기 경로로 읽으세요)"
    ),
    "en": (
        "This step has one WorkPlan pre-instruction attachment.\n"
        "- Original filename: {original}\n"
        "- Read target: attachment \"{filename}\" on document {doc_id}"
        " (use the read path from the attachments/read help leaf)"
    ),
    "ja": (
        "このステップには WorkPlan 事前指示の添付ファイルが1つあります。\n"
        "- 元のファイル名: {original}\n"
        "- 読み取り対象: 文書 {doc_id} の添付 \"{filename}\""
        " (attachments/read のヘルプ項目の読み取り経路を使用してください)"
    ),
}


def prepend_pre_instruction_section(
    mention_text: str,
    *,
    text: Optional[str],
    attachment: Optional[dict],
    locale: Optional[str],
) -> str:
    """Prepend the WorkPlan pre-instruction as its own section.

    ``attachment`` must already be a validated reference — callers resolve it through
    :func:`modules.flow_gate.services.work_plan_attachment_service.resolve_pre_instruction`,
    which raises rather than returning an invalid one. This function only formats.
    """
    loc = _locale(locale)
    parts: list[str] = []
    if text and text.strip():
        parts.append(text.strip())
    if attachment:
        parts.append(_PRE_INSTRUCTION_ATTACHMENT_LINES[loc].format(
            original=attachment.get("original_filename"),
            doc_id=attachment.get("doc_id"),
            filename=attachment.get("filename"),
        ))
    body = "\n\n".join(parts)
    if not body:
        return mention_text
    section = f"## {_PRE_INSTRUCTION_SECTION_HEADER[loc]}\n---\n{body}"
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
