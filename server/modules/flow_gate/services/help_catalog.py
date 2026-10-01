"""Help catalog assembly and the single visibility judgment (0372 D-0003 / P-0004 / L-0005).

The worker mention used to carry every request format, example and catalog inline.
Those sections now live behind ``GET /help`` as named items the worker pulls when it
actually needs them. This module owns three things:

* the static catalog — names, display order, form, localized title and summary;
* :func:`decide_visibility` — the ONE judgment the index, the direct item route and
  the bulk route all call, so "listed in the index" and "allowed when called back"
  can never disagree (D-0003 §3-4);
* the per-item suppliers, each of which delegates to the service that already owns
  that answer (``tool_registry``, ``template_provision``, ``tr_scope_service`` …)
  instead of re-deriving it here.

Nothing here is cached. The answer depends on token scope, step type, source mode,
locale and live DB state all at once, and a cache key missing any one of them would
hand a worker an item it must not see (L-0005 §2-11).
"""
from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from modules.flow_gate import template_provision
from modules.flow_gate.db import documents as db_documents
from modules.flow_gate.db import templates as db_templates
from modules.flow_gate.documents.constants import WORK_PLAN_TYPE
from modules.flow_gate.services import (
    engine_recipe_service,
    remote_tool_service,
    step_verification_service,
    test_command_service,
    tool_registry,
    tr_scope_service,
    work_plan_service,
)

_logger = logging.getLogger(__name__)

VERSION = "v1"

# ── Parameters (L-0005 §1) ───────────────────────────────────────────────────
BULK_ITEM_MIN = 1
BULK_ITEM_MAX = 10
GROUP_DOCUMENTS_LIMIT = 20
GROUP_DOCUMENTS_MORE_LIMIT = 5

#: The one display order. The index, ``detail=true`` and the bulk ``requested``
#: list are all derived from this tuple — there is no second ordering anywhere.
CATALOG_ORDER: tuple[str, ...] = (
    "notices",
    "group_documents",
    "document_access",
    "document_attachments",
    "doc_type",
    "question",
    "submit",
    "source_tools",
    "source_bundles",
    "source_snapshots",
    "design_template",
    "authoring_guide",
    "test_commands",
    "tr_self_check",
    "changed_files_format",
    "step_verification_format",
)

ITEM_FORM: dict[str, str] = {
    "notices": "content",
    "group_documents": "content",
    "document_access": "content",
    "document_attachments": "content",
    "doc_type": "content",
    "question": "content",
    "submit": "content",
    "source_tools": "children",
    "source_bundles": "content",
    "source_snapshots": "content",
    "design_template": "children",
    "authoring_guide": "children",
    "test_commands": "content",
    "tr_self_check": "content",
    "changed_files_format": "content",
    "step_verification_format": "content",
}

_CATALOG = frozenset(CATALOG_ORDER)

#: Items every worker token sees, whatever it is doing.
ALWAYS_VISIBLE = frozenset({
    "notices", "group_documents", "document_access", "document_attachments", "doc_type", "question", "submit",
})
#: A console user JWT carries no document context, so only the two context-free
#: items survive (P-0004 [edge 4]).
USER_SESSION_VISIBLE = frozenset({"document_access", "doc_type"})

AUTHORING_SCOPES = frozenset({"new", "edit"})
GUIDE_TYPES = frozenset({"N", "T", "T2", "TR", "TR2", "TS"})
INVESTIGATION_TYPES = frozenset({"N", "NR"})
# Mutating types come from the registry that already gates the write tools, so the
# "you may write source" judgment and the "you must report changed files" judgment
# cannot drift apart.
MUTATING_TYPES = tool_registry.MUTATING_STEP_TYPES

SUPPORTED_LOCALES = ("ko", "en", "ja")
FALLBACK_LOCALE = "ko"


# ── Localized catalog copy ───────────────────────────────────────────────────
TITLES: dict[str, dict[str, str]] = {
    "ko": {
        "notices": "주의사항",
        "group_documents": "그룹의 문서 목록",
        "document_access": "문서 조회 방법",
        "document_attachments": "문서 첨부파일 이용 방법",
        "doc_type": "문서 타입 안내",
        "question": "질의(Q) 등록",
        "submit": "결과 제출 방법",
        "source_tools": "소스 도구",
        "source_bundles": "Source Bundle 사용 정책",
        "source_snapshots": "Legacy Snapshot 종료 안내",
        "design_template": "설계서 템플릿",
        "authoring_guide": "작성 지침",
        "test_commands": "검증된 테스트 명령",
        "tr_self_check": "TR Self-check 사용법",
        "changed_files_format": "변경 파일 보고 서식",
        "step_verification_format": "단계별 확인 절 서식",
    },
    "en": {
        "notices": "Notices",
        "group_documents": "Documents in this group",
        "document_access": "How to read documents",
        "document_attachments": "How to use document attachments",
        "doc_type": "Document type guide",
        "question": "Register a query (Q)",
        "submit": "How to submit",
        "source_tools": "Source tools",
        "source_bundles": "Source Bundle policy",
        "source_snapshots": "Legacy Snapshot retirement",
        "design_template": "Design document template",
        "authoring_guide": "Authoring guide",
        "test_commands": "Verified test commands",
        "tr_self_check": "TR Self-check guide",
        "changed_files_format": "Changed-files report format",
        "step_verification_format": "Step-verification section format",
    },
    "ja": {
        "notices": "注意事項",
        "group_documents": "グループの文書一覧",
        "document_access": "文書の参照方法",
        "document_attachments": "文書添付ファイルの利用方法",
        "doc_type": "文書タイプ案内",
        "question": "質問(Q)の登録",
        "submit": "結果の提出方法",
        "source_tools": "ソースツール",
        "source_bundles": "Source Bundle の利用方針",
        "source_snapshots": "旧 Snapshot の廃止",
        "design_template": "設計書テンプレート",
        "authoring_guide": "作成ガイド",
        "test_commands": "検証済みテストコマンド",
        "tr_self_check": "TR Self-check の使い方",
        "changed_files_format": "変更ファイル報告フォーマット",
        "step_verification_format": "段階別確認節フォーマット",
    },
}

#: ``submit`` renames itself after the work the token was issued for — a reviewer
#: is not "submitting a result", it is submitting a verdict (P-0004 [normal 3]).
SUBMIT_TITLES: dict[str, dict[str, str]] = {
    "ko": {
        "review": "판정 제출 방법",
        "workflow_decide": "진행 순서 제출 방법",
        "workflow_sequence_edit": "진행 순서 제출 방법",
    },
    "en": {
        "review": "How to submit a verdict",
        "workflow_decide": "How to submit the workflow order",
        "workflow_sequence_edit": "How to submit the workflow order",
    },
    "ja": {
        "review": "判定の提出方法",
        "workflow_decide": "進行順序の提出方法",
        "workflow_sequence_edit": "進行順序の提出方法",
    },
}

SUMMARIES: dict[str, dict[str, str]] = {
    "ko": {
        "notices": "이 작업에서 반드시 지켜야 할 금지 사항.",
        "group_documents": "이 그룹의 문서 ID·타입·제목 목록을 바로 돌려준다.",
        "document_access": "문서 본문과 메타데이터를 읽는 주소.",
        "document_attachments": "첨부파일 목록/읽기/소스 복사 주소와 권한.",
        "doc_type": "문서 타입 코드와 이름 목록.",
        "question": "막혔을 때 질의를 등록하는 방법.",
        "submit": "작성한 문서를 등록하는 요청 서식.",
        "source_tools": "이 토큰이 쓸 수 있는 원격 소스 도구 목록.",
        "source_bundles": "자동 준비, 읽기·실행, freshness 및 promotion 금지 정책.",
        "source_snapshots": "Legacy Snapshot 생성·승인 종료 및 역사 조회.",
        "design_template": "설계 타입별 표준 템플릿 본문.",
        "authoring_guide": "이 타입의 문서를 쓰는 방법.",
        "test_commands": "이 프로젝트에 등록된, 실행이 확인된 테스트 명령.",
        "tr_self_check": "TR 수정 단계의 공식 검증 경로(run/read/cancel_self_check) 요청 서식·수명주기·오류 대응.",
        "changed_files_format": "제출 시 반드시 넣어야 하는 변경 파일 절의 서식.",
        "step_verification_format": "TR 제출 시 반드시 넣어야 하는 단계별 확인 절의 서식.",
    },
    "en": {
        "notices": "What this step forbids and must not be skipped.",
        "group_documents": "Document ids, types and titles in this group, returned directly.",
        "document_access": "Addresses that read a document body and its metadata.",
        "document_attachments": "Addresses and permissions for attachment list/read/copy-to-source.",
        "doc_type": "Document type codes and their names.",
        "question": "How to register a query when you are blocked.",
        "submit": "Request format that registers the document you wrote.",
        "source_tools": "Remote source tools this token may call.",
        "source_bundles": "Automatic preparation, access, execution, freshness, and no-promotion policy.",
        "source_snapshots": "Legacy Snapshot creation and approval retirement.",
        "design_template": "Standard template body per design type.",
        "authoring_guide": "How to write a document of this type.",
        "test_commands": "Test commands registered for this project and verified on this host.",
        "tr_self_check": "The TR edit verification path (run/read/cancel_self_check): request format, lifecycle and error handling.",
        "changed_files_format": "Format of the changed-files section your submission must carry.",
        "step_verification_format": "Format of the step-verification section a TR submission must carry.",
    },
    "ja": {
        "notices": "この作業で必ず守るべき禁止事項。",
        "group_documents": "このグループの文書ID・タイプ・タイトル一覧をそのまま返す。",
        "document_access": "文書本文とメタデータを読むアドレス。",
        "document_attachments": "添付ファイルの一覧/読み取り/ソースコピーのアドレスと権限。",
        "doc_type": "文書タイプコードと名称の一覧。",
        "question": "行き詰まったときに質問を登録する方法。",
        "submit": "作成した文書を登録するリクエスト形式。",
        "source_tools": "このトークンが使えるリモートソースツールの一覧。",
        "source_bundles": "自動準備、参照・実行、鮮度、昇格禁止の方針。",
        "source_snapshots": "旧 Snapshot の作成・承認の廃止。",
        "design_template": "設計タイプ別の標準テンプレート本文。",
        "authoring_guide": "このタイプの文書を書く方法。",
        "test_commands": "このプロジェクトに登録され、実行が確認されたテストコマンド。",
        "tr_self_check": "TR修正段階の公式検証経路(run/read/cancel_self_check)のリクエスト形式・ライフサイクル・エラー対応。",
        "changed_files_format": "提出時に必ず入れる変更ファイル節のフォーマット。",
        "step_verification_format": "TR提出時に必ず入れる段階別確認節のフォーマット。",
    },
}

SUBMIT_SUMMARIES: dict[str, dict[str, str]] = {
    "ko": {
        "review": "검토 판정(pass/issues/hold)을 보내는 요청 서식.",
        "workflow_decide": "진행 순서를 확정해 보내는 요청 서식.",
        "workflow_sequence_edit": "진행 순서를 고쳐 보내는 요청 서식.",
    },
    "en": {
        "review": "Request format that sends a review verdict (pass/issues/hold).",
        "workflow_decide": "Request format that fixes the workflow order.",
        "workflow_sequence_edit": "Request format that edits the workflow order.",
    },
    "ja": {
        "review": "レビュー判定(pass/issues/hold)を送るリクエスト形式。",
        "workflow_decide": "進行順序を確定して送るリクエスト形式。",
        "workflow_sequence_edit": "進行順序を修正して送るリクエスト形式。",
    },
}


