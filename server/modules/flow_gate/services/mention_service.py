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
  2. Clarification guide      (embedded query-registration POST + no-choices guard)
  3. Instruction to include the next document header
  4. Reference documents      (one line each for head + selected: {slash-path}: GET {url})
  5. Recent documents in the group  (omit the section when there are 0)
  6. Artifact registration    (includes a complete POST example)
  7. Scratch directory        (inactive)
  8. doc_type guide           (GET /api/v1/help/doc_type)
  9. Reminder                 (the no-choices guard repeated for recency)

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
from modules.flow_gate.services import engine_recipe_service
from modules.flow_gate.services import tr_scope_service

logger = logging.getLogger(__name__)


# Investigation-only document types — they produce findings/reports, never code.
# See R0013 (flowgate.default.0013.0001-R): workers asked to produce an
# investigation document were implementing code on their own. The mention must
# state the scope boundary explicitly; implementation belongs in a T (work-instruction) doc.
_INVESTIGATION_ONLY_TYPES = {"N", "NR"}
_REMOTE_MUTATING_WORK_TYPES = {"T", "TR"}


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


def _remote_source_crud_section(base: str, raw_token: str, step_type: str) -> str:
    """Return the remote project-source API guide for worker mentions.

    T/TR workers receive full source CRUD. Other document types receive read/search
    guidance only, so investigation/review/design mentions do not contradict their
    scope boundary by advertising mutating source operations.
    """
    if not raw_token:
        return ""

    def _json(data: dict) -> str:
        return json.dumps(data, ensure_ascii=False, indent=2)

    mutating = step_type in _REMOTE_MUTATING_WORK_TYPES
    lines = [
        "Use these endpoints when you need to inspect or change the remote project's source tree.",
        "All paths are project-source-root relative; do not send absolute paths or '..' segments.",
        f"Authorization: Bearer {raw_token}",
        "",
        f"Read file: POST {base}/remote/read",
        _json({"path": "app/main.py", "max_bytes": 20000, "encoding": "utf-8"}),
        "",
        f"Search text: POST {base}/remote/grep",
        _json({"pattern": "TODO", "path": "", "glob": "**/*.py", "ignore_case": True, "max_results": 20}),
        "",
        f"List files: POST {base}/remote/glob",
        _json({"path": "", "pattern": "**/*.py"}),
    ]
    if mutating:
        lines.extend([
            "",
            f"Create or replace file: POST {base}/remote/write",
            _json({"path": "app/main.py", "content": "<complete file content>", "mode": "create|overwrite|append", "encoding": "utf-8"}),
            "",
            f"Delete file: POST {base}/remote/remove",
            _json({"path": "app/obsolete.py"}),
            "",
            "After write/remove succeeds, summarize the changed source files in the task report.",
        ])
    else:
        lines.extend([
            "",
            "This document type is read/search only for source access. Do not call write/remove in this step.",
        ])
    return _section("Remote project source CRUD", "\n".join(lines))


def _include_remote_source_crud(project: str) -> bool:
    try:
        return source_mode_service.include_remote_api_section(project)
    except Exception:
        logger.warning("source mode resolution failed; falling back to remote mode", exc_info=True)
        return True


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

# Placeholder question (title/body/options) the worker copies into the embedded POST.
# `options` is optional — a Q without it is exactly the pre-extension Q (L0008 §2.6). It is
# shown here so the copy-paste POST itself advertises the field the prescription tells the
# worker to use, instead of only naming it in prose.
_Q_PLACEHOLDER = {
    "ko": {
        "title": "<짧은 제목 / short title>",
        "body": (
            "<무엇이 모호한지 + 진행하려면 무엇이 필요한지 / "
            "what is ambiguous and what you need in order to proceed>"
        ),
        "options": ["<옵션1 / option 1 — 선택 · 없으면 생략>", "<옵션2 / option 2>"],
    },
    "ja": {
        "title": "<短いタイトル / short title>",
        "body": (
            "<何が曖昧か + 進めるために何が必要か / "
            "what is ambiguous and what you need in order to proceed>"
        ),
        "options": ["<選択肢1 / option 1 — 任意 · 不要なら省略>", "<選択肢2 / option 2>"],
    },
    "en": {
        "title": "<short title>",
        "body": "<what is ambiguous and what you need in order to proceed>",
        "options": ["<option 1 — optional, omit if not needed>", "<option 2>"],
    },
}

