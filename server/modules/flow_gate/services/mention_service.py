"""R018 enhancement: prompt service with richer worker context.

After the R018 §2-1 enhancement, prompts are generated as ordered sections.

Section layout (D027 §6, P005 §3). R0001/T0004 hoisted the Q guide from the
bottom to right below Document information so workers actually read it, and the
"don't offer choices" guard now lives inside it. B0001 (group 0063) / NR0003
further aligned the guide with the real mechanism — questions are document-bound
query data (POST /q/{doc_id}/questions), NOT a Q document — and embedded a
ready-to-send POST so there is a zero-friction alternative to offering choices.
The guard is also repeated once at the very bottom (recency) so a long prompt
does not bury it:
  1. Document information
  2. Clarification guide      (query-registration POST address + no-choices guard; the
                               ready-to-send body example moved to the `question` help
                               item — group 0372 D-0003 §3-2 "kept, abridged")
  3. Help                     (central help-index block, L-0005 §2-10 — once per worker
                               mention, right below the identity + guide blocks)
  4. Instruction to include the next document header
  5. Reference documents      (one line each for head + selected: {slash-path}: GET {url})
  6. Recent documents in the group  — REMOVED (group 0372 D-0003 §3-2 "dropped"); the
                                       `group_documents` help item covers it
  7. Artifact registration    (edit: complete POST example, unchanged; new: address +
                                a pointer to the `submit` help item — D-0003 §3-2/§3-5)
  8. Scratch directory        (token-owned path for doc_path files)
  9. doc_type guide           — REMOVED (D-0003 §3-2 "dropped"); it was already a one-line
                               help URL and is absorbed into the always-visible
                               `doc_type` entry of the help index
 10. Reminder                 (the no-choices guard repeated for recency)

Insert a placeholder into next_type based on the head state:
  - no head            → <Sequence undecided>
  - head in_progress   → <In progress: {head_type}>
  - head pending       → {head_type} (normal)
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from modules.flow_gate import template_provision
from modules.flow_gate.db.document_type_labels import get_type_name
from modules.flow_gate.settings import source_mode_service
from modules.flow_gate.services import test_command_service
from modules.flow_gate.services import tool_registry
from modules.flow_gate.services import tr_scope_service
from modules.flow_gate.services import conversation_query_service
from modules.flow_gate.services.conversation_turn_service import turn_wire
from modules.flow_gate.db import conversation_turns as turn_store
from modules.flow_gate.documents import document_service
# AUTO_REPORT_MAP is imported lazily at its use site to avoid a service import cycle.

logger = logging.getLogger(__name__)


# Investigation-only document types — they produce findings/reports, never code.
# See R0013 (flowgate.default.0013.0001-R): workers asked to produce an
# investigation document were implementing code on their own. The mention must
# state the scope boundary explicitly; implementation belongs in a T (work-instruction) doc.
_INVESTIGATION_ONLY_TYPES = {"N", "NR"}
# The "which step types may mutate source" constant used to live here as a second copy of
# remote_tool_service's, and the two had already drifted (this one lacked TSR). 0349 D0004
# D-2 removes the duplicate: tool_registry.kind_for_step is now the only judge, and both
# this mention and the permission check ask it.


# ── API base path (including CONTEXT) ────────────────────────────────────────

def _api_base(base_url: str) -> str:
    """Remove the trailing slash from base_url."""
    return base_url.rstrip("/")


# ── Dry-run usage hint (R0001 dry-run, group 0050) ──────────────────────────────
# Surfaced inside the POST block so a worker discovers it can validate a submission
# without creating a real document (the accident R0001 fixes). Kept to two lines.
_DRYRUN_HINT = (
    'Dry-run: add "dry_run": true to the body above to validate this submission '
    "(URL/token/fields/permissions) WITHOUT registering anything — nothing is created "
    "and the token is NOT consumed, so a successful dry-run can be followed by the real "
    "submission. Limited to a few attempts per token (HTTP 429 when exhausted)."
)


# ── Section helpers ───────────────────────────────────────────────────────────

def _section(header: str, body: str) -> str:
    """P005 §3-1 format: '## header\\n---\\nbody'."""
    return f"## {header}\n---\n{body}"


def _remote_source_crud_lines(
    base: str,
    raw_token: str,
    step_type: Optional[str],
    *,
    action_scope: str = "new",
    locale: str = "ko",
    kind: Optional[str] = None,
) -> list[str]:
    """The five mention lines that survive the 0349 shrink (P0005 [notes] / D0004 D-4).

    Every request format, field description and JSON example the mention used to carry now
    lives behind GET /help/tools — the mention was growing one block per tool and workers
    were reading the top and skipping the rest (R0001). What stays is exactly what a worker
    that never calls help must still know: call help first, do not touch the disk, which
    tools exist, the token, and where the detail is. All five are built from fixed strings
    plus ``kind``, so no lookup failure can empty the section (D0004 D-6).

    ``kind`` comes from tool_registry — the same judge the permission check uses — so the
    mention can no longer advertise a tool the server would refuse. Pass it directly when
    the caller holds a token rather than a step type (see q_answer_invoke_service); leave it
    None to have the step type judged here.
    """
    if not raw_token:
        return []
    loc = template_provision.normalize_locale(locale)
    if kind is None:
        kind, _reason = tool_registry.kind_for_step(action_scope, step_type)
    names = tool_registry.tool_names(kind)
    if not names:
        # kind=none: sequence-edit / test-run tokens get no tools at all, and there is
        # nothing to advertise. Matches the empty list /help/tools returns them (P0005 normal-3).
        return []
    text = tool_registry.MENTION_LINES[loc]
    return [
        text["first_action"].format(url=f"{base}/help/tools"),
        text["no_disk_edit"],
        f"{text['tools_label']}: " + ", ".join(names),
        f"Authorization: Bearer {raw_token}",
        text["detail"].format(url=f"{base}/help/tools/{{name}}"),
    ]


def _remote_source_crud_section(
    base: str,
    raw_token: str,
    step_type: Optional[str],
    locale: str = "ko",
    *,
    action_scope: str = "new",
) -> str:
    """``_remote_source_crud_lines`` as a mention section.

    The header stays "Remote project source CRUD": changing the title at the same time as
    the body would mix two causes into every regression failure (P0005 [notes]).

    ``locale`` stays positional-or-keyword: the chat mention pins it positionally
    (invoke_mention_service._chat_lookup_sections passes "en", because that mention is
    English-only by design) while every other caller passes it by keyword alongside
    ``action_scope``. Both spellings must keep working — 0349 introduced ``action_scope``
    and 0355 introduced the locale argument on separate branches, and only the merge sees
    both call shapes at once.
    """
    lines = _remote_source_crud_lines(
        base, raw_token, step_type, action_scope=action_scope, locale=locale
    )
    if not lines:
        return ""
    return _section("Remote project source CRUD", "\n".join(lines))


def _include_remote_source_crud(project: str) -> bool:
    try:
        return source_mode_service.include_remote_api_section(project)
    except Exception:
        logger.warning("source mode resolution failed; falling back to remote mode", exc_info=True)
        return True


# ── CH reference documents: inline turns instead of a bare GET link ──────
# T0005 (rev3) work item 2. A '## Reference documents' entry naming a CH (chat)
# document used to be the same bare `{doc_id}: GET {url}` line as every other
# type. Fetching that URL returns only frontmatter — a CH document's real
# content lives in the conversation_turns table, not the documents.content
# column (0344), and the dedicated turn route (`GET /conversation/{doc_id}/
# turns`) 403s for every token whose action_scope is not exactly "chat" bound
# to that document. A continuous-hop worker (edit/new/review scope) therefore
# had no way to ever see the chat content a mention pointed it at. The server
# process itself is not scope-bound, so it reads the turns at mention-assembly
# time and inlines them here instead of leaving the worker a link it cannot
# follow. Non-CH references are unaffected — they keep the historical
# single-line GET-link format.
_CH_NO_TURNS_TEXT: dict[str, str] = {
    "ko": "(이 CH 문서에는 아직 턴이 없습니다)",
    "ja": "（このCH文書にはまだターンがありません）",
    "en": "(this CH document has no turns yet)",
}


def _reference_doc_lines(doc_dot_dash_path: str, base: str, locale: str = "ko") -> list[str]:
    """Lines for one '## Reference documents' entry.

    Every non-CH type keeps the single-line `{doc_id}: GET {url}` format
    unchanged. A CH-typed reference instead gets that same link line followed
    by its conversation turns (or a "no turns yet" line), inlined so a worker
    whose token scope cannot open `/conversation/{doc_id}/turns` still sees
    the actual chat content.
    """
    doc_url = f"{base}/document/{doc_dot_dash_path}"
    link_line = f"{doc_dot_dash_path}: GET {doc_url}"
    try:
        doc = document_service.get_document(doc_dot_dash_path)
    except Exception:
        logger.exception("reference doc lookup failed for %s", doc_dot_dash_path)
        return [link_line]
    if doc is None or (doc.get("type_code") or "").upper() != "CH":
        return [link_line]

    try:
        conversation_query_service._ensure_readable_rows(doc_dot_dash_path)
        turns = turn_store.list_turns(doc_dot_dash_path)
    except Exception:
        logger.exception(
            "CH turn inline failed for %s; falling back to GET link", doc_dot_dash_path
        )
        return [link_line]

    lines = [link_line]
    if not turns:
        loc = template_provision.normalize_locale(locale)
        lines.append(_CH_NO_TURNS_TEXT.get(loc, _CH_NO_TURNS_TEXT["ko"]))
        return lines
    for row in turns:
        wire = turn_wire(row)
        speaker = wire.get("display_name") or wire.get("speaker") or "?"
        lines.append(f"--- turn {wire['seq']} [{speaker}] {wire.get('created_at') or ''} ---")
        lines.append(str(wire.get("body") or ""))
    return lines


def _document_lookup_lines(
    base: str,
    raw_token: str,
    *,
    project: str = "",
    doc_id: str = "",
) -> list[str]:
    """Advertise the bounded document reads added by group 0370.

    These endpoints existed but the worker mentions still advertised only the legacy full-body
    GET. Keep one shared block for creation/edit, workflow, review, chat and Q-answer work so a
    worker can discover the efficient route at the point where it is about to read documents.

    Merge note (group 0372 set 3 x group 0370): the document mentions built in THIS module no
    longer inline these lines — `document_access` (help_catalog._content_document_access) serves
    the same endpoints behind the help index, which every mention now teaches. The chat and
    Q-answer mentions are not help-index clients, so invoke_mention_service and
    q_answer_invoke_service still call this builder directly; that is why the line builder
    survives the shrink while its `_document_lookup_section` wrapper does not.
    """
    target = doc_id or "<doc_id>"
    project_filter = f"&project={project}" if project else ""
    return [
        "Read only the document data you need; use the full-document GET only when necessary:",
        f"- Metadata without body: GET {base}/document/{target}/meta",
        f"- Outline without body: GET {base}/document/{target}/outline",
        f"- One section from that outline: GET {base}/document/{target}/section?section_id=<section_id>",
        f"- Relationships without body: GET {base}/document/{target}/relations",
        (
            f"- Search bodies with match locations/context: GET {base}/search/documents/content"
            f"?q=<keyword>{project_filter}&include_matches=true&context_lines=2&hits_per_doc=5"
        ),
        "Section reads accept exactly one of section, section_id, lines, or chars.",
        f"Authorization: Bearer {raw_token}",
    ]


# ── Central help-index block (group 0372 set 3 — L-0005 §2-10) ────────────────
# Every worker mention carries this block exactly once, right below the identity
# header and the unmanned/no-choices guide block. It replaces the per-section
# bodies that moved behind help items: what the worker loses inline, it regains
# through the index this block teaches. Wording is pinned by L-0005 §2-10; the
# section header is the one worker-facing header that localizes (per-language "Help")
# because L-0005 fixed it per locale. `{name}` is a literal placeholder shown to
# the worker, never str.format-expanded.
_HELP_INDEX_HEADER: dict[str, str] = {"ko": "도움말", "ja": "ヘルプ", "en": "Help"}

_HELP_INDEX_TEXT: dict[str, str] = {
    "ko": (
        "첫 행동으로 GET {base}/help 를 호출해 도움말 목차를 받으세요.\n"
        "목차의 항목 중 필요한 것을 GET {base}/help/items/{{name}} 으로 골라 받습니다.\n"
        "한 번에 모두 받으려면 GET {base}/help?detail=true 를 쓰세요.\n"
        "Authorization: Bearer {token}"
    ),
    "ja": (
        "最初の行動として GET {base}/help を呼び出し、ヘルプ目次を取得してください。\n"
        "目次から必要な項目を GET {base}/help/items/{{name}} で選んで取得します。\n"
        "表示可能な全項目を一度に取得するには GET {base}/help?detail=true を使ってください。\n"
        "Authorization: Bearer {token}"
    ),
    "en": (
        "As your first action, call GET {base}/help to receive the help index.\n"
        "Choose what you need from the index with GET {base}/help/items/{{name}}.\n"
        "To receive all visible items at once, use GET {base}/help?detail=true.\n"
        "Authorization: Bearer {token}"
    ),
}


def _help_index_section(base: str, raw_token: str, locale: str = "ko") -> str:
    loc = template_provision.normalize_locale(locale)
    return _section(
        _HELP_INDEX_HEADER[loc],
        _HELP_INDEX_TEXT[loc].format(base=base, token=raw_token),
    )


# ── Clarification / no-choices guide (B0001 group 0063; NR0003) ───────────────
# B0001 recurred because the choice-prohibition guard was body text only AND the
# sanctioned alternative was reached through a 2-hop GET /help/question indirection.
# That friction (no copy-paste POST in the mention, plus "Q document" wording that
# does not match the live mechanism) pushed workers back toward offering console
# choices the remote run can never answer. NR0003 (flowgate.default.0063.0003-NR)
# friction-removal: state the real mechanism — questions are document-bound query
# data (POST /q/{doc_id}/questions), NOT a Q document — and embed a ready-to-send
# POST so the worker has a zero-friction path instead of presenting choices.
#
# anchor_doc_id is the document the worker's token is bound to (the inbox/edit
# spine for build_mention, the review target for build_review_mention). The q
# route requires the URL doc_id to match the token's doc_ref, then re-aims the
# question to the current work-context document via resolve_question_anchor.
# rev2 (B0001 2026-06-15 reject "some ments stay fixed in Korean even when the locale is switched"): rev1 left the
# guide/reminder text hardcoded with embedded Korean (short title, query, …) that ignored
# the worker's display locale, so switching locale to ja/en still leaked Korean. All
# worker-facing prose below now follows the requested locale via
# template_provision.normalize_locale (ko/ja/en) — Korean appears only for locale=ko.
# The critical English keywords (NOT a Q document / Do NOT present choices /
# force-terminated / register a Q / definite answer / next action) are kept in EVERY
# locale so the guard reads and asserts identically regardless of language.

# group 0372 set 3 (D-0003 §3-2 "kept, abridged"): the placeholder question JSON the guide
# used to embed (title/body/options) moved behind the `question` help item — the guide
# keeps the token-specific POST address + credential and points at the item for the
# body format. The help item's example (help_catalog.build_question_content) now also
# demonstrates the `options` array the prescription below tells the worker to use.
_QUESTION_FORMAT_POINTER: dict[str, str] = {
    "ko": "질문 본문 서식과 예시는 도움말 항목에 있습니다: GET {url}",
    "ja": "質問本文の形式と例はヘルプ項目にあります: GET {url}",
    "en": "The question body format and an example are in the help item: GET {url}",
}

# Lead-in (3 lines) / no-choices warning / positive "write a Q" redirect, per locale.
_CLARIFY_TEXT = {
    "ko": {
        "lead": (
            "불명확한 점이 있으면 추측하거나 가정으로 진행하지 마십시오.\n"
            "질문은 해당 문서에 바인딩된 질의 데이터로 등록하십시오 (this is NOT a Q document):\n"
            "질문을 채워 아래의 POST로 전송하십시오."
        ),
        "warn": (
            "⚠️ 사용자에게 선택지나 옵션을 제시하지 마십시오 — Do NOT present choices "
            "(객관식 목록·대화형 선택 프롬프트 금지).\n"
            "당신은 무인(UNMANNED) 시스템의 원격 워커입니다: 그런 프롬프트는 응답을 받지 못하고 "
            "실행이 답변 없이 강제 종료(force-terminated)됩니다."
        ),
        "positive": (
            "✅ 확실한 답(definite answer)을 받으려면 선택지를 제시하지 말고 반드시 "
            "Q(질의)를 등록(register a Q)해야 합니다.\n"
            "옵션 중 선택을 요청하려는 자신을 발견하면 STOP — 그 옵션들을 options 배열에 담아 "
            "Q 1건으로 등록하십시오(사용자는 문서의 [질의 응답] 패널에서 보기를 고르거나 자유 "
            "서술로 답합니다). Q 작성이 이 시스템에서 답을 받는 유일한 방법이며, 항상 선택지 대신 "
            "다음 행동(next action)으로 남기십시오."
        ),
    },
    "ja": {
        "lead": (
            "不明な点があれば推測せず、仮定で進めないでください。\n"
            "質問は当該文書に紐づくクエリデータとして登録してください (this is NOT a Q document):\n"
            "質問を記入し、下記のPOSTで送信してください。"
        ),
        "warn": (
            "⚠️ ユーザーに選択肢やオプションを提示しないでください — Do NOT present choices "
            "(多肢選択リスト・対話的な選択プロンプト禁止)。\n"
            "あなたは無人(UNMANNED)システムのリモートワーカーです: そうしたプロンプトは応答を "
            "得られず、実行は回答なしで強制終了(force-terminated)されます。"
        ),
        "positive": (
            "✅ 確実な回答(definite answer)を得るには、選択肢を提示せず必ず "
            "Q(質問)を登録(register a Q)してください。\n"
            "オプションの選択を求めようとしている自分に気づいたらSTOP — その選択肢をoptions配列に入れた "
            "Q 1件として登録してください(ユーザーは文書の[質疑応答]パネルで選択肢を選ぶか、自由記述で "
            "回答します)。Qの作成がこのシステムで回答を得る唯一の方法であり、常に "
            "選択肢ではなく次の行動(next action)として残してください。"
        ),
    },
    "en": {
        "lead": (
            "If anything is unclear, do NOT guess and do NOT proceed on assumptions.\n"
            "Register your question as document-bound query data (this is NOT a Q document):\n"
            "fill in the question(s) and send them with the POST below."
        ),
        "warn": (
            "⚠️ Do NOT present choices or options to the user (no multiple-choice lists,\n"
            "no interactive selection prompts). You are a remote worker on an UNMANNED\n"
            "system: such prompts get no response and the run is force-terminated with no\n"
            "answer."
        ),
        "positive": (
            "✅ To get a definite answer you MUST register a Q, not offer a choice.\n"
            "If you find yourself about to ask the user to pick between options, STOP and\n"
            "convert it: register ONE Q carrying those options in its options array (the\n"
            "user picks an option or writes freely in the document's Q&A panel). Writing\n"
            "a Q is the ONLY way to get an answer from this system — always leave it as the\n"
            "next action instead of a choice."
        ),
    },
}

# Recency repeat of the no-choices guard for the bottom of a long prompt (NR0003).
# {post} is filled with the POST line at format time.
_REMINDER_TEXT = {
    "ko": (
        "⚠️ 행동하기 전에 다시 읽으십시오 — do NOT present choices: 선택지/옵션을 제시하지 말고 "
        "사용자에게 대화형 질문을 하지 마십시오. 이것은 무인 시스템이므로 원격 실행은 답을 받지 못하고 "
        "강제 종료됩니다. 확신이 서지 않으면, 확실한 답(definite answer)을 받는 유일한 방법은 "
        "Q(질의)를 작성(register a Q)하는 것입니다: {post} 로 등록하십시오 "
        "(위 Clarification guide 참조). 보기가 필요하면 그 옵션들을 options 배열에 담아 Q 1건으로 "
        "등록하십시오. 항상 선택지가 아니라 다음 행동(next action)으로 'Q 작성'을 남기십시오."
    ),
    "ja": (
        "⚠️ 行動する前にもう一度読んでください — do NOT present choices: 選択肢/オプションを提示せず、"
        "ユーザーに対話的な質問をしないでください。これは無人システムなので、リモート実行は回答を得られず "
        "強制終了されます。不明な場合、確実な回答(definite answer)を得る唯一の方法は "
        "Q(質問)を作成(register a Q)することです: {post} で登録してください "
        "(上記 Clarification guide 参照)。選択肢が必要な場合は、その選択肢をoptions配列に入れた "
        "Q 1件として登録してください。常に選択肢ではなく次の行動(next action)として「Qの作成」を "
        "残してください。"
    ),
    "en": (
        "⚠️ Read this again before you act: do NOT present choices/options and do NOT ask "
        "the user an interactive question — this is an unmanned system, so a remote run "
        "gets no answer and is force-terminated. If you are unsure, the ONLY way to get a "
        "definite answer is to write a Q: register it with {post} "
        "(see the Clarification guide above). If you need to offer alternatives, register "
        "ONE Q carrying them in its options array. "
        "Leave 'write a Q' as the next action — never a choice."
    ),
}


# ── Continuous (unmanned) work guide (group 0051 R0001 / NR0003 §2-3, §4-3) ──
# R0001 requires that, in continuous (unmanned) mode, the Q/no-choices guidance is
# REPLACED (not removed) by a delegation/unmanned/no-stop/autonomous block:
#   - all authority delegated to the worker
#   - unmanned operation — no human is watching
#   - do not stop the work mid-chain
#   - when unsure, decide autonomously rather than asking
# This deliberately relaxes the FlowGate Q-gate, which is why the FE warning dialog
# (NR0003 §4-5) makes the user accept "no liability for results; quality depends on how much information is given" before a
# continuous token is minted. The block follows the worker display locale (ko/ja/en),
# keeping the same English keywords (UNMANNED / autonomous / do NOT stop) in every
# locale so the directive reads identically regardless of language. GET-credential
# notes, Artifact registration, and the doc_type guide are kept unchanged — only the
# Clarification guide §2 and the bottom Reminder §9 swap to this block.
_CONTINUOUS_TEXT = {
    "ko": (
        "이 작업은 무인(UNMANNED) 연속 작업 체인의 일부입니다. 사람이 지켜보고 있지 않습니다.\n"
        "\n"
        "- 모든 권한이 당신에게 위임되었습니다 — 진행에 필요한 결정을 직접 내리십시오.\n"
        "- 작업을 중단하지 마십시오(do NOT stop). 한 단계를 마치면 응답에 동봉된 다음 토큰/멘트로 "
        "곧장 다음 단계를 진행하십시오.\n"
        "- 장시간 검증(예: 전체 테스트 스위트)을 백그라운드(background)로 넘기고 완료 알림을 기다리며 "
        "현재 프로세스/턴을 끝내지 마십시오. 이 시스템에서는 그런 백그라운드+알림 대기가 정당한 대기로 "
        "인식되지 않고 무출력 재시도로 오판됩니다. 검증은 현재 프로세스 안에서 동기적으로(synchronously) "
        "끝까지 실행해 결과(성공/실패)를 확인한 뒤에만 다음 단계로 진행하십시오.\n"
        "- 불명확한 점이 있어도 선택지를 제시하거나 질문으로 멈추지 말고, 가진 정보로 가장 합리적인 "
        "판단을 내려 자율적으로(autonomously) 진행하십시오.\n"
        "- 결과의 품질은 제공된 정보량에 의존합니다. 사용자는 이 점을 수락하고 연속 작업을 개시했습니다."
    ),
    "ja": (
        "この作業は無人(UNMANNED)連続作業チェーンの一部です。人は見ていません。\n"
        "\n"
        "- すべての権限があなたに委任されています — 進行に必要な判断は自分で下してください。\n"
        "- 作業を中断しないでください(do NOT stop)。1ステップを終えたら、応答に同梱された次のトークン/"
        "メンションでそのまま次のステップへ進んでください。\n"
        "- 長時間の検証（例: 全体テストスイート）をバックグラウンド(background)に回し、完了通知を待って"
        "現在のプロセス/ターンを終了しないでください。このシステムでは、そうしたバックグラウンド+通知待ちは"
        "正当な待機と認識されず、無出力の再試行と誤判定されます。検証は現在のプロセス内で同期的に"
        "(synchronously)最後まで実行し、結果（成功/失敗）を確認してから次のステップへ進んでください。\n"
        "- 不明な点があっても選択肢を提示したり質問で止まったりせず、手持ちの情報で最も合理的な判断を下し、"
        "自律的に(autonomously)進めてください。\n"
        "- 結果の品質は提供された情報量に依存します。ユーザーはこれを承諾して連続作業を開始しました。"
    ),
    "en": (
        "This task is part of an UNMANNED continuous work chain. No human is watching.\n"
        "\n"
        "- All authority is delegated to you — make the decisions needed to proceed.\n"
        "- Do NOT stop the work. When you finish a step, continue straight to the next one "
        "using the next token/mention enclosed in the response.\n"
        "- Do NOT hand off long-running verification (e.g. the full test suite) to the "
        "background and end the current process/turn while waiting for a completion "
        "notification. This system does not recognize that kind of background-plus-notification "
        "wait as legitimate — it is misjudged as a silent (no-output) retry. Run verification to "
        "completion synchronously within the current process and confirm the result (pass/fail) "
        "before proceeding to the next step.\n"
        "- If something is unclear, do NOT present choices or halt with a question; make the "
        "most reasonable call with the information you have and proceed autonomously.\n"
        "- Output quality depends on the amount of information provided. The user accepted "
        "this and started the continuous run."
    ),
}


# AI review mode (group 0086 R0001 — TR0004 rework rev4): a continuous run the user launched
# with AI review mode ON. Review mode is NOT "go" yet — it is the PRE-FLIGHT Q-registration
# phase. Before the unmanned auto-run starts, the worker reads everything and registers any
# clarifying questions (Q); it does NOT produce the next document and does NOT advance the
# chain. The "create the next document (action:new)" guidance is therefore REMOVED in this
# phase and replaced by Q-registration guidance (reviewer feedback: "review mode is not go
# yet, it is pre-flight Q-registration time; drop the new-document guidance and show Q guidance").
#
# No-Q case (reviewer asked for ideas): if the worker has nothing to ask, it must still NOT
# auto-proceed — instead it registers a single "review done, nothing blocking, asking to confirm"
# acknowledgement Q (a review summary + plan). Either way the review phase ends paused on a
# human go-gate: the human reads the Qs / the ack and gives the explicit go (turns review
# mode off → the non-review auto-run takes over). This keeps review mode from ever being a
# silent no-op and keeps the human checkpoint that review mode exists to provide.
_CONTINUOUS_REVIEW_TEXT = {
    "ko": (
        "이 작업은 [AI 검토 모드]로 개시된 무인(UNMANNED) 연속 작업 체인의 **사전 검토 단계**입니다.\n"
        "아직 본 작업(go)을 실행할 때가 아니라, 무인 자동 실행 전에 **질의(Q)를 미리 등록**하는 시간입니다.\n"
        "\n"
        "- 이 단계에서는 다음 산출물을 **만들지 마십시오** — 새 문서를 생성·등록하지 않습니다(작업 지시·리포트 작성 금지).\n"
        "- 먼저 참조 문서(요건·이전 단계 산출물·결정된 시퀀스)를 끝까지 읽고, 무인으로 진행하기 전에 사람이 "
        "풀어줘야 할 의문·모호함·결함을 찾으십시오.\n"
        "- 막는 의문이 있으면 추측하지 말고 q 엔드포인트(POST .../q/<문서>/questions)로 **Q(질의)를 등록**하십시오. "
        "선택지를 제시하지 말고, 보기가 필요하면 `options`를 가진 Q 1건으로 등록하십시오. "
        "체인은 사람이 답할 때까지 멈춰 기다립니다.\n"
        "- 막는 의문이 **하나도 없더라도** 임의로 진행하지 말고, 같은 q 엔드포인트로 **'검토 완료 — 막는 의문 없음 — "
        "이대로 진행해도 되는지 확인 요청'** Q를 한 건 등록하십시오(검토 요약과 진행 계획을 본문에 담아). 그러면 "
        "사람이 검토 결과를 보고 **명시적으로 go**(검토 모드 해제 후 진행)를 줄 수 있습니다.\n"
        "- 어느 경우든 검토 단계는 **사람의 go가 있을 때까지 다음 단계로 넘어가지 않습니다.**"
    ),
    "ja": (
        "この作業は[AIレビューモード]で開始された無人(UNMANNED)連続作業チェーンの**事前レビュー段階**です。\n"
        "まだ本作業(go)を実行する時ではなく、無人自動実行の前に**質問(Q)を事前登録**する時間です。\n"
        "\n"
        "- この段階では次の成果物を**作成しないでください** — 新規文書を生成・登録しません(作業指示・レポート作成禁止)。\n"
        "- まず参照文書(要件・前ステップの成果物・決定済みシーケンス)を最後まで読み、無人で進める前に人が"
        "解消すべき疑問・曖昧さ・欠陥を洗い出してください。\n"
        "- 行き詰まる疑問があれば推測せず、qエンドポイント(POST .../q/<文書>/questions)で**Q(質問)を登録**してください。"
        "選択肢を提示せず、選択肢が必要な場合は`options`を持つQ 1件として登録してください。"
        "チェーンは人が答えるまで停止して待機します。\n"
        "- 行き詰まる疑問が**一つも無くても**勝手に進めず、同じqエンドポイントで**「レビュー完了 — 阻む疑問なし — "
        "このまま進めてよいか確認依頼」**のQを1件登録してください(レビュー要約と進行計画を本文に含めて)。そうすれば"
        "人がレビュー結果を見て**明示的にgo**(レビューモード解除後に進行)を出せます。\n"
        "- いずれの場合もレビュー段階は**人のgoがあるまで次のステップへ進みません。**"
    ),
    "en": (
        "This task is the PRE-FLIGHT REVIEW phase of an UNMANNED continuous work chain the "
        "user launched with [AI review mode] ON.\n"
        "This is NOT 'go' yet — it is the time to register clarifying questions (Q) BEFORE the "
        "unmanned auto-run starts.\n"
        "\n"
        "- Do NOT produce the next deliverable in this phase — do not create or register a new "
        "document (no work instruction, no report).\n"
        "- First read the reference documents (requirements, previous steps' output, the decided "
        "sequence) end to end, and surface any ambiguity/defect a human should resolve before "
        "the run proceeds unmanned.\n"
        "- If a blocking question remains, do NOT guess — register a Q at the q endpoint "
        "(POST .../q/<doc>/questions). Do not present choices; if alternatives are needed, "
        "register ONE Q carrying them in its `options`. "
        "The chain idles until the human answers.\n"
        "- Even if you have NO blocking question, do NOT proceed on your own: register a single "
        "'review complete — no blockers — requesting confirmation to proceed' Q at the same q "
        "endpoint (include a review summary and your plan in the body). The human then reads "
        "the review and gives the explicit go (turns review mode off → the run proceeds).\n"
        "- In every case the review phase does NOT move to the next step until the human gives "
        "the go."
    ),
}


def _continuous_guide_body(locale: str = "ko", review_mode: bool = False) -> str:
    """Continuous-mode replacement for the Clarification guide (R0001 / NR0003 §4-3).

    ``review_mode`` (group 0086 R0001, AI review mode): in review mode this returns the
    pre-flight Q-registration block (no "create the next document" guidance) — the worker
    registers Qs and the chain waits for the human go. ``build_mention`` additionally embeds
    a ready-to-send Q POST via ``_continuous_review_guide_body``; this text-only form is for
    the workflow-decision mention and the bottom Reminder, where no concrete POST is embedded.
    """
    loc = template_provision.normalize_locale(locale)
    if review_mode:
        return _CONTINUOUS_REVIEW_TEXT[loc]
    return _CONTINUOUS_TEXT[loc]


# Short ko/ja/en line repeated at the very bottom of a review-phase mention (recency).
_CONTINUOUS_REVIEW_REMINDER = {
    "ko": (
        "⚠️ 검토 단계입니다 — 새 문서를 만들지 마십시오. 막는 의문은 Q로 등록하고(POST {post}), "
        "막는 의문이 없으면 '검토 완료·진행 확인' Q를 남긴 뒤 사람의 go를 기다리십시오."
    ),
    "ja": (
        "⚠️ レビュー段階です — 新規文書を作成しないでください。阻む疑問はQで登録し(POST {post})、"
        "阻む疑問が無ければ「レビュー完了・進行確認」のQを残して人のgoを待ってください。"
    ),
    "en": (
        "⚠️ Review phase — do NOT create a new document. Register any blocking question as a Q "
        "(POST {post}); if you have none, leave a 'review complete · confirm to proceed' Q and "
        "wait for the human go."
    ),
}


def _continuous_review_guide_body(
    base: str, anchor_doc_id: str, raw_token: str, locale: str = "ko"
) -> str:
    """Pre-flight review block + the Q-registration POST address (group 0086 rev4).

    Review mode replaces the action:new "Artifact registration" guidance with Q registration.
    The POST address is anchored at the worker's bound document and carries the worker
    token; the body format/example lives behind the `question` help item (group 0372
    set 3 — D-0003 §3-2 "move the question-registration example into the help").
    """
    loc = template_provision.normalize_locale(locale)
    pointer = _QUESTION_FORMAT_POINTER[loc].format(url=f"{base}/help/items/question")
    return (
        f"{_CONTINUOUS_REVIEW_TEXT[loc]}\n"
        "\n"
        f"POST {base}/q/{anchor_doc_id}/questions\n"
        f"Authorization: Bearer {raw_token}\n"
        f"{pointer}"
    )


def _continuous_review_reminder(base: str, anchor_doc_id: str, locale: str = "ko") -> str:
    loc = template_provision.normalize_locale(locale)
    post = f"{base}/q/{anchor_doc_id}/questions"
    return _CONTINUOUS_REVIEW_REMINDER[loc].format(post=post)


def _clarification_guide_body(
    base: str, anchor_doc_id: str, raw_token: str, locale: str = "ko"
) -> str:
    loc = template_provision.normalize_locale(locale)
    txt = _CLARIFY_TEXT[loc]
    pointer = _QUESTION_FORMAT_POINTER[loc].format(url=f"{base}/help/items/question")
    return (
        f"{txt['lead']}\n"
        "\n"
        f"POST {base}/q/{anchor_doc_id}/questions\n"
        f"Authorization: Bearer {raw_token}\n"
        f"{pointer}\n"
        "\n"
        f"{txt['warn']}\n"
        "\n"
        f"{txt['positive']}"
    )


def _no_choices_reminder(base: str, anchor_doc_id: str, locale: str = "ko") -> str:
    """Recency repeat of the no-choices guard for the bottom of a long prompt (NR0003)."""
    loc = template_provision.normalize_locale(locale)
    post = f"POST {base}/q/{anchor_doc_id}/questions"
    return _REMINDER_TEXT[loc].format(post=post)


# Other worker-facing strings that used to be Korean-fixed regardless of locale
# (B0001 rev2). The "work instruction" doc-type gloss in the investigation-scope
# guard and the rejection_response placeholder in the artifact POST now follow
# the worker locale too.
_WORK_INSTRUCTION_LABEL = {
    "ko": "작업지시 / work instruction",
    "ja": "作業指示 / work instruction",
    "en": "work instruction",
}

_REJECTION_RESPONSE_PLACEHOLDER = {
    "ko": "<반려 사유에 대한 대응 내용: 각 반려 사유를 어떻게 반영했는지 기술>",
    "ja": "<却下理由への対応内容: 各却下理由をどのように反映したか記述>",
    "en": "<response to each rejection reason: describe how each one was addressed>",
}


# ── Authoring guide / submission pointers (group 0372 set 3 — D-0003 §3-2) ───────────
# The full "how to write it" content (TS/N/T grammar, the POST body format+example, the
# changed-files section format) now lives behind help items. The mention keeps only what
# D-0003 §3-1 says must stay: values unique to this token, and facts that break the run
# if unread. _ts_authoring_section / _nt_authoring_section below still build the FULL
# body, unchanged — they remain the sole content the `authoring_guide` help item serves
# (help_catalog._authoring_guide_body imports them); only the mention stops inlining it.
_AUTHORING_GUIDE_POINTER_TEXT: dict[str, str] = {
    "ko": "이 문서 타입을 작성하는 방법은 도움말 항목에 있습니다: GET {url}",
    "ja": "この文書タイプの作成方法はヘルプ項目にあります: GET {url}",
    "en": "How to write this document type is in the help item: GET {url}",
}


def _authoring_guide_pointer_section(header: str, type_code: str, locale: str, base: str) -> str:
    """One-line pointer replacing the full TS/N/T authoring body (D-0003 §3-2 "dropped")."""
    loc = template_provision.normalize_locale(locale)
    url = f"{base}/help/items/authoring_guide/{type_code}"
    return _section(header, _AUTHORING_GUIDE_POINTER_TEXT[loc].format(url=url))


_PREDECESSOR_IDENTITY_WARNING_TEXT: dict[str, str] = {
    "ko": (
        "이 정보는 앞 문서(참고용)이며 지금 작성할 문서의 신원이 아닙니다. "
        "실제로 제출할 문서 타입은 아래 next_type을 따르십시오."
    ),
    "ja": (
        "この情報は前の文書（参照用）のものであり、現在作成する文書の識別情報ではありません。"
        "実際に提出する文書タイプは、下の next_type に従ってください。"
    ),
    "en": (
        "This information identifies the preceding document for reference; it is not the "
        "identity of the document you are writing now. Follow next_type below for the "
        "document type to submit."
    ),
}


_SUBMIT_POINTER_TEXT: dict[str, str] = {
    "ko": "요청 서식과 예시, dry-run 사용법은 도움말 항목에 있습니다: GET {url}",
    "ja": "リクエストの書式と例、dry-runの使い方はヘルプ項目にあります: GET {url}",
    "en": "The request format, an example, and how to dry-run are in the help item: GET {url}",
}

# Same pointer for the two submissions that have no dry-run (workflow decide /
# sequence edit) — naming dry-run there would promise a switch those routes reject.
_SUBMIT_BODY_POINTER_TEXT: dict[str, str] = {
    "ko": "요청 본문 서식과 예시는 도움말 항목에 있습니다: GET {url}",
    "ja": "リクエスト本文の形式と例はヘルプ項目にあります: GET {url}",
    "en": "The request body format and an example are in the help item: GET {url}",
}

_SEQUENCE_EDIT_METADATA_COPY: dict[str, dict[str, str]] = {
    "ko": {
        "json_intro": "아래 JSON 배열은 PATCH items에 그대로 넣을 수 있는 대기 행입니다.",
        "rules": (
            "손대지 않은 지시/일반 행은 받은 note/source_doc_id/source_revision_no/provider_id/provider_display_name을 글자 그대로 되돌리십시오. 타입을 바꾸거나 새로 넣은 행은 다섯 값을 비우고, 지우는 행은 보내지 마십시오. "
            "provider_id·provider_display_name 키를 통째로 빼고 보내면 서버가 저장돼 있던 공급자를 그대로 지킵니다. 공급자를 정말로 비우려면 provider_id를 null로 명시해 보내십시오. "
            "NR/TR/TSR 레포트 행은 보내지 마십시오. 서버가 바로 앞 지시 행의 값을 자동으로 이어 붙이므로 되돌릴 필요가 없습니다."
        ),
    },
    "ja": {
        "json_intro": "次の JSON 配列は PATCH の items にそのまま入れられる保留行です。",
        "rules": (
            "変更しない指示行と通常行は受け取った note/source_doc_id/source_revision_no/"
            "provider_id/provider_display_name を文字どおり返してください。タイプを変えた行と "
            "新規行では5値を空にし、削除行は送らないでください。provider_id・"
            "provider_display_name のキーごと省いて送ると、サーバーは保存済みの供給者をその "
            "まま保持します。供給者を本当に空にするには provider_id を null と明示して送って "
            "ください。NR/TR/TSR レポート行は送らないでください。直前の指示行の値はサーバーが "
            "自動的に引き継ぐため、返す必要はありません。"
        ),
    },
    "en": {
        "json_intro": "The JSON array below can be returned unchanged as PATCH items.",
        "rules": (
            "Return note/source_doc_id/source_revision_no/provider_id/provider_display_name "
            "byte-for-byte for untouched instruction and ordinary rows. Clear all five for "
            "retyped or newly inserted rows, and omit deleted rows. Omitting the provider_id "
            "and provider_display_name keys entirely keeps the provider already stored on that "
            "row; to genuinely empty it, send provider_id as an explicit null. Omit NR/TR/TSR "
            "report rows. The server carries every one of these values forward from the "
            "preceding instruction row, so report-row values do not need to be returned."
        ),
    },
}


# 0393 T0005 §2-7: the review submission's file form. Kept to one sentence so the
# "address + pointer" shape of the section (0372 set 3) is preserved.
_REVIEW_FILE_SUBMIT_TEXT: dict[str, str] = {
    "ko": (
        "판정을 파일로 낼 수도 있습니다: 이 토큰의 스크래치 디렉터리 안에 "
        "verdict/findings/comment 를 담은 JSON 파일을 만들고, content 대신 doc_path 에 "
        "그 절대 경로를 보내십시오."
    ),
    "ja": (
        "判定をファイルで提出することもできます: このトークンのスクラッチディレクトリ内に "
        "verdict/findings/comment を含む JSON ファイルを作成し、content の代わりに doc_path に "
        "その絶対パスを送ってください。"
    ),
    "en": (
        "The verdict may also be submitted as a file: write a JSON file holding "
        "verdict/findings/comment inside this token's scratch directory and send its "
        "absolute path as doc_path instead of content."
    ),
}

_CHANGED_FILES_REQUIRED_TEXT: dict[str, str] = {
    "ko": "제출 본문에는 변경 파일 절이 반드시 있어야 합니다 — 서식은 GET {url} 에 있습니다.",
    "ja": "提出本文には変更ファイル節が必須です — 書式は GET {url} にあります。",
    "en": "The submitted content must include a changed-files section — its format is at GET {url}.",
}


# ── 0405 P0004 [mention body] — work-plan (WP) proposal scope section ────────────
# When the next action is a work plan, this is the ONLY channel carrying the scope a human
# picked in the proposal dialog to the worker, exactly as P0004's "what this mention must guarantee" requires.
#   - It appears only when the head type is WP and a work_plan_scope was given; every
#     other mention is unchanged down to the byte.
#   - Its position is right after '## Instruction to include next document header' and
#     right before '## Document template' — the worker reads "what" then "in what format".
#   - When both arrays are empty the section is kept and the line reads "none". If the whole
#     section disappeared, "there is no scope" could not be told from "no scope section arrived".
#   - 0405 T0011 rev1 (rejected: "steps to delegate??? why is this even here"): the field
#     letting a human pick steps was removed, so this section writes no such line either —
#     step allocation is always the authoring worker's job, as the tail line below states.
_WORK_PLAN_SCOPE_HEAD_TYPE = "WP"

# What P0004's [DEFERRED] row handed to this instruction: P0004 fixed the ko copy, and the
# en and ja wordings are decided here.
_WORK_PLAN_SCOPE_COPY: dict[str, dict[str, str]] = {
    "ko": {
        "header": "작업계획 맡길 범위",
        "lead": "아래 범위는 사람이 화면에서 고른 것입니다. 이 범위대로 작업계획을 작성하십시오.",
        "quantities": "장수를 셀 타입",
        # flowgate.default.0423 T0005 item 8: fills the gap where the §8 tail wording
        # tells the worker to prefer a supplied workflow_type_counts value, without the
        # mention ever showing that value.
        "type_counts": "workflow_type_counts 제시값",
        # 0416 TR0005 (rejected: "where did the run provider go?"): the run provider picked on
        # screen — a different value from the multi-select candidates (providers). rev2 (review
        # finding 2): it is NOT the value to copy into the plan's defaults.provider_id. Doing so
        # would silently assign every created step to that provider — step assignment belongs to
        # the post-creation editor. This line only tells the worker "the provider running this
        # mention right now", and default_provider_rule below states that to the worker directly.
        "default_provider": "실행 프로바이더",
        "providers": "후보 공급자",
        # 0416 T0004 (B0001 "so there is no way to pass any note to the planner?"): the planner
        # note typed on screen is written as its own labelled line. It is the same value that
        # must be preserved verbatim as the plan's defaults.note (work_plan_service.py's rule).
        "note": "전달 멘트",
        "none": "(없음)",
        "tail": (
            "선택된 타입은 수량을 결정해도 되는 범위이지 수량 1이라는 뜻이 아닙니다. 먼저 Reference documents의 부모 R/B와 선택 문서 본문을 읽고, 도움말의 group_documents 및 제공된 워크플로 시퀀스를 확인하십시오. 명시된 산출물 수와 독립 작업 단위를 타입별 수량으로 환산하고, 서버가 제시한 workflow_type_counts 값이 있으면 이를 우선 근거로 사용하십시오. 사용하지 않는 타입은 counted_types와 quantities에 남겨 count 0으로 표시하고 steps에는 펼치지 마십시오. 근거가 없는 수량을 1로 추측하지 말고 0으로 두며, 수량 근거를 각 단계 note 또는 제출 요약에서 짧게 밝히십시오. 후보에 없는 공급자를 steps[].provider_id 에 "
            "적지 마십시오.\n"
            "단계 배분은 당신에게 맡깁니다 — 위 타입과 수량대로 단계를 펼치십시오."
        ),
        "tail_no_providers": (
            "선택된 타입은 수량을 결정해도 되는 범위이지 수량 1이라는 뜻이 아닙니다. 먼저 Reference documents의 부모 R/B와 선택 문서 본문을 읽고, 도움말의 group_documents 및 제공된 워크플로 시퀀스를 확인하십시오. 명시된 산출물 수와 독립 작업 단위를 타입별 수량으로 환산하고, 서버가 제시한 workflow_type_counts 값이 있으면 이를 우선 근거로 사용하십시오. 사용하지 않는 타입은 counted_types와 quantities에 남겨 count 0으로 표시하고 steps에는 펼치지 마십시오. 근거가 없는 수량을 1로 추측하지 말고 0으로 두며, 수량 근거를 각 단계 note 또는 제출 요약에서 짧게 밝히십시오. 이 프로젝트에는 등록된 AI 공급자가 없으므로 "
            "steps[].provider_id 는 비워 두십시오.\n"
            "단계 배분은 당신에게 맡깁니다 — 위 타입과 수량대로 단계를 펼치십시오."
        ),
        # 0416 TR0005 rev2 (review finding 4): the run provider is independent of the candidate
        # multi-select, so a provider outside the candidates can arrive. If the section carrying
        # it also held only the tail's "do not write a non-candidate provider into
        # steps[].provider_id", the worker would read two conflicting orders in one section with
        # no way to tell which wins. One line pinning down what each value is for removes that.
        "default_provider_rule": (
            "'실행 프로바이더'는 이 멘트를 받아 지금 실행 중인 공급자이며 단계 배정 후보가 "
            "아닙니다 — steps[].provider_id 와 defaults.provider_id 에는 위 '후보 공급자'에 "
            "있는 값만 적으십시오."
        ),
        # 0416 TR0005 rev2 (review finding 3): writing the value down without saying where to
        # preserve it gives no guarantee the note survives into the final plan on the AI path.
        # T0004 completion criterion 2 ("an AI-proposed plan must also preserve the input in
        # defaults.note") reaches the worker through this one line and no other.
        "note_rule": (
            "위 '전달 멘트'는 작업계획을 쓸 때 참고할 입력 요구사항입니다. 참조 문서와 함께 "
            "읽고, 모든 단계에 공통으로 붙는 defaults.note 와 각 단계의 steps[].note 를 새로 "
            "작성하십시오. (없음)이면 참고할 전달 멘트가 없다는 뜻이므로, 범위와 참조 문서만으로 "
            "공통 멘트를 작성하십시오."
        ),
    },
    "en": {
        "header": "Work plan scope to delegate",
        "lead": "A person chose the scope below on screen. Write the work plan to this scope.",
        "quantities": "Types to count",
        # flowgate.default.0423 T0005 item 8: fills the gap where the §8 tail wording
        # tells the worker to prefer a supplied workflow_type_counts value, without the
        # mention ever showing that value.
        "type_counts": "Supplied workflow_type_counts",
        # flowgate.default.0416 TR0005 — the provider the person picked for this run,
        # distinct from the candidate multi-select (providers).
        "default_provider": "Execution provider",
        "providers": "Candidate providers",
        "note": "Delivery note",
        "none": "(none)",
        "tail": (
            "Selected types are the scope whose quantities may be decided; they do not imply a quantity of 1. Read the parent R/B and selected documents under Reference documents, then inspect group_documents and any supplied workflow sequence. Convert explicit deliverable counts and independent work units into quantities by type, preferring a supplied workflow_type_counts value when available. Keep unused types in counted_types and quantities with count 0 and do not expand them into steps. Do not guess 1 when there is no basis; leave the count at 0 and briefly state the basis for each non-zero quantity in the step note or submission summary. Do not write a provider outside these "
            "candidates into steps[].provider_id.\n"
            "Laying the steps out is delegated to you — expand them from the types and "
            "quantities above."
        ),
        "tail_no_providers": (
            "Selected types are the scope whose quantities may be decided; they do not imply a quantity of 1. Read the parent R/B and selected documents under Reference documents, then inspect group_documents and any supplied workflow sequence. Convert explicit deliverable counts and independent work units into quantities by type, preferring a supplied workflow_type_counts value when available. Keep unused types in counted_types and quantities with count 0 and do not expand them into steps. Do not guess 1 when there is no basis; leave the count at 0 and briefly state the basis for each non-zero quantity in the step note or submission summary. This project has no registered AI "
            "provider, so leave steps[].provider_id empty.\n"
            "Laying the steps out is delegated to you — expand them from the types and "
            "quantities above."
        ),
        "default_provider_rule": (
            "The Execution provider is the provider running this mention right now, not a "
            "step-assignment candidate — write only values listed under Candidate providers "
            "into steps[].provider_id and defaults.provider_id."
        ),
        "note_rule": (
            "The Delivery note above is an input requirement to consult while writing the work "
            "plan. Read it together with the reference documents and author new text for "
            "defaults.note (the instruction common to every step) and each step's steps[].note. "
            "When it reads (none), there is no delivery note to consult -- author the common "
            "note from the scope and reference documents alone."
        ),
    },
    "ja": {
        "header": "作業計画を任せる範囲",
        "lead": "以下の範囲は人が画面で選んだものです。この範囲どおりに作業計画を作成してください。",
        "quantities": "枚数を数えるタイプ",
        # flowgate.default.0423 T0005 item 8: §8 の tail 文言が workflow_type_counts の
        # 提示値を優先根拠にせよと言いながら、その値自体を示していなかった間隙を埋める。
        "type_counts": "提示された workflow_type_counts",
        # flowgate.default.0416 TR0005 — 画面で選んだ今回実行のプロバイダー。候補複数選択
        # (providers)とは別の値。
        "default_provider": "実行プロバイダー",
        "providers": "候補プロバイダー",
        "note": "伝達メモ",
        "none": "(なし)",
        "tail": (
            "選択されたタイプは数量を決めてもよい範囲であり、数量 1 を意味しません。まず Reference documents の親 R/B と選択文書の本文を読み、group_documents と提示されたワークフローシーケンスを確認してください。明示された成果物数と独立した作業単位をタイプ別数量に換算し、workflow_type_counts の提示値があれば優先根拠として使用してください。使用しないタイプは counted_types と quantities に残して count 0 とし、steps には展開しないでください。根拠がない数量を 1 と推測せず 0 のままにし、0 より大きい各数量の根拠を step note または提出要約に短く記してください。候補にないプロバイダーを "
            "steps[].provider_id に書かないでください。\n"
            "段階の割り当てはあなたに任せます — 上のタイプと数量どおりに展開してください。"
        ),
        "tail_no_providers": (
            "選択されたタイプは数量を決めてもよい範囲であり、数量 1 を意味しません。まず Reference documents の親 R/B と選択文書の本文を読み、group_documents と提示されたワークフローシーケンスを確認してください。明示された成果物数と独立した作業単位をタイプ別数量に換算し、workflow_type_counts の提示値があれば優先根拠として使用してください。使用しないタイプは counted_types と quantities に残して count 0 とし、steps には展開しないでください。根拠がない数量を 1 と推測せず 0 のままにし、0 より大きい各数量の根拠を step note または提出要約に短く記してください。このプロジェクトには登録済みの AI "
            "プロバイダーがないため、steps[].provider_id は空欄にしてください。\n"
            "段階の割り当てはあなたに任せます — 上のタイプと数量どおりに展開してください。"
        ),
        "default_provider_rule": (
            "「実行プロバイダー」はこのメモを受け取って今実行しているプロバイダーであり、"
            "段階割り当ての候補ではありません — steps[].provider_id と defaults.provider_id "
            "には上の「候補プロバイダー」にある値だけを書いてください。"
        ),
        "note_rule": (
            "上の「伝達メモ」は作業計画を書く際に参考にする入力要件です。参照文書と合わせて読み、"
            "全ての段階に共通で付く defaults.note と各段階の steps[].note を新しく書いてください。"
            "(なし)の場合は参考にする伝達メモがないという意味なので、範囲と参照文書だけで共通メモを"
            "書いてください。"
        ),
    },
}


def _work_plan_scope_lines(value) -> list:
    """Accept only a list/tuple of non-blank strings — anything else is an empty scope."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _work_plan_provider_names(project_id: str) -> dict:
    """provider_id → display name. A failed lookup degrades to the bare id, never to a 500."""
    try:
        from modules.flow_gate.settings import ai_settings_service

        effective = ai_settings_service.resolve_effective(project_id)
        return {
            str(provider.get("id")): str(provider.get("name") or provider.get("id"))
            for provider in (effective.get("providers") or [])
            if provider.get("id")
        }
    except Exception:  # a provider lookup must never abort mention generation
        logger.warning(
            "work-plan scope: provider display names unavailable for project=%s",
            project_id, exc_info=True,
        )
        return {}