# ── notices copy (L-0005 §2-9) ───────────────────────────────────────────────
NOTICE_LINES: dict[str, dict[str, str]] = {
    "ko": {
        "continuous_unattended": "이 작업은 무인(UNMANNED) 연속 작업 체인의 일부입니다. 사람이 지켜보고 있지 않습니다.",
        "continuous_no_stop": "한 단계를 마치면 응답의 다음 토큰과 멘트로 다음 단계를 이어가는 것이 이 연속 작업의 기본 동작입니다.",
        "continuous_autonomous": "작업 범위 안의 일반적이고 되돌릴 수 있는 판단은 가진 정보와 자체 조사 결과를 바탕으로 합리적인 방법을 선택해 진행할 수 있습니다. 요청 범위 밖의 변경, 조사로 확보할 수 없는 필수 정보, 승인되지 않은 파괴적·비가역적 작업이 필요한 경우에만 중단하거나 질의하십시오.",
        "interactive_query_without_choice": "진행을 막는 정보가 없으면 문서에 연결된 질의(Q)를 등록하십시오. 콘솔 선택지를 요구하지 말고, 안전한 가정을 세울 수 있으면 그 가정을 명시하고 계속 진행하십시오.",
        "review_no_modify": "검토 작업에서는 대상 문서를 수정하거나 새 결과 문서를 만들지 말고, 지정된 판정만 제출하십시오.",
        "investigation_only": "이 단계는 조사 전용입니다. 소스 파일을 수정·생성·삭제하지 마십시오.",
        "assigned_scope_only": "이 작업에 배정된 그룹 작업 공간과 파일만 변경하고, 변경 파일은 작업 레포트에 빠짐없이 보고하십시오.",
        "source_snapshot_policy": "Source Bundle은 소스 접근이나 실행이 필요할 때 자동 준비되며 사람 승인이 필요하지 않습니다. 영구 변경은 FlowGate 소스 변경 도구를 사용하고 Bundle/Scratch를 원본으로 취급하거나 되돌려 반영하지 마세요.",
    },
    "en": {
        "continuous_unattended": "This task is part of an UNMANNED continuous work chain. Nobody is watching.",
        "continuous_no_stop": "After one step is complete, continuing to the next step with the token and mention in the response is the normal behavior of this continuous run.",
        "continuous_autonomous": "For routine, reversible decisions within the requested scope, select a reasonable approach using the available information and your own investigation. Pause or raise a question only when work requires an out-of-scope change, essential information cannot be obtained through investigation, or a destructive or irreversible action has not been authorized.",
        "interactive_query_without_choice": "If information you need is missing, register a query (Q) bound to the document. Do not demand a console choice; when a safe assumption exists, state it and continue.",
        "review_no_modify": "In a review step, do not modify the target document or create a new result document — submit only the verdict you were asked for.",
        "investigation_only": "This step is investigation-only. Do not modify, create or delete source files.",
        "assigned_scope_only": "Change only the group workspace and files assigned to this task, and report every changed file in the work report.",
        "source_snapshot_policy": "Source Bundle is prepared automatically when source access or execution needs it; no human approval is required. Use canonical FlowGate mutation tools for persistent edits. Bundle and Scratch are not the source of truth and cannot be promoted.",
    },
    "ja": {
        "continuous_unattended": "この作業は無人(UNMANNED)連続作業チェーンの一部です。人は見ていません。",
        "continuous_no_stop": "一段階が完了したら、応答に含まれる次のトークンとメンションで次の段階を続けることが、この連続作業の基本動作です。",
        "continuous_autonomous": "要求範囲内の通常かつ元に戻せる判断は、手持ちの情報と自力の調査に基づいて合理的な方法を選び、進めることができます。要求範囲外の変更、調査でも得られない必須情報、または承認されていない破壊的・不可逆な作業が必要な場合に限り、中断または質問してください。",
        "interactive_query_without_choice": "進行を妨げる情報が無い場合は、文書に紐づく質問(Q)を登録してください。コンソールの選択肢を求めず、安全な仮定を立てられるならそれを明記して続行してください。",
        "review_no_modify": "レビュー作業では対象文書を修正したり新しい結果文書を作成したりせず、指定された判定のみを提出してください。",
        "investigation_only": "この段階は調査専用です。ソースファイルを修正・作成・削除しないでください。",
        "assigned_scope_only": "この作業に割り当てられたグループ作業領域とファイルのみ変更し、変更ファイルは作業レポートに漏れなく報告してください。",
        "source_snapshot_policy": "Source Bundle はソース参照や実行が必要なとき自動準備され、人の承認は不要です。永続的な変更には FlowGate の正規ソース変更ツールを使い、Bundle/Scratch を原本として扱ったり昇格させたりしないでください。",
    },
}


# ── question copy (moved here from help_routes so /help/question and the
#    `question` item are served by one supplier — L-0005 §2-8) ────────────────
QUESTION_HELP_COPY: dict[str, dict] = {
    "ko": {
        "note": "질의는 Q 문서가 아니라 해당 문서의 질의 데이터로 등록합니다. 콘솔 선택지를 강요하지 마세요.",
        "titles": ("기능 범위", "질의 우선순위"),
        "bodies": (
            "R0001 본문이 한 줄이라 DS 설계 대상이 불명확합니다. 구체적 기능 범위와 인수 기준은 무엇입니까?",
            "멘트의 next_type 은 DS 인데 질의가 필요합니다. 질의를 먼저 등록할까요, 모호함을 가정하고 DS 를 작성할까요?",
        ),
        "continue_note": "질의를 등록해도 작업은 멈추지 않습니다. 무인 연속 작업에서는 가정을 명시하고 계속 진행하세요.",
        "option_labels": ("<옵션1 — 선택 · 없으면 생략>", "<옵션2>"),
    },
    "en": {
        "note": "Register a query as query data on the relevant document, not as a Q document. Do not force console choices.",
        "titles": ("Feature scope", "Query priority"),
        "bodies": (
            "The R0001 body has only one line, so the DS design scope is unclear. What are the specific feature scope and acceptance criteria?",
            "The mention says next_type is DS, but clarification is needed. Should the query be registered first, or should DS be drafted with explicit assumptions?",
        ),
        "continue_note": "Registering a query does not pause the work. In an unmanned chain, state your assumption and keep going.",
        "option_labels": ("<option 1 — optional, omit if not needed>", "<option 2>"),
    },
    "ja": {
        "note": "質問はQ文書ではなく、対象文書の質問データとして登録します。コンソールで選択肢を強制しないでください。",
        "titles": ("機能範囲", "質問の優先順位"),
        "bodies": (
            "R0001の本文が1行だけなので、DS設計の対象が不明確です。具体的な機能範囲と受入基準は何ですか。",
            "メンションのnext_typeはDSですが、確認が必要です。先に質問を登録するべきですか、それとも前提を明記してDSを作成するべきですか。",
        ),
        "continue_note": "質問を登録しても作業は止まりません。無人の連続作業では前提を明記して続行してください。",
        "option_labels": ("<選択肢1 — 任意・不要なら省略>", "<選択肢2>"),
    },
}


# ── item-level notes ─────────────────────────────────────────────────────────
_ITEM_NOTES: dict[str, dict[str, str]] = {
    "ko": {
        "group_documents": "본문까지 읽으려면 GET {base}/document/{{doc_id}} 를 호출하세요.",
        "document_access": "경로는 단수형 /document/ 입니다. 복수형 /documents/ 는 콘솔 전용 API라 작업 토큰에 401 을 돌려줍니다.",
        "document_attachments": "첨부파일은 복사하기 전까지 프로젝트 소스가 아닙니다. /remote/read, grep, glob으로 찾지 마십시오. read 권한은 list/read만, read_write 권한은 list/read/copy까지, none 권한은 이 API를 전혀 쓸 수 없습니다.",
        "design_template_children": "default_child 는 이번에 작성할 문서의 타입입니다. 특별한 이유가 없으면 그것을 받으세요. 작업계획(WP)을 쓸 때 수량은 근거 기반으로 산정하고, 근거가 없으면 counted_types/quantities에 키를 남긴 채 count 0으로 둡니다(1로 추측하지 않습니다).",
        "design_template_body": "rendered 는 예전 지시문의 '## Document template' 절과 같은 완성 블록입니다. 본문에는 파일 경로가 들어가지 않습니다. 작업계획(WP)을 쓸 때 수량은 근거 기반으로 산정하고, 근거가 없으면 counted_types/quantities에 키를 남긴 채 count 0으로 둡니다(1로 추측하지 않습니다).",
        "design_template_fallback": "resolved_locale 이 requested_locale 과 다르면 본문은 대체 로케일의 것입니다. 작업계획(WP)을 쓸 때 수량은 근거 기반으로 산정하고, 근거가 없으면 counted_types/quantities에 키를 남긴 채 count 0으로 둡니다(1로 추측하지 않습니다).",
        "test_commands_empty": "이 프로젝트에는 아직 검증된 테스트 명령이 없습니다. 새 명령을 직접 작성해도 되며, 원격 테스트 실행이 그것을 검증합니다.",
        "changed_files": "섹션 제목에 번호를 붙이면(예: '## 5. 변경 파일') 보고가 비어 있는 것으로 파싱됩니다.",
        "submit_dry_run": "본문에 \"dry_run\": true 를 넣으면 등록 없이 검증만 합니다. 토큰은 소비되지 않습니다.",
        "submit_review_dry_run": "검토 판정 제출은 두 단계로 이루어집니다. 먼저 최종 의미 페이로드와 `dry_run: true`를 POST로 보내 영수증을 받고, 동일한 페이로드를 그 `receipt`와 함께 (dry_run 없이) 다시 POST합니다. 판정, 검사 결과, 의견, 지문 또는 강제 사유를 다시 만들거나 정규화하지 마십시오. 인코딩 실패는 제출을 중단합니다. 강제를 자동으로 추가하지 마십시오.",
        "submit_encoding_guard": "본문이 깨진 글자(????)로 보이면 등록이 거절됩니다. 계산 순서: 먼저 본문을 UTF-8 파일로 쓰고, 그 파일에서 글자 수(body_chars)와 sha256(body_sha256)을 구한 다음 요청을 만드세요. 지문이 맞으면 그것으로 판정하고, 안 보내면 물음표 비율로 판정합니다. 지문이 어긋나거나 정말로 이대로 보내야 하면 force_encoding_reason 에 사유(공백 제외 10자 이상)를 적으세요.",
        "submit_work_plan": "작업계획의 content에는 design_template/WP 형식의 정본 JSON만 넣으십시오. 새 작업계획을 등록할 때 제목은 title, 붙일 문서는 prev_doc_id 요청 항목으로 보내며 JSON 본문에 넣지 마십시오. 수량은 근거 기반으로 산정하고, 근거가 없으면 counted_types/quantities에 키를 남긴 채 count 0으로 둡니다(1로 추측하지 않습니다).",
        "submit_workflow_sequence_edit": "손대지 않은 지시/일반 행은 받은 note/source_doc_id/source_revision_no/provider_id/provider_display_name을 그대로 보내고, 타입을 바꾸거나 새로 넣은 행은 다섯 값을 비우며, 지운 행과 NR/TR/TSR 행은 보내지 마십시오. provider_id·provider_display_name 키를 통째로 빼고 보내면 서버가 저장돼 있던 공급자를 그대로 지키며, 공급자를 정말로 비우려면 provider_id를 null로 명시해 보내십시오. 레포트 행의 값은 서버가 바로 앞 지시 행에서 자동으로 이어 붙입니다.",
    },
    "en": {
        "group_documents": "To read a body, call GET {base}/document/{{doc_id}}.",
        "document_access": "The path is singular /document/. The plural /documents/ is the console-only API and answers a work token with 401.",
        "document_attachments": "An attachment is not project source until you copy it. Do not search for it through /remote/read, grep or glob. A read kind may list/read only; read_write may list/read/copy; none cannot use these endpoints at all.",
        "design_template_children": "default_child is the type you are writing now. Take that one unless you have a reason not to. When authoring a work plan (WP), derive quantities from evidence; when there is no basis, keep the key in counted_types/quantities with count 0 -- never guess 1.",
        "design_template_body": "rendered is the finished block that used to be the '## Document template' section of the mention. The body never contains a file path. When authoring a work plan (WP), derive quantities from evidence; when there is no basis, keep the key in counted_types/quantities with count 0 -- never guess 1.",
        "design_template_fallback": "When resolved_locale differs from requested_locale, the body is the fallback locale's. When authoring a work plan (WP), derive quantities from evidence; when there is no basis, keep the key in counted_types/quantities with count 0 -- never guess 1.",
        "test_commands_empty": "This project has no verified test command yet. You may author a new one; the remote test run will verify it.",
        "changed_files": "Numbering the heading (e.g. '## 5. Changed Files') makes the report parse as empty.",
        "submit_dry_run": "Adding \"dry_run\": true to the body validates the submission without registering anything. The token is not consumed.",
        "submit_review_dry_run": "Review submission requires two steps: POST the final semantic payload with `dry_run: true`, receive a receipt, then POST the identical payload with that `receipt` and without `dry_run`. Do not rebuild or normalize verdict, findings, comment, fingerprints, or force reason between requests. Encoding failure stops submission; do not add force automatically.",
        "submit_encoding_guard": "A body that looks corrupted (mojibake '?'s) is rejected. Calculation order: write the body to a UTF-8 file first, compute its character count (body_chars) and sha256 (body_sha256) from that file, then build the request. A matching fingerprint is trusted over the question-mark heuristic; if none is sent, the heuristic runs instead. If the fingerprint mismatches, or you must send it as-is, put a reason (>=10 non-whitespace chars) in force_encoding_reason.",
        "submit_work_plan": "The work-plan content must contain only canonical JSON in the design_template/WP format. When registering a new work plan, send the title in the title request field and its attachment target in prev_doc_id; do not put either in the JSON body. Derive quantities from evidence; when there is no basis, keep the key in counted_types/quantities with count 0 -- never guess 1.",
        "submit_workflow_sequence_edit": "Return note/source_doc_id/source_revision_no/provider_id/provider_display_name unchanged for untouched instruction and ordinary rows; clear all five for retyped or newly inserted rows; omit deleted and NR/TR/TSR rows. Omitting the provider_id and provider_display_name keys entirely keeps the provider already stored on the row; to genuinely empty it, send provider_id as an explicit null. The server carries the report-row values forward from the preceding instruction row.",
    },
    "ja": {
        "group_documents": "本文まで読むには GET {base}/document/{{doc_id}} を呼び出してください。",
        "document_access": "パスは単数形の /document/ です。複数形の /documents/ はコンソール専用APIで、作業トークンには401を返します。",
        "document_attachments": "添付ファイルはコピーするまでプロジェクトソースではありません。/remote/read、grep、globで探さないでください。read権限はlist/readのみ、read_write権限はlist/read/copyまで、none権限はこれらのエンドポイントを一切使用できません。",
        "design_template_children": "default_child は今回作成する文書のタイプです。特別な理由が無ければそれを取得してください。作業計画(WP)を書くとき、数量は根拠から算定し、根拠がなければ counted_types/quantities にキーを残したまま count 0 とします(1 と推測しません)。",
        "design_template_body": "rendered は以前の指示文の '## Document template' 節と同じ完成ブロックです。本文にファイルパスは入りません。作業計画(WP)を書くとき、数量は根拠から算定し、根拠がなければ counted_types/quantities にキーを残したまま count 0 とします(1 と推測しません)。",
        "design_template_fallback": "resolved_locale が requested_locale と異なる場合、本文は代替ロケールのものです。作業計画(WP)を書くとき、数量は根拠から算定し、根拠がなければ counted_types/quantities にキーを残したまま count 0 とします(1 と推測しません)。",
        "test_commands_empty": "このプロジェクトにはまだ検証済みのテストコマンドがありません。新しいコマンドを作成しても構いません。リモートテスト実行が検証します。",
        "changed_files": "見出しに番号を付ける(例: '## 5. 変更ファイル')と、報告が空としてパースされます。",
        "submit_dry_run": "本文に \"dry_run\": true を入れると、登録せず検証のみ行います。トークンは消費されません。",
        "submit_review_dry_run": "レビュー判定の提出は2つのステップで行われます。まずセマンティクペイロードの最終形を `dry_run: true` で POST し、レシートを受け取ります。その後、同じペイロードをそのレシートと共に (dry_run なしで) 再度 POST します。判定、検査結果、コメント、指紋、または強制理由を再構築または正規化しないでください。エンコーディング障害は提出を停止します。強制を自動的に追加しないでください。",
        "submit_encoding_guard": "本文が文字化け(????)に見える場合、登録は拒否されます。計算順序: まず本文をUTF-8ファイルとして書き出し、そのファイルから文字数(body_chars)とsha256(body_sha256)を求めてからリクエストを作成してください。フィンガープリントが一致すればそれを優先し、送らなければ疑問符比率で判定します。フィンガープリントが一致しない場合、またはどうしてもそのまま送る必要がある場合は、force_encoding_reason に理由(空白を除き10文字以上)を記入してください。",
        "submit_work_plan": "作業計画の content には design_template/WP 形式の正規 JSON だけを入れてください。新しい作業計画を登録する場合、タイトルは title、紐付け先文書は prev_doc_id リクエスト項目で送り、JSON 本文には入れないでください。数量は根拠から算定し、根拠がなければ counted_types/quantities にキーを残したまま count 0 とします(1 と推測しません)。",
        "submit_workflow_sequence_edit": "変更しない指示行と通常行は受け取った note/source_doc_id/source_revision_no/provider_id/provider_display_name をそのまま返し、タイプを変えた行と新規行では5値を空にし、削除行と NR/TR/TSR 行は送らないでください。provider_id・provider_display_name のキーごと省いて送ると、サーバーは保存済みの供給者をそのまま保持します。本当に空にするには provider_id を null と明示してください。レポート行の値はサーバーが直前の指示行から自動的に引き継ぎます。",
    },
}