# Lead-in (3 lines) / no-choices warning / positive "write a Q" redirect, per locale.
_CLARIFY_TEXT = {
    "ko": {
        "lead": (
            "불명확한 점이 있으면 추측하거나 가정으로 진행하지 마십시오.\n"
            "질문은 해당 문서에 바인딩된 질의 데이터로 등록하십시오 (this is NOT a Q document):\n"
            "질문을 채워 아래의 즉시 사용 가능한 POST를 그대로 전송하십시오."
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
            "質問を記入し、下記のそのまま使えるPOSTを送信してください。"
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
            "fill in the question(s) and send the ready-to-use POST below as-is."
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
#   - 모든 권한 위임 (all authority delegated to the worker)
#   - 무인 운영 (unmanned operation — no human is watching)
#   - 작업 중단 금지 (do not stop the work mid-chain)
#   - 모를 경우 자율 판단 (when unsure, decide autonomously rather than asking)
# This deliberately relaxes the FlowGate Q-gate, which is why the FE warning dialog
# (NR0003 §4-5) makes the user accept "결과 무책임 · 품질은 정보량에 의존" before a
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
        "- If something is unclear, do NOT present choices or halt with a question; make the "
        "most reasonable call with the information you have and proceed autonomously.\n"
        "- Output quality depends on the amount of information provided. The user accepted "
        "this and started the continuous run."
    ),
}


# AI review mode (group 0086 R0001 — TR0004 rework rev4): a continuous run the user launched
# with [AI 검토 모드] ON. Review mode is NOT "go" yet — it is the PRE-FLIGHT Q-registration
# phase. Before the unmanned auto-run starts, the worker reads everything and registers any
# clarifying questions (Q); it does NOT produce the next document and does NOT advance the
# chain. The "create the next document (action:new)" guidance is therefore REMOVED in this
# phase and replaced by Q-registration guidance (reviewer feedback: "검토모드=아직 go가 아니라
# 사전 질의등록 시간이다. 이때는 new안내를 빼고 Q안내가 나와야 한다").
#
# No-Q case (reviewer asked for ideas): if the worker has nothing to ask, it must still NOT
# auto-proceed — instead it registers a single "검토 완료 · 막는 의문 없음 · 진행 확인 요청"
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

    ``review_mode`` (group 0086 R0001 [AI 검토 모드]): in review mode this returns the
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
    """Pre-flight review block + a ready-to-send Q POST (group 0086 TR0004 rework rev4).

    Review mode replaces the action:new "Artifact registration" guidance with Q registration.
    The embedded POST is anchored at the worker's bound document and uses the worker token,
    so the worker can register either a real clarifying Q or the no-blockers acknowledgement Q.
    """
    loc = template_provision.normalize_locale(locale)
    ph = _Q_PLACEHOLDER[loc]
    q_post_body = {
        "asker_kind": "ai",
        "questions": [{"title": ph["title"], "body": ph["body"], "options": ph["options"]}],
    }
    q_json = json.dumps(q_post_body, ensure_ascii=False, indent=2)
    return (
        f"{_CONTINUOUS_REVIEW_TEXT[loc]}\n"
        "\n"
        f"POST {base}/q/{anchor_doc_id}/questions\n"
        f"Authorization: Bearer {raw_token}\n"
        "\n"
        f"{q_json}"
    )


def _continuous_review_reminder(base: str, anchor_doc_id: str, locale: str = "ko") -> str:
    loc = template_provision.normalize_locale(locale)
    post = f"{base}/q/{anchor_doc_id}/questions"
    return _CONTINUOUS_REVIEW_REMINDER[loc].format(post=post)


def _clarification_guide_body(
    base: str, anchor_doc_id: str, raw_token: str, locale: str = "ko"
) -> str:
    loc = template_provision.normalize_locale(locale)
    ph = _Q_PLACEHOLDER[loc]
    txt = _CLARIFY_TEXT[loc]
    q_post_body = {
        "asker_kind": "ai",
        "questions": [{"title": ph["title"], "body": ph["body"], "options": ph["options"]}],
    }
    q_json = json.dumps(q_post_body, ensure_ascii=False, indent=2)
    return (
        f"{txt['lead']}\n"
        "\n"
        f"POST {base}/q/{anchor_doc_id}/questions\n"
        f"Authorization: Bearer {raw_token}\n"
        "\n"
        f"{q_json}\n"
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

# TS is authored by the worker (excluded from auto-instruction), and FlowGate runs
# it remotely from the project source root. Without this block the worker receives a
# generic new-document mention and the TS it writes fails parse_test_plan
# (no_test_cases / invalid_case_block). The three H2 headers are Korean literals
# because test_run_service.parse_test_plan matches them verbatim, regardless of locale.
_TS_AUTHORING_TYPES = {"TS"}


def _ts_authoring_section() -> str:
    host_os = test_command_service.current_os()
    body = (
        "Write this TS as an executable spec. FlowGate runs it remotely from the project\n"
        "source root — do NOT assume any locally-running service. Use three H2 sections in\n"
        "this order:\n\n"
        "## 테스트 준비        (optional; runs first, in order)\n"
        "- cmd: <shell command, single line>            # setup step\n"
        "- 기동: <server start command, backgrounded>   # long-lived service\n"
        "- 대기: {PORT}                                  # wait until 127.0.0.1:{PORT} accepts\n\n"
        "## 테스트 케이스       (required; at least one case)\n"
        "### TC-1: <title>\n"
        "- cmd: <single-line command; PASS iff exit code 0>\n"
        "- 기대: <expected behavior, human-readable>\n\n"
        "## 테스트 정리        (optional; always runs, even on failure)\n"
        "- cmd: <cleanup command>\n\n"
        "Placeholders — {PORT}: port FlowGate allocates (also env FLOWGATE_TEST_PORT);\n"
        "{SCRATCH}: per-run scratch dir, deleted afterward (env FLOWGATE_TEST_SCRATCH).\n"
        "Commit the actual test code to the repo in this step (no auto-generation).\n"
        "Limits: at most 50 cases, 20 setup/teardown steps, 5 services. Verdict is exit-0 only.\n\n"
        "Framework-agnostic: the ONLY verdict is the process exit code (0 = pass). Any\n"
        "runner works — pytest, `npm test`, `npx vitest run`, `go test`, `cargo test`, a bare\n"
        "script. This is NOT Python-only; pick whatever matches the code under test.\n\n"
        + _ts_host_shell_guidance(host_os) +
        "Because cmd runs at the SOURCE ROOT, cd into the subproject first. Example — the\n"
        "frontend Vitest suite (lives in client/, config at client/vitest.config.ts):\n\n"
        "## 테스트 준비\n"
        "- cmd: cd client && npm install          # installs vitest; use install, NOT `npm ci` (esbuild lock)\n"
        "## 테스트 케이스\n"
        "### TC-1: frontend unit suite is green\n"
        "- cmd: cd client && npm test             # == `vitest run`; PASS iff exit 0\n"
        "- 기대: all Vitest specs pass (exit 0)\n"
        "### TC-2: frontend type check is clean\n"
        "- cmd: cd client && npm run typecheck    # == `vue-tsc -b`; PASS iff exit 0\n"
        "- 기대: no TS errors (exit 0)\n\n"
        "Pair the two whenever a change touches the client: Vitest transpiles without type\n"
        "checking, so a type error passes `npm test` and only surfaces in the deploy build\n"
        "(flowgate.default.0300 B0001 -> NR0003 §4). The typecheck case closes that gap.\n\n"
        "(`cd X && <runner>` is the one chaining form that works on both cmd.exe and /bin/sh,\n"
        "which is why the example uses it.)\n\n"
        "Node/npm must be on the FlowGate host PATH for JS runners; a fresh source tree has\n"
        "no node_modules, so the install setup step above is required."
    )
    return _section("Test scenario authoring (TS)", body)


# Host-shell guidance (flowgate.default.0277 B0001 -> NR0003 §4 F1).
# Every cmd is spawned with shell=True, so it is interpreted by %COMSPEC% (cmd.exe) on
# Windows and /bin/sh on POSIX. The guide never said which, so workers defaulted to POSIX
# and their commands failed outright once FlowGate moved to a Windows host. State the
# actual host shell and name the idioms that do not survive it.
_TS_WINDOWS_SHELL_GUIDANCE = (
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
)

_TS_POSIX_SHELL_GUIDANCE = (
    "HOST SHELL — this FlowGate host is POSIX (os.name=posix). Every cmd is interpreted by\n"
    "/bin/sh (NOT bash): avoid bashisms such as [[ ]], arrays, and `source` (use `.`).\n"
    "Windows-only syntax (%VAR%, `set VAR=x`, backslash paths, `2>nul`) will not work.\n"
    "Prefer language-level runners (python -m pytest, npm test, go test) over shell builtins —\n"
    "they keep the TS portable if this project is ever moved to a Windows host.\n\n"
)


def _ts_host_shell_guidance(host_os: str) -> str:
    """The shell-specific do/don't block, chosen from the host FlowGate actually runs on."""
    if host_os == test_command_service.OS_WINDOWS:
        return _TS_WINDOWS_SHELL_GUIDANCE
    return _TS_POSIX_SHELL_GUIDANCE


# ── N/T instruction authoring (group 0230 R0001 / T0005 WI-7) ────────────────────
# When a continuous run chooses "AI 직접 작성" (continuation_instruction_mode = ai_direct),
# advance_workflow SKIPS the server-side auto-complete of N/T instruction heads, so the head
# reaches the worker mention as an N or T. Without a dedicated guide the worker would receive a
# generic new-document mention and might mis-scope the instruction (e.g. an N that fails to name
# what to investigate, or a T with no acceptance criteria). This section tells the worker how to
# AUTHOR the instruction document itself — mirroring _ts_authoring_section's role for TS.
#
# In the default (auto_approved) path N/T are auto-completed server-side and never reach this
# code, so the section is only ever emitted when the flag is on — no regression to managed runs.
_NT_AUTHORING_TYPES = {"N", "T"}


def _nt_authoring_section(scope_type: str) -> str:
    """Authoring guidance for an instruction document the worker writes directly (N or T)."""
    stype = (scope_type or "").upper()
    if stype == "N":
        body = (
            "You are authoring this N (조사지시 / investigation-instruction) directly instead of\n"
            "the server emitting a fixed template. Reflect the actual context of this group and\n"
            "the requirement it serves. A good N does NOT investigate or implement anything — it\n"
            "DIRECTS the investigation the paired NR will carry out. Cover:\n"
            "- 목적/배경: why this investigation is needed (tie it to the driving R/B).\n"
            "- 조사 범위: the concrete questions the NR must answer + the code/areas to inspect.\n"
            "- 산출물 기대: what the NR should conclude (root cause, coordinates, reuse anchors).\n"
            "Keep it an instruction: name what to find out, not the findings themselves — those\n"
            "belong in the NR that follows. Do NOT modify source code in this step."
        )
    else:  # T (and any other instruction head routed here)
        body = (
            "You are authoring this T (작업지시 / work-instruction) directly instead of the server\n"
            "emitting a fixed template. Reflect the actual context of this group and the findings\n"
            "of the preceding investigation. A good T DIRECTS the work the paired TR will carry\n"
            "out. Cover:\n"
            "- 목적/범위: what change is being made and why (tie it to the driving R/B + NR).\n"
            "- 작업 항목: the concrete, ordered work items (files/areas to touch, the approach).\n"
            "- 완료 기준: how the TR proves it is done (tests to run GREEN, acceptance checks).\n"
            "Keep it an instruction: direct the work; the implementation + evidence belong in the\n"
            "TR that follows. Do NOT implement the code in this step — write the directive."
        )
    body += (
        "\n\nInclude the next-document header exactly as given in the 'Instruction to include next\n"
        "document header' section above (next_type / project / module / group / title / target_id).\n"
        "On submit this instruction is auto-approved (non-{M,CH}) like any managed instruction and\n"
        "the unmanned chain proceeds to its paired report step."
    )
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
    # Continuous AI-review mode (group 0086 R0001 [AI 검토 모드]). When True (with
    # ``continuous``), the replacement block is the review variant that keeps the Q
    # latitude (scrutinise → Q-if-blocked → else proceed) instead of "never stop, never ask".
    continuous_review_mode: bool = False,
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
    # Review phase (group 0086 R0001 — TR0004 rework rev4): a continuous run with [AI 검토 모드]
    # ON is the pre-flight Q-registration phase, not "go". All "create the next document"
    # guidance (the "new안내") is removed so the worker only registers Qs.
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
    # A worker whose head/target is a TS must be told the 3-section case-block grammar
    # and {PORT}/{SCRATCH} conventions, else the TS it writes cannot be parsed/run.
    ts_authoring_section = ""
    if scope_type in _TS_AUTHORING_TYPES:
        ts_authoring_section = _ts_authoring_section()

    # ── N/T instruction authoring guidance (group 0230 R0001 / T0005 WI-7) ────
    # Only reached when the run chose ai_direct: in auto_approved mode N/T are
    # auto-completed server-side and never arrive here as a worker mention.
    nt_authoring_section = ""
    if scope_type in _NT_AUTHORING_TYPES:
        nt_authoring_section = _nt_authoring_section(scope_type)

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
        doc_url = f"{base}/document/{doc_dot_dash_path}"
        s3_lines.append(f"{doc_dot_dash_path}: GET {doc_url}")
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

    # ── Section 4: recent documents in the group (omit when there are 0) ─────
    s4_section = ""
    if group_recent_docs:
        count = len(group_recent_docs)
        lines_doc = [f"Recent documents in group (relative to {parent_doc_id}, {count} items):"]
        oldest_canonical_id = ""
        for item in group_recent_docs:
            seq = item.get("seq")
            short_id = (
                f"{item['doc_type']}{seq:04d}" if seq else item.get("doc_id", "")
            )
            doc_type = item.get("doc_type", "")
            title = item.get("title", "")
            status = item.get("status", "")
            type_label = get_type_name(doc_type, locale) if doc_type else ""
            lines_doc.append(f"- {short_id}  [{doc_type}] {type_label}  {title} ({status})")
            oldest_canonical_id = item.get("doc_id", short_id)

        nav_gid = group_id or f"{project}-{module}-{group}"
        lines_doc.append("")
        lines_doc.append("To browse earlier documents:")
        lines_doc.append(
            f"GET {base}/list/groups/{nav_gid}/documents?before={oldest_canonical_id}&limit=5"
        )
        s4_section = "\n" + _section("Recent documents in group", "\n".join(lines_doc))

    # ── Section 5: artifact registration (complete POST example) ─────────────
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
        }
        # Rejection rework: the resubmission must carry the worker's response to the
        # rejection. Surfacing `rejection_response` here in the copy mention is the
        # ONLY thing that prompts the AI to send it — the server (inbox _handle_edit
        # → record_rejection_response) then attaches it to the latest rejection item
        # for read-only display. Without this field the response is never collected,
        # so the reviewer sees no "response content" and cannot tell what changed.
        if edit_reason == "rejected":
            post_body["rejection_response"] = _REJECTION_RESPONSE_PLACEHOLDER[
                template_provision.normalize_locale(locale)
            ]
    else:
        doc_type_value = head_type if head_type else "<Sequence undecided>"
        post_body = {
            "action": "new",
            "project": project,
            "module": module,
            "group_name": group_name_value,
            "doc_type": doc_type_value,
        }
        if prev_doc_id_value:
            post_body["prev_doc_id"] = prev_doc_id_value
        post_body["title"] = "<Fill this in>"
        # TR 작업범위 검증 (0299 D0004 §3.9): TR 은 content 자리에 빈 `## 변경 파일`
        # 섹션을 미리 넣어 둔다. 칸이 있으면 채우고, 없으면 안내를 읽어도 빠뜨린다 —
        # 이 placeholder 가 T1 의 "TR 서식에 빈 섹션 추가"에 해당한다.
        if str(doc_type_value).upper() == "TR":
            post_body["content"] = (
                "<Fill this in>\n\n"
                + tr_scope_service.TR_SECTION_PLACEHOLDER
            )
        else:
            post_body["content"] = "<Fill this in>"
        # TR commit-message draft (flowgate.default.0173 D0002 §2 / P0003 §1): the TR
        # worker understands the work in English, so it supplies the commit subject at
        # report time. Optional and TR-only; the server ignores it for other types.
        if str(doc_type_value).upper() == "TR":
            post_body["commit_message"] = (
                "<Optional. English one-line commit subject summarizing this group's "
                "work, e.g. fix(git): preserve finalized commit subject>"
            )

    post_json = json.dumps(post_body, ensure_ascii=False, indent=2)
    commit_hint = ""
    # 0299: 재제출(edit)도 작업범위 검증을 거친다. 재작업 지시에 형식 안내가 빠져 있으면
    # 반려된 작업자가 형식을 모르는 채로 다시 제출해 두 번째 반려를 맞는다.
    if is_edit and str(parent_type or "").upper() == "TR":
        commit_hint = f"\n{tr_scope_service.TR_SECTION_GUIDE}"
    if not is_edit and str(head_type or "").upper() == "TR":
        commit_hint = (
            "\nThe optional `commit_message` is an English one-line commit subject "
            "(<=200 chars) that becomes the finalize commit for this group. Write it "
            "in the Conventional Commits form and in English; omit it if unsure.\n"
            # 0299 D0004 §3.9: 작업 지시가 검증의 전제다. 형식 안내가 먼저 나가야
            # 대조할 대상이 생기고, 반려당한 뒤에 처음 형식을 배우는 일이 없다.
            f"\n{tr_scope_service.TR_SECTION_GUIDE}"
        )
    s5_body = (
        f"Artifact registration: POST {base}/inbox\n"
        f"Authorization: Bearer {raw_token}\n"
        f"\n"
        f"{post_json}\n"
        f"{commit_hint}"
        f"\n"
        f"{_DRYRUN_HINT}"
    )

    # ── Section 6: scratch directory ──────────────────────────────────────────
    # Disabled: remote HTTP consumers cannot access the server host's local paths. Restore when a command-invocation mode is introduced.
    # s6_body = scratch_dir

    # ── Section 7: doc_type guide ────────────────────────────────────────────
    s7_body = f"GET {base}/help/doc_type"

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
    # Q-registration guide and the action:new "Artifact registration" section (the "new안내") is
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

    # ── Document template (group 0024): push the DB-held design template BODY ──
    # The remote worker authors design docs (D/P/L/DB) from a standard template.
    # The G1 query API is session/RBAC-scoped (admin/UI) so a worker bearer token
    # cannot pull it (401); delivery for the worker is therefore push-via-mention.
    # For a new hand-off the template is for the document being created (head_type);
    # for an in-place edit it is for the document being revised (parent_type).
    # Non-design types embed nothing (their file-path pointers are out of scope).
    # Resolution touches the DB and must never abort mention generation — any
    # failure degrades silently to no section (writing still proceeds from skeleton).
    template_section = ""
    tmpl_type = parent_type if is_edit else head_type
    if tmpl_type:
        try:
            if template_provision.is_design_type(tmpl_type):
                req_locale = template_provision.normalize_locale(locale)
                resolved = template_provision.resolve_active_template(
                    project, tmpl_type, req_locale
                )
                template_section = _section(
                    "Document template",
                    template_provision.render_provision_block(tmpl_type, req_locale, resolved),
                )
        except Exception:  # never let template provision break the mention
            logger.warning(
                "template provision failed for type=%s; mention degrades without it",
                tmpl_type, exc_info=True,
            )
            template_section = ""

    source_crud_section = ""
    if _include_remote_source_crud(project):
        source_crud_section = _remote_source_crud_section(base, raw_token, scope_type)

    # ── Assembly ──────────────────────────────────────────────────────────────
    sections = [
        _section("Document information", s1_body),
        # R0001/T0004: the clarification guide is hoisted directly under the
        # document-identity header (was last → ignored). The no-choices guard lives
        # inside s8_body so workers stop offering options the remote run can't answer.
        # In continuous mode the header/body swap to the unmanned work block (s8_header).
        _section(s8_header, s8_body),
        _section(s2_header, s2_body),
    ]
    if scope_section:
        sections.append(scope_section)
    if nt_authoring_section:
        sections.append(nt_authoring_section)
    if ts_authoring_section:
        sections.append(ts_authoring_section)
        # flowgate.default.0152: right after the TS authoring guide, list this project's
        # verified test commands so the worker prefers known-good commands over guessing.
        # Omitted when the project has none (build_* returns ""); never breaks mention build.
        try:
            verified_commands_body = test_command_service.build_verified_commands_block(project)
        except Exception:
            logger.warning("verified test-command block failed", exc_info=True)
            verified_commands_body = ""
        if verified_commands_body:
            sections.append(
                _section(f"Verified test commands (project: {project})", verified_commands_body)
            )
        # flowgate.default.0157: the engine-recipe help pointer, right after the verified commands.
        # Always emitted when the TS authoring guide is present — even with zero recipes — so the first
        # help call teaches the registry (L §2-7). No per-language rules (0156.0002-CH). Never breaks build.
        try:
            engine_recipes_body = engine_recipe_service.build_engine_recipes_block(base)
        except Exception:
            logger.warning("engine recipes block failed", exc_info=True)
            engine_recipes_body = ""
        if engine_recipes_body:
            sections.append(_section("Engine recipes (environment setup)", engine_recipes_body))
    if template_section:
        sections.append(template_section)
    if source_crud_section:
        sections.append(source_crud_section)
    sections.append(_section("Reference documents", s3_body))
    if review_section:
        sections.append(review_section)
    if s4_section:
        # s4_section already includes a leading "\n" + section
        sections.append(s4_section.lstrip("\n"))
    # Review phase suppresses the action:new artifact POST (the "new안내"): the worker must
    # register Qs, not create the next document. The Q POST is embedded in s8_body instead.
    if not review_continuous:
        sections.append(_section("Artifact registration", s5_body))
    # sections.append(_section("Scratch directory", s6_body))  # disabled (see §6 above)
    sections.append(_section("doc_type guide", s7_body))
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
    workflow is decided ("워크플로 결정부터"). The Clarification guide + bottom Reminder
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
        "steps. Use the document type guide for valid type codes."
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
            "\n\nThis is the PRE-FLIGHT REVIEW phase of an UNMANNED run ([AI 검토 모드] ON) — "
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
    # accepts (NR0003 §타당성 검증).
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
        _section("Workflow decision instructions", s2_body),
        _section("Reference documents", s3_body),
    ]

    if group_recent_docs:
        lines = [
            f"Recent documents in group (relative to {short_id}, {len(group_recent_docs)} items):"
        ]
        oldest_id = doc_id
        for item in group_recent_docs:
            item_seq = item.get("seq")
            item_type = item.get("doc_type", "")
            item_short = (
                f"{item_type}{item_seq:04d}" if item_seq else item.get("doc_id", "")
            )
            item_label = get_type_name(item_type, locale) if item_type else ""
            lines.append(
                f"- {item_short}  [{item_type}] {item_label}  "
                f"{item.get('title', '')} ({item.get('status', '')})"
            )
            oldest_id = item.get("doc_id", item_short)
        lines.extend([
            "",
            "To browse earlier documents:",
            f"GET {base}/list/groups/{group_id}/documents?before={oldest_id}&limit=5",
        ])
        sections.append(_section("Recent documents in group", "\n".join(lines)))

    decision_body = {
        "doc_id": doc_id,
        "doc_class": "R",
        "sequence": [
            {"id": 1, "type": "<TYPE_CODE>", "label": "<STEP_LABEL>"},
        ],
    }
    s5_body = (
        f"Submit the workflow decision: POST {base}/workflow/decide\n"
        f"Authorization: Bearer {raw_token}\n\n"
        f"{json.dumps(decision_body, ensure_ascii=False, indent=2)}"
    )
    sections.extend([
        _section("Workflow decision submission", s5_body),
        _section("doc_type guide", f"GET {base}/help/doc_type"),
    ])
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
        f"{_fmt(pending)}"
    )

    s2_body = (
        f"The workflow for {doc_id} is already decided. Edit ONLY the pending (not-yet-started)\n"
        "tail of its sequence — add, remove, or reorder pending steps to match what the work now\n"
        "needs. Locked steps (done / in progress) are immutable and preserved automatically.\n"
        "Submit the FULL replacement pending list (not a diff) through the PATCH below. Report\n"
        "steps (NR/TR/TSR) are attached automatically to each N/T/TS step, so submit only the\n"
        "instruction and design steps. Do NOT edit the root document. Use the document type\n"
        "guide for valid type codes. You may not empty a decided workflow that has no locked\n"
        "step — an empty pending list in that case is rejected (invalid_sequence_empty)."
    )

    s3_body = (
        f"Note: All GET requests require an Authorization: Bearer {raw_token} header\n\n"
        f"{doc_id}: GET {base}/document/{doc_id}\n"
        f"Current sequence: GET {base}/workflow/sequence?doc_id={doc_id}"
    )

    edit_body = {
        "doc_id": doc_id,
        "items": [
            {"type": "<TYPE_CODE>", "label": "<STEP_LABEL>"},
        ],
    }
    s5_body = (
        f"Submit the sequence edit: PATCH {base}/workflow/sequence\n"
        f"Authorization: Bearer {raw_token}\n\n"
        f"{json.dumps(edit_body, ensure_ascii=False, indent=2)}"
    )

    sections = [
        _section("Document information", s1_body),
        _section("Clarification guide", _clarification_guide_body(base, doc_id, raw_token, locale)),
        _section("Sequence edit instructions", s2_body),
        _section("Current sequence", seq_body),
        _section("Reference documents", s3_body),
        _section("Sequence edit submission", s5_body),
        _section("doc_type guide", f"GET {base}/help/doc_type"),
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
    s3_lines = [f"{canonical_id}: GET {base}/document/{canonical_id}"]
    if ref_doc_ids:
        for d in ref_doc_ids:
            if d == canonical_id:
                continue
            s3_lines.append(f"{d}: GET {base}/document/{d}")
    s3_body = (
        f"Note: All GET requests require an Authorization: Bearer {raw_token} header\n\n"
        + "\n".join(s3_lines)
    )

    # ── Section 4: recent documents in the group (omit when there are 0) ─────
    s4_section = ""
    if group_recent_docs:
        count = len(group_recent_docs)
        lines_doc = [f"Recent documents in group (relative to {short_id}, {count} items):"]
        oldest_canonical_id = ""
        for item in group_recent_docs:
            iseq = item.get("seq")
            isid = f"{item['doc_type']}{iseq:04d}" if iseq else item.get("doc_id", "")
            ilabel = get_type_name(item.get("doc_type", ""), locale) if item.get("doc_type") else ""
            lines_doc.append(
                f"- {isid}  [{item.get('doc_type', '')}] {ilabel}  {item.get('title', '')} ({item.get('status', '')})"
            )
            oldest_canonical_id = item.get("doc_id", isid)
        nav_gid = group_id_full or f"{project}-{module}-{group}"
        lines_doc.append("")
        lines_doc.append("To browse earlier documents:")
        lines_doc.append(
            f"GET {base}/list/groups/{nav_gid}/documents?before={oldest_canonical_id}&limit=5"
        )
        s4_section = _section("Recent documents in group", "\n".join(lines_doc))

    # ── Section 5: review submission (inbox action:review) ────────────────────
    post_body = {
        "action": "review",
        "project": project,
        "doc_id": canonical_id,
        "verdict": "pass | issues | hold",
        "findings": [{"locus": "<where in the doc>", "note": "<what is wrong / to improve>"}],
        "comment": "<optional overall comment>",
    }
    post_json = json.dumps(post_body, ensure_ascii=False, indent=2)
    s5_body = (
        f"Submit your review: POST {base}/inbox\n"
        f"Authorization: Bearer {raw_token}\n"
        "\n"
        f"{post_json}\n"
        "\n"
        f"{_DRYRUN_HINT}"
    )

    # ── Section 6: scratch directory ─────────────────────────────────────────
    # Disabled: remote HTTP consumers cannot access the server host's local paths. Restore when a command-invocation mode is introduced.
    # s6_body = scratch_dir

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
        _section("Review instructions", s2_body),
        _section("Reference documents", s3_body),
    ]
    if s4_section:
        sections.append(s4_section)
    sections.append(_section("Review submission", s5_body))
    # sections.append(_section("Scratch directory", s6_body))  # disabled (see §6 above)
    sections.append(_section("Verdict guide", s7_body))
    # NR0003 recency: repeat the no-choices guard at the very bottom (see build_mention).
    sections.append(_section("Reminder", _no_choices_reminder(base, canonical_id, locale)))

    return "\n\n".join(sections)