def _work_plan_type_counts(parent_doc_id: str) -> dict:
    """The workflow_type_counts value derived from this group's workflow sequence (T0005 item 8).

    Computes the very value the tail wording tells the worker to prefer as its basis. An
    unreadable sequence yields an empty dict -- mention generation must not stop over this.
    """
    if not parent_doc_id:
        return {}
    try:
        from modules.flow_gate.db import workflow_sequences as db_wfseq
        from modules.flow_gate.services import work_plan_service

        seq = db_wfseq.get_sequence_by_doc_id(parent_doc_id)
        if seq is None:
            return {}
        items = db_wfseq.get_sequence_items(seq["id"])
        return work_plan_service.workflow_type_counts(items)
    except Exception:  # noqa: BLE001 -- an unreadable sequence must not abort the mention
        logger.warning("work-plan scope: workflow_type_counts unavailable for parent=%s", parent_doc_id, exc_info=True)
        return {}


def _work_plan_scope_section(scope: dict, project_id: str, locale: str, parent_doc_id: str = "") -> str:
    """Render the P0004 work-plan-scope section from the screen's scope payload."""
    loc = template_provision.normalize_locale(locale)
    copy = _WORK_PLAN_SCOPE_COPY[loc]
    scope = scope if isinstance(scope, dict) else {}

    type_codes = [code.upper() for code in _work_plan_scope_lines(scope.get("quantity_type_codes"))]
    provider_ids = _work_plan_scope_lines(scope.get("provider_ids"))
    type_counts = _work_plan_type_counts(parent_doc_id)
    type_counts_text = " / ".join(f"{code} {type_counts[code]}" for code in sorted(type_counts))
    # 0416 TR0005 (rejected: "where did the run provider go?"): the run provider picked on the
    # screen. A different value from provider_ids (the candidates), so it gets its own line.
    default_provider_id = str(scope.get("provider_id") or "").strip()

    quantity_text = " / ".join(
        f"{code} {get_type_name(code, loc) or code}" for code in type_codes
    ) or copy["none"]

    def _block(caption: str, items: list) -> str:
        if not items:
            return f"{caption}: {copy['none']}"
        return caption + ":\n" + "\n".join(f"- {item}" for item in items)

    names = _work_plan_provider_names(project_id) if (provider_ids or default_provider_id) else {}
    default_provider_text = (
        f"{default_provider_id} · {names.get(default_provider_id, default_provider_id)}"
        if default_provider_id else copy["none"]
    )
    # 0405 T0011 rev2: with no candidates, "do not write a non-candidate provider" tells the
    # worker nothing. "There are no providers, so leave it empty" is written instead.
    tail = copy["tail"] if provider_ids else copy["tail_no_providers"]
    # 0416 T0004 -- the planner note typed on screen. An empty value writes "none", for the
    # same reason the section itself is never dropped. It must be preserved verbatim as the
    # plan's defaults.note — note_rule below carries that order to the worker (comments do not).
    note_text = str(scope.get("note") or "").strip()
    # 0416 TR0005 rev2 (review findings 3 and 4): merely writing the value down instructs the
    # worker in nothing. The section carrying the run provider also states that the value is not
    # for step assignment, and every section carries, after the tail, the order to copy the handoff note into defaults.note.
    rules = []
    if default_provider_id:
        rules.append(copy["default_provider_rule"])
    rules.append(copy["note_rule"])
    body = "\n".join([
        copy["lead"],
        "",
        f"{copy['quantities']}: {quantity_text}",
        f"{copy['type_counts']}: {type_counts_text or copy['none']}",
        f"{copy['default_provider']}: {default_provider_text}",
        _block(copy["providers"], [f"{pid} · {names.get(pid, pid)}" for pid in provider_ids]),
        f"{copy['note']}: {note_text or copy['none']}",
        "",
        tail,
        *rules,
    ])
    return _section(copy["header"], body)