_TR_AUTHORING_GUIDE: dict[str, str] = {
    "ko": (
        "작업 레포트(TR)에는 아래를 순서대로 적습니다.\n"
        "\n"
        "1. 무엇을 고쳤는가 — 지시받은 항목별로 실제로 바꾼 동작을 한 문단씩.\n"
        "2. 왜 그렇게 고쳤는가 — 설계 문서의 어느 결정을 따랐는지, 벗어났다면 그 이유.\n"
        "3. 어떻게 확인했는가 — 실행한 테스트 명령과 그 결과. 돌리지 못했으면 그 사실을 적습니다.\n"
        "4. 남은 것 — 이번에 하지 않은 범위와 그 이유.\n"
        "5. 변경 파일 절 — 서식은 도움말 항목 changed_files_format 에 있습니다.\n"
        "6. 단계별 확인 절 — 검수자가 그대로 따라 하면 되는 확인 절차. 서식은 도움말 항목\n"
        "   step_verification_format 에 있습니다.\n"
        "\n"
        "확인을 사용자에게 넘기지 마십시오. 재현·수정·측정까지 마친 뒤 제출합니다.\n"
    ),
    "en": (
        "A work report (TR) states, in this order:\n"
        "\n"
        "1. What you changed — one paragraph per instructed item, describing the behaviour that actually changed.\n"
        "2. Why — which design decision you followed, and the reason for any deviation.\n"
        "3. How you verified it — the test commands you ran and their results. If you could not run them, say so.\n"
        "4. What is left — the scope you did not cover and why.\n"
        "5. The changed-files section — its format is in the changed_files_format help item.\n"
        "6. The step-verification section — a procedure the reviewer can follow verbatim.\n"
        "   Its format is in the step_verification_format help item.\n"
        "\n"
        "Do not hand verification back to the user. Reproduce, fix and measure before submitting.\n"
    ),
    "ja": (
        "作業レポート(TR)には次の順で記載します。\n"
        "\n"
        "1. 何を直したか — 指示項目ごとに、実際に変わった挙動を1段落ずつ。\n"
        "2. なぜそう直したか — 設計文書のどの決定に従ったか、外れた場合はその理由。\n"
        "3. どう確認したか — 実行したテストコマンドとその結果。実行できなかった場合はその事実。\n"
        "4. 残ったもの — 今回対応しなかった範囲とその理由。\n"
        "5. 変更ファイル節 — フォーマットはヘルプ項目 changed_files_format にあります。\n"
        "6. 段階別確認節 — 検収者がそのまま従える確認手順。フォーマットはヘルプ項目\n"
        "   step_verification_format にあります。\n"
        "\n"
        "確認をユーザーに委ねないでください。再現・修正・計測まで済ませてから提出します。\n"
    ),
}

_AUTHORING_GUIDE_TITLES: dict[str, dict[str, str]] = {
    "ko": {"N": "조사지시 작성", "T": "작업지시 작성", "T2": "반영지시 작성", "TR": "작업레포트 작성", "TR2": "반영안 작성", "TS": "테스트시나리오 작성"},
    "en": {"N": "Writing an investigation instruction", "T": "Writing a work instruction",
           "T2": "Writing an apply instruction", "TR": "Writing a work report", "TR2": "Writing an apply proposal", "TS": "Writing a test scenario"},
    "ja": {"N": "調査指示の作成", "T": "作業指示の作成", "T2": "反映指示の作成", "TR": "作業レポートの作成", "TR2": "反映案の作成", "TS": "テストシナリオの作成"},
}


class HelpSupplierError(RuntimeError):
    """A supplier could not build its content (missing context, storage failure).

    Never downgraded into a per-item 403/404: a storage failure dressed up as a
    permission failure sends the worker looking for a permission it already has
    (L-0005 §2-7).
    """


class Decision(NamedTuple):
    visible: bool
    reason: Optional[str]


VISIBLE = Decision(True, None)


def normalize_locale(candidate: Optional[str]) -> str:
    """``ko`` / ``en`` / ``ja``; anything else folds to ``ko`` without an error."""
    value = (candidate or "").strip()
    return value if value in SUPPORTED_LOCALES else FALLBACK_LOCALE


def _copy(table: dict, locale: str, key: str) -> str:
    """One localized string, falling back to ko rather than mixing languages."""
    entry = table.get(locale) or {}
    if key in entry:
        return entry[key]
    return table[FALLBACK_LOCALE][key]


# ── Context ──────────────────────────────────────────────────────────────────

def _effective_doc_type(token_rec: dict) -> tuple[Optional[str], bool]:
    """The type this token is working on (L-0005 §2-2).

    Authoring scopes resolve through the same workflow-head lookup ``tool_registry``
    uses for the tool judgment, so the type-conditional items and the source tools
    can never be decided from two different types.
    """
    action_scope = token_rec.get("action_scope")
    if action_scope in AUTHORING_SCOPES:
        return remote_tool_service._worker_token_step_type_result(token_rec)
    doc_ref = token_rec.get("doc_ref")
    if not doc_ref:
        return None, False
    try:
        doc = db_documents.get_by_id(doc_ref)
    except Exception:
        _logger.warning("help context: document lookup failed for %s", doc_ref, exc_info=True)
        return None, True
    if not doc:
        return None, False
    return doc.get("type_code"), False


def resolve_context(token_rec: dict, locale: str, base_url: str) -> dict:
    """Everything the catalog needs about this caller, resolved once per request."""
    locale = normalize_locale(locale)
    if token_rec.get("_is_user_jwt"):
        return {
            "principal_kind": "user_session",
            "project": None,
            "group_id": None,
            "doc_id": None,
            "doc_type": None,
            "action_scope": None,
            "tool_kind": "none",
            "source_mode": None,
            "reason": "user_session",
            "locale": locale,
            "base_url": base_url,
            "registry": None,
            "scratch_dir": None,
            "continuous": False,
            "token_rec": token_rec,
        }

    project = token_rec.get("project")
    doc_type, _lookup_failed = _effective_doc_type(token_rec)
    registry = tool_registry.resolve_registry(token_rec, project, locale)
    return {
        "principal_kind": "worker",
        "project": project,
        "group_id": token_rec.get("group_id"),
        "doc_id": token_rec.get("doc_ref"),
        "doc_type": doc_type,
        "action_scope": token_rec.get("action_scope"),
        "tool_kind": registry["kind"],
        "source_mode": registry["source_mode"],
        # resolve_registry already applies the §4 precedence
        # (source_mode_local → token_scope_none → step_lookup_failed → null).
        "reason": registry["reason"],
        "locale": locale,
        "base_url": base_url,
        "registry": registry,
        "scratch_dir": token_rec.get("scratch_dir"),
        # An unmanned chain token carries the target sequence it is walking toward.
        "continuous": bool(token_rec.get("continuation_target_seq")),
        "token_rec": token_rec,
    }


def context_envelope(ctx: dict) -> dict:
    return {
        "doc_id": ctx["doc_id"],
        "doc_type": ctx["doc_type"],
        "action_scope": ctx["action_scope"],
        "tool_kind": ctx["tool_kind"],
        "source_mode": ctx["source_mode"],
        "reason": ctx["reason"],
    }


# ── The one visibility judgment (D-0003 §3-4 / L-0005 §2-4) ──────────────────

def _is_design_type(type_code: Optional[str]) -> bool:
    if not type_code:
        return False
    try:
        return bool(template_provision.is_design_type(type_code))
    except Exception:
        # Narrow side on doubt: an unshown item costs one unused help call, a shown
        # one that 403s costs the worker a debugging detour (D-0003 §3-4).
        _logger.warning("help visibility: design-type lookup failed for %s", type_code, exc_info=True)
        return False



