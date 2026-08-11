"""Shared document workflow constants."""
from __future__ import annotations

# Notes that complete at creation time and never require a review action.
AUTO_COMPLETE_TYPES = frozenset({"M", "CH"})

# Explicit review gates whose state is carried entirely by the database row.
# These types still require a human approval action; they are not auto-complete.
FILELESS_APPROVABLE_TYPES = frozenset({"AC"})

# Records that are intentionally outside workflow_sequence_items result slots.
NON_SLOT_WORKFLOW_TYPES = AUTO_COMPLETE_TYPES | frozenset({"Q", "A", "AC"})
# 작업계획(WP)은 이 집합에 **넣지 않는다**. D0007 §7 이 "작업계획이 어디에 속하는지
# 명시해야 한다"고 요구했고, 같은 문서가 그것을 "요건정의 다음에 오는 일반 칸"으로
# 정했다 — P0009 §7.2 의 미리보기 예시에서도 WP 가 item_seq 2 를 차지한다. 즉 WP 는
# 칸을 차지하는 타입이므로, 칸에 붙지 않은 WP 는 고아로 보여 붙일 자리를 물어야 한다.
# 아래 HEAD_TYPE_GUARD_EXEMPT_TYPES 와 헷갈리면 안 된다: 저쪽은 "지금 워크플로 머리가
# WP 여야만 만들 수 있다"를 풀어 주는 것이고, 이쪽은 "아예 칸을 갖지 않는다"는 뜻이다.

# ── Work plan (WP) — flowgate.default.0395 R0001 / D0007 ─────────────────────
# The advisory plan document: type counts + per-step provider assignment, stored
# as a JSON canonical body. It is NOT auto-complete (D0007 §3.1 결정 4) — it is
# created pending_review and goes through the ordinary review pipeline.
WORK_PLAN_TYPE = "WP"

# Types exempt from the inbox workflow-head type guard. A workflow head pins the
# next expected document type, which is right for step documents. A work plan is
# advisory and may be written at any point in a group's life — D0007 §3.1 결정 5
# explicitly allows several plans per group — so pinning it to the head would make
# the documented AI creation path (P0009 §5.1) fail whenever the group is mid-flow.
HEAD_TYPE_GUARD_EXEMPT_TYPES = AUTO_COMPLETE_TYPES | frozenset({WORK_PLAN_TYPE})

# ── Countable type registry (P0009 §4.1, L0010 §2.1) ─────────────────────────
# "Countable" means the type occupies a quantity box in a work plan. The unit and
# the instruction→result pairing are facts about the workflow, not per-project
# settings, so they live in code — but the *order* the types are listed in is read
# from the document_types table (L0010 §2.1 결정 2), never from this file.
WORK_PLAN_SHEET_TYPES = ("DS", "D", "P", "L", "DB")
WORK_PLAN_SET_TYPES = ("N", "T", "TS")

# instruction code → result code. Mirrors workflow_decision_service.AUTO_REPORT_MAP;
# duplicated here (not imported) so the document layer does not depend on the
# workflow-decision service just to describe a type.
WORK_PLAN_PAIR_MAP = {"N": "NR", "T": "TR", "TS": "TSR"}

WORK_PLAN_TYPE_UNITS: dict[str, str] = {
    **{code: "sheet" for code in WORK_PLAN_SHEET_TYPES},
    **{code: "set" for code in WORK_PLAN_SET_TYPES},
}

# Fallback ordering used only when the type table cannot be read.
WORK_PLAN_COUNTABLE_ORDER = WORK_PLAN_SHEET_TYPES + WORK_PLAN_SET_TYPES

# Every type code a work plan step may carry: the countable ones plus the result
# halves of each set.
WORK_PLAN_STEP_TYPES = frozenset(
    set(WORK_PLAN_COUNTABLE_ORDER) | set(WORK_PLAN_PAIR_MAP.values())
)

# Steps the server assembles itself — no provider, no note (DS0006 §2-7).
WORK_PLAN_LOCKED_TYPES = frozenset({"TSR"})

# ── 한줄 멘트 길이 상한 — 정본 (0406 T0022 작업 6 / M0019) ────────────────────
# 같은 값이 work_plan_service, work_plan_sequence_service, 화면 세 곳에 따로 박혀
# 있었고 셋 다 200 이었다. 작업계획 편집기는 그 200 에서 경고 없이 타이핑을 막았고,
# 시퀀스 저장 경로는 200 뒤를 조용히 잘랐다 — "작업 계획도 존나 끊겨서 들어가네".
# 이제 상한은 여기 한 곳이고, 서버의 두 서비스와 화면(API 응답의 limits)이 모두 이
# 값을 읽는다. 저장 경로는 넘치면 자르지 않고 note_too_long 으로 거절한다.
STEP_NOTE_MAX_CHARS = 1000