# TS is authored by the worker (excluded from auto-instruction), and FlowGate runs
# it remotely from the project source root. Without this block the worker receives a
# generic new-document mention and the TS it writes fails parse_test_plan
# (no_test_cases / invalid_case_block). The three H2 headers and the Korean field labels
# fields are matched verbatim by test_run_service.parse_test_plan; T0009 added the
# English aliases (Setup/Test Cases/Teardown, expect|expected/start/wait) as a second
# accepted spelling, not a replacement — either form parses. This guide was still
# English-fixed regardless of the worker's requested locale (B0001 rev2 follow-up); it
# now follows the same convention TR0012 set for the TR grammar: the ko request shows
# the Korean form as the primary example, en/ja requests show the English form as the
# primary example (there is no ja grammar alias — T0009/0355), and both note the other
# spelling still parses. 'cmd' and the section/field GRAMMAR TOKENS themselves are
# never translated — only the surrounding prose is.
_TS_AUTHORING_TYPES = {"TS"}

_TS_AUTHORING_TEXT = {
    "ko": (
        "이 TS를 실행 가능한 스펙으로 작성하십시오. FlowGate는 프로젝트 소스 루트에서 이를\n"
        "원격으로 실행합니다 — 로컬에서 실행 중인 서비스가 있다고 가정하지 마십시오. 아래 순서로\n"
        "H2 섹션 세 개를 사용하십시오:\n\n"
        "## 테스트 준비        (선택; 있으면 순서대로 가장 먼저 실행)\n"
        "- cmd: <셸 명령, 한 줄>                          # 준비 단계\n"
        "- 기동: <서버 시작 명령, 백그라운드 실행>        # 오래 떠 있는 서비스\n"
        "- 대기: {PORT}                                    # 127.0.0.1:{PORT} 가 응답할 때까지 대기\n\n"
        "## 테스트 케이스       (필수; 최소 한 개)\n"
        "### TC-1: <제목>\n"
        "- cmd: <한 줄 명령; 종료코드 0이면 PASS>\n"
        "- 기대: <기대하는 동작, 사람이 읽을 수 있게>\n\n"
        "## 테스트 정리        (선택; 실패해도 항상 실행)\n"
        "- cmd: <정리 명령>\n\n"
        "영어 별칭도 허용됩니다(철자가 달라도 섹션 단위로 둘 다 파싱됩니다):\n"
        "'## Setup' / '## Test Cases' / '## Teardown', 그리고 필드는 '기대' 대신 'expect'\n"
        "(또는 'expected'), '기동' 대신 'start', '대기' 대신 'wait'. 'cmd'는 어느 쪽이든 동일합니다.\n\n"
        "자리표시자 — {PORT}: FlowGate가 할당하는 포트(환경변수 FLOWGATE_TEST_PORT로도 제공);\n"
        "{SCRATCH}: 실행별 스크래치 디렉터리, 종료 후 삭제(환경변수 FLOWGATE_TEST_SCRATCH).\n"
        "실제 테스트 코드를 이 단계에서 저장소에 커밋하십시오(자동 생성 없음).\n"
        "제한: 케이스 최대 50개, 준비/정리 단계 최대 20개, 서비스 최대 5개. 판정은 종료코드 0 여부뿐입니다.\n\n"
        "프레임워크 무관: 유일한 판정 기준은 프로세스 종료 코드(0 = 통과)입니다. pytest, `npm test`,\n"
        "`npx vitest run`, `go test`, `cargo test`, 순수 스크립트 등 어떤 러너를 써도 됩니다.\n"
        "Python 전용이 아니므로 테스트 대상 코드에 맞는 것을 고르십시오.\n\n"
        "{shell_guidance}"
        "cmd는 SOURCE ROOT에서 실행되므로 먼저 서브프로젝트로 cd 하십시오. 예시 —\n"
        "프런트엔드 Vitest 스위트(client/에 위치, 설정은 client/vitest.config.ts):\n\n"
        "## 테스트 준비\n"
        "- cmd: cd client && npm install          # vitest 설치; `npm ci` 대신 install 사용(esbuild lock)\n"
        "## 테스트 케이스\n"
        "### TC-1: 프런트엔드 유닛 스위트가 통과한다\n"
        "- cmd: cd client && npm test             # == `vitest run`; 종료코드 0이면 PASS\n"
        "- 기대: 모든 Vitest 스펙이 통과(종료코드 0)\n"
        "### TC-2: 프런트엔드 타입체크가 깨끗하다\n"
        "- cmd: cd client && npm run typecheck    # == `vue-tsc -b`; 종료코드 0이면 PASS\n"
        "- 기대: TS 에러 없음(종료코드 0)\n\n"
        "클라이언트를 건드리는 변경이면 둘을 항상 짝지으십시오: Vitest는 타입체크 없이 트랜스파일하므로\n"
        "타입 에러가 `npm test`는 통과하고 배포 빌드에서만 드러납니다(flowgate.default.0300 B0001 ->\n"
        "NR0003 §4). 타입체크 케이스가 이 틈을 막습니다.\n\n"
        "(`cd X && <runner>`는 cmd.exe와 /bin/sh 양쪽에서 동작하는 유일한 체이닝 형태라\n"
        "예시에서 이 형태를 씁니다.)\n\n"
        "JS 러너를 쓰려면 FlowGate 호스트 PATH에 Node/npm이 있어야 합니다; 새로 받은 소스 트리에는\n"
        "node_modules가 없으므로 위 install 준비 단계가 필요합니다."
    ),
    "ja": (
        "このTSを実行可能なスペックとして作成してください。FlowGateはプロジェクトソースルートから\n"
        "リモートでこれを実行します — ローカルで動いているサービスがあると仮定しないでください。\n"
        "以下の順でH2セクションを3つ使ってください:\n\n"
        "## Setup        (任意; あれば最初に順番に実行)\n"
        "- cmd: <シェルコマンド、1行>                     # 準備ステップ\n"
        "- start: <サーバ起動コマンド、バックグラウンド実行>  # 常駐サービス\n"
        "- wait: {PORT}                                    # 127.0.0.1:{PORT} が応答するまで待機\n\n"
        "## Test Cases       (必須; 最低1件)\n"
        "### TC-1: <タイトル>\n"
        "- cmd: <1行コマンド; 終了コード0でPASS>\n"
        "- expect: <期待する動作、人が読める形で>\n\n"
        "## Teardown        (任意; 失敗しても常に実行)\n"
        "- cmd: <後始末コマンド>\n\n"
        "従来の韓国語のセクション名とフィールド名も引き続き受け付けますが、\n"
        "このロケールでは上記の英語表記を使用してください。\n\n"
        "プレースホルダ — {PORT}: FlowGateが割り当てるポート(環境変数FLOWGATE_TEST_PORTでも取得可);\n"
        "{SCRATCH}: 実行ごとのスクラッチディレクトリ、終了後に削除(環境変数FLOWGATE_TEST_SCRATCH)。\n"
        "実際のテストコードはこのステップでリポジトリにコミットしてください(自動生成なし)。\n"
        "上限: ケース最大50件、setup/teardownステップ最大20件、サービス最大5件。判定は終了コード0のみです。\n\n"
        "フレームワーク非依存: 唯一の判定基準はプロセスの終了コード(0 = 合格)です。pytest、`npm test`、\n"
        "`npx vitest run`、`go test`、`cargo test`、素のスクリプトなど、どのランナーでも構いません。\n"
        "Python専用ではないので、テスト対象のコードに合ったものを選んでください。\n\n"
        "{shell_guidance}"
        "cmdはSOURCE ROOTで実行されるので、まずサブプロジェクトへcdしてください。例 —\n"
        "フロントエンドVitestスイート(client/にあり、設定はclient/vitest.config.ts):\n\n"
        "## Setup\n"
        "- cmd: cd client && npm install          # vitestをインストール; `npm ci`ではなくinstallを使う(esbuild lock)\n"
        "## Test Cases\n"
        "### TC-1: frontend unit suite is green\n"
        "- cmd: cd client && npm test             # == `vitest run`; 終了コード0でPASS\n"
        "- expect: すべてのVitestスペックが合格(終了コード0)\n"
        "### TC-2: frontend type check is clean\n"
        "- cmd: cd client && npm run typecheck    # == `vue-tsc -b`; 終了コード0でPASS\n"
        "- expect: TSエラーなし(終了コード0)\n\n"
        "クライアントに触れる変更では常にこの2つを対にしてください: Vitestは型チェックなしで\n"
        "トランスパイルするため、型エラーが`npm test`では通過しデプロイビルドでのみ表面化します\n"
        "(flowgate.default.0300 B0001 -> NR0003 §4)。typecheckケースがこの隙間を塞ぎます。\n\n"
        "(`cd X && <runner>`はcmd.exeと/bin/shの両方で動く唯一のチェイン形式なので、\n"
        "例ではこの形式を使っています。)\n\n"
        "JSランナーを使うにはFlowGateホストのPATHにNode/npmが必要です; 新規のソースツリーには\n"
        "node_modulesが無いため、上記のinstall準備ステップが必要です。"
    ),
    "en": (
        "Write this TS as an executable spec. FlowGate runs it remotely from the project\n"
        "source root — do NOT assume any locally-running service. Use three H2 sections in\n"
        "this order:\n\n"
        "## Setup        (optional; runs first, in order)\n"
        "- cmd: <shell command, single line>            # setup step\n"
        "- start: <server start command, backgrounded>  # long-lived service\n"
        "- wait: {PORT}                                  # wait until 127.0.0.1:{PORT} accepts\n\n"
        "## Test Cases       (required; at least one case)\n"
        "### TC-1: <title>\n"
        "- cmd: <single-line command; PASS iff exit code 0>\n"
        "- expect: <expected behavior, human-readable>\n\n"
        "## Teardown        (optional; always runs, even on failure)\n"
        "- cmd: <cleanup command>\n\n"
        "Legacy Korean section and field spellings remain accepted, but use the English\n"
        "spellings shown above for this locale.\n\n"
        "Placeholders — {PORT}: port FlowGate allocates (also env FLOWGATE_TEST_PORT);\n"
        "{SCRATCH}: per-run scratch dir, deleted afterward (env FLOWGATE_TEST_SCRATCH).\n"
        "Commit the actual test code to the repo in this step (no auto-generation).\n"
        "Limits: at most 50 cases, 20 setup/teardown steps, 5 services. Verdict is exit-0 only.\n\n"
        "Framework-agnostic: the ONLY verdict is the process exit code (0 = pass). Any\n"
        "runner works — pytest, `npm test`, `npx vitest run`, `go test`, `cargo test`, a bare\n"
        "script. This is NOT Python-only; pick whatever matches the code under test.\n\n"
        "{shell_guidance}"
        "Because cmd runs at the SOURCE ROOT, cd into the subproject first. Example — the\n"
        "frontend Vitest suite (lives in client/, config at client/vitest.config.ts):\n\n"
        "## Setup\n"
        "- cmd: cd client && npm install          # installs vitest; use install, NOT `npm ci` (esbuild lock)\n"
        "## Test Cases\n"
        "### TC-1: frontend unit suite is green\n"
        "- cmd: cd client && npm test             # == `vitest run`; PASS iff exit 0\n"
        "- expect: all Vitest specs pass (exit 0)\n"
        "### TC-2: frontend type check is clean\n"
        "- cmd: cd client && npm run typecheck    # == `vue-tsc -b`; PASS iff exit 0\n"
        "- expect: no TS errors (exit 0)\n\n"
        "Pair the two whenever a change touches the client: Vitest transpiles without type\n"
        "checking, so a type error passes `npm test` and only surfaces in the deploy build\n"
        "(flowgate.default.0300 B0001 -> NR0003 §4). The typecheck case closes that gap.\n\n"
        "(`cd X && <runner>` is the one chaining form that works on both cmd.exe and /bin/sh,\n"
        "which is why the example uses it.)\n\n"
        "Node/npm must be on the FlowGate host PATH for JS runners; a fresh source tree has\n"
        "no node_modules, so the install setup step above is required."
    ),
}