def decide_visibility(name: str, ctx: dict) -> Decision:
    """Visible for this caller? The index and every direct call share this answer."""
    if name not in _CATALOG:
        return Decision(False, "unknown_item")

    if ctx["principal_kind"] == "user_session":
        if name in USER_SESSION_VISIBLE:
            return VISIBLE
        return Decision(False, "user_session")

    if name in ALWAYS_VISIBLE:
        return VISIBLE

    if name in {"source_tools", "source_bundles", "source_snapshots"}:
        if ctx.get("source_mode") != "remote":
            return Decision(False, "source_mode_local")
        if ctx.get("tool_kind") == "none":
            return Decision(False, "token_scope_none")
        return VISIBLE

    authoring = ctx.get("action_scope") in AUTHORING_SCOPES
    doc_type = ctx.get("doc_type")

    if name == "design_template":
        if authoring and template_provision.has_body_template(doc_type):
            return VISIBLE
        return Decision(False, "not_design_type")

    if name == "authoring_guide":
        if authoring and doc_type in GUIDE_TYPES:
            return VISIBLE
        return Decision(False, "no_guide_for_type")

    if name == "test_commands":
        if authoring and doc_type == "TS":
            return VISIBLE
        return Decision(False, "not_ts_type")

    if name == "tr_self_check":
        # Same judgment as api_server_tools.definitions_for_run(): a worker token on a TR edit step.
        if ctx.get("principal_kind") == "worker" and ctx.get("action_scope") == "edit" and doc_type == "TR":
            return VISIBLE
        return Decision(False, "not_tr_edit")

    if name == "changed_files_format":
        if authoring and doc_type in MUTATING_TYPES:
            return VISIBLE
        return Decision(False, "not_mutating_type")

    if name == "step_verification_format":
        if authoring and doc_type == "TR":
            return VISIBLE
        return Decision(False, "not_tr_type")

    return Decision(False, "unknown_item")


def visible_names(ctx: dict) -> list[str]:
    """Catalog order, filtered — the source of the ``detail=true`` request list."""
    return [name for name in CATALOG_ORDER if decide_visibility(name, ctx).visible]


# ── URLs ─────────────────────────────────────────────────────────────────────

def item_url(base_url: str, name: str, child: Optional[str] = None) -> str:
    if child is None:
        return f"{base_url}/help/items/{name}"
    return f"{base_url}/help/items/{name}/{child}"


# ── Children enumeration ─────────────────────────────────────────────────────

def _design_type_rows(ctx: dict) -> list[dict]:
    rows = db_templates.list_document_types(project_id=None, series="design", locale=ctx["locale"])
    return [row for row in rows if row.get("is_active", 1)]


def enumerate_children(name: str, ctx: dict) -> list[dict]:
    """Child entries for a ``children`` item; empty list for a ``content`` item."""
    base = ctx["base_url"]
    if name == "source_tools":
        registry = ctx["registry"] or {"tools": []}
        return [
            {
                "name": tool["name"],
                "title": tool["name"],
                "summary": tool["summary"],
                "method": tool["method"],
                "path": tool["path"],
                "scope": tool["scope"],
                "url": item_url(base, "source_tools", tool["name"]),
            }
            for tool in registry["tools"]
        ]

    if name == "design_template":
        children = []
        for row in _design_type_rows(ctx):
            code = row["type_code"]
            children.append({
                "name": code,
                "title": row.get("type_name") or code,
                "summary": row.get("description") or "",
                "url": item_url(base, "design_template", code),
            })
        # The work plan is not in the design series, so it is listed only for the worker
        # who is actually writing one — a D author has no use for the WP body format.
        if str(ctx.get("doc_type") or "").upper() == WORK_PLAN_TYPE:
            children.append({
                "name": WORK_PLAN_TYPE,
                "title": _copy(_WORK_PLAN_TEMPLATE_TITLE, ctx["locale"], "title"),
                "summary": _copy(_WORK_PLAN_TEMPLATE_TITLE, ctx["locale"], "summary"),
                "url": item_url(base, "design_template", WORK_PLAN_TYPE),
            })
        return children

    if name == "authoring_guide":
        doc_type = ctx.get("doc_type")
        if doc_type not in GUIDE_TYPES:
            return []
        return [{
            "name": doc_type,
            "title": _copy(_AUTHORING_GUIDE_TITLES, ctx["locale"], doc_type),
            "summary": _copy(SUMMARIES, ctx["locale"], "authoring_guide"),
            "url": item_url(base, "authoring_guide", doc_type),
        }]

    return []


def _children_count(name: str, ctx: dict) -> Optional[int]:
    if ITEM_FORM[name] != "children":
        return None
    return len(enumerate_children(name, ctx))


# ── Index (L-0005 §2-5) ──────────────────────────────────────────────────────

def _title_for(name: str, ctx: dict) -> str:
    if name == "submit":
        scope_titles = SUBMIT_TITLES.get(ctx["locale"]) or {}
        override = scope_titles.get(ctx.get("action_scope") or "")
        if override:
            return override
    return _copy(TITLES, ctx["locale"], name)


def _summary_for(name: str, ctx: dict) -> str:
    if name == "submit":
        scope_summaries = SUBMIT_SUMMARIES.get(ctx["locale"]) or {}
        override = scope_summaries.get(ctx.get("action_scope") or "")
        if override:
            return override
    return _copy(SUMMARIES, ctx["locale"], name)


def build_index(ctx: dict) -> dict:
    """Names, one-line summaries and what is hidden — never any item body."""
    items: list[dict] = []
    hidden: list[dict] = []
    for name in CATALOG_ORDER:
        decision = decide_visibility(name, ctx)
        if not decision.visible:
            hidden.append({"name": name, "reason": decision.reason})
            continue
        items.append({
            "name": name,
            "title": _title_for(name, ctx),
            "summary": _summary_for(name, ctx),
            "form": ITEM_FORM[name],
            "children_count": _children_count(name, ctx),
            "url": item_url(ctx["base_url"], name),
        })
    return {"items": items, "hidden": hidden}


# ── Suppliers (L-0005 §2-8) ──────────────────────────────────────────────────

def _content_notices(ctx: dict) -> dict:
    keys: list[str] = []
    if ctx["continuous"]:
        keys += ["continuous_unattended", "continuous_no_stop", "continuous_autonomous"]
    else:
        keys.append("interactive_query_without_choice")
    if ctx.get("action_scope") == "review":
        keys.append("review_no_modify")
    if ctx.get("doc_type") in INVESTIGATION_TYPES:
        keys.append("investigation_only")
    if ctx.get("doc_type") in MUTATING_TYPES:
        keys.append("assigned_scope_only")
    # A key requested by two conditions still prints once.
    ordered = list(dict.fromkeys(keys))
    lines = [_copy(NOTICE_LINES, ctx["locale"], key) for key in ordered]
    if ctx.get("source_mode") == "remote" and ctx.get("tool_kind") != "none":
        lines.append(_copy(NOTICE_LINES, ctx["locale"], "source_snapshot_policy"))
    return {"lines": lines}


