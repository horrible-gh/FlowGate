"""Protected-Korean allowlist (T0009 work item 1).

Coordinates in this module are the B list (functional Korean, NR0008 §4) plus known A
(locale-dictionary) corrections that the new-source guards (test_server_korean_leak_0355.py's
widened call-arg scan, test_korean_source_census_0430.py's line scanner) must not flag.

Every entry below was grep/read-verified against the actual 2026-08-17 source before being
registered here (T0009 §3, work item 1). Where NR0008's coordinates (line numbers, symbol
spelling) drifted from the real file, the verified value is used and the drift is noted.
"""
from __future__ import annotations

# Each item: file is a path relative to server/ (matching how the guards resolve
# _SERVER_DIR-relative paths). symbols is a list of literal substrings (module-level
# constant names, or literal Korean snippets when no named constant exists) that are
# allowed to appear unbranched. reason explains what the string is FOR — not why it is
# Korean. tests lists the suites that already pin this literal's exact wording, so a
# translation would break them.
PROTECTED = [
    {
        "file": "modules/flow_gate/conversation.py",
        "symbols": ["_USER_NAME_TO_LOCALE", "_SPEAKER_ALT", "사용자"],
        "reason": (
            "Conversation Markdown parser's speaker/locale inference — "
            "'사용자' is the literal ko speaker header the regex/dict match."
        ),
        "tests": [
            "tests/test_conversation_0044.py::test_mixed_locale_conversation_parses_every_turn_as_user",
        ],
    },
    {
        "file": "modules/flow_gate/services/mention_service.py",
        "symbols": ["이 작업은 무인(UNMANNED) 연속 작업 체인의 일부입니다"],
        "reason": (
            "Exact leading sentence of the unmanned-continuous-work directive sent to "
            "AI workers — several suites assert this precise ko prefix."
        ),
        "tests": [
            "tests/test_help_items_0372.py:291",
            "tests/test_continuous_work_0051.py:51,67,148",
            "tests/test_workflow_decision_mention_q_guide_0110.py:90",
            "tests/test_test_run_chain_0150.py:168,208",
        ],
    },
    {
        "file": "modules/flow_gate/services/tr_scope_service.py",
        "symbols": ["SECTION_HEADING", "NONE_MARKER"],
        "reason": (
            "TR '## 변경 파일' heading parser and its '없음' none-marker — the protocol "
            "grammar TR submissions are validated against (NR0008's headline example)."
        ),
        "tests": [
            "tests/test_tr_scope_0299.py",
            "tests/test_document_outline_0370.py:280,304",
        ],
    },
    {
        "file": "modules/flow_gate/workflow/prompt_copy_service.py",
        "symbols": ["## 사용자 질의응답"],
        "reason": "Literal Q&A section heading appended to prompts sent to AI providers.",
        "tests": ["tests/test_prompt_copy_qa_options_0243.py:26"],
    },
    {
        "file": "modules/flow_gate/services/q_answer_invoke_service.py",
        # NR0008 §4 #4 lists both markers under one row; verified 2026-08-17 that
        # The user-Q&A heading lives in BOTH this module and prompt_copy_service.py, so it
        # is registered in both entries rather than only the latter.
        "symbols": ["[질의]", "## 사용자 질의응답"],
        "reason": "Literal prompt-assembly markers sent to AI providers ahead of the Q&A body.",
        "tests": [
            "tests/test_mention_tools_0349.py:167,182",
            "tests/test_document_query_mentions_0370.py:172",
            "tests/test_workflow.py:483-576",
        ],
    },
    {
        "file": "modules/flow_gate/services/test_run_service.py",
        "symbols": [
            "TEST_CASES_SECTION_NAMES", "SETUP_SECTION_NAMES", "TEARDOWN_SECTION_NAMES",
            "_DISPLAY_FIELD",
            # The literal parser tokens themselves — the guards match on the LITERAL a
            # scan resolved, not on the constant's name, so the names above alone never
            # exempt anything. _DISPLAY_FIELD's values reach a TestCaseParseError raise
            # through a local variable, which the local-hop widening (work item 3) now follows.
            "테스트 케이스", "테스트 준비", "테스트 정리", "기대", "기동", "대기",
        ],
        "reason": (
            "TS document body parser: section headings ('테스트 케이스'/'테스트 준비'/"
            "'테스트 정리') and field-label lookup ('기대'/'기동'/'대기') that extract "
            "test cases from a submitted TS. These are INPUT parser tokens — distinct "
            "from the auto-generated TSR report body headings ('## 실행 환경' etc.), "
            "which are output-only and were reclassified D (작업 2, 작업 4 표)."
        ),
        "tests": [
            "tests/test_server_korean_leak_0355.py::test_runtime_generated_instructions_and_errors_have_zero_korean",
        ],
    },
    {
        "file": "tools/gen_seed_045.py",
        "symbols": ["D_KO", "P_KO", "L_KO", "DB_KO"],
        "reason": (
            "Seed-template section structure (e.g. '## 목적', '## 작성 원칙') that later "
            "document submissions are validated against — translating the ko template "
            "body would desync new D/P/L/DB templates from the structure check."
        ),
        "tests": [
            "tests/test_inbox_dry_run_R0001.py::test_new_design_dry_run_passes_after_template_structure_match",
            "tests/test_inbox_dry_run_R0001.py::test_new_design_dry_run_rejects_template_mismatch_before_counting",
        ],
    },
    {
        "file": "modules/flow_gate/services/tool_registry.py",
        "symbols": ["EXAMPLE_RESPONSES"],
        "reason": (
            "Tool-catalog documentation embeds real wire-format response examples "
            "(continuation.ment etc.) — some example payloads are themselves Korean "
            "text a worker would send/receive, not translatable UI copy."
        ),
        "tests": [
            "tests/test_tool_catalog_parity_0356.py",
            "tests/test_remote_tool_0003_T0012.py:740",
        ],
    },
    {
        "file": "modules/flow_gate/services/remote_tool_service.py",
        "symbols": ['re.search(r"[가-힣]", exc.message)'],
        "reason": (
            "Not translatable copy — the code uses the Hangul-range regex AS LOGIC to "
            "decide whether an upstream error message is already Korean. Deleting/"
            "translating the pattern changes behavior, not wording; left as-is per "
            "NR0008 §4 #8 (no test pins it explicitly — flagged in NR0008 §6 미해결 질문 1 "
            "as needing a unit test before any future change)."
        ),
        "tests": [],
    },
    {
        "file": "client/src/main/stores/docTypeStore.ts",
        "symbols": ["INSTRUCTION_SUFFIXES"],
        "reason": (
            "getSetName() strips N/T/TS document-name suffixes ('지시'/'指示'/' Instruction') "
            "by literal match — out of this T's server-only scope (§1.3), registered here "
            "for completeness per NR0008 §4 #9. No unit test currently pins it "
            "(NR0008 §6 미해결 질문 1)."
        ),
        "tests": [],
    },
    {
        "file": "tests/test_mention_reduction_0372.py",
        "symbols": ["_HELP_HEADERS"],
        "reason": (
            "Test fixture asserting the actual chat-client-grepped help heading "
            "('## 도움말' 등) — out of this T's server/modules scope, registered for "
            "completeness per NR0008 §4 #10."
        ),
        "tests": ["tests/test_mention_reduction_0372.py"],
    },
    {
        "file": "tests/test_work_plan_proposal_0405.py",
        "symbols": ["SCOPE_HEADER", "## 작업계획 맡길 범위"],
        "reason": (
            "Fixed contract heading for the work-plan-proposal scope section (0405 T0011 "
            "rev1 rejection basis) — out of this T's server/modules scope, registered for "
            "completeness per NR0008 §4 #11."
        ),
        "tests": ["tests/test_work_plan_proposal_0405.py"],
    },
    {
        "file": "modules/flow_gate/services/mention_service.py",
        "symbols": [
            "작업계획 범위 채우기", "본문은 Markdown이 아니라", "수량을 정해도 되는 타입",
            "프로바이더와 한줄 멘트를 정해도 되는 단계", "고를 수 있는 프로바이더",
            "범위 밖 값은 지금 값 그대로 두십시오", "note를 반드시 채우십시오",
            "정본 JSON 전체를 인박스 수정",
        ],
        "reason": (
            "NR0008 §3.1 D 표 정정 (T0009 §3.2 이미 확인): build_work_plan_fill_mention()'s "
            "field-label `copy` dict (~line 2300) has full ko/en/ja siblings — it is a "
            "normal locale dictionary (A), NOT the locale-free D hardcode NR0008 first "
            "classified it as. Left untranslated; ko/en/ja triplet already exists."
        ),
        "tests": [],
    },
    {
        "file": "modules/flow_gate/services/invoke_mention_service.py",
        "symbols": [
            "직전 AI 실행", "직전 실행({runId})이 제한시간에 걸려", "중단 진단", "소스 상태",
            "그 실행이 시작된 뒤 생긴 변경", "작업 폴더에 남은 변경이 있지만",
            "그 실행이 남긴 변경은 없습니다", "작업 폴더 상태를 확인하지 못했습니다",
            "이 파일들은 이전 세션의 미완 변경일 수 있습니다",
        ],
        "reason": (
            "0446 T0016 §4-5: the ko half of build_previous_run_section()'s label "
            "dictionaries (_PREV_RUN_*). Every one has full ko/en/ja siblings in the same "
            "dict, so this is a normal locale dictionary (A) — the same class as the four "
            "_MM_SECTION_HEADER / _REJECT_TEMPLATE / _DESIGN_* entries already in this "
            "file, which the census counts under its cap for the same reason."
        ),
        "tests": [
            "tests/test_ai_invoke_run_diagnostics_0446.py::TestPreviousRunBlock",
            "tests/test_server_korean_leak_0355.py::test_static_locale_branch_scan_has_zero_korean",
        ],
    },
    {
        "file": "modules/flow_gate/api/inbox_routes.py",
        "symbols": [
            "_SERVER_ASSEMBLED_NEW_COPY",
            # The census matches the resolved LITERAL, not the constant name, so the ko
            # branch's own three source lines are registered as well.
            "테스트 실행 결과로 서버가 조립하는 문서입니다",
            "승인된 테스트시나리오(TS) 문서로 테스트를",
            "만들어 워크플로에 등록합니다",
        ],
        "reason": (
            "0441 T0004 item 3: the ko branch of _SERVER_ASSEMBLED_NEW_COPY, the refusal a "
            "worker reads when it tries to submit a hand-written TSR. Category A "
            "(locale-dictionary): the en and ja branches carry the same sentence and are "
            "scanned for leakage by test_server_korean_leak_0355.py as usual. Registered "
            "rather than added to the file's line cap, so this T contributes zero to the "
            "census budget."
        ),
        "tests": [
            "tests/test_server_assembled_tsr_0441.py::test_inbox_new_tsr_refusal_speaks_the_workers_locale",
        ],
    },
    {
        "file": "modules/flow_gate/api/inbox_routes.py",
        "symbols": [
            "_REVIEW_ROUND_DUPLICATE_MESSAGES",
            # The census matches the resolved LITERAL, not the constant name.
            "이 AI 검수 회차에는 이미 등록된 결과가 있어 두 번째 검수 결과를 등록하지 않았습니다.",
        ],
        "reason": (
            "flowgate.default.0583 T0004 section 7: the ko branch of "
            "_REVIEW_ROUND_DUPLICATE_MESSAGES, the answer a worker reads when it submits "
            "a second verdict for a review round that already has one. Category A "
            "(locale-dictionary): the en and ja branches carry the same sentence and are "
            "scanned for leakage by test_server_korean_leak_0355.py as usual. Registered "
            "rather than added to the file's line cap, so this T contributes zero to the "
            "census budget -- same convention as the _SERVER_ASSEMBLED_NEW_COPY entry "
            "above."
        ),
        "tests": [
            "tests/test_review_round_idempotency_0583.py::test_the_duplicate_answer_speaks_the_requested_locale",
        ],
    },
    {
        "file": "modules/flow_gate/api/token_routes.py",
        "symbols": [
            "이 재지시는 [수정 적용]으로 시작되었습니다", "직접 파일을 쓰는 도구는 이 실행에 없습니다",
            "제출하는 것이 유일한 반영 경로이며", "실패 시 그대로 롤백합니다",
            "테스트 경로 편집이 이번 재지시에서 허용되었습니다", "제품 코드와 같은 절차로 operations[]에 넣어도 됩니다",
            "경로 세그먼트 test/tests)의 변경은", "operations[]에 넣지 마십시오",
            "적용되지 않은 채 사람에게 표시됩니다", "위 세션이 보여준 `review_fingerprint`와 정확히 같아야 합니다",
            "read/grep/glob/stat/diff/log/show 도구로 현재 상태를 다시 확인하십시오",
            "치환 대상 자체의 정확한 바이트이며", "않는 일치 수가 `expected_count`와",
            "세션이 보여준 스냅샷에 없는 경로에만 허용됩니다", "fallback이 아닙니다",
            "모든 바이트 필드는 base64입니다", "`.git` 내부 경로는 거절됩니다",
            "이 창구는 이 group_id와 merge_id에 바인딩된 토큰만 받습니다",
            "엔드포인트는 이 토큰으로 접근할 수 없습니다",
            "## Write plan 제출",
        ],
        "reason": (
            "flowgate.default.0578 T0012 §2.1/§2.3: the ko branch of token_routes.py's "
            "_WRITE_PLAN_COPY -- the merge-review write-plan channel's procedure copy, now "
            "locale-aware (ko/en/ja siblings). Category A (locale-dictionary): the en/ja "
            "branches are scanned for leakage by test_server_korean_leak_0355.py as usual. "
            "Registered rather than added to the file's line cap, so this T contributes "
            "zero to the census budget -- same convention as the inbox_routes.py "
            "_SERVER_ASSEMBLED_NEW_COPY entry above."
        ),
        "tests": [
            "tests/test_server_korean_leak_0355.py::test_runtime_generated_instructions_and_errors_have_zero_korean",
        ],
    },
    {
        "file": "templates/flow_gate/group_detail.html",
        "symbols": ["다음 예상 액션", "멘트복사"],
        "reason": (
            "Product-facing UI copy, not a code comment or error message — T0009 §5 found no "
            "router serves this template with a locale signal, so inventing a locale scheme "
            "here would be unsupported guesswork. A pinned regression test asserts this exact "
            "ko text, confirming it is deliberate, not leftover."
        ),
        "tests": [
            "tests/test_process_service_next_actions.py::test_group_detail_template_renders_next_action_panel",
        ],
    },
    {
        "file": 'modules/flow_gate/services/test_spec_service.py',
        "symbols": [
            'SPEC_SECTION_NAMES = ("시험 사양", "Test Specification", "試験仕様")',
            '_SPEC_SECTION_BY_LOCALE = {"ko": "시험 사양", "en": "Test Specification", "ja": "試験仕様"}',
            '"category": "category", "kind": "category", "분류": "category", "分類": "category",',
            '"requirement_ref": "requirement", "covers": "requirement", "대상": "requirement",',
            '"검증 대상": "requirement", "요구사항": "requirement", "対象": "requirement",',
            '"실행 방식": "execution_mode", "実行方式": "execution_mode",',
            '"required": "required", "필수": "required", "必須": "required",',
            '"전제조건": "precondition", "전제": "precondition", "前提条件": "precondition",',
            '"input": "input", "inputs": "input", "입력": "input", "입력/조건": "input",',
            '"procedure": "procedure", "steps": "procedure", "절차": "procedure",',
            '"시험 절차": "procedure", "手順": "procedure",',
            '"expected": "expected", "expect": "expected", "기대": "expected",',
            '"기대 결과": "expected", "期待結果": "expected", "期待": "expected",',
            '"확인 관점": "check_points", "確認観点": "check_points",',
            '"automation_ref": "automation_ref", "자동화 참조": "automation_ref",',
            '_LEGACY_FIELDS = {"cmd", "assert", "기동", "start", "대기", "wait"}',
            '_TRUE_WORDS = {"true", "yes", "y", "1", "required", "필수", "必須", "o"}',
            '_FALSE_WORDS = {"false", "no", "n", "0", "optional", "선택", "任意", "x"}',
            '"No \'## Test Specification\' (## 시험 사양 / ## 試験仕様) section.",',
            '"kind": "> 문서 성격: 시험성적서 (TS {doc_id} revision {revision} 의 Case ID별 결과)",',
            '"run": "> 결과 기록: {run_id} · 기록 시각 {at}",',
            '"overall": "> 종합 판정(서버 계산, 필수 Case 기준): **{overall}** — {gate}",',
            '"gate_pass": "시험 gate 통과",',
            '"gate_block": "시험 gate 미통과 · 다음 단계 진행 차단",',
            '"summary_heading": "## 요약",',
            '"summary_header": "| 구분 | 전체 | PASS | FAIL | BLOCKED | NOT_RUN |",',
            '"all": "전체", "required": "필수", "optional": "선택",',
            '"cases_heading": "## Case별 결과",',
            '"cases_header": "| Case | 제목 | 필수 | 방식 | 기대 결과 | 실제 결과 | 판정 | 결함/참조 |",',
            '"evidence_heading": "## Evidence 및 source identity",',
            '"unmapped_heading": "## TS에 없는 결과 (unmapped)",',
            '"conflicts_heading": "## 중복 매핑 (mapping conflict)",',
            '"none": "없음",',
            '"carried": "이전 기록에서 유지: {run_id}",',
            '"footer": "*이 시험성적서는 결과 기록 {run_id} 로부터 FlowGate가 조립했다. 종합 판정은 문서 본문이 아니라 서버 기록으로 결정된다.*",',
            '"yes": "필수", "no": "선택",',
        ],
        "reason": (
            "flowgate.default.0549 T0008: the specification-TS grammar and the TSR test report. Category B (protocol grammar): the ko section heading '시험 사양' and the ko field/boolean aliases ('분류', '대상', '필수', '선택', ...) are INPUT parser tokens a submitted TS is read with (their en/ja siblings parse too). Category A (locale dictionary): the ko branch of _REPORT_STRINGS renders the TSR test report; its en/ja siblings stay Korean-free. Registered instead of raising a line cap, so this T adds zero to the census budget."
        ),
        "tests": [
            'tests/test_test_spec_0549.py::test_spec_parses_every_structured_field_with_aliases_and_multiline_procedure',
            'tests/test_test_spec_0549.py::test_tsr_report_lists_every_case_even_when_failing',
        ],
    },
    {
        "file": 'modules/flow_gate/services/mention_service.py',
        "symbols": [
            "'새 TS는 사람이 검토·승인하는 시험사양서(test specification)로 작성하십시오. frontmatter에\\n'",
            "'`test_contract_version: 2` 를 반드시 넣으십시오 — 이 표시가 있는 TS는 FlowGate가 셸 명령으로\\n'",
            "'실행하지 않고, 시험 결과를 Case ID별로 받아 시험성적서(TSR)를 조립합니다. 표시가 없는 TS는\\n'",
            "'기존 실행형(legacy) TS로 계속 취급됩니다.\\n'",
            "'# <시험사양서 제목>\\n'",
            "'<선택: 범위·전제 설명>\\n'",
            "'## 시험 사양\\n'",
            "'### TC-001: <케이스 제목>\\n'",
            "'- requirement: <R/설계/AC 참조; 예: R0001 §4.2 / AC-03>\\n'",
            "'- precondition: <전제조건>\\n'",
            "'- input: <입력/조건>\\n'",
            "'- procedure: <시험 절차; 여러 줄이면 다음 줄부터 두 칸 들여쓰기>\\n'",
            "'- expected: <기대 결과 — 요구사항에서 온 값>\\n'",
            "'- check_points: <확인 관점>\\n'",
            "'- automation_ref: <선택: 자동 시험 위치; 예: tests/test_x.py::test_tc_001>\\n'",
            "'규칙: Case ID는 TS 안에서 유일해야 하고, 필수(required: true) Case가 최소 하나 있어야 합니다.\\n'",
            "'필드 이름(category, requirement, …)은 번역하지 않는 문법 토큰입니다. cmd/assert/setup 같은\\n'",
            "'실행형 필드는 시험사양서에서 거부됩니다. 기대 결과는 구현을 보고 쓰지 말고 R/승인 설계/AC/\\n'",
            "'지시를 근거로 쓰십시오. 케이스 수보다 요구사항·AC 커버리지(정상/부정/경계/회귀)를 우선하십시오.\\n'",
            "'자동 시험 코드는 저장소 테스트로 두고, JUnit 결과에서 `flowgate.case_id` 속성이나 테스트 이름의\\n'",
            "'`TC_001` 토큰으로 Case ID를 연결할 수 있습니다. 서버는 필수 Case 기준으로 종합 판정을 직접\\n'",
            "'계산하므로 문서나 결과에 적은 종합 PASS는 판정에 쓰이지 않습니다.\\n'",
            "'── 기존 실행형 TS(test_contract_version 표시 없음)를 고칠 때만 아래 문법을 씁니다 ──\\n'",
        ],
        "reason": (
            'flowgate.default.0549 T0008: the ko branch of _TS_SPEC_AUTHORING_TEXT, the specification -TS authoring guide served by the authoring_guide/TS help item. Category A (locale dictionary): the en/ja siblings are scanned by test_server_korean_leak_0355.py.'
        ),
        "tests": [
            'tests/test_test_spec_0549.py::test_ts_authoring_guide_defaults_to_the_specification_in_every_locale',
        ],
    },
    {
        "file": 'modules/flow_gate/workflow/pipeline_service.py',
        "symbols": [
            '"ko": "시험 gate를 통과하지 못한 시험성적서(TSR)는 승인할 수 없습니다. 서버 판정: {overall} (필수 Case 기준).",',
            '"ko": "시험사양서(TS) 구조 검증 오류가 있어 승인할 수 없습니다: {errors}",',
        ],
        "reason": (
            'flowgate.default.0549 T0008: the ko branch of _TEST_GATE_MESSAGES (TSR test-gate and TS specification approval refusals). Category A (locale dictionary).'
        ),
        "tests": [
            'tests/test_test_spec_0549.py::test_approval_guard_blocks_a_non_pass_tsr_and_allows_pass',
        ],
    },
]


def allowlisted_files() -> set[str]:
    return {item["file"] for item in PROTECTED}


def is_allowlisted(file_relpath: str, value: str) -> bool:
    """True when a Korean literal at ``file_relpath`` matches a registered symbol.

    Matching is substring-based in both directions: a short registered marker
    (e.g. a short bracketed label) matches inside a longer literal, and a longer registered phrase
    matches literals that are a prefix/fragment of it (guard messages sometimes
    truncate long strings for the offenders report).
    """
    for item in PROTECTED:
        if item["file"] != file_relpath:
            continue
        for symbol in item.get("symbols", []):
            if symbol in value or value in symbol:
                return True
    return False