def _ts_authoring_section(locale: str = "ko") -> str:
    loc = template_provision.normalize_locale(locale)
    host_os = test_command_service.current_os()
    # {PORT}/{SCRATCH} in the text above are literal placeholders shown to the worker,
    # not str.format fields — use a plain marker replace so they survive untouched.
    body = _TS_AUTHORING_TEXT[loc].replace("{shell_guidance}", _ts_host_shell_guidance(host_os, loc))
    return _section("Test scenario authoring (TS)", body)


# Host-shell guidance (flowgate.default.0277 B0001 -> NR0003 §4 F1).
# Every cmd is spawned with shell=True, so it is interpreted by %COMSPEC% (cmd.exe) on
# Windows and /bin/sh on POSIX. The guide never said which, so workers defaulted to POSIX
# and their commands failed outright once FlowGate moved to a Windows host. State the
# actual host shell and name the idioms that do not survive it.
_TS_WINDOWS_SHELL_GUIDANCE = {
    "ko": (
        "HOST SHELL — 이 FlowGate 호스트는 WINDOWS입니다(os.name=nt). 모든 cmd는 /bin/sh가 아닌\n"
        "cmd.exe로 해석됩니다. POSIX 전용 문법은 여기서 동작하지 않습니다. 쓰지 마십시오: rm -rf\n"
        "(대신 `rmdir /s /q` 또는 `del /q`), ls, cat, grep, touch, which, `export VAR=x`(대신\n"
        "`set VAR=x`), `$VAR`(대신 `%VAR%`), `$(cmd)` 치환, 작은따옴표 'strings'(cmd.exe는 이를\n"
        "벗겨내지 않습니다 — 큰따옴표 사용), `2>/dev/null`(대신 `2>nul`), `&&`로 이어진\n"
        "`source`/`.`, `/`로 시작하는 절대경로. 존재한다면 이식 가능한 대안을 우선하십시오: `&&`\n"
        "체이닝, `cd`, 언어 수준 러너(python -m pytest, npm test, go test)는 두 플랫폼에서 동일하게\n"
        "동작하므로 셸 전용 코드를 쓰기 전에 먼저 검토하십시오. 정말 POSIX 동작이 필요한 단계라면\n"
        "셸 빌트인을 인라인으로 쓰지 말고 저장소에 스크립트로 작성해 PATH에 있는 인터프리터로\n"
        "실행하십시오(예: `python tools/check.py`).\n\n"
    ),
    "ja": (
        "HOST SHELL — このFlowGateホストはWINDOWSです(os.name=nt)。すべてのcmdは/bin/shではなく\n"
        "cmd.exeで解釈されます。POSIX専用の文法はここでは動作しません。使わないでください: rm -rf\n"
        "(代わりに`rmdir /s /q`または`del /q`)、ls、cat、grep、touch、which、`export VAR=x`\n"
        "(代わりに`set VAR=x`)、`$VAR`(代わりに`%VAR%`)、`$(cmd)`置換、シングルクォート\n"
        "'strings'(cmd.exeはこれを取り除きません — ダブルクォートを使用)、`2>/dev/null`\n"
        "(代わりに`2>nul`)、`&&`で連結した`source`/`.`、`/`で始まる絶対パス。存在する場合は\n"
        "移植可能な代替を優先してください: `&&`チェイン、`cd`、言語レベルのランナー\n"
        "(python -m pytest, npm test, go test)は両プラットフォームで同じ動作をするため、\n"
        "シェル専用コードを書く前にまずこちらを検討してください。本当にPOSIXの動作が必要な\n"
        "ステップなら、シェルビルトインをインラインで書かず、リポジトリにスクリプトとして書いて\n"
        "PATH上のインタプリタから実行してください(例: `python tools/check.py`)。\n\n"
    ),
    "en": (
        "HOST SHELL — this FlowGate host is WINDOWS (os.name=nt). Every cmd is interpreted by\n"
        "cmd.exe, NOT /bin/sh. POSIX-only syntax fails here. Do NOT use: rm -rf (use `rmdir /s /q`\n"
        "or `del /q`), ls, cat, grep, touch, which, `export VAR=x` (use `set VAR=x`), `$VAR`\n"
        "(use `%VAR%`), `$(cmd)` substitution, single-quoted 'strings' (cmd.exe does not strip\n"
        "them — use double quotes), `2>/dev/null` (use `2>nul`), `&&`-chained `source`/`.`, and\n"
        "`/`-rooted absolute paths. Portable alternatives are preferred where they exist: `&&`\n"
        "chaining, `cd`, and any language-level runner (python -m pytest, npm test, go test)\n"
        "behave the same on both platforms — reach for those before writing shell-specific code.\n"
        "If a step genuinely needs POSIX semantics, write it as a script in the repo and invoke\n"
        "it through an interpreter that is on PATH (e.g. `python tools/check.py`) rather than\n"
        "inlining shell builtins.\n\n"
    ),
}

