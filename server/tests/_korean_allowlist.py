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
