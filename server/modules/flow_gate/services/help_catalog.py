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
    "design_template",
    "authoring_guide",
    "test_commands",
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
    "design_template": "children",
    "authoring_guide": "children",
    "test_commands": "content",
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
GUIDE_TYPES = frozenset({"N", "T", "TR", "TS"})
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
        "design_template": "설계서 템플릿",
        "authoring_guide": "작성 지침",
        "test_commands": "검증된 테스트 명령",
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
        "design_template": "Design document template",
        "authoring_guide": "Authoring guide",
        "test_commands": "Verified test commands",
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
        "design_template": "設計書テンプレート",
        "authoring_guide": "作成ガイド",
        "test_commands": "検証済みテストコマンド",
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
        "design_template": "설계 타입별 표준 템플릿 본문.",
        "authoring_guide": "이 타입의 문서를 쓰는 방법.",
        "test_commands": "이 프로젝트에 등록된, 실행이 확인된 테스트 명령.",
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
        "design_template": "Standard template body per design type.",
        "authoring_guide": "How to write a document of this type.",
        "test_commands": "Test commands registered for this project and verified on this host.",
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
        "design_template": "設計タイプ別の標準テンプレート本文。",
        "authoring_guide": "このタイプの文書を書く方法。",
        "test_commands": "このプロジェクトに登録され、実行が確認されたテストコマンド。",
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
        "continuous_no_stop": "작업을 중단하지 마십시오. 한 단계를 마치면 응답의 다음 토큰과 멘트로 곧장 다음 단계를 진행하십시오.",
        "continuous_autonomous": "불명확한 점이 있어도 선택지를 제시하거나 질문으로 멈추지 말고, 가진 정보로 가장 합리적인 판단을 내려 진행하십시오.",
        "interactive_query_without_choice": "진행을 막는 정보가 없으면 문서에 연결된 질의(Q)를 등록하십시오. 콘솔 선택지를 요구하지 말고, 안전한 가정을 세울 수 있으면 그 가정을 명시하고 계속 진행하십시오.",
        "review_no_modify": "검토 작업에서는 대상 문서를 수정하거나 새 결과 문서를 만들지 말고, 지정된 판정만 제출하십시오.",
        "investigation_only": "이 단계는 조사 전용입니다. 소스 파일을 수정·생성·삭제하지 마십시오.",
        "assigned_scope_only": "이 작업에 배정된 그룹 작업 공간과 파일만 변경하고, 변경 파일은 작업 레포트에 빠짐없이 보고하십시오.",
    },
    "en": {
        "continuous_unattended": "This task is part of an UNMANNED continuous work chain. Nobody is watching.",
        "continuous_no_stop": "Do not stop. When one step finishes, continue straight into the next step with the token and mention in the response.",
        "continuous_autonomous": "Even when something is unclear, do not present choices or stop to ask. Make the most reasonable judgment from what you have and continue.",
        "interactive_query_without_choice": "If information you need is missing, register a query (Q) bound to the document. Do not demand a console choice; when a safe assumption exists, state it and continue.",
        "review_no_modify": "In a review step, do not modify the target document or create a new result document — submit only the verdict you were asked for.",
        "investigation_only": "This step is investigation-only. Do not modify, create or delete source files.",
        "assigned_scope_only": "Change only the group workspace and files assigned to this task, and report every changed file in the work report.",
    },
    "ja": {
        "continuous_unattended": "この作業は無人(UNMANNED)連続作業チェーンの一部です。人は見ていません。",
        "continuous_no_stop": "作業を中断しないでください。一段階終えたら、応答に含まれる次のトークンとメントでそのまま次の段階へ進んでください。",
        "continuous_autonomous": "不明な点があっても選択肢を提示したり質問で止まったりせず、手持ちの情報で最も合理的な判断を下して進めてください。",
        "interactive_query_without_choice": "進行を妨げる情報が無い場合は、文書に紐づく質問(Q)を登録してください。コンソールの選択肢を求めず、安全な仮定を立てられるならそれを明記して続行してください。",
        "review_no_modify": "レビュー作業では対象文書を修正したり新しい結果文書を作成したりせず、指定された判定のみを提出してください。",
        "investigation_only": "この段階は調査専用です。ソースファイルを修正・作成・削除しないでください。",
        "assigned_scope_only": "この作業に割り当てられたグループ作業領域とファイルのみ変更し、変更ファイルは作業レポートに漏れなく報告してください。",
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
    "ko": {"N": "조사지시 작성", "T": "작업지시 작성", "TR": "작업레포트 작성", "TS": "테스트시나리오 작성"},
    "en": {"N": "Writing an investigation instruction", "T": "Writing a work instruction",
           "TR": "Writing a work report", "TS": "Writing a test scenario"},
    "ja": {"N": "調査指示の作成", "T": "作業指示の作成", "TR": "作業レポートの作成", "TS": "テストシナリオの作成"},
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

    if name == "source_tools":
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
    return {"lines": [_copy(NOTICE_LINES, ctx["locale"], key) for key in ordered]}


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
            "dry_run": dry_run,
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


_CONTENT_SUPPLIERS = {
    "notices": _content_notices,
    "group_documents": _content_group_documents,
    "document_access": _content_document_access,
    "document_attachments": _content_document_attachments,
    "doc_type": _content_doc_type,
    "question": _content_question,
    "submit": _content_submit,
    "test_commands": _content_test_commands,
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

    if type_code == "TS":
        return mention_service._ts_authoring_section(locale)
    if type_code in {"N", "T"}:
        return mention_service._nt_authoring_section(type_code, locale)
    # TR is the one guide type the mention never carried a block for.
    return _TR_AUTHORING_GUIDE.get(locale, _TR_AUTHORING_GUIDE[FALLBACK_LOCALE])


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