_TS_POSIX_SHELL_GUIDANCE = {
    "ko": (
        "HOST SHELL — 이 FlowGate 호스트는 POSIX입니다(os.name=posix). 모든 cmd는 /bin/sh로\n"
        "해석됩니다(bash 아님): [[ ]], 배열, `source`(대신 `.`) 같은 bash 전용 문법을 피하십시오.\n"
        "Windows 전용 문법(%VAR%, `set VAR=x`, 백슬래시 경로, `2>nul`)은 동작하지 않습니다.\n"
        "셸 빌트인보다 언어 수준 러너(python -m pytest, npm test, go test)를 우선하십시오 —\n"
        "이 프로젝트가 나중에 Windows 호스트로 옮겨져도 TS의 이식성이 유지됩니다.\n\n"
    ),
    "ja": (
        "HOST SHELL — このFlowGateホストはPOSIXです(os.name=posix)。すべてのcmdは/bin/shで\n"
        "解釈されます(bashではありません): [[ ]]、配列、`source`(代わりに`.`)のような\n"
        "bash専用文法は避けてください。Windows専用の文法(%VAR%, `set VAR=x`, バックスラッシュ\n"
        "パス, `2>nul`)は動作しません。シェルビルトインより言語レベルのランナー\n"
        "(python -m pytest, npm test, go test)を優先してください — このプロジェクトが\n"
        "将来Windowsホストへ移されてもTSの移植性が保たれます。\n\n"
    ),
    "en": (
        "HOST SHELL — this FlowGate host is POSIX (os.name=posix). Every cmd is interpreted by\n"
        "/bin/sh (NOT bash): avoid bashisms such as [[ ]], arrays, and `source` (use `.`).\n"
        "Windows-only syntax (%VAR%, `set VAR=x`, backslash paths, `2>nul`) will not work.\n"
        "Prefer language-level runners (python -m pytest, npm test, go test) over shell builtins —\n"
        "they keep the TS portable if this project is ever moved to a Windows host.\n\n"
    ),
}


def _ts_host_shell_guidance(host_os: str, locale: str = "ko") -> str:
    """The shell-specific do/don't block, chosen from the host FlowGate actually runs on."""
    loc = template_provision.normalize_locale(locale)
    if host_os == test_command_service.OS_WINDOWS:
        return _TS_WINDOWS_SHELL_GUIDANCE[loc]
    return _TS_POSIX_SHELL_GUIDANCE[loc]


# ── N/T instruction authoring (group 0230 R0001 / T0005 WI-7) ────────────────────
# When a continuous run chooses "the AI writes it directly" (continuation_instruction_mode = ai_direct),
# advance_workflow SKIPS the server-side auto-complete of N/T instruction heads, so the head
# reaches the worker mention as an N or T. Without a dedicated guide the worker would receive a
# generic new-document mention and might mis-scope the instruction (e.g. an N that fails to name
# what to investigate, or a T with no acceptance criteria). This section tells the worker how to
# AUTHOR the instruction document itself — mirroring _ts_authoring_section's role for TS.
#
# In the default (auto_approved) path N/T are auto-completed server-side and never reach this
# code, so the section is only ever emitted when the flag is on — no regression to managed runs.
_NT_AUTHORING_TYPES = {"N", "T"}

# This guide was still English-fixed regardless of the worker's requested locale (B0001
# rev2 follow-up). Unlike _ts_authoring_section, the bullet labels below (purpose/background,
# investigation scope, ...) are plain prose headings for the worker to fill in — nothing here is matched
# by a parser — so, unlike the TS grammar tokens, they translate freely per locale.
_NT_AUTHORING_TEXT = {
    "ko": {
        "N": (
            "고정 템플릿을 서버가 내려주는 대신, 이 N(조사지시 / investigation-instruction)을\n"
            "직접 작성하는 것입니다. 이 그룹의 실제 맥락과 그것이 뒷받침하는 요건을 반영하십시오.\n"
            "좋은 N은 아무것도 조사하거나 구현하지 않습니다 — 짝을 이루는 NR이 수행할 조사를\n"
            "지시(DIRECT)할 뿐입니다. 다음을 다루십시오:\n"
            "- 목적/배경: 왜 이 조사가 필요한지(근거가 되는 R/B와 연결).\n"
            "- 조사 범위: NR이 답해야 할 구체적인 질문 + 살펴볼 코드/영역.\n"
            "- 산출물 기대: NR이 도출해야 할 결론(근본 원인, 좌표, 재사용 앵커).\n"
            "지시문으로만 남기십시오: 무엇을 알아내야 하는지만 이름 붙이고, 결과 자체는 이어지는\n"
            "NR에 담으십시오. 이 단계에서 소스 코드를 수정하지 마십시오."
        ),
        "T": (
            "고정 템플릿을 서버가 내려주는 대신, 이 T(작업지시 / work-instruction)를 직접\n"
            "작성하는 것입니다. 이 그룹의 실제 맥락과 선행 조사의 결과를 반영하십시오. 좋은 T는\n"
            "짝을 이루는 TR이 수행할 작업을 지시(DIRECT)합니다. 다음을 다루십시오:\n"
            "- 목적/범위: 무엇을 왜 변경하는지(근거가 되는 R/B + NR과 연결).\n"
            "- 작업 항목: 구체적이고 순서가 있는 작업 항목(건드릴 파일/영역, 접근 방식).\n"
            "- 완료 기준: TR이 완료를 어떻게 증명하는지(GREEN으로 돌려야 할 테스트, 인수 기준).\n"
            "지시문으로만 남기십시오: 작업을 지시하고, 구현과 증거는 이어지는 TR에 담으십시오.\n"
            "이 단계에서 코드를 구현하지 말고 지시 내용만 작성하십시오."
        ),
        "footer": (
            "\n\n위 'Instruction to include next document header' 섹션에 주어진 다음 문서 헤더를\n"
            "(next_type / project / module / group / title / target_id) 그대로 포함하십시오.\n"
            "제출되면 이 지시문은 다른 관리형 지시문과 마찬가지로 자동 승인되며(non-{M,CH}),\n"
            "무인 체인은 짝을 이루는 리포트 단계로 진행됩니다."
        ),
    },
    "ja": {
        "N": (
            "サーバが固定テンプレートを出す代わりに、このN(調査指示 / investigation-instruction)を\n"
            "あなた自身が作成します。このグループの実際の文脈と、それが支える要件を反映してください。\n"
            "良いNは何も調査・実装しません — 対になるNRが行う調査を指示(DIRECT)するだけです。\n"
            "以下をカバーしてください:\n"
            "- 目的/背景: なぜこの調査が必要か(根拠となるR/Bに結び付ける)。\n"
            "- 調査範囲: NRが答えるべき具体的な質問 + 調べるべきコード/領域。\n"
            "- 成果物への期待: NRが導くべき結論(根本原因、座標、再利用アンカー)。\n"
            "あくまで指示文にとどめてください: 何を明らかにすべきかだけを名指しし、結果そのものは\n"
            "後に続くNRに書いてください。このステップではソースコードを変更しないでください。"
        ),
        "T": (
            "サーバが固定テンプレートを出す代わりに、このT(作業指示 / work-instruction)を\n"
            "あなた自身が作成します。このグループの実際の文脈と、先行調査の結果を反映してください。\n"
            "良いTは対になるTRが行う作業を指示(DIRECT)します。以下をカバーしてください:\n"
            "- 目的/範囲: 何をなぜ変更するのか(根拠となるR/B + NRに結び付ける)。\n"
            "- 作業項目: 具体的で順序立った作業項目(触れるファイル/領域、アプローチ)。\n"
            "- 完了基準: TRがどう完了を証明するか(GREENにすべきテスト、受け入れ基準)。\n"
            "あくまで指示文にとどめてください: 作業を指示し、実装と証拠は後に続くTRに書いて\n"
            "ください。このステップではコードを実装せず、指示内容だけを書いてください。"
        ),
        "footer": (
            "\n\n上記の「Instruction to include next document header」セクションに示された次の\n"
            "文書ヘッダー(next_type / project / module / group / title / target_id)をそのまま\n"
            "含めてください。提出されるとこの指示文は他の管理下の指示文と同様に自動承認され\n"
            "(non-{M,CH})、無人チェーンは対になるレポート段階へ進みます。"
        ),
    },
    "en": {
        "N": (
            "You are authoring this N (investigation-instruction) directly instead of\n"
            "the server emitting a fixed template. Reflect the actual context of this group and\n"
            "the requirement it serves. A good N does NOT investigate or implement anything — it\n"
            "DIRECTS the investigation the paired NR will carry out. Cover:\n"
            "- Purpose/background: why this investigation is needed (tie it to the driving R/B).\n"
            "- Investigation scope: the concrete questions the NR must answer + the code/areas to inspect.\n"
            "- Expected output: what the NR should conclude (root cause, coordinates, reuse anchors).\n"
            "Keep it an instruction: name what to find out, not the findings themselves — those\n"
            "belong in the NR that follows. Do NOT modify source code in this step."
        ),
        "T": (
            "You are authoring this T (work-instruction) directly instead of the server\n"
            "emitting a fixed template. Reflect the actual context of this group and the findings\n"
            "of the preceding investigation. A good T DIRECTS the work the paired TR will carry\n"
            "out. Cover:\n"
            "- Purpose/scope: what change is being made and why (tie it to the driving R/B + NR).\n"
            "- Work items: the concrete, ordered work items (files/areas to touch, the approach).\n"
            "- Completion criteria: how the TR proves it is done (tests to run GREEN, acceptance checks).\n"
            "Keep it an instruction: direct the work; the implementation + evidence belong in the\n"
            "TR that follows. Do NOT implement the code in this step — write the directive."
        ),
        "footer": (
            "\n\nInclude the next-document header exactly as given in the 'Instruction to include next\n"
            "document header' section above (next_type / project / module / group / title / target_id).\n"
            "On submit this instruction is auto-approved (non-{M,CH}) like any managed instruction and\n"
            "the unmanned chain proceeds to its paired report step."
        ),
    },
}


def _nt_authoring_section(scope_type: str, locale: str = "ko") -> str:
    """Authoring guidance for an instruction document the worker writes directly (N or T)."""
    stype = (scope_type or "").upper()
    loc = template_provision.normalize_locale(locale)
    texts = _NT_AUTHORING_TEXT[loc]
    body = texts["N"] if stype == "N" else texts["T"]
    body += texts["footer"]
    return _section(f"Instruction authoring ({stype})", body)


# ── R018 prompt builder ───────────────────────────────────────────────────────