def _content_group_documents(ctx: dict) -> dict:
    group_id = ctx.get("group_id")
    if not group_id:
        raise HelpSupplierError("work token has no group_id")
    try:
        rows = db_documents.get_documents_by_group_id(group_id)
    except Exception as exc:  # storage failure, not a permission failure
        raise HelpSupplierError(f"group document lookup failed: {group_id}") from exc

    def sort_key(row: dict):
        try:
            seq = int(row.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        return (seq, str(row.get("doc_id") or ""))

    rows = sorted(rows, key=sort_key)
    total = len(rows)
    shown = rows[-GROUP_DOCUMENTS_LIMIT:] if total > GROUP_DOCUMENTS_LIMIT else rows

    more_url = None
    if total > GROUP_DOCUMENTS_LIMIT and shown:
        oldest = shown[0].get("doc_id")
        more_url = (
            f"{ctx['base_url']}/list/groups/{group_id}/documents"
            f"?before={oldest}&limit={GROUP_DOCUMENTS_MORE_LIMIT}"
        )

    return {
        "group_id": group_id,
        "total": total,
        "limit": GROUP_DOCUMENTS_LIMIT,
        "more_url": more_url,
        "documents": [
            {
                "doc_id": row.get("doc_id"),
                "type": row.get("type_code") or row.get("type"),
                "title": row.get("title"),
                "status": row.get("status"),
            }
            for row in shown
        ],
    }


def _content_document_access(ctx: dict) -> dict:
    base = ctx["base_url"]
    doc_id = ctx.get("doc_id") or "{doc_id}"
    project_filter = f"&project={ctx['project']}" if ctx.get("project") else ""
    return {
        "body": {
            "method": "GET",
            "url": f"{base}/document/{{doc_id}}",
            "example": f"{base}/document/{doc_id}",
        },
        "path": {"method": "GET", "url": f"{base}/document/{{doc_id}}/path"},
        # Bounded reads (group 0370). The full-body GET is the expensive one; these
        # exist so a worker can read only the part it needs.
        "partial": {
            "note": (
                "Read only the document data you need; use the full-document GET only "
                "when necessary."
            ),
            "meta": {"method": "GET", "url": f"{base}/document/{{doc_id}}/meta",
                     "summary": "Metadata without the body."},
            "outline": {"method": "GET", "url": f"{base}/document/{{doc_id}}/outline",
                        "summary": "Section outline without the body."},
            "section": {"method": "GET",
                        "url": f"{base}/document/{{doc_id}}/section?section_id=<section_id>",
                        "summary": "One section from that outline.",
                        "rule": "Section reads accept exactly one of section, section_id, lines, or chars."},
            "relations": {"method": "GET", "url": f"{base}/document/{{doc_id}}/relations",
                          "summary": "Relationships without the body."},
            "content_search": {
                "method": "GET",
                "url": (f"{base}/search/documents/content?q=<keyword>{project_filter}"
                        "&include_matches=true&context_lines=2&hits_per_doc=5"),
                "summary": "Search bodies and receive match locations with context.",
            },
        },
        "note": _copy(_ITEM_NOTES, ctx["locale"], "document_access"),
    }

def _attachment_permission_table() -> dict:
    """Which operations each kind may call, derived from the one judge.

    This used to be a literal table here -- a second copy of the read/read_write rule the
    worker routes enforce, free to drift from them. It is now read out of
    tool_registry.ATTACHMENT_KINDS, the same table /help/tools and the routes consult.
    """
    return {
        kind: [name.replace("attachment_", "", 1) for name in tool_registry.attachment_names(kind)]
        for kind in ("read", "read_write", "none")
    }


def _content_document_attachments(ctx: dict) -> dict:
    """T0004 s.4/s.6 -- reuses the same tool_registry kind ctx already carries
    (resolve_context calls tool_registry.resolve_registry once per request), so this
    item can never advertise a permission the worker route itself would refuse.
    """
    base = ctx["base_url"]
    doc_id = ctx.get("doc_id") or "{doc_id}"
    kind = ctx.get("tool_kind") or "none"
    table = _attachment_permission_table()
    view = tool_registry.attachment_view(kind, ctx["locale"], base)
    return {
        "list": {"method": "GET", "url": f"{base}/document/{{doc_id}}/attachments",
                 "example": f"{base}/document/{doc_id}/attachments"},
        "read": {"method": "GET", "url": f"{base}/document/{{doc_id}}/attachments/{{name}}/read"},
        "copy": {
            "method": "POST",
            "url": f"{base}/document/{{doc_id}}/attachments/{{name}}/copy",
            "headers": {"Authorization": "Bearer <YOUR_TOKEN>", "Content-Type": "application/json"},
            # T0004 s.11/s.34: destination is always the caller's own group worktree --
            # there is no group_id field to steer it elsewhere, unlike the Console contract.
            "body": {
                "target_path": "<path inside the source tree, e.g. assets/schema.json>",
            },
        },
        "permission": {
            "kind": kind,
            "allowed": table.get(kind, []),
            "by_kind": table,
        },
        # What a work token CANNOT do with an attachment, said out loud rather than left
        # to be inferred from the three keys above (T0004 s.19 applied to the capability
        # list itself): uploading and deleting are Console-screen actions.
        "absent": view["absent"],
        "note": _copy(_ITEM_NOTES, ctx["locale"], "document_attachments"),
    }


def _content_doc_type(ctx: dict) -> dict:
    try:
        rows = db_templates.list_document_types(project_id=None, locale=ctx["locale"])
    except Exception as exc:
        raise HelpSupplierError("document type lookup failed") from exc
    return {
        "types": [
            {
                "type_code": row["type_code"],
                "name": row["type_name"],
                "series": row["series"],
                "description": row.get("description"),
            }
            for row in rows
            if row.get("is_active", 1)
        ]
    }


def build_question_content(locale: str) -> dict:
    """The body of ``GET /help/question`` and of the ``question`` help item."""
    copy = QUESTION_HELP_COPY[normalize_locale(locale)]
    titles = copy["titles"]
    bodies = copy["bodies"]
    return {
        "note": copy["note"],
        "example": {
            "method": "POST",
            "url": "/flowgate/api/v1/q/{doc_id}/questions",
            "headers": {
                "Authorization": "Bearer <YOUR_TOKEN>",
                "Content-Type": "application/json",
            },
            "body": {
                "asker_kind": "ai",
                "questions": [
                    {"title": titles[0], "body": bodies[0]},
                    # group 0372 set 3: the mention no longer embeds a placeholder Q
                    # POST, so this example is the one place that demonstrates the
                    # optional `options` array the no-choices guard prescribes
                    # (offer alternatives as ONE Q carrying options, never a prompt).
                    {
                        "title": titles[1],
                        "body": bodies[1],
                        "options": list(copy["option_labels"]),
                    },
                ],
            },
        },
    }


def _content_question(ctx: dict) -> dict:
    return build_question_content(ctx["locale"])


def _prev_doc_id(ctx: dict) -> Optional[str]:
    """The document the submission binds to — the chain target, not the last step."""
    doc_id = ctx.get("doc_id")
    if not doc_id:
        return None
    try:
        doc = db_documents.get_by_id(doc_id)
    except Exception:
        return doc_id
    if not doc:
        return doc_id
    return doc.get("target_id") or doc_id


def _module_and_group(ctx: dict) -> tuple[str, str]:
    parts = (ctx.get("group_id") or "").split(".", 2)
    if len(parts) == 3:
        return parts[1], ctx["group_id"]
    return "none", ctx.get("group_id") or ""


def _content_submit(ctx: dict) -> dict:
    base = ctx["base_url"]
    scope = ctx.get("action_scope")
    doc_id = ctx.get("doc_id")
    headers = {"Authorization": "Bearer <YOUR_TOKEN>", "Content-Type": "application/json"}
    dry_run = _copy(_ITEM_NOTES, ctx["locale"], "submit_dry_run")

    encoding_guard = _copy(_ITEM_NOTES, ctx["locale"], "submit_encoding_guard")

    if scope == "review":
        review_dry_run = _copy(_ITEM_NOTES, ctx["locale"], "submit_review_dry_run")
        return {
            "action_scope": scope,
            "method": "POST",
            "url": f"{base}/inbox",
            "headers": headers,
            "body": {
                "action": "review",
                "project": ctx.get("project"),
                "doc_id": doc_id,
                "verdict": "pass | issues | hold",
                "findings": [{"locus": "<where in the doc>", "note": "<what is wrong / to improve>"}],
                "comment": "<optional overall comment>",
                "body_sha256": "<optional: sha256 hex of comment, UTF-8 bytes>",
                "body_chars": "<optional: character count of comment>",
                "force_encoding_reason": "<optional: only if a genuinely-flagged comment must go through anyway>",
            },
            # 0393 T0005 §2-7: a long Korean overall comment survives a file far better
            # than a command line, so the verdict may travel the same way a document does.
            "source_choice": (
                "verdict/findings/comment may travel inline (as above) or in a file. For the "
                "file form send `doc_path` instead — an absolute path inside this token's "
                "scratch directory holding a JSON object with those same keys. Sending both "
                "`doc_path` and `content` is rejected."
            ),
            "verdict_guide": {
                "pass": "meets requirements, no blocking issues",
                "issues": "defects found; list each one in findings (locus + note)",
                "hold": "cannot decide yet (missing context / blocked)",
            },
            "dry_run": review_dry_run,
            "encoding_guard": encoding_guard,
        }

    if scope == "workflow_decide":
        return {
            "action_scope": scope,
            "method": "POST",
            "url": f"{base}/workflow/decide",
            "headers": headers,
            "body": {
                "doc_id": doc_id,
                "doc_class": "R",
                "sequence": [{"id": 1, "type": "<TYPE_CODE>", "label": "<STEP_LABEL>"}],
                # 0391 T0005 §5-5: a corrupted step label is rejected here now (it used
                # to be silently replaced by the type name), so this path needs the same
                # escape hatch as the others.
                "force_encoding_reason": "<optional: only if a genuinely-flagged label must go through anyway>",
            },
            "encoding_guard": encoding_guard,
        }

    if scope == "workflow_sequence_edit":
        return {
            "action_scope": scope,
            "method": "PATCH",
            "url": f"{base}/workflow/sequence",
            "headers": headers,
            "body": {
                "doc_id": doc_id,
                "items": [{
                    "type": "<TYPE_CODE>",
                    "label": "<STEP_LABEL>",
                    "note": "<UNCHANGED_NOTE_OR_EMPTY>",
                    "source_doc_id": None,
                    "source_revision_no": None,
                    # 0444 T0007 (NR0003 §4-6): omitting both provider keys keeps whatever is
                    # stored on the row; an explicit null is what clears it.
                    "provider_id": "<UNCHANGED_PROVIDER_ID_OR_NULL>",
                    "provider_display_name": None,
                }],
                "force_encoding_reason": "<optional: only if a genuinely-flagged label must go through anyway>",
            },
            "encoding_guard": encoding_guard,
            "guidance": _copy(
                _ITEM_NOTES, ctx["locale"], "submit_workflow_sequence_edit"
            ),
        }

    module, group_name = _module_and_group(ctx)
    doc_type = ctx.get("doc_type") or "<Sequence undecided>"
    if scope == "edit":
        body = {
            "action": "edit",
            "project": ctx.get("project"),
            "doc_id": doc_id,
            "edit_reason": "rejected | user_comment",
            "content": "<Complete revised document content>",
            "body_sha256": "<optional: sha256 hex of content, UTF-8 bytes>",
            "body_chars": "<optional: character count of content>",
            "force_encoding_reason": "<optional: only if a genuinely-flagged content must go through anyway>",
        }
    else:
        body = {
            "action": "new",
            "project": ctx.get("project"),
            "module": module,
            "group_name": group_name,
            "doc_type": doc_type,
            "prev_doc_id": _prev_doc_id(ctx),
            "title": "<Fill this in>",
            "content": "<Fill this in>",
            "body_sha256": "<optional: sha256 hex of content, UTF-8 bytes>",
            "body_chars": "<optional: character count of content>",
            "force_encoding_reason": "<optional: only if a genuinely-flagged content must go through anyway>",
        }
        if str(doc_type).upper() in tool_registry.MUTATING_STEP_TYPES:
            body["content"] = "<Fill this in>\n\n" + tr_scope_service.tr_section_placeholder(ctx["locale"])
            if str(doc_type).upper() == "TR":
                body["content"] += "\n" + step_verification_service.section_placeholder(ctx["locale"])
                body["commit_message"] = (
                    "<This TR's own approval commit subject, used verbatim. Conventional "
                    "commit format: type(scope): summary — e.g. "
                    "'fix(git): preserve finalized commit subject' or "
                    "'feat(workflow): add TR commit point'. Must be plain English (ASCII); "
                    "non-English text is ignored and a fixed fallback is used instead. "
                    "Leave empty and the commit becomes 'chore: approve <doc-code>'.>"
                )

    is_work_plan = str(doc_type).upper() == WORK_PLAN_TYPE
    if is_work_plan:
        body["content"] = "<Canonical work-plan JSON>"

    payload = {
        "action_scope": scope or "new",
        "method": "POST",
        "url": f"{base}/inbox",
        "headers": headers,
        "body": body,
        "source_choice": (
            "Send exactly one of `content` and `doc_path`. `doc_path` must be an absolute "
            "path inside this token's scratch directory."
        ),
        "dry_run": dry_run,
        "encoding_guard": encoding_guard,
    }
    if is_work_plan:
        payload["content_format"] = {
            "format": "canonical_json",
            "template_url": f"{base}/help/items/design_template/{WORK_PLAN_TYPE}",
            "guidance": _copy(_ITEM_NOTES, ctx["locale"], "submit_work_plan"),
        }
    if str(doc_type).upper() in tool_registry.MUTATING_STEP_TYPES and scope != "edit":
        payload["changed_files_required"] = True
    if str(doc_type).upper() == "TR" and scope != "edit":
        payload["step_verification_required"] = True
    return payload


def _content_test_commands(ctx: dict) -> dict:
    project = ctx.get("project") or ""
    try:
        block = test_command_service.build_verified_commands_block(project) if project else ""
        # group 0372 set 3 (D-0003 §3-2, "environment-preparation guidance: dropped"): the engine-recipe
        # guidance the TS mention used to inline (flowgate.default.0157) now rides in
        # this item, so the first help call still teaches the registry (L §2-7).
        engine_recipes = engine_recipe_service.build_engine_recipes_block(ctx["base_url"])
    except Exception as exc:
        raise HelpSupplierError("verified test command lookup failed") from exc
    return {
        "project": project,
        "host_os": test_command_service.current_os(),
        "shell": test_command_service.current_shell(),
        "has_commands": bool(block),
        "commands_block": block,
        "engine_recipes": engine_recipes,
    }


def _content_changed_files_format(ctx: dict) -> dict:
    locale = ctx["locale"]
    guide = tr_scope_service.tr_section_guide(locale)
    placeholder = tr_scope_service.tr_section_placeholder(locale)
    heading = placeholder.split("\n", 1)[0]
    return {
        "required": True,
        "heading": heading,
        "guide": guide,
        "placeholder": placeholder,
        "rule": _copy(_ITEM_NOTES, locale, "changed_files"),
        "example": (
            f"{heading}\n\n"
            "- server/modules/flow_gate/services/help_catalog.py\n"
            "- server/modules/flow_gate/api/v1/help_routes.py\n"
        ),
        "empty_case": f"{heading}\n\n{tr_scope_service._spelling_none(locale)}\n",
    }


_STEP_VERIFICATION_RULE: dict[str, str] = {
    "ko": "섹션 제목은 '### '(레벨 3)로 씁니다. 개요는 한 줄, 스텝은 하나 이상, 스텝마다 기대치가 하나 이상 있어야 합니다.",
    "en": "Section titles use '### ' (level 3). One summary line, at least one step, and every step needs at least one Expected line.",
    "ja": "セクション見出しは '### '(レベル3)で書きます。概要は1行、ステップは1つ以上、各ステップに期待値の行が1つ以上必要です。",
}

_STEP_VERIFICATION_EXAMPLE: dict[str, str] = {
    "ko": (
        "### 로그인 화면 확인\n"
        "- 개요: 잘못된 비밀번호 입력 시 오류 문구가 뜬다\n"
        "- 스텝: 임의의 비밀번호로 로그인 시도\n"
        "  - 기대치: '비밀번호가 올바르지 않습니다' 문구가 화면에 표시된다\n"
    ),
    "en": (
        "### Login screen check\n"
        "- Summary: An error message appears on a wrong password\n"
        "- Step: Attempt login with any wrong password\n"
        "  - Expected: The message 'Incorrect password' is shown on screen\n"
    ),
}


def _content_step_verification_format(ctx: dict) -> dict:
    locale = ctx["locale"]
    guide = step_verification_service.section_guide(locale)
    placeholder = step_verification_service.section_placeholder(locale)
    heading = placeholder.split("\n", 1)[0]
    example_body = _STEP_VERIFICATION_EXAMPLE.get(locale, _STEP_VERIFICATION_EXAMPLE["ko"])
    none_marker = step_verification_service._spelling_none(locale)
    return {
        "required": True,
        "heading": heading,
        "guide": guide,
        "placeholder": placeholder,
        "rule": _STEP_VERIFICATION_RULE.get(locale, _STEP_VERIFICATION_RULE["ko"]),
        "example": f"{heading}\n\n{example_body}",
        "empty_case": f"{heading}\n\n{none_marker}\n",
    }


def _content_source_snapshots(ctx: dict) -> dict:
    """The old help URL remains an alias so historical mentions explain retirement."""
    return {
        "operation": "access_source_bundle / run_source_bundle",
        "preparation": "Source Bundle is prepared automatically when source access or execution requires it; no human approval is needed.",
        "read_only_observability": "GET /api/v1/source-bundles?project_id=...&group_id=... lists status, revision, dirty flag, dates, size, policy, hashes, freshness, origin, failure and cleanup state. It never approves, rejects or materializes.",
        "access": ["status", "read", "search", "glob", "stat"],
        "execution": "Build, test, lint, typecheck, dependency/static analysis and temporary experiments run only in disposable AI Scratch copied from the Bundle.",
        "preferred_tools": ["read", "grep", "glob", "stat", "diff", "log", "show", "merge_preview", "Merge Context Tool"],
        "freshness": "Historical Bundle reads remain available; a current-worktree claim requires a fresh fingerprint and a stale claim is rejected.",
        "legacy_snapshot": "New Snapshot requests and approve/reject/materialize/run endpoints return snapshot_feature_retired. Existing created Snapshots remain readable until TTL or group cleanup; no legacy fallback occurs after a Bundle failure.",
        "persistent_changes": "Use canonical FlowGate write_source_file, patch_source_file or remove_source_file. Bundle and AI Scratch are not the source of truth and cannot be promoted, uploaded, committed, merged or synced back.",
    }


# ── tr_self_check (0654 T0004) ───────────────────────────────────────────────
# The request contract itself is owned by api_server_tools.SCHEMAS["run_self_check"],
# tr_self_check_service._validate_request and tr_self_check_policy; this item only
# describes it, and a regression test pins the field set so the two cannot drift.
SELF_CHECK_REQUEST_FIELDS: tuple[str, ...] = ("program", "args", "cwd", "timeout_seconds")
SELF_CHECK_TIMEOUT_DEFAULT = 300
SELF_CHECK_TIMEOUT_MAX = 1800

SELF_CHECK_EXAMPLE: dict = {
    "program": "pytest",
    "args": ["-q", "server/tests/test_x.py"],
    "cwd": ".",
    "timeout_seconds": SELF_CHECK_TIMEOUT_DEFAULT,
}
SELF_CHECK_INVALID_EXAMPLE: dict = {"program": "pytest -q server/tests/test_x.py"}

SELF_CHECK_STATUSES = ("pending", "running", "completed", "failed", "cancelled")
SELF_CHECK_RESULT_FIELDS = (
    "exit_code", "timed_out", "stdout_tail", "stderr_tail",
    "source_changed_during_run", "worktree_state_changed", "error_code",
)

#: The REST contract behind run/read/cancel. Owned by
#: modules/flow_gate/api/v1/self_check_routes.py (router prefix /api/v1/documents);
#: a regression test compares this table with the registered FastAPI routes.
SELF_CHECK_HTTP_BASE = "/flowgate/api/v1"
SELF_CHECK_HTTP_OPERATIONS: tuple[tuple[str, str, str, int], ...] = (
    ("run", "POST", "/documents/{tr_doc_id}/self-check/runs", 202),
    ("read", "GET", "/documents/{tr_doc_id}/self-check/runs/{run_id}", 200),
    ("cancel", "POST", "/documents/{tr_doc_id}/self-check/runs/{run_id}/cancel", 200),
    ("list", "GET", "/documents/{tr_doc_id}/self-check/runs", 200),
)

_LOCALE_INDEX = {"ko": 1, "en": 2, "ja": 3}

#: (code, ko, en, ja) — meaning and next action, never internal detail.
_SELF_CHECK_ERRORS: tuple[tuple[str, str, str, str], ...] = (
    ("selfcheck_invalid_request",
     "요청이 객체가 아니거나 program/args/cwd/timeout_seconds 이외의 필드가 있다 → 네 필드만 보낸다.",
     "The request is not an object or carries a field other than program/args/cwd/timeout_seconds -> send only those four.",
     "リクエストがオブジェクトでない、または program/args/cwd/timeout_seconds 以外のフィールドがある → 4つのフィールドだけ送る。"),
    ("selfcheck_invalid_program",
     "program 이 실행 파일 이름 하나가 아니다(경로·구분자·제어문자 포함) → program 에는 실행 파일 이름만 넣고 옵션과 대상은 args 로 분리한다.",
     "program is not a single executable name (path, separator or control character) -> pass only the executable name in program and move options/targets to args.",
     "program が実行ファイル名1つではない(パス・区切り・制御文字を含む) → program には実行ファイル名だけを入れ、オプションと対象は args に分ける。"),
    ("selfcheck_invalid_args",
     "args 가 문자열 배열이 아니거나 개수(256)·길이(8192) 제한을 넘는다 → 문자열 배열로 줄여서 다시 보낸다.",
     "args is not an array of strings or exceeds the count (256) / length (8192) limit -> resend a shorter array of strings.",
     "args が文字列配列でない、または個数(256)・長さ(8192)の上限を超えている → 文字列配列に直して再送する。"),
    ("selfcheck_invalid_timeout",
     f"timeout_seconds 가 1~{SELF_CHECK_TIMEOUT_MAX} 범위의 정수가 아니다 → 범위 안의 정수로 보낸다(생략하면 {SELF_CHECK_TIMEOUT_DEFAULT}).",
     f"timeout_seconds is not an integer in 1..{SELF_CHECK_TIMEOUT_MAX} -> send an in-range integer (omit for {SELF_CHECK_TIMEOUT_DEFAULT}).",
     f"timeout_seconds が 1〜{SELF_CHECK_TIMEOUT_MAX} の整数ではない → 範囲内の整数を送る(省略時は {SELF_CHECK_TIMEOUT_DEFAULT})。"),
    ("selfcheck_shell_operator",
     "args 안에 |, &&, ;, >, < 같은 셸 연산자가 있다 → 명령을 하나씩 나누어 각각 별도 run 으로 실행한다.",
     "args contains a shell operator such as |, &&, ;, > or < -> split the work into separate runs, one command each.",
     "args に |, &&, ;, >, < などのシェル演算子がある → コマンドを1つずつ分け、別々の run で実行する。"),
    ("selfcheck_inline_execution",
     "python -c, node -e 같은 인라인 코드 실행이다 → 테스트 파일이나 스크립트 경로를 args 로 준다.",
     "Inline code execution such as python -c or node -e -> pass a test file or script path in args.",
     "python -c や node -e のようなインラインコード実行 → テストファイルやスクリプトのパスを args で渡す。"),
    ("selfcheck_program_denied",
     "정책이 금지한 실행 파일이다(셸, git, 네트워크·삭제·시스템 도구 등) → 테스트/린트/빌드용 실행 파일로 바꾼다. 우회를 시도하지 않는다.",
     "The executable is denied by policy (shells, git, network/delete/system tools, ...) -> use a test/lint/build executable instead; do not try to work around it.",
     "ポリシーで禁止された実行ファイル(シェル、git、ネットワーク・削除・システムツール等) → テスト/リント/ビルド用の実行ファイルに変える。回避は試みない。"),
    ("selfcheck_executable_not_found",
     "program 을 찾을 수 없다(설치되지 않았거나 'pytest -q ...' 처럼 명령 전체를 program 에 넣었다) → 이름만 정확히 쓰고 나머지는 args 로 옮긴다. 설치되지 않았다면 그 사실을 보고한다.",
     "program cannot be found (not installed, or the whole command such as 'pytest -q ...' was put in program) -> write the bare name and move the rest to args; if it is not installed, report that.",
     "program が見つからない(未インストール、または 'pytest -q ...' のようにコマンド全体を program に入れた) → 名前だけを正しく書き残りは args に移す。未インストールならその事実を報告する。"),
    ("selfcheck_invalid_cwd",
     "cwd 가 관리 워크트리 안의 존재하는 상대 디렉터리가 아니다(절대경로, 드라이브, 범위 밖) → 워크트리 기준 상대 경로를 쓰거나 '.' 로 둔다.",
     "cwd is not an existing relative directory inside the managed worktree (absolute path, drive, outside) -> use a worktree-relative path or '.'.",
     "cwd が管理ワークツリー内に存在する相対ディレクトリではない(絶対パス、ドライブ、範囲外) → ワークツリー基準の相対パスを使うか '.' のままにする。"),
    ("selfcheck_disabled",
     "이 프로젝트는 Self-check 가 꺼져 있다 → 반환된 사유를 TR 에 보고하고 다른 실행 수단을 찾지 않는다.",
     "Self-check is disabled for this project -> report the returned reason in the TR and do not look for another execution path.",
     "このプロジェクトでは Self-check が無効 → 返された理由を TR に報告し、他の実行手段を探さない。"),
    ("selfcheck_already_running",
     "이 TR 에 이미 실행 중인 run 이 있다(응답에 기존 self_check_run_id) → 그 run 을 read 로 조회해 끝나기를 기다리거나 cancel 한 뒤 다시 실행한다.",
     "A run is already active for this TR (the response carries its self_check_run_id) -> read it and wait for it to finish, or cancel it, then run again.",
     "このTRには実行中の run がある(応答に既存の self_check_run_id) → その run を read で確認して完了を待つか cancel してから再実行する。"),
    ("selfcheck_source_busy",
     "다른 소스 변경 작업이 프로젝트 소스 잠금을 잡고 있다 → 그 작업이 끝난 뒤 잠시 후 다시 시도한다.",
     "Another source operation holds the project source lock -> retry after that operation finishes.",
     "別のソース操作がプロジェクトのソースロックを保持している → その操作の終了後にしばらくして再試行する。"),
    ("selfcheck_recovery_incomplete",
     "이전 run 의 프로세스 정리가 아직 끝나지 않았다 → 새 run 을 반복해서 밀어 넣지 말고 사유를 보고한 뒤 잠시 후 한 번 더 시도한다.",
     "Cleanup of an earlier run's process tree is unfinished -> do not hammer new runs; report the reason and retry once after a short wait.",
     "以前の run のプロセス整理が未完了 → 新しい run を連打せず、理由を報告して少し待ってから1回だけ再試行する。"),
    ("selfcheck_worktree_unavailable",
     "이 TR 의 관리 워크트리를 쓸 수 없다 → 반환된 사유를 보고한다. 다른 경로나 Bundle/Scratch 로 우회하지 않는다.",
     "The managed worktree of this TR is unavailable -> report the returned reason; do not route around it with another path or a Bundle/Scratch.",
     "このTRの管理ワークツリーを利用できない → 返された理由を報告する。別経路や Bundle/Scratch で回避しない。"),
)

_SELF_CHECK_COPY: dict[str, dict] = {
    "ko": {
        "role": "TR 수정(edit) 단계에서 테스트·검증을 실행하는 공식 경로는 Self-check 하나다. 도구는 run_self_check / read_self_check / cancel_self_check 이다.",
        "no_fallback": [
            "Source Bundle, AI Scratch, run_test 는 TR edit 의 Self-check 대체 수단이 아니다. TR edit 실행에서 access_source_bundle/run_source_bundle 은 self_check_required(409)로 거절된다.",
            "Self-check 를 쓸 수 없으면 반환된 reason/error_code 를 TR 에 보고하고, 다른 실행 수단을 찾거나 시도하지 않는다.",
            "Self-check 실행은 Source Bundle ensure 를 선행조건으로 하지 않는다. Bundle 오류가 나도 그것 때문에 Self-check 검증을 포기하지 않는다.",
            "실행할 명령은 TR 에 적힌 검증 명령 → T 에 적힌 검증 명령 → 변경에서 분명한 최소 검사 순으로 정한다. 정할 수 없으면 test_command_missing 으로 멈춘다.",
        ],
        "field_desc": {
            "program": "실행 파일 이름 하나. 예: pytest. 명령 문자열 전체 금지.",
            "args": "옵션과 대상 경로의 문자열 배열. 기본 [].",
            "cwd": "관리 워크트리 기준 상대 경로. 기본 \".\".",
            "timeout_seconds": f"실행 제한 시간(초), 1~{SELF_CHECK_TIMEOUT_MAX} 정수. 기본 {SELF_CHECK_TIMEOUT_DEFAULT}.",
        },
        "http_note": "tr_doc_id 는 지금 수정 중인 TR 문서 id(토큰에 바인딩된 문서)이고 run_id 는 run 응답의 self_check_run_id 이다. 헤더는 Authorization: Bearer <작업 토큰> 을 그대로 쓴다. 성공 응답은 {ok:true, ...run 필드}, 라우트가 만드는 실패 응답은 {ok:false, error:{code, message, details}} 이며 code 는 아래 오류 표의 selfcheck_* 또는 forbidden 이다. 다른 문서의 토큰이나 edit 가 아닌 토큰은 403 이고 error.code 는 forbidden 이다(message·details 포함). 토큰 자체가 검증에 실패하면(Authorization 헤더 없음 401, 만료·무효 토큰, 권한 없음 403) 라우트가 아니라 인증 계층이 {ok:false, http_status, error_message, help_url} 을 돌려주며 error 객체가 없다. 응답에서 확인할 것: run 은 202 와 self_check_run_id·status, read/cancel 은 200 과 status(아래 statuses 중 하나).",
        "invalid_note": "위 형식(명령 전체를 program 에 넣음)은 program/args 계약 위반이다. 'pytest' 와 ['-q', 'server/tests/test_x.py'] 로 나누어 보낸다.",
        "lifecycle": "run_self_check → self_check_run_id 수신 → pending/running 동안 read_self_check 로 반복 조회 → completed / failed / cancelled. 필요하면 cancel_self_check.",
        "non_zero": "exit_code 가 0 이 아닌 completed 는 전송 실패가 아니라 실제 검증 실패일 수 있다. stdout_tail/stderr_tail 을 읽고 → 범위 안에서 코드를 고치고 → 새 run_self_check 를 실행하는 것이 정상 절차다.",
        "failed_note": "status=failed 는 실행 자체가 실패한 것이다. error_code 를 아래 오류 표로 해석한다.",
        "changed_note": "source_changed_during_run / worktree_state_changed 가 true 이면 실행 중 소스나 워크트리 상태가 바뀐 것이므로 결과를 그대로 믿지 말고 안정된 뒤 다시 실행한다.",
    },
    "en": {
        "role": "Self-check is the one official way to run tests/verification in a TR edit step. The tools are run_self_check / read_self_check / cancel_self_check.",
        "no_fallback": [
            "Source Bundle, AI Scratch and run_test are not Self-check fallbacks for TR edit. In a TR edit run access_source_bundle/run_source_bundle are refused with self_check_required (409).",
            "If Self-check is unavailable, report the returned reason/error_code in the TR and do not look for or try another execution backend.",
            "A Self-check run does not require a Source Bundle ensure first. A Bundle error is not a reason to give up Self-check verification.",
            "Pick the command from the verification command named in the TR, else in the T, else the minimal check obvious from your change. If none can be determined, stop with test_command_missing.",
        ],
        "field_desc": {
            "program": "One executable name, e.g. pytest. Never the whole command string.",
            "args": "Array of strings: options and target paths. Default [].",
            "cwd": "Path relative to the managed worktree. Default \".\".",
            "timeout_seconds": f"Time limit in seconds, integer 1..{SELF_CHECK_TIMEOUT_MAX}. Default {SELF_CHECK_TIMEOUT_DEFAULT}.",
        },
        "http_note": "tr_doc_id is the TR document id you are editing (the document bound to the token) and run_id is the self_check_run_id from the run response. Send the same Authorization: Bearer <work token> header. Success is {ok:true, ...run fields}; route failures are {ok:false, error:{code, message, details}} where code is a selfcheck_* value from the error table below or forbidden. A token for another document or a non-edit token gets 403 with error.code forbidden (message and details included). If the token itself fails verification (missing Authorization header 401, expired or invalid token, no permission 403), the authentication layer answers instead of the route with {ok:false, http_status, error_message, help_url} and no error object. Verify in the response: run returns 202 with self_check_run_id and status; read/cancel return 200 with a status from the statuses list below.",
        "invalid_note": "The form above (whole command in program) violates the program/args contract. Send 'pytest' and ['-q', 'server/tests/test_x.py'] separately.",
        "lifecycle": "run_self_check -> receive self_check_run_id -> read_self_check repeatedly while pending/running -> completed / failed / cancelled. Use cancel_self_check if needed.",
        "non_zero": "A completed run with a non-zero exit_code is not a transport failure; it may be a real verification failure. Read stdout_tail/stderr_tail, fix the code within scope, then start a new run_self_check. That is the normal procedure.",
        "failed_note": "status=failed means the run itself failed. Interpret error_code with the error table below.",
        "changed_note": "If source_changed_during_run / worktree_state_changed is true, the source or worktree state moved during the run; do not trust the result blindly and rerun once it is stable.",
    },
    "ja": {
        "role": "TR修正(edit)段階でテスト・検証を実行する公式経路は Self-check ただ1つです。ツールは run_self_check / read_self_check / cancel_self_check です。",
        "no_fallback": [
            "Source Bundle、AI Scratch、run_test は TR edit の Self-check 代替手段ではありません。TR edit の実行では access_source_bundle/run_source_bundle は self_check_required(409)で拒否されます。",
            "Self-check が使えない場合は、返された reason/error_code を TR に報告し、他の実行手段を探したり試したりしないでください。",
            "Self-check の実行は Source Bundle の ensure を前提としません。Bundle エラーを理由に Self-check 検証を諦めないでください。",
            "実行コマンドは、TRに記載の検証コマンド → Tに記載の検証コマンド → 変更から明らかな最小の検査、の順で決めます。決められない場合は test_command_missing で停止します。",
        ],
        "field_desc": {
            "program": "実行ファイル名1つ。例: pytest。コマンド文字列全体は不可。",
            "args": "オプションと対象パスの文字列配列。既定 []。",
            "cwd": "管理ワークツリー基準の相対パス。既定 \".\"。",
            "timeout_seconds": f"実行制限時間(秒)、1〜{SELF_CHECK_TIMEOUT_MAX} の整数。既定 {SELF_CHECK_TIMEOUT_DEFAULT}。",
        },
        "http_note": "tr_doc_id は今修正中の TR 文書 id(トークンに紐づく文書)、run_id は run 応答の self_check_run_id です。ヘッダーは Authorization: Bearer <作業トークン> をそのまま使います。成功は {ok:true, ...run フィールド}、ルートが返す失敗は {ok:false, error:{code, message, details}} で、code は下のエラー表の selfcheck_* または forbidden です。他文書のトークンや edit 以外のトークンは 403 で error.code は forbidden です(message と details を含む)。トークン自体の検証に失敗した場合(Authorization ヘッダーなし 401、期限切れ・無効トークン、権限なし 403)は、ルートではなく認証層が {ok:false, http_status, error_message, help_url} を返し、error オブジェクトはありません。応答で確認すること: run は 202 と self_check_run_id・status、read/cancel は 200 と status(下の statuses のいずれか)。",
        "invalid_note": "上の形式(コマンド全体を program に入れる)は program/args 契約違反です。'pytest' と ['-q', 'server/tests/test_x.py'] に分けて送ってください。",
        "lifecycle": "run_self_check → self_check_run_id を受け取る → pending/running の間 read_self_check を繰り返す → completed / failed / cancelled。必要なら cancel_self_check。",
        "non_zero": "exit_code が 0 以外の completed は通信失敗ではなく、実際の検証失敗の可能性があります。stdout_tail/stderr_tail を読み、範囲内でコードを直し、新しい run_self_check を実行するのが正常な手順です。",
        "failed_note": "status=failed は実行自体の失敗です。error_code を下のエラー表で解釈してください。",
        "changed_note": "source_changed_during_run / worktree_state_changed が true なら実行中にソースやワークツリーの状態が変わっています。結果を鵜呑みにせず、安定してから再実行してください。",
    },
}


def _content_tr_self_check(ctx: dict) -> dict:
    locale = ctx["locale"]
    copy = _SELF_CHECK_COPY.get(locale, _SELF_CHECK_COPY[FALLBACK_LOCALE])
    idx = _LOCALE_INDEX.get(locale, 1)
    types = {"program": "string", "args": "array<string>", "cwd": "string", "timeout_seconds": "integer"}
    defaults = {"program": None, "args": [], "cwd": ".", "timeout_seconds": SELF_CHECK_TIMEOUT_DEFAULT}
    return {
        "role": copy["role"],
        "no_fallback": list(copy["no_fallback"]),
        "tools": {"run": "run_self_check", "read": "read_self_check", "cancel": "cancel_self_check"},
        "http": {
            "base_url": SELF_CHECK_HTTP_BASE,
            "note": copy["http_note"],
            "operations": [
                {"name": name, "method": method, "path": path,
                 "url": SELF_CHECK_HTTP_BASE + path, "success_status": status}
                for name, method, path, status in SELF_CHECK_HTTP_OPERATIONS
            ],
            "run_request_body": "request.example",
            "response_ok_fields": ["ok", "self_check_run_id", "status", *SELF_CHECK_RESULT_FIELDS],
            "error_shape": {"ok": False, "error": {"code": "selfcheck_*|forbidden", "message": "<code>", "details": {}}},
            "auth_error_shape": {"ok": False, "http_status": 401, "error_message": "<reason>", "help_url": "<url>"},
        },
        "request": {
            "fields": [
                {
                    "name": name,
                    "type": types[name],
                    "required": name == "program",
                    "default": defaults[name],
                    "description": copy["field_desc"][name],
                }
                for name in SELF_CHECK_REQUEST_FIELDS
            ],
            "example": dict(SELF_CHECK_EXAMPLE, args=list(SELF_CHECK_EXAMPLE["args"])),
            "invalid_example": dict(SELF_CHECK_INVALID_EXAMPLE),
            "invalid_example_note": copy["invalid_note"],
        },
        "lifecycle": {
            "flow": copy["lifecycle"],
            "statuses": list(SELF_CHECK_STATUSES),
            "result_fields": list(SELF_CHECK_RESULT_FIELDS),
            "non_zero_exit": copy["non_zero"],
            "failed": copy["failed_note"],
            "source_changed": copy["changed_note"],
        },
        "errors": [{"code": row[0], "guidance": row[idx]} for row in _SELF_CHECK_ERRORS],
    }


# ── source_bundles recovery guidance (0654 T0004) ────────────────────────────
#: (code, ko, en, ja) — what the code means and what to do next.
_BUNDLE_ERRORS: tuple[tuple[str, str, str, str], ...] = (
    ("source_changed",
     "Bundle 을 캡처하는 동안 source/worktree 가 바뀐 것을 감지한 fail-closed 일관성 가드다. 서로 다른 시점의 source 가 섞인 Bundle 을 만들지 않으려고 일부러 실패시킨다. 대상 변화: scan, hash/read, copy, post-copy verify, worktree identity/root 재확인, 동시 Bundle build. → 복구: 1) 동시 source 변경이 끝났는지 확인 2) worktree 가 안정된 상태인지 확인 3) Bundle ensure 를 다시 시도. 실패한 Bundle 은 source of truth 로 쓰지 않고 직접 promotion 하지 않는다. 일반적인 source_changed 는 별도 수동 unlock 대상이 아니다. capture 중에도 계속 변하면 반복 재시도해도 계속 실패할 수 있으니 변경이 멈춘 뒤 재시도한다.",
     "A fail-closed consistency guard: the source/worktree was detected changing while the Bundle was being captured. It fails on purpose so a Bundle never mixes source from different moments. Covered changes: scan, hash/read, copy, post-copy verify, worktree identity/root recheck, concurrent Bundle build. -> Recovery: 1) confirm the concurrent source mutation has finished 2) confirm the worktree is stable 3) retry Bundle ensure. Never use a failed Bundle as the source of truth and never promote it directly. An ordinary source_changed is not a case for a manual unlock. If the source keeps changing during capture, repeated retries will keep failing; retry once it has stopped.",
     "Bundle のキャプチャ中に source/worktree が変化したことを検知した fail-closed の整合性ガード。異なる時点の source が混ざった Bundle を作らないよう意図的に失敗させる。対象: scan、hash/read、copy、post-copy verify、worktree identity/root の再確認、並行 Bundle build。→ 復旧: 1) 並行する source 変更が終わったか確認 2) worktree が安定しているか確認 3) Bundle ensure を再試行。失敗した Bundle を source of truth にせず、直接 promotion もしない。通常の source_changed は手動 unlock の対象ではない。キャプチャ中に変化し続けると再試行しても失敗し続けるので、変化が止まってから再試行する。"),
    ("source_busy",
     "다른 source operation 이나 Self-check 가 project source lock 을 쓰는 중이다 → 그 작업이 끝난 뒤 다시 시도한다.",
     "Another source operation or Self-check holds the project source lock -> retry after it finishes.",
     "他の source operation や Self-check が project source lock を使用中 → その作業の終了後に再試行する。"),
    ("build_wait_timeout",
     "동시에 진행 중인 Bundle build 의 완료를 제한 시간 안에 확인하지 못했다 → Bundle 상태를 확인한 뒤 다시 시도한다.",
     "The concurrent Bundle build did not finish within the wait limit -> check the Bundle state, then retry.",
     "並行する Bundle build の完了を制限時間内に確認できなかった → Bundle の状態を確認してから再試行する。"),
    ("group_worktree_unavailable",
     "정확한 managed group worktree 를 확인할 수 없다 → Group/worktree 상태를 복구한 뒤 다시 시도한다.",
     "The exact managed group worktree cannot be confirmed -> restore the group/worktree state, then retry.",
     "正確な managed group worktree を確認できない → Group/worktree の状態を復旧してから再試行する。"),
    ("build_timeout",
     "Bundle build 가 시간 상한을 넘었다 → source 크기, 환경, 설정을 확인한다.",
     "The Bundle build exceeded its time ceiling -> check source size, environment and settings.",
     "Bundle build が時間上限を超えた → source サイズ、環境、設定を確認する。"),
    ("resource_limit",
     "파일 수, 개별 파일 크기, 총 크기 제한을 넘었다 → source, 제외 정책, limit 을 확인한다.",
     "The file-count, per-file size or total size limit was exceeded -> check the source, exclusion policy and limits.",
     "ファイル数・個別サイズ・総サイズの上限を超えた → source、除外ポリシー、limit を確認する。"),
    ("unsafe_path",
     "symlink, reparse point, 특수 파일 등 안전성 위반 경로가 있다 → source 구조를 고쳐야 한다.",
     "A path violates safety rules (symlink, reparse point, special file, ...) -> the source structure must be fixed.",
     "symlink・reparse point・特殊ファイルなど安全性違反のパスがある → source 構造の修正が必要。"),
    ("bundle_unavailable",
     "요청한 Bundle 이 정상 created + integrity 상태가 아니다 → 새로 Bundle ensure 를 한다.",
     "The requested Bundle is not in a healthy created + integrity state -> run a fresh Bundle ensure.",
     "要求した Bundle が正常な created + integrity 状態ではない → 新たに Bundle ensure を行う。"),
)

_BUNDLE_RECOVERY_NOTE: dict[str, str] = {
    "ko": "Bundle 오류는 기능 고장이 아니라 다음 행동이 정해진 안내 대상이다. TR edit 단계의 검증은 Bundle 이 아니라 Self-check(tr_self_check 항목)로 수행하므로 Bundle 오류 때문에 검증을 포기하지 않는다.",
    "en": "A Bundle error is guidance with a defined next action, not a broken feature. TR edit verification runs through Self-check (the tr_self_check item), not a Bundle, so a Bundle error is no reason to skip verification.",
    "ja": "Bundle エラーは機能の故障ではなく、次の行動が決まっている案内対象です。TR edit 段階の検証は Bundle ではなく Self-check(tr_self_check 項目)で行うため、Bundle エラーを理由に検証を諦めないでください。",
}


def _content_source_bundles(ctx: dict) -> dict:
    payload = _content_source_snapshots(ctx)
    locale = ctx["locale"]
    idx = _LOCALE_INDEX.get(locale, 1)
    payload["error_recovery"] = {
        "note": _BUNDLE_RECOVERY_NOTE.get(locale, _BUNDLE_RECOVERY_NOTE[FALLBACK_LOCALE]),
        "errors": [{"code": row[0], "guidance": row[idx]} for row in _BUNDLE_ERRORS],
    }
    return payload


_CONTENT_SUPPLIERS = {
    "notices": _content_notices,
    "source_bundles": _content_source_bundles,
    "source_snapshots": _content_source_snapshots,
    "group_documents": _content_group_documents,
    "document_access": _content_document_access,
    "document_attachments": _content_document_attachments,
    "doc_type": _content_doc_type,
    "question": _content_question,
    "submit": _content_submit,
    "test_commands": _content_test_commands,
    "tr_self_check": _content_tr_self_check,
    "changed_files_format": _content_changed_files_format,
    "step_verification_format": _content_step_verification_format,
}


def _item_notes(name: str, ctx: dict) -> list[str]:
    locale = ctx["locale"]
    base = ctx["base_url"]
    if name == "group_documents":
        return [_copy(_ITEM_NOTES, locale, "group_documents").format(base=base)]
    if name == "document_access":
        return [_copy(_ITEM_NOTES, locale, "document_access")]
    if name == "document_attachments":
        return [_copy(_ITEM_NOTES, locale, "document_attachments")]
    if name == "question":
        return [QUESTION_HELP_COPY[locale]["continue_note"]]
    if name == "design_template":
        return [_copy(_ITEM_NOTES, locale, "design_template_children")]
    if name == "changed_files_format":
        return [_copy(_ITEM_NOTES, locale, "changed_files")]
    if name == "source_tools":
        registry = ctx["registry"] or {"notes": []}
        return tool_registry.items_view_notes(registry, locale)
    return []


def build_item(name: str, ctx: dict) -> dict:
    """One item body, envelope excluded — the same shape a bulk entry carries."""
    form = ITEM_FORM[name]
    payload: dict = {
        "name": name,
        "title": _title_for(name, ctx),
        "form": form,
    }

    if name == "source_tools":
        registry = ctx["registry"] or {"kind": "none", "source_mode": None, "reason": None}
        payload["kind"] = registry["kind"]
        payload["source_mode"] = registry["source_mode"]
        payload["reason"] = registry["reason"]
        # The same second catalog /help/tools returns (0523 T0004 s.17). The source-tool
        # item must not be the one surface left answering "source tree only".
        payload["document_attachments"] = tool_registry.attachment_view(
            registry["kind"], ctx["locale"], ctx["base_url"]
        )

    if form == "children":
        payload["children"] = enumerate_children(name, ctx)
        if name == "design_template":
            doc_type = ctx.get("doc_type")
            payload["default_child"] = doc_type if _is_design_type(doc_type) else None
    else:
        payload["content"] = _CONTENT_SUPPLIERS[name](ctx)

    notes = _item_notes(name, ctx)
    # An empty registry is a normal answer, not a failure — say so rather than
    # returning a silent empty list (L-0005 §5, "no test commands").
    if name == "test_commands" and not payload["content"]["has_commands"]:
        notes = notes + [_copy(_ITEM_NOTES, ctx["locale"], "test_commands_empty")]
    if notes:
        payload["notes"] = notes
    return payload


# ── Child bodies ─────────────────────────────────────────────────────────────

def _child_source_tool(child: str, ctx: dict) -> dict:
    base = ctx["base_url"]
    registry = ctx["registry"] or {"kind": "none"}
    return {
        "name": "source_tools",
        "child": child,
        "form": "content",
        "kind": registry["kind"],
        "content": tool_registry.build_tool_detail(child, ctx["locale"], base),
        "notes": tool_registry.detail_notes(child, ctx["locale"]),
    }


_WORK_PLAN_TEMPLATE_TITLE: dict[str, dict[str, str]] = {
    "ko": {"title": "작업계획", "summary": "작업계획(WP) 정본 JSON 서식."},
    "en": {"title": "Work Plan", "summary": "Canonical JSON body of a work plan (WP)."},
    "ja": {"title": "作業計画", "summary": "作業計画(WP)の正本 JSON 書式。"},
}


def _child_design_template(child: str, ctx: dict) -> dict:
    locale = ctx["locale"]
    project = ctx.get("project")
    if str(child or "").upper() == WORK_PLAN_TYPE:
        # A work plan body is JSON, so it has no Markdown skeleton to resolve, no
        # project override and no template row — the canonical shape comes from the
        # same service that validates it, which is why it cannot drift from the rules.
        content = work_plan_service.template_payload(locale, project)
        return {
            "name": "design_template",
            "child": WORK_PLAN_TYPE,
            "form": "content",
            "content": content,
        }
    try:
        resolved = template_provision.resolve_active_template(project, child, locale)
        meta = template_provision.resolve_active_meta(project, child, locale)
        rendered = template_provision.render_provision_block(child, locale, resolved)
    except template_provision.UnknownDesignType:
        raise
    except Exception as exc:
        raise HelpSupplierError(f"template resolution failed for {child}") from exc

    # render_provision_block emits `heading, *provenance badges, "", body`; split at
    # the blank line so the caller gets the badge text without re-deriving the copy.
    head_lines: list[str] = []
    for line in rendered.split("\n"):
        if line == "":
            break
        head_lines.append(line)

    notes = [_copy(_ITEM_NOTES, locale, "design_template_body")]
    if meta["resolved_locale"] and meta["resolved_locale"] != meta["requested_locale"]:
        notes.append(_copy(_ITEM_NOTES, locale, "design_template_fallback"))

    return {
        "name": "design_template",
        "child": child,
        "form": "content",
        "content": {
            "type_code": child,
            # P0009 §6: every template used to be Markdown, so its format never had to be
            # stated. WP is JSON, so the format is now said out loud on BOTH branches —
            # a worker must not have to infer it from the body it happens to receive.
            "body_format": "markdown",
            "requested_locale": meta["requested_locale"],
            "resolved_locale": meta["resolved_locale"],
            "resolution": meta["resolution"],
            "scope": meta["scope"],
            "available_locales": meta["available_locales"],
            "bytes": meta["bytes"],
            "template_id": resolved["resolved_template_id"],
            "heading": head_lines[0] if head_lines else "",
            "provenance": "\n".join(head_lines[1:]) or None,
            "body": resolved["content"],
            "rendered": rendered,
        },
        "notes": notes,
    }


def _authoring_guide_body(type_code: str, locale: str) -> str:
    # Imported lazily: the mention builders own this copy, and importing them at
    # module scope would drag the whole mention assembly into every help request.
    from modules.flow_gate.services import mention_service

    if type_code == "T2":
        return {
            "ko": "TR2가 edit-spec으로 표현할 수 있는 소스 변경을 지시하는 Markdown 지시서를 작성합니다.",
            "en": "Write a Markdown instruction for source changes expressible as a TR2 edit-spec.",
            "ja": "TR2 の edit-spec で表現できるソース変更を Markdown で指示します.",
        }.get(locale, "TR2가 edit-spec으로 표현할 수 있는 소스 변경을 지시하는 Markdown 지시서를 작성합니다.")
    if type_code == "TR2":
        return {
            "ko": "본문은 Markdown이 아닌 canonical JSON(document.json)입니다. tr2_version=1, source_t2_doc_id, edit_spec의 termination/edits/deferred/gate를 제출하세요. source는 읽기 전용입니다. 직접 수정하지 말고 edit/create_file로 제안하세요. baseline_fingerprint는 서버가 계산합니다. ready_to_apply에는 edit가 한 건 이상 있어야 하고 gate.apply는 false입니다.",
            "en": "Submit canonical JSON (document.json), not Markdown: tr2_version=1, source_t2_doc_id, edit_spec with termination, edits, deferred, gate. Source is read-only; propose edit/create_file entries. The server computes baseline_fingerprint. ready_to_apply needs at least one edit; gate.apply must be false.",
            "ja": "本文は Markdown ではなく canonical JSON(document.json) です。tr2_version=1、source_t2_doc_id、termination/edits/deferred/gate を含む edit_spec を提出します。ソースは読み取り専用です。変更は edit/create_file で提案します。baseline_fingerprint はサーバーが計算します。ready_to_apply には一件以上の edit が必要で、gate.apply は false です。",
        }.get(locale, "TR2 canonical JSON(document.json): tr2_version=1, source_t2_doc_id, edit_spec; source read-only; gate.apply=false.")
    if type_code == "TS":
        return mention_service._ts_authoring_section(locale)
    if type_code in {"N", "T"}:
        return mention_service._nt_authoring_section(type_code, locale)
    if type_code == "TR":
        return _TR_AUTHORING_GUIDE.get(locale, _TR_AUTHORING_GUIDE[FALLBACK_LOCALE])
    raise ValueError(f"Unknown authoring guide type: {type_code}")


def _child_authoring_guide(child: str, ctx: dict) -> dict:
    locale = ctx["locale"]
    try:
        body = _authoring_guide_body(child, locale)
    except Exception as exc:
        raise HelpSupplierError(f"authoring guide build failed for {child}") from exc
    return {
        "name": "authoring_guide",
        "child": child,
        "form": "content",
        "content": {
            "type_code": child,
            "title": _copy(_AUTHORING_GUIDE_TITLES, locale, child),
            "body": body,
        },
    }


def build_child(name: str, child: str, ctx: dict) -> dict:
    if name == "source_tools":
        return _child_source_tool(child, ctx)
    if name == "design_template":
        return _child_design_template(child, ctx)
    if name == "authoring_guide":
        return _child_authoring_guide(child, ctx)
    raise HelpSupplierError(f"item '{name}' has no children")


# ── Bulk (L-0005 §2-7) ───────────────────────────────────────────────────────

class BulkRequestError(ValueError):
    """``items`` was empty or over the per-request cap — 422, never a 403."""


def parse_bulk_names(raw_items: str) -> list[str]:
    """Comma-separated names, trimmed, de-duplicated, order preserved."""
    names: list[str] = []
    for part in (raw_items or "").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    if len(names) < BULK_ITEM_MIN:
        raise BulkRequestError("items must contain at least one help item name")
    if len(names) > BULK_ITEM_MAX:
        raise BulkRequestError(
            f"Too many help items requested: {len(names)} (max {BULK_ITEM_MAX}). "
            "Use detail=true to expand everything."
        )
    return names


def build_bulk(requested_names: list[str], ctx: dict) -> tuple[list[dict], list[dict]]:
    """Return ``(items, unavailable)``; the caller decides the HTTP status."""
    items: list[dict] = []
    unavailable: list[dict] = []
    for name in requested_names:
        if name not in _CATALOG:
            unavailable.append({"name": name, "http_status": 404, "reason": "unknown_item"})
            continue
        decision = decide_visibility(name, ctx)
        if not decision.visible:
            unavailable.append({"name": name, "http_status": 403, "reason": decision.reason})
            continue
        items.append(build_item(name, ctx))
    return items, unavailable