def build_mention(
    *,
    # Parent document information
    project: str,
    module: str,
    group: str,
    parent_type: str,
    parent_doc_number: str,
    parent_title: str,
    parent_doc_id: str,
    # Head sequence item
    head_type: str,
    head_status: str,
    # Token/scratch
    scratch_dir: str,
    raw_token: str,
    # API
    api_base_url: str,
    parent_canonical_doc_id: str = "",
    # Recent documents in the group (R018 §2-3, P005 §3-3) — up to 5, seq DESC
    # Item format: {"doc_id": <canonical>, "doc_type": str, "seq": int, "title": str, "status": str}
    group_recent_docs: Optional[list] = None,
    # group_id (canonical) — used for the section 4 navigation URL
    group_id: str = "",
    # T358: canonical ID list of additional reference documents (multiple supported)
    ref_doc_ids: Optional[list] = None,
    # action_scope: decides the action value in the section 5 POST body ("new" | "edit")
    action_scope: str = "new",
    # Latest AI review for the document's current revision (edit mentions only).
    current_review: Optional[dict] = None,
    parent_revision_no: int = 0,
    edit_reason: str = "user_comment",
    # Display locale for human-readable doc-type names (ko/ja/en). Codes stay bare
    # for machine round-trip; the localized name is emitted alongside as *_detail.
    locale: str = "ko",
    # Section 1 ('## Document information') override (R0001 / T0004). In the advance/new
    # hand-off, parent_* describes the workflow-sequence-owning R (the spine, used for
    # target_id / prev_doc_id / token doc_ref). The head document the worker actually
    # builds upon is its predecessor — for an NR step the N, for a TR step the T. When
    # these are supplied (and not an edit), Section 1 reflects the predecessor instead
    # of the spine R; the threading fields (Section 2 target_id, Section 5 prev_doc_id)
    # keep using parent_*, unchanged.
    head_doc_type: Optional[str] = None,
    head_doc_number: Optional[str] = None,
    head_doc_title: Optional[str] = None,
    # Continuous (unmanned) work (group 0051 R0001 / NR0003 §4-3). When True (and not an
    # edit), the Clarification guide §2 and the bottom Reminder §9 are replaced by the
    # delegation/unmanned/no-stop/autonomous block instead of the Q-registration guard.
    continuous: bool = False,
    # Continuous AI-review mode (group 0086 R0001, AI review mode). When True (with
    # ``continuous``), the replacement block is the review variant that keeps the Q
    # latitude (scrutinise → Q-if-blocked → else proceed) instead of "never stop, never ask".
    continuous_review_mode: bool = False,
    # 0405 P0004: the work-plan proposal scope a person chose on screen
    # ({quantity_type_codes, step_keys, provider_ids}). Read ONLY when the head type is
    # WP and this is not an edit; every other mention is byte-identical without it.
    work_plan_scope: Optional[dict] = None,
) -> Optional[str]:
    """Return an R018-enhanced prompt string.

    Insert a placeholder into next_type based on the head state:
      - no head_type                → <Sequence undecided>
      - head_type with in_progress  → <In progress: {head_type}>
      - head_type with pending      → {head_type} (normal)

    When ref_doc_ids is provided, add multiple reference documents to section 3
    ('## Reference documents'). If ref_doc_ids contains the same slash-path as
    the head, remove the duplicate before output.
    """
    if not head_type:
        next_type_value = "<Sequence undecided>"
    elif head_status != "pending":
        next_type_value = f"<In progress: {head_type}>"
    else:
        next_type_value = head_type

    base = _api_base(api_base_url)

    is_edit = action_scope == "edit"
    # Review phase (group 0086 R0001 — TR0004 rework rev4): a continuous run with AI review mode
    # ON is the pre-flight Q-registration phase, not "go". All "create the next document"
    # guidance (the new-document section) is removed so the worker only registers Qs.
    review_continuous = continuous and continuous_review_mode and not is_edit

    # ── Section 1: document information ──────────────────────────────────────
    # Anchor at the step's predecessor (head_doc_*) when supplied for a new hand-off;
    # otherwise fall back to the sequence-owning parent (also the correct value for
    # the first step and for in-place edits). See the head_doc_* param docs above.
    if head_doc_type and not is_edit:
        s1_type = head_doc_type
        s1_doc_number = head_doc_number or parent_doc_number
        s1_title = head_doc_title if head_doc_title is not None else parent_title
    else:
        s1_type = parent_type
        s1_doc_number = parent_doc_number
        s1_title = parent_title
    s1_body = (
        f"project: {project}\n"
        f"module: {module}\n"
        f"group: {group}\n"
        f"type: {s1_type}\n"
        f"type_detail: {get_type_name(s1_type, locale)}\n"
        f"doc_number: {s1_doc_number}\n"
        f"title: {s1_title}"
    )
    # workflow_decision_service imports mention_service, so importing its constant at
    # module load time creates a cycle before AUTO_REPORT_MAP has been defined.
    from modules.flow_gate.services.workflow_decision_service import AUTO_REPORT_MAP

    if head_doc_type and not is_edit and s1_type.upper() in AUTO_REPORT_MAP.values():
        loc = template_provision.normalize_locale(locale)
        s1_body += f"\n{_PREDECESSOR_IDENTITY_WARNING_TEXT[loc]}"

    # ── Section 2: creation or in-place revision instructions ────────────────
    if is_edit:
        target_doc_id = parent_canonical_doc_id or parent_doc_id
        s2_header = "Revision instructions"
        s2_body = (
            f"Revise the existing document {target_doc_id} in place.\n"
            f"Current revision: {parent_revision_no}\n"
            "Read the target document and the review feedback below before editing.\n"
            "Preserve the document identity and submit the complete revised content."
        )
    elif review_continuous:
        # Review phase: show WHICH step would be produced (so the worker can scrutinise it)
        # but make clear it must NOT be created yet — this is Q-gathering, not creation.
        s2_header = "Step under review (do NOT create it yet)"
        next_type_detail = f"next_type_detail: {get_type_name(head_type, locale)}\n" if head_type else ""
        s2_body = (
            "The next step that the unmanned run WOULD produce after the go is shown below. "
            "In this review phase, do NOT create it — only review it and register any Q.\n"
            "\n"
            f"next_type: {next_type_value}\n"
            f"{next_type_detail}"
            f"target_id: {parent_doc_id}"
        )
    else:
        s2_header = "Instruction to include next document header"
        next_type_detail = f"next_type_detail: {get_type_name(head_type, locale)}\n" if head_type else ""
        s2_body = (
            f"next_type: {next_type_value}\n"
            f"{next_type_detail}"
            f"project: {project}\n"
            f"module: {module}\n"
            f"group: {group}\n"
            "title: <Title here>\n"
            f"target_id: {parent_doc_id}"
        )

    # ── Work scope guard (R0013 / flowgate.default.0013.0001-R) ──────────────
    # When the worker is asked to produce (or revise) an investigation document
    # (N investigation-instruction / NR investigation-report), state that the task is investigation-only.
    # This prevents workers from implementing code while writing a report —
    # implementation is carried out separately in a T (work-instruction) document.
    scope_type = parent_type if is_edit else head_type
    scope_section = ""
    if scope_type in _INVESTIGATION_ONLY_TYPES:
        scope_section = _section(
            "Work scope",
            f"This is an investigation document ({scope_type}). Investigate and report "
            "findings ONLY.\n"
            "Do NOT modify, create, or implement any source code or files. Implementation "
            "is performed\n"
            "separately in a T "
            f"({_WORK_INSTRUCTION_LABEL[template_provision.normalize_locale(locale)]}) "
            "document.",
        )

    # ── TS authoring guidance ────────────────────────────────────────────────
    # A worker whose head/target is a TS must be told to fetch the 3-section case-block
    # grammar before writing one. Group 0372 set 3 (D-0003 §3-2): the mention now carries
    # only the pointer — the full grammar lives behind the authoring_guide help item.
    ts_authoring_section = ""
    if scope_type in _TS_AUTHORING_TYPES:
        ts_authoring_section = _authoring_guide_pointer_section(
            "Test scenario authoring (TS)", "TS", locale, base
        )

    # ── N/T instruction authoring guidance (group 0230 R0001 / T0005 WI-7) ────
    # Only reached when the run chose ai_direct: in auto_approved mode N/T are
    # auto-completed server-side and never arrive here as a worker mention.
    # Group 0372 set 3 (D-0003 §3-2): pointer only, same reduction as TS above.
    nt_authoring_section = ""
    if scope_type in _NT_AUTHORING_TYPES:
        nt_authoring_section = _authoring_guide_pointer_section(
            f"Instruction authoring ({scope_type})", scope_type, locale, base
        )

    # ── Section 3: reference documents ───────────────────────────────────────
    # Format: {dot-dash-path}: GET {url}  (use only the new format)
    s3_lines = []
    effective_ref_ids = list(ref_doc_ids or [])
    target_doc_id = parent_canonical_doc_id or parent_doc_id
    if is_edit and target_doc_id:
        effective_ref_ids.insert(0, target_doc_id)
    seen_ref_ids: set[str] = set()
    for doc_dot_dash_path in effective_ref_ids:
        if not doc_dot_dash_path or doc_dot_dash_path in seen_ref_ids:
            continue
        seen_ref_ids.add(doc_dot_dash_path)
        s3_lines.extend(_reference_doc_lines(doc_dot_dash_path, base, locale))
    s3_body = f"Note: All GET requests require an Authorization: Bearer {raw_token} header\n\n" + "\n".join(s3_lines)

    # ── Edit-only review feedback for the current document revision ─────────
    review_section = ""
    if is_edit and current_review:
        findings = current_review.get("findings")
        if not isinstance(findings, list):
            findings = []
        review_lines = [
            f"Target revision: {current_review.get('revision_no', parent_revision_no)}",
            f"Verdict: {current_review.get('verdict', '')}",
        ]
        comment = current_review.get("comment")
        if comment:
            review_lines.extend(["", f"Overall comment: {comment}"])
        if findings:
            review_lines.extend(["", "Findings to address:"])
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                locus = str(finding.get("locus") or "Unspecified location")
                note = str(finding.get("note") or "")
                review_lines.append(f"- {locus}: {note}")
        if target_doc_id:
            review_lines.extend([
                "",
                f"Full review history: GET {base}/document/{target_doc_id}/reviews",
            ])
        review_section = _section("Review feedback", "\n".join(review_lines))

    # ── Section 4: recent documents in the group — REMOVED (D-0003 §3-2, dropped) ──
    # The list is now the `group_documents` help item, always visible in the index — a
    # worker never needs a pointer line for it (D-0003 §3-2: "folded into one line of the help
    # index"). group_recent_docs stays accepted for caller compatibility but is no
    # longer rendered.

    # ── Section 5: artifact registration ──────────────────────────────────────
    group_name_value = group_id or f"{project}.{module}.{group}"
    prev_doc_id_value = parent_canonical_doc_id or parent_doc_id
    if is_edit:
        post_body: dict = {
            "action": "edit",
            "project": project,
            "module": module,
            "group_name": group_name_value,
            "doc_id": prev_doc_id_value,
            "edit_reason": edit_reason,
            "content": "<Complete revised document content>",
            # 0391 T0005 §7-2: optional body fingerprint (proposal 4) + bypass door.
            "body_sha256": "<optional: sha256 hex of content, UTF-8 bytes>",
            "body_chars": "<optional: character count of content>",
            "force_encoding_reason": "<optional: only if a genuinely-flagged content must go through anyway>",
        }
        # Rejection rework: the resubmission must carry the worker's response to the
        # rejection. Surfacing `rejection_response` here in the copy mention is the
        # ONLY thing that prompts the AI to send it — the server (inbox _handle_edit
        # → record_rejection_response) then attaches it to the latest rejection item
        # for read-only display. Without this field the response is never collected,
        # so the reviewer sees no "response content" and cannot tell what changed.
        # D-0003 §3-1 question 2 ("would not reading it make the start itself wrong?") keeps this whole
        # example inline for the edit path — unlike `new` below, it is not boilerplate.
        if edit_reason == "rejected":
            post_body["rejection_response"] = _REJECTION_RESPONSE_PLACEHOLDER[
                template_provision.normalize_locale(locale)
            ]
        post_json = json.dumps(post_body, ensure_ascii=False, indent=2)
        commit_hint = ""
        # 0299: a resubmission (edit) goes through the work-scope check too. If the rework
        # instruction omits the format guidance, the rejected worker resubmits blind and is rejected again.
        if str(parent_type or "").upper() in tool_registry.MUTATING_STEP_TYPES:
            commit_hint = f"\n{tr_scope_service.tr_section_guide(template_provision.normalize_locale(locale))}"
        content_source_hint = (
            "Choose exactly one document source (XOR):\n"
            "- `content`: send the complete document inline, as shown in the POST example below.\n"
            "- `doc_path`: replace `content` with the absolute path of a UTF-8 file located "
            f"inside this token's scratch_dir: `{scratch_dir}`.\n"
            "Do not send both `content` and `doc_path`."
        )
        s5_body = (
            f"Artifact registration: POST {base}/inbox\n"
            f"Authorization: Bearer {raw_token}\n"
            f"\n"
            f"{content_source_hint}\n"
            f"\n"
            f"{post_json}\n"
            f"{commit_hint}"
            f"\n"
            f"{_DRYRUN_HINT}\n\n"
            "A successful new/edit response includes `change_summary`. "
            "Inspect it before continuing to confirm the saved lines and sections match your intent."
        )
    else:
        # group 0372 set 3 (D-0003 §3-2 / §3-5): the request format, the field-by-field
        # example and the dry-run guide now live behind the `submit` help item, which
        # already builds this exact body (including the TR content/commit_message
        # placeholders) for every action_scope. The mention keeps only what D-0003 §3-1
        # says cannot move: the address, the credential, and — §3-5's named exception —
        # the FACT (not the format) that a TR submission must carry a changed-files
        # section.
        doc_type_value = head_type if head_type else "<Sequence undecided>"
        loc = template_provision.normalize_locale(locale)
        s5_body = (
            f"Artifact registration: POST {base}/inbox\n"
            f"Authorization: Bearer {raw_token}\n"
            f"\n"
            f"{_SUBMIT_POINTER_TEXT[loc].format(url=f'{base}/help/items/submit')}"
        )
        if str(doc_type_value).upper() in tool_registry.MUTATING_STEP_TYPES:
            s5_body += "\n" + _CHANGED_FILES_REQUIRED_TEXT[loc].format(
                url=f"{base}/help/items/changed_files_format"
            )
        # group 0370 (merge): what the response CONTAINS is not a request format, so
        # D-0003 §3-1 keeps it inline — a worker cannot inspect a field it was never
        # told it receives.
        s5_body += (
            "\nA successful new/edit response includes `change_summary`. Inspect it "
            "before continuing to confirm the saved lines and sections match your intent."
        )

    # ── Section 6: scratch directory ──────────────────────────────────────────
    # Workers and FlowGate run against the same group-worktree filesystem. Expose
    # the token-owned directory so file submissions satisfy the server boundary.
    s6_body = (
        f"{scratch_dir}\n\n"
        "For file-based inbox submissions, `{SCRATCH}` means the path above. "
        "Create the file inside it and send that file's absolute path as `doc_path`."
    )

    # ── Section 7: doc_type guide — REMOVED (group 0372 set 3, D-0003 §3-2 "dropped") ──
    # It was already a bare one-line help URL; the always-visible `doc_type` entry of
    # the help index (taught by the central Help block below) absorbs it.

    # ── Clarification guide (embedded query POST + no-choices guard; hoisted to top in assembly) ──
    # Anchor the embedded POST at the document the worker's token is bound to (the
    # inbox/edit spine = prev_doc_id_value); the q route re-aims it to the current
    # work-context document. See _clarification_guide_body (B0001 / NR0003).
    #
    # Continuous (unmanned) mode (group 0051 R0001): the Q-registration guard is
    # replaced — not removed — by the delegation/unmanned/no-stop/autonomous block.
    # Edits never run continuously (a rejection rework is a human-gated step), so the
    # branch is guarded by `not is_edit`.
    continuous_mode = continuous and not is_edit
    # Review phase (review_continuous, computed above): the Clarification section becomes the
    # Q-registration guide and the action:new "Artifact registration" section is
    # suppressed below — the worker registers Qs, it does not create the next document.
    if review_continuous:
        s8_header = "Review phase (pre-flight Q registration)"
        s8_body = _continuous_review_guide_body(base, prev_doc_id_value, raw_token, locale)
    elif continuous_mode:
        s8_header = "Continuous work"
        s8_body = _continuous_guide_body(locale, review_mode=False)
    else:
        s8_header = "Clarification guide"
        s8_body = _clarification_guide_body(base, prev_doc_id_value, raw_token, locale)

    # ── Document template (group 0372 set 2): one help pointer, never the body ──
    # The full D/P/L/DB template now lives behind the worker-authenticated help item.
    # New hand-offs point at head_type; edits point at the design document being revised.
    template_section = ""
    tmpl_type = parent_type if is_edit else head_type
    if tmpl_type:
        try:
            if template_provision.has_body_template(tmpl_type):
                template_section = _section(
                    "Document template",
                    template_provision.render_help_pointer(tmpl_type, locale, base),
                )
        except Exception:  # a help pointer must never abort mention generation
            logger.warning(
                "design-template help pointer failed for type=%s",
                tmpl_type, exc_info=True,
            )
            template_section = ""

    source_crud_section = ""
    if _include_remote_source_crud(project):
        source_crud_section = _remote_source_crud_section(
            base, raw_token, scope_type, action_scope=action_scope, locale=locale
        )

    # ── Assembly ──────────────────────────────────────────────────────────────
    sections = [
        _section("Document information", s1_body),
        # R0001/T0004: the clarification guide is hoisted directly under the
        # document-identity header (was last → ignored). The no-choices guard lives
        # inside s8_body so workers stop offering options the remote run can't answer.
        # In continuous mode the header/body swap to the unmanned work block (s8_header).
        _section(s8_header, s8_body),
        # group 0372 set 3 (L-0005 §2-10): the central help-index block sits once per
        # mention, directly below the identity + guide blocks and above everything the
        # index can answer in depth.
        _help_index_section(base, raw_token, locale),
    ]
    # 0349 D0004 D-5: the tool section moves up to the highest slot it is allowed to take —
    # under the identity header and the continuous/clarification block (which tell the worker
    # who it is and how to ask), above the instruction/scope/authoring sections it used to
    # sit below. It was previously buried after the template and authoring blocks, which is
    # where long mentions stop being read.
    if source_crud_section:
        sections.append(source_crud_section)
    sections.append(_section(s2_header, s2_body))
    # 0405 P0004 [mention body]: right after '## Instruction to include next document header'
    # and right before '## Document template'. With a non-WP head, or no scope received, nothing
    # is appended, so existing mentions are unchanged.
    if (
        work_plan_scope is not None
        and not is_edit
        and str(head_type or "").upper() == _WORK_PLAN_SCOPE_HEAD_TYPE
    ):
        try:
            sections.append(_work_plan_scope_section(work_plan_scope, project, locale, parent_doc_id))
        except Exception:  # a scope section must never abort mention generation
            logger.warning("work-plan scope section failed", exc_info=True)
    if scope_section:
        sections.append(scope_section)
    if nt_authoring_section:
        sections.append(nt_authoring_section)
    if ts_authoring_section:
        sections.append(ts_authoring_section)
        # flowgate.default.0152 / group 0372 set 3: the verified-commands list used to be
        # inlined here (D-0003 §3-2, "verified test commands: dropped") — it is now the `test_commands`
        # help item, auto-visible in the index whenever a TS is being authored, so no pointer
        # line is needed here either (same "absorbed into the index" rule as recent_documents).
        # flowgate.default.0157's engine-recipe block ("prepare the execution environment" — same D-0003 row)
        # rides along: help_catalog._content_test_commands now carries it as
        # `engine_recipes`, so the registry is still taught on the first help call
        # (L §2-7) without this mention inlining it.
    if template_section:
        sections.append(template_section)
    sections.append(_section("Reference documents", s3_body))
    # MERGE NOTE (branch base 241f00d predates group 0370 on main): main inserts an
    # inline "Efficient document lookup" section right here. Group 0372 absorbed that
    # guidance into the `document_access` help item (D-0003 §3-2, "reference documents: drop the
    # lookup-method explanation" + M0009), so the inline section must NOT come back when this branch
    # merges — resolve any conflict here by keeping NO inline lookup section.
    if review_section:
        sections.append(review_section)
    # Review phase suppresses the action:new artifact POST section: the worker must
    # register Qs, not create the next document. The Q POST is embedded in s8_body instead.
    if not review_continuous:
        sections.append(_section("Artifact registration", s5_body))
    sections.append(_section("Scratch directory", s6_body))
    # doc_type guide section — REMOVED (see the Section 7 comment above).
    # NR0003 recency: repeat the no-choices guard at the very bottom so a long
    # prompt does not bury it; recency weighting keeps it in the worker's view.
    # Continuous mode repeats the unmanned directive instead (no Q-registration guard);
    # review phase repeats the "register a Q, do not create a document, wait for go" line.
    if review_continuous:
        sections.append(_section("Reminder", _continuous_review_reminder(base, prev_doc_id_value, locale)))
    elif continuous_mode:
        sections.append(_section("Reminder", _continuous_guide_body(locale, review_mode=False)))
    else:
        sections.append(_section("Reminder", _no_choices_reminder(base, prev_doc_id_value, locale)))

    return "\n\n".join(sections)


def build_mention_from_token_rec(
    token_rec: dict,
    head_type: str,
    head_status: str,
    parent_doc: dict,
    api_base_url: str,
    raw_token: str = "",
    group_recent_docs: Optional[list] = None,
    ref_doc_ids: Optional[list] = None,
    action_scope: str = "new",
    current_review: Optional[dict] = None,
    edit_reason: str = "user_comment",
    locale: str = "ko",
    head_context_doc: Optional[dict] = None,
    continuous: bool = False,
    continuous_review_mode: bool = False,
    work_plan_scope: Optional[dict] = None,
) -> Optional[str]:
    """Generate an R018-enhanced prompt from token_rec + a document record.

    token_rec fields: project, group_id, scratch_dir
    parent_doc fields: doc_id, type_code, seq, title, module, project_id

    group_id format: {project}-{module}-{group} (example: flowgate-server-0001)
    group_recent_docs: recent document list in the group (up to 5). Omit section 4 when None.
    ref_doc_ids: canonical ID list of additional reference documents (T358). No additions when None.
    head_context_doc: the predecessor document the current step builds upon (R0001 / T0004).
        When given and distinct from parent_doc, Section 1 'Document information' reflects it
        instead of the sequence-owning parent. parent_doc still drives target_id / prev_doc_id.
        Same fields as parent_doc (doc_id, type_code, seq, title).
    """
    project = token_rec.get("project", "")
    group_id_full = token_rec.get("group_id", "")  # e.g. flowgate.server.0001
    scratch_dir = token_rec.get("scratch_dir", "")

    # Split group_id: {project}.{module}.{group}
    parts = group_id_full.split(".", 2) if group_id_full else []
    if len(parts) == 3:
        _proj, module, group = parts
    else:
        module = parent_doc.get("module", "none")
        group = group_id_full

    parent_type = parent_doc.get("type_code", "")
    parent_seq = parent_doc.get("seq", 0)
    # parent_doc_id: short form (e.g. "R0001") — doc_id in the DB is canonical;
    # derive this value from type+seq
    parent_doc_id = f"{parent_type}{parent_seq:04d}" if parent_seq else parent_doc.get("doc_id", "")
    parent_canonical_doc_id = parent_doc.get("doc_id", "")
    parent_title = parent_doc.get("title", "")
    parent_doc_number = parent_doc_id

    # Section 1 override: when a distinct predecessor document is supplied, derive its
    # display fields (type / short doc_number / title). When it is the same object as
    # parent_doc (the first-step fallback), leave the override empty so Section 1 keeps
    # showing the sequence-owning R — which is correct when no prior step has produced a
    # document yet.
    head_doc_type = None
    head_doc_number = None
    head_doc_title = None
    if head_context_doc is not None and head_context_doc is not parent_doc:
        hc_type = head_context_doc.get("type_code", "")
        hc_seq = head_context_doc.get("seq", 0)
        head_doc_type = hc_type
        head_doc_number = (
            f"{hc_type}{hc_seq:04d}" if hc_seq else head_context_doc.get("doc_id", "")
        )
        head_doc_title = head_context_doc.get("title", "")

    return build_mention(
        project=project,
        module=module,
        group=group,
        parent_type=parent_type,
        parent_doc_number=parent_doc_number,
        parent_title=parent_title,
        parent_doc_id=parent_doc_id,
        parent_canonical_doc_id=parent_canonical_doc_id,
        head_type=head_type,
        head_status=head_status,
        scratch_dir=scratch_dir,
        raw_token=raw_token,
        api_base_url=api_base_url,
        group_recent_docs=group_recent_docs,
        group_id=group_id_full,
        ref_doc_ids=ref_doc_ids,
        action_scope=action_scope,
        current_review=current_review,
        parent_revision_no=int(parent_doc.get("revision_no") or 0),
        edit_reason=edit_reason,
        locale=locale,
        head_doc_type=head_doc_type,
        head_doc_number=head_doc_number,
        head_doc_title=head_doc_title,
        continuous=continuous,
        continuous_review_mode=continuous_review_mode,
        work_plan_scope=work_plan_scope,
    )


# ── Workflow-decision mention builder ────────────────────────────────────────

def build_workflow_decision_mention(
    *,
    token_rec: dict,
    target_doc: dict,
    api_base_url: str,
    raw_token: str,
    group_recent_docs: Optional[list] = None,
    locale: str = "ko",
    continuous: bool = False,
    continuous_review_mode: bool = False,
) -> str:
    """Build instructions for an AI worker to decide an R workflow.

    Continuous (unmanned) work (group 0086 R0001): when ``continuous`` is set, this
    decision step is the FIRST link of an unmanned chain that starts *before* the
    workflow is decided ("starting from the workflow decision"). The Clarification guide + bottom Reminder
    swap to the delegation/unmanned/no-stop/autonomous block (same one build_mention
    uses), and the worker is told the run continues automatically after the decision is
    saved — so it must decide and submit rather than stopping to ask. The server kicks
    off the next step from the decide response (workflow_decision_service
    .continuation_kickoff_after_decide).
    """
    project = token_rec.get("project", "")
    group_id = token_rec.get("group_id", "")
    parts = group_id.split(".", 2) if group_id else []
    if len(parts) == 3:
        _project, module, group = parts
    else:
        module = target_doc.get("module", "none")
        group = group_id

    doc_id = target_doc.get("doc_id", "")
    doc_type = target_doc.get("type_code", "")
    seq = target_doc.get("seq", 0)
    short_id = f"{doc_type}{seq:04d}" if seq else doc_id
    title = target_doc.get("title", "")
    base = _api_base(api_base_url)

    s1_body = (
        f"project: {project}\n"
        f"module: {module}\n"
        f"group: {group}\n"
        f"type: {doc_type}\n"
        f"type_detail: {get_type_name(doc_type, locale)}\n"
        f"doc_number: {short_id}\n"
        f"title: {title}"
    )
    s2_body = (
        f"Read and analyze the workflow-root document {doc_id}.\n"
        "Choose the workflow sequence needed to address it, then submit the\n"
        "decision through the workflow decision API below. Do not edit the root document and\n"
        "do not create a report document. Report steps (NR/TR/TSR) are attached\n"
        "automatically to each N/T/TS step, so submit only the instruction and design\n"
        f"steps. Valid type codes: GET {base}/help/items/doc_type"
    )
    if continuous and not continuous_review_mode:
        # The decision is the first step of an unmanned chain: after it is saved the
        # server mints the next step's token/mention and encloses it in the decide
        # response (continuation_kickoff_after_decide). The worker must continue from
        # there without stopping.
        s2_body += (
            "\n\nThis is an UNMANNED continuous run started before the workflow was "
            "decided. After you submit the decision, the response will enclose the next "
            "step's token + mention — continue straight to it and keep going until the "
            "chain reports it is done. Do NOT stop after deciding."
        )
    elif continuous and continuous_review_mode:
        # PRE-FLIGHT REVIEW phase (group 0086 TR0004 rework rev5): review mode is "not go
        # yet". Deciding the workflow is allowed (the sequence must exist before it can be
        # reviewed), but the worker must NOT auto-run the deliverables — the decide response
        # encloses the first step's REVIEW mention, which it reviews and answers with a Q.
        # rev4 still appended the non-review "keep going until done, do NOT stop" line here,
        # contradicting review mode; rev5 replaces it with the review-phase instruction.
        s2_body += (
            "\n\nThis is the PRE-FLIGHT REVIEW phase of an UNMANNED run (AI review mode ON) — "
            "it is NOT 'go' yet. Decide the workflow first so the sequence exists to review. "
            "After you submit the decision, the response encloses the FIRST step's review "
            "mention — review that step and register any blocking question as a Q (or, if you "
            "have none, a 'review complete — no blockers — confirm to proceed' Q). Do NOT "
            "create the step's deliverable and do NOT auto-run the chain: it waits for the "
            "human's explicit go (review mode off → the non-review auto-run takes over)."
        )
    s3_body = (
        f"Note: All GET requests require an Authorization: Bearer {raw_token} header\n\n"
        f"{doc_id}: GET {base}/document/{doc_id}"
    )

    # R0001/T0004: clarification/no-choices guide hoisted directly under the
    # document-identity header (was last → ignored).
    # Group 0110 B0001/NR0003: the non-continuous branch previously used inline text
    # that ONLY told the worker to "explain what context is missing", with neither the
    # embedded Q-registration POST nor the "register a Q" guidance the other worker
    # mentions (build_mention/build_review_mention) carry via _clarification_guide_body.
    # That left this mention with the no-choices warning but no non-interactive way to
    # actually get an answer (internal contradiction) and leaked English in ko/ja. We now
    # use the shared, locale-aware helper here too, anchoring the Q on the R-root doc_id —
    # the same doc_ref the workflow_decide token is minted with, which the Q endpoint
    # accepts (NR0003 §feasibility check).
    # Continuous (group 0086): this is the first link of an unmanned chain, so the guide
    # is REPLACED by the delegation/unmanned block — the worker decides autonomously and
    # never stops to ask (consistent with build_mention's continuous branch).
    if continuous:
        clarification_body = _continuous_guide_body(locale, review_mode=continuous_review_mode)
    else:
        clarification_body = _clarification_guide_body(base, doc_id, raw_token, locale)
    sections = [
        _section("Document information", s1_body),
        _section("Clarification guide", clarification_body),
        # group 0372 set 3 (L-0005 §2-10): central help-index block, same slot as
        # build_mention — once, right below the identity + guide blocks.
        _help_index_section(base, raw_token, locale),
    ]
    # 0349 D0004 D-3: a workflow_decide token already resolves to ["read", "grep"] server
    # side, but this mention never said so — the worker deciding a sequence could not read
    # the source it was deciding about, and would not have known it was allowed to.
    if _include_remote_source_crud(project):
        decision_tools = _remote_source_crud_section(
            base, raw_token, doc_type, action_scope="workflow_decide", locale=locale
        )
        if decision_tools:
            sections.append(decision_tools)
    sections.extend([
        _section("Workflow decision instructions", s2_body),
        _section("Reference documents", s3_body),
        # MERGE NOTE (branch base predates group 0370 on main): main inserts an inline
        # "Efficient document lookup" section here — absorbed into the `document_access`
        # help item (D-0003 §3-2); keep NO inline lookup section on merge.
    ])

    # Recent documents in group — REMOVED (D-0003 §3-2 "dropped"), see build_mention's
    # Section 4 comment. group_recent_docs stays accepted for caller compatibility.

    # group 0372 set 3 (D-0003 §3-2, "result registration: kept, minimal"): address + credential only;
    # the request body format/example lives behind the `submit` help item, which the
    # help catalog builds per action_scope (workflow_decide included).
    loc = template_provision.normalize_locale(locale)
    s5_body = (
        f"Submit the workflow decision: POST {base}/workflow/decide\n"
        f"Authorization: Bearer {raw_token}\n"
        f"\n"
        f"{_SUBMIT_BODY_POINTER_TEXT[loc].format(url=f'{base}/help/items/submit')}"
    )
    sections.append(_section("Workflow decision submission", s5_body))
    # doc_type guide section — REMOVED (D-0003 §3-2 "dropped"): absorbed into the help index.
    if continuous:
        sections.append(_section("Reminder", _continuous_guide_body(locale, review_mode=continuous_review_mode)))
    else:
        # Group 0110 B0001/NR0003: recency repeat of the no-choices / Q-registration guard
        # at the bottom of this (long) prompt, matching build_mention/build_review_mention.
        sections.append(_section("Reminder", _no_choices_reminder(base, doc_id, locale)))
    return "\n\n".join(sections)


# ── Sequence-edit mention builder (R0001 group 0208) ──────────────────────────
# Post-decision counterpart of build_workflow_decision_mention: the workflow is ALREADY
# decided, so instead of asking the worker to CHOOSE a sequence this shows it the current
# sequence (locked steps it must not touch + editable pending steps) and asks it to edit the
# pending tail, then submit via PATCH /workflow/sequence. Locked steps are immutable server-
# side; only the pending tail is replaced.

def build_sequence_edit_mention(
    *,
    token_rec: dict,
    target_doc: dict,
    api_base_url: str,
    raw_token: str,
    sequence_items: Optional[list] = None,
    locale: str = "ko",
) -> str:
    """Build instructions for an AI worker to EDIT a decided workflow's pending sequence.

    ``sequence_items`` is the current sequence as ``[{type, label, status}]`` (status one of
    pending / in_progress / done). The worker applies the change autonomously via PATCH
    /workflow/sequence — the same endpoint the human edit modal uses.
    """
    project = token_rec.get("project", "")
    group_id = token_rec.get("group_id", "")
    parts = group_id.split(".", 2) if group_id else []
    if len(parts) == 3:
        _project, module, group = parts
    else:
        module = target_doc.get("module", "none")
        group = group_id

    doc_id = target_doc.get("doc_id", "")
    doc_type = target_doc.get("type_code", "")
    seq = target_doc.get("seq", 0)
    short_id = f"{doc_type}{seq:04d}" if seq else doc_id
    title = target_doc.get("title", "")
    base = _api_base(api_base_url)

    s1_body = (
        f"project: {project}\n"
        f"module: {module}\n"
        f"group: {group}\n"
        f"type: {doc_type}\n"
        f"type_detail: {get_type_name(doc_type, locale)}\n"
        f"doc_number: {short_id}\n"
        f"title: {title}"
    )

    # Current sequence, split into locked (immutable) vs pending (editable).
    items = sequence_items or []
    locked = [it for it in items if (it.get("status") or "") != "pending"]
    pending = [it for it in items if (it.get("status") or "") == "pending"]
    has_pending_metadata = any(
        bool(it.get("note"))
        or it.get("source_doc_id") is not None
        or it.get("source_revision_no") is not None
        # 0444 T0007 (NR0003 §4-6): a row whose only stored value is its provider got no JSON
        # block at all, so the worker had nowhere to return that provider from.
        or it.get("provider_id") is not None
        for it in pending
    )
    pending_json = ""
    metadata_rules = ""
    if has_pending_metadata:
        copy = _SEQUENCE_EDIT_METADATA_COPY[template_provision.normalize_locale(locale)]
        payload = [
            {
                "type": it.get("type", ""),
                "label": it.get("label", ""),
                "note": it.get("note") or "",
                "source_doc_id": it.get("source_doc_id"),
                "source_revision_no": it.get("source_revision_no"),
                "provider_id": it.get("provider_id"),
                "provider_display_name": it.get("provider_display_name"),
            }
            for it in pending
        ]
        pending_json = (
            f"\n\n{copy['json_intro']}\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
        )
        metadata_rules = f"\n{copy['rules']}"

    def _fmt(rows: list) -> str:
        if not rows:
            return "  (none)"
        lines = []
        for i, it in enumerate(rows, start=1):
            tcode = it.get("type", "")
            label = it.get("label", "") or get_type_name(tcode, locale)
            lines.append(f"  {i}. [{tcode}] {label}")
        return "\n".join(lines)

    seq_body = (
        "Locked steps — already done or in progress; you CANNOT change these, they are\n"
        "preserved server-side no matter what you submit:\n"
        f"{_fmt(locked)}\n\n"
        "Pending steps — the editable tail; your submission REPLACES exactly this list:\n"
        f"{_fmt(pending)}{pending_json}"
    )

    s2_body = (
        f"The workflow for {doc_id} is already decided. Edit ONLY the pending (not-yet-started)\n"
        "tail of its sequence — add, remove, or reorder pending steps to match what the work now\n"
        "needs. Locked steps (done / in progress) are immutable and preserved automatically.\n"
        "Submit the FULL replacement pending list (not a diff) through the PATCH below. Report\n"
        "steps (NR/TR/TSR) are attached automatically to each N/T/TS step, so submit only the\n"
        "instruction and design steps. Do NOT edit the root document. Valid type codes:\n"
        f"GET {base}/help/items/doc_type\n"
        "You may not empty a decided workflow that has no locked\n"
        "step — an empty pending list in that case is rejected (invalid_sequence_empty)."
        f"{metadata_rules}"
    )

    s3_body = (
        f"Note: All GET requests require an Authorization: Bearer {raw_token} header\n\n"
        f"{doc_id}: GET {base}/document/{doc_id}\n"
        f"Current sequence: GET {base}/workflow/sequence?doc_id={doc_id}"
    )

    # group 0372 set 3 (D-0003 §3-2, "result registration: kept, minimal"): address + credential only;
    # the PATCH body format lives behind the `submit` help item (workflow_sequence_edit).
    loc = template_provision.normalize_locale(locale)
    s5_body = (
        f"Submit the sequence edit: PATCH {base}/workflow/sequence\n"
        f"Authorization: Bearer {raw_token}\n"
        f"\n"
        f"{_SUBMIT_BODY_POINTER_TEXT[loc].format(url=f'{base}/help/items/submit')}"
    )

    sections = [
        _section("Document information", s1_body),
        _section("Clarification guide", _clarification_guide_body(base, doc_id, raw_token, locale)),
        # group 0372 set 3 (L-0005 §2-10): central help-index block, same slot as
        # build_mention. doc_type guide section — REMOVED (absorbed into the index).
        _help_index_section(base, raw_token, locale),
        _section("Sequence edit instructions", s2_body),
        _section("Current sequence", seq_body),
        _section("Reference documents", s3_body),
        # MERGE NOTE (branch base predates group 0370 on main): keep NO inline
        # "Efficient document lookup" section here on merge — absorbed into the
        # `document_access` help item (D-0003 §3-2).
        _section("Sequence edit submission", s5_body),
        _section("Reminder", _no_choices_reminder(base, doc_id, locale)),
    ]
    return "\n\n".join(sections)


# ── Review-request mention builder ────────────────────────────────────────────
# Distinct genre from build_mention: build_mention hands off CREATING the next
# document (action:new + next_type); this asks a worker to REVIEW an existing
# document and submit a verdict (inbox action:review). No sequence/next_type is
# resolved — the target IS the document, so this works for any non-R doc.

def build_review_mention(
    *,
    token_rec: dict,
    target_doc: dict,
    api_base_url: str,
    raw_token: str = "",
    group_recent_docs: Optional[list] = None,
    ref_doc_ids: Optional[list] = None,
    locale: str = "ko",
) -> Optional[str]:
    """Build a "please review this document" mention.

    token_rec fields: project, group_id, scratch_dir
    target_doc fields: doc_id, type_code, seq, title, module, project_id

    The token must be bound to doc_ref=target_doc.doc_id so inbox _handle_review
    accepts the verdict submission.
    """
    project = token_rec.get("project", "")
    group_id_full = token_rec.get("group_id", "")
    scratch_dir = token_rec.get("scratch_dir", "")

    parts = group_id_full.split(".", 2) if group_id_full else []
    if len(parts) == 3:
        _proj, module, group = parts
    else:
        module = target_doc.get("module", "none")
        group = group_id_full

    doc_type = target_doc.get("type_code", "")
    seq = target_doc.get("seq", 0)
    short_id = f"{doc_type}{seq:04d}" if seq else target_doc.get("doc_id", "")
    canonical_id = target_doc.get("doc_id", "")
    title = target_doc.get("title", "")

    base = _api_base(api_base_url)

    # ── Section 1: document information (the doc under review) ────────────────
    s1_body = (
        f"project: {project}\n"
        f"module: {module}\n"
        f"group: {group}\n"
        f"type: {doc_type}\n"
        f"type_detail: {get_type_name(doc_type, locale)}\n"
        f"doc_number: {short_id}\n"
        f"title: {title}"
    )

    # ── Section 2: review instructions ───────────────────────────────────────
    s2_body = (
        f"Review the target document ({canonical_id}). Read it, evaluate it against its\n"
        "requirements and pass criteria, then submit a verdict (see below).\n"
        "Do NOT modify the document — a review is a child record attached to it; the\n"
        "approve/reject decision is made by a human afterward."
    )

    # ── Section 3: reference documents (target first, then extras) ───────────
    s3_lines = list(_reference_doc_lines(canonical_id, base, locale))
    if ref_doc_ids:
        for d in ref_doc_ids:
            if d == canonical_id:
                continue
            s3_lines.extend(_reference_doc_lines(d, base, locale))
    s3_body = (
        f"Note: All GET requests require an Authorization: Bearer {raw_token} header\n\n"
        + "\n".join(s3_lines)
    )

    # ── Section 4: recent documents in the group — REMOVED (D-0003 §3-2, dropped) ──
    # See build_mention's Section 4 comment; group_recent_docs stays accepted for
    # caller compatibility but is no longer rendered.

    # ── Section 5: review submission (inbox action:review) ────────────────────
    # group 0372 set 3 (D-0003 §3-2, "result registration: kept, minimal"): address + credential only.
    # The action:review body format/example (verdict enum, findings shape) lives behind
    # the `submit` help item, which builds the review contract for review tokens; the
    # Verdict guide section below keeps the verdict semantics inline (L-0006 §2-5 keeps
    # that purpose-specific slot, DEFERRED to unify).
    loc = template_provision.normalize_locale(locale)
    s5_body = (
        f"Submit your review: POST {base}/inbox\n"
        f"Authorization: Bearer {raw_token}\n"
        "\n"
        f"{_SUBMIT_POINTER_TEXT[loc].format(url=f'{base}/help/items/submit')}\n"
        f"{_REVIEW_FILE_SUBMIT_TEXT[loc]}"
    )

    # ── Section 6: scratch directory ─────────────────────────────────────────
    # Review workers share the FlowGate group-worktree filesystem as well. Keep
    # the same {SCRATCH} terminology used by task/test worker instructions.
    s6_body = (
        f"{scratch_dir}\n\n"
        "`{SCRATCH}` means the token-owned path above. Keep temporary review "
        "artifacts inside this directory."
    )

    # ── Section 7: verdict guide (inline — no help endpoint for verdicts) ────
    s7_body = (
        "verdict values:\n"
        "- pass   : meets requirements, no blocking issues\n"
        "- issues : defects found; list each one in findings (locus + note)\n"
        "- hold   : cannot decide yet (missing context / blocked)\n"
        "\n"
        "findings is a list of {locus, note}. The server counts them — do not write counts yourself."
    )

    # ── Clarification guide (embedded query POST + no-choices guard; hoisted to top in assembly) ──
    # The review token is bound to the target document, so anchor the embedded query
    # POST at canonical_id. See _clarification_guide_body (B0001 / NR0003).
    s8_body = _clarification_guide_body(base, canonical_id, raw_token, locale)

    sections = [
        _section("Document information", s1_body),
        # R0001/T0004: hoist the clarification guide (with the no-choices guard)
        # directly under the document-identity header so it is actually read.
        _section("Clarification guide", s8_body),
        # group 0372 set 3 (L-0005 §2-10): central help-index block, same slot as
        # build_mention.
        _help_index_section(base, raw_token, locale),
    ]
    # 0349 D0004 D-3: a review token resolves to ["read", "grep"] server side. Reviewing a
    # TR against the source it claims to have changed needs exactly that, and the mention
    # withholding it made the reviewer take the report's word for it.
    if _include_remote_source_crud(project):
        review_tools = _remote_source_crud_section(
            base, raw_token, doc_type, action_scope="review", locale=locale
        )
        if review_tools:
            sections.append(review_tools)
    sections.extend([
        _section("Review instructions", s2_body),
        _section("Reference documents", s3_body),
        # MERGE NOTE (branch base predates group 0370 on main): keep NO inline
        # "Efficient document lookup" section here on merge — absorbed into the
        # `document_access` help item (D-0003 §3-2).
    ])
    sections.append(_section("Review submission", s5_body))
    sections.append(_section("Scratch directory", s6_body))
    sections.append(_section("Verdict guide", s7_body))
    # NR0003 recency: repeat the no-choices guard at the very bottom (see build_mention).
    sections.append(_section("Reminder", _no_choices_reminder(base, canonical_id, locale)))

    return "\n\n".join(sections)
def build_work_plan_fill_mention(
    *,
    token_rec: dict,
    target_doc: dict,
    body: dict,
    scope: dict,
    api_base_url: str,
    raw_token: str,
    locale: str = "ko",
) -> str:
    """Build the bounded work-plan edit prompt used by the in-app AI runner."""
    import json

    language = locale if locale in {"ko", "en", "ja"} else "ko"
    copy = {
        "ko": {
            "title": "작업계획 범위 채우기",
            "canonical": "본문은 Markdown이 아니라 아래 정본 JSON 전체입니다.",
            "quantity": "수량을 정해도 되는 타입",
            "quantity_basis": "범위 안 타입도 수량을 1로 추측해 올리지 마십시오. 이 문서·참조 문서·workflow_type_counts에서 근거를 찾아 정하고, 근거가 없으면 지금 값을 유지하거나 0으로 두십시오.",
            "steps": "프로바이더와 한줄 멘트를 정해도 되는 단계",
            "providers": "고를 수 있는 프로바이더",
            "outside": "범위 밖 값은 지금 값 그대로 두십시오.",
            "notes": "선택된 모든 비잠금 단계의 note를 반드시 채우십시오(200자 이내, 줄바꿈·탭 금지).",
            "submit": "수정한 정본 JSON 전체를 인박스 수정(edit) 제출로 되돌려 주십시오.",
        },
        "en": {
            "title": "Fill a bounded work-plan scope",
            "canonical": "The body is the complete canonical JSON below, not Markdown.",
            "quantity": "Types whose quantities may change",
            "quantity_basis": "Even for types inside this scope, do not bump the quantity to 1 by guesswork. Decide it from this document, the referenced documents, or a supplied workflow_type_counts value; when there is no basis, keep the current value or leave it at 0.",
            "steps": "Steps whose provider and one-line note may change",
            "providers": "Providers that may be chosen",
            "outside": "Keep every value outside this scope exactly as it is.",
            "notes": "Fill note for every selected unlocked step (at most 200 characters; no newlines or tabs).",
            "submit": "Return the complete canonical JSON through an inbox edit submission.",
        },
        "ja": {
            "title": "作業計画の指定範囲を入力",
            "canonical": "本文はMarkdownではなく、以下の正本JSON全体です。",
            "quantity": "数量を変更できるタイプ",
            "quantity_basis": "範囲内のタイプでも、数量を根拠なく 1 に引き上げないでください。この文書・参照文書・workflow_type_counts の根拠から数量を決め、根拠がなければ現在の値を維持するか 0 のままにしてください。",
            "steps": "プロバイダーと一行メモを変更できる段階",
            "providers": "選択できるプロバイダー",
            "outside": "範囲外の値は現在のまま変更しないでください。",
            "notes": "選択したロックなし段階のnoteを必ず入力してください（200文字以内、改行・タブ禁止）。",
            "submit": "正本JSON全体をインボックスのedit提出で返してください。",
        },
    }[language]
    step_by_key = {str(step.get("key")): step for step in body.get("steps") or []}
    provider_by_id = {
        str(provider.get("provider_id")): provider
        for provider in body.get("provider_candidates") or []
    }
    # 0411 T0004 (B0001): a step provider may now sit outside the candidates — merely registered
    # in this project. Asking only the candidate snapshot for a name would send such an id into
    # the worker mention as a raw id, leaving the worker unable to tell who to pick. Lookup order
    # is snapshot (the name frozen at pick time) → registered list (current name) → id. Unreadable settings fall back to the id as before.
    registered_names: dict[str, str] = {}
    try:
        from modules.flow_gate.settings import ai_settings_service

        effective = ai_settings_service.resolve_effective(target_doc.get("project_id") or "")
        for provider in (effective or {}).get("providers") or []:
            if provider.get("id"):
                registered_names[str(provider["id"])] = str(provider.get("name") or "")
    except Exception:  # noqa: BLE001 — an unreadable provider list must not break the prompt
        registered_names = {}
    quantities = body.get("quantities") or {}
    unit_copy = {
        "ko": {"sheet": "장", "set": "세트"},
        "en": {"sheet": "sheet", "set": "set"},
        "ja": {"sheet": "枚", "set": "セット"},
    }[language]

    def step_label(key: str) -> str:
        step = step_by_key.get(key) or {}
        type_code = str(step.get("type") or "")
        name = get_type_name(type_code, language) if type_code else "?"
        quantity = quantities.get(type_code)
        if not quantity and step.get("pair_key"):
            quantity = quantities.get(str(step["pair_key"]).split("#", 1)[0])
        ordinal = step.get("ordinal")
        unit = unit_copy.get((quantity or {}).get("unit"))
        if ordinal and unit:
            suffix = f"{ordinal} {unit}" if language == "en" else f"{ordinal}{unit}"
            name = f"{name} {suffix}"
        return f"{key} · {name}"

    quantity_lines = scope.get("quantity_type_codes") or []
    step_lines = [step_label(key) for key in (scope.get("step_keys") or [])]
    def provider_name(provider_id: str) -> str:
        snapshot = (provider_by_id.get(provider_id) or {}).get("display_name")
        return snapshot or registered_names.get(provider_id) or provider_id

    provider_lines = [
        f"{provider_id} · {provider_name(provider_id)}"
        for provider_id in scope.get("provider_ids") or []
    ]
    def bullets(items: list[str]) -> str:
        return "\n".join(f"- {item}" for item in items) if items else "- (none)"

    doc_id = target_doc.get("doc_id") or ""
    scratch_dir = token_rec.get("scratch_dir") or ""
    return (
        f"## {copy['title']}\n\n"
        f"- target_doc_id: {doc_id}\n"
        f"- design template: GET {api_base_url}/flowgate/api/v1/help/items/design_template/WP\n"
        f"- Authorization: Bearer {raw_token}\n"
        f"- scratch_dir: {scratch_dir}\n\n"
        f"{copy['canonical']}\n\n"
        f"```json\n{json.dumps(body, ensure_ascii=False, indent=2)}\n```\n\n"
        f"### {copy['quantity']}\n\n{bullets(quantity_lines)}\n\n"
        f"{copy['quantity_basis']}\n\n"
        f"### {copy['steps']}\n\n{bullets(step_lines)}\n\n"
        f"### {copy['providers']}\n\n{bullets(provider_lines)}\n\n"
        f"{copy['outside']}\n\n{copy['notes']}\n\n{copy['submit']}\n"
        f"POST {api_base_url}/flowgate/api/v1/inbox (action=edit, doc_id={doc_id})\n"
    )
